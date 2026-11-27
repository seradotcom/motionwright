//! v0.1 owner-viewable MLT project lifetime inventory.
//!
//! This projects app-owned source receipts only. It never queries the live
//! Broker, issues grants, closes provider projects or fabricates liveness.
//! In particular, even a completed project.close cannot be promoted to a
//! VERIFIED cleanup without separate foreground Broker consent evidence.
use motionwright_domain::{Project, RevisionStamp};
use motionwright_storage::{ProductionReceipt, ProductionReceiptWatermark};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

const SCHEMA: &str = "motionwright.v01-mlt-cleanup-inventory/1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MltCloseDisposition {
    Closed,
    ConfirmationUnavailable,
    Refused,
    OutcomeUnknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltClosureProjection {
    pub disposition: Option<MltCloseDisposition>,
}

pub fn mlt_close_receipt_disposition(receipt: &ProductionReceipt) -> MltCloseDisposition {
    let payload = &receipt.payload;
    let generation = payload
        .pointer("/native_source/provider_generation")
        .and_then(Value::as_u64);
    match receipt.stage.as_str() {
        "completed"
            if generation.is_some()
                && payload
                    .pointer("/result/execution/provenance/provider_generation")
                    .and_then(Value::as_u64)
                    == generation
                && payload
                    .pointer("/result/data/closed")
                    .and_then(Value::as_bool)
                    == Some(true)
                && payload
                    .pointer("/result/execution/policy_decision")
                    .and_then(Value::as_str)
                    == Some("allow_after_confirmation")
                && payload
                    .pointer("/result/provider_bound/original/execution/policy_decision")
                    .and_then(Value::as_str)
                    == Some("allow_after_confirmation") =>
        {
            MltCloseDisposition::Closed
        }
        "failed_known"
            if payload.pointer("/error/code").and_then(Value::as_str) == Some("PolicyDenied")
                && payload
                    .pointer("/error/operator_refused")
                    .and_then(Value::as_bool)
                    == Some(true) =>
        {
            MltCloseDisposition::Refused
        }
        "failed_known"
            if payload.pointer("/error/code").and_then(Value::as_str)
                == Some("ConsentRequired") =>
        {
            MltCloseDisposition::ConfirmationUnavailable
        }
        _ => MltCloseDisposition::OutcomeUnknown,
    }
}

const START_COMMAND: &str = "driver.mlt-video.render.start";
const RESULT_COMMAND: &str = "driver.mlt-video.render.result";
const CLOSE_COMMAND: &str = "driver.mlt-video.project.close";
const START_SUFFIX: &str = ":mlt:render-start";
const RESULT_SUFFIX: &str = ":mlt:render-result";
const TIMELINE_SOURCE: &str = "mlt_timeline_source";

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalMltTimeline {
    schema: String,
    pub connection_identity: String,
    pub project_ref: String,
    pub semantic_revision: String,
    pub provider_generation: u64,
    job: String,
    pub output: String,
    profile: String,
}

pub struct RetainedMltClose {
    pub source: OriginalMltTimeline,
    pub result_id: Uuid,
    pub artifact_sha256: String,
    pub artifact_bytes: u64,
    pub request_id: String,
    pub request_sha256: String,
}

fn digest(command: &str, args: &Value) -> Option<String> {
    let bytes = serde_json::to_vec(&(command, args)).ok()?;
    Some(hex::encode(Sha256::digest(bytes)))
}

fn lowercase_hex(value: &str, bytes: usize) -> bool {
    value.len() == bytes * 2
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn mlt_result_request(start: &str) -> Option<String> {
    let root = start
        .strip_suffix(START_SUFFIX)
        .filter(|root| !root.is_empty())?;
    let request = format!("{root}{RESULT_SUFFIX}");
    (request.len() <= 256).then_some(request)
}

fn retained_pair<'a>(
    rows: &[&'a ProductionReceipt],
    project: Uuid,
    stamp: &RevisionStamp,
    request: &str,
    command: &str,
    mutation: bool,
) -> Option<(&'a ProductionReceipt, &'a ProductionReceipt)> {
    let [intent, terminal] = rows else {
        return None;
    };
    (intent.stage == "dispatching"
        && terminal.stage == "completed"
        && intent.payload["mutation"].as_bool() == Some(mutation)
        && lowercase_hex(&intent.request_sha256, 32)
        && intent.request_sha256 == terminal.request_sha256
        && intent.id != terminal.id
        && [intent, terminal].iter().all(|row| {
            row.project_id == project
                && row.generation == stamp.generation
                && row.revision == stamp.revision
                && row.request_id == request
                && row.command == command
        }))
    .then_some((*intent, *terminal))
}

fn provider_result(receipt: &ProductionReceipt, generation: u64) -> Option<&Value> {
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
            .is_some_and(|value| lowercase_hex(value, 32))
    {
        return None;
    }
    original.get("data")
}

pub fn retained_mlt_close(
    project: Uuid,
    stamp: &RevisionStamp,
    start_request: &str,
    start_rows: &[&ProductionReceipt],
    result_rows: &[&ProductionReceipt],
) -> Option<RetainedMltClose> {
    let result_request = mlt_result_request(start_request)?;
    let (start_intent, start) = retained_pair(
        start_rows,
        project,
        stamp,
        start_request,
        START_COMMAND,
        true,
    )?;
    let (intent, terminal) = retained_pair(
        result_rows,
        project,
        stamp,
        &result_request,
        RESULT_COMMAND,
        false,
    )?;
    let original = &terminal.payload[TIMELINE_SOURCE];
    let source: OriginalMltTimeline = serde_json::from_value(original.clone()).ok()?;
    let native_ref = source.project_ref.strip_prefix("video:")?;
    if original != &intent.payload[TIMELINE_SOURCE]
        || source.schema != "motionwright.mlt-timeline-source/1"
        || !lowercase_hex(&source.connection_identity, 32)
        || start_intent.payload["connection"] != json!(source.connection_identity)
        || intent.payload["connection"] != json!(source.connection_identity)
        || source.provider_generation == 0
        || !lowercase_hex(native_ref, 16)
        || !lowercase_hex(&source.semantic_revision, 32)
        || !matches!(source.profile.as_str(), "lossless" | "lossless-video-only")
        || !source.job.starts_with("render:")
        || source.job.len() > 80
        || source.output.is_empty()
        || digest(RESULT_COMMAND, &json!({"job":source.job}))? != terminal.request_sha256
    {
        return None;
    }
    let started = provider_result(start, source.provider_generation)?;
    let result = provider_result(terminal, source.provider_generation)?;
    if intent.payload["job_ref"] != json!(source.job)
        || terminal.payload["job_ref"] != json!(source.job)
        || result["state"] != json!("succeeded")
        || !result.get("error").is_some_and(Value::is_null)
        || [started, result].iter().any(|data| {
            data["job"] != json!(source.job)
                || data["project_revision"] != json!(source.semantic_revision)
                || data["profile"] != json!(source.profile)
                || data["output"] != json!(source.output)
        })
        || result.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
        || result.pointer("/artifact/path").and_then(Value::as_str) != Some(source.output.as_str())
    {
        return None;
    }
    let artifact_sha256 = result
        .pointer("/artifact/sha256")?
        .as_str()
        .filter(|hash| lowercase_hex(hash, 32))?;
    let artifact_bytes = result
        .pointer("/artifact/bytes")?
        .as_u64()
        .filter(|size| *size > 0)?;
    let close_key = digest(
        CLOSE_COMMAND,
        &json!({
            "connection_identity": source.connection_identity,
            "provider_generation": source.provider_generation,
            "project_ref": source.project_ref,
        }),
    )?;
    let request_sha256 = digest(
        CLOSE_COMMAND,
        &json!({
            "project": source.project_ref, "expected_revision": source.semantic_revision,
        }),
    )?;
    Some(RetainedMltClose {
        source,
        result_id: terminal.id,
        artifact_sha256: artifact_sha256.into(),
        artifact_bytes,
        request_id: format!("mlt-owner-close:{close_key}"),
        request_sha256,
    })
}

type TimelineKey<'a> = (Uuid, u64, &'a str);

struct CloseAttempt<'a> {
    intent: &'a ProductionReceipt,
    terminal: Option<&'a ProductionReceipt>,
}

impl<'a> CloseAttempt<'a> {
    fn observe(&mut self, original_request: &str, receipt: &'a ProductionReceipt) {
        if let Some(previous) = self.terminal {
            if mlt_close_receipt_disposition(previous)
                == MltCloseDisposition::ConfirmationUnavailable
                && receipt.stage == "dispatching"
                && receipt.request_id == format!("{original_request}:after:{}", previous.id)
            {
                self.intent = receipt;
                self.terminal = None;
            }
        } else if self.intent.stage == "dispatching"
            && receipt.stage != "dispatching"
            && receipt.request_id == self.intent.request_id
        {
            self.terminal = Some(receipt);
        }
    }

    fn disposition(&self) -> MltCloseDisposition {
        self.terminal.map_or(
            MltCloseDisposition::OutcomeUnknown,
            mlt_close_receipt_disposition,
        )
    }
}

pub(crate) struct MltClosures<'a> {
    timelines: BTreeMap<TimelineKey<'a>, RetainedMltClose>,
    dispositions: BTreeMap<Uuid, MltCloseDisposition>,
}

impl<'a> MltClosures<'a> {
    pub(crate) fn new(receipts: &'a [ProductionReceipt]) -> Self {
        type TimelineRows<'a> = (
            Vec<(usize, &'a ProductionReceipt)>,
            Vec<(usize, &'a ProductionReceipt)>,
        );
        let mut rows: BTreeMap<TimelineKey<'a>, TimelineRows<'a>> = BTreeMap::new();
        for (position, receipt) in receipts.iter().enumerate() {
            let (root, start) = match receipt.command.as_str() {
                START_COMMAND => (receipt.request_id.strip_suffix(START_SUFFIX), true),
                RESULT_COMMAND => (receipt.request_id.strip_suffix(RESULT_SUFFIX), false),
                _ => continue,
            };
            let Some(root) = root else { continue };
            let entry = rows
                .entry((receipt.generation, receipt.revision, root))
                .or_default();
            let list = if start { &mut entry.0 } else { &mut entry.1 };
            if list.len() < 3 {
                list.push((position, receipt));
            }
        }

        let mut timelines = BTreeMap::new();
        let mut result_keys = BTreeMap::new();
        for (key, (start, result)) in rows {
            let (
                [(_, start_intent), (start_position, start_terminal)],
                [
                    (result_position, result_intent),
                    (terminal_position, result_terminal),
                ],
            ) = (start.as_slice(), result.as_slice())
            else {
                continue;
            };
            if start_position >= result_position {
                continue;
            }
            let stamp = RevisionStamp {
                resource: format!("project:{}", start_intent.project_id),
                generation: key.0,
                revision: key.1,
            };
            let Some(source) = retained_mlt_close(
                start_intent.project_id,
                &stamp,
                &start_intent.request_id,
                &[*start_intent, *start_terminal],
                &[*result_intent, *result_terminal],
            ) else {
                continue;
            };
            result_keys.insert(
                source.result_id,
                (key, *terminal_position, *result_terminal, *start_intent),
            );
            timelines.insert(key, source);
        }

        let mut attempts = BTreeMap::<Uuid, CloseAttempt<'a>>::new();
        let mut unordered = BTreeSet::new();
        for (position, receipt) in receipts.iter().enumerate() {
            if receipt.command != CLOSE_COMMAND
                || receipt.payload["mutation"].as_bool() != Some(true)
            {
                continue;
            }
            let Some(id) = receipt.payload["result_receipt"]
                .as_str()
                .and_then(|value| Uuid::parse_str(value).ok())
            else {
                continue;
            };
            let Some((key, result_position, terminal, start)) = result_keys.get(&id) else {
                continue;
            };
            let source = &timelines[key];
            if receipt.project_id != terminal.project_id
                || receipt.generation != terminal.generation
                || receipt.revision != terminal.revision
                || receipt.request_sha256 != source.request_sha256
                || receipt.payload["native_source"] != terminal.payload[TIMELINE_SOURCE]
                || receipt.payload["start_request"].as_str() != Some(start.request_id.as_str())
            {
                continue;
            }
            if position <= *result_position {
                if receipt.request_id == source.request_id {
                    unordered.insert(id);
                }
                continue;
            }
            if let Some(attempt) = attempts.get_mut(&id) {
                attempt.observe(&source.request_id, receipt);
            } else if receipt.request_id == source.request_id {
                // An orphan terminal leaves UNKNOWN until the exact intent can be proven.
                attempts.insert(
                    id,
                    CloseAttempt {
                        intent: receipt,
                        terminal: None,
                    },
                );
            }
        }
        Self {
            timelines,
            dispositions: attempts
                .into_iter()
                .map(|(id, attempt)| (id, attempt.disposition()))
                .chain(
                    unordered
                        .into_iter()
                        .map(|id| (id, MltCloseDisposition::OutcomeUnknown)),
                )
                .collect(),
        }
    }

    pub(crate) fn for_job(
        &self,
        job: &crate::ProductionJobProjection,
    ) -> Option<MltClosureProjection> {
        if job.provider != "driver:mlt-video" || job.state != crate::ProductionJobState::Succeeded {
            return None;
        }
        let root = job.root_request_id.strip_suffix(START_SUFFIX)?;
        let original = self.timelines.get(&(job.generation, job.revision, root))?;
        if original.source.job != job.job_ref
            || Some(original.source.provider_generation) != job.provider_generation
        {
            return None;
        }
        Some(MltClosureProjection {
            disposition: self.dispositions.get(&original.result_id).copied(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MltCleanupInventory {
    pub schema: &'static str,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub source_receipts_scanned: u64,
    pub confirmed_native_creations_current_revision: usize,
    pub confirmed_native_creations_historical: usize,
    pub unconfirmed_or_invalid_creation_requests: usize,
    pub observed_close_request_identities: usize,
    pub disposition: &'static str,
    pub complete_source_receipt_snapshot: bool,
    pub native_provider_liveness_verified: bool,
    pub foreground_cleanup_consent_obtained: bool,
    pub verified_native_project_cleanup: bool,
    pub automatic_destructive_cleanup_allowed: bool,
}

/// Only trusted, completed and provenance-admitted create receipts count as
/// confirmed projects. Do not expose provider-native references, connection
/// paths, full receipts, session material or other secrets to the WebView.
fn confirmed_identity(receipt: &ProductionReceipt) -> Option<(Uuid, u64, u64, String)> {
    let payload = &receipt.payload;
    if receipt.stage != "completed"
        || receipt.command != "driver.mlt-video.project.create"
        || payload
            .pointer("/result/provenance_checked")
            .and_then(Value::as_bool)
            != Some(true)
        || payload
            .pointer("/result/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some("driver:mlt-video")
    {
        return None;
    }
    let generation = payload
        .pointer("/result/execution/provenance/provider_generation")
        .and_then(Value::as_u64)?;
    let ref_id = payload
        .pointer("/result/data/project")
        .and_then(Value::as_str)?;
    let revision = payload
        .pointer("/result/data/revision")
        .and_then(Value::as_str)?;
    if ref_id.is_empty()
        || ref_id.len() > 80
        || ref_id.chars().any(char::is_control)
        || revision.len() != 64
        || !revision
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    {
        return None;
    }
    Some((
        receipt.generation,
        receipt.revision,
        generation,
        ref_id.into(),
    ))
}

/// Caller guarantees the source snapshot is complete at a stable watermark.
/// All source receipts must be scoped to this project. Any incomplete window
/// or failed watermark comparison is rejected by the service before calling.
pub(crate) fn derive_mlt_cleanup_inventory(
    project: &Project,
    watermark: &ProductionReceiptWatermark,
    receipts: &[ProductionReceipt],
) -> Option<MltCleanupInventory> {
    if receipts.len() as u64 != watermark.count
        || receipts.iter().any(|r| {
            r.project_id != project.id
                || r.generation != project.generation
                || r.revision > project.revision
        })
    {
        return None;
    }
    // UUIDv7 is the durable append-only receipt ordering, but a later
    // dispatching/unknown receipt cannot undo an earlier completed and
    // provenance-admitted native create. Keep every distinct confirmed
    // native project identity, per request AND creative source scope.
    //
    // Production storage intentionally allows an identical request ID/digest
    // and command to be observed in a later project revision. Neither a later
    // completed create nor a later UNKNOWN can erase the earlier revision's
    // independent evidence or uncertain allocation. The command dimension
    // additionally prevents malformed cross-command collisions in this
    // read-only projection, without treating a close as approved cleanup.
    let mut confirmed_creations: BTreeMap<(&str, Uuid, u64), BTreeSet<_>> = BTreeMap::new();
    let mut latest: BTreeMap<(&str, &str, Uuid, u64), &ProductionReceipt> = BTreeMap::new();
    for receipt in receipts {
        if receipt.command == "driver.mlt-video.project.create"
            && let Some(identity) = confirmed_identity(receipt)
        {
            confirmed_creations
                .entry((
                    receipt.request_id.as_str(),
                    receipt.generation,
                    receipt.revision,
                ))
                .or_default()
                .insert(identity);
        }
        if !matches!(
            receipt.command.as_str(),
            "driver.mlt-video.project.create" | "driver.mlt-video.project.close"
        ) {
            continue;
        }
        let key = (
            receipt.command.as_str(),
            receipt.request_id.as_str(),
            receipt.generation,
            receipt.revision,
        );
        if latest.get(&key).is_none_or(|old| old.id < receipt.id) {
            latest.insert(key, receipt);
        }
    }
    let mut created = BTreeSet::new();
    let mut ambiguous = 0usize;
    let mut closes = 0usize;
    for receipt in latest.into_values() {
        match receipt.command.as_str() {
            "driver.mlt-video.project.create" => {
                if let Some(identities) = confirmed_creations.get(&(
                    receipt.request_id.as_str(),
                    receipt.generation,
                    receipt.revision,
                )) {
                    // Another stage may be UNKNOWN or failed, but it cannot
                    // erase an already proven creation. Distinct completed
                    // references reflect distinct possible native resources.
                    created.extend(identities.iter().cloned());
                } else {
                    ambiguous += 1;
                }
            }
            "driver.mlt-video.project.close" => {
                // A close receipt without proof of interactive foreground
                // authorization cannot be promoted to a proven cleanup.
                closes += 1;
            }
            _ => unreachable!(),
        }
    }
    let current = created
        .iter()
        .filter(|(generation, revision, ..)| {
            *generation == project.generation && *revision == project.revision
        })
        .count();
    let historical = created.len() - current;
    let disposition = if closes > 0 {
        "close_observed_without_foreground_consent_proof"
    } else if ambiguous > 0 {
        "creation_outcome_not_fully_proven"
    } else if !created.is_empty() {
        "project_lifetime_unverified_foreground_consent_required"
    } else {
        "no_verified_mlt_project_creations_in_local_receipts"
    };
    Some(MltCleanupInventory {
        schema: SCHEMA,
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        source_receipts_scanned: watermark.count,
        confirmed_native_creations_current_revision: current,
        confirmed_native_creations_historical: historical,
        unconfirmed_or_invalid_creation_requests: ambiguous,
        observed_close_request_identities: closes,
        disposition,
        complete_source_receipt_snapshot: true,
        native_provider_liveness_verified: false,
        foreground_cleanup_consent_obtained: false,
        verified_native_project_cleanup: false,
        automatic_destructive_cleanup_allowed: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn receipt(
        project: &Project,
        request: &str,
        command: &str,
        stage: &str,
        payload: Value,
    ) -> ProductionReceipt {
        ProductionReceipt {
            id: Uuid::now_v7(),
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: request.into(),
            request_sha256: "ab".repeat(32),
            command: command.into(),
            stage: stage.into(),
            payload,
            created_at: "2026-10-12T18:00:00Z".into(),
        }
    }
    fn start(project: &Project, request: &str, ref_id: &str) -> ProductionReceipt {
        receipt(
            project,
            request,
            "driver.mlt-video.project.create",
            "completed",
            json!({"result":{"provenance_checked":true,
                "data":{"project":ref_id,"revision":"a".repeat(64)},
                "execution":{"provenance":{
                    "provider":"driver:mlt-video","provider_generation":17
                }}
            }}),
        )
    }
    fn project_and_receipts() -> (Project, Vec<ProductionReceipt>) {
        let project = Project::new("Owner MLT lifetime").unwrap();
        let receipts = vec![
            receipt(
                &project,
                "owner-create",
                "driver.mlt-video.project.create",
                "dispatching",
                json!({"mutation":true}),
            ),
            start(&project, "owner-create", "mlt-project-opaque"),
        ];
        (project, receipts)
    }
    fn derive(project: &Project, receipts: &[ProductionReceipt]) -> Option<MltCleanupInventory> {
        derive_mlt_cleanup_inventory(
            project,
            &ProductionReceiptWatermark {
                latest_id: receipts.iter().map(|r| r.id).max(),
                count: receipts.len() as u64,
            },
            receipts,
        )
    }

    #[test]
    fn one_acknowledged_project_is_unverified_live_not_safe_to_delete() {
        let (project, receipts) = project_and_receipts();
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 1);
        assert_eq!(inventory.confirmed_native_creations_historical, 0);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 0);
        assert_eq!(inventory.source_receipts_scanned, 2);
        assert!(!inventory.verified_native_project_cleanup);
        assert!(!inventory.native_provider_liveness_verified);
        assert!(!inventory.automatic_destructive_cleanup_allowed);
        let serialized = serde_json::to_string(&inventory).unwrap();
        assert!(!serialized.contains("mlt-project-opaque"));
        assert!(!serialized.contains("owner-create"));
    }

    #[test]
    fn unconfirmed_created_project_cannot_be_reported_as_real_or_cleaned() {
        let (project, mut receipts) = project_and_receipts();
        receipts.pop();
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 0);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 1);
        assert_eq!(inventory.disposition, "creation_outcome_not_fully_proven");
    }

    #[test]
    fn invalid_provenance_and_forged_project_reference_are_rejected() {
        let (project, mut receipts) = project_and_receipts();
        // Isolate the forged create; the other dispatching request is tested
        // separately and would correctly count as a second unknown.
        receipts.clear();
        let mut forged = start(&project, "untrusted", "foreign-ref");
        forged.payload["result"]["execution"]["provenance"]["provider"] = json!("driver:browser");
        receipts.push(forged);
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 0);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 1);
        receipts.last_mut().unwrap().payload["result"]["execution"]["provenance"]["provider"] =
            json!("driver:mlt-video");
        receipts.last_mut().unwrap().payload["result"]["data"]["revision"] = json!("invalid");
        assert_eq!(
            derive(&project, &receipts)
                .unwrap()
                .unconfirmed_or_invalid_creation_requests,
            1
        );
    }

    #[test]
    fn same_native_reference_from_different_provider_generation_is_distinct() {
        let (project, mut receipts) = project_and_receipts();
        let mut another = start(&project, "other-project-creation", "mlt-project-opaque");
        another.payload["result"]["execution"]["provenance"]["provider_generation"] = json!(18);
        receipts.push(another);
        assert_eq!(
            derive(&project, &receipts)
                .unwrap()
                .confirmed_native_creations_current_revision,
            2
        );
    }

    #[test]
    fn closed_command_never_proves_foreground_consent_or_success() {
        let (project, mut receipts) = project_and_receipts();
        receipts.push(receipt(
            &project,
            "close-intent",
            "driver.mlt-video.project.close",
            "completed",
            json!({"result":{"data":{"closed":true},
                "execution":{"provenance":{"provider":"driver:mlt-video"}}}}),
        ));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.observed_close_request_identities, 1);
        assert_eq!(
            inventory.disposition,
            "close_observed_without_foreground_consent_proof"
        );
        assert!(!inventory.verified_native_project_cleanup);
        assert!(!inventory.foreground_cleanup_consent_obtained);
    }

    #[test]
    fn same_create_request_id_across_revisions_retains_new_uncertain_allocation() {
        let (mut project, mut receipts) = project_and_receipts();
        // The database allows the same command, request ID and digest in a
        // later creative revision. A later UNKNOWN must not erase the
        // earlier completed native creation or its own uncertain attempt.
        project.revision += 1;
        receipts.push(receipt(
            &project,
            "owner-create",
            "driver.mlt-video.project.create",
            "outcome_unknown",
            json!({"error":{"code":"Timeout","outcome_known":false}}),
        ));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.source_receipts_scanned, 3);
        assert_eq!(inventory.confirmed_native_creations_current_revision, 0);
        assert_eq!(inventory.confirmed_native_creations_historical, 1);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 1);
        assert_eq!(inventory.disposition, "creation_outcome_not_fully_proven");
        assert!(!inventory.verified_native_project_cleanup);
        assert!(!inventory.automatic_destructive_cleanup_allowed);
    }

    #[test]
    fn same_create_request_id_across_revisions_retains_older_unknown_attempt() {
        let (mut project, mut receipts) = project_and_receipts();
        // The older dispatching attempt may have allocated a different
        // native project despite its absent terminal response. It must
        // remain UNKNOWN when a new revision completes using the same ID.
        receipts.retain(|receipt| receipt.stage != "completed");
        project.revision += 1;
        receipts.push(start(&project, "owner-create", "new-revision-project"));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.source_receipts_scanned, 2);
        assert_eq!(inventory.confirmed_native_creations_current_revision, 1);
        assert_eq!(inventory.confirmed_native_creations_historical, 0);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 1);
        assert_eq!(inventory.disposition, "creation_outcome_not_fully_proven");
        assert!(!inventory.native_provider_liveness_verified);
        assert!(!inventory.foreground_cleanup_consent_obtained);
        assert!(!inventory.automatic_destructive_cleanup_allowed);
    }

    #[test]
    fn close_reusing_create_request_id_cannot_erase_proven_native_creation() {
        let (project, mut receipts) = project_and_receipts();
        // A malformed or collided close request ID must remain a distinct
        // observed operation, not replace confirmed project.create evidence.
        receipts.push(receipt(
            &project,
            "owner-create",
            "driver.mlt-video.project.close",
            "completed",
            json!({"result":{"data":{"closed":true},
                "execution":{"provenance":{"provider":"driver:mlt-video"}}}}),
        ));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 1);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 0);
        assert_eq!(inventory.observed_close_request_identities, 1);
        assert_eq!(
            inventory.disposition,
            "close_observed_without_foreground_consent_proof"
        );
        assert!(!inventory.verified_native_project_cleanup);
        assert!(!inventory.foreground_cleanup_consent_obtained);
        assert!(!inventory.automatic_destructive_cleanup_allowed);
        let output = serde_json::to_string(&inventory).unwrap();
        assert!(!output.contains("mlt-project-opaque"));
        assert!(!output.contains("owner-create"));
    }

    #[test]
    fn collided_close_cannot_erase_unknown_creation_or_promote_success() {
        let (project, mut receipts) = project_and_receipts();
        // A create with unknown outcome could still exist in the provider.
        // Later close/failed responses sharing the same request token must
        // not convert the state to no project ever created.
        receipts.retain(|r| r.stage != "completed");
        receipts.push(receipt(
            &project,
            "owner-create",
            "driver.mlt-video.project.close",
            "outcome_unknown",
            json!({"error":{"code":"Timeout","outcome_known":false}}),
        ));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 0);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 1);
        assert_eq!(inventory.observed_close_request_identities, 1);
        assert_eq!(
            inventory.disposition,
            "close_observed_without_foreground_consent_proof"
        );
        assert!(!inventory.native_provider_liveness_verified);
        assert!(!inventory.verified_native_project_cleanup);
        assert!(!inventory.foreground_cleanup_consent_obtained);
    }

    #[test]
    fn foreign_project_incomplete_receipt_window_or_future_revision_rejects() {
        let (project, mut receipts) = project_and_receipts();
        let old_receipt_count = receipts.len();
        let truncated = derive_mlt_cleanup_inventory(
            &project,
            &ProductionReceiptWatermark {
                latest_id: None,
                count: old_receipt_count as u64 + 1,
            },
            &receipts,
        );
        assert!(truncated.is_none());
        receipts.last_mut().unwrap().project_id = Uuid::new_v4();
        assert!(derive(&project, &receipts).is_none());
        receipts.last_mut().unwrap().project_id = project.id;
        receipts.last_mut().unwrap().revision = project.revision + 1;
        assert!(derive(&project, &receipts).is_none());
    }

    #[test]
    fn confirmed_create_remains_visible_after_later_unknown_stage() {
        let (project, mut receipts) = project_and_receipts();
        receipts.push(receipt(
            &project,
            "owner-create",
            "driver.mlt-video.project.create",
            "outcome_unknown",
            json!({"error":{"code":"Timeout","outcome_known":false}}),
        ));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 1);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 0);
        assert!(!inventory.verified_native_project_cleanup);
        assert!(!inventory.automatic_destructive_cleanup_allowed);
    }

    #[test]
    fn repeated_completed_create_with_different_native_refs_preserves_both() {
        let (project, mut receipts) = project_and_receipts();
        receipts.push(start(&project, "owner-create", "second-opaque-project"));
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 2);
        assert_eq!(inventory.unconfirmed_or_invalid_creation_requests, 0);
        assert!(!inventory.automatic_destructive_cleanup_allowed);

        // Duplicate successful receipt for the exact same native ref is not
        // another distinct resource, regardless of how many times observed.
        receipts.push(start(&project, "owner-create", "second-opaque-project"));
        assert_eq!(
            derive(&project, &receipts)
                .unwrap()
                .confirmed_native_creations_current_revision,
            2
        );
    }

    #[test]
    fn source_scope_counts_historical_project_lifetimes_without_current_claim() {
        let (mut project, receipts) = project_and_receipts();
        project.revision += 1;
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 0);
        assert_eq!(inventory.confirmed_native_creations_historical, 1);
    }
}
