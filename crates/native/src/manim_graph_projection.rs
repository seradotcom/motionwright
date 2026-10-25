//! Saved canonical Graph source on the existing Project/StudioService boundary.
//! This is a source-only adapter. Native dispatch and Graph write admission stay
//! closed until the existing Manim plugin composes typography and real evidence.
use motionwright_domain::{
    CameraState, DeliverableProfile, GraphSourceSnapshot, OutputColorSpace, OutputContainer,
    Project, RendererKind, Scene, VideoCodec,
};
use motionwright_manim_profile::{
    ManimFrameClockV2, ManimMatteKindV2, ManimMatteV2, ManimRenderProfile,
    semantic_graph::ManimSemanticGraphPlan,
};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use uuid::Uuid;

fn unsupported(message: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

fn invalid(message: &'static str) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn stale() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Semantic Graph source requires the selected current Project snapshot",
    )
}

fn current(service: &StudioService, expected: &GraphSourceSnapshot) -> NativeResult<Project> {
    let project = service
        .project(expected.project_id)
        .map_err(crate::storage_error)?;
    expected.require_current(&project).map_err(|_| stale())?;
    Ok(project)
}

/// Pure semantic selection for authoring a Graph in a saved scene.
///
/// This contains no execution authority or runtime availability claim. The
/// eventual Graph still requires its own dependency, reference-frame, interval,
/// matte, typography and source-program validation.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ManimSemanticGraphSceneOutputEligibility {
    pub source_snapshot: GraphSourceSnapshot,
    pub scene_id: Uuid,
    pub deliverable_id: Uuid,
    pub render_profile: ManimRenderProfile,
    pub scene_clock: ManimFrameClockV2,
}

fn selected_graph_scene_output(
    project: &Project,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<(&Scene, &DeliverableProfile)> {
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .ok_or_else(|| invalid("Graph scene is absent"))?;
    let output = project
        .deliverables
        .iter()
        .find(|output| output.id == deliverable_id)
        .ok_or_else(|| invalid("Graph deliverable is absent"))?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err(unsupported(
            "Semantic Graph requires the existing ManimCommunity renderer",
        ));
    }
    if !output.included_scene_ids.is_empty() && !output.included_scene_ids.contains(&scene.id) {
        return Err(unsupported("Saved deliverable excludes the Graph scene"));
    }
    if scene.camera != CameraState::default() || !scene.nodes.is_empty() || !scene.beats.is_empty()
    {
        return Err(unsupported(
            "The Graph source profile cannot discard authored Canvas, Beats or camera content",
        ));
    }
    if !project.production_design.measured_bindings.is_empty()
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|row| row.scene_id == scene.id)
        || project
            .production_design
            .heroes
            .iter()
            .any(|row| row.scene_id == scene.id)
        || project
            .production_design
            .stages
            .iter()
            .any(|row| row.scene_id == scene.id)
        || project
            .production_design
            .procedural_fields
            .iter()
            .any(|row| row.scene_id == scene.id)
    {
        return Err(unsupported(
            "The Graph source profile cannot discard other authored native components",
        ));
    }
    let end = scene
        .start
        .checked_add(scene.duration)
        .map_err(|_| invalid("Graph scene clock overflow"))?;
    if !project.audio.sfx_cues.is_empty()
        || project
            .audio
            .cues
            .iter()
            .any(|cue| cue.at >= scene.start && cue.at < end)
    {
        return Err(unsupported(
            "This source profile does not realize authored SFX or caption cues",
        ));
    }
    if output.frame_rate.den != 1
        || output.container != OutputContainer::Mp4
        || output.video_codec != VideoCodec::H264
        || output.color_space != OutputColorSpace::Rec709
        || output.burn_in_captions
    {
        return Err(unsupported(
            "Graph video contribution requires integral fps, Rec.709 H264 MP4 and no burned captions",
        ));
    }
    Ok((scene, output))
}

fn graph_scene_clock_and_profile(
    scene: &Scene,
    output: &DeliverableProfile,
) -> NativeResult<(ManimRenderProfile, ManimFrameClockV2)> {
    let render_profile = ManimRenderProfile {
        width: output.width,
        height: output.height,
        frame_rate: u32::try_from(output.frame_rate.num)
            .map_err(|_| unsupported("Graph fps is outside native bounds"))?,
    };
    render_profile
        .validate()
        .map_err(|_| unsupported("Graph output is outside the existing native pixel/fps bounds"))?;
    let scene_clock =
        ManimFrameClockV2::from_canonical(scene.start, scene.duration, output.frame_rate).map_err(
            |_| {
                unsupported("Graph scene clock requires exact integral frames within native bounds")
            },
        )?;
    Ok((render_profile, scene_clock))
}

/// Validate a saved scene/output before a first Graph has been authored.
///
/// This reads only the supplied current Project. It does not create a Graph,
/// mutate the Project, inspect fonts, admit a provider, or grant execution.
/// A source-specific check is still required for each proposed or saved Graph.
pub fn validate_manim_semantic_graph_scene_output(
    project: &Project,
    expected: &GraphSourceSnapshot,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimSemanticGraphSceneOutputEligibility> {
    expected.require_current(project).map_err(|_| stale())?;
    project
        .validate()
        .map_err(|_| invalid("Canonical Graph Project is invalid"))?;
    let (scene, output) = selected_graph_scene_output(project, scene_id, deliverable_id)?;
    let (render_profile, scene_clock) = graph_scene_clock_and_profile(scene, output)?;

    // These are the existing Native5 writer limits. First-authoring selection
    // must not offer a frame that the current Driver refuses before rendering.
    // Saved source projection retains its separate source-only contract.
    if render_profile.width % 2 != 0
        || render_profile.height % 2 != 0
        || render_profile.width > 4096
        || render_profile.height > 4096
        || u64::from(render_profile.width) * u64::from(render_profile.height) > 8_847_360
    {
        return Err(unsupported(
            "Graph reference frame is outside the original Native5 writer profile",
        ));
    }

    Ok(ManimSemanticGraphSceneOutputEligibility {
        source_snapshot: GraphSourceSnapshot::from_project(project),
        scene_id: scene.id,
        deliverable_id: output.id,
        render_profile,
        scene_clock,
    })
}

pub fn build_manim_semantic_graph_source_plan(
    project: &Project,
    expected: &GraphSourceSnapshot,
    graph_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimSemanticGraphPlan> {
    expected.require_current(project).map_err(|_| stale())?;
    project
        .validate()
        .map_err(|_| invalid("Canonical Graph Project is invalid"))?;
    let source = project
        .production_design
        .semantic_graphs
        .iter()
        .find(|graph| graph.id == graph_id)
        .ok_or_else(|| invalid("Saved semantic Graph is absent"))?;
    if source.variant_profile_ref != deliverable_id {
        return Err(unsupported(
            "Graph source belongs to another saved deliverable",
        ));
    }
    let (scene, output) = selected_graph_scene_output(project, source.scene_id, deliverable_id)?;
    // This is use-time validation, not validate_semantic_graph_for_write:
    // historical authorship must remain unchanged when a saved source is reused.
    project
        .validate_semantic_graph_dependencies(source)
        .map_err(|_| stale())?;
    let (render_profile, scene_clock) = graph_scene_clock_and_profile(scene, output)?;
    let covers_scene =
        source.time_range.start.num == 0 && source.time_range.duration == scene.duration;
    let background = if covers_scene {
        source.style.background.clone()
    } else {
        project
            .visual_language
            .palette
            .iter()
            .find(|entry| entry.name == "background")
            .or_else(|| {
                project
                    .visual_language
                    .palette
                    .iter()
                    .find(|entry| entry.name == "surface")
            })
            .map(|entry| entry.value.clone())
            .ok_or_else(|| {
                unsupported("A partial Graph interval requires an explicit authored scene matte")
            })?
    };
    ManimSemanticGraphPlan::new(
        GraphSourceSnapshot::from_project(project),
        scene_clock,
        render_profile,
        ManimMatteV2 {
            kind: ManimMatteKindV2::Solid,
            color: background,
            opacity: 1,
        },
        source.clone(),
    )
    .map_err(|_| unsupported("Canonical Graph exceeds the closed source projection"))
}

/// A read/prepare/read returns editable source only. It neither calls a runtime
/// nor changes service write admission, CAS records or production receipts.
pub fn prepare_saved_manim_semantic_graph_source(
    service: &StudioService,
    expected: &GraphSourceSnapshot,
    graph_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<Vec<u8>> {
    let before = current(service, expected)?;
    let plan = build_manim_semantic_graph_source_plan(&before, expected, graph_id, deliverable_id)?;
    let bytes = plan
        .source_document()
        .map_err(|_| unsupported("Graph source exceeds the existing document budget"))?;
    if current(service, expected)? != before {
        return Err(stale());
    }
    Ok(bytes)
}

// The carrier is the existing output-mount descriptor. This alias adds no path
// root, permission, Graph DTO, or native execution authority.
pub use crate::manim_editable_projection::OriginalEditableBundleInput as OriginalGraphBundleInput;

type GraphPackage = motionwright_manim_profile::semantic_graph_source::ManimGraphSourcePackageV1;

pub(crate) fn validate_original_manim_graph_input(
    input: &OriginalGraphBundleInput,
) -> NativeResult<()> {
    let name = input.relative_path.as_str();
    if name.len() < 13
        || name.len() > 128
        || !name.as_bytes()[0].is_ascii_alphanumeric()
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        || !name.ends_with(".mwgraph.zip")
        || input.sha256.len() != 64
        || !input
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || input.bytes == 0
        || input.bytes > motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2 as u64
    {
        return Err(invalid(
            "Original Graph input is outside the existing output-mount carrier bounds",
        ));
    }
    Ok(())
}

fn graph_original_bundle(
    output_root: &std::path::Path,
    input: &OriginalGraphBundleInput,
) -> NativeResult<GraphPackage> {
    validate_original_manim_graph_input(input)?;
    let bytes = crate::production::read_verified_production_artifact(
        output_root,
        &input.relative_path,
        &input.sha256,
        motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
    )?;
    if bytes.len() as u64 != input.bytes {
        return Err(invalid(
            "Original Graph input size changed before bounded inspection",
        ));
    }
    motionwright_manim_profile::semantic_graph_source::inspect_graph_source_bundle_v1(&bytes)
        .map_err(|_| unsupported("Original Graph input is not a closed canonical source bundle"))
}

/// Reopen the exact exported bytes at the exact current canonical snapshot.
/// This does not rebind an old ZIP, import Python, stage alternate fonts, or
/// admit a provider. The existing coordinator still owns the native dispatch.
/// The caller must supply its existing authorized output mount. This reader
/// creates no path authorization or grant.
pub fn prepare_saved_manim_semantic_graph_bundle_input(
    service: &StudioService,
    expected: &GraphSourceSnapshot,
    graph_id: Uuid,
    deliverable_id: Uuid,
    output_root: &std::path::Path,
    input: &OriginalGraphBundleInput,
) -> NativeResult<GraphPackage> {
    let before = current(service, expected)?;
    let package = graph_original_bundle(output_root, input)?;
    let plan = build_manim_semantic_graph_source_plan(&before, expected, graph_id, deliverable_id)?;
    if package.plan != plan {
        return Err(unsupported(
            "Original Graph bundle differs from the complete current saved plan",
        ));
    }
    if current(service, expected)? != before {
        return Err(stale());
    }
    Ok(package)
}

/// A post-render failure retains the original native outcome. The caller must
/// not translate a failed custody check into permission for another native job.
/// This supplements production::manim_graph::verify_rendered_source; it must
/// never replace that verifier's digest-checked read of the exported ZIP.
pub(crate) fn verify_rendered_manim_semantic_graph_bundle_input(
    output_root: &std::path::Path,
    plan: &ManimSemanticGraphPlan,
    input: &OriginalGraphBundleInput,
    artifact: &serde_json::Value,
) -> NativeResult<serde_json::Value> {
    let package = graph_original_bundle(output_root, input)?;
    if package.plan != *plan {
        return Err(invalid(
            "Original Graph input plan changed during the native job",
        ));
    }
    let observed = serde_json::json!({
        "input": input,
        "source_sha256": package.closure.source_sha256,
        "plan_sha256": package.closure.plan_sha256,
        "closure": package.closure,
    });
    if artifact.get("graph_input_source") != Some(&observed)
        || artifact.pointer("/graph_source/sha256") != Some(&serde_json::json!(input.sha256))
        || artifact.pointer("/graph_source/bytes") != Some(&serde_json::json!(input.bytes))
        || artifact.pointer("/graph_source/closure") != Some(&serde_json::json!(package.closure))
    {
        return Err(invalid(
            "Native Graph input or exported original source differs",
        ));
    }
    Ok(observed)
}

fn graph_package_assets(
    package: &GraphPackage,
) -> NativeResult<std::collections::BTreeMap<String, Vec<u8>>> {
    motionwright_manim_profile::semantic_graph_source::graph_builtin_asset_pins()
        .iter()
        .map(|pin| {
            let path = format!("graph-profile-v1/fonts/{}", pin.relative_path);
            let bytes = package
                .entries
                .get(&path)
                .ok_or_else(|| invalid("Inspected Graph source has no required bundled asset"))?;
            Ok((pin.relative_path.to_owned(), bytes.clone()))
        })
        .collect()
}

fn read_attached_graph_source(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
) -> NativeResult<(String, GraphPackage)> {
    use sha2::{Digest, Sha256};
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .ok_or_else(|| invalid("Graph source is not attached to the selected Project"))?;
    if asset.media_type != motionwright_manim_profile::semantic_graph_source::GRAPH_SOURCE_MIME_V1 {
        return Err(unsupported(
            "Attached Graph source has an unsupported media type",
        ));
    }
    let digest = asset
        .content_sha256
        .as_ref()
        .ok_or_else(|| invalid("Attached Graph source has no CAS digest"))?;
    let bytes = {
        let handle = service.store();
        let store = handle.lock();
        if store
            .load_project(project.id)
            .map_err(crate::storage_error)?
            != *project
        {
            return Err(stale());
        }
        store
            .read_blob(
                digest,
                motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
            )
            .map_err(crate::storage_error)?
    };
    // read_blob returns owned bytes. No Store mutex or temporary-path lease is
    // held while any later provider job runs.
    if hex::encode(Sha256::digest(&bytes)) != *digest {
        return Err(invalid("Attached Graph source differs from its CAS digest"));
    }
    let package =
        motionwright_manim_profile::semantic_graph_source::inspect_graph_source_bundle_v1(&bytes)
            .map_err(|_| {
            unsupported("Attached Graph source differs from its complete closed compiler")
        })?;
    if package.plan.execution_snapshot.project_id != project.id {
        return Err(unsupported(
            "Attached Graph source belongs to another Project",
        ));
    }
    Ok((digest.clone(), package))
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct InspectedSemanticGraphSource {
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub origin_execution_snapshot: GraphSourceSnapshot,
    pub current_execution_snapshot: GraphSourceSnapshot,
    pub original_closure: motionwright_manim_profile::ManimSourceClosureV1,
    pub current_closure: motionwright_manim_profile::ManimSourceClosureV1,
    pub current_plan: ManimSemanticGraphPlan,
    pub execution_snapshot_rebound: bool,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
}

/// Inspect one immutable CAS source against current saved controls. Only the
/// execution snapshot can be rebound; historical authorship remains unchanged.
pub fn inspect_saved_manim_semantic_graph_source(
    service: &StudioService,
    expected: &GraphSourceSnapshot,
    asset_id: Uuid,
) -> NativeResult<InspectedSemanticGraphSource> {
    let before = current(service, expected)?;
    let (digest, origin) = read_attached_graph_source(service, &before, asset_id)?;
    let plan = build_manim_semantic_graph_source_plan(
        &before,
        expected,
        origin.plan.source.id,
        origin.plan.source.variant_profile_ref,
    )?;
    let rebound = motionwright_manim_profile::semantic_graph_source::rebind_graph_source_v1(
        &origin.plan,
        &plan,
        &graph_package_assets(&origin)?,
    )
    .map_err(|_| unsupported("Imported Graph source no longer describes current saved controls"))?;
    if current(service, expected)? != before {
        return Err(stale());
    }
    Ok(InspectedSemanticGraphSource {
        source_asset_id: asset_id,
        source_sha256: digest,
        origin_execution_snapshot: origin.plan.execution_snapshot.clone(),
        current_execution_snapshot: plan.execution_snapshot.clone(),
        execution_snapshot_rebound: origin.plan.execution_snapshot != plan.execution_snapshot,
        original_closure: origin.closure,
        current_closure: rebound.closure,
        current_plan: plan,
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

fn vertex_label_mut(
    graph: &mut motionwright_domain::SemanticGraph,
    vertex_id: Uuid,
) -> NativeResult<&mut String> {
    let vertices = match &mut graph.content {
        motionwright_domain::GraphContent::CausalDiagram { vertices, .. }
        | motionwright_domain::GraphContent::SystemFlow { vertices, .. } => vertices,
    };
    vertices
        .iter_mut()
        .find(|vertex| vertex.id == vertex_id)
        .map(|vertex| &mut vertex.label)
        .ok_or_else(|| invalid("Selected Graph vertex is absent"))
}

fn preview_graph_label_change(
    project: &Project,
    expected: &GraphSourceSnapshot,
    origin: &GraphPackage,
    edited: &GraphPackage,
    vertex_id: Uuid,
) -> NativeResult<motionwright_domain::Change> {
    expected.require_current(project).map_err(|_| stale())?;
    let current_plan = build_manim_semantic_graph_source_plan(
        project,
        expected,
        origin.plan.source.id,
        origin.plan.source.variant_profile_ref,
    )?;
    motionwright_manim_profile::semantic_graph_source::rebind_graph_source_v1(
        &origin.plan,
        &current_plan,
        &graph_package_assets(origin)?,
    )
    .map_err(|_| unsupported("Original imported Graph differs from the current saved controls"))?;

    // Both packages have already passed the closed ZIP inspector. Permit one
    // explicit label edit; reject changes to bindings, style, geometry, timing,
    // helper bytes, fonts, historical authorship or any other editable source.
    let selected = edited
        .plan
        .source
        .content
        .vertices()
        .iter()
        .find(|vertex| vertex.id == vertex_id)
        .ok_or_else(|| invalid("Edited Graph vertex is absent"))?;
    let mut exact_edit = origin.plan.clone();
    let previous = vertex_label_mut(&mut exact_edit.source, vertex_id)?;
    if *previous == selected.label {
        return Err(invalid("Graph source import is a no-op"));
    }
    *previous = selected.label.clone();
    if exact_edit != edited.plan {
        return Err(unsupported(
            "Graph import changes fields outside the selected vertex label",
        ));
    }

    let mut graph = current_plan.source;
    *vertex_label_mut(&mut graph, vertex_id)? = selected.label.clone();
    // CAS imports have already changed the saved revision. Use the real current
    // stamp here, never a future revision or the old stamp supplied in a ZIP.
    graph.authorship.source_snapshot = expected.clone();
    let change = motionwright_domain::Change::UpsertSemanticGraph {
        graph: Box::new(graph),
    };
    let mut candidate = project.clone();
    candidate.apply_change(&change).map_err(|_| {
        unsupported("Graph label edit violates the canonical domain or a saved lock")
    })?;
    let candidate_stamp = GraphSourceSnapshot::from_project(&candidate);
    build_manim_semantic_graph_source_plan(
        &candidate,
        &candidate_stamp,
        origin.plan.source.id,
        origin.plan.source.variant_profile_ref,
    )?;
    Ok(change)
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct PreviewedSemanticGraphLabelEdit {
    pub expected: GraphSourceSnapshot,
    pub original_source_asset_id: Uuid,
    pub edited_source_asset_id: Uuid,
    pub original_source_sha256: String,
    pub edited_source_sha256: String,
    pub vertex_id: Uuid,
    pub historical_authorship_snapshot: GraphSourceSnapshot,
    pub proposed_authorship_snapshot: GraphSourceSnapshot,
    pub change: motionwright_domain::Change,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
    pub service_write_admission_granted: bool,
}

/// Produce one existing canonical Change for review. StudioService::apply
/// remains the sole mutation boundary and may still refuse Graph admission.
pub fn preview_saved_manim_semantic_graph_label_edit(
    service: &StudioService,
    expected: &GraphSourceSnapshot,
    original_asset_id: Uuid,
    edited_asset_id: Uuid,
    vertex_id: Uuid,
) -> NativeResult<PreviewedSemanticGraphLabelEdit> {
    if original_asset_id == edited_asset_id {
        return Err(invalid(
            "Edited Graph source must be a separately attached Asset",
        ));
    }
    let before = current(service, expected)?;
    let (original_digest, original) =
        read_attached_graph_source(service, &before, original_asset_id)?;
    let (edited_digest, edited) = read_attached_graph_source(service, &before, edited_asset_id)?;
    let change = preview_graph_label_change(&before, expected, &original, &edited, vertex_id)?;
    if current(service, expected)? != before {
        return Err(stale());
    }
    Ok(PreviewedSemanticGraphLabelEdit {
        expected: expected.clone(),
        original_source_asset_id: original_asset_id,
        edited_source_asset_id: edited_asset_id,
        original_source_sha256: original_digest,
        edited_source_sha256: edited_digest,
        vertex_id,
        historical_authorship_snapshot: original.plan.source.authorship.source_snapshot.clone(),
        proposed_authorship_snapshot: expected.clone(),
        change,
        native_execution_performed: false,
        imported_source_authority_used: false,
        service_write_admission_granted: false,
    })
}

/// Export the current canonical Graph using only the fixed original font and
/// license bytes from an inspected, attached source. The supplied Asset grants
/// no semantic authority: all content and the new execution stamp come from
/// the saved Project. After an edit this is a NEW package, not an old ZIP rebound.
pub fn export_saved_manim_semantic_graph_source_bundle(
    service: &StudioService,
    expected: &GraphSourceSnapshot,
    graph_id: Uuid,
    deliverable_id: Uuid,
    source_assets_id: Uuid,
) -> NativeResult<Vec<u8>> {
    let before = current(service, expected)?;
    let (_, source) = read_attached_graph_source(service, &before, source_assets_id)?;
    if source.plan.source.id != graph_id || source.plan.source.variant_profile_ref != deliverable_id
    {
        return Err(unsupported(
            "Graph export asset belongs to another selected Graph or deliverable",
        ));
    }
    let plan = build_manim_semantic_graph_source_plan(&before, expected, graph_id, deliverable_id)?;
    if source.plan.source.scene_id != plan.source.scene_id {
        return Err(unsupported(
            "Graph export asset belongs to another selected scene",
        ));
    }
    let bytes = motionwright_manim_profile::semantic_graph_source::graph_source_bundle_v1(
        &plan,
        &graph_package_assets(&source)?,
    )
    .map_err(|_| {
        unsupported("Current Graph cannot be exported within the closed source profile")
    })?;
    if current(service, expected)? != before {
        return Err(stale());
    }
    Ok(bytes)
}

#[cfg(test)]
#[path = "manim_graph_source_cycle_tests.rs"]
mod source_cycle_tests;
