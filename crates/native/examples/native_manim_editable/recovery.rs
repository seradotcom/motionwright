//! Source-only recovery of three already committed editable-source changes.
//!
//! The caller must restore authenticated private working copies of both Stores.
//! Source bytes are rehydrated through canonical capture into new managed CAS
//! roots; copied root anchors are never admitted or rewritten. This module does
//! not authenticate a ciphertext, grant Native authority, open a native session,
//! import an Asset, or call Store::apply.
//! The original failed edit report is never overwritten or manufactured.
use super::*;
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyOutcome, ApplyResultObservation, ProjectEvent, Store,
    VerifiedApplyReceiptV1, apply_request_digest_v1,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct EditIntent {
    schema: String,
    project_id: Uuid,
    expected: RevisionStamp,
    request_id: String,
    original_source_sha256: String,
    edited_source_sha256: String,
    automatic_retry: bool,
    provider_dispatch_performed: bool,
}

fn recorded_change(
    service: &StudioService,
    before: &Project,
    event: &ProjectEvent,
    request: &str,
) -> SourceResult<(Project, VerifiedApplyReceiptV1)> {
    let next = before.revision.checked_add(1).ok_or("revision overflow")?;
    if event.revision != next {
        return Err("source recovery event is not the next committed revision".into());
    }
    let request_sha = apply_request_digest_v1(before.id, &event.change)?;
    let observation = service.store().lock().lookup_apply_result_v1(
        before.id,
        before.generation,
        request,
        &request_sha,
        APPLY_REQUEST_ENCODING_V1,
    )?;
    let ApplyResultObservation::KnownVerifiedV1 { binding, outcome } = observation else {
        return Err("source recovery has no verified committed receipt; no retry".into());
    };
    let ApplyOutcome {
        project,
        previous_revision,
        request_id,
        replayed,
    } = *outcome;
    if previous_revision != before.revision || request_id != request || replayed {
        return Err("source recovery did not observe the original non-replayed result".into());
    }
    let mut expected = before.clone();
    expected.apply_change(&event.change)?;
    expected.commit_revision(next, &event.change)?;
    expected.updated_at = project.updated_at;
    if expected != project {
        return Err(
            "source recovery receipt changed content outside its original typed event".into(),
        );
    }
    Ok((project, *binding))
}

fn events(service: &StudioService, id: Uuid) -> SourceResult<Vec<ProjectEvent>> {
    let rows = service.store().lock().event_records_since(id, 0, 256)?;
    if rows.len() >= 256 {
        return Err("source recovery event history exceeds its closed read budget".into());
    }
    Ok(rows)
}

fn receipts(service: &StudioService, id: Uuid) -> SourceResult<serde_json::Value> {
    let rows = service.production_receipts(id, 256)?;
    if rows.len() >= 256 {
        return Err("source recovery native receipt history exceeds its closed read budget".into());
    }
    Ok(serde_json::to_value(rows)?)
}

fn source_asset(event: &ProjectEvent, expected_sha: &str) -> SourceResult<Uuid> {
    let Change::AddAsset { asset } = &event.change else {
        return Err("source recovery expects exactly two original source imports".into());
    };
    if asset.media_type != SOURCE_BUNDLE_MIME_V2
        || asset.content_sha256.as_deref() != Some(expected_sha)
    {
        return Err("source recovery import differs from the original intent bytes".into());
    }
    Ok(asset.id)
}

fn publish_recovered_blob(
    service: &StudioService,
    bytes: &[u8],
    expected: &str,
) -> SourceResult<()> {
    let handle = service.store();
    let store = handle.lock();
    let staged =
        store.stage_file_import_bytes_with_limit(bytes, profile::MAX_BUNDLE_BYTES as u64)?;
    if staged.descriptor().sha256 != expected
        || staged.descriptor().size_bytes != bytes.len() as u64
    {
        return Err("source recovery capture differs from authenticated source bytes".into());
    }
    let published = store.publish_file_import(&staged)?;
    if published.sha256 != expected
        || published.size_bytes != bytes.len() as u64
        || store
            .read_blob(expected, profile::MAX_BUNDLE_BYTES as u64)?
            .as_slice()
            != bytes
    {
        return Err("source recovery new CAS publication differs after readback".into());
    }
    Ok(())
}

/// Observe the original commits and create a new source-recovery export/report.
/// Store::open may maintain metadata in the private working copies. New CAS
/// identities are initialized by Store and capture authenticated source bytes. All
/// Projects, typed events and production receipts are compared again before
/// publication. The authenticated reference copies must remain separate.
pub(crate) fn recover_edit(
    before_database: &Path,
    current_database: &Path,
    original_bundle: &Path,
    edited_bundle: &Path,
    intent_path: &Path,
    output: &Path,
) -> SourceResult<serde_json::Value> {
    let before_cas = output.with_extension("before-cas");
    let current_cas = output.with_extension("current-cas");
    recover_edit_with_roots(
        before_database,
        current_database,
        original_bundle,
        edited_bundle,
        intent_path,
        output,
        (&before_cas, &current_cas),
    )
}

/// Restore into fresh default CAS roots for a later, separately authorized Host
/// owner. This only observes existing commits and captures original source bytes.
pub(crate) fn recover_edit_default_cas(
    before_database: &Path,
    current_database: &Path,
    original_bundle: &Path,
    edited_bundle: &Path,
    intent_path: &Path,
    output: &Path,
) -> SourceResult<serde_json::Value> {
    let before_cas = before_database
        .parent()
        .ok_or("before Store parent absent")?
        .join("blobs");
    let current_cas = current_database
        .parent()
        .ok_or("current Store parent absent")?
        .join("blobs");
    recover_edit_with_roots(
        before_database,
        current_database,
        original_bundle,
        edited_bundle,
        intent_path,
        output,
        (&before_cas, &current_cas),
    )
}

fn recover_edit_with_roots(
    before_database: &Path,
    current_database: &Path,
    original_bundle: &Path,
    edited_bundle: &Path,
    intent_path: &Path,
    output: &Path,
    cas_roots: (&Path, &Path),
) -> SourceResult<serde_json::Value> {
    let current_export = output.with_extension("current.mwedit.zip");
    let (before_cas, current_cas) = cas_roots;
    if !output.is_absolute()
        || !output.parent().is_some_and(Path::is_dir)
        || output.try_exists()?
        || current_export.try_exists()?
        || before_cas.try_exists()?
        || current_cas.try_exists()?
        || !before_cas.is_absolute()
        || !current_cas.is_absolute()
        || before_cas.starts_with(current_cas)
        || current_cas.starts_with(before_cas)
        || fs::canonicalize(before_database)? == fs::canonicalize(current_database)?
    {
        return Err(
            "source recovery requires distinct retained Stores and new owned outputs".into(),
        );
    }
    let before_bytes = read(before_database, 16 * 1024 * 1024)?;
    let current_bytes = read(current_database, 16 * 1024 * 1024)?;
    let intent_bytes = read(intent_path, 8192)?;
    let intent: EditIntent = serde_json::from_slice(&intent_bytes)?;
    let original_bytes = read(original_bundle, profile::MAX_BUNDLE_BYTES)?;
    let edited_bytes = read(edited_bundle, profile::MAX_BUNDLE_BYTES)?;
    if intent.schema != "motionwright.manim.editable.source-edit-intent/1"
        || intent.automatic_retry
        || intent.provider_dispatch_performed
        || intent.request_id.is_empty()
        || intent.request_id.len() > 64
        || intent.request_id.chars().any(char::is_control)
        || hash(&original_bytes) != intent.original_source_sha256
        || hash(&edited_bytes) != intent.edited_source_sha256
    {
        return Err("source recovery intent or original bundle binding differs".into());
    }
    let original = profile::inspect_source_bundle(&original_bytes)?;
    let edited = profile::inspect_source_bundle(&edited_bytes)?;
    let mut changed_plan = original.plan.clone();
    let text_nodes: Vec<_> = changed_plan
        .nodes
        .iter()
        .enumerate()
        .filter(|(_, node)| node.typography.is_some())
        .map(|(index, _)| index)
        .collect();
    if text_nodes.len() != 1
        || changed_plan.nodes[text_nodes[0]]
            .typography
            .as_ref()
            .unwrap()
            .text
            != ORIGINAL_TEXT
    {
        return Err("source recovery requires the original single equation title".into());
    }
    let node_id = changed_plan.nodes[text_nodes[0]].node_id;
    changed_plan.nodes[text_nodes[0]]
        .typography
        .as_mut()
        .unwrap()
        .text = EDITED_TEXT.into();
    if serde_json::to_value(&changed_plan)? != serde_json::to_value(&edited.plan)?
        || profile::source_bundle(&changed_plan, &fonts(&original))? != edited_bytes
    {
        return Err(
            "source recovery bundle contains more than the one authorized text change".into(),
        );
    }
    // Physical identity cannot survive copying an old managed CAS. Create new
    // roots through the official initializer instead of deleting or forging its
    // retained anchor. These copies receive no original root/Native authority.
    let before_service = StudioService::from_store(Store::open_with_managed_blob_root(
        before_database,
        before_cas,
    )?);
    let current_service = StudioService::from_store(Store::open_with_managed_blob_root(
        current_database,
        current_cas,
    )?);
    let before = before_service.project(intent.project_id)?;
    let current = current_service.project(intent.project_id)?;
    let next = before.revision.checked_add(3).ok_or("revision overflow")?;
    if RevisionStamp::from(&before) != intent.expected
        || current.generation != before.generation
        || current.revision != next
        || original.plan.binding.project_id != before.id
        || original.plan.binding.generation != before.generation
        || original.plan.binding.revision != before.revision
    {
        return Err(
            "source recovery Store snapshots do not bracket the original three commits".into(),
        );
    }
    let before_events = events(&before_service, before.id)?;
    let current_events = events(&current_service, current.id)?;
    let prior_receipts = receipts(&before_service, before.id)?;
    if current_events.len() != before_events.len() + 3
        || serde_json::to_value(&current_events[..before_events.len()])?
            != serde_json::to_value(&before_events)?
        || receipts(&current_service, current.id)? != prior_receipts
    {
        return Err("source recovery changed prior events or native receipts".into());
    }
    let changes = &current_events[before_events.len()..];
    let original_id = source_asset(&changes[0], &intent.original_source_sha256)?;
    let edited_id = source_asset(&changes[1], &intent.edited_source_sha256)?;
    let text_change = Change::UpdateCanvasText {
        scene_id: original.plan.binding.scene_id,
        node_id,
        text: Some(EDITED_TEXT.into()),
    };
    if original_id == edited_id
        || before
            .assets
            .iter()
            .any(|asset| asset.id == original_id || asset.id == edited_id)
        || changes[2].change != text_change
    {
        return Err(
            "source recovery does not contain the two new sources and exact typed text event"
                .into(),
        );
    }
    let (attached_original, original_binding) = recorded_change(
        &current_service,
        &before,
        &changes[0],
        &format!("{}:attach-original", intent.request_id),
    )?;
    let (attached, edited_binding) = recorded_change(
        &current_service,
        &attached_original,
        &changes[1],
        &format!("{}:attach-edited", intent.request_id),
    )?;
    let (applied, text_binding) = recorded_change(
        &current_service,
        &attached,
        &changes[2],
        &format!("{}:apply-text", intent.request_id),
    )?;
    verify_source_edit_commit(&attached, &applied, &text_change)?;
    if applied != current {
        return Err("source recovery current Store is not the original committed result".into());
    }
    let original_fonts = fonts(&original);
    let font_name = format!("fonts/{EDITABLE_FONT_SHA256}.ttf");
    if original_fonts.len() != 1
        || original_fonts.get(&font_name).map(Vec::as_slice) != Some(EDITABLE_FONT)
    {
        return Err("source recovery original licensed font closure differs".into());
    }
    publish_recovered_blob(&before_service, EDITABLE_FONT, EDITABLE_FONT_SHA256)?;
    publish_recovered_blob(&current_service, EDITABLE_FONT, EDITABLE_FONT_SHA256)?;
    publish_recovered_blob(
        &current_service,
        &original_bytes,
        &intent.original_source_sha256,
    )?;
    publish_recovered_blob(
        &current_service,
        &edited_bytes,
        &intent.edited_source_sha256,
    )?;
    let original_prepared = prepare_saved_manim_editable_source(
        &before_service,
        before.id,
        &intent.expected,
        original.plan.binding.scene_id,
        original.plan.binding.deliverable_id,
    )?;
    if original_prepared.entries != original.entries {
        return Err(
            "source recovery original package differs from the preceding saved Store".into(),
        );
    }
    for (sha, bytes) in [
        (&intent.original_source_sha256, &original_bytes),
        (&intent.edited_source_sha256, &edited_bytes),
    ] {
        if current_service
            .store()
            .lock()
            .read_blob(sha, profile::MAX_BUNDLE_BYTES as u64)?
            != *bytes
        {
            return Err(
                "source recovery CAS bytes differ from the authenticated input bundles".into(),
            );
        }
    }
    let inspected = inspect_saved_manim_editable_source(
        &current_service,
        current.id,
        &RevisionStamp::from(&current),
        edited_id,
    )?;
    let exported = export_saved_manim_editable_source_bundle(
        &current_service,
        current.id,
        &RevisionStamp::from(&current),
        original.plan.binding.scene_id,
        original.plan.binding.deliverable_id,
    )?;
    let package = profile::inspect_source_bundle(&exported)?;
    changed_plan.binding = package.plan.binding.clone();
    if package.plan.binding.project_id != current.id
        || package.plan.binding.generation != current.generation
        || package.plan.binding.revision != current.revision
        || serde_json::to_value(&package.plan)? != serde_json::to_value(&changed_plan)?
        || serde_json::to_value(&inspected.current_plan)? != serde_json::to_value(&package.plan)?
        || current_service.project(current.id)? != current
        || before_service.project(before.id)? != before
        || serde_json::to_value(events(&current_service, current.id)?)?
            != serde_json::to_value(&current_events)?
        || serde_json::to_value(events(&before_service, before.id)?)?
            != serde_json::to_value(&before_events)?
        || receipts(&current_service, current.id)? != prior_receipts
        || receipts(&before_service, before.id)? != prior_receipts
    {
        return Err("source recovery export or retained Store observations changed".into());
    }
    retain_seed_fixture(&current_export, &exported)?;
    let fresh_export = read(&current_export, profile::MAX_BUNDLE_BYTES)?;
    if fresh_export != exported
        || profile::inspect_source_bundle(&fresh_export)?.entries != package.entries
    {
        return Err("source recovery export differs after physical file readback".into());
    }
    let report = json!({
        "schema":"motionwright.manim.editable.committed-source-recovery/1",
        "status":"SOURCE_COMMITTED_RECOVERED", "scope":"SOURCE_ONLY_NO_NATIVE_AUTHORITY",
        "input_sha256":{"before_database":hash(&before_bytes),"current_database":hash(&current_bytes),
            "original_bundle":hash(&original_bytes),"edited_bundle":hash(&edited_bytes),"intent":hash(&intent_bytes)},
        "original_intent":intent,"original_stamp":RevisionStamp::from(&before),
        "attached_stamp":RevisionStamp::from(&attached),"current_stamp":RevisionStamp::from(&current),
        "original_source_asset_id":original_id,"edited_source_asset_id":edited_id,
        "committed_bindings":[original_binding,edited_binding,text_binding],
        "current_source_inspection":inspected,"prepared_plan":package.plan,"closure":package.closure,
        "current_export_sha256":hash(&exported),"current_export_bytes":exported.len(),
        "source_assets_original_bytes_verified":true,"prior_events_and_native_receipts_unchanged":true,
        "fresh_source_export_file_readback":true,
        "typed_commit_lookup_performed":true,"original_edit_report_recreated":false,
        "working_cas_provenance":"CAS_REHYDRATED_NEW_IDENTITY",
        "working_cas_blobs_published":4,"original_cas_identity_claimed":false,
        "project_changes_applied":0,
        "source_mutation_performed":false,"provider_dispatch_performed":false,"native_execution_performed":false,
        "native_bundle_reopen_verified":false,"inherited_native_results_revalidated":false,
        "human_acceptance":false,"automatic_retry":false,
    });
    new_report(output, &report)?;
    Ok(report)
}

/// Open both recovered Stores in a fresh process after one original Native phase.
/// The recovered-source receipt has its own schema; an absent historical edit
/// report is never reconstructed or used as Native authority.
pub(crate) fn readback_recovered(
    before_database: &Path,
    current_database: &Path,
    recovery_path: &Path,
    native_path: &Path,
    output_root: &Path,
    output: &Path,
) -> SourceResult<serde_json::Value> {
    let recovery_raw = read(recovery_path, 4 * 1024 * 1024)?;
    let recovery: serde_json::Value = serde_json::from_slice(&recovery_raw)?;
    if recovery["schema"] != "motionwright.manim.editable.committed-source-recovery/1"
        || recovery["status"] != "SOURCE_COMMITTED_RECOVERED"
        || recovery["scope"] != "SOURCE_ONLY_NO_NATIVE_AUTHORITY"
        || recovery["working_cas_provenance"] != "CAS_REHYDRATED_NEW_IDENTITY"
        || recovery["original_cas_identity_claimed"] != false
        || recovery["project_changes_applied"] != 0
        || recovery["original_edit_report_recreated"] != false
    {
        return Err(
            "committed source recovery receipt is not an original source-only observation".into(),
        );
    }
    let intent: EditIntent = serde_json::from_value(recovery["original_intent"].clone())?;
    if intent.schema != "motionwright.manim.editable.source-edit-intent/1"
        || intent.automatic_retry
        || intent.provider_dispatch_performed
        || intent.request_id.is_empty()
        || intent.request_id.len() > 64
        || intent.request_id.chars().any(char::is_control)
    {
        return Err("committed source recovery original intent is invalid".into());
    }
    let before_service = StudioService::open(before_database)?;
    let current_service = StudioService::open(current_database)?;
    let before = before_service.project(intent.project_id)?;
    let current = current_service.project(intent.project_id)?;
    if intent.expected != RevisionStamp::from(&before)
        || recovery["original_stamp"] != serde_json::to_value(RevisionStamp::from(&before))?
        || recovery["current_stamp"] != serde_json::to_value(RevisionStamp::from(&current))?
        || before.generation != current.generation
        || current.revision != before.revision.checked_add(3).ok_or("revision overflow")?
    {
        return Err("committed source recovery Store stamps changed".into());
    }
    let before_events = events(&before_service, before.id)?;
    let current_events = events(&current_service, current.id)?;
    if current_events.len() != before_events.len() + 3
        || serde_json::to_value(&current_events[..before_events.len()])?
            != serde_json::to_value(&before_events)?
    {
        return Err("committed source recovery event prefix changed".into());
    }
    let changes = &current_events[before_events.len()..];
    let original_id = source_asset(&changes[0], &intent.original_source_sha256)?;
    let edited_id = source_asset(&changes[1], &intent.edited_source_sha256)?;
    let (original_attached, first_binding) = recorded_change(
        &current_service,
        &before,
        &changes[0],
        &format!("{}:attach-original", intent.request_id),
    )?;
    let (attached, second_binding) = recorded_change(
        &current_service,
        &original_attached,
        &changes[1],
        &format!("{}:attach-edited", intent.request_id),
    )?;
    let (applied, third_binding) = recorded_change(
        &current_service,
        &attached,
        &changes[2],
        &format!("{}:apply-text", intent.request_id),
    )?;
    verify_source_edit_commit(&attached, &applied, &changes[2].change)?;
    let bindings = serde_json::to_value([first_binding, second_binding, third_binding])?;
    if applied != current
        || bindings != recovery["committed_bindings"]
        || recovery["original_source_asset_id"] != json!(original_id)
        || recovery["edited_source_asset_id"] != json!(edited_id)
        || recovery["attached_stamp"] != serde_json::to_value(RevisionStamp::from(&attached))?
    {
        return Err("committed source recovery verified binding changed".into());
    }
    let prior_receipts = receipts(&before_service, before.id)?;
    let current_receipts = receipts(&current_service, current.id)?;
    let old_rows = prior_receipts
        .as_array()
        .ok_or("original native receipt array absent")?;
    let new_rows = current_receipts
        .as_array()
        .ok_or("current native receipt array absent")?;
    if new_rows.len() < old_rows.len()
        || old_rows
            .iter()
            .any(|old| new_rows.iter().filter(|row| *row == old).count() != 1)
    {
        return Err("original native receipts changed after resumed rendering".into());
    }
    for (id, expected_sha) in [
        (original_id, &intent.original_source_sha256),
        (edited_id, &intent.edited_source_sha256),
    ] {
        let asset = current
            .assets
            .iter()
            .find(|asset| asset.id == id)
            .ok_or("source Asset disappeared")?;
        if asset.media_type != SOURCE_BUNDLE_MIME_V2
            || asset.content_sha256.as_ref() != Some(expected_sha)
        {
            return Err("committed source Asset changed after resumed rendering".into());
        }
        let bytes = current_service
            .store()
            .lock()
            .read_blob(expected_sha, profile::MAX_BUNDLE_BYTES as u64)?;
        if hash(&bytes) != *expected_sha {
            return Err("committed source CAS bytes changed after resumed rendering".into());
        }
        profile::inspect_source_bundle(&bytes)?;
    }
    let inspected = inspect_saved_manim_editable_source(
        &current_service,
        current.id,
        &RevisionStamp::from(&current),
        edited_id,
    )?;
    if serde_json::to_value(&inspected)? != recovery["current_source_inspection"] {
        return Err("committed source inspection changed after resumed rendering".into());
    }
    let (bytes, package, native_link) =
        native_source(&current_service, &current, native_path, output_root)?;
    let original_native = object(native_path)?;
    let receipt_delta = super::reference::classify_added_receipts(
        &prior_receipts,
        &current_receipts,
        &native_link,
        &serde_json::to_value(package.plan.render_profile)?,
        text(&original_native, "/runtime_observation/connection_identity")?,
    )?;
    if hash(&bytes) != text(&recovery, "/current_export_sha256")?
        || serde_json::to_value(&package.plan)? != recovery["prepared_plan"]
        || current_service.project(current.id)? != current
        || before_service.project(before.id)? != before
        || serde_json::to_value(events(&current_service, current.id)?)?
            != serde_json::to_value(&current_events)?
        || serde_json::to_value(events(&before_service, before.id)?)?
            != serde_json::to_value(&before_events)?
        || receipts(&current_service, current.id)? != current_receipts
        || receipts(&before_service, before.id)? != prior_receipts
    {
        return Err(
            "resumed native source differs from its saved recovery or readback changed a Store"
                .into(),
        );
    }
    let report = json!({
        "schema":"motionwright.manim.editable.recovered-source-readback/1",
        "status":"SOURCE_READBACK_REQUIRES_NATIVE_CUSTODY", "project_id":current.id,
        "canonical_stamp":RevisionStamp::from(&current), "recovery_receipt_sha256":hash(&recovery_raw),
        "current_native":native_link, "source_sha256":hash(&bytes), "source_closure":package.closure,
        "additional_native_receipts":receipt_delta["additional_native_receipts"],
        "added_native_receipts":receipt_delta["added_native_receipts"],
        "additional_native_receipts_scope":receipt_delta["additional_native_receipts_scope"],
        "additional_production_receipts":receipt_delta["additional_production_receipts"],
        "added_production_receipts":receipt_delta["added_production_receipts"],
        "operational_native_receipts":receipt_delta["operational_native_receipts"],
        "native_render_groups":receipt_delta["native_render_groups"],
        "all_added_production_receipts_classified":true,
        "historical_production_receipts_unchanged":true,
        "typed_commit_lookup_performed":true,
        "committed_bindings":bindings, "fresh_sqlite_readback":true,
        "source_assets_original_bytes_verified":true, "saved_export_matches_native_carrier":true,
        "original_native_receipts_unchanged":true, "production_receipts_unchanged_during_readback":true,
        "project_events_unchanged":true, "original_edit_report_recreated":false,
        "project_changes_applied":0, "native_execution_performed":false,
        "provider_dispatch_performed":false, "native_bundle_reopen_verified":false,
        "human_acceptance":false, "automatic_retry":false,
    });
    new_report(output, &report)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        _temporary: tempfile::TempDir,
        before: PathBuf,
        current: PathBuf,
        original_bundle: PathBuf,
        edited_bundle: PathBuf,
        intent: PathBuf,
        output: PathBuf,
        id: Uuid,
    }

    fn fixture(commit_text: bool) -> Fixture {
        let temporary = tempfile::tempdir().unwrap();
        let before_root = temporary.path().join("before");
        fs::create_dir(&before_root).unwrap();
        let before = before_root.join("motionwright.sqlite3");
        seed(&before).unwrap();
        let service = StudioService::open(&before).unwrap();
        let project = service.projects(2).unwrap().remove(0);
        let package = prepare_saved_manim_editable_source(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            project.scenes[0].id,
            project.deliverables[0].id,
        )
        .unwrap();
        let original_bytes = profile::source_bundle(&package.plan, &fonts(&package)).unwrap();
        let mut edited_plan = package.plan.clone();
        let node = edited_plan
            .nodes
            .iter_mut()
            .find(|node| node.typography.is_some())
            .unwrap();
        let node_id = node.node_id;
        node.typography.as_mut().unwrap().text = EDITED_TEXT.into();
        let edited_bytes = profile::source_bundle(&edited_plan, &fonts(&package)).unwrap();
        drop(service);
        let current_root = temporary.path().join("current");
        fs::create_dir(&current_root).unwrap();
        let current = current_root.join("motionwright.sqlite3");
        fs::copy(&before, &current).unwrap();
        let original_bundle = temporary.path().join("original.mwedit.zip");
        let edited_bundle = temporary.path().join("edited.mwedit.zip");
        retain_seed_fixture(&original_bundle, &original_bytes).unwrap();
        retain_seed_fixture(&edited_bundle, &edited_bytes).unwrap();
        let intent = temporary.path().join("original-edit.intent.json");
        let request = "source-only-recovery-fixture";
        new_report(&intent, &json!({
            "schema":"motionwright.manim.editable.source-edit-intent/1",
            "project_id":project.id,"expected":RevisionStamp::from(&project),"request_id":request,
            "original_source_sha256":hash(&original_bytes),"edited_source_sha256":hash(&edited_bytes),
            "automatic_retry":false,"provider_dispatch_performed":false,
        })).unwrap();
        let service = StudioService::open(&current).unwrap();
        publish_recovered_blob(&service, EDITABLE_FONT, EDITABLE_FONT_SHA256).unwrap();
        let (attached, original_id) = attach(
            &service,
            &project,
            &original_bytes,
            temporary.path(),
            "Original native editable source",
            &format!("{request}:attach-original"),
        )
        .unwrap();
        let (attached, edited_id) = attach(
            &service,
            &attached,
            &edited_bytes,
            temporary.path(),
            "Edited native equation source",
            &format!("{request}:attach-edited"),
        )
        .unwrap();
        if commit_text {
            let preview = preview_saved_manim_editable_edit(
                &service,
                project.id,
                &RevisionStamp::from(&attached),
                original_id,
                edited_id,
                node_id,
                EditableProperty::Text,
            )
            .unwrap();
            service
                .apply(
                    project.id,
                    &preview.expected,
                    &format!("{request}:apply-text"),
                    &preview.change,
                )
                .unwrap();
        }
        drop(service);
        let output = temporary.path().join("source-recovery.json");
        Fixture {
            _temporary: temporary,
            before,
            current,
            original_bundle,
            edited_bundle,
            intent,
            output,
            id: project.id,
        }
    }

    fn recover(fixture: &Fixture) -> SourceResult<serde_json::Value> {
        recover_edit(
            &fixture.before,
            &fixture.current,
            &fixture.original_bundle,
            &fixture.edited_bundle,
            &fixture.intent,
            &fixture.output,
        )
    }

    #[test]
    fn committed_edit_recovery_exports_saved_source_without_another_commit() {
        let fixture = fixture(true);
        let service = StudioService::open(&fixture.current).unwrap();
        let project = service.project(fixture.id).unwrap();
        let original_events = serde_json::to_value(events(&service, fixture.id).unwrap()).unwrap();
        drop(service);
        let report = recover(&fixture).unwrap();
        assert_eq!(report["status"], "SOURCE_COMMITTED_RECOVERED");
        assert_eq!(report["committed_bindings"].as_array().unwrap().len(), 3);
        assert_eq!(report["source_mutation_performed"], false);
        assert_eq!(report["working_cas_blobs_published"], 4);
        assert_eq!(report["original_cas_identity_claimed"], false);
        assert_eq!(report["project_changes_applied"], 0);
        assert_eq!(report["native_execution_performed"], false);
        assert_eq!(report["fresh_source_export_file_readback"], true);
        assert_eq!(report["native_bundle_reopen_verified"], false);
        let reopened = StudioService::open(&fixture.current).unwrap();
        assert_eq!(reopened.project(fixture.id).unwrap(), project);
        assert_eq!(
            serde_json::to_value(events(&reopened, fixture.id).unwrap()).unwrap(),
            original_events
        );
        assert!(
            reopened
                .production_receipts(fixture.id, 256)
                .unwrap()
                .is_empty()
        );
        let exported = read(
            &fixture.output.with_extension("current.mwedit.zip"),
            profile::MAX_BUNDLE_BYTES,
        )
        .unwrap();
        assert_eq!(
            profile::inspect_source_bundle(&exported)
                .unwrap()
                .plan
                .binding
                .revision,
            project.revision
        );
    }

    #[test]
    fn committed_edit_recovery_missing_apply_stays_unresolved_without_a_retry() {
        let fixture = fixture(false);
        let service = StudioService::open(&fixture.current).unwrap();
        let before = service.project(fixture.id).unwrap();
        let original_events = serde_json::to_value(events(&service, fixture.id).unwrap()).unwrap();
        drop(service);
        assert!(recover(&fixture).is_err());
        assert!(!fixture.output.exists());
        let reopened = StudioService::open(&fixture.current).unwrap();
        assert_eq!(reopened.project(fixture.id).unwrap(), before);
        assert_eq!(
            serde_json::to_value(events(&reopened, fixture.id).unwrap()).unwrap(),
            original_events
        );
    }

    #[test]
    fn committed_edit_recovery_missing_receipt_never_becomes_known() {
        let fixture = fixture(true);
        let before_service = StudioService::open(&fixture.before).unwrap();
        let before = before_service.project(fixture.id).unwrap();
        let current = StudioService::open(&fixture.current).unwrap();
        let events = current
            .store()
            .lock()
            .event_records_since(fixture.id, before.revision, 4)
            .unwrap();
        let error = recorded_change(&current, &before, &events[0], "absent-original-request")
            .unwrap_err()
            .to_string();
        assert!(error.contains("no verified committed receipt; no retry"));
        assert!(!fixture.output.exists());
    }

    #[test]
    fn committed_edit_recovery_rejects_changed_original_bundle() {
        let fixture = fixture(true);
        let original = fs::read(&fixture.edited_bundle).unwrap();
        fs::write(
            &fixture.edited_bundle,
            [original.as_slice(), b"changed"].concat(),
        )
        .unwrap();
        let error = recover(&fixture).unwrap_err().to_string();
        assert!(error.contains("original bundle binding differs"));
        assert!(!fixture.output.exists());
    }

    #[test]
    fn committed_edit_recovery_never_overwrites_a_retained_report() {
        let fixture = fixture(true);
        let retained = b"original partial recovery custody";
        fs::write(&fixture.output, retained).unwrap();
        assert!(recover(&fixture).is_err());
        assert_eq!(fs::read(&fixture.output).unwrap(), retained);
        assert!(!fixture.output.with_extension("current.mwedit.zip").exists());
    }

    fn default_working_stores(fixture: &Fixture) -> (PathBuf, PathBuf, PathBuf) {
        let root = fixture._temporary.path().join("fresh-default-owner");
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join("before")).unwrap();
        fs::create_dir(root.join("current")).unwrap();
        let before = root.join("before/motionwright.sqlite3");
        let current = root.join("current/motionwright.sqlite3");
        fs::copy(&fixture.before, &before).unwrap();
        fs::copy(&fixture.current, &current).unwrap();
        (before, current, root.join("recovery.json"))
    }

    #[test]
    fn committed_source_resume_default_cas_reopens_in_fresh_store() {
        let fixture = fixture(true);
        let expected = StudioService::open(&fixture.current)
            .unwrap()
            .project(fixture.id)
            .unwrap();
        let (before, current, output) = default_working_stores(&fixture);
        // Both modes begin with identical physical SQLite bytes. Their fresh
        // CAS roots must not leak path or anchor identity into either report
        // serialization or the saved editable source export.
        let explicit = recover_edit(
            &fixture.before,
            &fixture.current,
            &fixture.original_bundle,
            &fixture.edited_bundle,
            &fixture.intent,
            &fixture.output,
        )
        .unwrap();
        let report = recover_edit_default_cas(
            &before,
            &current,
            &fixture.original_bundle,
            &fixture.edited_bundle,
            &fixture.intent,
            &output,
        )
        .unwrap();
        assert_eq!(report, explicit);
        assert_eq!(
            fs::read(&output).unwrap(),
            fs::read(&fixture.output).unwrap()
        );
        assert_eq!(
            fs::read(output.with_extension("current.mwedit.zip")).unwrap(),
            fs::read(fixture.output.with_extension("current.mwedit.zip")).unwrap()
        );
        assert_eq!(report["working_cas_blobs_published"], 4);
        assert_eq!(report["project_changes_applied"], 0);
        let reopened = StudioService::open(&current).unwrap();
        assert_eq!(reopened.project(fixture.id).unwrap(), expected);
        let edited_id = serde_json::from_value(report["edited_source_asset_id"].clone()).unwrap();
        let inspected = inspect_saved_manim_editable_source(
            &reopened,
            fixture.id,
            &RevisionStamp::from(&expected),
            edited_id,
        )
        .unwrap();
        assert_eq!(
            serde_json::to_value(inspected).unwrap(),
            report["current_source_inspection"]
        );
        assert_eq!(
            reopened
                .store()
                .lock()
                .read_blob(EDITABLE_FONT_SHA256, profile::MAX_BUNDLE_BYTES as u64)
                .unwrap(),
            EDITABLE_FONT
        );
        let reopened_before = StudioService::open(&before).unwrap();
        assert_eq!(
            reopened_before
                .store()
                .lock()
                .read_blob(EDITABLE_FONT_SHA256, profile::MAX_BUNDLE_BYTES as u64)
                .unwrap(),
            EDITABLE_FONT
        );
        assert_eq!(
            events(&reopened, fixture.id).unwrap().len(),
            events(&reopened_before, fixture.id).unwrap().len() + 3
        );
    }

    #[test]
    fn committed_source_resume_existing_or_shared_root_is_not_reinitialized() {
        let fixture = fixture(true);
        let original = fs::read(&fixture.current).unwrap();
        assert!(
            recover_edit_default_cas(
                &fixture.before,
                &fixture.current,
                &fixture.original_bundle,
                &fixture.edited_bundle,
                &fixture.intent,
                &fixture.output
            )
            .is_err()
        );
        assert_eq!(fs::read(&fixture.current).unwrap(), original);
        assert!(!fixture.output.exists());
        let shared = fixture._temporary.path().join("shared-default-root");
        fs::create_dir(&shared).unwrap();
        let before = shared.join("before.sqlite3");
        let current = shared.join("current.sqlite3");
        fs::copy(&fixture.before, &before).unwrap();
        fs::copy(&fixture.current, &current).unwrap();
        assert!(
            recover_edit_default_cas(
                &before,
                &current,
                &fixture.original_bundle,
                &fixture.edited_bundle,
                &fixture.intent,
                &fixture.output
            )
            .is_err()
        );
        assert!(!shared.join("blobs").exists());
        assert_eq!(fs::read(&current).unwrap(), original);
        assert!(!fixture.output.exists());
    }

    #[test]
    fn committed_source_resume_readback_rejects_changed_committed_receipt() {
        let fixture = fixture(true);
        let (before, current, output) = default_working_stores(&fixture);
        let mut changed = recover_edit_default_cas(
            &before,
            &current,
            &fixture.original_bundle,
            &fixture.edited_bundle,
            &fixture.intent,
            &output,
        )
        .unwrap();
        changed["committed_bindings"][0] = serde_json::Value::Null;
        let changed_path = output.with_extension("changed.json");
        new_report(&changed_path, &changed).unwrap();
        let report = output.with_extension("readback.json");
        let before_events = serde_json::to_value(
            events(&StudioService::open(&current).unwrap(), fixture.id).unwrap(),
        )
        .unwrap();
        let error = readback_recovered(
            &before,
            &current,
            &changed_path,
            &output.with_extension("native-not-created.json"),
            output.parent().unwrap(),
            &report,
        )
        .unwrap_err()
        .to_string();
        assert!(error.contains("verified binding changed"));
        assert!(!report.exists());
        assert_eq!(
            serde_json::to_value(
                events(&StudioService::open(&current).unwrap(), fixture.id).unwrap()
            )
            .unwrap(),
            before_events
        );
    }
}
