//! Source/Store/CAS tests. No test here is evidence of Manim execution, decoded
//! frames, permission grants, audio mastering, or creative acceptance.
use motionwright_domain::{
    Asset, AudioCue, Beat, BlendMode, CameraReframe, CameraState, CanvasKeyframe, CanvasNode,
    Change, CoordinateSpace, CueEvidence, Fidelity, FidelityReport, MeasuredBinding,
    MeasuredOutput, MeasuredView, MotionInterpolation, MotionProperty, NativeCapsule, NodeProperty,
    NodeStyle, Project, RationalTime, RendererKind, RevisionStamp,
};
use motionwright_manim_profile::{
    ManimSourceShapeKindV2, SOURCE_BUNDLE_MIME_V2,
    planar_motion::{
        ManimMotionInterpolationV3, ManimMotionPropertyV3, ManimScenePlanV3,
        compile_manim_python_v3, inspect_motion_readback_v3, inspect_source_bundle_v3,
        motion_sampling_v3, projected_rectangle_vertices_v3, rebind_source_v3, sample_frame_v3,
        source_bundle_v3, source_package_v3, validate_plan_v3,
    },
};
use motionwright_native::manim_projection::{
    apply_saved_manim_motion_edit, build_manim_plan_v3, inspect_saved_manim_motion_source,
    prepare_saved_manim_motion_source, preview_saved_manim_motion_edit,
};
use motionwright_service::StudioService;
use semwright_native_sdk::ErrorCode;
use std::{collections::BTreeSet, fs, path::Path};
use uuid::Uuid;

const RED: Uuid = Uuid::from_u128(0x20000000000040008000000000000001);
const GREEN: Uuid = Uuid::from_u128(0x20000000000040008000000000000002);

fn changes(p: &Project) -> Vec<Change> {
    let scene_id = p.scenes[0].id;
    let mut profile = p.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    profile.frame_rate = RationalTime::new(12, 1).unwrap();
    let mut language = p.visual_language.clone();
    language
        .palette
        .iter_mut()
        .find(|x| x.name == "surface")
        .unwrap()
        .value = "#000000".into();
    let node = |id, kind: &str, x, y, w, h, color: &str, z| CanvasNode {
        id,
        name: format!("Original {kind}"),
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
            fill: Some(color.into()),
            ..NodeStyle::default()
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    };
    let mut out = vec![
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
        Change::UpsertDeliverable { profile },
        Change::SetVisualLanguage {
            visual_language: language,
        },
        // Inverse vector/z ordering is deliberate and survives source round-trip.
        Change::AddCanvasNode {
            scene_id,
            node: node(GREEN, "circle", 750.0, 390.0, 240.0, 240.0, "#20CC40", 20),
        },
        Change::AddCanvasNode {
            scene_id,
            node: node(RED, "rectangle", 240.0, 180.0, 600.0, 360.0, "#E02040", 10),
        },
    ];
    for (property, value, interpolation) in [
        (MotionProperty::X, 600.0, MotionInterpolation::Linear),
        (MotionProperty::Y, 300.0, MotionInterpolation::Hold),
        (
            MotionProperty::Width,
            840.0,
            MotionInterpolation::EaseOutCubic,
        ),
        (
            MotionProperty::Height,
            480.0,
            MotionInterpolation::EaseInOut,
        ),
        (
            MotionProperty::RotationDeg,
            90.0,
            MotionInterpolation::EaseInOut,
        ),
        (MotionProperty::Opacity, 0.25, MotionInterpolation::Linear),
    ] {
        out.push(Change::SetCanvasKeyframe {
            scene_id,
            node_id: RED,
            keyframe: CanvasKeyframe {
                at: RationalTime::new(1, 1).unwrap(),
                property,
                value,
                interpolation,
            },
        });
    }
    out
}

fn initial_scene() -> Change {
    Change::AddScene {
        name: "Original motion study".into(),
        objective: "Verify six controls, source and exact time".into(),
        duration_seconds: 2,
    }
}
fn project() -> Project {
    let mut p = Project::new("Original editable motion fixture").unwrap();
    p.apply_change(&initial_scene()).unwrap();
    for c in changes(&p) {
        p.apply_change(&c).unwrap();
    }
    p
}
fn plan(p: &Project) -> ManimScenePlanV3 {
    build_manim_plan_v3(
        p,
        &RevisionStamp::from(p),
        p.scenes[0].id,
        p.deliverables[0].id,
    )
    .unwrap()
}
fn apply(service: &StudioService, p: &Project, change: Change) -> Project {
    service
        .apply(
            p.id,
            &RevisionStamp::from(p),
            &format!("source-motion-change-{}", p.revision),
            &change,
        )
        .unwrap()
        .project
}
fn stored_project(service: &StudioService) -> Project {
    let p = service
        .create_project("Original editable motion fixture")
        .unwrap();
    let mut p = apply(service, &p, initial_scene());
    for c in changes(&p) {
        p = apply(service, &p, c);
    }
    p
}

fn attach(
    service: &StudioService,
    p: &Project,
    folder: &Path,
    name: &str,
    bytes: &[u8],
    capsule: bool,
) -> (Project, Uuid) {
    let path = folder.join(name);
    fs::write(&path, bytes).unwrap();
    let blob = service.ingest_blob_file(&path).unwrap();
    let asset = Asset {
        id: Uuid::now_v7(),
        name: name.into(),
        media_type: SOURCE_BUNDLE_MIME_V2.into(),
        content_sha256: Some(blob.sha256.clone()),
        source_revision: Some(p.revision.to_string()),
    };
    let id = asset.id;
    let mut p = apply(service, p, Change::AddAsset { asset });
    if capsule {
        let source = inspect_source_bundle_v3(bytes).unwrap();
        let capsule = NativeCapsule {
            id: Uuid::now_v7(),
            scene_id: source.plan.binding.scene_id,
            source_asset_id: id,
            source_sha256: blob.sha256,
            label: "Motion source awaiting native evidence".into(),
            fidelity: FidelityReport {
                renderer: "manim-community".into(),
                renderer_version: "0.21.0".into(),
                visual: Fidelity::Unavailable,
                temporal: Fidelity::Unavailable,
                structural: Fidelity::Translated,
                editable: Fidelity::Translated,
                losses: vec![
                    "Sampler, native reopen and decoded frames are not verified by source tests"
                        .into(),
                ],
                evidence_sha256: None,
            },
            editable_parameters: vec![
                "node.transform".into(),
                "node.keyframe".into(),
                "scene.camera.static".into(),
            ],
            native_editor_hint: "Edit plan data and regenerate trusted source".into(),
            property_fidelity: vec![],
        };
        p = apply(service, &p, Change::UpsertNativeCapsule { capsule });
    }
    (p, id)
}

fn pair(
    service: &StudioService,
    folder: &Path,
    edit: impl FnOnce(&mut ManimScenePlanV3),
) -> (Project, Uuid, Uuid) {
    let p = stored_project(service);
    let mut data = plan(&p);
    let original = source_bundle_v3(&data).unwrap();
    let (p, origin) = attach(service, &p, folder, "origin.mwmsrc.zip", &original, true);
    edit(&mut data);
    let bytes = source_bundle_v3(&data).unwrap();
    let (p, edited) = attach(service, &p, folder, "edited.mwmsrc.zip", &bytes, false);
    (p, origin, edited)
}

#[test]
fn all_six_channels_use_destination_easing_and_exact_arrival() {
    let p = project();
    let data = plan(&p);
    let before = p.clone();
    let poses = sample_frame_v3(&data, 6).unwrap();
    let m = poses[1];
    assert_eq!(m.x, 420.0);
    assert_eq!(m.y, 180.0);
    assert_eq!(m.width, 810.0);
    assert_eq!(m.height, 420.0);
    assert_eq!(m.rotation_deg, 45.0);
    assert_eq!(m.opacity, 0.625);
    let arrival = sample_frame_v3(&data, 12).unwrap()[1];
    assert_eq!(arrival.x, 600.0);
    assert_eq!(arrival.y, 300.0);
    assert_eq!(arrival.width, 840.0);
    assert_eq!(arrival.height, 480.0);
    assert_eq!(arrival.rotation_deg, 90.0);
    assert_eq!(arrival.opacity, 0.25);
    assert_eq!(sample_frame_v3(&data, 23).unwrap()[1], arrival);
    assert!(sample_frame_v3(&data, 24).is_err());
    assert_eq!(p, before);
}

#[test]
fn key_at_zero_replaces_base_before_interpolation_without_rounding_its_time() {
    let mut data = plan(&project());
    data.nodes[1].keyframes.push(
        motionwright_manim_profile::planar_motion::ManimMotionKeyframeV3 {
            at: RationalTime::ZERO,
            property: ManimMotionPropertyV3::X,
            value: 400.0,
            interpolation: ManimMotionInterpolationV3::Hold,
        },
    );
    assert_eq!(sample_frame_v3(&data, 0).unwrap()[1].x, 400.0);
    assert_eq!(sample_frame_v3(&data, 6).unwrap()[1].x, 500.0);
    let retained = inspect_source_bundle_v3(&source_bundle_v3(&data).unwrap()).unwrap();
    assert_eq!(retained.plan, data);
}

#[test]
fn midpoint_sampler_preserves_canonical_clock_and_remains_unverified() {
    let data = plan(&project());
    assert_eq!(data.clock.duration, RationalTime::new(2, 1).unwrap());
    assert_eq!(data.clock.frame_count, 24);
    let sample = motion_sampling_v3(24, 12).unwrap();
    assert_eq!(
        sample.internal_play_duration,
        RationalTime::new(47, 24).unwrap()
    );
    assert!(!sample.runtime_all_frames_verified);
    let adverse = motion_sampling_v3(29, 25).unwrap();
    assert_eq!(
        adverse.internal_play_duration,
        RationalTime::new(57, 50).unwrap()
    );
    assert_eq!(adverse.predicted_frame_count, 29);
    assert!(motion_sampling_v3(1, 12).is_err());
    assert!(motion_sampling_v3(3_601, 12).is_err());
}

#[test]
fn rotation_is_about_the_authored_center_and_camera_does_not_mutate_pose() {
    let mut p = project();
    let before = p.scenes[0].nodes.clone();
    p.scenes[0].camera = CameraState {
        center_x: 800.0,
        center_y: 400.0,
        zoom: 1.5,
        rotation_deg: 0.0,
        safe_margin: 0.1,
    };
    let data = plan(&p);
    let mut pose = data.nodes[1].base;
    pose.rotation_deg = 90.0;
    let v = projected_rectangle_vertices_v3(pose);
    let center = [
        (240.0 + 300.0 - 960.0) / 135.0,
        (540.0 - 180.0 - 180.0) / 135.0,
    ];
    assert!((v[0][0] - (center[0] + 180.0 / 135.0)).abs() < 1e-9);
    assert!((v[0][1] - (center[1] - 300.0 / 135.0)).abs() < 1e-9);
    assert_eq!(p.scenes[0].nodes, before);
    assert_eq!(data.camera.zoom, 1.5);
}

#[test]
fn source_budget_and_unrepresented_geometry_fail_before_native_start() {
    let valid = plan(&project());
    for case in 0..7 {
        let mut bad = valid.clone();
        match case {
            0 => bad.nodes[1].base.width = 0.0,
            1 => bad.nodes[1].base.opacity = f64::NAN,
            2 => bad.nodes[1].keyframes[0].at = bad.clock.duration,
            3 => {
                let duplicate = bad.nodes[1].keyframes[0].clone();
                bad.nodes[1].keyframes.push(duplicate);
            }
            4 => bad.nodes[0].node_id = bad.nodes[1].node_id,
            5 => bad.nodes[0].base.width += 1.0,
            _ => bad.camera.rotation_deg = 5.0,
        }
        assert!(source_package_v3(&bad).is_err(), "case {case}");
    }
    let mut bad = valid.clone();
    bad.nodes[0].keyframes.push(
        valid.nodes[1]
            .keyframes
            .iter()
            .find(|k| k.property == ManimMotionPropertyV3::Width)
            .unwrap()
            .clone(),
    );
    assert!(
        validate_plan_v3(&bad)
            .unwrap_err()
            .to_string()
            .contains("tracks_differ")
    );
    let mut bad = valid;
    bad.nodes = (0..512)
        .map(|i| {
            let mut n = bad.nodes[1].clone();
            n.node_id = Uuid::from_u128(i as u128 + 1);
            n.canonical_ordinal = i;
            n
        })
        .collect();
    assert!(source_package_v3(&bad).is_err());
}

#[test]
fn strict_v3_source_refuses_unknown_fields_and_cannot_parse_as_static_v2() {
    let data = plan(&project());
    let mut value = serde_json::to_value(&data).unwrap();
    value["arbitrary_program"] = serde_json::json!("__import__('os').system('forbidden')");
    assert!(serde_json::from_value::<ManimScenePlanV3>(value).is_err());
    assert!(
        serde_json::from_value::<motionwright_manim_profile::ManimScenePlanV2>(
            serde_json::to_value(&data).unwrap()
        )
        .is_err()
    );
    let mut quoted = data;
    quoted.nodes[1].display_name = "label \" __import__('os') #".into();
    let code = compile_manim_python_v3(&quoted).unwrap();
    assert!(code.contains("json.loads(_DATA)"));
    assert!(!code.contains("eval("));
    assert!(!code.contains("exec("));
    assert!(code.contains("VGroup(*objects)"));
}

#[test]
fn source_roundtrip_retains_parameters_clock_kind_and_full_uuid() {
    let data = plan(&project());
    let bytes = source_bundle_v3(&data).unwrap();
    let observed = inspect_source_bundle_v3(&bytes).unwrap();
    assert_eq!(observed.plan, data);
    assert_eq!(observed.entries.len(), 5);
    assert_eq!(observed.closure.plan_schema_version, 3);
    assert_eq!(source_bundle_v3(&observed.plan).unwrap(), bytes);
    assert_eq!(
        observed.plan.nodes[0].source_kind,
        ManimSourceShapeKindV2::Circle
    );
    assert_eq!(observed.plan.nodes[1].node_id, RED);
    let mut current = data.clone();
    current.binding.generation = Uuid::new_v4();
    current.binding.revision += 9;
    assert_eq!(rebind_source_v3(&data, &current).unwrap().plan, current);
    current.nodes[1].base.x += 1.0;
    assert!(rebind_source_v3(&data, &current).is_err());
}

#[test]
fn unsupported_beats_cues_font_and_camera_are_explicit_preflight_errors() {
    let original = project();
    for case in 0..7 {
        let mut p = original.clone();
        match case {
            0 => p.scenes[0].beats.push(Beat {
                id: Uuid::new_v4(),
                label: "Required cue".into(),
                objective: "Not silently omitted".into(),
                start: RationalTime::ZERO,
                duration: RationalTime::new(1, 1).unwrap(),
            }),
            1 => p.audio.cues.push(AudioCue {
                id: Uuid::new_v4(),
                label: "Required onset".into(),
                at: RationalTime::new(1, 2).unwrap(),
                source_segment_id: None,
                evidence: CueEvidence::Manual,
            }),
            2 => p.scenes[0].nodes[1].style.font_family = Some("Unresolved source font".into()),
            3 => p.scenes[0].camera.rotation_deg = 10.0,
            4 => p.scenes[0].nodes[1].style.blend_mode = BlendMode::Multiply,
            5 => p.production_design.measured_bindings.push(MeasuredBinding {
                id: Uuid::new_v4(),
                scene_id: p.scenes[0].id,
                node_id: RED,
                table_id: Uuid::new_v4(),
                datum_key: "original".into(),
                view: MeasuredView::Bar {
                    axis_min_milli: 0,
                    axis_max_milli: 1000,
                    max_width_px: 600,
                },
                baseline: MeasuredOutput::Bar { width_px: 600 },
            }),
            _ => p.production_design.camera_reframes.push(CameraReframe {
                id: Uuid::new_v4(),
                scene_id: p.scenes[0].id,
                component_version: 1,
                start_center_x: 960.0,
                start_center_y: 540.0,
                end_center_x: 990.0,
                end_center_y: 540.0,
                duration_frames: 30,
            }),
        }
        let error = build_manim_plan_v3(
            &p,
            &RevisionStamp::from(&p),
            p.scenes[0].id,
            p.deliverables[0].id,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported, "case {case}");
        assert!(error.outcome_known);
    }
}

#[test]
fn saved_preparation_rejects_foreign_stamp_and_cue_without_journal_or_files() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let p = stored_project(&service);
    let mut foreign = RevisionStamp::from(&p);
    foreign.resource = "project:foreign".into();
    assert_eq!(
        prepare_saved_manim_motion_source(
            &service,
            p.id,
            &foreign,
            p.scenes[0].id,
            p.deliverables[0].id
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    let cue = AudioCue {
        id: Uuid::new_v4(),
        label: "Not implemented beat link".into(),
        at: RationalTime::new(1, 2).unwrap(),
        source_segment_id: None,
        evidence: CueEvidence::Manual,
    };
    let p = apply(&service, &p, Change::UpsertAudioCue { cue });
    let before_files = fs::read_dir(folder.path()).unwrap().count();
    assert_eq!(
        prepare_saved_manim_motion_source(
            &service,
            p.id,
            &RevisionStamp::from(&p),
            p.scenes[0].id,
            p.deliverables[0].id
        )
        .unwrap_err()
        .code,
        ErrorCode::Unsupported
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
    assert_eq!(fs::read_dir(folder.path()).unwrap().count(), before_files);
}

#[test]
fn saved_motion_key_edit_is_one_canonical_change_and_keeps_identity_and_carriers() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let (p, origin, edited) = pair(&service, folder.path(), |data| {
        data.nodes[1]
            .keyframes
            .iter_mut()
            .find(|k| k.property == ManimMotionPropertyV3::X)
            .unwrap()
            .value = 720.0;
    });
    let preview =
        preview_saved_manim_motion_edit(&service, p.id, &RevisionStamp::from(&p), origin, edited)
            .unwrap();
    assert!(matches!(preview.change, Change::SetCanvasKeyframe { .. }));
    assert!(!preview.canonical_import_applied);
    assert!(!preview.native_execution_performed);
    assert_eq!(service.project(p.id).unwrap(), p);
    let result = apply_saved_manim_motion_edit(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        "one-key-import",
        origin,
        edited,
    )
    .unwrap();
    let mut expected = p.clone();
    expected.apply_change(&preview.change).unwrap();
    expected.updated_at = result.updated_at;
    assert_eq!(result, expected);
    assert_eq!(result.assets, p.assets);
    assert_eq!(result.locks, p.locks);
    assert_eq!(
        result.production_design.capsules,
        p.production_design.capsules
    );
    assert_eq!(sample_frame_v3(&plan(&result), 6).unwrap()[1].x, 480.0);
    assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
    assert_eq!(
        apply_saved_manim_motion_edit(
            &service,
            p.id,
            &RevisionStamp::from(&p),
            "stale-original-cannot-replay",
            origin,
            edited
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
}

#[test]
fn saved_pose_and_camera_edit_each_use_the_existing_change_without_hidden_updates() {
    for camera in [false, true] {
        let folder = tempfile::tempdir().unwrap();
        let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
        let (p, origin, edited) = pair(&service, folder.path(), |data| {
            if camera {
                data.camera.zoom = 1.25;
                data.camera.center_x = 900.0;
            } else {
                data.nodes[1].base.x = 330.0;
                data.nodes[1].base.rotation_deg = 15.0;
                data.nodes[1].base.opacity = 0.8;
            }
        });
        let preview = preview_saved_manim_motion_edit(
            &service,
            p.id,
            &RevisionStamp::from(&p),
            origin,
            edited,
        )
        .unwrap();
        assert!(if camera {
            matches!(preview.change, Change::SetCamera { .. })
        } else {
            matches!(preview.change, Change::TransformCanvasNode { .. })
        });
        let result = apply_saved_manim_motion_edit(
            &service,
            p.id,
            &RevisionStamp::from(&p),
            "one-source-edit",
            origin,
            edited,
        )
        .unwrap();
        assert_eq!(result.revision, p.revision + 1);
        assert_eq!(result.assets, p.assets);
        assert_eq!(result.scenes[0].nodes[0], p.scenes[0].nodes[0]);
        assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
    }
}

#[test]
fn locked_key_edit_leaves_project_and_original_receipt_stream_unchanged() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let (p, origin, edited) = pair(&service, folder.path(), |data| {
        data.nodes[1]
            .keyframes
            .iter_mut()
            .find(|k| k.property == ManimMotionPropertyV3::X)
            .unwrap()
            .value = 720.0;
    });
    let p = apply(
        &service,
        &p,
        Change::SetNodePropertyLock {
            scene_id: p.scenes[0].id,
            node_id: RED,
            property: NodeProperty::Position,
            locked: true,
        },
    );
    let receipts = service.production_receipts(p.id, 256).unwrap();
    let error = apply_saved_manim_motion_edit(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        "locked-key",
        origin,
        edited,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::PermissionDenied);
    assert!(error.outcome_known);
    assert_eq!(service.project(p.id).unwrap(), p);
    assert_eq!(
        serde_json::to_value(service.production_receipts(p.id, 256).unwrap()).unwrap(),
        serde_json::to_value(receipts).unwrap()
    );
}

#[test]
fn rehashed_multi_control_identity_style_and_noop_edits_are_not_importable() {
    for case in 0..5 {
        let folder = tempfile::tempdir().unwrap();
        let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
        let (p, origin, edited) = pair(&service, folder.path(), |data| match case {
            0 => {
                data.nodes[1].base.x += 50.0;
                data.nodes[0].base.x += 30.0;
            }
            1 => data.nodes[1].fill_hex = "#112233".into(),
            2 => data.nodes[1].node_id = Uuid::new_v4(),
            3 => {
                data.camera.zoom = 1.1;
                data.nodes[1].base.x += 30.0;
            }
            _ => {}
        });
        assert!(
            preview_saved_manim_motion_edit(
                &service,
                p.id,
                &RevisionStamp::from(&p),
                origin,
                edited
            )
            .is_err(),
            "case {case}"
        );
        assert_eq!(service.project(p.id).unwrap(), p);
        assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
    }
}

#[test]
fn portable_bundle_restores_cas_and_rebinds_generation_without_native_authority() {
    let first = tempfile::tempdir().unwrap();
    let second = tempfile::tempdir().unwrap();
    let service = StudioService::open(first.path().join("source.sqlite")).unwrap();
    let p = stored_project(&service);
    let bytes = source_bundle_v3(&plan(&p)).unwrap();
    let original_plan = inspect_source_bundle_v3(&bytes).unwrap().plan;
    let (p, asset_id) = attach(
        &service,
        &p,
        first.path(),
        "motion.mwmsrc.zip",
        &bytes,
        true,
    );
    let bundle = first.path().join("portable-project");
    service.export_project_bundle(p.id, &bundle).unwrap();
    let restored_service = StudioService::open(second.path().join("restored.sqlite")).unwrap();
    let restored = restored_service.import_project_bundle(&bundle).unwrap();
    assert_eq!(restored.id, p.id);
    assert_eq!(restored.revision, p.revision);
    assert_ne!(restored.generation, p.generation);
    let mut unchanged = restored.clone();
    unchanged.generation = p.generation;
    unchanged.updated_at = p.updated_at;
    assert_eq!(unchanged, p);
    let observed = inspect_saved_manim_motion_source(
        &restored_service,
        restored.id,
        &RevisionStamp::from(&restored),
        asset_id,
    )
    .unwrap();
    assert_eq!(observed.origin_binding, original_plan.binding);
    assert_eq!(observed.current_binding.generation, restored.generation);
    assert!(!observed.imported_receipts_used_as_authority);
    assert!(!observed.native_execution_performed);
    assert!(
        restored_service
            .production_receipts(restored.id, 256)
            .unwrap()
            .is_empty()
    );
    let prepared = prepare_saved_manim_motion_source(
        &restored_service,
        restored.id,
        &RevisionStamp::from(&restored),
        restored.scenes[0].id,
        restored.deliverables[0].id,
    )
    .unwrap();
    assert_eq!(prepared.closure, observed.prepared_source);
}

fn native_trace_fixture(data: &ManimScenePlanV3) -> serde_json::Value {
    let mut order = (0..data.nodes.len()).collect::<Vec<_>>();
    order.sort_by_key(|i| (data.nodes[*i].z_index, data.nodes[*i].canonical_ordinal));
    let frames = (0..data.clock.frame_count).map(|index| {
        let poses = sample_frame_v3(data, index).unwrap();
        let objects = order.iter().map(|i| {
            let node = &data.nodes[*i]; let p = poses[*i];
            let center = [(p.x + p.width / 2.0 - 960.0) / 135.0, (540.0 - p.y - p.height / 2.0) / 135.0, 0.0];
            let circle = node.source_kind == ManimSourceShapeKindV2::Circle;
            let v = projected_rectangle_vertices_v3(p);
            let (s, c) = (-p.rotation_deg.to_radians()).sin_cos(); let radius = p.width / 270.0;
            let start = if circle { [center[0] + radius * c, center[1] + radius * s, 0.0] } else { v[0] };
            let mid = if circle { [center[0] - radius * c, center[1] - radius * s, 0.0] } else { v[2] };
            serde_json::json!({"name":format!("mw_{}",node.node_id.simple()),"primitive":if circle {"Circle"} else {"Rectangle"},
                "center":center,"width":if circle {p.width/135.0} else {(p.width*c.abs()+p.height*s.abs())/135.0},
                "height":if circle {p.height/135.0} else {(p.width*s.abs()+p.height*c.abs())/135.0},
                "z_index":node.z_index,"fill_hex":node.fill_hex,"fill_opacity":p.opacity,"stroke_width":0.0,"stroke_opacity":0.0,
                "vertices":if circle {vec![]} else {v.to_vec()},"curve_start":start,"curve_mid":mid})
        }).collect::<Vec<_>>();
        serde_json::json!({"index":index,"objects":objects,"camera":{
            "pixel_width":data.render_profile.width,"pixel_height":data.render_profile.height,
            "frame_rate":data.render_profile.frame_rate,"frame_width":128.0/(9.0*data.camera.zoom),
            "frame_height":8.0/data.camera.zoom,"frame_center":[(data.camera.center_x-960.0)/135.0,(540.0-data.camera.center_y)/135.0,0.0],
            "background_hex":"#000000","background_opacity":1.0}})
    }).collect::<Vec<_>>();
    serde_json::json!({"schema_version":1,"profile_id":"mw-manim-planar-motion-v3","scene_class":"MotionwrightScene",
        "plan_sha256":source_package_v3(data).unwrap().closure.plan_sha256,"binding":data.binding,
        "sampler_id":"manim-0.21-cairo-midpoint-play-v1","sample_count":data.clock.frame_count,
        "callback_alphas":(0..data.clock.frame_count).map(|i| f64::from(i)/(f64::from(data.clock.frame_count)-0.5)).collect::<Vec<_>>(),
        "samples":frames,"decoded_video_verified":false,"canonical_import_applied":false})
}

#[test]
fn typed_native_getters_require_every_original_frame_and_do_not_infer_decoded_video() {
    let data = plan(&project());
    let fixture = native_trace_fixture(&data);
    let observed =
        inspect_motion_readback_v3(&data, &serde_json::to_vec(&fixture).unwrap()).unwrap();
    assert_eq!(observed.samples.len(), 24);
    assert!(!observed.decoded_video_verified);
    assert!(!observed.canonical_import_applied);
    for case in 0..10 {
        let mut bad = fixture.clone();
        match case {
            0 => {
                bad["samples"].as_array_mut().unwrap().pop();
            }
            1 => bad["callback_alphas"][6] = serde_json::json!(1.0),
            2 => bad["samples"][6]["index"] = serde_json::json!(5),
            3 => bad["samples"][6]["objects"][0]["fill_opacity"] = serde_json::json!(1.0),
            4 => bad["samples"][6]["objects"]
                .as_array_mut()
                .unwrap()
                .reverse(),
            5 => bad["binding"]["generation"] = serde_json::json!(Uuid::new_v4()),
            6 => bad["samples"][6]["camera"]["frame_center"] = serde_json::json!([1.0, 0.0, 0.0]),
            7 => {
                bad["samples"][6]["objects"][0]["curve_start"] = serde_json::json!([0.0, 0.0, 0.0])
            }
            8 => bad["decoded_video_verified"] = serde_json::json!(true),
            _ => {
                bad["samples"][6]["objects"][0]
                    .as_object_mut()
                    .unwrap()
                    .remove("vertices");
            }
        }
        assert!(
            inspect_motion_readback_v3(&data, &serde_json::to_vec(&bad).unwrap()).is_err(),
            "case {case}"
        );
    }
}
