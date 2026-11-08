//! Read back a new reference render from the retained pre-edit Store.
//! This does not seed a Project, replay an edit, or grant Native authority.
use super::*;

/// Separate the two acknowledged render endpoints from their complete durable
/// dispatch/status history. Every added record remains in the report.
fn receipt_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn operation_pair<'a>(
    rows: &[&'a serde_json::Value],
) -> SourceResult<(&'a serde_json::Value, &'a serde_json::Value)> {
    let dispatch: Vec<_> = rows
        .iter()
        .copied()
        .filter(|row| row["stage"] == "dispatching")
        .collect();
    let completed: Vec<_> = rows
        .iter()
        .copied()
        .filter(|row| row["stage"] == "completed")
        .collect();
    if rows.len() != 2
        || dispatch.len() != 1
        || completed.len() != 1
        || dispatch[0]["request_sha256"] != completed[0]["request_sha256"]
        || dispatch[0]["command"] != completed[0]["command"]
    {
        return Err(
            "Native operation needs one dispatch and one unchanged completed receipt".into(),
        );
    }
    Ok((dispatch[0], completed[0]))
}

pub(super) fn classify_added_receipts(
    prior: &serde_json::Value,
    current: &serde_json::Value,
    native_link: &serde_json::Value,
    render_profile: &serde_json::Value,
    current_connection: &str,
) -> SourceResult<serde_json::Value> {
    use std::collections::BTreeSet;
    let old = prior.as_array().ok_or("original receipt array absent")?;
    let new = current.as_array().ok_or("current receipt array absent")?;
    // Store::production_receipts returns descending committed order. Added
    // records precede the exact historical suffix; UUIDs and wall time are not
    // ordering keys and must never be used to rearrange the original rows.
    if old.len() >= 256
        || new.len() >= 256
        || !new.ends_with(old)
        || old
            .iter()
            .any(|row| new.iter().filter(|value| *value == row).count() != 1)
    {
        return Err("Native receipt history changed or exceeded its original read budget".into());
    }
    for rows in [old, new] {
        let mut ids = BTreeSet::new();
        for row in rows {
            let id = text(row, "/id")?;
            if Uuid::parse_str(id)?.to_string() != id || !ids.insert(id) {
                return Err("Native receipt identity is repeated or noncanonical".into());
            }
        }
    }
    let added: Vec<_> = new
        .iter()
        .filter(|row| !old.contains(row))
        .cloned()
        .collect();
    if added.is_empty() || new.len() != old.len() + added.len() {
        return Err(
            "retained source needs one or two complete new Native render lifecycles".into(),
        );
    }
    let current_request = text(native_link, "/request_id")?;
    let canonical = &native_link["original_start_receipt"];
    let source = &native_link["source"];
    let plan_sha = text(source, "/closure/plan_sha256")?;
    if !receipt_hex(current_connection, 64)
        || !receipt_hex(plan_sha, 64)
        || !receipt_hex(text(source, "/sha256")?, 64)
        || source["bytes"].as_u64().is_none_or(|count| count == 0)
        || canonical["revision"].as_u64().is_none()
    {
        return Err(
            "Native receipt classification needs the admitted current source binding".into(),
        );
    }
    let mut groups: BTreeMap<String, BTreeMap<String, Vec<&serde_json::Value>>> = BTreeMap::new();
    for row in &added {
        if row["project_id"] != canonical["project_id"]
            || row["generation"] != canonical["generation"]
            || row["revision"] != canonical["revision"]
            || !receipt_hex(text(row, "/request_sha256")?, 64)
            || !matches!(text(row, "/stage")?, "dispatching" | "completed")
        {
            return Err("added Native receipt has another stamp, fingerprint or stage".into());
        }
        let request = text(row, "/request_id")?;
        let (base, operation) = match text(row, "/command")? {
            "driver.manim-community.editable.render.start" => (
                request
                    .strip_suffix(":start")
                    .ok_or("Native start request suffix changed")?,
                "start".to_string(),
            ),
            "driver.manim-community.render.result" => (
                request
                    .strip_suffix(":result")
                    .ok_or("Native result request suffix changed")?,
                "result".to_string(),
            ),
            "driver.manim-community.render.status" => {
                let (base, index) = request
                    .rsplit_once(":status:")
                    .ok_or("Native status request suffix changed")?;
                let number: usize = index.parse()?;
                if number >= 128 || number.to_string() != index {
                    return Err("Native status request index is not canonical or bounded".into());
                }
                (base, format!("status:{index}"))
            }
            _ => return Err("unexpected production command among new Native receipts".into()),
        };
        if base.is_empty()
            || base.len() > 64
            || base.contains(':')
            || base.chars().any(char::is_control)
        {
            return Err("Native render request owner is invalid".into());
        }
        groups
            .entry(base.to_string())
            .or_default()
            .entry(operation)
            .or_default()
            .push(row);
    }
    if !matches!(groups.len(), 1 | 2) || !groups.contains_key(current_request) {
        return Err(
            "retained source needs one or two complete new Native render lifecycles".into(),
        );
    }
    let mut endpoint_receipts: Vec<serde_json::Value> = Vec::new();
    let mut render_groups = Vec::new();
    let mut jobs = BTreeSet::new();
    let mut connections = BTreeSet::new();
    for (request, operations) in &groups {
        let (start_dispatch, start) = operation_pair(
            operations
                .get("start")
                .ok_or("Native lifecycle start absent")?,
        )?;
        let (_, terminal) = operation_pair(
            operations
                .get("result")
                .ok_or("Native lifecycle result absent")?,
        )?;
        let connection = text(start_dispatch, "/payload/connection")?;
        let job = text(terminal, "/payload/result/data/job_ref")?;
        let job_id = job
            .strip_prefix("manim-")
            .ok_or("Native job namespace changed")?;
        if !receipt_hex(connection, 64)
            || !receipt_hex(job_id, 32)
            || !jobs.insert(job)
            || !connections.insert(connection)
            || (request == current_request
                && (connection != current_connection
                    || start != &native_link["original_start_receipt"]
                    || terminal != &native_link["original_result_receipt"]))
        {
            return Err(
                "Native render owner/job/connection or original endpoint receipt differs".into(),
            );
        }
        let mut status_indices = Vec::new();
        let mut record_ids = Vec::new();
        for (operation, rows) in operations {
            let (dispatch, completed) = operation_pair(rows)?;
            let is_start = operation == "start";
            let expected_job = if is_start {
                serde_json::Value::Null
            } else {
                json!(job)
            };
            let result = &completed["payload"]["result"];
            let data = &result["data"];
            let execution = &result["execution"];
            let state = text(data, "/state")?;
            if dispatch["payload"]["connection"] != connection
                || dispatch["payload"]["job_ref"] != expected_job
                || dispatch["payload"]["mutation"] != is_start
                || completed["payload"]["job_ref"] != expected_job
                || data["job_ref"] != job
                || data["plan_sha256"] != plan_sha
                || data["source_file"] != format!("mw_{job_id}.py")
                || data["profile"] != *render_profile
                || !data["diagnostic"].is_null()
                || !data["original_error"].is_null()
                || !data["source_custody_error"].is_null()
                || result["provenance_checked"] != true
                || execution["dry_run"] != false
                || execution["backend"] != "driver:manim-community"
                || execution["policy_decision"] != "allow"
                || execution["fallbacks_attempted"] != json!([])
                || execution["provenance"]["execution_provider"] != "driver:manim-community"
                || execution["provenance"]["provider"] != "driver:manim-community"
                || execution["provenance"]["source"] != "driver"
                || !receipt_hex(text(&execution["provenance"], "/descriptor_sha256")?, 64)
                || execution["provenance"]["provider_generation"]
                    .as_u64()
                    .is_none_or(|value| value == 0)
                || execution["provenance"]["provider_generation"]
                    != start["payload"]["result"]["execution"]["provenance"]["provider_generation"]
                || execution["provenance"]["provider_version"]
                    != start["payload"]["result"]["execution"]["provenance"]["provider_version"]
                || execution["provenance"]["capability_version"] != "1"
                || !matches!(state, "rendering" | "succeeded")
                || (operation == "result" && state != "succeeded")
            {
                return Err("Native operational receipt differs from its owned job, source or successful dispatch".into());
            }
            if let Some(index) = operation.strip_prefix("status:") {
                status_indices.push(index.parse::<usize>()?);
            }
            record_ids.extend([dispatch["id"].clone(), completed["id"].clone()]);
        }
        status_indices.sort_unstable();
        if status_indices != (0..status_indices.len()).collect::<Vec<_>>() {
            return Err("Native status operation history has a gap or duplicate".into());
        }
        for (index, _) in status_indices.iter().enumerate() {
            let (_, completed) = operation_pair(&operations[&format!("status:{index}")])?;
            let expected_state = if index + 1 == status_indices.len() {
                "succeeded"
            } else {
                "rendering"
            };
            if completed["payload"]["result"]["data"]["state"] != expected_state {
                return Err("Native status operation has an unexpected terminal state".into());
            }
        }
        let descriptor = &terminal["payload"]["result"]["data"]["artifact"]["editable_source"];
        if descriptor["sha256"] != source["sha256"]
            || descriptor["bytes"] != source["bytes"]
            || descriptor["closure"] != source["closure"]
            || descriptor["media_type"] != source["media_type"]
            || descriptor["relative_path"] != format!("mw_{job_id}.mwedit.zip")
            || terminal["payload"]["result"]["data"]["artifact"]["plan_sha256"] != plan_sha
        {
            return Err("Native render receipt is not the same saved source closure".into());
        }
        endpoint_receipts.extend([start.clone(), terminal.clone()]);
        render_groups.push(
            json!({"request_id":request,"job_ref":job,"connection_identity":connection,
            "completed_start_receipt_id":start["id"],"completed_result_receipt_id":terminal["id"],
            "status_operations":status_indices.len(),"production_receipt_ids":record_ids}),
        );
    }
    let operational: Vec<_> = added
        .iter()
        .filter(|row| !endpoint_receipts.contains(row))
        .cloned()
        .collect();
    Ok(json!({
        "additional_native_receipts":endpoint_receipts.len(),"added_native_receipts":endpoint_receipts,
        "additional_native_receipts_scope":"completed_start_and_result_only",
        "additional_production_receipts":added.len(),"added_production_receipts":added,
        "operational_native_receipts":operational,"native_render_groups":render_groups,
        "all_added_production_receipts_classified":true,"historical_production_receipts_unchanged":true,
    }))
}

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

/// Capture the licensed original font into a new default CAS for a retained
/// reference Store. The saved Project, events and every receipt are unchanged.
pub(crate) fn rehydrate_reference(
    baseline_database: &Path,
    reference_database: &Path,
    original_bundle: &Path,
    output: &Path,
) -> SourceResult<serde_json::Value> {
    let parent = reference_database
        .parent()
        .ok_or("retained reference parent absent")?;
    let baseline_parent = baseline_database
        .parent()
        .ok_or("reference baseline parent absent")?;
    let cas = parent.join("blobs");
    if !output.is_absolute()
        || !output.parent().is_some_and(Path::is_dir)
        || output.try_exists()?
        || !reference_database.is_absolute()
        || !baseline_database.is_absolute()
        || fs::canonicalize(reference_database)? != reference_database
        || fs::canonicalize(baseline_database)? != baseline_database
        || fs::canonicalize(parent)? == fs::canonicalize(baseline_parent)?
        || cas.try_exists()?
        || fs::symlink_metadata(&cas).is_ok()
        || fs::read_dir(parent)?.any(|entry| {
            entry
                .map(|entry| {
                    entry
                        .file_name()
                        .to_string_lossy()
                        .starts_with(".motionwright-cas-")
                })
                .unwrap_or(true)
        })
    {
        return Err(
            "retained reference rehydration requires separate Stores and a new canonical CAS"
                .into(),
        );
    }
    let baseline_bytes = read(baseline_database, 16 * 1024 * 1024)?;
    let reference_bytes = read(reference_database, 16 * 1024 * 1024)?;
    let source = read(original_bundle, profile::MAX_BUNDLE_BYTES)?;
    let package = profile::inspect_source_bundle(&source)?;
    let original_fonts = fonts(&package);
    let font_name = format!("fonts/{EDITABLE_FONT_SHA256}.ttf");
    if original_fonts.len() != 1
        || original_fonts.get(&font_name).map(Vec::as_slice) != Some(EDITABLE_FONT)
    {
        return Err("retained reference licensed font closure changed".into());
    }
    let baseline = StudioService::open(baseline_database)?;
    let reference = StudioService::from_store(
        motionwright_storage::Store::open_with_managed_blob_root(reference_database, &cas)?,
    );
    let project = baseline.project(package.plan.binding.project_id)?;
    let original_events = events(&baseline, project.id)?;
    let baseline_receipts = receipts(&baseline, project.id)?;
    let reference_receipts = receipts(&reference, project.id)?;
    let baseline_rows = baseline_receipts
        .as_array()
        .ok_or("reference baseline receipts absent")?;
    let reference_rows = reference_receipts
        .as_array()
        .ok_or("retained reference receipts absent")?;
    if reference.project(project.id)? != project
        || events(&reference, project.id)? != original_events
        || package.plan.binding.generation != project.generation
        || package.plan.binding.revision != project.revision
        || !reference_rows.ends_with(baseline_rows)
        || baseline_rows
            .iter()
            .any(|row| reference_rows.iter().filter(|value| *value == row).count() != 1)
    {
        return Err(
            "retained reference Project, events or historical receipts changed before rehydration"
                .into(),
        );
    }
    {
        let handle = reference.store();
        let store = handle.lock();
        let staged = store
            .stage_file_import_bytes_with_limit(EDITABLE_FONT, profile::MAX_BUNDLE_BYTES as u64)?;
        if staged.descriptor().sha256 != EDITABLE_FONT_SHA256
            || staged.descriptor().size_bytes != EDITABLE_FONT.len() as u64
        {
            return Err(
                "retained reference font capture differs from original source bytes".into(),
            );
        }
        let published = store.publish_file_import(&staged)?;
        if published.sha256 != EDITABLE_FONT_SHA256
            || published.size_bytes != EDITABLE_FONT.len() as u64
            || store
                .read_blob(EDITABLE_FONT_SHA256, profile::MAX_BUNDLE_BYTES as u64)?
                .as_slice()
                != EDITABLE_FONT
        {
            return Err("retained reference new CAS publication differs after readback".into());
        }
    }
    let prepared = prepare_saved_manim_editable_source(
        &reference,
        project.id,
        &RevisionStamp::from(&project),
        package.plan.binding.scene_id,
        package.plan.binding.deliverable_id,
    )?;
    if prepared.entries != package.entries
        || profile::source_bundle(&prepared.plan, &fonts(&prepared))? != source
        || baseline.project(project.id)? != project
        || reference.project(project.id)? != project
        || events(&baseline, project.id)? != original_events
        || events(&reference, project.id)? != original_events
        || receipts(&baseline, project.id)? != baseline_receipts
        || receipts(&reference, project.id)? != reference_receipts
        || read(original_bundle, profile::MAX_BUNDLE_BYTES)? != source
    {
        return Err(
            "retained reference rehydration changed saved state or original receipt history".into(),
        );
    }
    let report = json!({
        "schema":"motionwright.manim.editable.retained-reference-rehydration/1",
        "status":"REFERENCE_CAS_REHYDRATED_SOURCE_ONLY",
        "canonical_stamp":RevisionStamp::from(&project),
        "input_sha256":{"baseline_database":hash(&baseline_bytes),"reference_database":hash(&reference_bytes),
            "original_bundle":hash(&source)},
        "source_sha256":hash(&source),"source_bytes":source.len(),"source_closure":package.closure,
        "baseline_production_receipt_count":baseline_receipts.as_array().map(Vec::len),
        "retained_production_receipt_count":reference_receipts.as_array().map(Vec::len),
        "baseline_production_receipts_sha256":hash(&serde_json::to_vec(&baseline_receipts)?),
        "retained_production_receipts_sha256":hash(&serde_json::to_vec(&reference_receipts)?),
        "project_events_and_all_production_receipts_unchanged":true,
        "source_assets_original_bytes_verified":true,"saved_source_bundle_unchanged":true,
        "working_cas_provenance":"CAS_REHYDRATED_NEW_IDENTITY","new_cas_capture_count":1,
        "original_cas_identity_claimed":false,"project_changes_applied":0,"source_edit_performed":false,
        "native_execution_performed":false,"provider_dispatch_performed":false,
        "original_edit_report_recreated":false,"native_bundle_reopen_verified":false,
        "human_acceptance":false,"automatic_retry":false,
    });
    new_report(output, &report)?;
    Ok(report)
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
    if new.len() == old.len() {
        return Err(
            "retained source needs one or two complete new Native render lifecycles".into(),
        );
    }
    let (source, package, native_link) = native_source(
        &observed.current,
        &observed.project,
        native_path,
        output_root,
    )?;
    let original_native = object(native_path)?;
    let delta = classify_added_receipts(
        &observed.prior_receipts,
        &observed.current_receipts,
        &native_link,
        &serde_json::to_value(&package.plan.render_profile)?,
        text(&original_native, "/runtime_observation/connection_identity")?,
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
        "additional_native_receipts":delta["additional_native_receipts"],
        "added_native_receipts":delta["added_native_receipts"],
        "additional_native_receipts_scope":delta["additional_native_receipts_scope"],
        "additional_production_receipts":delta["additional_production_receipts"],
        "added_production_receipts":delta["added_production_receipts"],
        "operational_native_receipts":delta["operational_native_receipts"],
        "native_render_groups":delta["native_render_groups"],
        "all_added_production_receipts_classified":true,
        "historical_production_receipts_unchanged":true,
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

    fn receipt_fixture() -> serde_json::Value {
        let value: serde_json::Value = serde_json::from_str(include_str!(
            "../../tests/fixtures/manim-n1-receipt-lifecycle-42369864661.json"
        ))
        .unwrap();
        assert_eq!(
            value["scope"],
            "STRUCTURAL_REGRESSION_FIXTURE_NOT_NATIVE_OR_G2_EVIDENCE"
        );
        assert_eq!(
            value["original_private_observation_sha256"],
            "8298e90e38f2aa1f38a16e32ff266d10a7f6dc00f5739233c470ecaf98961bfb"
        );
        assert_eq!(
            value["receipt_order"]["query"],
            "production_receipt_order.sequence DESC"
        );
        assert_eq!(
            value["receipt_order"]["predicate_observation_sha256"],
            "8256b508b12a59e4cec5afb610538e0a4f6a354fa3ffe1f6e43e9c1e0edcdf97"
        );
        value
    }

    fn classify_fixture(value: &serde_json::Value) -> SourceResult<serde_json::Value> {
        classify_added_receipts(
            &value["prior"],
            &value["current"],
            &value["native_link"],
            &value["profile"],
            value["current_connection"].as_str().unwrap(),
        )
    }

    fn original_operation(value: &serde_json::Value, suffix: &str, stage: &str) -> usize {
        let request = format!(
            "{}:{suffix}",
            value["native_link"]["request_id"].as_str().unwrap()
        );
        value["current"]
            .as_array()
            .unwrap()
            .iter()
            .position(|row| row["request_id"] == request && row["stage"] == stage)
            .unwrap()
    }

    fn original_receipt_projection_passes_without_discarding_operational_rows() {
        let value = receipt_fixture();
        let original = serde_json::to_vec(&value).unwrap();
        let observed = classify_fixture(&value).unwrap();
        assert_eq!(value["prior"].as_array().unwrap().len(), 20);
        assert_eq!(value["current"].as_array().unwrap().len(), 30);
        let prior = value["prior"].as_array().unwrap();
        let current = value["current"].as_array().unwrap();
        assert!(current.ends_with(prior));
        assert!(!current.starts_with(prior));
        assert_eq!(observed["additional_native_receipts"], 2);
        assert_eq!(observed["additional_production_receipts"], 10);
        assert_eq!(
            observed["operational_native_receipts"]
                .as_array()
                .unwrap()
                .len(),
            8
        );
        assert_eq!(
            observed["native_render_groups"].as_array().unwrap().len(),
            1
        );
        assert_eq!(observed["native_render_groups"][0]["status_operations"], 3);
        assert_eq!(
            observed["added_native_receipts"],
            json!([
                value["native_link"]["original_start_receipt"],
                value["native_link"]["original_result_receipt"]
            ])
        );
        let expected: Vec<_> = value["current"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|row| !value["prior"].as_array().unwrap().contains(row))
            .cloned()
            .collect();
        assert_eq!(observed["added_production_receipts"], json!(expected));
        assert_eq!(serde_json::to_vec(&value).unwrap(), original);

        // A structural second job exercises the variable operational history.
        // This derivative is a unit input, never a Native observation.
        let mut two = value.clone();
        let old_request = value["native_link"]["request_id"].as_str().unwrap();
        let new_request = old_request.replace("-n1", "-n2");
        let old_job = value["native_link"]["original_result_receipt"]["payload"]["result"]["data"]
            ["job_ref"]
            .as_str()
            .unwrap();
        let old_job_id = old_job.strip_prefix("manim-").unwrap();
        let new_job_id = "11111111111111111111111111111111";
        let new_connection = "2".repeat(64);
        let encoded = serde_json::to_string(&expected)
            .unwrap()
            .replace(old_request, &new_request)
            .replace(old_job_id, new_job_id)
            .replace(
                value["current_connection"].as_str().unwrap(),
                &new_connection,
            );
        let mut additional: Vec<serde_json::Value> = serde_json::from_str(&encoded).unwrap();
        for (index, row) in additional.iter_mut().enumerate() {
            row["id"] = json!(format!("00000000-0000-4000-8000-{:012}", index + 1));
        }
        two["native_link"]["request_id"] = json!(new_request);
        two["native_link"]["source"]["relative_path"] =
            json!(format!("mw_{new_job_id}.mwedit.zip"));
        for (field, suffix) in [
            ("original_start_receipt", ":start"),
            ("original_result_receipt", ":result"),
        ] {
            two["native_link"][field] = additional
                .iter()
                .find(|row| {
                    row["stage"] == "completed"
                        && row["request_id"].as_str().unwrap().ends_with(suffix)
                })
                .unwrap()
                .clone();
        }
        two["current_connection"] = json!(new_connection);
        additional.extend(two["current"].as_array().unwrap().iter().cloned());
        two["current"] = json!(additional);
        let second = classify_fixture(&two).unwrap();
        assert_eq!(second["additional_native_receipts"], 4);
        assert_eq!(second["additional_production_receipts"], 20);
        assert_eq!(
            second["operational_native_receipts"]
                .as_array()
                .unwrap()
                .len(),
            16
        );
        assert_eq!(second["native_render_groups"].as_array().unwrap().len(), 2);
    }

    fn original_receipt_projection_rejects_unbound_or_ambiguous_history() {
        fn reject(change: impl FnOnce(&mut serde_json::Value)) {
            let mut value = receipt_fixture();
            change(&mut value);
            assert!(classify_fixture(&value).is_err());
        }
        reject(|value| {
            let row = value["current"][original_operation(value, "result", "completed")].clone();
            value["current"].as_array_mut().unwrap().insert(0, row);
        });
        reject(|value| {
            let mut row =
                value["current"][original_operation(value, "result", "completed")].clone();
            row["id"] = json!("00000000-0000-4000-8000-999999999999");
            value["current"].as_array_mut().unwrap().insert(0, row);
        });
        reject(|value| {
            let index = original_operation(value, "status:1", "dispatching");
            value["current"].as_array_mut().unwrap().remove(index);
        });
        for (suffix, stage, pointer, replacement) in [
            ("status:0", "completed", "/revision", json!(9)),
            ("status:0", "completed", "/stage", json!("failed")),
            (
                "status:1",
                "completed",
                "/payload/result/data/job_ref",
                json!("manim-11111111111111111111111111111111"),
            ),
            (
                "status:1",
                "completed",
                "/payload/result/data/state",
                json!("failed"),
            ),
            (
                "status:1",
                "completed",
                "/payload/result/data/plan_sha256",
                json!("0".repeat(64)),
            ),
            (
                "status:1",
                "completed",
                "/payload/result/execution/provenance/provider_generation",
                json!(999),
            ),
            (
                "status:1",
                "dispatching",
                "/payload/connection",
                json!("0".repeat(64)),
            ),
            ("status:1", "dispatching", "/payload/mutation", json!(true)),
            (
                "status:1",
                "dispatching",
                "/request_sha256",
                json!("0".repeat(64)),
            ),
            (
                "status:1",
                "completed",
                "/command",
                json!("driver.manim-community.unexpected"),
            ),
            (
                "result",
                "completed",
                "/payload/result/data/artifact/editable_source/closure/plan_sha256",
                json!("0".repeat(64)),
            ),
            (
                "result",
                "completed",
                "/payload/result/data/artifact/editable_source/relative_path",
                json!("foreign.mwedit.zip"),
            ),
        ] {
            reject(|value| {
                let index = original_operation(value, suffix, stage);
                *value["current"][index].pointer_mut(pointer).unwrap() = replacement;
            });
        }
        reject(|value| {
            for stage in ["dispatching", "completed"] {
                let index = original_operation(value, "status:2", stage);
                value["current"][index]["request_id"] = json!(format!(
                    "{}:status:9",
                    value["native_link"]["request_id"].as_str().unwrap()
                ));
            }
        });
        reject(|value| value["current"][10]["payload"] = json!({"changed":true}));
        reject(|value| value["current"].as_array_mut().unwrap().swap(10, 11));
        reject(|value| value["current"].as_array_mut().unwrap().swap(0, 10));
        reject(|value| {
            value["prior"].as_array_mut().unwrap().reverse();
            value["current"].as_array_mut().unwrap().reverse();
        });
        reject(|value| {
            value["native_link"]["original_start_receipt"]["id"] =
                json!("00000000-0000-4000-8000-999999999999")
        });
        reject(|value| value["current_connection"] = json!("0".repeat(64)));
    }

    fn append_order_fixture(service: &StudioService, project: &Project, request_id: &str) {
        // Real Store writes exercise its public descending-order query. These
        // inert unit records are never evidence of a Native execution.
        for stage in ["dispatching", "completed"] {
            service
                .append_production_receipt(motionwright_storage::ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: request_id.into(),
                    request_sha256: hash(request_id.as_bytes()),
                    command: "driver.manim-community.render.status".into(),
                    stage: stage.into(),
                    payload: json!({"fixture_only":true,"mutation":false}),
                })
                .unwrap();
        }
    }

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
        append_order_fixture(&service, &project, "unit-reference-historical-order");
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
        original_receipt_projection_passes_without_discarding_operational_rows();
        let (root, baseline, current, bundle) = fixture();
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
        let original_project = reopened.project.clone();
        append_order_fixture(
            &reopened.current,
            &reopened.project,
            "unit-reference-added-order",
        );
        let original_receipts = receipts(&reopened.current, reopened.project.id).unwrap();
        let prior = reopened.prior_receipts.as_array().unwrap();
        let added = original_receipts.as_array().unwrap();
        assert_eq!((prior.len(), added.len()), (2, 4));
        assert!(added.ends_with(prior));
        assert!(!added.starts_with(prior));
        assert_eq!(added[0]["request_id"], "unit-reference-added-order");
        assert_eq!(added[0]["stage"], "completed");
        assert_eq!(added[1]["stage"], "dispatching");
        drop(reopened);
        let restored_parent = root.path().join("rehydrated-reference");
        fs::create_dir(&restored_parent).unwrap();
        let restored = restored_parent.join("motionwright.sqlite3");
        fs::copy(&current, &restored).unwrap();
        let report = root.path().join("reference-rehydration.json");
        let retained = rehydrate_reference(&baseline, &restored, &bundle, &report).unwrap();
        assert_eq!(retained["status"], "REFERENCE_CAS_REHYDRATED_SOURCE_ONLY");
        assert_eq!(
            retained["project_events_and_all_production_receipts_unchanged"],
            true
        );
        assert_eq!(retained["new_cas_capture_count"], 1);
        assert_eq!(retained["baseline_production_receipt_count"], 2);
        assert_eq!(retained["retained_production_receipt_count"], 4);
        assert_eq!(retained["project_changes_applied"], 0);
        assert_eq!(retained["native_execution_performed"], false);
        let rehydrated = source_state(&baseline, &restored, &bundle).unwrap();
        assert_eq!(rehydrated.project, original_project);
        assert_eq!(rehydrated.current_receipts, original_receipts);
        drop(rehydrated);
        let repeated = root.path().join("reference-rehydration-again.json");
        assert!(rehydrate_reference(&baseline, &restored, &bundle, &repeated).is_err());
        assert!(!repeated.exists());
        assert!(rehydrate_reference(&baseline, &baseline, &bundle, &repeated).is_err());
        assert!(!repeated.exists());
    }

    #[test]
    fn retained_reference_readback_rejects_changed_source_shared_store_and_missing_native() {
        original_receipt_projection_rejects_unbound_or_ambiguous_history();
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
                .contains("one or two complete new Native render lifecycles")
        );
        assert!(!report.exists());
        let observed = source_state(&baseline, &current, &bundle).unwrap();
        assert_eq!(observed.prior_receipts, observed.current_receipts);
    }
}
