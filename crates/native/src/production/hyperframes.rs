//! Public provider route using the existing source-bound production journal.
use super::*;
use crate::hyperframes::build_hyperframes_source;
use motionwright_hyperframes_profile::{self as model, SourceInventory};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HyperframesSourceEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub source: SourceInventory,
    pub runtime_lock_sha256: String,
    pub job_ref: String,
    pub result: Value,
    pub output_kind: String,
    pub human_acceptance: bool,
}
impl ProductionCoordinator {
    pub async fn render_hyperframes_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        runtime_lock_sha256: &str,
    ) -> NativeResult<HyperframesSourceEvidence> {
        if request_id.is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
            || !model::sha_valid(runtime_lock_sha256)
        {
            return Err(invalid("HyperFrames source request identity invalid"));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed since HyperFrames source preparation",
            ));
        }
        // All unsupported source semantics reject before any provider command.
        let composition = build_hyperframes_source(&project, deliverable_id)?;
        let compiled = model::compile(&composition)?;
        let source = compiled.inventory();
        let plan = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:plan"),
                "driver.hyperframes.project.plan",
                json!({"composition":composition,"runtime_lock_sha256":runtime_lock_sha256}),
                false,
            )
            .await?;
        let planned = response_data(&plan)?;
        if planned.get("runnable") != Some(&Value::Bool(true))
            || planned.get("dependencies_verified") != Some(&Value::Bool(true))
            || planned.get("implicit_installation") != Some(&Value::Bool(false))
            || planned.get("source") != Some(&serde_json::to_value(&source)?)
            || planned.get("runtime_lock_sha256").and_then(Value::as_str)
                != Some(runtime_lock_sha256)
        {
            return Err(backend(
                "HyperFrames provider plan differs from the application source or verified dependency pin",
            ));
        }
        let current = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:inspect-before"),
                "driver.hyperframes.project.inspect",
                json!({}),
                false,
            )
            .await?;
        let observed = response_data(&current)?;
        let previous = match observed.get("source") {
            Some(Value::Null) => None,
            Some(value) => Some(
                serde_json::from_value::<SourceInventory>(value.clone())
                    .map_err(|_| backend("HyperFrames prior native source inventory malformed"))?
                    .fingerprint,
            ),
            None => return Err(backend("HyperFrames source inspection missing")),
        };
        let applied = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:apply"),
                "driver.hyperframes.project.apply",
                json!({"composition":composition,"expected_fingerprint":previous,"dry_run":false}),
                true,
            )
            .await?;
        let applied = response_data(&applied)?;
        if applied.get("applied") != Some(&Value::Bool(true))
            || applied.get("source") != Some(&serde_json::to_value(&source)?)
        {
            return Err(backend("HyperFrames source apply was not acknowledged"));
        }
        let attempt_id = format!(
            "hf-{}",
            model::sha(
                format!(
                    "{request_id}\n{}\n{runtime_lock_sha256}",
                    source.fingerprint
                )
                .as_bytes()
            )
        );
        let started=self.execute(project_id,expected,&format!("{request_id}:render-start"),"driver.hyperframes.render.start",
            json!({"fingerprint":source.fingerprint,"runtime_lock_sha256":runtime_lock_sha256,"attempt_id":attempt_id}),true).await?;
        let started = response_data(&started)?;
        let job_ref = started
            .get("job_ref")
            .and_then(Value::as_str)
            .filter(|v| *v == attempt_id)
            .ok_or_else(|| {
                backend("HyperFrames original render start identity differs").uncertain()
            })?
            .to_owned();
        let deadline = Instant::now() + Duration::from_secs(330);
        let mut poll = 0u32;
        loop {
            if Instant::now() >= deadline {
                return Err(Error::new(
                    ErrorCode::Timeout,
                    "Original HyperFrames job not terminal; reconcile without restart",
                )
                .uncertain());
            }
            let observed = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:render-status:{poll}"),
                    "driver.hyperframes.render.status",
                    json!({"job_ref":job_ref}),
                    false,
                )
                .await
                .map_err(Error::uncertain)?;
            let data = response_data(&observed).map_err(Error::uncertain)?;
            if data.get("job_ref").and_then(Value::as_str) != Some(job_ref.as_str())
                || data.get("fingerprint").and_then(Value::as_str)
                    != Some(source.fingerprint.as_str())
            {
                return Err(
                    backend("HyperFrames status belongs to another original job").uncertain(),
                );
            }
            match data.get("state").and_then(Value::as_str) {
                Some("succeeded") => break,
                Some("running") | Some("cancelling") => {}
                Some("failed") => {
                    return Err(backend(
                        "Original HyperFrames native command failed; no retry was made",
                    ));
                }
                Some("cancelled") => {
                    return Err(Error::new(
                        ErrorCode::Cancelled,
                        "Original HyperFrames job was cancelled",
                    ));
                }
                _ => {
                    return Err(backend(
                        "Original HyperFrames job outcome is unknown; no retry was made",
                    )
                    .uncertain());
                }
            }
            poll = poll
                .checked_add(1)
                .ok_or_else(|| backend("HyperFrames poll identity overflow").uncertain())?;
            sleep(Duration::from_millis(1000)).await;
        }
        let result = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:render-result"),
                "driver.hyperframes.render.result",
                json!({"job_ref":job_ref}),
                false,
            )
            .await
            .map_err(Error::uncertain)?;
        let data = response_data(&result).map_err(Error::uncertain)?;
        if data.get("state").and_then(Value::as_str) != Some("succeeded")
            || data.get("job_ref").and_then(Value::as_str) != Some(job_ref.as_str())
            || data.pointer("/receipt/frame_count").and_then(Value::as_u64)
                != Some(source.composition.frame_count as u64)
            || data
                .pointer("/receipt/source_sha256")
                .and_then(Value::as_str)
                != Some(source.source_sha256.as_str())
            || data.pointer("/receipt/html_sha256").and_then(Value::as_str)
                != Some(source.html_sha256.as_str())
            || data.get("runtime_lock_sha256").and_then(Value::as_str) != Some(runtime_lock_sha256)
        {
            return Err(
                backend("Original HyperFrames result is not source-bound native output")
                    .uncertain(),
            );
        }
        Ok(HyperframesSourceEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            source,
            runtime_lock_sha256: runtime_lock_sha256.into(),
            job_ref,
            result: data.clone(),
            output_kind: "native_png_sequence_intermediate".into(),
            human_acceptance: false,
        })
    }
}
