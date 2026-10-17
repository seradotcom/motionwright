#![recursion_limit = "256"]

//! Synthetic contract-negative tests. These do not run Blender or prove media/source files.
use motionwright_native::{
    multi_renderer::{BlenderPrimitive, BlenderPrimitiveContribution, BlenderSceneContribution},
    production::blender_editable::{
        BlenderEditableEvidence, BlenderEditablePhase, BlenderEditableSource,
        BlenderEditableStageEvidence, prepare_blender_editable_project_source,
        prepare_blender_editable_source, verify_blender_editable_evidence_custody,
        verify_blender_editable_reopen, verify_blender_editable_snapshot,
        verify_blender_editable_stage_custody, verify_blender_editable_stored_evidence_custody,
    },
};
use semwright_native_sdk::{
    ErrorCode,
    types::{Execution, InvocationProvenance, SourceKind},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn contribution() -> BlenderSceneContribution {
    // Same UUID prefix as a realistic v7 collision; the complete IDs must survive.
    let first = Uuid::parse_str("0198aabb-ccdd-7000-8000-000000000001").unwrap();
    let second = Uuid::parse_str("0198aabb-ccdd-7000-8000-000000000002").unwrap();
    let object = |node_id, primitive, rgba| BlenderPrimitiveContribution {
        node_id,
        name: "Motionwright editable shape".into(),
        material_name: "legacy display only".into(),
        color_rgba: rgba,
        primitive,
        location: [1.25, -0.5, 0.01],
        rotation: [0.0, 0.0, 0.125],
        scale: [2.0, 1.0, 0.005],
    };
    BlenderSceneContribution {
        project_id: Uuid::from_u128(1),
        generation: Uuid::from_u128(2),
        revision: 7,
        scene_id: Uuid::from_u128(3),
        collection_name: "legacy-readable-collection".into(),
        objects: vec![
            object(first, BlenderPrimitive::Plane, [0.2, 0.4, 0.6, 1.0]),
            object(second, BlenderPrimitive::Cylinder, [0.7, 0.3, 0.1, 0.5]),
        ],
    }
}

fn source() -> BlenderEditableSource {
    prepare_blender_editable_source(
        "project:00000000-0000-0000-0000-000000000001",
        contribution(),
    )
    .unwrap()
}

fn canonical_project() -> motionwright_domain::Project {
    use motionwright_domain::{
        CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RendererKind,
    };
    let mut project = Project::new("Editable contract fixture").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Collection".into(),
            objective: "Check admission before native work".into(),
            duration_seconds: 2,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    project
        .apply_change(&Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::Blender,
        })
        .unwrap();
    project
        .apply_change(&Change::AddCanvasNode {
            scene_id,
            node: CanvasNode {
                id: Uuid::from_u128(10),
                name: "Editable shape".into(),
                kind: "rectangle".into(),
                parent_id: None,
                x: 360.0,
                y: 280.0,
                width: 240.0,
                height: 180.0,
                rotation_deg: 12.0,
                opacity: 1.0,
                text: None,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 1,
                style: NodeStyle {
                    fill: Some("#4F7CAC80".into()),
                    ..NodeStyle::default()
                },
                relations: vec![],
                property_locks: Default::default(),
                keyframes: vec![],
            },
        })
        .unwrap();
    project
}

#[test]
fn project_admission_rejects_controls_that_a_collection_cannot_preserve() {
    let project = canonical_project();
    let scene_id = project.scenes[0].id;
    assert!(prepare_blender_editable_project_source(&project, scene_id).is_ok());
    for pointer in [
        "/scenes/0/camera/center_x",
        "/scenes/0/camera/center_y",
        "/scenes/0/camera/zoom",
        "/scenes/0/camera/rotation_deg",
        "/scenes/0/camera/safe_margin",
    ] {
        let mut encoded = serde_json::to_value(&project).unwrap();
        let actual = encoded.pointer(pointer).unwrap().as_f64().unwrap();
        *encoded.pointer_mut(pointer).unwrap() = json!(actual + 0.1);
        let changed = serde_json::from_value(encoded).unwrap();
        assert_eq!(
            prepare_blender_editable_project_source(&changed, scene_id)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported,
            "{pointer}"
        );
    }
    for (pointer, value) in [
        ("/scenes/0/nodes/0/text", json!("authored text on a shape")),
        (
            "/scenes/0/nodes/0/style/font_family",
            json!("Authored font"),
        ),
        ("/scenes/0/nodes/0/style/font_size", json!(32.0)),
        ("/scenes/0/nodes/0/style/font_weight", json!(600)),
        ("/scenes/0/nodes/0/style/line_height", json!(1.2)),
        ("/scenes/0/nodes/0/property_locks", json!(["position"])),
    ] {
        let mut encoded = serde_json::to_value(&project).unwrap();
        *encoded.pointer_mut(pointer).unwrap() = value;
        let changed = serde_json::from_value(encoded).unwrap();
        assert_eq!(
            prepare_blender_editable_project_source(&changed, scene_id)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported,
            "{pointer}"
        );
    }
}

fn snapshot(source: &BlenderEditableSource) -> Value {
    let island = "0123456789abcdef_owned";
    let collection = format!("SW_{}_{}", &island[..12], source.spec.collection);
    let items: Vec<Value> = source.spec.entities.iter().enumerate().map(|(index, entity)| {
        let material = &source.spec.materials[index];
        let name = format!("SW_{}_{}", &island[..12], entity.id);
        let (vertices, polygons) = if index == 0 { (4, 1) } else { (64, 34) };
        json!({
            "entity":entity.id, "name":name, "type":"MESH", "native_island":island,
            "library":false, "translation":entity.transform.translation, "rotation":entity.transform.rotation,
            "scale":entity.transform.scale, "parent":null, "parent_name":null, "parent_type":"OBJECT", "parent_bone":"",
            "parent_inverse":[[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,0.0],[0.0,0.0,0.0,1.0]],
            "collections":[collection], "hidden_render":false, "hidden_viewport":false,
            "modifiers":[], "constraints":[], "action":null, "actions":[], "nla_tracks":[], "drivers":0,
            "data_name":name, "data_library":false, "data_users":1,
            "geometry_digest":"a".repeat(64), "normal_digest":"b".repeat(64),
            "normal_method":"source-polygon-normal-v1", "smooth_polygons":0, "attributes":[],
            "vertices":vertices, "polygons":polygons, "uv_layers":0,
            "materials":[{
                "id":material.id, "name":format!("SW_{}_{}", &island[..12], material.id),
                "color":material.base_color, "roughness":material.roughness, "metallic":material.metallic,
                "library":false, "base_color":material.base_color, "opacity":material.opacity,
                "emission_color":material.emission_color, "emission_strength":material.emission_strength,
                "nodes":[
                    {"name":"Principled BSDF", "type":"ShaderNodeBsdfPrincipled", "inputs":[
                        ["Base Color",material.base_color],["Alpha",material.opacity],["Roughness",material.roughness],
                        ["Metallic",material.metallic],["Emission Color",material.emission_color],["Emission Strength",0.0]
                    ]},
                    {"name":"Material Output", "type":"ShaderNodeOutputMaterial", "inputs":[]}
                ],
                "links":[["Principled BSDF","BSDF","Material Output","Surface"]], "texture_bindings":[]
            }]
        })
    }).collect();
    json!({"native_session":"source_session", "island":island, "fingerprint":"c".repeat(64), "drift":false,
        "total":items.len(), "items":items, "source_only":true, "exhaustive":true})
}

#[test]
fn complete_logical_ids_survive_the_native_alias_budget() {
    let source = source();
    assert_eq!(source.spec.entities.len(), 2);
    assert_ne!(source.spec.entities[0].id, source.spec.entities[1].id);
    for (entity, original) in source
        .spec
        .entities
        .iter()
        .zip(&source.canonical_contribution.objects)
    {
        assert!(entity.id.contains(&original.node_id.simple().to_string()));
        assert!(entity.id.len() <= 40);
        assert!(entity.materials[0].contains(&original.node_id.simple().to_string()));
        assert!(entity.materials[0].len() <= 40);
    }
    assert!(source.spec.collection.len() <= 40);
}

#[test]
fn effective_alpha_is_an_explicit_material_parameter() {
    let source = source();
    assert_eq!(source.spec.materials[1].opacity, 0.5);
    assert_eq!(source.spec.materials[1].base_color[3], 0.5);
    let fixture = snapshot(&source);
    assert!(verify_blender_editable_snapshot(&source, &fixture).is_ok());
    let mut wrong = fixture;
    wrong["items"][1]["materials"][0]["opacity"] = json!(1.0);
    assert!(verify_blender_editable_snapshot(&source, &wrong).is_err());
}

#[test]
fn bounded_source_rejects_identity_name_channel_and_transform_errors() {
    let baseline = contribution();
    let mut duplicate = baseline.clone();
    duplicate.objects[1].node_id = duplicate.objects[0].node_id;
    assert_eq!(
        prepare_blender_editable_source("project:one", duplicate)
            .unwrap_err()
            .code,
        ErrorCode::InvalidArgument
    );
    for name in ["x".repeat(81), "bad\nname".into(), String::new()] {
        let mut changed = baseline.clone();
        changed.objects[0].name = name;
        assert!(prepare_blender_editable_source("project:one", changed).is_err());
    }
    for value in [f64::NAN, f64::INFINITY, -0.01, 1.01] {
        let mut changed = baseline.clone();
        changed.objects[0].color_rgba[0] = value;
        assert!(prepare_blender_editable_source("project:one", changed).is_err());
    }
    let mut singular = baseline.clone();
    singular.objects[0].scale[2] = 0.0;
    assert!(prepare_blender_editable_source("project:one", singular).is_err());
    let mut too_many = baseline.clone();
    too_many.objects = (0..65)
        .map(|n| {
            let mut object = baseline.objects[0].clone();
            object.node_id = Uuid::from_u128(100 + n);
            object
        })
        .collect();
    assert_eq!(
        prepare_blender_editable_source("project:one", too_many)
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
}

#[test]
fn source_roundtrip_rejects_added_controls_or_a_tampered_projection() {
    let source = source();
    let encoded = serde_json::to_value(&source).unwrap();
    let decoded: BlenderEditableSource = serde_json::from_value(encoded.clone()).unwrap();
    assert_eq!(decoded, source);
    let mut unknown = encoded.clone();
    unknown["spec"]["python"] = json!("excluded");
    assert!(serde_json::from_value::<BlenderEditableSource>(unknown).is_err());
    let mut unsupported = encoded;
    unsupported["spec"]["entities"][0]["shape"] = json!({"kind":"camera", "lens_mm":50.0});
    assert!(serde_json::from_value::<BlenderEditableSource>(unsupported).is_err());
    let mut tampered = source.clone();
    tampered.spec.materials[0].opacity = 0.2;
    assert!(verify_blender_editable_snapshot(&tampered, &snapshot(&source)).is_err());
}

#[test]
fn snapshot_rejects_missing_wrong_or_uneditable_native_state() {
    let source = source();
    let valid = snapshot(&source);
    assert!(verify_blender_editable_snapshot(&source, &valid).is_ok());
    for (pointer, value) in [
        ("/drift", json!(true)),
        ("/source_only", json!(false)),
        ("/exhaustive", json!(false)),
        ("/total", json!(1)),
        ("/items/0/entity", json!("node_other")),
        ("/items/0/hidden_render", json!(true)),
        ("/items/0/library", json!(true)),
        ("/items/0/data_users", json!(2)),
        ("/items/0/drivers", json!(1)),
        ("/items/0/parent_inverse/0/3", json!(0.5)),
        ("/items/0/materials/0/name", json!("another-material")),
        (
            "/items/0/materials/0/emission_color",
            json!([1.0, 0.0, 0.0, 1.0]),
        ),
        ("/items/0/translation/0", json!(9.0)),
        ("/items/0/vertices", json!(0)),
        ("/items/0/constraints", json!([{"type":"COPY_LOCATION"}])),
        ("/items/0/collections", json!(["unowned"])),
        ("/items/0/geometry_digest", json!("not-a-sha")),
    ] {
        let mut changed = valid.clone();
        *changed.pointer_mut(pointer).unwrap() = value;
        assert!(
            verify_blender_editable_snapshot(&source, &changed).is_err(),
            "{pointer}"
        );
    }
    let mut duplicate = valid;
    duplicate["items"][1] = duplicate["items"][0].clone();
    assert!(verify_blender_editable_snapshot(&source, &duplicate).is_err());
}

#[test]
fn shader_socket_mutations_fail_when_datablock_metadata_is_unchanged() {
    let source = source();
    let valid = snapshot(&source);
    for socket in ["Alpha", "Roughness", "Metallic", "Emission Strength"] {
        let mut changed = valid.clone();
        let inputs = changed["items"][0]["materials"][0]["nodes"][0]["inputs"]
            .as_array_mut()
            .unwrap();
        let row = inputs.iter_mut().find(|row| row[0] == socket).unwrap();
        row[1] = json!(0.73);
        assert!(
            verify_blender_editable_snapshot(&source, &changed).is_err(),
            "{socket}"
        );
    }
    let mut duplicate = valid.clone();
    duplicate["items"][0]["materials"][0]["nodes"][0]["inputs"]
        .as_array_mut()
        .unwrap()
        .push(json!(["Alpha", 1.0]));
    assert!(verify_blender_editable_snapshot(&source, &duplicate).is_err());
    let mut relinked = valid;
    relinked["items"][0]["materials"][0]["links"][0][1] = json!("wrong_output");
    assert!(verify_blender_editable_snapshot(&source, &relinked).is_err());
}

#[test]
fn reopen_requires_a_different_session_and_exact_preserved_source() {
    let source = source();
    let original_json = snapshot(&source);
    let original = verify_blender_editable_snapshot(&source, &original_json).unwrap();
    assert!(verify_blender_editable_reopen(&source, &original, &original_json).is_err());
    let mut reopened = original_json;
    reopened["native_session"] = json!("fresh_native_session");
    assert!(verify_blender_editable_reopen(&source, &original, &reopened).is_ok());
    for (pointer, value) in [
        ("/fingerprint", json!("d".repeat(64))),
        ("/items/0/geometry_digest", json!("e".repeat(64))),
        ("/items/0/materials/0/name", json!("silently_renamed")),
    ] {
        let mut changed = reopened.clone();
        *changed.pointer_mut(pointer).unwrap() = value;
        assert!(
            verify_blender_editable_reopen(&source, &original, &changed).is_err(),
            "{pointer}"
        );
    }
}

fn synthetic_sdk_execution(dry_run: bool, untrusted_metadata: bool) -> Execution {
    // Real public SDK types; values remain explicitly synthetic. This helper
    // exercises serde contracts and never fabricates a provider-issued receipt.
    Execution {
        backend: "driver:blender".into(),
        duration_ms: 1,
        policy_decision: "allow".into(),
        fallbacks_attempted: vec![],
        provenance: Some(InvocationProvenance {
            provider: "driver:blender".into(),
            source: SourceKind::Driver,
            provider_version: "synthetic-contract-fixture".into(),
            capability_version: "synthetic-contract-fixture".into(),
            descriptor_sha256: "b".repeat(64),
            untrusted_metadata,
            catalog_revision: 1,
            execution_provider: Some("driver:blender".into()),
            provider_generation: Some(17),
        }),
        dry_run,
    }
}

#[test]
fn sdk_serde_preserves_explicit_false_execution_and_provenance_flags() {
    let encoded = serde_json::to_value(synthetic_sdk_execution(false, false)).unwrap();
    assert_eq!(encoded.get("dry_run").and_then(Value::as_bool), Some(false));
    assert_eq!(
        encoded
            .pointer("/provenance/untrusted_metadata")
            .and_then(Value::as_bool),
        Some(false),
    );
    let decoded: Execution = serde_json::from_value(encoded.clone()).unwrap();
    assert!(!decoded.dry_run);
    assert!(!decoded.provenance.unwrap().untrusted_metadata);

    // Missing booleans cannot silently acquire a trusted default during typed
    // deserialization. The consumer also rejects absence in a Value envelope.
    let mut missing_dry_run = encoded.clone();
    missing_dry_run.as_object_mut().unwrap().remove("dry_run");
    assert!(serde_json::from_value::<Execution>(missing_dry_run).is_err());
    let mut missing_untrusted = encoded;
    missing_untrusted["provenance"]
        .as_object_mut()
        .unwrap()
        .remove("untrusted_metadata");
    assert!(serde_json::from_value::<Execution>(missing_untrusted).is_err());
}

fn synthetic_custody(source: &BlenderEditableSource) -> (BlenderEditableStageEvidence, Vec<Value>) {
    let result = json!({"data":{"native_session":"contract-fixture"}, "provenance_checked":true,
        "execution":synthetic_sdk_execution(false, false), "warnings":[]});
    let mut row = json!({"project_id":source.canonical_contribution.project_id,
        "generation":source.canonical_contribution.generation, "revision":source.canonical_contribution.revision,
        "request_id":"synthetic-create-inspect", "request_sha256":"c".repeat(64),
        "command":"driver.blender.composition.inspect", "stage":"dispatching",
        "payload":{"connection":"a".repeat(64), "mutation":false, "job_ref":null}});
    let dispatch = row.clone();
    row["stage"] = json!("completed");
    row["payload"] =
        json!({"result":result, "broker_request_id":"synthetic-not-native", "job_ref":null});
    let response = json!({"replayed":false, "result":result, "receipt":row});
    let proof = BlenderEditableStageEvidence {
        stage: "create-inspect".into(),
        command: "driver.blender.composition.inspect".into(),
        request_id: "synthetic-create-inspect".into(),
        request_sha256: "c".repeat(64),
        connection_identity: "a".repeat(64),
        provider_generation: 17,
        descriptor_sha256: "b".repeat(64),
        response,
    };
    (proof, vec![dispatch, row])
}

#[test]
fn custody_rejects_dry_run_untrusted_or_missing_flags_even_in_matching_rows() {
    let source = source();
    let (valid, journal) = synthetic_custody(&source);
    assert!(verify_blender_editable_stage_custody(&source, &valid, &journal).is_ok());
    for (dry_run, untrusted_metadata) in [(true, false), (false, true), (true, true)] {
        let mut changed = valid.clone();
        let mut changed_rows = journal.clone();
        changed.response["result"]["execution"] =
            serde_json::to_value(synthetic_sdk_execution(dry_run, untrusted_metadata)).unwrap();
        changed_rows[1]["payload"]["result"] = changed.response["result"].clone();
        changed.response["receipt"] = changed_rows[1].clone();
        assert!(verify_blender_editable_stage_custody(&source, &changed, &changed_rows).is_err());
    }
    for (parent, field) in [
        ("/result/execution", "dry_run"),
        ("/result/execution/provenance", "untrusted_metadata"),
    ] {
        for replacement in [None, Some(Value::Null), Some(json!("false"))] {
            let mut changed = valid.clone();
            let mut changed_rows = journal.clone();
            let object = changed
                .response
                .pointer_mut(parent)
                .unwrap()
                .as_object_mut()
                .unwrap();
            if let Some(value) = replacement {
                object.insert(field.into(), value);
            } else {
                object.remove(field);
            }
            changed_rows[1]["payload"]["result"] = changed.response["result"].clone();
            changed.response["receipt"] = changed_rows[1].clone();
            assert!(
                verify_blender_editable_stage_custody(&source, &changed, &changed_rows).is_err()
            );
        }
    }
}

#[test]
fn genuine_receipt_pairs_do_not_authenticate_a_forged_wrapped_result() {
    let source = source();
    let (valid, journal) = synthetic_custody(&source);
    assert!(verify_blender_editable_stage_custody(&source, &valid, &journal).is_ok());
    let mut changed = valid.clone();
    changed.response["result"]["data"]["native_session"] = json!("forged");
    assert!(verify_blender_editable_stage_custody(&source, &changed, &journal).is_err());
    let mut changed = valid.clone();
    changed.provider_generation += 1;
    assert!(verify_blender_editable_stage_custody(&source, &changed, &journal).is_err());
    let mut changed = valid.clone();
    changed.descriptor_sha256 = "d".repeat(64);
    assert!(verify_blender_editable_stage_custody(&source, &changed, &journal).is_err());
    let mut changed = valid;
    changed.stage = "source-reopen".into();
    assert!(verify_blender_editable_stage_custody(&source, &changed, &journal).is_err());
}

#[test]
fn custody_rejects_unknown_duplicate_cross_generation_and_missing_journal_rows() {
    let source = source();
    let (valid, journal) = synthetic_custody(&source);
    let mut unknown = journal.clone();
    let mut row = unknown[1].clone();
    row["stage"] = json!("outcome_unknown");
    unknown.push(row);
    assert!(verify_blender_editable_stage_custody(&source, &valid, &unknown).is_err());
    let mut duplicate = journal.clone();
    duplicate.push(duplicate[0].clone());
    assert!(verify_blender_editable_stage_custody(&source, &valid, &duplicate).is_err());
    assert!(verify_blender_editable_stage_custody(&source, &valid, &journal[1..]).is_err());
    let mut wrong = source;
    wrong.canonical_contribution.generation = Uuid::from_u128(99);
    assert!(verify_blender_editable_stage_custody(&wrong, &valid, &journal).is_err());
}

fn synthetic_full_chain() -> (BlenderEditableEvidence, Vec<Value>) {
    synthetic_full_chain_for_source(source())
}

fn synthetic_full_chain_for_source(
    source: BlenderEditableSource,
) -> (BlenderEditableEvidence, Vec<Value>) {
    let after = snapshot(&source);
    let island = after["island"].as_str().unwrap().to_owned();
    let base = |generation: &str, fingerprint: &str| {
        json!([{
            "key":{"provider":"driver:blender", "resource":"authoring-workspace"},
            "document_id":"blender-authoring-workspace", "provider_session":"synthetic-owner",
            "generation":generation, "revision":{"kind":"fingerprint", "value":fingerprint},
            "concurrency":"best_effort_revalidate",
        }])
    };
    let initial_fingerprint = "e".repeat(64);
    let initial = json!({"native_session":"source_session", "island":null, "fingerprint":initial_fingerprint,
        "drift":false, "total":0, "items":[], "source_only":true, "exhaustive":true});
    let intent = json!({"kind":"create", "spec":source.spec});
    let address = json!({"resource":{"provider":"driver:blender", "resource":"authoring-workspace"},
        "logical_id":"synthetic-observation", "property":"source-projection"});
    let preservation = json!({"resource":address["resource"], "logical_id":"unmanaged-scene", "property":"source-projection"});
    let plan_digest = "d".repeat(64);
    let required = json!([
        "blender.plan-native-readback.v1",
        "blender.unmanaged-preservation.v1"
    ]);
    let plan = json!({"plan_ref":"synthetic-plan", "plan":{"digest":plan_digest, "body":{
        "owner":{"session":"synthetic-owner", "principal":"host_session"},
        "profile":{"id":"blender-native-authoring", "version":1},
        "base":base("source_session", &initial_fingerprint), "intent":intent, "required_rules":required,
        "changes":{"operations":[{"writes":[address]}]},
    }}});
    let post_base = base("source_session", after["fingerprint"].as_str().unwrap());
    let checks: Vec<Value> = [
        ("blender.plan-native-readback.v1", "blender-bounded-source-rna", &address),
        ("blender.unmanaged-preservation.v1", "blender-unmanaged-source-projection", &preservation),
    ].into_iter().map(|(rule, method, scope)| json!({
        "rule":rule, "version":1, "verdict":"PASS", "evidence_class":"DETERMINISTIC", "reason":null,
        "evidence":[{"id":format!("obs-{rule}"), "base":post_base, "source":"native_api", "method":method,
            "method_version":1, "scope":[scope], "artifact":null, "exhaustive":true}],
    })).collect();
    let report = json!({"execution_status":"completed", "support_level":"native",
        "effects_observed":[address], "effects_unobservable":[],
        "validation":{"plan_digest":plan_digest, "base":post_base, "required_rules":required, "checks":checks}});
    let prefix = "synthetic-attempt";
    let attempt = format!("{:x}", Sha256::digest(prefix.as_bytes()));
    let path = format!(
        "blender-editable-{}-{}-{}.blend",
        source.canonical_contribution.scene_id.simple(),
        &source.source_sha256[..16],
        &attempt[..16]
    );
    let persisted = json!({"path":path, "sha256":"f".repeat(64), "bytes":1234, "island":island,
        "source_fingerprint":after["fingerprint"], "method":"libraries-write-indirect-closure", "fresh_process_verified":false,
        "inventory":{"collection":after["items"][0]["collections"][0], "objects":after["items"].as_array().unwrap()
            .iter().map(|row| row["name"].clone()).collect::<Vec<_>>()}});
    let operations = [
        (
            "create-inspect",
            "driver.blender.composition.inspect",
            json!({}),
            initial,
        ),
        (
            "create-plan",
            "driver.blender.composition.plan",
            json!({"intent":intent}),
            plan,
        ),
        (
            "create-apply",
            "driver.blender.composition.apply",
            json!({"plan_ref":"synthetic-plan"}),
            json!({"plan_ref":"synthetic-plan", "island":island, "snapshot":after, "report":report}),
        ),
        (
            "create-verify",
            "driver.blender.composition.verify",
            json!({"plan_ref":"synthetic-plan"}),
            report,
        ),
        (
            "create-readback",
            "driver.blender.composition.inspect",
            json!({"island":island}),
            after.clone(),
        ),
        (
            "create-persist",
            "driver.blender.composition.persist",
            json!({"island":island, "path":path}),
            persisted.clone(),
        ),
    ];
    let mut stages = vec![];
    let mut journal = vec![];
    for (stage, command, args, data) in operations {
        let (mut proof, mut rows) = synthetic_custody(&source);
        proof.stage = stage.into();
        proof.command = command.into();
        proof.request_id = format!("{prefix}-{stage}");
        proof.request_sha256 = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&(command, &args)).unwrap())
        );
        proof.response["result"]["data"] = data;
        for row in &mut rows {
            row["request_id"] = json!(proof.request_id);
            row["command"] = json!(command);
            row["request_sha256"] = json!(proof.request_sha256);
        }
        rows[0]["payload"]["mutation"] = json!(matches!(stage, "create-apply" | "create-persist"));
        rows[1]["payload"]["result"] = proof.response["result"].clone();
        proof.response["receipt"] = rows[1].clone();
        journal.extend(rows);
        stages.push(proof);
    }
    let evidence = BlenderEditableEvidence {
        schema:"motionwright-blender-editable-evidence/1".into(), phase:BlenderEditablePhase::SourcePersisted,
        source, snapshot:serde_json::from_value(after).unwrap(), artifact:serde_json::from_value(json!({
            "relative_path":path, "sha256":persisted["sha256"], "bytes":persisted["bytes"], "island":island,
            "source_fingerprint":persisted["source_fingerprint"], "inventory":persisted["inventory"], "method":persisted["method"],
        })).unwrap(), stages, source_parameter_readback:true, fresh_process_reopened:false,
        native_transform_edit_persisted:false, independent_file_readback_verified:false,
        raster_verified:false, audio_verified:false, full_scene_verified:false, canonical_edit_imported:false,
        limitations:["collection_library_only", "static_mesh_subset_only", "normalized_color_channels_without_raster_colorimetry",
            "display_names_are_bounded_native_aliases", "scene_camera_world_render_settings_not_persisted",
            "text_fonts_animation_audio_not_in_profile", "independent_blend_topology_readback_required",
            "native_transform_edit_not_imported_into_project", "external_asset_rights_and_nle_roundtrip_not_evaluated"]
            .into_iter().map(str::to_owned).collect(),
    };
    (evidence, journal)
}

#[test]
fn full_chain_rejects_detached_artifact_snapshot_or_coverage_claims() {
    // The rows are fixtures, not provider evidence. Passing this data-only
    // helper never invokes the coordinator or creates an execution grant.
    let (valid, journal) = synthetic_full_chain();
    assert!(verify_blender_editable_evidence_custody(&valid, &journal).is_ok());
    let mut forged = valid.clone();
    forged.artifact.sha256 = "0".repeat(64);
    assert!(verify_blender_editable_evidence_custody(&forged, &journal).is_err());
    let mut forged = valid.clone();
    forged.snapshot.native_session = "detached-session".into();
    assert!(verify_blender_editable_evidence_custody(&forged, &journal).is_err());
    let mut forged = valid.clone();
    forged.independent_file_readback_verified = true;
    assert!(verify_blender_editable_evidence_custody(&forged, &journal).is_err());
    let mut forged = valid;
    forged.limitations.clear();
    assert!(verify_blender_editable_evidence_custody(&forged, &journal).is_err());
}

#[test]
fn typed_report_gate_rejects_wrong_native_method_base_or_unobservable_effects() {
    let (valid, journal) = synthetic_full_chain();
    for (pointer, value) in [
        ("/execution_status", json!("unknown")),
        ("/support_level", json!("read_only")),
        ("/effects_observed", json!([])),
        (
            "/effects_unobservable",
            json!([{"resource":{"provider":"driver:blender", "resource":"authoring-workspace"},
            "logical_id":"unobserved", "property":"source-projection"}]),
        ),
        ("/validation/checks/0/version", json!(2)),
        ("/validation/checks/0/evidence/0/source", json!("fixture")),
        (
            "/validation/checks/0/evidence/0/method",
            json!("unrelated-native-method"),
        ),
        ("/validation/checks/0/evidence/0/method_version", json!(2)),
        (
            "/validation/checks/0/evidence/0/scope/0/logical_id",
            json!("another-entity"),
        ),
        ("/validation/base/0/revision/value", json!("0".repeat(64))),
    ] {
        let mut forged = valid.clone();
        let mut forged_rows = journal.clone();
        let proof = &mut forged.stages[3];
        *proof.response["result"]["data"]
            .pointer_mut(pointer)
            .unwrap() = value;
        proof.response["receipt"]["payload"]["result"] = proof.response["result"].clone();
        forged_rows[7] = proof.response["receipt"].clone();
        assert!(verify_blender_editable_stage_custody(&forged.source, proof, &forged_rows).is_ok());
        assert!(
            verify_blender_editable_evidence_custody(&forged, &forged_rows).is_err(),
            "{pointer}"
        );
    }
}

struct StoredSourceFixture {
    // Close SQLite before dropping the temporary directory on every platform.
    service: motionwright_service::StudioService,
    project: motionwright_domain::Project,
    evidence: BlenderEditableEvidence,
    _temporary: tempfile::TempDir,
}

fn stored_source_fixture(omit_first_completion: bool) -> StoredSourceFixture {
    let temporary = tempfile::tempdir().unwrap();
    let project = canonical_project();
    let mut store =
        motionwright_storage::Store::open(temporary.path().join("blender-journal.sqlite3"))
            .unwrap();
    store.create_project(&project).unwrap();
    let service = motionwright_service::StudioService::from_store(store);
    let source = prepare_blender_editable_project_source(&project, project.scenes[0].id).unwrap();
    let (mut evidence, journal) = synthetic_full_chain_for_source(source);
    for row in journal {
        let input: motionwright_storage::ProductionReceiptInput =
            serde_json::from_value(row).unwrap();
        if omit_first_completion
            && input.stage == "completed"
            && input.request_id == evidence.stages[0].request_id
        {
            continue;
        }
        // Payloads remain synthetic; receipt IDs/timestamps and original rows
        // are created by the actual application journal API in a private DB.
        let receipt = service.append_production_receipt(input).unwrap();
        if receipt.stage == "completed" {
            let proof = evidence
                .stages
                .iter_mut()
                .find(|proof| proof.request_id == receipt.request_id)
                .unwrap();
            proof.response["receipt"] = serde_json::to_value(receipt).unwrap();
        }
    }
    StoredSourceFixture {
        service,
        project,
        evidence,
        _temporary: temporary,
    }
}

fn append_unrelated_receipts(fixture: &StoredSourceFixture, start: usize, count: usize) {
    for index in start..start + count {
        fixture
            .service
            .append_production_receipt(motionwright_storage::ProductionReceiptInput {
                project_id: fixture.project.id,
                generation: fixture.project.generation,
                revision: fixture.project.revision,
                request_id: format!("unrelated-read-{index}"),
                request_sha256: "9".repeat(64),
                command: "driver.motion-canvas.composition.inspect".into(),
                stage: "completed".into(),
                payload: json!({"scope":"unrelated-synthetic-read-fixture"}),
            })
            .unwrap();
    }
}

fn source_observation_input(
    fixture: &StoredSourceFixture,
    stage: &str,
) -> motionwright_storage::ProductionReceiptInput {
    let proof = &fixture.evidence.stages[0];
    motionwright_storage::ProductionReceiptInput {
        project_id: fixture.project.id,
        generation: fixture.project.generation,
        revision: fixture.project.revision,
        request_id: proof.request_id.clone(),
        request_sha256: proof.request_sha256.clone(),
        command: proof.command.clone(),
        stage: stage.into(),
        payload: json!({"result":proof.response["result"],
            "broker_request_id":"synthetic-journal-observation", "job_ref":null}),
    }
}

#[test]
fn stored_custody_survives_more_than_256_unrelated_original_receipts() {
    let fixture = stored_source_fixture(false);
    append_unrelated_receipts(&fixture, 0, 300);
    let before_project = serde_json::to_value(&fixture.project).unwrap();
    let before_watermark = fixture
        .service
        .production_receipt_watermark(fixture.project.id)
        .unwrap();
    assert_eq!(before_watermark.count, 312);
    let old_window: Vec<Value> = fixture
        .service
        .production_receipts(fixture.project.id, 256)
        .unwrap()
        .into_iter()
        .map(|receipt| serde_json::to_value(receipt).unwrap())
        .collect();
    assert_eq!(old_window.len(), 256);
    assert!(
        verify_blender_editable_evidence_custody(&fixture.evidence, &old_window).is_err(),
        "the former latest-256 reader really loses this intact original chain"
    );
    assert!(
        verify_blender_editable_stored_evidence_custody(&fixture.service, &fixture.evidence)
            .is_ok()
    );
    assert_eq!(
        fixture
            .service
            .production_receipt_watermark(fixture.project.id)
            .unwrap(),
        before_watermark
    );
    assert_eq!(
        serde_json::to_value(fixture.service.project(fixture.project.id).unwrap()).unwrap(),
        before_project
    );
}

#[test]
fn stored_custody_rejects_missing_rows_even_with_a_supplied_completed_receipt() {
    let fixture = stored_source_fixture(true);
    let mut caller_supplied: Vec<Value> = fixture
        .service
        .production_receipts(fixture.project.id, 256)
        .unwrap()
        .into_iter()
        .map(|receipt| serde_json::to_value(receipt).unwrap())
        .collect();
    caller_supplied.push(fixture.evidence.stages[0].response["receipt"].clone());
    assert!(
        verify_blender_editable_evidence_custody(&fixture.evidence, &caller_supplied).is_ok(),
        "a consistent synthetic JSON journal is not storage authority"
    );
    append_unrelated_receipts(&fixture, 0, 300);
    assert!(
        verify_blender_editable_stored_evidence_custody(&fixture.service, &fixture.evidence)
            .is_err()
    );
}

#[test]
fn stored_custody_rejects_unknown_without_appending_a_repair_or_dispatch() {
    // The same assertions cover a missing completion and an older UNKNOWN
    // hidden behind complete newer pairs plus an extra known observation.
    for newer_pairs_before_unknown in [false, true] {
        let mut fixture = stored_source_fixture(!newer_pairs_before_unknown);
        let mut unknown = source_observation_input(&fixture, "outcome_unknown");
        if newer_pairs_before_unknown {
            let proof = fixture.evidence.stages.last().unwrap();
            unknown.request_id = proof.request_id.clone();
            unknown.request_sha256 = proof.request_sha256.clone();
            unknown.command = proof.command.clone();
        }
        unknown.payload = json!({"scope":"synthetic-uncertain-observation", "outcome_known":false});
        let original_unknown = fixture.service.append_production_receipt(unknown).unwrap();
        append_unrelated_receipts(&fixture, 0, 300);
        if newer_pairs_before_unknown {
            // Append trusted-store rows, not caller JSON authority. These
            // duplicates must never justify accepting or replaying the job.
            let rows = stored_journal_rows(&fixture);
            for proof in &mut fixture.evidence.stages {
                for stage in ["dispatching", "completed"] {
                    let original = rows
                        .iter()
                        .find(|row| row["request_id"] == proof.request_id && row["stage"] == stage)
                        .unwrap();
                    let input = motionwright_storage::ProductionReceiptInput {
                        project_id: fixture.project.id,
                        generation: fixture.project.generation,
                        revision: fixture.project.revision,
                        request_id: proof.request_id.clone(),
                        request_sha256: proof.request_sha256.clone(),
                        command: proof.command.clone(),
                        stage: stage.into(),
                        payload: original["payload"].clone(),
                    };
                    let receipt = fixture.service.append_production_receipt(input).unwrap();
                    if stage == "completed" {
                        proof.response["receipt"] = serde_json::to_value(receipt).unwrap();
                    }
                }
            }
            fixture
                .service
                .append_production_receipt(source_observation_input(&fixture, "completed"))
                .unwrap();
        }
        let before = fixture
            .service
            .production_receipt_watermark(fixture.project.id)
            .unwrap();
        let before_rows = stored_journal_rows(&fixture);
        assert!(before_rows.len() > 256);
        let error =
            verify_blender_editable_stored_evidence_custody(&fixture.service, &fixture.evidence)
                .unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(
            !error.outcome_known,
            "an original UNKNOWN must retain its uncertainty"
        );
        assert_eq!(
            fixture
                .service
                .production_receipt_watermark(fixture.project.id)
                .unwrap(),
            before
        );
        assert_eq!(
            stored_journal_rows(&fixture),
            before_rows,
            "classification must neither append repair/dispatch rows nor rewrite the original journal"
        );
        assert!(before_rows.iter().any(
            |row| row["id"] == json!(original_unknown.id) && row["stage"] == "outcome_unknown"
        ));
        assert_eq!(
            serde_json::to_value(fixture.service.project(fixture.project.id).unwrap()).unwrap(),
            serde_json::to_value(&fixture.project).unwrap()
        );
    }
}

#[test]
fn stored_custody_rejects_a_buried_duplicate_completed_observation() {
    let fixture = stored_source_fixture(false);
    fixture
        .service
        .append_production_receipt(source_observation_input(&fixture, "completed"))
        .unwrap();
    append_unrelated_receipts(&fixture, 0, 300);
    let before = fixture
        .service
        .production_receipt_watermark(fixture.project.id)
        .unwrap();
    assert_eq!(before.count, 313);
    assert!(
        verify_blender_editable_stored_evidence_custody(&fixture.service, &fixture.evidence)
            .is_err()
    );
    assert_eq!(
        fixture
            .service
            .production_receipt_watermark(fixture.project.id)
            .unwrap(),
        before
    );
}

#[test]
fn stored_custody_rejects_a_real_receipt_from_another_project() {
    let mut fixture = stored_source_fixture(false);
    let other = fixture
        .service
        .create_project("Foreign journal fixture")
        .unwrap();
    let mut input = source_observation_input(&fixture, "completed");
    input.project_id = other.id;
    input.generation = other.generation;
    input.revision = other.revision;
    let foreign = fixture.service.append_production_receipt(input).unwrap();
    assert_ne!(foreign.project_id, fixture.project.id);
    fixture.evidence.stages[0].response["receipt"] = serde_json::to_value(foreign).unwrap();
    append_unrelated_receipts(&fixture, 0, 300);
    assert!(
        verify_blender_editable_stored_evidence_custody(&fixture.service, &fixture.evidence)
            .is_err()
    );
}

#[test]
fn snapshot_page_rejects_journal_growth_even_when_project_revision_is_unchanged() {
    let fixture = stored_source_fixture(false);
    append_unrelated_receipts(&fixture, 0, 300);
    let snapshot = fixture
        .service
        .production_receipt_watermark(fixture.project.id)
        .unwrap();
    let first = fixture
        .service
        .production_receipts_snapshot_page(fixture.project.id, &snapshot, None, 256)
        .unwrap();
    assert_eq!(first.len(), 256);
    append_unrelated_receipts(&fixture, 300, 1);
    let error = fixture
        .service
        .production_receipts_snapshot_page(
            fixture.project.id,
            &snapshot,
            first.last().map(|row| row.id),
            256,
        )
        .unwrap_err();
    assert!(matches!(
        error,
        motionwright_storage::StorageError::ReceiptStreamChanged
    ));
    assert_eq!(
        serde_json::to_value(fixture.service.project(fixture.project.id).unwrap()).unwrap(),
        serde_json::to_value(&fixture.project).unwrap()
    );
}

fn stored_journal_rows(fixture: &StoredSourceFixture) -> Vec<Value> {
    let watermark = fixture
        .service
        .production_receipt_watermark(fixture.project.id)
        .unwrap();
    let mut before = None;
    let mut rows = Vec::new();
    while rows.len() < usize::try_from(watermark.count).unwrap() {
        let page = fixture
            .service
            .production_receipts_snapshot_page(fixture.project.id, &watermark, before, 256)
            .unwrap();
        assert!(!page.is_empty());
        before = page.last().map(|row| row.id);
        rows.extend(
            page.into_iter()
                .map(|row| serde_json::to_value(row).unwrap()),
        );
    }
    assert_eq!(rows.len(), usize::try_from(watermark.count).unwrap());
    assert_eq!(
        fixture
            .service
            .production_receipt_watermark(fixture.project.id)
            .unwrap(),
        watermark
    );
    rows
}

#[test]
fn data_only_custody_classifies_every_selected_unknown_before_pair_errors() {
    let (evidence, mut journal) = synthetic_full_chain();
    // The first request is already invalid before the later request is reached.
    journal.push(evidence.stages[0].response["receipt"].clone());
    let last = evidence.stages.last().unwrap();
    let mut unknown = last.response["receipt"].clone();
    unknown["stage"] = json!("outcome_unknown");
    unknown["payload"] = json!({"outcome_known":false});
    journal.push(unknown);
    let before = journal.clone();
    for error in [
        verify_blender_editable_evidence_custody(&evidence, &journal).unwrap_err(),
        verify_blender_editable_stage_custody(&evidence.source, last, &journal).unwrap_err(),
    ] {
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(!error.outcome_known);
    }
    assert_eq!(journal, before);
}

#[test]
fn stored_custody_does_not_reclassify_known_denial_as_unknown() {
    let fixture = stored_source_fixture(true);
    let mut denied = source_observation_input(&fixture, "failed_known");
    denied.payload = json!({"error":{"code":"policy_denied", "outcome_known":true}});
    fixture.service.append_production_receipt(denied).unwrap();
    let before = stored_journal_rows(&fixture);
    let error =
        verify_blender_editable_stored_evidence_custody(&fixture.service, &fixture.evidence)
            .unwrap_err();
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert!(
        error.outcome_known,
        "known denial is a failed proof, not an uncertain native mutation"
    );
    assert_eq!(stored_journal_rows(&fixture), before);
}
