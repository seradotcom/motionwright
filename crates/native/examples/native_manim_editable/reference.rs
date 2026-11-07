//! Read back a new reference render from the retained pre-edit Store.
//! This does not seed a Project, replay an edit, or grant Native authority.
use super::*;

struct ReferenceState {
    baseline: StudioService,
    current: StudioService,
    project: Project,
    events: serde_json::Value,
    prior_receipts: serde_json::Value,
    current_receipts: serde_json::Value,
    source: Vec<u8>,
    baseline_sha256: String,
}

fn events(service: &StudioService, id: Uuid) -> SourceResult<serde_json::Value> {
    let rows = service.store().lock().event_records_since(id, 0, 256)?;
    if rows.len() >= 256 {
        return Err("retained reference event history exceeds its read budget".into());
    }
    Ok(serde_json::to_value(rows)?)
}

fn receipts(service: &StudioService, id: Uuid) -> SourceResult<serde_json::Value> {
    let rows = service.production_receipts(id, 256)?;
    if rows.len() >= 256 {
        return Err("retained reference receipt history exceeds its read budget".into());
    }
    Ok(serde_json::to_value(rows)?)
}

fn source_state(
    baseline_database: &Path,
    current_database: &Path,
    original_bundle: &Path,
) -> SourceResult<ReferenceState> {
    if fs::canonicalize(baseline_database)? == fs::canonicalize(current_database)?
        || fs::canonicalize(baseline_database.parent().ok_or("baseline parent absent")?)?
            == fs::canonicalize(current_database.parent().ok_or("reference parent absent")?)?
    {
        return Err("retained reference requires a separate unchanged baseline Store".into());
    }
    let baseline_bytes = read(baseline_database, 16 * 1024 * 1024)?;
    let source = read(original_bundle, profile::MAX_BUNDLE_BYTES)?;
    let package = profile::inspect_source_bundle(&source)?;
    let baseline = StudioService::open(baseline_database)?;
    let current = StudioService::open(current_database)?;
    let before = baseline.project(package.plan.binding.project_id)?;
    let project = current.project(before.id)?;
    let original_events = events(&baseline, before.id)?;
    if before != project
        || events(&current, before.id)? != original_events
        || package.plan.binding.generation != before.generation
        || package.plan.binding.revision != before.revision
    {
        return Err("retained reference changed the saved Project or original events".into());
    }
    let prepared = prepare_saved_manim_editable_source(
        &current,
        project.id,
        &RevisionStamp::from(&project),
        package.plan.binding.scene_id,
        package.plan.binding.deliverable_id,
    )?;
    if prepared.entries != package.entries
        || profile::source_bundle(&prepared.plan, &fonts(&prepared))? != source
    {
        return Err("retained reference source differs from the original saved bundle".into());
    }
    let prior_receipts = receipts(&baseline, before.id)?;
    let current_receipts = receipts(&current, before.id)?;
    let old = prior_receipts
        .as_array()
        .ok_or("baseline receipts absent")?;
    let new = current_receipts
        .as_array()
        .ok_or("reference receipts absent")?;
    if new.len() < old.len()
        || old
            .iter()
            .any(|value| new.iter().filter(|row| *row == value).count() != 1)
    {
        return Err("retained reference changed a preceding Native receipt".into());
    }
    Ok(ReferenceState {
        baseline,
        current,
        project,
        events: original_events,
        prior_receipts,
        current_receipts,
        source,
        baseline_sha256: hash(&baseline_bytes),
    })
}

pub(crate) fn readback_reference(
    baseline_database: &Path,
    reference_database: &Path,
    original_bundle: &Path,
    native_path: &Path,
    output_root: &Path,
    output: &Path,
) -> SourceResult<serde_json::Value> {
    if !output.is_absolute() || !output.parent().is_some_and(Path::is_dir) || output.try_exists()? {
        return Err("retained reference readback requires a new owned report".into());
    }
    let observed = source_state(baseline_database, reference_database, original_bundle)?;
    let old = observed
        .prior_receipts
        .as_array()
        .ok_or("baseline receipts absent")?;
    let new = observed
        .current_receipts
        .as_array()
        .ok_or("reference receipts absent")?;
    let added: Vec<_> = new
        .iter()
        .filter(|row| !old.contains(row))
        .cloned()
        .collect();
    if !matches!(added.len(), 2 | 4) || new.len() != old.len() + added.len() {
        return Err("retained reference needs exactly one or two new Native receipt pairs".into());
    }
    let (source, package, native_link) = native_source(
        &observed.current,
        &observed.project,
        native_path,
        output_root,
    )?;
    if source != observed.source
        || observed.baseline.project(observed.project.id)? != observed.project
        || observed.current.project(observed.project.id)? != observed.project
        || events(&observed.baseline, observed.project.id)? != observed.events
        || events(&observed.current, observed.project.id)? != observed.events
        || receipts(&observed.baseline, observed.project.id)? != observed.prior_receipts
        || receipts(&observed.current, observed.project.id)? != observed.current_receipts
    {
        return Err("retained reference source or Store changed during physical readback".into());
    }
    let report = json!({
        "schema":"motionwright.manim.editable.retained-reference-readback/1",
        "status":"REFERENCE_READBACK_REQUIRES_NATIVE_CUSTODY",
        "canonical_stamp":RevisionStamp::from(&observed.project),
        "baseline_database_sha256":observed.baseline_sha256,
        "source_sha256":hash(&source),"source_bytes":source.len(),
        "source_closure":package.closure,"current_native":native_link,
        "additional_native_receipts":added.len(),"added_native_receipts":added,
        "fresh_sqlite_readback":true,"saved_export_matches_native_carrier":true,
        "original_project_and_events_unchanged":true,"original_native_receipts_unchanged":true,
        "production_receipts_unchanged_during_readback":true,
        "source_mutation_performed":false,"project_changes_applied":0,
        "native_execution_performed":false,"provider_dispatch_performed":false,
        "native_bundle_reopen_verified":false,"original_edit_report_recreated":false,
        "equivalence_to_old_runtime_claimed":false,"human_acceptance":false,"automatic_retry":false,
    });
    new_report(output, &report)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, PathBuf, PathBuf, PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let baseline = root.path().join("baseline");
        let current = root.path().join("reference");
        fs::create_dir(&baseline).unwrap();
        fs::create_dir(&current).unwrap();
        let baseline = baseline.join("motionwright.sqlite3");
        let current = current.join("motionwright.sqlite3");
        seed(&baseline).unwrap();
        let service = StudioService::open(&baseline).unwrap();
        let project = service.projects(2).unwrap().remove(0);
        let package = prepare_saved_manim_editable_source(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            project.scenes[0].id,
            project.deliverables[0].id,
        )
        .unwrap();
        let source = profile::source_bundle(&package.plan, &fonts(&package)).unwrap();
        drop(service);
        fs::copy(&baseline, &current).unwrap();
        let service = StudioService::open(&current).unwrap();
        let handle = service.store();
        let store = handle.lock();
        let captured = store
            .stage_file_import_bytes_with_limit(EDITABLE_FONT, profile::MAX_BUNDLE_BYTES as u64)
            .unwrap();
        store.publish_file_import(&captured).unwrap();
        drop(store);
        drop(handle);
        drop(service);
        let bundle = root.path().join("original.mwedit.zip");
        retain_seed_fixture(&bundle, &source).unwrap();
        (root, baseline, current, bundle)
    }

    #[test]
    fn retained_reference_source_reopens_without_reapplying_events() {
        let (_root, baseline, current, bundle) = fixture();
        let observed = source_state(&baseline, &current, &bundle).unwrap();
        assert_eq!(observed.prior_receipts, observed.current_receipts);
        assert_eq!(
            observed.source,
            read(&bundle, profile::MAX_BUNDLE_BYTES).unwrap()
        );
        assert_eq!(
            events(&observed.current, observed.project.id).unwrap(),
            observed.events
        );
        drop(observed);
        let reopened = source_state(&baseline, &current, &bundle).unwrap();
        assert_eq!(reopened.prior_receipts, reopened.current_receipts);
    }

    #[test]
    fn retained_reference_readback_rejects_changed_source_shared_store_and_missing_native() {
        let (root, baseline, current, bundle) = fixture();
        assert!(source_state(&baseline, &baseline, &bundle).is_err());
        let original = read(&bundle, profile::MAX_BUNDLE_BYTES).unwrap();
        let package = profile::inspect_source_bundle(&original).unwrap();
        let mut changed = package.plan.clone();
        changed
            .nodes
            .iter_mut()
            .find(|node| node.typography.is_some())
            .unwrap()
            .typography
            .as_mut()
            .unwrap()
            .text = EDITED_TEXT.into();
        let changed_path = root.path().join("changed.mwedit.zip");
        retain_seed_fixture(
            &changed_path,
            &profile::source_bundle(&changed, &fonts(&package)).unwrap(),
        )
        .unwrap();
        assert!(source_state(&baseline, &current, &changed_path).is_err());
        let report = root.path().join("reference-readback.json");
        let error = readback_reference(
            &baseline,
            &current,
            &bundle,
            &root.path().join("missing-native.json"),
            root.path(),
            &report,
        )
        .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("exactly one or two new Native receipt pairs")
        );
        assert!(!report.exists());
        let observed = source_state(&baseline, &current, &bundle).unwrap();
        assert_eq!(observed.prior_receipts, observed.current_receipts);
    }
}
