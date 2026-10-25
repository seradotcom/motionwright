//! Real canonical Project fixtures; no native capability or result is mocked.
use motionwright_domain::*;
use motionwright_native::manim_graph_projection::{
    ManimSemanticGraphSceneOutputEligibility, build_manim_semantic_graph_source_plan,
    validate_manim_semantic_graph_scene_output,
};
use semwright_native_sdk::ErrorCode;
use uuid::Uuid;

#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod source;

fn prepared(flow: bool) -> (Project, Uuid, Uuid) {
    let mut project = source::seed_domain();
    let output = &mut project.deliverables[0];
    output.container = OutputContainer::Mp4;
    output.video_codec = VideoCodec::H264;
    output.color_space = OutputColorSpace::Rec709;
    output.burn_in_captions = false;
    let mut graph = source::graph_for(&project, flow);
    graph.time_range.start = RationalTime { num: 0, den: 1 };
    graph.time_range.duration = project.scenes[0].duration;
    let graph_id = graph.id;
    let profile_id = graph.variant_profile_ref;
    project
        .apply_change(&Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        })
        .unwrap();
    project.validate().unwrap();
    (project, graph_id, profile_id)
}

fn build(
    project: &Project,
    graph: Uuid,
    profile: Uuid,
) -> std::result::Result<
    motionwright_manim_profile::semantic_graph::ManimSemanticGraphPlan,
    semwright_native_sdk::Error,
> {
    build_manim_semantic_graph_source_plan(
        project,
        &GraphSourceSnapshot::from_project(project),
        graph,
        profile,
    )
}

#[test]
fn saved_canonical_source_reaches_the_existing_profile_unchanged() {
    for flow in [false, true] {
        let (project, graph_id, profile_id) = prepared(flow);
        let before = project.clone();
        let plan = build(&project, graph_id, profile_id).unwrap();
        assert_eq!(plan.source, project.production_design.semantic_graphs[0]);
        assert_eq!(
            plan.execution_snapshot,
            GraphSourceSnapshot::from_project(&project)
        );
        assert_eq!(plan.scene_clock.duration, project.scenes[0].duration);
        assert_eq!(project, before);
    }
}

#[test]
fn all_four_current_snapshot_dimensions_are_required_before_source_preparation() {
    let (project, graph_id, profile_id) = prepared(false);
    for dimension in 0..4 {
        let mut stale = GraphSourceSnapshot::from_project(&project);
        match dimension {
            0 => stale.project_id = Uuid::new_v4(),
            1 => stale.generation = Uuid::new_v4(),
            2 => stale.branch_id = Uuid::new_v4(),
            _ => stale.revision += 1,
        }
        let error = build_manim_semantic_graph_source_plan(&project, &stale, graph_id, profile_id)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
    }
}

#[test]
fn unrelated_canonical_edit_does_not_rewrite_historical_authorship() {
    let (mut project, graph_id, profile_id) = prepared(false);
    let original = project.production_design.semantic_graphs[0].clone();
    project
        .apply_change(&Change::AddClaim {
            text: "An unrelated source claim".into(),
            source: None,
            context: "Authored fixture".into(),
            source_revision: Some("v1".into()),
        })
        .unwrap();
    let plan = build(&project, graph_id, profile_id).unwrap();
    assert_eq!(plan.source, original);
    assert_eq!(
        plan.execution_snapshot,
        GraphSourceSnapshot::from_project(&project)
    );
}

#[test]
fn stale_bound_claim_is_rejected_even_when_the_current_snapshot_matches() {
    let mut project = source::seed_domain();
    project.deliverables[0].container = OutputContainer::Mp4;
    project.deliverables[0].video_codec = VideoCodec::H264;
    project.deliverables[0].color_space = OutputColorSpace::Rec709;
    project.deliverables[0].burn_in_captions = false;
    project
        .apply_change(&Change::AddClaim {
            text: "Bound claim".into(),
            source: None,
            context: "Authored scenario".into(),
            source_revision: Some("v1".into()),
        })
        .unwrap();
    let claim = project.brief.claims[0].clone();
    let mut graph = source::graph_for(&project, false);
    graph.time_range.duration = project.scenes[0].duration;
    graph.subject_ref = GraphSubjectRef::Claim {
        claim_id: claim.id,
        content_sha256: semantic_graph_claim_digest(&claim).unwrap(),
    };
    let graph_id = graph.id;
    let profile_id = graph.variant_profile_ref;
    project
        .apply_change(&Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        })
        .unwrap();
    build(&project, graph_id, profile_id).unwrap();
    let expected = ClaimEditableFields::from(&claim);
    let mut replacement = expected.clone();
    replacement.source_revision = Some("v2".into());
    project
        .apply_change(&Change::UpdateClaim {
            claim_id: claim.id,
            expected,
            replacement,
        })
        .unwrap();
    project.validate().unwrap();
    assert_eq!(
        build(&project, graph_id, profile_id).unwrap_err().code,
        ErrorCode::StaleReference
    );
    assert_eq!(project.production_design.semantic_graphs[0], graph);
}

#[test]
fn a_different_deliverable_cannot_reuse_the_saved_graph_identity() {
    let (project, graph_id, _) = prepared(false);
    assert_eq!(
        build(&project, graph_id, Uuid::new_v4()).unwrap_err().code,
        ErrorCode::Unsupported,
    );
}

#[test]
fn an_absent_graph_is_not_invented_from_scene_contents() {
    let (project, _, profile_id) = prepared(false);
    assert_eq!(
        build(&project, Uuid::new_v4(), profile_id)
            .unwrap_err()
            .code,
        ErrorCode::InvalidArgument,
    );
}

#[test]
fn fractional_native_fps_is_not_rounded() {
    let (mut project, graph_id, profile_id) = prepared(false);
    project.deliverables[0].frame_rate = RationalTime {
        num: 30_000,
        den: 1001,
    };
    assert!(build(&project, graph_id, profile_id).is_err());
}

#[test]
fn resizing_the_output_does_not_rescale_the_authored_reference_frame() {
    let (mut project, graph_id, profile_id) = prepared(false);
    project.deliverables[0].width += 2;
    let original = project.production_design.semantic_graphs[0].clone();
    assert!(build(&project, graph_id, profile_id).is_err());
    assert_eq!(project.production_design.semantic_graphs[0], original);
}

#[test]
fn caption_burn_in_is_not_silently_omitted() {
    let (mut project, graph_id, profile_id) = prepared(false);
    project.deliverables[0].burn_in_captions = true;
    assert!(build(&project, graph_id, profile_id).is_err());
}

#[test]
fn the_saved_source_edit_uses_the_existing_domain_change() {
    let (mut project, graph_id, profile_id) = prepared(true);
    let mut edited = project.production_design.semantic_graphs[0].clone();
    let GraphContent::SystemFlow { vertices, .. } = &mut edited.content else {
        unreachable!()
    };
    vertices[0].label = "Edited authored label\nwith an explicit newline".into();
    edited.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
    project
        .apply_change(&Change::UpsertSemanticGraph {
            graph: Box::new(edited.clone()),
        })
        .unwrap();
    assert_eq!(
        build(&project, graph_id, profile_id).unwrap().source,
        edited
    );
}

fn first_graph_project() -> Project {
    let mut project = source::seed_domain();
    let output = &mut project.deliverables[0];
    output.container = OutputContainer::Mp4;
    output.video_codec = VideoCodec::H264;
    output.color_space = OutputColorSpace::Rec709;
    output.burn_in_captions = false;
    project.validate().unwrap();
    assert!(project.production_design.semantic_graphs.is_empty());
    project
}

fn first_graph_eligibility(
    project: &Project,
) -> std::result::Result<ManimSemanticGraphSceneOutputEligibility, semwright_native_sdk::Error> {
    validate_manim_semantic_graph_scene_output(
        project,
        &GraphSourceSnapshot::from_project(project),
        project.scenes[0].id,
        project.deliverables[0].id,
    )
}

#[test]
fn first_graph_scene_output_is_pure_and_requires_no_saved_graph() {
    let project = first_graph_project();
    let before = project.clone();
    let selected = first_graph_eligibility(&project).unwrap();
    assert_eq!(
        selected.source_snapshot,
        GraphSourceSnapshot::from_project(&project)
    );
    assert_eq!(selected.scene_id, project.scenes[0].id);
    assert_eq!(selected.deliverable_id, project.deliverables[0].id);
    assert_eq!(selected.render_profile.width, project.deliverables[0].width);
    assert_eq!(
        selected.render_profile.height,
        project.deliverables[0].height
    );
    assert_eq!(selected.scene_clock.duration, project.scenes[0].duration);
    assert_eq!(
        build(&project, Uuid::new_v4(), project.deliverables[0].id)
            .unwrap_err()
            .code,
        ErrorCode::InvalidArgument,
    );
    assert!(project.production_design.semantic_graphs.is_empty());
    assert_eq!(project, before);
}

#[test]
fn first_graph_scene_output_matches_the_saved_projection_clock_and_profile() {
    for flow in [false, true] {
        let (project, graph_id, profile_id) = prepared(flow);
        let before = project.clone();
        let selected = first_graph_eligibility(&project).unwrap();
        let saved = build(&project, graph_id, profile_id).unwrap();
        assert_eq!(selected.render_profile, saved.render_profile);
        assert_eq!(
            serde_json::to_value(&selected.scene_clock).unwrap(),
            serde_json::to_value(&saved.scene_clock).unwrap(),
        );
        assert_eq!(selected.source_snapshot, saved.execution_snapshot);
        assert_eq!(project, before);
    }
}

#[test]
fn first_graph_scene_output_requires_all_four_current_snapshot_dimensions() {
    let project = first_graph_project();
    let before = project.clone();
    for dimension in 0..4 {
        let mut expected = GraphSourceSnapshot::from_project(&project);
        match dimension {
            0 => expected.project_id = Uuid::new_v4(),
            1 => expected.generation = Uuid::new_v4(),
            2 => expected.branch_id = Uuid::new_v4(),
            _ => expected.revision += 1,
        }
        let error = validate_manim_semantic_graph_scene_output(
            &project,
            &expected,
            project.scenes[0].id,
            project.deliverables[0].id,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
    }
    assert_eq!(project, before);
}

#[test]
fn first_graph_scene_output_rejects_absent_selection_without_mutation() {
    let project = first_graph_project();
    let before = project.clone();
    let expected = GraphSourceSnapshot::from_project(&project);
    for (scene_id, deliverable_id) in [
        (Uuid::new_v4(), project.deliverables[0].id),
        (project.scenes[0].id, Uuid::new_v4()),
    ] {
        let error = validate_manim_semantic_graph_scene_output(
            &project,
            &expected,
            scene_id,
            deliverable_id,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
    assert_eq!(project, before);
}

#[test]
fn first_graph_scene_output_rejects_other_renderers() {
    for renderer in [RendererKind::MotionCanvas, RendererKind::Blender] {
        let mut project = first_graph_project();
        project.scenes[0].renderer = renderer;
        project.validate().unwrap();
        let before = project.clone();
        assert_eq!(
            first_graph_eligibility(&project).unwrap_err().code,
            ErrorCode::Unsupported
        );
        assert_eq!(project, before);
    }
}

#[test]
fn first_graph_scene_output_preserves_explicit_scene_exclusion() {
    let mut project = first_graph_project();
    let selected_scene = project.scenes[0].id;
    project
        .apply_change(&Change::AddScene {
            name: "Other selected scene".into(),
            objective: "Keep the first scene explicitly excluded".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let other_scene = project
        .scenes
        .iter()
        .find(|scene| scene.id != selected_scene)
        .unwrap()
        .id;
    project.deliverables[0].included_scene_ids = vec![other_scene];
    project.validate().unwrap();
    let before = project.clone();
    let error = validate_manim_semantic_graph_scene_output(
        &project,
        &GraphSourceSnapshot::from_project(&project),
        selected_scene,
        project.deliverables[0].id,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    assert_eq!(project, before);
}

#[test]
fn first_graph_scene_output_does_not_discard_an_authored_camera() {
    let mut project = first_graph_project();
    project.scenes[0].camera.zoom = 2.0;
    project.validate().unwrap();
    let before = project.clone();
    assert_eq!(
        first_graph_eligibility(&project).unwrap_err().code,
        ErrorCode::Unsupported
    );
    assert_eq!(project, before);
}

#[test]
fn first_graph_scene_output_rejects_captions_and_fractional_native_fps() {
    for captions in [true, false] {
        let mut project = first_graph_project();
        if captions {
            project.deliverables[0].burn_in_captions = true;
        } else {
            project.deliverables[0].frame_rate = RationalTime {
                num: 30_000,
                den: 1001,
            };
        }
        project.validate().unwrap();
        let before = project.clone();
        assert_eq!(
            first_graph_eligibility(&project).unwrap_err().code,
            ErrorCode::Unsupported
        );
        assert_eq!(project, before);
    }
}

#[test]
fn first_graph_scene_output_rejects_unrepresented_native_formats() {
    for (container, codec, color) in [
        (
            OutputContainer::Mov,
            VideoCodec::H264,
            OutputColorSpace::Rec709,
        ),
        (
            OutputContainer::Mp4,
            VideoCodec::Hevc,
            OutputColorSpace::Rec709,
        ),
        (
            OutputContainer::Mp4,
            VideoCodec::H264,
            OutputColorSpace::DisplayP3,
        ),
    ] {
        let mut project = first_graph_project();
        project.deliverables[0].container = container;
        project.deliverables[0].video_codec = codec;
        project.deliverables[0].color_space = color;
        project.validate().unwrap();
        let before = project.clone();
        assert_eq!(
            first_graph_eligibility(&project).unwrap_err().code,
            ErrorCode::Unsupported
        );
        assert_eq!(project, before);
    }
}

#[test]
fn first_graph_scene_output_requires_an_exact_integral_frame_clock() {
    let mut project = first_graph_project();
    project.scenes[0].duration = RationalTime { num: 175, den: 29 };
    project.validate().unwrap();
    let before = project.clone();
    assert_eq!(
        first_graph_eligibility(&project).unwrap_err().code,
        ErrorCode::Unsupported
    );
    assert_eq!(project, before);
}

#[test]
fn first_graph_scene_output_respects_native_writer_dimension_and_area_bounds() {
    let mut at_boundary = first_graph_project();
    at_boundary.deliverables[0].width = 4096;
    at_boundary.deliverables[0].height = 2160;
    at_boundary.validate().unwrap();
    let before = at_boundary.clone();
    let selected = first_graph_eligibility(&at_boundary).unwrap();
    assert_eq!(
        u64::from(selected.render_profile.width) * u64::from(selected.render_profile.height),
        8_847_360
    );
    assert_eq!(at_boundary, before);
    for (width, height) in [
        (1081, 1080),
        (1080, 1081),
        (4098, 1080),
        (1080, 4098),
        (4096, 2162),
    ] {
        let mut project = first_graph_project();
        project.deliverables[0].width = width;
        project.deliverables[0].height = height;
        project.validate().unwrap();
        let before = project.clone();
        assert_eq!(
            first_graph_eligibility(&project).unwrap_err().code,
            ErrorCode::Unsupported,
            "{width}x{height}"
        );
        assert_eq!(project, before);
    }
}
