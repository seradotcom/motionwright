//! Direct-change guards for the atomic source operation.
//! These tests do not execute a renderer or represent human authorization.
use motionwright_domain::{
    Asset, Change, Fidelity, LockKind, NativeCapsule, NativeCapsuleSourceSpec,
    NativePropertyFidelity, Project,
};
use uuid::Uuid;

fn fixture() -> (Project, Asset, NativeCapsuleSourceSpec) {
    let mut project = Project::new("Atomic source domain fixture").unwrap();
    for name in ["Original scene", "Other scene"] {
        project
            .apply_change(&Change::AddScene {
                name: name.into(),
                objective: "Preserve source binding".into(),
                duration_seconds: 3,
            })
            .unwrap();
    }
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "Authored source".into(),
        media_type: "application/json".into(),
        content_sha256: Some("1".repeat(64)),
        source_revision: Some("native-capsule-source-import".into()),
    };
    let spec = NativeCapsuleSourceSpec {
        name: asset.name.clone(),
        media_type: asset.media_type.clone(),
        capsule_id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        label: "Editable authored source".into(),
        editable_parameters: vec!["label".into()],
        native_editor_hint: "Synthetic editor".into(),
    };
    (project, asset, spec)
}

fn apply(project: &mut Project, asset: &Asset, capsule: &NativeCapsule) {
    project
        .apply_change(&Change::ImportNativeCapsuleSource {
            asset: asset.clone(),
            capsule: capsule.clone(),
        })
        .unwrap();
}

#[track_caller]
fn reject_unchanged(project: &mut Project, asset: &Asset, capsule: &NativeCapsule) {
    let before = serde_json::to_vec(project).unwrap();
    assert!(
        project
            .apply_change(&Change::ImportNativeCapsuleSource {
                asset: asset.clone(),
                capsule: capsule.clone(),
            })
            .is_err()
    );
    assert_eq!(serde_json::to_vec(project).unwrap(), before);
}

#[test]
fn source_asset_and_capsule_are_bound_by_a_single_change() {
    let (mut project, asset, spec) = fixture();
    let capsule = spec.bind_asset(&asset).unwrap();
    apply(&mut project, &asset, &capsule);
    assert_eq!(project.assets, vec![asset]);
    assert_eq!(project.production_design.capsules, vec![capsule]);
    project.validate().unwrap();
}

#[test]
fn replacement_keeps_old_source_and_drops_previous_native_claims() {
    let (mut project, asset, spec) = fixture();
    let first = spec.bind_asset(&asset).unwrap();
    apply(&mut project, &asset, &first);
    let mut historical = first.clone();
    historical.fidelity.visual = Fidelity::Native;
    historical.fidelity.evidence_sha256 = Some("a".repeat(64));
    historical.property_fidelity = vec![NativePropertyFidelity {
        path: "camera.focal_length".into(),
        fidelity: Fidelity::Native,
        loss_note: None,
    }];
    project
        .apply_change(&Change::UpsertNativeCapsule {
            capsule: historical,
        })
        .unwrap();
    let mut replacement = asset.clone();
    replacement.id = Uuid::new_v4();
    replacement.content_sha256 = Some("2".repeat(64));
    let next = spec.bind_asset(&replacement).unwrap();
    apply(&mut project, &replacement, &next);
    assert_eq!(project.assets, vec![asset, replacement]);
    assert_eq!(project.production_design.capsules, vec![next.clone()]);
    assert_eq!(next.fidelity.visual, Fidelity::Unavailable);
    assert!(next.fidelity.evidence_sha256.is_none());
    assert!(next.property_fidelity.is_empty());
}

#[test]
fn direct_change_cannot_inject_fidelity_evidence_or_alternate_source() {
    let (project, asset, spec) = fixture();
    let valid = spec.bind_asset(&asset).unwrap();
    let mut alterations = Vec::new();
    let mut changed = valid.clone();
    changed.fidelity.visual = Fidelity::Native;
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.temporal = Fidelity::Approximate;
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.structural = Fidelity::Native;
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.editable = Fidelity::Native;
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.evidence_sha256 = Some("a".repeat(64));
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.renderer = "claimed-real-renderer".into();
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.renderer_version = "claimed-version".into();
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.fidelity.losses.clear();
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.property_fidelity.push(NativePropertyFidelity {
        path: "camera.focal_length".into(),
        fidelity: Fidelity::Native,
        loss_note: None,
    });
    alterations.push(changed);
    let mut changed = valid.clone();
    changed.source_asset_id = Uuid::new_v4();
    alterations.push(changed);
    let mut changed = valid;
    changed.source_sha256 = "2".repeat(64);
    alterations.push(changed);
    for altered in alterations {
        reject_unchanged(&mut project.clone(), &asset, &altered);
    }
}

#[test]
fn direct_change_cannot_substitute_an_ordinary_import_or_missing_digest() {
    let (project, asset, spec) = fixture();
    let capsule = spec.bind_asset(&asset).unwrap();
    for purpose in [
        None,
        Some("local-import".into()),
        Some("voice-import".into()),
    ] {
        let mut altered = asset.clone();
        altered.source_revision = purpose;
        reject_unchanged(&mut project.clone(), &altered, &capsule);
    }
    let mut missing = asset;
    missing.content_sha256 = None;
    reject_unchanged(&mut project.clone(), &missing, &capsule);
}

#[test]
fn missing_scene_and_duplicate_asset_rejections_preserve_entire_document() {
    let (mut project, asset, mut spec) = fixture();
    let capsule = spec.bind_asset(&asset).unwrap();
    apply(&mut project, &asset, &capsule);
    reject_unchanged(&mut project, &asset, &capsule);
    let mut another = asset;
    another.id = Uuid::new_v4();
    spec.scene_id = Uuid::new_v4();
    let missing_scene = spec.bind_asset(&another).unwrap();
    reject_unchanged(&mut project, &another, &missing_scene);
}

#[test]
fn project_and_target_scene_content_locks_reject_source_save() {
    let (project, asset, spec) = fixture();
    for resource in [project.resource_key(), format!("scene:{}", spec.scene_id)] {
        let mut locked = project.clone();
        locked
            .apply_change(&Change::SetLock {
                resource,
                kind: LockKind::Content,
                note: "Protected authored content".into(),
            })
            .unwrap();
        reject_unchanged(&mut locked, &asset, &spec.bind_asset(&asset).unwrap());
    }
}

#[test]
fn moving_capsule_cannot_evade_previous_scene_content_lock() {
    let (mut project, asset, mut spec) = fixture();
    apply(&mut project, &asset, &spec.bind_asset(&asset).unwrap());
    project
        .apply_change(&Change::SetLock {
            resource: format!("scene:{}", spec.scene_id),
            kind: LockKind::Content,
            note: "Protected original source binding".into(),
        })
        .unwrap();
    spec.scene_id = project.scenes[1].id;
    let mut replacement = asset;
    replacement.id = Uuid::new_v4();
    let capsule = spec.bind_asset(&replacement).unwrap();
    reject_unchanged(&mut project, &replacement, &capsule);
}

#[test]
fn final_project_validation_failure_does_not_partially_mutate_document() {
    let (mut project, asset, spec) = fixture();
    // Exercise the guard even when a caller hands the domain a corrupt document.
    project.title.clear();
    assert!(project.validate().is_err());
    reject_unchanged(&mut project, &asset, &spec.bind_asset(&asset).unwrap());
}

#[test]
fn capsule_limit_rejects_addition_but_allows_replacement() {
    let (mut project, asset, spec) = fixture();
    apply(&mut project, &asset, &spec.bind_asset(&asset).unwrap());
    for _ in 1..256 {
        let mut capsule = project.production_design.capsules[0].clone();
        capsule.id = Uuid::new_v4();
        project.production_design.capsules.push(capsule);
    }
    project.validate().unwrap();
    let mut replacement = asset;
    replacement.id = Uuid::new_v4();
    let mut addition = spec.clone();
    addition.capsule_id = Uuid::new_v4();
    reject_unchanged(
        &mut project,
        &replacement,
        &addition.bind_asset(&replacement).unwrap(),
    );
    apply(
        &mut project,
        &replacement,
        &spec.bind_asset(&replacement).unwrap(),
    );
    assert_eq!(project.production_design.capsules.len(), 256);
}

#[test]
fn closed_spec_rejects_claims_grants_and_supplied_digest() {
    let (_, _, spec) = fixture();
    for key in [
        "fidelity",
        "grant",
        "consent",
        "source_sha256",
        "property_fidelity",
    ] {
        let mut encoded = serde_json::to_value(&spec).unwrap();
        encoded
            .as_object_mut()
            .unwrap()
            .insert(key.into(), true.into());
        assert!(serde_json::from_value::<NativeCapsuleSourceSpec>(encoded).is_err());
    }
}

#[test]
fn metadata_mismatch_or_nil_identity_cannot_bind_source() {
    let (_, asset, spec) = fixture();
    let mut altered = asset.clone();
    altered.name = "Different source".into();
    assert!(spec.bind_asset(&altered).is_err());
    let mut altered = asset.clone();
    altered.media_type = "image/svg+xml".into();
    assert!(spec.bind_asset(&altered).is_err());
    let mut altered = asset.clone();
    altered.id = Uuid::nil();
    assert!(spec.bind_asset(&altered).is_err());
    let mut altered = spec.clone();
    altered.capsule_id = Uuid::nil();
    assert!(altered.bind_asset(&asset).is_err());
    let mut altered = spec;
    altered.scene_id = Uuid::nil();
    assert!(altered.bind_asset(&asset).is_err());
}
