//! Real StudioService / SQLite / CAS coverage with explicitly synthetic WAVs.
//! Native realization and prior-binary compatibility are separate gates.
use motionwright_domain::*;
use motionwright_service::{StudioService, VoiceImportMetadata};
use motionwright_storage::{ProjectBackup, StorageError};
use std::{
    fs,
    path::Path,
    sync::{Arc, Barrier},
};
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}

fn change(profile_id: Uuid, recipe: Option<DeliveryRecipe>) -> Change {
    Change::SetDeliveryRecipe {
        deliverable_profile_id: profile_id,
        recipe,
    }
}

fn wav(seed: i16) -> Vec<u8> {
    let frames = 48_000_u32;
    let data_bytes = frames * 4;
    let mut bytes = Vec::with_capacity(data_bytes as usize + 44);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&192_000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for index in 0..frames {
        let sample = ((index % 97) as i16 - 48) * seed;
        bytes.extend_from_slice(&sample.to_le_bytes());
        bytes.extend_from_slice(&(-sample).to_le_bytes());
    }
    bytes
}

fn source(
    service: &StudioService,
    project: &Project,
    folder: &Path,
    label: &str,
    seed: i16,
) -> (Project, Uuid) {
    let path = folder.join(format!("{label}.wav"));
    let bytes = wav(seed);
    fs::write(&path, &bytes).unwrap();
    let project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            &format!("import-{label}"),
            &path,
            format!("Synthetic {label}"),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let asset = project.assets.last().unwrap();
    let id = asset.id;
    assert_eq!(
        service
            .store()
            .lock()
            .read_blob(asset.content_sha256.as_ref().unwrap(), 256 * 1024)
            .unwrap(),
        bytes
    );
    fs::remove_file(path).unwrap();
    (project, id)
}

struct Fixture {
    folder: tempfile::TempDir,
    service: StudioService,
    project: Project,
    music: Uuid,
    sfx: Uuid,
}

fn fixture() -> Fixture {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("studio.sqlite3")).unwrap();
    let mut project = service
        .create_project("Synthetic delivery recipe persistence")
        .unwrap();
    for (index, name) in ["Opening", "Evidence"].iter().enumerate() {
        project = apply(
            &service,
            &project,
            &format!("scene-{index}"),
            Change::AddScene {
                name: (*name).into(),
                objective: "An editable source scene".into(),
                duration_seconds: 2,
            },
        );
    }
    let (project, music) = source(&service, &project, folder.path(), "music", 11);
    let (project, sfx) = source(&service, &project, folder.path(), "sfx", 17);
    Fixture {
        folder,
        service,
        project,
        music,
        sfx,
    }
}

fn recipe(fixture: &Fixture) -> DeliveryRecipe {
    DeliveryRecipe {
        schema: DeliveryRecipeSchema::V1,
        scene_intents: fixture
            .project
            .scenes
            .iter()
            .enumerate()
            .map(|(index, scene)| DeliverySceneIntent {
                scene_id: scene.id,
                role: Some(if index == 0 {
                    DeliveryNarrativeRole::Hook
                } else {
                    DeliveryNarrativeRole::Evidence
                }),
                archetype: Some(if index == 0 {
                    DeliveryArchetype::Statement
                } else {
                    DeliveryArchetype::EvidenceFrame
                }),
            })
            .collect(),
        original_mix: Some(OriginalMixRecipe {
            music_asset_id: Some(fixture.music),
            sfx_asset_id: Some(fixture.sfx),
            sfx_gain_db: -12.0,
            duck_attenuation_db: 8.0,
            duck_attack_samples: 960,
            duck_release_samples: 4_800,
        }),
    }
}

fn assert_unchanged(service: &StudioService, before: &ProjectBackup) {
    assert_eq!(service.project(before.project.id).unwrap(), before.project);
    let after = service.export_project(before.project.id).unwrap();
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
}

#[test]
fn two_profiles_reopen_and_inverse_revisioned_changes_preserve_exact_intent() {
    let f = fixture();
    let a = f.project.deliverables[0].id;
    let b = f.project.deliverables[1].id;
    let first = recipe(&f);
    let mut second = first.clone();
    second.scene_intents[0].role = Some(DeliveryNarrativeRole::Reveal);
    second.original_mix.as_mut().unwrap().duck_attenuation_db = 4.0;
    let p = apply(
        &f.service,
        &f.project,
        "recipe-a",
        change(a, Some(first.clone())),
    );
    let p = apply(&f.service, &p, "recipe-b", change(b, Some(second.clone())));
    assert_eq!(p.production_design.delivery_recipes[&a], first);
    assert_eq!(p.production_design.delivery_recipes[&b], second);
    let before_clear = p.clone();
    let p = apply(&f.service, &p, "clear-a", change(a, None));
    assert!(!p.production_design.delivery_recipes.contains_key(&a));
    assert_eq!(p.production_design.delivery_recipes[&b], second);
    // There is no generic recipe undo. An inverse is another ordinary CAS
    // change, not a fabricated Canvas patch/undo ID or restored authority.
    let p = apply(
        &f.service,
        &p,
        "inverse-clear-a",
        change(a, Some(first.clone())),
    );
    assert_eq!(
        p.production_design.delivery_recipes,
        before_clear.production_design.delivery_recipes
    );
    assert_eq!(p.revision, before_clear.revision + 2);
    assert_eq!(p.scenes, f.project.scenes);
    assert_eq!(p.audio.mix, f.project.audio.mix);
    let journal = f.service.history(p.id, 0, 256).unwrap();
    let path = f.folder.path().join("studio.sqlite3");
    drop(f.service);
    let reopened = StudioService::open(path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    assert_eq!(reopened.history(p.id, 0, 256).unwrap(), journal);
    assert_eq!(
        reopened
            .export_project(p.id)
            .unwrap()
            .project
            .production_design
            .delivery_recipes
            .len(),
        2
    );
}

#[test]
fn two_real_connections_have_one_cas_winner_and_replay_keeps_its_original_receipt() {
    let f = fixture();
    let profile = f.project.deliverables[0].id;
    let first = recipe(&f);
    let mut second = first.clone();
    second.original_mix.as_mut().unwrap().sfx_gain_db = -6.0;
    let changes = [change(profile, Some(first)), change(profile, Some(second))];
    let barrier = Arc::new(Barrier::new(3));
    let left = StudioService::open(f.folder.path().join("studio.sqlite3")).unwrap();
    let right = StudioService::open(f.folder.path().join("studio.sqlite3")).unwrap();
    let outcomes = std::thread::scope(|scope| {
        let a = Arc::clone(&barrier);
        let b = Arc::clone(&barrier);
        let first = &changes[0];
        let second = &changes[1];
        let project = &f.project;
        let x = scope.spawn(move || {
            a.wait();
            left.apply(project.id, &project.stamp(), "cas-left", first)
        });
        let y = scope.spawn(move || {
            b.wait();
            right.apply(project.id, &project.stamp(), "cas-right", second)
        });
        barrier.wait();
        [x.join().unwrap(), y.join().unwrap()]
    });
    assert_eq!(outcomes.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        outcomes
            .iter()
            .filter(|result| matches!(result, Err(StorageError::Conflict { .. })))
            .count(),
        1
    );
    let winner_index = usize::from(outcomes[1].is_ok());
    let winner = outcomes[winner_index].as_ref().unwrap();
    assert_eq!(winner.project.revision, f.project.revision + 1);
    let request = if winner_index == 0 {
        "cas-left"
    } else {
        "cas-right"
    };
    let replay = f
        .service
        .apply(
            f.project.id,
            &f.project.stamp(),
            request,
            &changes[winner_index],
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, winner.project);
    assert!(matches!(
        f.service.apply(
            f.project.id,
            &f.project.stamp(),
            request,
            &changes[1 - winner_index]
        ),
        Err(StorageError::RequestReuse)
    ));
    assert_eq!(
        f.service
            .history(f.project.id, f.project.revision, 256)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(f.service.project(f.project.id).unwrap(), winner.project);
}

#[test]
fn invalid_recipe_and_locked_edit_do_not_write_a_document_event_or_receipt() {
    let f = fixture();
    let profile = f.project.deliverables[0].id;
    let valid = recipe(&f);
    let before = f.service.export_project(f.project.id).unwrap();
    let mut invalid = valid.clone();
    invalid.original_mix.as_mut().unwrap().duck_release_samples = 0;
    assert!(matches!(
        f.service.apply(
            f.project.id,
            &f.project.stamp(),
            "retry-after-invalid",
            &change(profile, Some(invalid))
        ),
        Err(StorageError::Domain(_))
    ));
    assert_unchanged(&f.service, &before);
    let p = apply(
        &f.service,
        &f.project,
        "retry-after-invalid",
        change(profile, Some(valid.clone())),
    );
    assert_eq!(p.revision, f.project.revision + 1);

    let before = f.service.export_project(p.id).unwrap();
    for (name, mut invalid) in [
        ("unknown-source", valid.clone()),
        ("same-stems", valid.clone()),
        ("duplicate-scene", valid.clone()),
    ] {
        match name {
            "unknown-source" => {
                invalid.original_mix.as_mut().unwrap().music_asset_id = Some(Uuid::new_v4())
            }
            "same-stems" => invalid.original_mix.as_mut().unwrap().music_asset_id = Some(f.sfx),
            _ => invalid.scene_intents.push(invalid.scene_intents[0].clone()),
        }
        assert!(
            f.service
                .apply(p.id, &p.stamp(), name, &change(profile, Some(invalid)))
                .is_err()
        );
        assert_unchanged(&f.service, &before);
    }
    let p = apply(
        &f.service,
        &p,
        "protect-content",
        Change::SetLock {
            resource: p.resource_key(),
            kind: LockKind::Content,
            note: "Explicit protected content".into(),
        },
    );
    let before = f.service.export_project(p.id).unwrap();
    assert!(matches!(
        f.service
            .apply(p.id, &p.stamp(), "locked-recipe", &change(profile, None)),
        Err(StorageError::Domain(DomainError::Locked(_)))
    ));
    assert_unchanged(&f.service, &before);
}

#[test]
fn branch_checkout_merge_and_profile_deletion_use_branch_local_recipe_references() {
    let f = fixture();
    let a = f.project.deliverables[0].id;
    let b = f.project.deliverables[1].id;
    let first = recipe(&f);
    let mut alternate = first.clone();
    alternate.scene_intents[0].archetype = Some(DeliveryArchetype::ProductProof);
    let mut p = apply(
        &f.service,
        &f.project,
        "first-a",
        change(a, Some(first.clone())),
    );
    p = apply(&f.service, &p, "first-b", change(b, Some(first.clone())));
    let main = p.active_branch;
    p = apply(
        &f.service,
        &p,
        "make-branch",
        Change::CreateBranch {
            name: "Recipe alternative".into(),
        },
    );
    let branch = p
        .branches
        .iter()
        .find(|branch| branch.name == "Recipe alternative")
        .unwrap()
        .id;
    p = apply(
        &f.service,
        &p,
        "checkout-alternative",
        Change::CheckoutBranch { branch_id: branch },
    );
    p = apply(
        &f.service,
        &p,
        "alternate-a",
        change(a, Some(alternate.clone())),
    );
    p = apply(
        &f.service,
        &p,
        "checkout-main",
        Change::CheckoutBranch { branch_id: main },
    );
    assert_eq!(p.production_design.delivery_recipes[&a], first);
    p = apply(
        &f.service,
        &p,
        "merge-alternative",
        Change::MergeBranch {
            source_branch_id: branch,
        },
    );
    assert_eq!(p.production_design.delivery_recipes[&a], alternate);
    assert_eq!(p.production_design.delivery_recipes[&b], first);

    let before = f.service.export_project(p.id).unwrap();
    assert!(
        f.service
            .apply(
                p.id,
                &p.stamp(),
                "refuse-profile",
                &Change::RemoveDeliverable { profile_id: a }
            )
            .is_err()
    );
    assert_unchanged(&f.service, &before);
    p = apply(&f.service, &p, "clear-main-a", change(a, None));
    p = apply(
        &f.service,
        &p,
        "remove-main-profile-a",
        Change::RemoveDeliverable { profile_id: a },
    );
    assert!(!p.deliverables.iter().any(|profile| profile.id == a));
    p = apply(
        &f.service,
        &p,
        "return-to-intact-branch",
        Change::CheckoutBranch { branch_id: branch },
    );
    assert!(p.deliverables.iter().any(|profile| profile.id == a));
    assert_eq!(p.production_design.delivery_recipes[&a], alternate);
    p.validate().unwrap();
    let path = f.folder.path().join("studio.sqlite3");
    drop(f.service);
    assert_eq!(StudioService::open(path).unwrap().project(p.id).unwrap(), p);
}

#[test]
fn retained_branch_bases_keep_global_assets_after_current_recipes_are_cleared() {
    let f = fixture();
    let profile = f.project.deliverables[0].id;
    let mut p = apply(
        &f.service,
        &f.project,
        "base-recipe",
        change(profile, Some(recipe(&f))),
    );
    let main = p.active_branch;
    p = apply(
        &f.service,
        &p,
        "branch-with-source",
        Change::CreateBranch {
            name: "Retained source".into(),
        },
    );
    let branch = p
        .branches
        .iter()
        .find(|branch| branch.name == "Retained source")
        .unwrap()
        .id;
    p = apply(&f.service, &p, "clear-main", change(profile, None));
    p = apply(
        &f.service,
        &p,
        "checkout-retained",
        Change::CheckoutBranch { branch_id: branch },
    );
    p = apply(
        &f.service,
        &p,
        "clear-retained-current",
        change(profile, None),
    );
    p = apply(
        &f.service,
        &p,
        "save-retained-current",
        Change::CheckoutBranch { branch_id: main },
    );
    assert!(p.production_design.delivery_recipes.is_empty());
    assert!(p.branch_workspaces.iter().all(|workspace| {
        workspace
            .current_state
            .production_design
            .delivery_recipes
            .is_empty()
    }));
    assert!(p.branch_workspaces.iter().any(|workspace| {
        !workspace
            .base_state
            .production_design
            .delivery_recipes
            .is_empty()
    }));
    let before = f.service.export_project(p.id).unwrap();
    for id in [f.music, f.sfx] {
        let mut domain = p.clone();
        let remove = Change::RemoveAsset { asset_id: id };
        assert!(domain.apply_change(&remove).is_err());
        assert_eq!(domain, p);
        assert!(
            f.service
                .apply(p.id, &p.stamp(), &format!("remove-retained-{id}"), &remove)
                .is_err()
        );
        assert_unchanged(&f.service, &before);
    }
}

#[test]
fn explicit_clear_allows_unreferenced_deletion_but_does_not_erase_recipe_journal() {
    let f = fixture();
    let profile = f.project.deliverables[0].id;
    let mut p = apply(
        &f.service,
        &f.project,
        "set-before-clear",
        change(profile, Some(recipe(&f))),
    );
    let before = f.service.export_project(p.id).unwrap();
    assert!(
        f.service
            .apply(
                p.id,
                &p.stamp(),
                "referenced-asset",
                &Change::RemoveAsset { asset_id: f.music }
            )
            .is_err()
    );
    assert_unchanged(&f.service, &before);
    p = apply(&f.service, &p, "explicit-clear", change(profile, None));
    p = apply(
        &f.service,
        &p,
        "delete-unreferenced",
        Change::RemoveAsset { asset_id: f.music },
    );
    let backup = f.service.export_project(p.id).unwrap();
    assert!(
        serde_json::to_value(&backup.project).unwrap()["production_design"]
            .get("delivery_recipes")
            .is_none()
    );
    assert!(backup.events.iter().any(|event| matches!(
        &event.change,
        Change::SetDeliveryRecipe {
            recipe: Some(_),
            ..
        }
    )));
    assert!(backup.events.iter().any(|event| matches!(
        &event.change,
        Change::SetDeliveryRecipe { recipe: None, .. }
    )));
    assert_eq!(
        serde_json::from_str::<ProjectBackup>(&serde_json::to_string(&backup).unwrap()).unwrap(),
        backup
    );
}

#[test]
fn real_bundle_preserves_recipes_and_cas_and_rotates_generation_before_old_authority() {
    let f = fixture();
    let a = f.project.deliverables[0].id;
    let b = f.project.deliverables[1].id;
    let mut second = recipe(&f);
    second.original_mix.as_mut().unwrap().sfx_gain_db = -3.0;
    let mut p = apply(
        &f.service,
        &f.project,
        "bundle-a",
        change(a, Some(recipe(&f))),
    );
    p = apply(&f.service, &p, "bundle-b", change(b, Some(second)));
    p = apply(
        &f.service,
        &p,
        "bundle-branch",
        Change::CreateBranch {
            name: "Exported alternative".into(),
        },
    );
    let backup = f.service.export_project(p.id).unwrap();
    let bundle = f.folder.path().join("portable-bundle");
    let manifest = f.service.export_project_bundle(p.id, &bundle).unwrap();
    for asset in &p.assets {
        assert!(
            manifest
                .blobs
                .iter()
                .any(|blob| Some(&blob.sha256) == asset.content_sha256.as_ref())
        );
    }
    let destination = tempfile::tempdir().unwrap();
    let path = destination.path().join("restored.sqlite3");
    let restored = StudioService::open(&path).unwrap();
    restored.inspect_project_bundle(&bundle).unwrap();
    let imported = restored.import_project_bundle(&bundle).unwrap();
    assert_eq!(imported.id, p.id);
    assert_eq!(imported.revision, p.revision);
    assert_ne!(imported.generation, p.generation);
    assert_eq!(imported.production_design, p.production_design);
    assert_eq!(imported.branch_workspaces, p.branch_workspaces);
    assert_eq!(
        restored.history(imported.id, 0, 256).unwrap(),
        backup.events
    );
    for asset in &p.assets {
        let sha = asset.content_sha256.as_ref().unwrap();
        assert_eq!(
            restored.store().lock().read_blob(sha, 256 * 1024).unwrap(),
            f.service.store().lock().read_blob(sha, 256 * 1024).unwrap()
        );
    }
    assert!(matches!(
        restored.apply(p.id, &p.stamp(), "bundle-a", &change(a, Some(recipe(&f)))),
        Err(StorageError::GenerationConflict)
    ));
    let before = restored.export_project(imported.id).unwrap();
    assert!(matches!(
        restored.apply(
            imported.id,
            &p.stamp(),
            "old-authority-new-request",
            &change(a, None)
        ),
        Err(StorageError::GenerationConflict)
    ));
    assert_unchanged(&restored, &before);
    drop(restored);
    let reopened = StudioService::open(path).unwrap();
    assert_eq!(reopened.project(imported.id).unwrap(), imported);
    assert_eq!(
        reopened.export_project(imported.id).unwrap().events,
        backup.events
    );
}

#[test]
fn raw_backup_duplicate_profile_spellings_reject_before_typed_import() {
    let f = fixture();
    let profile = f.project.deliverables[0].id;
    let recipe = recipe(&f);
    let p = apply(
        &f.service,
        &f.project,
        "typed-raw-recipe",
        change(profile, Some(recipe.clone())),
    );
    let backup = f.service.export_project(p.id).unwrap();
    let raw = serde_json::to_string(&backup).unwrap();
    let value = serde_json::to_string(&recipe).unwrap();
    let entry = format!("\"{profile}\":{value}");
    assert!(raw.contains(&entry));
    for other in [
        profile.to_string(),
        profile.simple().to_string(),
        profile.to_string().to_uppercase(),
    ] {
        let duplicate = format!("{entry},\"{other}\":{value}");
        let invalid = raw.replacen(&entry, &duplicate, 1);
        let error = serde_json::from_str::<ProjectBackup>(&invalid).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("duplicate delivery recipe profile UUID")
        );
        assert_unchanged(&f.service, &backup);
    }
}

#[test]
fn consumed_voice_and_transcript_windows_invalidate_only_the_selected_mix() {
    let f = fixture();
    let a = f.project.deliverables[0].id;
    let b = f.project.deliverables[1].id;
    let voice_path = f.folder.path().join("voice.wav");
    fs::write(&voice_path, wav(7)).unwrap();
    let mut p = f
        .service
        .import_voice_file(
            f.project.id,
            &f.project.stamp(),
            "measured-voice",
            &voice_path,
            VoiceImportMetadata {
                name: "Synthetic voice source".into(),
                label: "Synthetic timing input".into(),
                media_type: "audio/wav".into(),
            },
        )
        .unwrap()
        .project;
    fs::remove_file(&voice_path).unwrap();
    let voice = p.audio.active_voice_track_id.unwrap();
    for id in [a, b] {
        let mut profile = p
            .deliverables
            .iter()
            .find(|profile| profile.id == id)
            .unwrap()
            .clone();
        profile.voice_track_id = Some(voice);
        p = apply(
            &f.service,
            &p,
            &format!("voice-for-{id}"),
            Change::UpsertDeliverable { profile },
        );
    }
    let mut segment = TranscriptSegment {
        id: Uuid::new_v4(),
        voice_track_id: voice,
        start: RationalTime::ZERO,
        end: RationalTime { num: 1, den: 1 },
        text: "Synthetic words with explicitly manual timing".into(),
        speaker: None,
        alignment: AlignmentEvidence::Manual,
    };
    p = apply(
        &f.service,
        &p,
        "manual-interval",
        Change::AddTranscriptSegment {
            segment: segment.clone(),
        },
    );
    p = apply(&f.service, &p, "mix-for-a", change(a, Some(recipe(&f))));
    let first_a = variant_dependency_fingerprint(&p, a).unwrap();
    let first_b = variant_dependency_fingerprint(&p, b).unwrap();
    segment.end = RationalTime { num: 4, den: 5 };
    p = apply(
        &f.service,
        &p,
        "review-interval",
        Change::UpsertTranscriptSegment {
            segment: segment.clone(),
        },
    );
    let timed_a = variant_dependency_fingerprint(&p, a).unwrap();
    let timed_b = variant_dependency_fingerprint(&p, b).unwrap();
    assert_ne!(first_a.audio_inputs_sha256, timed_a.audio_inputs_sha256);
    assert_eq!(first_a.frame_inputs_sha256, timed_a.frame_inputs_sha256);
    assert_eq!(first_b.audio_inputs_sha256, timed_b.audio_inputs_sha256);
    segment.text = "Changed wording does not change PCM duck intervals".into();
    p = apply(
        &f.service,
        &p,
        "review-wording",
        Change::UpsertTranscriptSegment { segment },
    );
    let wording = variant_dependency_fingerprint(&p, a).unwrap();
    assert_eq!(wording.audio_inputs_sha256, timed_a.audio_inputs_sha256);
    assert_ne!(wording.caption_inputs_sha256, timed_a.caption_inputs_sha256);

    // A fingerprint binds actual source digests, not asset UUIDs alone. This
    // pure candidate uses another existing CAS digest without changing SQLite.
    let mut candidate = p.clone();
    candidate
        .assets
        .iter_mut()
        .find(|asset| asset.id == f.music)
        .unwrap()
        .content_sha256 = p
        .assets
        .iter()
        .find(|asset| asset.id == f.sfx)
        .unwrap()
        .content_sha256
        .clone();
    assert_ne!(
        variant_dependency_fingerprint(&candidate, a)
            .unwrap()
            .audio_inputs_sha256,
        wording.audio_inputs_sha256
    );
    assert_eq!(
        variant_dependency_fingerprint(&candidate, b)
            .unwrap()
            .audio_inputs_sha256,
        timed_b.audio_inputs_sha256
    );
    assert_eq!(f.service.project(p.id).unwrap(), p);

    fs::write(&voice_path, wav(19)).unwrap();
    p = f
        .service
        .import_voice_file(
            p.id,
            &p.stamp(),
            "different-active-voice",
            &voice_path,
            VoiceImportMetadata {
                name: "Another synthetic voice source".into(),
                label: "Another measured input".into(),
                media_type: "audio/wav".into(),
            },
        )
        .unwrap()
        .project;
    assert!(
        variant_dependency_fingerprint(&p, a)
            .unwrap_err()
            .to_string()
            .contains("profile voice differs")
    );
    assert!(variant_dependency_fingerprint(&p, b).is_ok());
    p = apply(&f.service, &p, "remove-original-mix", change(a, None));
    assert!(variant_dependency_fingerprint(&p, a).is_ok());
}
