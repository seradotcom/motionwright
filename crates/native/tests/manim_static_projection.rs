//! Source/runtime-contract tests; physical renders remain a distinct G2 gate.
use motionwright_domain::{
    BlendMode, CanvasKeyframe, CanvasNode, Change, CoordinateSpace, MotionInterpolation,
    MotionProperty, NodeStyle, Project, RationalTime, RendererKind, RevisionStamp, VisualToken,
};
use motionwright_manim_profile::{
    SOURCE_BUNDLE_MIME_V2, inspect_source_bundle_v2, source_bundle_v2,
};
use motionwright_native::manim_static_projection::{
    build_manim_static_plan_v2, export_saved_manim_static_source_bundle,
    inspect_saved_manim_static_source, prepare_saved_manim_static_source,
    preview_saved_manim_static_transform_edit,
};
use motionwright_service::StudioService;
use semwright_native_sdk::ErrorCode;
use std::collections::BTreeSet;
use uuid::Uuid;

fn apply(service: Option<&StudioService>, project: &mut Project, change: Change) {
    if let Some(service) = service {
        *project = service
            .apply(
                project.id,
                &RevisionStamp::from(&*project),
                &format!("static-fixture-{}", Uuid::now_v7()),
                &change,
            )
            .unwrap()
            .project;
    } else {
        project.apply_change(&change).unwrap();
    }
}
fn source(service: Option<&StudioService>) -> Project {
    let mut p = if let Some(service) = service {
        service.create_project("Exact native static clock").unwrap()
    } else {
        Project::new("Exact native static clock").unwrap()
    };
    apply(
        service,
        &mut p,
        Change::AddScene {
            name: "Prior canonical interval".into(),
            objective: "Preserve global offset".into(),
            duration_seconds: 1,
        },
    );
    apply(
        service,
        &mut p,
        Change::AddScene {
            name: "Static editable shapes".into(),
            objective: "Keep clock, order and geometry".into(),
            duration_seconds: 4,
        },
    );
    let scene_id = p.scenes[1].id;
    apply(
        service,
        &mut p,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    );
    for (index, kind, x, y, w, h, z, fill) in [
        (0, "rectangle", 420.0, 337.5, 270.0, 135.0, 12, "#86C8D1"),
        (1, "circle", 1050.0, 420.0, 180.0, 180.0, -5, "#E53935"),
        (2, "shape", 810.0, 620.0, 240.0, 90.0, 12, "#E8BC55"),
    ] {
        apply(
            service,
            &mut p,
            Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id: Uuid::now_v7(),
                    name: format!("Editable source {index}"),
                    kind: kind.into(),
                    parent_id: None,
                    x,
                    y,
                    width: w,
                    height: h,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                    text: None,
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: z,
                    style: NodeStyle {
                        fill: Some(fill.into()),
                        ..Default::default()
                    },
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                },
            },
        );
    }
    let mut language = p.visual_language.clone();
    language.palette.retain(|row| row.name != "background");
    language.palette.push(VisualToken {
        name: "background".into(),
        value: "#000000".into(),
    });
    apply(
        service,
        &mut p,
        Change::SetVisualLanguage {
            visual_language: language,
        },
    );
    let mut output = p.deliverables[0].clone();
    output.width = 640;
    output.height = 360;
    output.frame_rate = RationalTime::new(30, 1).unwrap();
    output.captions = false;
    output.burn_in_captions = false;
    apply(
        service,
        &mut p,
        Change::UpsertDeliverable { profile: output },
    );
    p.validate().unwrap();
    p
}
fn plan(p: &Project) -> motionwright_manim_profile::ManimScenePlanV2 {
    build_manim_static_plan_v2(
        p,
        &RevisionStamp::from(p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap()
}

#[test]
fn static_projection_preserves_offset_duration_full_ids_geometry_and_stacking() {
    let p = source(None);
    let before = p.clone();
    let plan = plan(&p);
    assert_eq!(plan.clock.project_start_frame, 30);
    assert_eq!(plan.clock.project_end_frame_exclusive, 150);
    assert_eq!(plan.clock.frame_count, 120);
    assert_eq!(
        plan.nodes.iter().map(|row| row.z_index).collect::<Vec<_>>(),
        vec![12, -5, 12]
    );
    assert_eq!(
        plan.nodes
            .iter()
            .map(|row| row.canonical_ordinal)
            .collect::<Vec<_>>(),
        vec![0, 1, 2]
    );
    for (projected, original) in plan.nodes.iter().zip(&p.scenes[1].nodes) {
        assert_eq!(projected.node_id, original.id);
        for (actual, expected) in [
            (projected.x_px, original.x),
            (projected.y_px, original.y),
            (projected.width_px, original.width),
            (projected.height_px, original.height),
        ] {
            assert_eq!(actual.to_bits(), expected.to_bits());
        }
    }
    let bytes = source_bundle_v2(&plan).unwrap();
    assert_eq!(inspect_source_bundle_v2(&bytes).unwrap().plan, plan);
    assert_eq!(p, before);
}

#[test]
fn static_projection_refuses_every_unrepresented_property_without_rewriting_source() {
    let p = source(None);
    for property in [
        "opacity",
        "rotation",
        "font",
        "font_size",
        "font_weight",
        "line_height",
        "stroke",
        "blend_multiply",
        "blend_screen",
        "blend_add",
        "text",
        "animation",
        "parent",
        "camera_pan",
        "camera_zoom",
        "camera_rotation",
        "camera_margin",
        "clock_fraction",
        "non_square_circle",
    ] {
        let mut altered = p.clone();
        let parent = altered.scenes[1].nodes[1].id;
        let n = &mut altered.scenes[1].nodes[0];
        match property {
            "opacity" => n.opacity = 0.75,
            "rotation" => n.rotation_deg = 12.0,
            "font" => n.style.font_family = Some("Author font".into()),
            "font_size" => n.style.font_size = Some(48.0),
            "font_weight" => n.style.font_weight = Some(600),
            "line_height" => n.style.line_height = Some(1.2),
            "stroke" => {
                n.style.stroke = Some("#FFFFFF".into());
                n.style.stroke_width = 2.0;
            }
            "blend_multiply" => n.style.blend_mode = BlendMode::Multiply,
            "blend_screen" => n.style.blend_mode = BlendMode::Screen,
            "blend_add" => n.style.blend_mode = BlendMode::Add,
            "text" => {
                n.kind = "text".into();
                n.text = Some("Keep authored text".into());
            }
            "animation" => n.keyframes.push(CanvasKeyframe {
                at: RationalTime::new(1, 1).unwrap(),
                property: MotionProperty::X,
                value: 500.0,
                interpolation: MotionInterpolation::Linear,
            }),
            "parent" => n.parent_id = Some(parent),
            "camera_pan" => altered.scenes[1].camera.center_x = 1000.0,
            "camera_zoom" => altered.scenes[1].camera.zoom = 1.25,
            "camera_rotation" => altered.scenes[1].camera.rotation_deg = 10.0,
            "camera_margin" => altered.scenes[1].camera.safe_margin = 0.1,
            "clock_fraction" => altered.scenes[1].duration = RationalTime::new(121, 31).unwrap(),
            "non_square_circle" => altered.scenes[1].nodes[1].height = 181.0,
            _ => unreachable!(),
        }
        altered.validate().unwrap();
        let before = altered.clone();
        let error = build_manim_static_plan_v2(
            &altered,
            &RevisionStamp::from(&altered),
            altered.scenes[1].id,
            altered.deliverables[0].id,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported, "{property}");
        assert_eq!(altered, before, "{property}");
    }
}

fn attach(
    service: &StudioService,
    directory: &std::path::Path,
    p: &Project,
    name: &str,
    bytes: &[u8],
) -> (Project, Uuid) {
    let path = directory.join(format!("{name}.mwstatic.zip"));
    std::fs::write(&path, bytes).unwrap();
    let attached = service
        .import_asset_file(
            p.id,
            &RevisionStamp::from(p),
            name,
            &path,
            name.into(),
            SOURCE_BUNDLE_MIME_V2.into(),
        )
        .unwrap()
        .project;
    let id = attached
        .assets
        .iter()
        .find(|row| row.name == name)
        .unwrap()
        .id;
    std::fs::remove_file(path).unwrap();
    (attached, id)
}

#[test]
fn static_saved_package_import_edit_once_and_reopen_preserve_canonical_source() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("source.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let p = source(Some(&service));
    let identity = p.id;
    let original = export_saved_manim_static_source_bundle(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let (p, original_asset) = attach(&service, temp.path(), &p, "original", &original);
    let inspected =
        inspect_saved_manim_static_source(&service, p.id, &RevisionStamp::from(&p), original_asset)
            .unwrap();
    assert!(inspected.revision_only_rebound);
    assert!(!inspected.native_execution_performed);
    assert!(!inspected.imported_source_authority_used);
    let mut edited = inspect_source_bundle_v2(&original).unwrap().plan;
    let node_id = edited.nodes[0].node_id;
    edited.nodes[0].x_px += 45.0;
    edited.nodes[0].height_px += 27.0;
    let (p, edited_asset) = attach(
        &service,
        temp.path(),
        &p,
        "edited",
        &source_bundle_v2(&edited).unwrap(),
    );
    let stamp = RevisionStamp::from(&p);
    let preview = preview_saved_manim_static_transform_edit(
        &service,
        p.id,
        &stamp,
        original_asset,
        edited_asset,
        node_id,
    )
    .unwrap();
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(!preview.native_execution_performed);
    assert!(!preview.imported_source_authority_used);
    let committed = service
        .apply(p.id, &stamp, "static-one-transform", &preview.change)
        .unwrap()
        .project;
    assert_eq!(committed.revision, p.revision + 1);
    assert_eq!(committed.scenes[1].nodes[0].x, 465.0);
    assert_eq!(committed.scenes[1].nodes[0].height, 162.0);
    assert_eq!(committed.scenes[1].nodes[1..], p.scenes[1].nodes[1..]);
    assert_eq!(committed.scenes[1].duration, p.scenes[1].duration);
    assert_eq!(committed.scenes[1].camera, p.scenes[1].camera);
    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(identity).unwrap(), committed);
    let current = prepare_saved_manim_static_source(
        &reopened,
        identity,
        &RevisionStamp::from(&committed),
        committed.scenes[1].id,
        committed.deliverables[0].id,
    )
    .unwrap();
    assert_eq!(current.plan.nodes[0].node_id, node_id);
    assert_eq!(current.plan.nodes[0].x_px, 465.0);
    assert_eq!(current.plan.clock.frame_count, 120);
    let recovered = inspect_saved_manim_static_source(
        &reopened,
        identity,
        &RevisionStamp::from(&committed),
        edited_asset,
    )
    .unwrap();
    assert_eq!(recovered.current_plan, current.plan);
    assert!(
        reopened
            .production_receipts(identity, 100)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        prepare_saved_manim_static_source(
            &reopened,
            identity,
            &stamp,
            committed.scenes[1].id,
            committed.deliverables[0].id
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
}

#[test]
fn static_import_refuses_extra_edit_without_applying_any_change() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source(Some(&service));
    let original = source_bundle_v2(&plan(&p)).unwrap();
    let (p, source_asset) = attach(&service, temp.path(), &p, "source", &original);
    let mut changed = inspect_source_bundle_v2(&original).unwrap().plan;
    changed.nodes[0].x_px += 45.0;
    changed.nodes[1].fill_hex = "#FFFFFF".into();
    let node_id = changed.nodes[0].node_id;
    let (p, edited_asset) = attach(
        &service,
        temp.path(),
        &p,
        "foreign-edit",
        &source_bundle_v2(&changed).unwrap(),
    );
    let error = preview_saved_manim_static_transform_edit(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        source_asset,
        edited_asset,
        node_id,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 100).unwrap().is_empty());
}

#[test]
fn normal_manim_profile_requires_one_saved_output_and_preserves_its_exact_clock() {
    use motionwright_manim_profile::ManimRenderProfile;
    use motionwright_native::manim_static_projection::select_saved_manim_deliverable_for_profile;
    let p = source(None);
    let before = p.clone();
    let scene = p.scenes[1].id;
    let profile = ManimRenderProfile {
        width: 640,
        height: 360,
        frame_rate: 30,
    };
    let selected =
        select_saved_manim_deliverable_for_profile(&p, &RevisionStamp::from(&p), scene, &profile)
            .unwrap();
    assert_eq!(selected, p.deliverables[0].id);
    let projected =
        build_manim_static_plan_v2(&p, &RevisionStamp::from(&p), scene, selected).unwrap();
    assert_eq!(projected.clock.project_start_frame, 30);
    assert_eq!(projected.clock.frame_count, 120);
    assert_eq!(p, before);
    let mismatch = ManimRenderProfile {
        width: 640,
        height: 360,
        frame_rate: 12,
    };
    assert_eq!(
        select_saved_manim_deliverable_for_profile(&p, &RevisionStamp::from(&p), scene, &mismatch)
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
    let mut ambiguous = p.clone();
    let mut duplicate = ambiguous.deliverables[0].clone();
    duplicate.id = Uuid::now_v7();
    duplicate.name = "Same dimensions, different saved output".into();
    ambiguous.deliverables.push(duplicate);
    ambiguous.validate().unwrap();
    assert_eq!(
        select_saved_manim_deliverable_for_profile(
            &ambiguous,
            &RevisionStamp::from(&ambiguous),
            scene,
            &profile
        )
        .unwrap_err()
        .code,
        ErrorCode::Unsupported
    );
    let mut stale = RevisionStamp::from(&p);
    stale.revision += 1;
    assert_eq!(
        select_saved_manim_deliverable_for_profile(&p, &stale, scene, &profile)
            .unwrap_err()
            .code,
        ErrorCode::StaleReference
    );
}
