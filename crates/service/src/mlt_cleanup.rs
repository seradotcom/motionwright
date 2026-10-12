//! v0.1 owner-viewable MLT project lifetime inventory.
//!
//! This projects app-owned source receipts only. It never queries the live
//! Broker, issues grants, closes provider projects or fabricates liveness.
//! In particular, even a completed project.close cannot be promoted to a
//! VERIFIED cleanup without separate foreground Broker consent evidence.
use motionwright_domain::Project;
use motionwright_storage::{ProductionReceipt, ProductionReceiptWatermark};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

const SCHEMA: &str = "motionwright.v01-mlt-cleanup-inventory/1";

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
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
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
    // One logical request can have dispatching + completed receipts; only its
    // latest stage counts. UUIDv7 is the durable append-only receipt ordering.
    let mut latest: BTreeMap<&str, &ProductionReceipt> = BTreeMap::new();
    for receipt in receipts {
        if !matches!(
            receipt.command.as_str(),
            "driver.mlt-video.project.create" | "driver.mlt-video.project.close"
        ) {
            continue;
        }
        match latest.get(receipt.request_id.as_str()) {
            Some(old) if old.id >= receipt.id => {}
            _ => {
                latest.insert(receipt.request_id.as_str(), receipt);
            }
        }
    }
    let mut created = BTreeSet::new();
    let mut ambiguous = 0usize;
    let mut closes = 0usize;
    for receipt in latest.into_values() {
        match receipt.command.as_str() {
            "driver.mlt-video.project.create" => {
                if let Some(key) = confirmed_identity(receipt) {
                    created.insert(key);
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
        receipts.pop();
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
    fn source_scope_counts_historical_project_lifetimes_without_current_claim() {
        let (mut project, mut receipts) = project_and_receipts();
        project.revision += 1;
        let inventory = derive(&project, &receipts).unwrap();
        assert_eq!(inventory.confirmed_native_creations_current_revision, 0);
        assert_eq!(inventory.confirmed_native_creations_historical, 1);
    }
}
