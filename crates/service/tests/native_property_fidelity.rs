use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;

/// Valid minimal binary GLB v2; the asset is imported through ordinary
/// source-content CAS admission, never created by forging a hash in Project.
fn write_minimal_original_glb(path: &std::path::Path) {
    let mut json = br#"{"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[]}]}"#.to_vec();
    while !json.len().is_multiple_of(4) {
        json.push(b' ');
    }
    let length: u32 = u32::try_from(json.len()).unwrap();
    let total = 12_u32 + 8 + length;
    let mut document = Vec::with_capacity(total as usize);
    document.extend_from_slice(b"glTF");
    document.extend_from_slice(&2_u32.to_le_bytes());
    document.extend_from_slice(&total.to_le_bytes());
    document.extend_from_slice(&length.to_le_bytes());
    document.extend_from_slice(b"JSON");
    document.extend_from_slice(&json);
    std::fs::write(path, document).unwrap();
}

#[test]
fn native_capsule_property_negotiation_survives_sqlite_reopen_without_changing_glb() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("native-source.sqlite3");
    let original = temp.path().join("original.glb");
    write_minimal_original_glb(&original);
    let exact_bytes = std::fs::read(&original).unwrap();
    let service = StudioService::open(&database).unwrap();
    let mut project = service
        .create_project("Original GLB editor source")
        .unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "add-scene",
            &Change::AddScene {
                name: "Native camera study".into(),
                objective: "A user can review imported property loss before translation".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "admit-original-glb",
            &original,
            "original.glb".into(),
            "model/gltf-binary".into(),
        )
        .unwrap()
        .project;
    let asset = project.assets.first().unwrap().clone();
    let capsule = NativeCapsule {
        id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        label: "Owner's original GLB".into(),
        native_editor_hint: "Blender original source".into(),
        fidelity: FidelityReport {
            renderer: "blender".into(),
            renderer_version: "not-measured".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Unavailable,
            losses: vec!["Opaque imported source; no native effect admitted".into()],
            evidence_sha256: None,
        },
        editable_parameters: vec![],
        property_fidelity: vec![
            NativePropertyFidelity {
                path: "camera.focal_length".into(),
                fidelity: Fidelity::Baked,
                loss_note: Some("Target timeline would not retain editable lens metadata".into()),
            },
            NativePropertyFidelity {
                path: "geometry.mesh".into(),
                fidelity: Fidelity::Native,
                loss_note: None,
            },
        ],
    };
    let stamp_before = project.stamp();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "record-property-review",
            &Change::UpsertNativeCapsule {
                capsule: capsule.clone(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(project.revision, stamp_before.revision + 1);
    assert_eq!(project.production_design.capsules[0], capsule);
    assert_eq!(project.assets[0], asset);
    assert_eq!(std::fs::read(&original).unwrap(), exact_bytes);
    assert!(
        service
            .apply(
                project.id,
                &stamp_before,
                "stale-agent",
                &Change::UpsertNativeCapsule {
                    capsule: capsule.clone()
                },
            )
            .is_err()
    );
    drop(service);
    let reopened = StudioService::open(&database)
        .unwrap()
        .project(project.id)
        .unwrap();
    assert_eq!(reopened, project);
    assert_eq!(
        reopened.production_design.capsules[0].property_fidelity,
        capsule.property_fidelity
    );
    assert!(
        reopened.production_design.capsules[0]
            .editable_parameters
            .is_empty()
    );
    assert!(
        reopened.production_design.capsules[0]
            .fidelity
            .evidence_sha256
            .is_none()
    );
}

#[test]
fn tampered_source_digest_is_rejected_even_when_loss_notes_are_present() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("source.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = service
        .create_project("Source-backed native report")
        .unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "scene",
            &Change::AddScene {
                name: "Camera shot".into(),
                objective: "Save scene before source".into(),
                duration_seconds: 5,
            },
        )
        .unwrap()
        .project;
    let original = temp.path().join("original.glb");
    write_minimal_original_glb(&original);
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "original",
            &original,
            "original.glb".into(),
            "model/gltf-binary".into(),
        )
        .unwrap()
        .project;
    let saved = project.clone();
    let asset = project.assets[0].clone();
    let c = NativeCapsule {
        id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        source_asset_id: asset.id,
        source_sha256: "b".repeat(64),
        label: "Wrong imported fingerprint".into(),
        native_editor_hint: "Unknown tool".into(),
        fidelity: FidelityReport {
            renderer: "blender".into(),
            renderer_version: "unknown".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Unavailable,
            losses: vec!["No native provider verified".into()],
            evidence_sha256: None,
        },
        editable_parameters: vec![],
        property_fidelity: vec![NativePropertyFidelity {
            path: "geometry.mesh".into(),
            fidelity: Fidelity::Native,
            loss_note: None,
        }],
    };
    assert!(
        service
            .apply(
                project.id,
                &project.stamp(),
                "tampered",
                &Change::UpsertNativeCapsule { capsule: c }
            )
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), saved);
}
