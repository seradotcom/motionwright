//! Source-only tests for the exact closed Manim Community planar camera profile.
//! New native renderer, dispatch or source compiler behavior is NOT assumed.
use motionwright_manim_profile::{
    MAX_SOURCE_ENTRY_BYTES_V2, ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2,
    ManimProfileError, ManimRenderProfile, ManimSceneBindingV2, ManimSourceShapeKindV2,
    ManimStaticShapeV2,
};
use semwright_media_time::Rational;
use uuid::Uuid;

#[path = "../../manim-profile/src/planar_camera.rs"]
mod camera_source;

#[cfg(test)]
mod production {
    use super::*;
    use camera_source::{
        CAMERA_PROFILE_V1, CAMERA_SCHEMA_V1, CameraMoveV1, ManimCameraPoseV1,
        ManimCameraProfileIdV1, ManimCameraScenePlanV1,
    };

    fn clock() -> ManimFrameClockV2 {
        ManimFrameClockV2::from_canonical(
            Rational::new(0, 1).unwrap(),
            Rational::new(1, 1).unwrap(),
            Rational::new(30, 1).unwrap(),
        )
        .unwrap()
    }
    fn node(
        kind: ManimSourceShapeKindV2,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
        color: &str,
        ordinal: u32,
    ) -> ManimStaticShapeV2 {
        ManimStaticShapeV2 {
            node_id: Uuid::from_u128(0x40000000_0000_4000_8000_000000000001 + u128::from(ordinal)),
            display_name: format!("source object {ordinal}"),
            source_kind: kind,
            canonical_ordinal: ordinal,
            z_index: ordinal as i32 + 1,
            x_px: x,
            y_px: y,
            width_px: w,
            height_px: h,
            fill_hex: color.into(),
        }
    }
    fn finite_fixture() -> ManimCameraScenePlanV1 {
        let scene_id = Uuid::from_u128(0x20000000_0000_4000_8000_000000000001);
        ManimCameraScenePlanV1 {
            schema_version: CAMERA_SCHEMA_V1,
            profile_id: ManimCameraProfileIdV1::PlanarCamera,
            output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
            binding: ManimSceneBindingV2 {
                project_id: Uuid::new_v4(),
                generation: Uuid::new_v4(),
                revision: 8,
                scene_id,
                deliverable_id: Uuid::new_v4(),
            },
            clock: clock(),
            render_profile: ManimRenderProfile {
                width: 640,
                height: 360,
                frame_rate: 30,
            },
            canvas: ManimCanvasV2::default(),
            background: ManimMatteV2::default(),
            camera: ManimCameraPoseV1 {
                center_x: 1000.0,
                center_y: 520.0,
                zoom: 1.0,
                rotation_deg: 0.0,
                safe_margin: 0.05,
            },
            reframe: CameraMoveV1 {
                id: Uuid::from_u128(0x30000000_0000_4000_8000_000000000001),
                scene_id,
                component_version: 2,
                start_center_x: 960.0,
                start_center_y: 540.0,
                end_center_x: 1000.0,
                end_center_y: 520.0,
                duration_frames: 24,
            },
            nodes: vec![
                node(
                    ManimSourceShapeKindV2::Rectangle,
                    420.0,
                    270.0,
                    270.0,
                    180.0,
                    "#E53935",
                    0,
                ),
                node(
                    ManimSourceShapeKindV2::Circle,
                    1125.0,
                    540.0,
                    180.0,
                    180.0,
                    "#2BBBAD",
                    1,
                ),
            ],
        }
    }

    #[test]
    fn exact_source_stamps_and_all_thirty_frames_follow_one_camera_not_objects() {
        let plan = finite_fixture();
        assert_eq!(CAMERA_PROFILE_V1, "mw-manim-planar-camera-v1");
        plan.validate_source_only().unwrap();
        let samples = plan.expected_frame_samples().unwrap();
        assert_eq!(samples.len(), 30);
        assert_eq!(samples[0].pts, Rational::new(0, 30).unwrap());
        assert_eq!(samples[0].center_x, 960.0);
        assert_eq!(samples[0].center_y, 540.0);
        let midway = plan.sample(12).unwrap();
        assert_eq!(midway.center_x, 980.0);
        assert_eq!(midway.center_y, 530.0);
        let endpoint = plan.sample(24).unwrap();
        assert_eq!(endpoint.pts, Rational::new(24, 30).unwrap());
        assert_eq!(endpoint.center_x, 1000.0);
        assert_eq!(endpoint.center_y, 520.0);
        assert!((endpoint.world_frame_center[0] - (40.0 / 135.0)).abs() < 1e-12);
        assert!((endpoint.world_frame_center[1] - (20.0 / 135.0)).abs() < 1e-12);
        assert_eq!(endpoint.world_frame_width, 128.0 / 9.0);
        assert_eq!(endpoint.world_frame_height, 8.0);
        for frame in 24..30 {
            let held = plan.sample(frame).unwrap();
            assert_eq!((held.center_x, held.center_y), (1000.0, 520.0));
        }
        let before = plan
            .output_pixel_from_source_point(420.0, 270.0, 0)
            .unwrap();
        let after = plan
            .output_pixel_from_source_point(420.0, 270.0, 24)
            .unwrap();
        assert_eq!(before, [140.0, 90.0]);
        assert_eq!(after, [126.66666666666667, 96.66666666666667]);
        assert_eq!(plan.nodes[0].x_px, 420.0);
        assert_eq!(plan.nodes[1].x_px, 1125.0);
        assert!(plan.sample(30).is_err());
    }

    #[test]
    fn source_roundtrip_and_single_bound_camera_edit_keep_all_world_objects() {
        let original = finite_fixture();
        let json = serde_json::to_vec(&original).unwrap();
        let reopen: ManimCameraScenePlanV1 = serde_json::from_slice(&json).unwrap();
        assert_eq!(reopen, original);
        let mut edited = original.clone();
        edited.reframe.end_center_x = 1012.0;
        edited.camera.center_x = 1012.0; // Derived same canonical Change.
        edited.binding.revision += 1;
        edited.validate_source_only().unwrap();
        assert_eq!(edited.nodes, original.nodes);
        assert_eq!(edited.binding.project_id, original.binding.project_id);
        assert_eq!(edited.binding.generation, original.binding.generation);
        assert_ne!(
            edited.sample(24).unwrap().center_x,
            original.sample(24).unwrap().center_x
        );
        let edited_bytes = serde_json::to_vec(&edited).unwrap();
        assert_eq!(
            serde_json::from_slice::<ManimCameraScenePlanV1>(&edited_bytes).unwrap(),
            edited
        );
        // A source plan cannot masquerade as a native source execution or grant.
        let mut forged = serde_json::to_value(&edited).unwrap();
        forged["native_ready"] = serde_json::json!(true);
        assert!(serde_json::from_value::<ManimCameraScenePlanV1>(forged).is_err());
    }

    #[test]
    fn incompatible_renderer_version_clock_crop_optics_and_keys_reject_closed() {
        let plan = finite_fixture();
        let mut neg = plan.clone();
        neg.reframe.component_version = 1;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.schema_version = 3;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.reframe.scene_id = Uuid::new_v4();
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.clock = ManimFrameClockV2::from_canonical(
            Rational::new(0, 1).unwrap(),
            Rational::new(1, 1).unwrap(),
            Rational::new(60, 1).unwrap(),
        )
        .unwrap();
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.camera.zoom = 1.1;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.camera.rotation_deg = 30.0;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.reframe.end_center_x = f64::NAN;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.reframe.end_center_x = 1140.0;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.reframe.duration_frames = 11;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.reframe.duration_frames = 31;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.camera.center_x += 1.0;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[0].source_kind = ManimSourceShapeKindV2::Shape;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[0].x_px = 5.0;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[1].width_px = 190.0;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.render_profile.width = 1920;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[0].canonical_ordinal = 9;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[0].display_name.clear();
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[0].fill_hex = "#ZZZZZZ".into();
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.background.color = "#110011".into();
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.clock.project_start_frame = 5;
        assert!(neg.validate_source_only().is_err());
        let mut neg = plan.clone();
        neg.nodes[1].node_id = neg.nodes[0].node_id;
        assert!(neg.validate_source_only().is_err());
    }

    #[test]
    fn source_budgets_reject_extreme_frame_object_combinations_without_dispatch() {
        let mut plan = finite_fixture();
        plan.clock = ManimFrameClockV2::from_canonical(
            Rational::new(0, 1).unwrap(),
            Rational::new(6, 1).unwrap(),
            Rational::new(30, 1).unwrap(),
        )
        .unwrap();
        plan.nodes = (0..32)
            .map(|i| {
                node(
                    ManimSourceShapeKindV2::Rectangle,
                    420.0 + f64::from(i),
                    270.0,
                    80.0,
                    80.0,
                    "#FF0000",
                    i,
                )
            })
            .collect();
        assert!(
            plan.validate_source_only().is_err(),
            "32x180 getter transport cannot fit current inline budget"
        );
        assert!(MAX_SOURCE_ENTRY_BYTES_V2 < 32 * 180 * 1024);
        assert!(plan.sample(0).is_err());
    }

    #[test]
    fn trusted_camera_compiler_has_canonical_bytes_hash_and_no_user_code_interpolation() {
        use camera_source::{CAMERA_COMPILER_V1, compile_camera_program_source_only};
        use sha2::{Digest, Sha256};

        let source = finite_fixture();
        let original = compile_camera_program_source_only(&source).unwrap();
        let replay = compile_camera_program_source_only(&source).unwrap();
        assert_eq!(
            original, replay,
            "exact source must compile deterministically"
        );
        assert_eq!(original.compiler_id, CAMERA_COMPILER_V1);
        assert!(original.plan_document.ends_with(b"\n"));
        assert_eq!(
            serde_json::from_slice::<ManimCameraScenePlanV1>(&original.plan_document).unwrap(),
            source
        );
        assert_eq!(
            original.plan_sha256,
            hex::encode(Sha256::digest(&original.plan_document))
        );
        assert_eq!(
            original.source_sha256,
            hex::encode(Sha256::digest(original.python_source.as_bytes()))
        );
        assert!(
            original.python_source.len() <= MAX_SOURCE_ENTRY_BYTES_V2
                && !original.native_execution_performed
                && !original.runtime_sample_pts_verified
        );
        assert!(
            original
                .python_source
                .contains("class MotionwrightScene(MovingCameraScene):")
        );
        assert!(original.python_source.contains("UpdateFromAlphaFunc"));
        assert!(original.python_source.contains("run_time=seconds"));
        assert!(original.python_source.contains("self.camera.frame.move_to"));
        assert!(
            original
                .python_source
                .contains("native_grants_verified_by_this_script")
        );
        assert!(
            !original.python_source.contains("subprocess")
                && !original.python_source.contains("os.system(")
        );

        // Malicious display names are only JSON-string values. The only
        // variation in the emitted program is the quoted _DATA literal;
        // absolutely no authored bytes may become executable statements.
        let mut adversarial = source.clone();
        adversarial.nodes[0].display_name = "'); __import__('os').system('evil'); #".into();
        let alternate = compile_camera_program_source_only(&adversarial).unwrap();
        let fixed_program = "from manim import Circle, Rectangle, MovingCameraScene";
        assert_eq!(
            original.python_source.split_once(fixed_program).unwrap().1,
            alternate.python_source.split_once(fixed_program).unwrap().1,
            "the native program must be static across all authored source values"
        );
        assert_ne!(original.plan_sha256, alternate.plan_sha256);
        assert_ne!(original.source_sha256, alternate.source_sha256);

        // A canonical camera edit has exactly one authored endpoint and one
        // derived saved terminal camera. The trusted source and its closure
        // must necessarily change as well.
        let mut edited = source;
        edited.reframe.end_center_x = 1012.0;
        edited.camera.center_x = 1012.0;
        edited.binding.revision += 1;
        let next = compile_camera_program_source_only(&edited).unwrap();
        assert_ne!(original.plan_sha256, next.plan_sha256);
        assert_ne!(original.source_sha256, next.source_sha256);
        assert_eq!(
            original.python_source.split_once(fixed_program).unwrap().1,
            next.python_source.split_once(fixed_program).unwrap().1
        );
    }

    #[test]
    fn source_compiler_rejects_foreign_clock_camera_and_transport_budget() {
        use camera_source::compile_camera_program_source_only;
        let valid = finite_fixture();
        let mut bad = valid.clone();
        bad.reframe.component_version = 1;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = valid.clone();
        bad.camera.center_x = 12_000.0;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = valid.clone();
        bad.clock.frame_count += 1;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = valid.clone();
        bad.nodes[0].fill_hex = "'; raise Exception(); #".into();
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = valid;
        bad.nodes[0].display_name = "X".repeat(161);
        assert!(compile_camera_program_source_only(&bad).is_err());
    }
}
