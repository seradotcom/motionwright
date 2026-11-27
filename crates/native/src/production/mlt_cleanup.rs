//! Owner-confirmed closure of the native project behind one retained MLT job.
use super::{provider_binding::MLT_CLOSE, *};
use motionwright_service::{OriginalMltTimeline, mlt_result_request, retained_mlt_close};

const MAX_ORIGINAL_BYTES: u64 = 1024 * 1024 * 1024;
const MAX_CONFIRMATION_HISTORY: usize = 32;

pub struct PreparedMltClose {
    project: Uuid,
    stamp: RevisionStamp,
    source: OriginalMltTimeline,
    start_request: String,
    result_receipt: Uuid,
    request_id: String,
    request_sha256: String,
}

impl ProductionCoordinator {
    pub fn prepare_mlt_close(
        &self,
        project: Uuid,
        stamp: &RevisionStamp,
        start_request: &str,
    ) -> NativeResult<PreparedMltClose> {
        let current = self.service.project(project).map_err(storage_error)?;
        if current.stamp() != *stamp || self.client.connection().resource != stamp.resource {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "MLT closure belongs to a different saved project",
            ));
        }
        let result_request = mlt_result_request(start_request)
            .ok_or_else(|| invalid("Select an original MLT timeline render in Jobs"))?;
        let start_rows = self
            .service
            .original_production_receipt_pair(project, start_request)
            .map_err(storage_error)?;
        let result_rows = self
            .service
            .original_production_receipt_pair(project, &result_request)
            .map_err(storage_error)?;
        let retained = retained_mlt_close(
            project,
            stamp,
            start_request,
            &start_rows.iter().collect::<Vec<_>>(),
            &result_rows.iter().collect::<Vec<_>>(),
        )
        .ok_or_else(|| {
            invalid("MLT closure requires the completed original render and its source")
        })?;
        let [_, start] = start_rows.as_slice() else {
            return Err(invalid("The original MLT start pair changed"));
        };
        let [result_intent, _] = result_rows.as_slice() else {
            return Err(invalid("The original MLT result pair changed"));
        };
        if !self
            .service
            .production_receipt_committed_before(project, start.id, result_intent.id)
            .map_err(storage_error)?
        {
            return Err(invalid("The MLT result precedes the original render start"));
        }
        let timeline = retained.source;
        if timeline.connection_identity != self.client.connection().identity()? {
            return Err(invalid("The original MLT production connection changed"));
        }
        let artifact = output_artifact_path(
            &self.client.connection().output_root,
            &timeline.output,
            MAX_ORIGINAL_BYTES,
        )?;
        let measured = motionwright_storage::Store::file_import_source_descriptor_with_limit(
            &artifact,
            MAX_ORIGINAL_BYTES,
        )
        .map_err(storage_error)?;
        if measured.sha256 != retained.artifact_sha256
            || measured.size_bytes != retained.artifact_bytes
        {
            return Err(invalid("MLT output differs from its original bytes"));
        }
        Ok(PreparedMltClose {
            project,
            stamp: stamp.clone(),
            source: timeline,
            start_request: start_request.to_owned(),
            result_receipt: retained.result_id,
            request_id: retained.request_id,
            request_sha256: retained.request_sha256,
        })
    }

    pub async fn close_prepared_mlt(
        &self,
        mut prepared: PreparedMltClose,
    ) -> NativeResult<MltCloseDisposition> {
        if self.client.connection().identity()? != prepared.source.connection_identity {
            return Err(invalid(
                "The original production connection changed before MLT closure",
            ));
        }
        // Only an authenticated, definite lack of confirmation permits a new
        // explicit attempt. The preceding receipt ID makes concurrent retries
        // choose the same durable key.
        let original_key = prepared.request_id.clone();
        for _ in 0..MAX_CONFIRMATION_HISTORY {
            let Some(prior) = self
                .service
                .latest_production_receipt(prepared.project, &prepared.request_id)
                .map_err(storage_error)?
            else {
                return self.dispatch_mlt_close(prepared).await;
            };
            match self.retained_close_disposition(&prior, &prepared)? {
                MltCloseDisposition::ConfirmationUnavailable => {
                    prepared.request_id = format!("{original_key}:after:{}", prior.id);
                }
                other => return Ok(other),
            }
        }
        Err(invalid(
            "MLT foreground confirmation history exceeded its bound",
        ))
    }

    async fn dispatch_mlt_close(
        &self,
        prepared: PreparedMltClose,
    ) -> NativeResult<MltCloseDisposition> {
        let target = &prepared.source;
        let binding = self
            .client
            .bind_mlt_close(target.provider_generation)
            .await?;
        let units = RootBudgetUnits {
            attempts: 1,
            ..RootBudgetUnits::default()
        };
        let root = match self
            .service
            .root_operation_budget_for_key(prepared.project, &prepared.request_id)
            .map_err(storage_error)?
        {
            Some(root) => root,
            None => self
                .service
                .create_root_operation_budget(RootOperationBudgetInput {
                    project_id: prepared.project,
                    generation: prepared.stamp.generation,
                    revision: prepared.stamp.revision,
                    operation_key: prepared.request_id.clone(),
                    limits: units,
                })
                .map_err(storage_error)?,
        };
        if root.limits != units {
            return Err(invalid(
                "MLT closure budget differs from its original limit",
            ));
        }
        let args =
            json!({"project": target.project_ref, "expected_revision": target.semantic_revision});
        let mut receipt = ProductionReceiptInput {
            project_id: prepared.project,
            generation: prepared.stamp.generation,
            revision: prepared.stamp.revision,
            request_id: prepared.request_id.clone(),
            request_sha256: prepared.request_sha256.clone(),
            command: MLT_CLOSE.into(),
            stage: "dispatching".into(),
            payload: json!({
                "mutation": true,
                "start_request": prepared.start_request,
                "result_receipt": prepared.result_receipt,
                "native_source": target,
                "provider_binding": binding.receipt_context(),
            }),
        };
        match self
            .service
            .admit_root_operation_dispatch(RootDispatchBudgetInput {
                root_id: root.root_id,
                receipt: receipt.clone(),
                units,
            })
            .map_err(storage_error)?
        {
            RootDispatchBudgetOutcome::AlreadyCompleted(prior) => {
                return self.retained_close_disposition(&prior, &prepared);
            }
            RootDispatchBudgetOutcome::Admitted(_) => {}
        }

        let outcome = self
            .client
            .execute_bound_media_transport(&binding, MLT_CLOSE, args, true)
            .await;
        let disposition = match outcome {
            Ok(observed) => match binding.qualify_result(&observed, MLT_CLOSE, true) {
                Ok(result) => {
                    receipt.stage = "completed".into();
                    receipt.payload["result"] = result;
                    MltCloseDisposition::Closed
                }
                Err(error) => {
                    receipt.stage = "outcome_unknown".into();
                    receipt.payload["result"] = observed.value;
                    receipt.payload["error"] = json!({"code": error.code});
                    MltCloseDisposition::OutcomeUnknown
                }
            },
            Err(error)
                if error.outcome_known
                    && error.code == ErrorCode::PolicyDenied
                    && error.message == "Operator declined or approval expired" =>
            {
                receipt.stage = "failed_known".into();
                receipt.payload["error"] = json!({"code": error.code, "operator_refused": true});
                MltCloseDisposition::Refused
            }
            Err(error) if error.outcome_known && error.code == ErrorCode::ConsentRequired => {
                receipt.stage = "failed_known".into();
                receipt.payload["error"] = json!({"code": error.code});
                MltCloseDisposition::ConfirmationUnavailable
            }
            Err(error) => {
                receipt.stage = "outcome_unknown".into();
                receipt.payload["error"] = json!({"code": error.code});
                MltCloseDisposition::OutcomeUnknown
            }
        };
        if self.service.append_production_receipt(receipt).is_err() {
            return Ok(MltCloseDisposition::OutcomeUnknown);
        }
        Ok(disposition)
    }
    fn retained_close_disposition(
        &self,
        prior: &ProductionReceipt,
        prepared: &PreparedMltClose,
    ) -> NativeResult<MltCloseDisposition> {
        if prior.stage == "dispatching" || prior.stage == "outcome_unknown" {
            return Ok(MltCloseDisposition::OutcomeUnknown);
        }
        let rows = self
            .service
            .original_production_receipt_pair(prepared.project, &prepared.request_id)
            .map_err(storage_error)?;
        let [intent, terminal] = rows.as_slice() else {
            return Ok(MltCloseDisposition::OutcomeUnknown);
        };
        let exact = intent.stage == "dispatching"
            && terminal.id == prior.id
            && [intent, terminal].iter().all(|receipt| {
                receipt.project_id == prepared.project
                    && receipt.generation == prepared.stamp.generation
                    && receipt.revision == prepared.stamp.revision
                    && receipt.command == MLT_CLOSE
                    && receipt.request_id == prepared.request_id
                    && receipt.request_sha256 == prepared.request_sha256
                    && receipt.payload["mutation"] == json!(true)
                    && receipt.payload["start_request"] == json!(prepared.start_request)
                    && receipt.payload["result_receipt"] == json!(prepared.result_receipt)
                    && receipt.payload["native_source"] == json!(prepared.source)
            });
        Ok(
            if exact
                && self
                    .service
                    .production_receipt_committed_before(
                        prepared.project,
                        prepared.result_receipt,
                        intent.id,
                    )
                    .map_err(storage_error)?
            {
                motionwright_service::mlt_close_receipt_disposition(terminal)
            } else {
                MltCloseDisposition::OutcomeUnknown
            },
        )
    }
}
