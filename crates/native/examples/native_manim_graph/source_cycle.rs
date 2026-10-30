//! Source handoff and readback for the existing Manim Graph production APIs.
//! Graph saves belong to the existing bound Native provider. This consumer
//! never constructs an admission, invokes Store.apply, or retries a native job.
use super::*;
use motionwright_domain::{
    GraphContent, GraphSourceSnapshot, Project, RationalTime, SemanticGraph,
};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2,
    semantic_graph::ManimSemanticGraphPlan,
    semantic_graph_source::{
        GRAPH_SOURCE_MIME_V1, ManimGraphSourcePackageV1, compile_graph_program_source_only,
        graph_builtin_asset_pins, graph_source_bundle_v1, inspect_graph_source_bundle_v1,
    },
};
use motionwright_native::manim_graph_projection::{
    OriginalGraphBundleInput, build_manim_semantic_graph_source_plan,
    export_saved_manim_semantic_graph_source_bundle, inspect_saved_manim_semantic_graph_source,
    preview_saved_manim_semantic_graph_label_edit,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[cfg(unix)]
use std::io::Read;

#[path = "../../../domain/tests/support/semantic_graph_source.rs"]
mod authored_source;

type GraphResult<T> = Result<T, Box<dyn std::error::Error>>;
const REPORT_MAX: u64 = 4 * 1024 * 1024;
const ORIGINAL_LABEL: &str = "Authored concept";
const EDITED_LABEL: &str = "Edited concept";
const START: &str = "driver.manim-community.graph.render.start";

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PendingSource {
    schema: String,
    phase: String,
    project_before: Project,
    graph: SemanticGraph,
    original_source_asset_id: Option<Uuid>,
    edited_source_asset_id: Option<Uuid>,
    original_source_sha256: Option<String>,
    edited_source_sha256: Option<String>,
    original_native: Option<serde_json::Value>,
    native_execution_performed: bool,
    graph_write_performed: bool,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    schema: String,
    source_snapshot: GraphSourceSnapshot,
    graph_id: Uuid,
    scene_id: Uuid,
    deliverable_id: Uuid,
}

fn hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn field<'a>(value: &'a serde_json::Value, pointer: &str) -> GraphResult<&'a str> {
    value
        .pointer(pointer)
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("Missing original Graph field {pointer}").into())
}

#[cfg(unix)]
fn original(path: &Path, limit: u64) -> GraphResult<Vec<u8>> {
    use std::os::unix::fs::MetadataExt;
    // Reports and source ZIPs retain their separate, existing byte budgets.
    if limit != REPORT_MAX && limit != MAX_SOURCE_BUNDLE_BYTES_V2 as u64 {
        return Err("Graph original requires a fixed report or source carrier budget".into());
    }
    let before = std::fs::symlink_metadata(path)?;
    if !path.is_absolute()
        || path.canonicalize()? != path
        || !before.is_file()
        || before.file_type().is_symlink()
        || before.nlink() != 1
        || before.len() == 0
        || before.len() > limit
    {
        return Err("Graph original is not a bounded canonical regular file".into());
    }
    // Reuse the original example's held descriptor, including its FIFO defense.
    let mut file = graph_profile_open_original(path, &before)?;
    let opened = file.metadata()?;
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(limit + 1)
        .read_to_end(&mut bytes)?;
    let after = file.metadata()?;
    let final_path = std::fs::symlink_metadata(path)?;
    if bytes.len() as u64 != opened.len()
        || bytes.len() as u64 > limit
        || graph_profile_original_identity(&before) != graph_profile_original_identity(&opened)
        || graph_profile_original_identity(&opened) != graph_profile_original_identity(&after)
        || graph_profile_original_identity(&after) != graph_profile_original_identity(&final_path)
        || path.canonicalize()? != path
    {
        return Err("Graph original changed during descriptor capture".into());
    }
    Ok(bytes)
}

#[cfg(not(unix))]
fn original(_path: &Path, _limit: u64) -> GraphResult<Vec<u8>> {
    Err(semwright_native_sdk::Error::new(
        semwright_native_sdk::ErrorCode::Unsupported,
        "This original Graph lifecycle consumer requires Unix descriptor identity",
    )
    .into())
}

fn object(path: &Path) -> GraphResult<serde_json::Value> {
    Ok(serde_json::from_slice(&original(path, REPORT_MAX)?)?)
}

fn check_destination(path: &Path) -> GraphResult<()> {
    if !path.is_absolute()
        || !path.parent().is_some_and(Path::is_dir)
        || path.try_exists()?
        || path
            .parent()
            .ok_or("Graph destination has no parent")?
            .canonicalize()?
            != path.parent().ok_or("Graph destination has no parent")?
    {
        return Err("Graph report requires a fresh bounded owned destination".into());
    }
    Ok(())
}

fn new_report(path: &Path, value: &serde_json::Value) -> GraphResult<()> {
    check_destination(path)?;
    if serde_json::to_vec(value)?.len() as u64 > REPORT_MAX {
        return Err("Graph report exceeds the fixed original byte budget".into());
    }
    write_camera_evidence(path, value)?;
    if object(path)? != *value {
        return Err("Graph source report changed after exclusive publication".into());
    }
    Ok(())
}

fn selection(project: &Project, graph: &SemanticGraph) -> Selection {
    Selection {
        schema: "motionwright.manim.graph-cycle-selection/1".into(),
        source_snapshot: GraphSourceSnapshot::from_project(project),
        graph_id: graph.id,
        scene_id: graph.scene_id,
        deliverable_id: graph.variant_profile_ref,
    }
}

fn selected(project: &Project, input: &Selection) -> GraphResult<ManimSemanticGraphPlan> {
    if input.schema != "motionwright.manim.graph-cycle-selection/1" {
        return Err("Graph selection schema differs".into());
    }
    input.source_snapshot.require_current(project)?;
    let plan = build_manim_semantic_graph_source_plan(
        project,
        &input.source_snapshot,
        input.graph_id,
        input.deliverable_id,
    )?;
    if plan.source.scene_id != input.scene_id {
        return Err("Graph selection changed the scene".into());
    }
    require_fixture_clock(&plan)?;
    Ok(plan)
}

fn require_fixture_clock(plan: &ManimSemanticGraphPlan) -> GraphResult<()> {
    if plan.render_profile.width != 640
        || plan.render_profile.height != 360
        || plan.render_profile.frame_rate != 12
        || plan.scene_clock.frame_count != 24
        || plan.source.reference_frame.width != 640
        || plan.source.reference_frame.height != 360
    {
        return Err("Graph lifecycle source exceeds its explicit 24-frame profile".into());
    }
    Ok(())
}

fn authored_graph(project: &Project, id: Uuid) -> GraphResult<SemanticGraph> {
    let mut graph = authored_source::graph_for(project, true);
    graph.id = id;
    graph.time_range.start = project.scenes[0].start;
    graph.time_range.duration = project.scenes[0].duration;
    graph.authorship.author_label = "Native source lifecycle fixture".into();
    graph.authorship.rationale = "Observe one source reopen and one label edit".into();
    let GraphContent::SystemFlow {
        vertices,
        edges,
        events,
        ..
    } = &mut graph.content
    else {
        return Err("Shared Graph fixture is not the authored system flow".into());
    };
    // The source fixture is deliberately replanned to the existing saved clock,
    // rather than quantizing its 30-fps domain-test intervals during execution.
    for vertex in vertices {
        vertex.appearance.at = RationalTime::new(0, 1)?;
        vertex.appearance.duration = RationalTime::new(1, 12)?;
    }
    for edge in edges {
        edge.label = "Flow".into();
        edge.appearance.at = RationalTime::new(1, 12)?;
        edge.appearance.duration = RationalTime::new(1, 12)?;
    }
    for event in events {
        event.label = "Transfer".into();
        event.at = RationalTime::new(2, 12)?;
        event.duration = RationalTime::new(1, 12)?;
    }
    Ok(graph)
}

fn label_mut(graph: &mut SemanticGraph, id: Uuid) -> GraphResult<&mut String> {
    let vertices = match &mut graph.content {
        GraphContent::CausalDiagram { vertices, .. }
        | GraphContent::SystemFlow { vertices, .. } => vertices,
    };
    vertices
        .iter_mut()
        .find(|row| row.id == id)
        .map(|row| &mut row.label)
        .ok_or_else(|| "Selected Graph vertex is absent".into())
}

fn validate_pending(pending: &PendingSource) -> GraphResult<()> {
    if pending.schema != "motionwright.manim.graph-cycle-pending/1"
        || pending.native_execution_performed
        || pending.graph_write_performed
        || pending.graph.authorship.source_snapshot
            != GraphSourceSnapshot::from_project(&pending.project_before)
    {
        return Err("Pending Graph source changed its original scope or asserts execution".into());
    }
    match pending.phase.as_str() {
        "initial" => {
            if !pending
                .project_before
                .production_design
                .semantic_graphs
                .is_empty()
                || pending.graph != authored_graph(&pending.project_before, pending.graph.id)?
                || pending.original_source_asset_id.is_some()
                || pending.edited_source_asset_id.is_some()
                || pending.original_source_sha256.is_some()
                || pending.edited_source_sha256.is_some()
                || pending.original_native.is_some()
            {
                return Err("Initial Graph source differs from the shared authored fixture".into());
            }
        }
        "label" => {
            let saved = pending
                .project_before
                .production_design
                .semantic_graphs
                .iter()
                .find(|row| row.id == pending.graph.id)
                .ok_or("Original saved Graph is absent")?;
            let target = saved
                .content
                .vertices()
                .first()
                .ok_or("Graph has no first vertex")?
                .id;
            let mut expected = saved.clone();
            if label_mut(&mut expected, target)?.as_str() != ORIGINAL_LABEL {
                return Err("Original Graph label differs from the retained source".into());
            }
            *label_mut(&mut expected, target)? = EDITED_LABEL.into();
            expected.authorship.source_snapshot =
                GraphSourceSnapshot::from_project(&pending.project_before);
            if expected != pending.graph
                || pending.original_source_asset_id.is_none()
                || pending.edited_source_asset_id.is_none()
                || pending.original_source_asset_id == pending.edited_source_asset_id
                || pending.original_source_sha256.is_none()
                || pending.edited_source_sha256.is_none()
                || pending.original_source_sha256 == pending.edited_source_sha256
                || pending.original_native.is_none()
            {
                return Err("Pending Graph source exceeds the one selected label edit".into());
            }
        }
        _ => return Err("Unknown Graph source phase".into()),
    }
    let mut candidate = pending.project_before.clone();
    candidate.apply_change(&Change::UpsertSemanticGraph {
        graph: Box::new(pending.graph.clone()),
    })?;
    candidate.validate()?;
    require_fixture_clock(&build_manim_semantic_graph_source_plan(
        &candidate,
        &GraphSourceSnapshot::from_project(&candidate),
        pending.graph.id,
        pending.graph.variant_profile_ref,
    )?)?;
    Ok(())
}

pub(super) fn seed(database: &Path, report: &Path) -> GraphResult<serde_json::Value> {
    check_destination(report)?;
    let seed = create_graph_profile_seed(database)?;
    let service = StudioService::open(database)?;
    let project = service.project(seed.project_id)?;
    check_graph_profile_seed(&project, &seed)?;
    let graph = authored_graph(&project, Uuid::new_v4())?;
    let pending = PendingSource {
        schema: "motionwright.manim.graph-cycle-pending/1".into(),
        phase: "initial".into(),
        project_before: project.clone(),
        graph,
        original_source_asset_id: None,
        edited_source_asset_id: None,
        original_source_sha256: None,
        edited_source_sha256: None,
        original_native: None,
        native_execution_performed: false,
        graph_write_performed: false,
    };
    validate_pending(&pending)?;
    let value = json!({
        "schema":"motionwright.manim.graph-cycle-source/1",
        "application":motionwright_native::APP_ID,
        "application_version":motionwright_native::APP_VERSION,
        "resource":project.resource_key(), "seed":seed, "pending":pending,
        "graph_persisted":false, "native_authority":false,
        "render_performed":false, "human_acceptance":false,
    });
    new_report(report, &value)?;
    if StudioService::open(database)?.project(project.id)? != project
        || !service.production_receipts(project.id, 1)?.is_empty()
    {
        return Err("Preparing Graph source changed the persisted source or native ledger".into());
    }
    Ok(value)
}

fn pending_from(value: &serde_json::Value) -> GraphResult<PendingSource> {
    let pending: PendingSource = serde_json::from_value(
        value
            .get("pending")
            .cloned()
            .ok_or("Pending Graph handoff is absent")?,
    )?;
    validate_pending(&pending)?;
    Ok(pending)
}

fn package_assets(package: &ManimGraphSourcePackageV1) -> GraphResult<BTreeMap<String, Vec<u8>>> {
    graph_builtin_asset_pins()
        .iter()
        .map(|pin| {
            let key = format!("graph-profile-v1/fonts/{}", pin.relative_path);
            Ok((
                pin.relative_path.to_owned(),
                package
                    .entries
                    .get(&key)
                    .ok_or("Original Graph bundle lost a fixed font or license")?
                    .clone(),
            ))
        })
        .collect()
}

fn unchanged_receipts(
    service: &StudioService,
    project: &Project,
    native: &serde_json::Value,
) -> GraphResult<()> {
    let request = field(native, "/request_id")?;
    for (field_name, suffix) in [
        ("original_start_receipt", ":start"),
        ("original_result_receipt", ":result"),
    ] {
        let original = service
            .latest_production_receipt(project.id, &format!("{request}{suffix}"))?
            .ok_or("Original Graph receipt is absent")?;
        if serde_json::to_value(original)? != native[field_name] {
            return Err("Original Graph receipt changed during source-only work".into());
        }
    }
    Ok(())
}

fn native_source(
    service: &StudioService,
    project: &Project,
    path: &Path,
    root: &Path,
) -> GraphResult<(Vec<u8>, ManimGraphSourcePackageV1, serde_json::Value)> {
    let native = object(path)?;
    let expected = GraphSourceSnapshot::from_project(project);
    if native.get("native_manim_graph_e2e") != Some(&json!("PASS"))
        || native.get("source_snapshot") != Some(&json!(expected))
        || native.get("project_id") != Some(&json!(project.id))
    {
        return Err("Original native Graph belongs to another saved snapshot".into());
    }
    unchanged_receipts(service, project, &native)?;
    let custody = native
        .pointer("/result/artifact/graph_source")
        .ok_or("Native Graph source custody is absent")?;
    if custody.get("source_and_getter_bytes_verified") != Some(&json!(true))
        || native
            .pointer("/result/artifact/graph_source_custody_error")
            .is_some()
        || custody.get("source_snapshot") != Some(&json!(expected))
    {
        return Err("Original native Graph source custody is incomplete".into());
    }
    let descriptor = custody
        .get("source")
        .ok_or("Native Graph source descriptor is absent")?;
    let bytes = motionwright_native::production::read_verified_production_artifact(
        root,
        field(descriptor, "/relative_path")?,
        field(descriptor, "/sha256")?,
        MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
    )?;
    if descriptor.get("bytes").and_then(serde_json::Value::as_u64) != Some(bytes.len() as u64)
        || descriptor.get("media_type") != Some(&json!(GRAPH_SOURCE_MIME_V1))
    {
        return Err("Original Graph source carrier differs from its descriptor".into());
    }
    let package = inspect_graph_source_bundle_v1(&bytes)?;
    let plan = build_manim_semantic_graph_source_plan(
        project,
        &expected,
        package.plan.source.id,
        package.plan.source.variant_profile_ref,
    )?;
    if package.plan != plan || custody.get("source_closure") != Some(&json!(package.closure)) {
        return Err("Original Graph package differs from the complete current saved plan".into());
    }
    Ok((bytes, package, native))
}

fn attach(
    service: &StudioService,
    project: &Project,
    bytes: &[u8],
    root: &Path,
    request: &str,
) -> GraphResult<(Project, Uuid)> {
    let path = root.join(format!("source-{}.mwgraph.zip", Uuid::new_v4()));
    retain_seed_fixture(&path, bytes)?;
    let updated = service
        .import_asset_file(
            project.id,
            &RevisionStamp::from(project),
            request,
            &path,
            "Original editable Graph source".into(),
            GRAPH_SOURCE_MIME_V1.into(),
        )?
        .project;
    let additions: Vec<_> = updated
        .assets
        .iter()
        .filter(|asset| !project.assets.iter().any(|before| before.id == asset.id))
        .collect();
    if additions.len() != 1
        || additions[0].content_sha256.as_deref() != Some(hash(bytes).as_str())
        || additions[0].media_type != GRAPH_SOURCE_MIME_V1
        || updated.revision
            != project
                .revision
                .checked_add(1)
                .ok_or("Graph import revision overflow")?
    {
        return Err("Graph CAS import did not retain exactly one original source".into());
    }
    let id = additions[0].id;
    Ok((updated, id))
}

pub(super) fn prepare_edit(
    database: &Path,
    native_path: &Path,
    output_root: &Path,
    report: &Path,
    request: &str,
) -> GraphResult<serde_json::Value> {
    check_destination(report)?;
    if request.is_empty()
        || request.len() > 64
        || request.chars().any(char::is_control)
        || report.try_exists()?
        || !report.parent().is_some_and(Path::is_dir)
    {
        return Err("Graph edit requires a fresh bounded source attempt".into());
    }
    let native = object(native_path)?;
    let project_id = Uuid::parse_str(field(&native, "/project_id")?)?;
    let service = StudioService::open(database)?;
    let project = service.project(project_id)?;
    let (bytes, package, original_native) =
        native_source(&service, &project, native_path, output_root)?;
    let mut plan = package.plan.clone();
    let vertex_id = plan
        .source
        .content
        .vertices()
        .first()
        .ok_or("Graph has no editable vertex")?
        .id;
    if label_mut(&mut plan.source, vertex_id)?.as_str() != ORIGINAL_LABEL {
        return Err("Original Graph first label differs".into());
    }
    *label_mut(&mut plan.source, vertex_id)? = EDITED_LABEL.into();
    let edited_bytes = graph_source_bundle_v1(&plan, &package_assets(&package)?)?;
    let parent = report.parent().ok_or("Graph source report has no parent")?;
    new_report(
        &report.with_extension("intent.json"),
        &json!({
            "schema":"motionwright.manim.graph-cycle-cas-intent/1",
            "source_snapshot":GraphSourceSnapshot::from_project(&project),
            "request":request, "original_sha256":hash(&bytes), "edited_sha256":hash(&edited_bytes),
            "graph_write_performed":false, "provider_dispatch_performed":false, "automatic_retry":false,
        }),
    )?;
    let (attached, original_id) = attach(
        &service,
        &project,
        &bytes,
        parent,
        &format!("{request}:attach-original"),
    )?;
    let (attached, edited_id) = attach(
        &service,
        &attached,
        &edited_bytes,
        parent,
        &format!("{request}:attach-edited"),
    )?;
    drop(service);
    let service = StudioService::open(database)?;
    if service.project(project.id)? != attached {
        return Err("Graph source imports did not survive a fresh SQLite open".into());
    }
    unchanged_receipts(&service, &attached, &original_native)?;
    let expected = GraphSourceSnapshot::from_project(&attached);
    let inspected = inspect_saved_manim_semantic_graph_source(&service, &expected, original_id)?;
    let preview = preview_saved_manim_semantic_graph_label_edit(
        &service,
        &expected,
        original_id,
        edited_id,
        vertex_id,
    )?;
    if inspected.source_sha256 != hash(&bytes)
        || preview.original_source_sha256 != hash(&bytes)
        || preview.edited_source_sha256 != hash(&edited_bytes)
        || preview.expected != expected
        || preview.native_execution_performed
        || preview.imported_source_authority_used
        || preview.service_write_admission_granted
    {
        return Err("Graph label preview changed source bytes or asserts authority".into());
    }
    let Change::UpsertSemanticGraph { graph } = &preview.change else {
        return Err("Graph label preview is not the one canonical source Change".into());
    };
    let pending = PendingSource {
        schema: "motionwright.manim.graph-cycle-pending/1".into(),
        phase: "label".into(),
        project_before: attached,
        graph: graph.as_ref().clone(),
        original_source_asset_id: Some(original_id),
        edited_source_asset_id: Some(edited_id),
        original_source_sha256: Some(hash(&bytes)),
        edited_source_sha256: Some(hash(&edited_bytes)),
        original_native: Some(original_native),
        native_execution_performed: false,
        graph_write_performed: false,
    };
    validate_pending(&pending)?;
    let result = json!({
        "schema":"motionwright.manim.graph-cycle-edit-prepared/1",
        "pending":pending, "preview":preview, "original_source_inspection":inspected,
        "original_native_evidence_sha256":hash(&original(native_path, REPORT_MAX)?),
        "original_label":ORIGINAL_LABEL, "edited_label":EDITED_LABEL, "vertex_id":vertex_id,
        "source_assets_retained":true, "fresh_sqlite_readback":true,
        "graph_write_performed":false, "provider_dispatch_performed":false, "automatic_retry":false,
    });
    new_report(report, &result)?;
    Ok(result)
}

fn verify_cas(
    service: &StudioService,
    project: &Project,
    id: Uuid,
    digest: &str,
) -> GraphResult<()> {
    let asset = project
        .assets
        .iter()
        .find(|row| row.id == id)
        .ok_or("Retained Graph asset is absent")?;
    if asset.content_sha256.as_deref() != Some(digest) || asset.media_type != GRAPH_SOURCE_MIME_V1 {
        return Err("Retained Graph asset metadata differs".into());
    }
    let store = service.store();
    let store = store.lock();
    if store.load_project(project.id)? != *project {
        return Err("Graph project changed before original CAS readback".into());
    }
    let bytes = store.read_blob(digest, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)?;
    drop(store);
    if hash(&bytes) != digest {
        return Err("Retained Graph CAS bytes differ".into());
    }
    inspect_graph_source_bundle_v1(&bytes)?;
    Ok(())
}

pub(super) fn finish_save(
    database: &Path,
    pending_path: &Path,
    terminal_path: &Path,
    report: &Path,
) -> GraphResult<serde_json::Value> {
    check_destination(report)?;
    let prepared = object(pending_path)?;
    let pending = pending_from(&prepared)?;
    // This is the original CLI envelope after a single public bound invocation.
    // Neither this data nor this readback grants a later Graph save permission.
    let terminal = object(terminal_path)?;
    if terminal.get("ok") != Some(&json!(true))
        || terminal.pointer("/data/result/ok") != Some(&json!(true))
        || terminal.pointer("/data/admission_current") != Some(&json!(true))
        || terminal.pointer("/data/admission_state") != Some(&json!("current"))
    {
        return Err("Original Graph owner operation did not report success; no replay".into());
    }
    let applied = terminal
        .pointer("/data/result/data")
        .ok_or("Bound Graph operation has no original application result")?;
    if applied.get("replayed") != Some(&json!(false))
        || applied.get("resource") != Some(&json!(pending.project_before.resource_key()))
        || applied.get("generation") != Some(&json!(pending.project_before.generation.to_string()))
        || applied.get("previous_revision")
            != Some(&json!(pending.project_before.revision.to_string()))
    {
        return Err("Bound Graph save differs from the fresh original source operation".into());
    }
    let request = field(applied, "/request_id")?;
    if request.is_empty() || request.len() > 512 || request.chars().any(char::is_control) {
        return Err("Original Graph save has no bounded request identity".into());
    }
    let revision = pending
        .project_before
        .revision
        .checked_add(1)
        .ok_or("Graph save revision overflow")?;
    if applied.get("revision") != Some(&json!(revision.to_string())) {
        return Err("Graph save did not perform the one expected revision transition".into());
    }
    let service = StudioService::open(database)?;
    let current = service.project(pending.project_before.id)?;
    let mut intended = pending.project_before.clone();
    intended.apply_change(&Change::UpsertSemanticGraph {
        graph: Box::new(pending.graph.clone()),
    })?;
    intended.revision = revision;
    if current != intended || applied.get("project") != Some(&json!(current)) {
        return Err("Fresh Graph SQLite readback differs from the original admitted save".into());
    }
    let target = selection(&current, &pending.graph);
    let plan = selected(&current, &target)?;
    let mut export = None;
    if pending.phase == "label" {
        let original_id = pending
            .original_source_asset_id
            .ok_or("Original Graph asset identity was lost")?;
        let edited_id = pending
            .edited_source_asset_id
            .ok_or("Edited Graph asset identity was lost")?;
        verify_cas(
            &service,
            &current,
            original_id,
            pending
                .original_source_sha256
                .as_deref()
                .ok_or("Original Graph digest was lost")?,
        )?;
        verify_cas(
            &service,
            &current,
            edited_id,
            pending
                .edited_source_sha256
                .as_deref()
                .ok_or("Edited Graph digest was lost")?,
        )?;
        unchanged_receipts(
            &service,
            &current,
            pending
                .original_native
                .as_ref()
                .ok_or("Original Graph receipts were lost")?,
        )?;
        // The two CAS packages retain their historical authorship. The admitted
        // label save has a new current authorship stamp, so it must be exported
        // as a new package by the existing public API, never rebound in place.
        let bytes = export_saved_manim_semantic_graph_source_bundle(
            &service,
            &target.source_snapshot,
            target.graph_id,
            target.deliverable_id,
            edited_id,
        )?;
        let package = inspect_graph_source_bundle_v1(&bytes)?;
        if package.plan != plan {
            return Err("Current Graph export changed the complete saved source".into());
        }
        let path = report.with_extension("current.mwgraph.zip");
        retain_seed_fixture(&path, &bytes)?;
        if original(&path, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)? != bytes {
            return Err("Current Graph export changed during original readback".into());
        }
        export = Some(json!({
            "path":path, "sha256":hash(&bytes), "bytes":bytes.len(), "closure":package.closure,
            "closed_package_plan_equals_saved_plan":true, "historical_assets_rewritten":false,
        }));
    } else if !service.production_receipts(current.id, 1)?.is_empty() {
        return Err("Initial Graph save unexpectedly acquired native render receipts".into());
    }
    let value = json!({
        "schema":"motionwright.manim.graph-cycle-saved/1", "phase":pending.phase,
        "pending_source_sha256":hash(&original(pending_path, REPORT_MAX)?),
        "owner_terminal_sha256":hash(&original(terminal_path, REPORT_MAX)?),
        "owner_request_id":request, "selection":target, "project":current,
        "prepared_plan":plan, "current_export":export,
        "original_label":ORIGINAL_LABEL, "edited_label":EDITED_LABEL,
        "original_source_asset_id":pending.original_source_asset_id,
        "edited_source_asset_id":pending.edited_source_asset_id,
        "original_source_sha256":pending.original_source_sha256,
        "edited_source_sha256":pending.edited_source_sha256,
        "fresh_sqlite_readback":true, "render_performed":false,
        "native_source_reopen_verified":false, "human_acceptance":false, "automatic_retry":false,
    });
    new_report(report, &value)?;
    Ok(value)
}

pub(super) async fn realize(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    saved_path: &Path,
    input_path: Option<&Path>,
    request: &str,
) -> GraphResult<()> {
    check_destination(evidence_path)?;
    let saved = object(saved_path)?;
    if saved.get("schema") != Some(&json!("motionwright.manim.graph-cycle-saved/1"))
        || saved.get("fresh_sqlite_readback") != Some(&json!(true))
    {
        return Err("Graph source readback is absent".into());
    }
    let target: Selection = serde_json::from_value(saved["selection"].clone())?;
    let service = StudioService::open(database)?;
    let project = service.project(target.source_snapshot.project_id)?;
    let plan = selected(&project, &target)?;
    if saved.get("project") != Some(&json!(project))
        || saved.get("prepared_plan") != Some(&json!(plan))
    {
        return Err("Graph source selection changed before native dispatch".into());
    }
    let start_id = format!("{request}:start");
    if service
        .latest_production_receipt(project.id, &start_id)?
        .is_some()
    {
        return Err("Original Graph attempt already exists; inspect it without replay".into());
    }
    let input: Option<OriginalGraphBundleInput> = input_path
        .map(object)
        .transpose()?
        .map(serde_json::from_value)
        .transpose()?;
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("Graph connection belongs to another canonical project".into());
    }
    let prepared = compile_graph_program_source_only(&plan)?;
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let runtime = coordinator
        .observe_manim_runtime_version(project.id, &RevisionStamp::from(&project))
        .await;
    let probe = json!({
        "schema":"motionwright-manim-runtime-probe-e2e/1",
        "project_id":project.id, "generation":project.generation, "revision":project.revision,
        "observation":runtime.as_ref().ok(), "error":runtime.as_ref().err(), "render_started":false,
    });
    retain_runtime_probe_to(
        &evidence_path.with_file_name("motionwright-manim-runtime-observation.json"),
        &probe,
        runtime.as_ref().err().cloned(),
        write_camera_evidence,
        &mut std::io::stderr().lock(),
    )?;
    let runtime = runtime?;
    if runtime.state() != ManimRuntimeVersionState::ObservedCompatible {
        return Err(
            "Manim runtime was not observed compatible before the original Graph render".into(),
        );
    }
    let attempted = match &input {
        Some(input) => {
            coordinator
                .reopen_manim_semantic_graph(
                    &target.source_snapshot,
                    request,
                    target.graph_id,
                    target.deliverable_id,
                    input.clone(),
                )
                .await
        }
        None => {
            coordinator
                .realize_manim_semantic_graph(
                    &target.source_snapshot,
                    request,
                    target.graph_id,
                    target.deliverable_id,
                )
                .await
        }
    };
    let receipts = coordinator.receipts(project.id, 256);
    let mut evidence = json!({
        "source_snapshot":target.source_snapshot,
        "project_id":project.id, "resource":project.resource_key(), "generation":project.generation,
        "revision":project.revision, "scene_id":target.scene_id, "graph_id":target.graph_id,
        "deliverable_id":target.deliverable_id, "request_id":request, "profile":plan.render_profile,
        "prepared_plan":plan, "runtime_observation":runtime,
        "requested_original_source_bundle":input,
        "recent_receipts":receipts.as_ref().ok(), "receipt_read_error":receipts.as_ref().err(),
        "receipt_window_limit":256, "decoded_frames_verified":false,
        "native_bundle_reopen_verified":false, "human_acceptance":false, "automatic_retry":false,
    });
    let completed = match attempted {
        Ok(value) => value,
        Err(error) => {
            return Err(retain_native_error_to(
                evidence_path,
                &mut evidence,
                error,
                None,
                &mut std::io::stderr().lock(),
                "native_manim_graph_e2e",
            )
            .into());
        }
    };
    let receipts = match receipts {
        Ok(value) => value,
        Err(error) => {
            return Err(retain_native_error_to(
                evidence_path,
                &mut evidence,
                error,
                Some(completed),
                &mut std::io::stderr().lock(),
                "native_manim_graph_e2e",
            )
            .into());
        }
    };
    drop(coordinator);
    finish_native_evidence(
        evidence_path,
        &mut evidence,
        completed,
        |evidence| {
            let result = &evidence["result"];
            if result.get("renderer") != Some(&json!("manim-community"))
                || result.get("native_driver") != Some(&json!("driver:manim-community"))
                || result.get("runtime_contract") != Some(&json!("Manim Community 0.21.0"))
                || result.get("project_revision") != Some(&json!(project.revision))
                || result.get("scene_id") != Some(&json!(target.scene_id))
                || result.get("plan_sha256") != Some(&json!(prepared.plan_sha256))
                || result.get("profile") != Some(&json!(plan.render_profile))
            {
                return Err("Original Graph terminal changed source or runtime identity".into());
            }
            let artifact = result
                .get("artifact")
                .ok_or("Graph native artifact absent")?;
            let source = artifact
                .get("graph_source")
                .ok_or("Graph native source custody absent")?;
            if artifact.get("graph_source_custody_error").is_some()
                || source.get("source_and_getter_bytes_verified") != Some(&json!(true))
                || source.get("source_snapshot") != Some(&json!(target.source_snapshot))
                || source.get("graph_id") != Some(&json!(target.graph_id))
                || source.get("scene_id") != Some(&json!(target.scene_id))
                || source.get("deliverable_id") != Some(&json!(target.deliverable_id))
                || source.pointer("/source_closure/plan_sha256")
                    != Some(&json!(prepared.plan_sha256))
                || source.pointer("/readback/sample_count")
                    != Some(&json!(plan.scene_clock.frame_count))
            {
                return Err(
                    "Original Graph source/getter custody differs from the complete saved plan"
                        .into(),
                );
            }
            if let Some(input) = &input {
                if artifact.get("graph_input_source_custody_error").is_some()
                    || artifact.pointer("/graph_input_source/input") != Some(&json!(input))
                    || source.pointer("/source/sha256") != Some(&json!(input.sha256))
                    || source.pointer("/source/bytes") != Some(&json!(input.bytes))
                {
                    return Err(
                        "Original Graph ZIP was not preserved by the independent source open"
                            .into(),
                    );
                }
            } else if artifact.get("graph_input_source").is_some() {
                return Err("Generated Graph render unexpectedly reported a source input".into());
            }
            let reopened = StudioService::open(database)?;
            if reopened.project(project.id)? != project {
                return Err("Graph project changed during the original native render".into());
            }
            let start = reopened
                .latest_production_receipt(project.id, &start_id)?
                .ok_or("Graph start receipt was not durable")?;
            let terminal = reopened
                .latest_production_receipt(project.id, &format!("{request}:result"))?
                .ok_or("Graph result receipt was not durable")?;
            if start.command != START
                || terminal.command != "driver.manim-community.render.result"
                || start.stage != "completed"
                || terminal.stage != "completed"
                || start.generation != project.generation
                || terminal.generation != project.generation
                || start.revision != project.revision
                || terminal.revision != project.revision
                || result.pointer("/result/receipt") != Some(&json!(terminal))
            {
                return Err(
                    "Graph native receipt changed its original command or canonical scope".into(),
                );
            }
            evidence["original_start_receipt"] = json!(start);
            evidence["original_result_receipt"] = json!(terminal);
            evidence["receipt_count"] = json!(receipts.len());
            Ok(())
        },
        write_camera_evidence,
        "native_manim_graph_e2e",
    )?;
    writeln!(&mut std::io::stdout().lock(), "{evidence}")?;
    Ok(())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn report_and_source_originals_keep_their_distinct_fixed_byte_budgets() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let report = root.join("source.json");
        let value = json!({"padding": "x".repeat(MAX_SOURCE_BUNDLE_BYTES_V2)});
        new_report(&report, &value).unwrap();
        let bytes = std::fs::metadata(&report).unwrap().len();
        assert!(bytes > MAX_SOURCE_BUNDLE_BYTES_V2 as u64 && bytes <= REPORT_MAX);
        assert_eq!(object(&report).unwrap(), value);
        let error = original(&report, MAX_SOURCE_BUNDLE_BYTES_V2 as u64).unwrap_err();
        assert!(error.to_string().contains("bounded canonical regular file"));
        for limit in [0, MAX_SOURCE_BUNDLE_BYTES_V2 as u64 + 1, REPORT_MAX + 1] {
            assert!(original(&report, limit).is_err());
        }

        let oversized = root.join("oversized.json");
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&oversized)
            .unwrap();
        file.set_len(REPORT_MAX + 1).unwrap();
        drop(file);
        assert!(object(&oversized).is_err());
        assert_eq!(std::fs::metadata(&oversized).unwrap().len(), REPORT_MAX + 1);
    }

    #[test]
    fn source_seed_keeps_graph_absent_until_the_original_bound_provider_save() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let database = root.join("motionwright.sqlite3");
        let report = root.join("source.json");
        let value = seed(&database, &report).unwrap();
        let pending = pending_from(&value).unwrap();
        let service = StudioService::open(&database).unwrap();
        assert_eq!(
            service.project(pending.project_before.id).unwrap(),
            pending.project_before
        );
        assert!(
            pending
                .project_before
                .production_design
                .semantic_graphs
                .is_empty()
        );
        assert!(
            service
                .production_receipts(pending.project_before.id, 1)
                .unwrap()
                .is_empty()
        );
        assert_eq!(object(&report).unwrap(), value);
        assert_eq!(value["native_authority"], json!(false));
    }

    #[test]
    fn pending_source_rejects_wrong_clock_extra_label_and_serialized_write_claims() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let value = seed(
            &root.join("motionwright.sqlite3"),
            &root.join("source.json"),
        )
        .unwrap();
        let original = pending_from(&value).unwrap();
        let mut wrong = original.clone();
        wrong.native_execution_performed = true;
        assert!(validate_pending(&wrong).is_err());
        let mut wrong = original.clone();
        wrong.graph_write_performed = true;
        assert!(validate_pending(&wrong).is_err());
        let mut wrong = original.clone();
        wrong.graph.time_range.duration = RationalTime::new(3, 1).unwrap();
        assert!(validate_pending(&wrong).is_err());
        let mut wrong = original;
        let vertex = wrong.graph.content.vertices()[1].id;
        *label_mut(&mut wrong.graph, vertex).unwrap() = "Unexpected second change".into();
        assert!(validate_pending(&wrong).is_err());
    }

    #[test]
    fn rejected_source_destination_does_not_open_or_create_a_database() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let database = root.join("motionwright.sqlite3");
        let report = root.join("source.json");
        std::fs::write(&report, b"original destination").unwrap();
        assert!(seed(&database, &report).is_err());
        assert!(!database.exists());
        assert_eq!(std::fs::read(&report).unwrap(), b"original destination");
    }
}
