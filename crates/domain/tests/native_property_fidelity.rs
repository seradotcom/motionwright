use motionwright_domain::*;
use uuid::Uuid;

fn base() -> (Project, NativeCapsule) {
    let mut project = Project::new("Real editable native source attachment").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Original native shot".into(),
            objective: "Preserve the authored GLB without pretending to render it".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "Original owner file".into(),
        media_type: "model/gltf-binary".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: Some("immutable-source".into()),
    };
    project
        .apply_change(&Change::AddAsset {
            asset: asset.clone(),
        })
        .unwrap();
    let capsule = NativeCapsule {
        id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.unwrap(),
        label: "Source GLB without lossy import".into(),
        native_editor_hint: "Owner Blender source".into(),
        fidelity: FidelityReport {
            renderer: "blender".into(),
            renderer_version: "unverified".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Unavailable,
            losses: vec!["An opaque GLB attachment is not renderer admission".into()],
            evidence_sha256: None,
        },
        editable_parameters: vec![],
        property_fidelity: vec![],
    };
    (project, capsule)
}
fn property(path: &str, fidelity: Fidelity, note: Option<&str>) -> NativePropertyFidelity {
    NativePropertyFidelity {
        path: path.to_owned(),
        fidelity,
        loss_note: note.map(str::to_owned),
    }
}

#[test]
fn one_property_classification_roundtrips_without_allocating_a_domain_revision() {
    let (mut p, mut c) = base();
    let source = p.assets[0].clone();
    let before = p.revision;
    c.property_fidelity.push(property(
        "camera.focal_length",
        Fidelity::Baked,
        Some("The native focal length is retained in source, not editable in the timeline"),
    ));
    c.property_fidelity
        .push(property("geometry.mesh", Fidelity::Native, None));
    p.apply_change(&Change::UpsertNativeCapsule { capsule: c.clone() })
        .unwrap();
    // In-memory Project::apply_change validates and stages a candidate, but
    // does not allocate a persistent revision. The StudioService SQLite CAS
    // contract owns the single revision increment, tested separately by
    // crates/service/tests/native_property_fidelity.rs against a real GLB.
    assert_eq!(p.revision, before);
    let saved = &p.production_design.capsules[0];
    assert_eq!(saved.property_fidelity, c.property_fidelity);
    assert_eq!(saved.source_sha256, source.content_sha256.unwrap());
    assert_eq!(saved.editable_parameters, Vec::<String>::new());
    assert_eq!(saved.fidelity.editable, Fidelity::Unavailable);
    assert!(saved.fidelity.evidence_sha256.is_none());
    assert!(p.production_design.capsules[0].fidelity.visual == Fidelity::Unavailable);

    let decoded: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    assert_eq!(decoded, p);
    decoded.validate().unwrap();
}

#[test]
fn updating_one_source_property_does_not_authorize_code_execution() {
    let (mut p, mut c) = base();
    p.apply_change(&Change::UpsertNativeCapsule { capsule: c.clone() })
        .unwrap();
    let asset = p.assets[0].clone();
    let scene = p.scenes[0].clone();
    c.property_fidelity = vec![property(
        "animation.keyframes",
        Fidelity::Approximate,
        Some("Frame-rate retargeting may change easing curves"),
    )];
    p.apply_change(&Change::UpsertNativeCapsule { capsule: c.clone() })
        .unwrap();
    assert_eq!(p.assets[0], asset);
    assert_eq!(p.scenes[0], scene);
    assert_eq!(
        p.production_design.capsules[0].property_fidelity,
        c.property_fidelity
    );
    let snapshot = serde_json::to_string(&p.production_design.capsules[0]).unwrap();
    assert!(!snapshot.contains("shell"));
    assert!(!snapshot.contains("effect_grant_token"));
    assert!(!snapshot.contains("render_pass"));
}

#[test]
fn malformed_semantic_paths_and_prototype_keys_fail_closed() {
    for candidate in [
        "",
        ".camera",
        "camera.",
        "geometry..mesh",
        "0camera.value",
        "camera-value",
        "__proto__",
        "camera.__proto__",
        "geometry.constructor",
        "geometry.prototype.material",
        "a.b.c.d.e.f",
    ] {
        assert!(
            property(candidate, Fidelity::Native, None)
                .validate()
                .is_err(),
            "{candidate}"
        );
    }
    assert!(
        property(&"x".repeat(129), Fidelity::Native, None)
            .validate()
            .is_err()
    );
    assert!(
        property("camera.zoom", Fidelity::Native, None)
            .validate()
            .is_ok()
    );
    assert!(
        property("materials.base_color", Fidelity::Translated, None)
            .validate()
            .is_ok()
    );
}

#[test]
fn absent_or_misleading_loss_explanations_are_rejected() {
    for fidelity in [
        Fidelity::Baked,
        Fidelity::Approximate,
        Fidelity::Unavailable,
    ] {
        assert!(property("camera.zoom", fidelity, None).validate().is_err());
        assert!(
            property("camera.zoom", fidelity, Some(""))
                .validate()
                .is_err()
        );
        assert!(
            property("camera.zoom", fidelity, Some("   "))
                .validate()
                .is_err()
        );
        assert!(
            property(
                "camera.zoom",
                fidelity,
                Some("A source property is not available after export")
            )
            .validate()
            .is_ok()
        );
    }
    assert!(
        property(
            "geometry.mesh",
            Fidelity::Native,
            Some("No claimed render readback")
        )
        .validate()
        .is_ok()
    );
    assert!(
        property("geometry.mesh", Fidelity::Translated, Some("valid\0bad"))
            .validate()
            .is_err()
    );
    assert!(
        property("geometry.mesh", Fidelity::Baked, Some(&"x".repeat(501)))
            .validate()
            .is_err()
    );
}

#[test]
fn duplicate_or_oversized_property_classifications_do_not_change_revision() {
    let (mut p, mut c) = base();
    let old = p.clone();
    c.property_fidelity = vec![
        property("camera.zoom", Fidelity::Native, None),
        property(
            "camera.zoom",
            Fidelity::Baked,
            Some("Flattened camera parameter"),
        ),
    ];
    assert!(
        p.apply_change(&Change::UpsertNativeCapsule { capsule: c.clone() })
            .is_err()
    );
    assert_eq!(p, old);
    c.property_fidelity = (0..65)
        .map(|i| property(&format!("camera.p{i}"), Fidelity::Native, None))
        .collect();
    assert!(
        p.apply_change(&Change::UpsertNativeCapsule { capsule: c })
            .is_err()
    );
    assert_eq!(p, old);
}

#[test]
fn legacy_opaque_capsules_still_deserialize_and_keep_same_serialization() {
    let (_, c) = base();
    let old = serde_json::to_value(&c).unwrap();
    assert!(old.get("property_fidelity").is_none());
    let legacy: NativeCapsule = serde_json::from_value(old.clone()).unwrap();
    assert!(legacy.property_fidelity.is_empty());
    assert_eq!(serde_json::to_value(legacy).unwrap(), old);
}

#[test]
fn property_negotiation_cannot_elevate_source_capsule_without_valid_asset() {
    let (mut p, mut c) = base();
    c.property_fidelity
        .push(property("camera.zoom", Fidelity::Translated, None));
    c.source_sha256 = "b".repeat(64);
    assert!(
        p.apply_change(&Change::UpsertNativeCapsule { capsule: c })
            .is_err()
    );
    assert!(p.production_design.capsules.is_empty());
}

#[test]
fn unfamiliar_property_metadata_fields_cannot_disguise_execution_permissions() {
    let (_, mut c) = base();
    c.property_fidelity.push(property(
        "camera.focal_length",
        Fidelity::Unavailable,
        Some("No approved native camera operation"),
    ));
    let mut serde = serde_json::to_value(c).unwrap();
    serde["property_fidelity"][0]["execute_native_command"] = serde_json::json!("rm");
    assert!(serde_json::from_value::<NativeCapsule>(serde).is_err());
}
