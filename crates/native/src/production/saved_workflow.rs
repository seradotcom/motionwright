//! Historical completed workflow readback. This path never invokes Core,
//! reserves a budget, repairs history, or settles an absent/UNKNOWN native result.
use super::*;
use semwright_native_sdk::types::Execution;

impl ProductionCoordinator {
    pub fn read_recorded_workflow_completion(
        &self,
        project_id: Uuid,
        original: &RevisionStamp,
        request_id: &str,
        command: &str,
        args: &Value,
    ) -> NativeResult<Option<Value>> {
        if !WORKFLOW_ACTION_COMMANDS.contains(&command)
            || request_id.is_empty()
            || request_id.len() > 128
            || request_id.trim() != request_id
            || request_id.chars().any(char::is_control)
            || !args.is_object()
            || serde_json::to_vec(args)
                .map_err(|_| invalid("Invalid workflow input"))?
                .len()
                > MAX_ARGS_BYTES
        {
            return Err(invalid("Original workflow selector is invalid"));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if original.resource != project.resource_key()
            || original.generation != project.generation
            || original.revision > project.revision
            || self.client.connection().resource != project.resource_key()
        {
            return Err(invalid(
                "Original workflow belongs to another project scope",
            ));
        }
        self.client.connection().validate()?;
        let identity = self.client.connection().identity()?;
        let rows = self
            .service
            .original_production_receipt_pair(project_id, request_id)
            .map_err(storage_error)?;
        let digest = request_digest(command, args)?;
        for row in &rows {
            if row.project_id != project_id
                || row.generation != original.generation
                || row.revision != original.revision
                || row.request_id != request_id
                || row.command != command
                || row.request_sha256 != digest
            {
                return Err(invalid(
                    "Stored workflow source or arguments differ from the original request",
                ));
            }
        }
        let Some(intent) = rows.first() else {
            return Ok(None);
        };
        if intent.stage != "dispatching"
            || intent.payload.get("connection").and_then(Value::as_str) != Some(identity.as_str())
            || intent.payload.get("mutation").and_then(Value::as_bool)
                != Some(WORKFLOW_MUTATING_COMMANDS.contains(&command))
        {
            return Err(invalid(
                "Original workflow dispatch or connection does not match",
            ));
        }
        let Some(terminal) = rows.get(1) else {
            return Ok(None);
        };
        if terminal.stage != "completed" {
            // Includes failed_known: this endpoint recovers only a complete original success.
            // Neither a historical observation nor an absent row authorizes a retry.
            return Ok(None);
        }
        let result = terminal
            .payload
            .get("result")
            .ok_or_else(|| invalid("Stored workflow result is missing"))?;
        if serde_json::to_vec(result)
            .map_err(|_| invalid("Stored workflow result is invalid"))?
            .len()
            > MAX_OUTPUT_BYTES
            || result.get("provenance_checked").and_then(Value::as_bool) != Some(true)
            || result.get("data").is_none()
        {
            return Err(invalid("Stored workflow result is incomplete"));
        }
        let execution: Execution =
            serde_json::from_value(result.get("execution").cloned().unwrap_or(Value::Null))
                .map_err(|_| invalid("Stored workflow execution is incomplete"))?;
        let provenance = execution
            .provenance
            .as_ref()
            .ok_or_else(|| invalid("Stored workflow provenance is missing"))?;
        if execution.dry_run
            || provenance.provider != "semwright-core"
            || provenance.source != SourceKind::Builtin
            || provenance.untrusted_metadata
            || !is_sha256(&provenance.descriptor_sha256)
        {
            return Err(invalid(
                "Stored workflow result does not identify the original Core authority",
            ));
        }
        let broker_id = terminal
            .payload
            .get("broker_request_id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty() && id.len() <= 1024 && !id.chars().any(char::is_control))
            .ok_or_else(|| invalid("Original workflow Broker request is missing"))?;
        let _ = broker_id;
        // Historical completion remains tied to its original revision. Later creative
        // edits are preserved; a change during this read makes the observation stale.
        let current = self.service.project(project_id).map_err(storage_error)?;
        if current.generation != project.generation
            || current.revision != project.revision
            || self
                .service
                .original_production_receipt_pair(project_id, request_id)
                .map_err(storage_error)?
                != rows
        {
            return Err(invalid(
                "Workflow source or original receipt changed during readback",
            ));
        }
        self.client.connection().validate()?;
        Ok(Some(
            json!({"replayed": true, "result": result, "receipt": terminal}),
        ))
    }
}
