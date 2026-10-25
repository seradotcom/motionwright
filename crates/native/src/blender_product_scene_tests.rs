use super::*;
use std::collections::{BTreeMap, BTreeSet};

fn fixture() -> ProductScene {
    ProductScene::vessel("product_test", CameraCurve::BezierAutoClamped)
}
fn observed(binding: &Binding) -> Option<ObservedReference> {
    Some(ObservedReference {
        reference: format!("blender-rna/v1/unit_observation_{binding:?}"),
        native_name: Some(format!("observed_{binding:?}")),
    })
}
fn calls(steps: &[SemanticStep]) -> Vec<NativeCall> {
    steps
        .iter()
        .map(|step| step.bind(observed).unwrap())
        .collect()
}
fn has_set(steps: &[SemanticStep], target: Binding, property: &str, expected: Value) -> bool {
    steps.iter().any(|step| {
        step.required_bindings().contains(&&target)
            && step.bind(observed).is_ok_and(|call| {
                call.operation == "driver.blender.semantic.property.set"
                    && call.input["property"] == property
                    && call.input["value"] == expected
            })
    })
}
#[test]
fn original_shell_is_closed_oriented_and_non_degenerate() {
    let (vertices, faces) = vessel_mesh();
    assert_eq!(vertices.len(), 768);
    assert_eq!(faces.len(), 768);
    let mut edges: BTreeMap<(u32, u32), (usize, i32)> = BTreeMap::new();
    let mut unique_faces = BTreeSet::new();
    for face in &faces {
        assert_eq!(face.len(), 4);
        let distinct: BTreeSet<_> = face.iter().copied().collect();
        assert_eq!(distinct.len(), 4);
        assert!(unique_faces.insert(distinct));
        for i in 0..face.len() {
            let a = face[i];
            let b = face[(i + 1) % face.len()];
            assert!((a as usize) < vertices.len() && (b as usize) < vertices.len());
            let entry = edges.entry((a.min(b), a.max(b))).or_default();
            entry.0 += 1;
            entry.1 += if a < b { 1 } else { -1 };
        }
        let a = vertices[face[0] as usize];
        let b = vertices[face[1] as usize];
        let c = vertices[face[2] as usize];
        let u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        let v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let cross = [
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        ];
        assert!(cross.iter().map(|x| x * x).sum::<f64>() > 1e-20);
    }
    assert_eq!(edges.len(), 1536);
    assert!(edges.values().all(|entry| *entry == (2, 0)));
    assert_eq!(
        vertices.len() as i64 - edges.len() as i64 + faces.len() as i64,
        0
    );
    let heights: Vec<_> = vertices.iter().map(|p| p[2]).collect();
    assert_eq!(heights.iter().copied().fold(f64::INFINITY, f64::min), 0.008);
    assert_eq!(
        heights.iter().copied().fold(f64::NEG_INFINITY, f64::max),
        0.100
    );
    let largest_radius = vertices
        .iter()
        .map(|p| p[0].hypot(p[1]))
        .fold(0.0, f64::max);
    assert!((largest_radius - 0.052).abs() < 1e-14);
}
#[test]
fn shell_seams_share_original_indices() {
    let (_, faces) = vessel_mesh();
    assert_eq!(faces[0], vec![0, 8, 9, 1]);
    assert_eq!(faces[7], vec![7, 15, 8, 0]);
    assert_eq!(faces[767], vec![767, 7, 0, 760]);
}
#[test]
fn static_composition_preserves_physical_scene_and_sdk_bounds() {
    let result = project_product_scene(&fixture()).unwrap();
    let spec = &result.composition_spec;
    assert_eq!(spec["version"], 1);
    assert_eq!(spec["meters_per_unit"], 1.0);
    assert!(spec["animation"].is_object());
    assert_eq!(spec["textures"], json!([]));
    let entities = spec["entities"].as_array().unwrap();
    assert_eq!(entities.len(), 14);
    let ids: BTreeSet<_> = entities.iter().map(|e| e["id"].as_str().unwrap()).collect();
    assert_eq!(ids.len(), entities.len());
    assert_eq!(
        entities
            .iter()
            .filter(|e| e["shape"]["kind"] == "camera")
            .count(),
        2
    );
    assert_eq!(
        entities
            .iter()
            .filter(|e| e["shape"]["kind"] == "area_light")
            .count(),
        3
    );
    for entity in entities {
        if let Some(vertices) = entity["shape"]["vertices"].as_array() {
            assert!(vertices.len() <= 8192);
        }
        if let Some(faces) = entity["shape"]["faces"].as_array() {
            assert!(faces.len() <= 8192);
            assert!(
                faces
                    .iter()
                    .all(|f| (3..=32).contains(&f.as_array().unwrap().len()))
            );
        }
    }
    assert_eq!(spec["relations"].as_array().unwrap().len(), 5);
    let input = result.composition_plan_input();
    assert_eq!(input["intent"]["kind"], "create");
    assert_eq!(input["intent"]["spec"], *spec);
    assert!(result.before_create.len() + result.after_create.len() <= 512);
}
#[test]
fn timeline_is_half_open_and_native_cut_is_frame_601() {
    assert_eq!(FRAME_COUNT / FPS, 40);
    assert_eq!(SHOTS[0].start_frame, 0);
    assert_eq!(SHOTS[0].end_frame_exclusive, SHOTS[1].start_frame);
    assert_eq!(SHOTS[1].end_frame_exclusive, 1200);
    assert_eq!(native_frame(0).unwrap(), 1);
    assert_eq!(native_frame(599).unwrap(), 600);
    assert_eq!(native_frame(600).unwrap(), 601);
    assert_eq!(native_frame(1199).unwrap(), 1200);
    assert!(native_frame(1200).is_err());
    assert!(native_frame(u32::MAX).is_err());
    let projection = project_product_scene(&fixture()).unwrap();
    let marker_frames: Vec<_> = calls(&projection.after_create)
        .into_iter()
        .filter(|call| call.operation == "driver.blender.scene.marker.add")
        .map(|call| call.input["frame"].as_u64().unwrap())
        .collect();
    assert_eq!(marker_frames, vec![1, 601]);
}
#[test]
fn unknown_native_interpolation_is_rejected_before_projection() {
    let mut source = fixture();
    source.camera_curve = CameraCurve::NativeDefault;
    assert!(matches!(
        project_product_scene(&source),
        Err(ProjectionError::Unsupported {
            path: "camera_curve",
            ..
        })
    ));
}
#[test]
fn explicit_linear_and_bezier_remain_distinct_and_fully_observable() {
    for (curve, expected) in [
        (CameraCurve::Linear, "LINEAR"),
        (CameraCurve::BezierAutoClamped, "BEZIER"),
    ] {
        let mut source = fixture();
        source.camera_curve = curve;
        let projection = project_product_scene(&source).unwrap();
        let key_getters: Vec<_> = projection
            .readback
            .iter()
            .filter(|check| matches!(check.target, Binding::CameraKey { .. }))
            .collect();
        assert_eq!(key_getters.len(), 60);
        for (entity, frame, _) in CAMERA_POSES {
            for axis in 0..3 {
                let target = Binding::CameraKey {
                    entity,
                    axis,
                    frame,
                };
                for (property, value) in [
                    ("interpolation", expected),
                    ("handle_left_type", "AUTO_CLAMPED"),
                    ("handle_right_type", "AUTO_CLAMPED"),
                ] {
                    assert!(has_set(
                        &projection.after_create,
                        target.clone(),
                        property,
                        json!(value)
                    ));
                    assert!(key_getters.iter().any(|check| check.target == target
                        && check.property == property
                        && check.expected == value));
                }
            }
        }
    }
}
#[test]
fn source_values_never_silently_drop_invalid_or_unknown_semantics() {
    let source = fixture();
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -0.01, 1.01] {
        for edit in [ProductEdit::Roughness(value), ProductEdit::Metallic(value)] {
            assert!(project_product_edit(&source, &edit).is_err());
        }
    }
    for samples in [0, 15, 257, u32::MAX] {
        let mut bad = source.clone();
        bad.samples = samples;
        assert!(project_product_scene(&bad).is_err());
    }
    for namespace in ["", "../other", "E:\\scene", "with space", "name;command"] {
        let mut bad = source.clone();
        bad.namespace = namespace.into();
        assert!(project_product_scene(&bad).is_err());
    }
    let mut unknown = serde_json::to_value(&source).unwrap();
    unknown["bpy"] = json!("arbitrary code");
    assert!(serde_json::from_value::<ProductScene>(unknown).is_err());
    let mut unknown = serde_json::to_value(&source).unwrap();
    unknown["camera_curve"] = json!("bezier_approximation");
    assert!(serde_json::from_value::<ProductScene>(unknown).is_err());
}
#[test]
fn text_is_editable_data_with_explicit_glyph_profile_limits() {
    let source = fixture();
    for bad in ["", "line\n", "a\nb\nc", "café", "bad\u{0000}", "bad\ttext"] {
        assert!(project_product_edit(&source, &ProductEdit::Annotation(bad.into())).is_err());
    }
    assert!(project_product_edit(&source, &ProductEdit::Annotation("x".repeat(33))).is_err());
    let literal = "__import__('os')";
    let (edited, steps) =
        project_product_edit(&source, &ProductEdit::Annotation(literal.into())).unwrap();
    assert_eq!(edited.annotation, literal);
    let call = steps[0].bind(observed).unwrap();
    assert_eq!(call.operation, "driver.blender.semantic.property.set");
    assert_eq!(call.input["property"], "body");
    assert_eq!(call.input["value"], literal);
}
#[test]
fn edits_preserve_source_identity_geometry_cameras_and_opaque_native_scope() {
    let source = fixture();
    let serialized = serde_json::to_value(&source).unwrap();
    let old_projection = project_product_scene(&source).unwrap();
    for edit in [
        ProductEdit::Coating(Coating::Clay),
        ProductEdit::Roughness(0.7),
        ProductEdit::Metallic(0.2),
        ProductEdit::Annotation("CLAY\nSURFACE".into()),
    ] {
        let (next, steps) = project_product_edit(&source, &edit).unwrap();
        assert_eq!(next.namespace, source.namespace);
        assert_eq!(next.camera_curve, source.camera_curve);
        let next_projection = project_product_scene(&next).unwrap();
        assert_eq!(
            next_projection.composition_spec["entities"],
            old_projection.composition_spec["entities"]
        );
        assert_eq!(
            next_projection.composition_spec["relations"],
            old_projection.composition_spec["relations"]
        );
        assert!(
            steps
                .iter()
                .all(|step| step.operation() == "driver.blender.semantic.property.set")
        );
        assert!((1..=2).contains(&steps.len()));
        assert!(edit.parameter_path().starts_with("product."));
    }
    assert_eq!(serde_json::to_value(&source).unwrap(), serialized);
}
#[test]
fn surface_recipe_and_readbacks_include_bevel_angle_and_flat_caps() {
    let projection = project_product_scene(&fixture()).unwrap();
    assert!(has_set(
        &projection.after_create,
        Binding::BodyBevel,
        "limit_method",
        json!("ANGLE")
    ));
    assert!(has_set(
        &projection.after_create,
        Binding::BodyBevel,
        "angle_limit",
        json!(25.0_f64.to_radians())
    ));
    for entity in ["vessel_base", "vessel_floor"] {
        for index in 0..=1 {
            let target = Binding::CylinderCap { entity, index };
            assert!(has_set(
                &projection.after_create,
                target.clone(),
                "use_smooth",
                json!(false)
            ));
            assert!(
                projection
                    .readback
                    .iter()
                    .any(|check| check.target == target
                        && check.property == "use_smooth"
                        && check.expected == false)
            );
        }
    }
}
#[test]
fn font_is_native_font_with_emission_parent_and_discrete_visibility() {
    let projection = project_product_scene(&fixture()).unwrap();
    let native_calls = calls(&projection.after_create);
    assert!(native_calls.iter().any(|call| call.operation
        == "driver.blender.semantic.datablock.create"
        && call.input["root"] == "curves"
        && call.input["kind"] == "FONT"));
    let create_font = projection
        .after_create
        .iter()
        .find(|step| step.result_binding() == Some(&Binding::FontObject))
        .unwrap();
    assert!(
        create_font
            .bind(observed)
            .unwrap()
            .input
            .get("collection_ref")
            .is_none()
    );
    assert!(
        native_calls
            .iter()
            .any(|call| call.operation == "driver.blender.node.add"
                && call.input["type"] == "ShaderNodeEmission")
    );
    assert!(projection.after_create.iter().any(|step| {
        step.operation() == "driver.blender.semantic.relation.set"
            && step
                .required_bindings()
                .contains(&&Binding::Entity("camera_surface"))
            && step.required_bindings().contains(&&Binding::FontObject)
    }));
    for frame in [1, 600, 601, 1200] {
        assert!(has_set(
            &projection.after_create,
            Binding::FontVisibilityKey(frame),
            "interpolation",
            json!("CONSTANT")
        ));
    }
}
#[test]
fn all_recipe_calls_are_existing_public_operations_and_do_not_execute_or_save() {
    let allowed: BTreeSet<_> = [
        "driver.blender.render.settings",
        "driver.blender.semantic.property.set",
        "driver.blender.semantic.datablock.create",
        "driver.blender.semantic.object.create",
        "driver.blender.semantic.relation.set",
        "driver.blender.animation.keyframe.insert",
        "driver.blender.scene.marker.add",
        "driver.blender.node.remove",
        "driver.blender.node.add",
        "driver.blender.node.link",
        "driver.blender.material.assign",
    ]
    .into_iter()
    .collect();
    let projection = project_product_scene(&fixture()).unwrap();
    for call in calls(&projection.before_create)
        .into_iter()
        .chain(calls(&projection.after_create))
    {
        assert!(allowed.contains(call.operation));
        assert_ne!(call.operation, "driver.blender.render");
        assert_ne!(call.operation, "driver.blender.file.save");
        assert!(call.input.is_object());
    }
}
#[test]
fn binding_fails_closed_without_current_observation_or_native_name() {
    let projection = project_product_scene(&fixture()).unwrap();
    let setter = projection
        .after_create
        .iter()
        .find(|step| !step.required_bindings().is_empty())
        .unwrap();
    assert!(matches!(
        setter.bind(|_| None),
        Err(ProjectionError::MissingObservation(_))
    ));
    assert!(matches!(
        setter.bind(|_| Some(ObservedReference {
            reference: "guessed_object".into(),
            native_name: None
        })),
        Err(ProjectionError::InvalidObservation(_))
    ));
    let assign = projection
        .after_create
        .iter()
        .find(|step| step.operation() == "driver.blender.material.assign")
        .unwrap();
    assert!(
        assign
            .bind(|binding| observed(binding).map(|mut value| {
                value.native_name = None;
                value
            }))
            .is_err()
    );
    let call = assign.bind(observed).unwrap();
    assert_eq!(call.input["object"], "observed_FontObject");
    assert_eq!(call.input["material"], "observed_FontInk");
}
#[test]
fn semantic_binding_is_recomputed_for_each_dispatch() {
    let projection = project_product_scene(&fixture()).unwrap();
    let setter = &projection.after_create[0];
    let first = setter
        .bind(|_| {
            Some(ObservedReference {
                reference: "blender-rna/v1/generation_one".into(),
                native_name: None,
            })
        })
        .unwrap();
    let second = setter
        .bind(|_| {
            Some(ObservedReference {
                reference: "blender-rna/v1/generation_two".into(),
                native_name: None,
            })
        })
        .unwrap();
    assert_ne!(first.input["ref"], second.input["ref"]);
}
#[test]
fn property_readback_rejects_wrong_curve_handles_target_and_value_type() {
    let projection = project_product_scene(&fixture()).unwrap();
    let check = projection
        .readback
        .iter()
        .find(|check| {
            matches!(check.target, Binding::CameraKey { .. }) && check.property == "interpolation"
        })
        .unwrap();
    let request = check.getter().bind(observed).unwrap();
    let correct = json!({"ref":request.input["ref"],"property":"interpolation","value":"BEZIER"});
    assert!(check.matches_reply(&request, &correct));
    for (key, bad) in [
        ("ref", json!("blender-rna/v1/other")),
        ("property", json!("handle_left_type")),
        ("value", json!("LINEAR")),
        ("value", json!(1)),
        ("value", Value::Null),
    ] {
        let mut reply = correct.clone();
        reply[key] = bad;
        assert!(!check.matches_reply(&request, &reply));
    }
    let handles = projection
        .readback
        .iter()
        .find(|check| check.property == "handle_left_type")
        .unwrap();
    let request = handles.getter().bind(observed).unwrap();
    assert!(!handles.matches_reply(
        &request,
        &json!({"ref":request.input["ref"],"property":"handle_left_type","value":"AUTO"})
    ));
}
#[test]
fn numerical_readback_has_bounded_tolerance_and_exact_cardinality() {
    let check = expect(
        Binding::FontObject,
        "location",
        json!([0.018, -0.018, -0.30]),
    );
    let request = check.getter().bind(observed).unwrap();
    let reply = |value| json!({"ref":request.input["ref"],"property":"location","value":value});
    assert!(check.matches_reply(&request, &reply(json!([0.0180001, -0.018, -0.30]))));
    assert!(!check.matches_reply(&request, &reply(json!([0.0181, -0.018, -0.30]))));
    assert!(!check.matches_reply(&request, &reply(json!([0.018, -0.018]))));
    assert!(!check.matches_reply(&request, &reply(json!(["0.018", -0.018, -0.30]))));
    assert!(!check.matches_reply(&request, &reply(Value::Null)));
}
#[test]
fn srgb_conversion_keeps_linear_threshold_and_alpha() {
    let value = srgb_linear([0.0, 0.04045, 1.0]);
    assert_eq!(value[0], 0.0);
    assert!((value[1] - 0.0031308049535603713).abs() < 1e-15);
    assert_eq!(value[2], 1.0);
    assert_eq!(value[3], 1.0);
}
#[test]
fn source_config_roundtrip_retains_typed_editability() {
    let source = fixture();
    let bytes = serde_json::to_vec(&source).unwrap();
    assert_eq!(
        serde_json::from_slice::<ProductScene>(&bytes).unwrap(),
        source
    );
    let edited = source.edited(&ProductEdit::Coating(Coating::Clay)).unwrap();
    let bytes = serde_json::to_vec(&edited).unwrap();
    assert_eq!(
        serde_json::from_slice::<ProductScene>(&bytes).unwrap(),
        edited
    );
}
#[test]
fn temporal_readback_never_accepts_fractional_frames_or_clock_drift() {
    let clock = expect(Binding::RenderSettings, "fps_base", json!(1.0));
    let request = clock.getter().bind(observed).unwrap();
    assert!(!clock.matches_reply(
        &request,
        &json!({"ref":request.input["ref"],"property":"fps_base","value":1.0000001})
    ));
    let key = expect(
        Binding::CameraKey {
            entity: "camera_form",
            axis: 0,
            frame: 600,
        },
        "co",
        json!([600.0, 0.30]),
    );
    let request = key.getter().bind(observed).unwrap();
    assert!(key.matches_reply(
        &request,
        &json!({"ref":request.input["ref"],"property":"co","value":[600.0,0.3000001]})
    ));
    assert!(!key.matches_reply(
        &request,
        &json!({"ref":request.input["ref"],"property":"co","value":[600.0000001,0.30]})
    ));
}

#[test]
fn camera_actions_are_seeded_by_public_animation_before_semantic_finalization() {
    let scene = ProductScene::vessel("owned_actions", CameraCurve::BezierAutoClamped);
    let projection = project_product_scene(&scene).unwrap();
    let animation = &projection.composition_spec["animation"];
    assert_eq!(animation["id"], json!("camera_motion"));
    assert_eq!(animation["rate"], json!({"num": 30, "den": 1}));
    assert_eq!(animation["nla_tracks"], json!([]));
    assert_eq!(animation.as_object().unwrap().len(), 4);
    let channels = animation["channels"].as_array().unwrap();
    assert_eq!(channels.len(), 2);
    for (channel, entity) in channels.iter().zip(["camera_form", "camera_surface"]) {
        assert_eq!(channel["entity"], json!(entity));
        assert_eq!(channel["bone"], Value::Null);
        assert_eq!(channel["property"], json!("translation"));
        assert_eq!(channel.as_object().unwrap().len(), 4);
        let expected: Vec<Value> = CAMERA_POSES
            .iter()
            .copied()
            .filter(|(camera, _, _)| *camera == entity)
            .map(|(_, frame, location)| json!({"frame": frame, "value": location}))
            .collect();
        assert_eq!(channel["keys"], json!(expected));
        for key in channel["keys"].as_array().unwrap() {
            assert!(key["frame"].as_u64().is_some());
            assert_eq!(key.as_object().unwrap().len(), 2);
        }
    }
    let inserted: Vec<_> = projection
        .after_create
        .iter()
        .filter(|step| step.operation() == "driver.blender.animation.keyframe.insert")
        .collect();
    assert_eq!(inserted.len(), 4);
    for step in inserted {
        assert_eq!(step.required_bindings(), vec![&Binding::FontObject]);
    }
}
