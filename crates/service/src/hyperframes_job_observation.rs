//! Display lineage for explicit reads of an existing HyperFrames job.
//! Receipt revisions remain unchanged. A later read can describe an earlier
//! source only when its exact original start, dispatches and connection agree.
use crate::ProductionReceipt;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use uuid::Uuid;

type DispatchKey = (Uuid, Uuid, String);
type OriginalKey = (Uuid, Uuid, String, String);
type Positioned<'a> = (usize, &'a ProductionReceipt);
pub(super) struct OriginalObservations<'a> {
    dispatches: BTreeMap<DispatchKey, Vec<Positioned<'a>>>,
    originals: BTreeMap<OriginalKey, Vec<Positioned<'a>>>,
}
fn sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
impl<'a> OriginalObservations<'a> {
    pub(super) fn new(receipts: &'a [ProductionReceipt]) -> Self {
        let mut result = Self {
            dispatches: BTreeMap::new(),
            originals: BTreeMap::new(),
        };
        for (position, row) in receipts.iter().enumerate() {
            if row.stage == "dispatching" {
                result
                    .dispatches
                    .entry((row.project_id, row.generation, row.request_id.clone()))
                    .or_default()
                    .push((position, row));
            }
            if row.stage != "completed" || row.command != "driver.hyperframes.render.start" {
                continue;
            }
            let Some(original) = row.request_id.strip_suffix(":render-start") else {
                continue;
            };
            let Some(wrapper) = super::completed_wrapper(row) else {
                continue;
            };
            if wrapper
                .pointer("/execution/provenance/provider")
                .and_then(Value::as_str)
                != Some("driver:hyperframes")
                || super::provider_generation(wrapper).is_none()
            {
                continue;
            }
            let Some(job) = super::driver_data(wrapper)
                .get("job_ref")
                .and_then(Value::as_str)
            else {
                continue;
            };
            result
                .originals
                .entry((
                    row.project_id,
                    row.generation,
                    job.into(),
                    hex::encode(Sha256::digest(original.as_bytes())),
                ))
                .or_default()
                .push((position, row));
        }
        result
    }
    fn dispatch(
        &self,
        terminal: &ProductionReceipt,
        mutation: bool,
        job: &str,
    ) -> Option<(usize, &'a str)> {
        let rows = self.dispatches.get(&(
            terminal.project_id,
            terminal.generation,
            terminal.request_id.clone(),
        ))?;
        if rows.len() != 1 {
            return None;
        }
        let (position, row) = *rows.first()?;
        let connection = row.payload.get("connection")?.as_str()?;
        if row.command != terminal.command
            || row.revision != terminal.revision
            || row.request_sha256 != terminal.request_sha256
            || !sha(connection)
            || row.payload.get("mutation") != Some(&Value::Bool(mutation))
            || row.payload.get("job_ref") != Some(&if mutation { Value::Null } else { json!(job) })
        {
            return None;
        }
        Some((position, connection))
    }
    fn resolve(&self, row: &ProductionReceipt, position: usize, origin: &str) -> Option<u64> {
        let job = super::job_ref_from_receipt(row)?;
        let starts =
            self.originals
                .get(&(row.project_id, row.generation, job.into(), origin.into()))?;
        if starts.len() != 1 {
            return None;
        }
        let (start_position, start) = *starts.first()?;
        let (intent_position, connection) = self.dispatch(start, true, job)?;
        let (read_position, read_connection) = self.dispatch(row, false, job)?;
        let digest = hex::encode(Sha256::digest(
            serde_json::to_vec(&(row.command.as_str(), json!({"job_ref":job}))).ok()?,
        ));
        if connection != read_connection
            || row.request_sha256 != digest
            || intent_position >= start_position
            || start_position >= read_position
            || read_position >= position
            || row.revision < start.revision
        {
            return None;
        }
        Some(start.revision)
    }
    /// None is a legacy/unrelated request. Invalid explicit bindings remain
    /// uncertain even when their current revision happens to match a start.
    pub(super) fn source_revision(
        &self,
        row: &ProductionReceipt,
        position: usize,
    ) -> Option<Result<u64, ()>> {
        let suffix = match row.command.as_str() {
            "driver.hyperframes.render.status" => ":status",
            "driver.hyperframes.render.result" => ":result",
            _ => return None,
        };
        let rest = row.request_id.strip_prefix("hf-observe-")?;
        let Some(core) = rest.strip_suffix(suffix) else {
            return Some(Err(()));
        };
        // Prior request IDs contained only the observation UUID. Their native
        // readback remains valid, but their source revision is not inferred.
        if Uuid::parse_str(core).is_ok() {
            return None;
        }
        let Some((origin, nonce)) = core.split_once('-') else {
            return Some(Err(()));
        };
        let valid_nonce =
            Uuid::parse_str(nonce).is_ok_and(|id| !id.is_nil() && id.to_string() == nonce);
        if !sha(origin) || !valid_nonce {
            return Some(Err(()));
        }
        Some(self.resolve(row, position, origin).ok_or(()))
    }
}
