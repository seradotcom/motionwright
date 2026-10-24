//! Read-only admission of the original synchronous audio receipt history.
//!
//! A later completed row is not reconciliation of an earlier UNKNOWN for the
//! same atomic audio request. Render status/result keys remain independent;
//! this module neither interprets those jobs nor grants a retry or Store write.
use super::{
    AudioReceiptContext, Error, ErrorCode, MEASURE_COMMAND, MUX_COMMAND, NativeResult,
    ProductionReceipt, RevisionStamp, SourceBinding, Uuid, stale, storage_error,
};
use motionwright_service::StudioService;
use std::collections::HashSet;

// Local evidence-read budget, aligned with the existing service job snapshot
// ceiling. Exceeding it refuses proof; it is not a Store or provider limit.
const MAX_HISTORY_RECEIPTS: u64 = 20_000;

/// Visits the entire bounded stable snapshot, even after a selection is found.
/// Only the caller's selected row is cloned; no parallel history is retained.
fn visit_stable_history(
    service: &StudioService,
    project_id: Uuid,
    visit: impl FnMut(&ProductionReceipt) -> NativeResult<()>,
) -> NativeResult<()> {
    visit_history(service, project_id, false, visit)
}

fn visit_history(
    service: &StudioService,
    project_id: Uuid,
    allow_empty: bool,
    mut visit: impl FnMut(&ProductionReceipt) -> NativeResult<()>,
) -> NativeResult<()> {
    let watermark = service
        .production_receipt_watermark(project_id)
        .map_err(storage_error)?;
    if allow_empty && watermark.count == 0 && watermark.latest_id.is_none() {
        if service
            .production_receipt_watermark(project_id)
            .map_err(storage_error)?
            == watermark
        {
            return Ok(());
        }
        return Err(stale("Audio discovery empty snapshot changed"));
    }
    if watermark.count == 0 || watermark.latest_id.is_none() {
        return Err(stale("Audio receipt history is incomplete"));
    }
    if watermark.count > MAX_HISTORY_RECEIPTS {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Audio receipt history exceeds the bounded complete-read budget; no proof was admitted",
        ));
    }
    let mut before = None;
    let mut observed = 0_u64;
    let mut ids = HashSet::new();
    loop {
        let page = service
            .production_receipts_snapshot_page(project_id, &watermark, before, 256)
            .map_err(storage_error)?;
        if page.is_empty() {
            break;
        }
        let next = page.last().map(|receipt| receipt.id);
        if next == before {
            return Err(stale("Audio receipt cursor did not advance"));
        }
        let full_page = page.len() == 256;
        for receipt in &page {
            observed = observed
                .checked_add(1)
                .ok_or_else(|| stale("Audio receipt history count overflow"))?;
            if observed > watermark.count
                || !ids.insert(receipt.id)
                || receipt.project_id != project_id
            {
                return Err(stale(
                    "Audio receipt snapshot contains duplicate, foreign or excess observations",
                ));
            }
            visit(receipt)?;
        }
        before = next;
        if !full_page {
            break;
        }
    }
    if observed != watermark.count
        || service
            .production_receipt_watermark(project_id)
            .map_err(storage_error)?
            != watermark
    {
        return Err(stale(
            "Audio receipt history is not a complete stable snapshot",
        ));
    }
    Ok(())
}

pub(super) fn selected_decoded_measurement(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    receipt_id: Uuid,
) -> NativeResult<ProductionReceipt> {
    let mut selected = None;
    visit_stable_history(service, project_id, |receipt| {
        if receipt.id == receipt_id {
            selected = Some(receipt.clone());
        }
        Ok(())
    })?;
    let selected = selected.ok_or_else(|| {
        Error::new(
            ErrorCode::NotFound,
            "Selected audio receipt is absent from the complete project snapshot",
        )
    })?;
    if selected.generation != expected.generation
        || selected.revision != expected.revision
        || selected.command != MEASURE_COMMAND
        || selected.stage != "completed"
        || !selected.request_id.ends_with(":measure-decoded")
    {
        return Err(stale(
            "Selected audio receipt is not the current completed decoded measurement",
        ));
    }
    verify_selected_atomic_history(service, &selected)?;
    Ok(selected)
}

pub(super) fn completed_audio_receipt(
    service: &StudioService,
    source: &SourceBinding,
    request_id: &str,
) -> NativeResult<ProductionReceipt> {
    let receipt = service
        .latest_production_receipt(source.project_id, request_id)
        .map_err(storage_error)?
        .ok_or_else(|| {
            Error::new(
                ErrorCode::Conflict,
                "Audio proof needs the original completed receipt; inspect uncertain work without redispatch",
            )
        })?;
    // A non-completed latest observation cannot erase an earlier UNKNOWN.
    // Admit the bounded stable history before classifying the latest stage.
    verify_original_atomic_history(service, source, &receipt)?;
    if receipt.stage != "completed" {
        let error = Error::new(
            ErrorCode::Conflict,
            "Audio proof needs the original completed receipt; inspect uncertain work without redispatch",
        );
        return Err(if receipt.stage == "outcome_unknown" {
            error.uncertain()
        } else {
            error
        });
    }
    Ok(receipt)
}

fn verify_original_atomic_history(
    service: &StudioService,
    source: &SourceBinding,
    selected: &ProductionReceipt,
) -> NativeResult<()> {
    if selected.project_id != source.project_id
        || selected.generation != source.generation
        || selected.revision != source.revision
        || (selected.command != MEASURE_COMMAND && selected.command != MUX_COMMAND)
    {
        return Err(stale(
            "Audio receipt history belongs to another source or command",
        ));
    }
    verify_selected_atomic_history(service, selected)
}

fn verify_selected_atomic_history(
    service: &StudioService,
    selected: &ProductionReceipt,
) -> NativeResult<()> {
    // Both proof-admitting callers retain their own completed-stage guard.
    // This scan must also preserve uncertainty for a non-completed latest row.
    if selected.command != MEASURE_COMMAND && selected.command != MUX_COMMAND {
        return Err(stale(
            "Selected atomic audio history is not an audio command",
        ));
    }
    let (selected_context, mut selector_mismatch) = if selected.command == MEASURE_COMMAND {
        match AudioReceiptContext::from_receipt(selected) {
            Ok(context) => (context, false),
            Err(_) => (None, true),
        }
    } else {
        (None, false)
    };
    let mut first_matching_seen = false;
    let mut selected_is_latest = true;
    let mut selected_seen = false;
    let mut same_atomic_unknown = false;
    visit_stable_history(service, selected.project_id, |receipt| {
        if receipt.request_id != selected.request_id {
            return Ok(());
        }
        if receipt.project_id != selected.project_id
            || receipt.generation != selected.generation
            || receipt.revision != selected.revision
            || receipt.command != selected.command
            || receipt.request_sha256 != selected.request_sha256
        {
            return Err(stale(
                "Audio request history changed its immutable source identity",
            ));
        }
        // Pages are newest first by committed ordinal, not UUID or time.
        if !first_matching_seen {
            first_matching_seen = true;
            selected_is_latest = receipt == selected;
        }
        selected_seen |= receipt.id == selected.id;
        same_atomic_unknown |= receipt.stage == "outcome_unknown";
        if selected.command == MEASURE_COMMAND {
            selector_mismatch |= match AudioReceiptContext::from_receipt(receipt) {
                Ok(context) => context != selected_context,
                Err(_) => true,
            };
        }
        Ok(())
    })?;
    if !selected_seen {
        return Err(stale(
            "Selected audio observation is absent from its history",
        ));
    }
    if same_atomic_unknown {
        // These exact operations are synchronous. No admitted original
        // status/result reconciliation for their atomic key exists here.
        // Metadata inconsistency cannot turn UNKNOWN into a known result.
        return Err(Error::new(
            ErrorCode::Conflict,
            "An original synchronous audio request remains UNKNOWN; a later observation is not reconciliation",
        )
        .uncertain());
    }
    if !selected_is_latest {
        return Err(stale(
            "Audio observation changed before its history snapshot",
        ));
    }
    if selector_mismatch {
        return Err(stale(
            "Audio dispatch and terminal observations disagree on original selector metadata",
        ));
    }
    Ok(())
}

/// Project-scoped discovery uses the same complete snapshot reader as proof
/// admission. Only small hints survive this call; raw receipt data stays private.
pub(super) fn discovery_inventory(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
) -> NativeResult<super::AudioQcReceiptInventory> {
    use std::collections::BTreeMap;
    struct DecodedHint {
        id: Uuid,
        completed: bool,
        context: Option<AudioReceiptContext>,
    }
    struct MuxHint {
        completed: bool,
        digest: Option<String>,
    }
    let project = service.project(project_id).map_err(storage_error)?;
    super::require_current(&project, expected)?;
    if !project
        .deliverables
        .iter()
        .any(|profile| profile.id == deliverable_id)
    {
        return Err(stale("Audio discovery output profile is absent"));
    }
    let initial_watermark = service
        .production_receipt_watermark(project_id)
        .map_err(storage_error)?;
    let mut decoded = BTreeMap::<String, DecodedHint>::new();
    let mut muxes = BTreeMap::<String, MuxHint>::new();
    visit_history(service, project_id, true, |row| {
        if row.generation != expected.generation || row.revision != expected.revision {
            return Ok(());
        }
        // The traversal is newest first. A later failure/UNKNOWN/dispatch
        // must suppress a former completed row for the same original key.
        if row.command == MEASURE_COMMAND && row.request_id.ends_with(":measure-decoded") {
            if let std::collections::btree_map::Entry::Vacant(entry) =
                decoded.entry(row.request_id.clone())
            {
                let namespace = row
                    .request_id
                    .strip_suffix(":measure-decoded")
                    .ok_or_else(|| stale("Audio discovery request is malformed"))?;
                if namespace.len() != 54
                    || !namespace.starts_with("ma")
                    || !namespace[2..]
                        .bytes()
                        .all(|byte| matches!(byte, b'a'..=b'z' | b'2'..=b'7'))
                {
                    return Err(stale("Audio discovery request namespace is malformed"));
                }
                entry.insert(DecodedHint {
                    id: row.id,
                    completed: row.stage == "completed",
                    context: AudioReceiptContext::from_receipt(row)?,
                });
            }
        } else if row.command == MUX_COMMAND {
            let std::collections::btree_map::Entry::Vacant(entry) =
                muxes.entry(row.request_id.clone())
            else {
                return Ok(());
            };
            let completed = row.stage == "completed";
            let digest = if completed {
                row.payload
                    .pointer("/result/data/artifact")
                    .and_then(|value| {
                        super::artifact_identity(value, super::MLT_MASTER_MAX_BYTES).ok()
                    })
                    .map(|artifact| artifact.sha256)
            } else {
                None
            };
            entry.insert(MuxHint { completed, digest });
        }
        Ok(())
    })?;
    let mut items = Vec::new();
    let mut legacy_unbound_count = 0_u64;
    let mut unavailable_count = 0_u64;
    for (request_id, decoded) in decoded {
        let Some(context) = decoded.context else {
            legacy_unbound_count += 1;
            continue;
        };
        if context.deliverable_id != deliverable_id {
            continue;
        }
        if !decoded.completed {
            unavailable_count += 1;
            continue;
        }
        let namespace = request_id
            .strip_suffix(":measure-decoded")
            .ok_or_else(|| stale("Audio discovery request is malformed"))?;
        let mux_key = format!("{}{}", namespace, context.lane()?.mux_suffix());
        let Some(parent) = muxes.get(&mux_key) else {
            unavailable_count += 1;
            continue;
        };
        if !parent.completed {
            unavailable_count += 1;
            continue;
        }
        let master_sha256 = parent
            .digest
            .clone()
            .ok_or_else(|| stale("Original mux receipt has no bounded master artifact identity"))?;
        items.push(super::AudioQcReceiptHint {
            decoded_measurement_receipt_id: decoded.id,
            master_sha256,
        });
        if items.len() > 128 {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Audio discovery exceeds its complete inventory budget; no truncated inventory was returned",
            ));
        }
    }
    super::require_current(
        &service.project(project_id).map_err(storage_error)?,
        expected,
    )?;
    if service
        .production_receipt_watermark(project_id)
        .map_err(storage_error)?
        != initial_watermark
    {
        return Err(stale("Audio receipts changed during discovery projection"));
    }
    Ok(super::AudioQcReceiptInventory {
        project_id,
        generation: expected.generation,
        revision: expected.revision,
        deliverable_id,
        items,
        legacy_unbound_count,
        unavailable_count,
    })
}
