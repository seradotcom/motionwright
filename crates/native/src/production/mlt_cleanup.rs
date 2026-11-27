//! Owner-confirmed closure of the native project behind one retained MLT job.
use super::{mlt_timeline::TIMELINE_SOURCE, provider_binding::MLT_CLOSE, *};

const START_SUFFIX: &str = ":mlt:render-start";
const START_COMMAND: &str = "driver.mlt-video.render.start";
const RESULT_COMMAND: &str = "driver.mlt-video.render.result";
const SOURCE_SCHEMA: &str = "motionwright.mlt-timeline-source/1";
const MAX_ORIGINAL_BYTES: u64 = 1024 * 1024 * 1024;
const MAX_CONFIRMATION_HISTORY: usize = 32;

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MltCloseDisposition {
    Closed,
    ConfirmationUnavailable,
    Refused,
    OutcomeUnknown,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct OriginalTimeline {
    schema: String,
    connection_identity: String,
    project_ref: String,
    semantic_revision: String,
    provider_generation: u64,
    job: String,
    output: String,
    profile: String,
}

pub struct PreparedMltClose {
    project: Uuid,
    stamp: RevisionStamp,
    source: OriginalTimeline,
    start_request: String,
    result_receipt: Uuid,
    request_id: String,
}

fn retained_pair<'a>(
    rows: &'a [ProductionReceipt],
    project: Uuid,
    stamp: &RevisionStamp,
    request: &str,
    command: &str,
) -> NativeResult<(&'a ProductionReceipt, &'a ProductionReceipt)> {
    let [intent, terminal] = rows else {
        return Err(invalid("MLT job has no exact original receipt pair"));
    };
    if intent.stage != "dispatching"
        || terminal.stage != "completed"
        || !is_sha256(&intent.request_sha256)
        || intent.request_sha256 != terminal.request_sha256
        || intent.id == terminal.id
        || [intent, terminal].iter().any(|row| {
            row.project_id != project
                || row.generation != stamp.generation
                || row.revision != stamp.revision
                || row.request_id != request
                || row.command != command
        })
    {
        return Err(invalid(
            "MLT original receipt scope or terminal identity differs",
        ));
    }
    Ok((intent, terminal))
}

fn provider_result(receipt: &ProductionReceipt, generation: u64) -> NativeResult<&Value> {
    let original = &receipt.payload["result"];
    let exec = &original["execution"];
    let provenance = &exec["provenance"];
    if original["provenance_checked"] != json!(true)
        || exec["dry_run"] != json!(false)
        || exec["backend"] != json!("driver:mlt-video")
        || !provenance["untrusted_metadata"].is_boolean()
        || provenance["provider"] != json!("driver:mlt-video")
        || provenance["execution_provider"] != json!("driver:mlt-video")
        || provenance["source"] != json!("driver")
        || provenance["provider_generation"].as_u64() != Some(generation)
        || !provenance["descriptor_sha256"]
            .as_str()
            .is_some_and(is_sha256)
    {
        return Err(invalid("MLT original provider provenance is invalid"));
    }
    original
        .get("data")
        .ok_or_else(|| invalid("MLT original response data is absent"))
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
        let prefix = start_request
            .strip_suffix(START_SUFFIX)
            .filter(|prefix| !prefix.is_empty() && start_request.len() <= 256)
            .ok_or_else(|| invalid("Select an original MLT timeline render in Jobs"))?;
        let result_request = format!("{prefix}:mlt:render-result");
        let result_rows = self
            .service
            .original_production_receipt_pair(project, &result_request)
            .map_err(storage_error)?;
        let (intent, terminal) = retained_pair(
            &result_rows,
            project,
            stamp,
            &result_request,
            RESULT_COMMAND,
        )?;
        let original = &terminal.payload[TIMELINE_SOURCE];
        let timeline: OriginalTimeline = serde_json::from_value(original.clone())
            .map_err(|_| invalid("The MLT result lacks a retained native project identity"))?;
        let connection_identity = self.client.connection().identity()?;
        let ref_id = timeline
            .project_ref
            .strip_prefix("video:")
            .unwrap_or_default();
        if original != &intent.payload[TIMELINE_SOURCE]
            || timeline.schema != SOURCE_SCHEMA
            || timeline.connection_identity != connection_identity
            || intent.payload["connection"] != json!(connection_identity)
            || timeline.provider_generation == 0
            || ref_id.len() != 32
            || !ref_id
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || !is_sha256(&timeline.semantic_revision)
            || !matches!(
                timeline.profile.as_str(),
                "lossless" | "lossless-video-only"
            )
            || !timeline.job.starts_with("render:")
            || timeline.job.len() > 80
            || request_digest(RESULT_COMMAND, &json!({"job":timeline.job}))?
                != terminal.request_sha256
        {
            return Err(invalid(
                "The MLT result does not identify the original runtime and native project",
            ));
        }

        let start_rows = self
            .service
            .original_production_receipt_pair(project, start_request)
            .map_err(storage_error)?;
        let (start_intent, start) =
            retained_pair(&start_rows, project, stamp, start_request, START_COMMAND)?;
        let started = provider_result(start, timeline.provider_generation)?;
        let result = provider_result(terminal, timeline.provider_generation)?;
        if start_intent.payload["connection"] != json!(connection_identity)
            || intent.payload["job_ref"] != json!(timeline.job)
            || terminal.payload["job_ref"] != json!(timeline.job)
            || result["state"] != json!("succeeded")
            || !result.get("error").is_some_and(Value::is_null)
            || [started, result].iter().any(|data| {
                data["job"] != json!(timeline.job)
                    || data["project_revision"] != json!(timeline.semantic_revision)
                    || data["profile"] != json!(timeline.profile)
                    || data["output"] != json!(timeline.output)
            })
            || result.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
            || result.pointer("/artifact/path").and_then(Value::as_str)
                != Some(timeline.output.as_str())
        {
            return Err(invalid(
                "MLT closure requires the completed original render and its output",
            ));
        }
        let sha = required_string(
            result,
            "/artifact/sha256",
            "MLT render has no retained output digest",
        )?;
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
        if measured.sha256 != sha
            || result.pointer("/artifact/bytes").and_then(Value::as_u64)
                != Some(measured.size_bytes)
        {
            return Err(invalid("MLT output differs from its original bytes"));
        }
        let request_id = format!(
            "mlt-owner-close:{}",
            request_digest(
                MLT_CLOSE,
                &json!({
                    "connection_identity": connection_identity,
                    "provider_generation": timeline.provider_generation,
                    "project_ref": timeline.project_ref,
                })
            )?
        );
        Ok(PreparedMltClose {
            project,
            stamp: stamp.clone(),
            source: timeline,
            start_request: start_request.to_owned(),
            result_receipt: terminal.id,
            request_id,
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
            match close_disposition(&prior, &prepared) {
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
        let request_sha256 = request_digest(MLT_CLOSE, &args)?;
        let mut receipt = ProductionReceiptInput {
            project_id: prepared.project,
            generation: prepared.stamp.generation,
            revision: prepared.stamp.revision,
            request_id: prepared.request_id.clone(),
            request_sha256,
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
                return Ok(close_disposition(&prior, &prepared));
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
}

fn close_disposition(
    prior: &ProductionReceipt,
    prepared: &PreparedMltClose,
) -> MltCloseDisposition {
    if prior.project_id != prepared.project
        || prior.generation != prepared.stamp.generation
        || prior.revision != prepared.stamp.revision
        || prior.command != MLT_CLOSE
        || prior.request_id != prepared.request_id
        || prior.payload["start_request"] != json!(prepared.start_request)
        || prior.payload["result_receipt"] != json!(prepared.result_receipt)
        || prior.payload["native_source"] != json!(prepared.source)
    {
        return MltCloseDisposition::OutcomeUnknown;
    }
    match prior.stage.as_str() {
        "completed"
            if prior.payload.pointer("/result/data/closed") == Some(&json!(true))
                && prior.payload.pointer("/result/execution/policy_decision")
                    == Some(&json!("allow_after_confirmation"))
                && prior
                    .payload
                    .pointer("/result/provider_bound/original/execution/policy_decision")
                    == Some(&json!("allow_after_confirmation"))
                && prior
                    .payload
                    .pointer("/result/execution/provenance/provider_generation")
                    == Some(&json!(prepared.source.provider_generation)) =>
        {
            MltCloseDisposition::Closed
        }
        "failed_known"
            if prior.payload.pointer("/error/code") == Some(&json!(ErrorCode::PolicyDenied))
                && prior.payload.pointer("/error/operator_refused") == Some(&json!(true)) =>
        {
            MltCloseDisposition::Refused
        }
        "failed_known"
            if prior.payload.pointer("/error/code") == Some(&json!(ErrorCode::ConsentRequired)) =>
        {
            MltCloseDisposition::ConfirmationUnavailable
        }
        _ => MltCloseDisposition::OutcomeUnknown,
    }
}
