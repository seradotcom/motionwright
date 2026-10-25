//! Canonical Graph caller on the existing production connection and receipt body.
//! Source/readback verification grants no profile readiness, video acceptance,
//! source edit or permission to repeat an unresolved native request.
use super::*;
use motionwright_domain::{GraphSourceSnapshot, Project};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2, ManimSourceEntryV1,
    semantic_graph::ManimSemanticGraphPlan,
    semantic_graph_readback::inspect_native_graph_readback_v1,
    semantic_graph_source::{
        GRAPH_SOURCE_MIME_V1, compile_graph_program_source_only, graph_native_readback_paths_v1,
        inspect_graph_source_bundle_v1,
    },
};
use std::collections::{BTreeMap, BTreeSet};

pub(super) const START_COMMAND: &str = "driver.manim-community.graph.render.start";
const INDEX_SCHEMA: &str = "motionwright.manim-graph-original-readback/1";
const PROFILE_ID: &str = "mw-manim-semantic-graph-v1";
const MAX_INDEX_BYTES: u64 = 1024 * 1024;
const MAX_READBACK_BYTES: u64 = 32 * 1024 * 1024;
const MAX_ORIGINAL_FILES: usize = 4096;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphStart {
    plan: ManimSemanticGraphPlan,
    profile: ManimRenderProfile,
    #[serde(default, deserialize_with = "graph_source_bundle_field")]
    source_bundle: Option<crate::manim_graph_projection::OriginalGraphBundleInput>,
}

fn graph_source_bundle_field<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<crate::manim_graph_projection::OriginalGraphBundleInput>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    crate::manim_graph_projection::OriginalGraphBundleInput::deserialize(deserializer).map(Some)
}

fn unsupported(message: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

fn stale() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Semantic Graph requires the original current project, generation, branch and revision",
    )
}

fn current(service: &StudioService, expected: &GraphSourceSnapshot) -> NativeResult<Project> {
    let project = service
        .project(expected.project_id)
        .map_err(storage_error)?;
    expected.require_current(&project).map_err(|_| stale())?;
    Ok(project)
}

fn source_document(plan: &ManimSemanticGraphPlan) -> NativeResult<Vec<u8>> {
    plan.source_document()
        .map_err(|_| unsupported("Graph source is outside the closed canonical profile"))
}

fn require_saved_plan(project: &Project, plan: &ManimSemanticGraphPlan) -> NativeResult<()> {
    plan.execution_snapshot
        .require_current(project)
        .map_err(|_| stale())?;
    let saved = crate::manim_graph_projection::build_manim_semantic_graph_source_plan(
        project,
        &plan.execution_snapshot,
        plan.source.id,
        plan.source.variant_profile_ref,
    )?;
    if source_document(&saved)? != source_document(plan)? {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Graph dispatch source differs from the selected persisted canonical Graph",
        ));
    }
    Ok(())
}

pub(super) fn require_non_graph_selection(
    project: &Project,
    scene_id: Uuid,
    deliverable_id: Option<Uuid>,
) -> NativeResult<()> {
    if project
        .production_design
        .semantic_graphs
        .iter()
        .any(|graph| {
            graph.scene_id == scene_id
                && deliverable_id.is_none_or(|selected| graph.variant_profile_ref == selected)
        })
    {
        return Err(unsupported(
            "Saved semantic Graph requires the explicit four-field Graph production entrypoint",
        ));
    }
    Ok(())
}

pub(super) fn require_non_graph_saved_selection(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<()> {
    let project = service.project(project_id).map_err(storage_error)?;
    if RevisionStamp::from(&project) != *expected {
        return Err(stale());
    }
    require_non_graph_selection(&project, scene_id, Some(deliverable_id))
}

fn bound_uuid(args: &Value, pointer: &str) -> NativeResult<Uuid> {
    let spelling = args
        .pointer(pointer)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("Manim start has no explicit canonical source identity"))?;
    let id =
        Uuid::parse_str(spelling).map_err(|_| invalid("Manim source identity is not a UUID"))?;
    if id.to_string() != spelling {
        return Err(invalid(
            "Manim source identity must use its canonical UUID spelling",
        ));
    }
    Ok(id)
}

fn is_start(command: &str) -> bool {
    matches!(
        command,
        START_COMMAND
            | "driver.manim-community.render.start"
            | "driver.manim-community.static.render.start"
            | "driver.manim-community.editable.render.start"
            | "driver.manim-community.camera.render.start"
    )
}

/// Runs before receipt replay and before any reservation or dispatch. A supplied
/// Graph plan is data: it must be exactly reconstructed from the current Project.
pub(super) fn validate_dispatch_source(
    project: &Project,
    command: &str,
    args: &Value,
) -> NativeResult<()> {
    if command == START_COMMAND {
        if serde_json::to_vec(args)
            .map_err(|_| invalid("Graph start cannot be encoded"))?
            .len()
            > MAX_ARGS_BYTES
        {
            return Err(unsupported(
                "Graph start exceeds the existing native argument byte bound",
            ));
        }
        let wire: GraphStart = serde_json::from_value(args.clone())
            .map_err(|_| invalid("Graph start must contain only its canonical plan, profile and optional original input"))?;
        if wire.profile != wire.plan.render_profile {
            return Err(invalid(
                "Graph start profile differs from its authored plan",
            ));
        }
        if let Some(input) = &wire.source_bundle {
            crate::manim_graph_projection::validate_original_manim_graph_input(input)?;
        }
        return require_saved_plan(project, &wire.plan);
    }
    if !is_start(command) || project.production_design.semantic_graphs.is_empty() {
        return Ok(());
    }
    let (scene_id, deliverable_id) = if command == "driver.manim-community.render.start" {
        (bound_uuid(args, "/plan/scene_id")?, None)
    } else {
        (
            bound_uuid(args, "/plan/binding/scene_id")?,
            Some(bound_uuid(args, "/plan/binding/deliverable_id")?),
        )
    };
    require_non_graph_selection(project, scene_id, deliverable_id)
}

/// A second read immediately before the existing custody/intent boundary keeps
/// a changed branch or source from using the first preflight. Store admission
/// still owns its existing transactional generation/revision fence.
pub(super) fn recheck_before_dispatch(
    service: &StudioService,
    prepared_project: &Project,
    command: &str,
    args: &Value,
) -> NativeResult<()> {
    if !is_start(command) {
        return Ok(());
    }
    let project = service
        .project(prepared_project.id)
        .map_err(storage_error)?;
    if project != *prepared_project {
        return Err(stale());
    }
    validate_dispatch_source(&project, command, args)
}

impl ProductionCoordinator {
    /// Reopen the exact closed Graph archive through the existing admitted Host.
    /// This neither refreshes the original snapshot nor grants Graph writes.
    pub async fn reopen_manim_semantic_graph(
        &self,
        expected: &GraphSourceSnapshot,
        request_id: &str,
        graph_id: Uuid,
        deliverable_id: Uuid,
        input: crate::manim_graph_projection::OriginalGraphBundleInput,
    ) -> NativeResult<Value> {
        validate_manim_request_id(request_id)?;
        let before = current(&self.service, expected)?;
        let package =
            crate::manim_graph_projection::prepare_saved_manim_semantic_graph_bundle_input(
                &self.service,
                expected,
                graph_id,
                deliverable_id,
                &self.client.connection().output_root,
                &input,
            )?;
        require_saved_plan(&before, &package.plan)?;
        if current(&self.service, expected)? != before {
            return Err(stale());
        }
        self.realize_prepared_manim_scene(
            expected.project_id,
            &RevisionStamp::from(&before),
            request_id,
            package.plan.source.scene_id,
            PreparedManimRender {
                start_command: START_COMMAND,
                plan_value: serde_json::to_value(&package.plan)
                    .map_err(|_| invalid("Original Graph plan cannot be encoded"))?,
                plan_sha256: package.closure.plan_sha256,
                profile: package.plan.render_profile,
                requires_camera_getters: false,
                source_bundle: Some(PreparedManimSourceInput::Graph(input)),
            },
        )
        .await
    }

    /// Realize one persisted Graph on the existing Manim Driver Host. The
    /// caller must supply all four current snapshot fields; this method never
    /// derives the intended branch from the three-field storage stamp.
    pub async fn realize_manim_semantic_graph(
        &self,
        expected: &GraphSourceSnapshot,
        request_id: &str,
        graph_id: Uuid,
        deliverable_id: Uuid,
    ) -> NativeResult<Value> {
        validate_manim_request_id(request_id)?;
        let before = current(&self.service, expected)?;
        let document = crate::manim_graph_projection::prepare_saved_manim_semantic_graph_source(
            &self.service,
            expected,
            graph_id,
            deliverable_id,
        )?;
        let plan: ManimSemanticGraphPlan = serde_json::from_slice(&document)
            .map_err(|_| invalid("Saved Graph source cannot be reopened"))?;
        require_saved_plan(&before, &plan)?;
        let compiled = compile_graph_program_source_only(&plan)
            .map_err(|_| unsupported("Saved Graph cannot be compiled by its closed profile"))?;
        if compiled.plan_document != document
            || compiled.native_execution_performed
            || compiled.runtime_sample_pts_verified
            || hex::encode(Sha256::digest(&document)) != compiled.plan_sha256
        {
            return Err(invalid(
                "Graph compiler did not preserve the original source-only binding",
            ));
        }
        if current(&self.service, expected)? != before {
            return Err(stale());
        }
        let storage_stamp = RevisionStamp::from(&before);
        self.realize_prepared_manim_scene(
            expected.project_id,
            &storage_stamp,
            request_id,
            plan.source.scene_id,
            PreparedManimRender {
                start_command: START_COMMAND,
                plan_value: serde_json::to_value(&plan)
                    .map_err(|_| invalid("Graph plan cannot be encoded"))?,
                plan_sha256: compiled.plan_sha256,
                profile: plan.render_profile,
                requires_camera_getters: false,
                source_bundle: None,
            },
        )
        .await
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileDescriptor {
    relative_path: String,
    sha256: String,
    bytes: u64,
    media_type: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceDescriptor {
    relative_path: String,
    sha256: String,
    bytes: u64,
    media_type: String,
    closure: Value,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OriginalEntry {
    path: String,
    bytes: u64,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OriginalIndex {
    schema: String,
    profile_id: String,
    source_file: String,
    plan_sha256: String,
    generated_source_sha256: String,
    original_directory: String,
    entries: Vec<OriginalEntry>,
    inspector_summary: Value,
    native_video_verified: bool,
    native_reopen_verified: bool,
    profile_admitted: bool,
}

fn job_paths(
    video_relative: &str,
    profile: ManimRenderProfile,
) -> NativeResult<(String, String, String)> {
    let token = video_relative
        .rsplit('/')
        .next()
        .and_then(|name| name.strip_prefix("mw_"))
        .and_then(|name| name.strip_suffix(".mp4"))
        .filter(|token| {
            token.len() == 32
                && token
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        .ok_or_else(|| invalid("Graph video name does not identify the original native job"))?;
    if video_relative
        != format!(
            "videos/mw_{token}/{}/mw_{token}.mp4",
            profile.quality_directory()
        )
    {
        return Err(invalid(
            "Graph video path differs from the original job/profile",
        ));
    }
    Ok((
        format!("mw_{token}.py"),
        format!("mw_{token}.mwgraph.zip"),
        format!("mw_{token}.graph-readback"),
    ))
}

fn checked_bytes(
    root: &Path,
    relative: &str,
    digest: &str,
    bytes: u64,
    maximum: u64,
) -> NativeResult<Vec<u8>> {
    if bytes == 0 || bytes > maximum {
        return Err(invalid(
            "Graph artifact exceeds its explicit original byte bound",
        ));
    }
    let raw = read_verified_production_artifact(root, relative, digest, maximum)?;
    if raw.len() as u64 != bytes {
        return Err(invalid(
            "Graph artifact size differs from its original descriptor",
        ));
    }
    Ok(raw)
}

fn decode_index(
    raw: &[u8],
    source_file: &str,
    directory: &str,
    plan_sha256: &str,
    source_sha256: &str,
    required: &BTreeSet<String>,
    allowed: &BTreeSet<String>,
) -> NativeResult<OriginalIndex> {
    if raw.is_empty()
        || raw.len() as u64 > MAX_INDEX_BYTES
        || allowed.len() > MAX_ORIGINAL_FILES
        || !required.is_subset(allowed)
    {
        return Err(invalid(
            "Graph readback index exceeds its explicit bounded role set",
        ));
    }
    let index: OriginalIndex = serde_json::from_slice(raw)
        .map_err(|_| invalid("Graph original readback index is not the closed typed document"))?;
    if index.schema != INDEX_SCHEMA
        || index.profile_id != PROFILE_ID
        || index.source_file != source_file
        || index.original_directory != directory
        || index.plan_sha256 != plan_sha256
        || index.generated_source_sha256 != source_sha256
        || index.native_video_verified
        || index.native_reopen_verified
        || index.profile_admitted
        || index.entries.len() > allowed.len()
    {
        return Err(invalid(
            "Graph original index has a different binding or asserts authority",
        ));
    }
    let mut observed = BTreeSet::new();
    let mut total = 0u64;
    for entry in &index.entries {
        if !allowed.contains(&entry.path)
            || !observed.insert(entry.path.clone())
            || entry.bytes == 0
            || !is_sha256(&entry.sha256)
        {
            return Err(invalid(
                "Graph original index has an unapproved, repeated or invalid role",
            ));
        }
        total = total
            .checked_add(entry.bytes)
            .ok_or_else(|| invalid("Graph original index byte budget overflow"))?;
        if total > MAX_READBACK_BYTES {
            return Err(invalid(
                "Graph originals exceed the aggregate readback byte bound",
            ));
        }
    }
    if !required.is_subset(&observed) {
        return Err(invalid("Graph original index omits a required observation"));
    }
    Ok(index)
}

fn observed_paths(root: &Path, directory: &str) -> NativeResult<BTreeSet<String>> {
    let path = root.join(directory);
    if !safe_metadata(&path)?.is_dir() {
        return Err(invalid(
            "Graph retained original directory is not a plain directory",
        ));
    }
    let labels = path.join("labels");
    if !safe_metadata(&labels)?.is_dir() {
        return Err(invalid(
            "Graph retained label directory is not a plain directory",
        ));
    }
    let unavailable = |_| {
        Error::new(
            ErrorCode::Unavailable,
            "Graph original roster is unavailable",
        )
    };
    let mut top = BTreeSet::new();
    for entry in fs::read_dir(&path).map_err(unavailable)? {
        let entry = entry.map_err(unavailable)?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("Graph original role is not UTF-8"))?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(unavailable)?;
        if is_link_or_reparse(&metadata)
            || !top.insert(name.clone())
            || !matches!(
                name.as_str(),
                "header.json" | "frames.jsonl" | "index.json" | "labels"
            )
            || (name == "labels" && !metadata.is_dir())
            || (name != "labels" && !metadata.is_file())
        {
            return Err(invalid(
                "Graph original roster contains an unexpected file or link",
            ));
        }
    }
    let expected_top: BTreeSet<String> = ["header.json", "frames.jsonl", "index.json", "labels"]
        .map(str::to_owned)
        .into();
    if top != expected_top {
        return Err(invalid("Graph original roster is incomplete"));
    }
    let mut observed: BTreeSet<String> = ["header.json", "frames.jsonl"].map(str::to_owned).into();
    for entry in fs::read_dir(&labels).map_err(unavailable)? {
        if observed.len() >= MAX_ORIGINAL_FILES {
            return Err(invalid("Graph original roster exceeds its file bound"));
        }
        let entry = entry.map_err(unavailable)?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("Graph label role is not UTF-8"))?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(unavailable)?;
        if is_link_or_reparse(&metadata)
            || !metadata.is_file()
            || !observed.insert(format!("labels/{name}"))
        {
            return Err(invalid(
                "Graph original label is a link, directory or repeated role",
            ));
        }
    }
    Ok(observed)
}

/// The enclosing coordinator already checked the acknowledged job/generation
/// and video bytes. This function independently reopens all source/readback
/// originals. No imported JSON field can grant execution or acceptance.
pub(super) fn verify_rendered_source(
    service: &StudioService,
    output_root: &Path,
    video_relative: &str,
    plan: &ManimSemanticGraphPlan,
    artifact: &Value,
) -> NativeResult<Value> {
    let before = current(service, &plan.execution_snapshot)?;
    require_saved_plan(&before, plan)?;
    let (source_file, source_path, directory) = job_paths(video_relative, plan.render_profile)?;
    let source: SourceDescriptor = serde_json::from_value(
        artifact
            .get("graph_source")
            .cloned()
            .ok_or_else(|| invalid("Native Graph result has no retained source descriptor"))?,
    )
    .map_err(|_| invalid("Native Graph source descriptor is not closed"))?;
    if source.relative_path != source_path || source.media_type != GRAPH_SOURCE_MIME_V1 {
        return Err(invalid(
            "Graph source descriptor belongs to another job or media type",
        ));
    }
    let source_raw = checked_bytes(
        output_root,
        &source_path,
        &source.sha256,
        source.bytes,
        MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
    )?;
    let package = inspect_graph_source_bundle_v1(&source_raw)
        .map_err(|_| invalid("Native Graph source differs from the frozen trusted compiler"))?;
    if source_document(&package.plan)? != source_document(plan)?
        || serde_json::to_value(&package.closure)
            .map_err(|_| invalid("Graph source closure cannot be encoded"))?
            != source.closure
    {
        return Err(invalid(
            "Graph source closure differs from the original canonical source",
        ));
    }
    let getter: FileDescriptor = serde_json::from_value(
        artifact
            .get("graph_getters")
            .cloned()
            .ok_or_else(|| invalid("Native Graph result has no original readback index"))?,
    )
    .map_err(|_| invalid("Native Graph getter descriptor is not closed"))?;
    let index_path = format!("{directory}/index.json");
    if getter.relative_path != index_path || getter.media_type != "application/json" {
        return Err(invalid(
            "Graph getter descriptor belongs to another original job",
        ));
    }
    let index_raw = checked_bytes(
        output_root,
        &index_path,
        &getter.sha256,
        getter.bytes,
        MAX_INDEX_BYTES,
    )?;
    let spec = graph_native_readback_paths_v1(plan)
        .map_err(|_| unsupported("Graph original role set exceeds its closed profile"))?;
    let index = decode_index(
        &index_raw,
        &source_file,
        &directory,
        &package.closure.plan_sha256,
        &package.closure.source_sha256,
        &spec.required,
        &spec.allowed,
    )?;
    let indexed: BTreeSet<String> = index
        .entries
        .iter()
        .map(|entry| entry.path.clone())
        .collect();
    if observed_paths(output_root, &directory)? != indexed {
        return Err(invalid(
            "Graph index does not enumerate the complete retained original roster",
        ));
    }
    let mut files = BTreeMap::new();
    let mut remaining = MAX_READBACK_BYTES;
    for entry in &index.entries {
        let raw = checked_bytes(
            output_root,
            &format!("{directory}/{}", entry.path),
            &entry.sha256,
            entry.bytes,
            remaining,
        )?;
        remaining = remaining
            .checked_sub(raw.len() as u64)
            .ok_or_else(|| invalid("Graph original readback byte budget overflow"))?;
        files.insert(entry.path.clone(), raw);
    }
    let summary = inspect_native_graph_readback_v1(plan, &files).map_err(|_| {
        invalid("Graph original readback fails independent native geometry/typography checks")
    })?;
    let summary_value = serde_json::to_value(&summary)
        .map_err(|_| invalid("Graph readback summary cannot be encoded"))?;
    if index.inspector_summary != summary_value {
        return Err(invalid(
            "Graph index summary differs from the original observed files",
        ));
    }
    let entries: Vec<ManimSourceEntryV1> = files
        .iter()
        .map(|(path, raw)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: raw.len() as u64,
            sha256: hex::encode(Sha256::digest(raw)),
        })
        .collect();
    let mut recomputed = serde_json::to_vec_pretty(&json!({
        "schema": INDEX_SCHEMA, "profile_id": PROFILE_ID,
        "source_file": source_file,
        "plan_sha256": package.closure.plan_sha256,
        "generated_source_sha256": package.closure.source_sha256,
        "original_directory": directory, "entries": entries,
        "inspector_summary": summary_value,
        "native_video_verified": false, "native_reopen_verified": false,
        "profile_admitted": false,
    }))
    .map_err(|_| invalid("Graph original index cannot be recomputed"))?;
    recomputed.push(b'\n');
    if recomputed != index_raw
        || observed_paths(output_root, &directory)? != indexed
        || checked_bytes(
            output_root,
            &index_path,
            &getter.sha256,
            getter.bytes,
            MAX_INDEX_BYTES,
        )? != index_raw
        || checked_bytes(
            output_root,
            &source_path,
            &source.sha256,
            source.bytes,
            MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
        )? != source_raw
        || current(service, &plan.execution_snapshot)? != before
    {
        return Err(stale());
    }
    Ok(json!({
        "schema": "motionwright.manim-graph-source-custody/1",
        "source_snapshot": plan.execution_snapshot,
        "graph_id": plan.source.id, "scene_id": plan.source.scene_id,
        "deliverable_id": plan.source.variant_profile_ref,
        "source": artifact["graph_source"], "getters": artifact["graph_getters"],
        "source_closure": package.closure,
        "readback": summary,
        "source_and_getter_bytes_verified": true,
        "native_video_verified": false, "native_reopen_verified": false,
        "profile_admitted": false, "imported_source_authority_used": false
    }))
}

#[cfg(test)]
pub(super) mod tests;
