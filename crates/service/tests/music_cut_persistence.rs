use motionwright_domain::*;
use motionwright_service::StudioService;
use rusqlite::{Connection, OpenFlags};

const DIGEST: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn write_original_wav(path: &std::path::Path, frames: u32) {
    // Real, valid PCM16 RIFF source; no forged CAS digest or bypass of asset
    // admission. The service computes the hash from these exact bytes.
    let sample_rate = 48_000_u32;
    let data_bytes = frames * 2;
    let mut wav = Vec::with_capacity(44 + data_bytes as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&sample_rate.to_le_bytes());
    wav.extend_from_slice(&(sample_rate * 2).to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_bytes.to_le_bytes());
    for frame in 0..frames {
        let sample = if frame % 2 == 0 {
            1_200_i16
        } else {
            -1_200_i16
        };
        wav.extend_from_slice(&sample.to_le_bytes());
    }
    std::fs::write(path, wav).unwrap();
}

fn apply(service: &StudioService, project: &Project, id: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), id, &change)
        .unwrap()
        .project
}

#[test]
fn exact_music_cut_persists_under_one_native_service_transaction_and_stale_cas_fails() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("music-persistent.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service
        .create_project("Author-marked music campaign")
        .unwrap();
    p = apply(
        &service,
        &p,
        "add-a",
        Change::AddScene {
            name: "First".into(),
            objective: "Establish the beat".into(),
            duration_seconds: 7,
        },
    );
    p = apply(
        &service,
        &p,
        "add-b",
        Change::AddScene {
            name: "Second".into(),
            objective: "Continue without replacing the scene".into(),
            duration_seconds: 9,
        },
    );
    let first = p.scenes[0].id;
    let second = p.scenes[1].id;
    let source = temp.path().join("original-music.wav");
    write_original_wav(&source, 480);
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "imported",
            &source,
            "Original authorized owner audio.wav".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let imported = p
        .assets
        .iter()
        .find(|asset| asset.name == "Original authorized owner audio.wav")
        .expect("real imported audio file must be admitted");
    let asset_id = imported.id;
    let source_sha256 = imported.content_sha256.clone().unwrap();
    let shots = p
        .scenes
        .iter()
        .map(|scene| PlannedShot {
            scene_id: scene.id,
            purpose: scene.objective.clone(),
            claim_ids: vec![],
            asset_ids: vec![],
            evidence_kind: NarrativeEvidenceKind::GraphicStudy,
        })
        .collect();
    p = apply(
        &service,
        &p,
        "music-plan",
        Change::SetProductionPlan {
            plan: Some(ProductionPlan {
                objective: "Align the storyboard to authorized audio".into(),
                audience: "Editors".into(),
                concept: "Exact musical cut with source-locked, reviewable impact".into(),
                reference_constraints: vec![],
                exclusions: vec!["No automatic cue movement or time stretch".into()],
                shots,
                clock: ProductionClock::Music {
                    asset_id,
                    beats: vec![
                        RationalTime::new(0, 1).unwrap(),
                        RationalTime::new(8, 1).unwrap(),
                    ],
                },
                approval: None,
            }),
        },
    );
    let before = p.stamp();
    let cut = Change::SnapSceneCutToMusic {
        scene_id: first,
        beat_index: 1,
        source_sha256,
        base_revision: p.revision,
        acknowledge_absolute_anchors: false,
    };
    p = apply(&service, &p, "scoped-cut", cut.clone());
    assert_eq!(p.revision, before.revision + 1);
    assert_eq!(p.scenes[0].duration, RationalTime::new(8, 1).unwrap());
    assert_eq!(p.scenes[1].id, second);
    assert_eq!(p.scenes[1].start, RationalTime::new(8, 1).unwrap());
    assert!(
        service
            .apply(p.id, &before, "stale-independent-agent", &cut)
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    drop(service);
    let restored = StudioService::open(&db).unwrap().project(p.id).unwrap();
    assert_eq!(restored, p);
    assert_eq!(
        restored.production_design.plan.as_ref().unwrap().clock,
        p.production_design.plan.as_ref().unwrap().clock
    );
}

#[test]
fn changing_an_absolute_marker_never_grants_a_stale_mutation() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("stale-marker.sqlite3")).unwrap();
    let mut p = service.create_project("Locked source").unwrap();
    p = apply(
        &service,
        &p,
        "add-a",
        Change::AddScene {
            name: "Opening".into(),
            objective: "A real cut".into(),
            duration_seconds: 7,
        },
    );
    let first = p.scenes[0].id;
    let stale = p.stamp();
    p = apply(
        &service,
        &p,
        "marker",
        Change::AddMarker {
            at: RationalTime::new(3, 1).unwrap(),
            label: "Existing absolute cue".into(),
        },
    );
    let proposed = Change::SnapSceneCutToMusic {
        scene_id: first,
        beat_index: 0,
        source_sha256: DIGEST.into(),
        base_revision: stale.revision,
        acknowledge_absolute_anchors: true,
    };
    assert!(
        service
            .apply(p.id, &stale, "stale-music-cut", &proposed)
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
}

#[test]
fn sfx_cut_veto_has_no_write_and_valid_ripple_reopens_with_one_receipt() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("music-sfx.sqlite3");
    let source = temp.path().join("original-six-second.wav");
    write_original_wav(&source, 6 * 48_000);
    let original_bytes = std::fs::read(&source).unwrap();
    let service = StudioService::open(&db).unwrap();
    let mut p = service
        .create_project("Source-bound SFX music cut")
        .unwrap();
    for (id, seconds) in [("opening", 7), ("following", 9)] {
        p = apply(
            &service,
            &p,
            id,
            Change::AddScene {
                name: id.into(),
                objective: "Retain the original scene and audio source".into(),
                duration_seconds: seconds,
            },
        );
    }
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "import-sfx-source",
            &source,
            "Original six-second WAV".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let asset = p
        .assets
        .iter()
        .find(|asset| asset.name == "Original six-second WAV")
        .unwrap()
        .clone();
    let digest = asset.content_sha256.clone().unwrap();
    let first = p.scenes[0].id;
    let second = p.scenes[1].id;
    let shots = p
        .scenes
        .iter()
        .map(|scene| PlannedShot {
            scene_id: scene.id,
            purpose: scene.objective.clone(),
            claim_ids: vec![],
            asset_ids: vec![],
            evidence_kind: NarrativeEvidenceKind::GraphicStudy,
        })
        .collect();
    p = apply(
        &service,
        &p,
        "music-grid",
        Change::SetProductionPlan {
            plan: Some(ProductionPlan {
                objective: "Keep complete source-bound SFX tails".into(),
                audience: "Editors".into(),
                concept: "Review the exact saved cut before one durable change".into(),
                reference_constraints: vec![],
                exclusions: vec!["No implicit audio trim".into()],
                shots,
                clock: ProductionClock::Music {
                    asset_id: asset.id,
                    beats: vec![
                        RationalTime::ZERO,
                        RationalTime::new(5, 1).unwrap(),
                        RationalTime::new(8, 1).unwrap(),
                    ],
                },
                approval: None,
            }),
        },
    );
    for (id, scene_id, source_frames) in [("sfx-tail", first, 180), ("sfx-ripple", second, 30)] {
        p = apply(
            &service,
            &p,
            id,
            Change::UpsertSemanticSfx {
                cue: SemanticSfxCue {
                    schema: SFX_CUE_SCHEMA.into(),
                    id: uuid::Uuid::new_v4(),
                    scene_id,
                    event: SfxEvent::UiFocus,
                    anchor: SfxAnchor::SceneStart,
                    offset_frames: 0,
                    source_asset_id: asset.id,
                    source_sha256: digest.clone(),
                    source_frames,
                    gain_milli_db: -12_000,
                    attack_frames: 3,
                    release_frames: 8,
                    tail_policy: SfxTailPolicy::WithinScene,
                    original_source_attestation: "Original synthetic PCM test source".into(),
                },
            },
        );
    }
    assert_eq!(
        service
            .verified_semantic_sfx_wav(p.id, &p.stamp(), asset.id, &digest)
            .unwrap(),
        original_bytes
    );
    let before_schedule = p.validated_sfx_schedule().unwrap();
    let before_events = service.export_project(p.id).unwrap().events;
    let monitor = Connection::open_with_flags(&db, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let observe = || {
        (
            monitor
                .query_row("PRAGMA data_version", [], |row| row.get::<_, i64>(0))
                .unwrap(),
            monitor
                .query_row(
                    "SELECT count(*) FROM receipts WHERE project_id=?1",
                    [p.id.to_string()],
                    |row| row.get::<_, i64>(0),
                )
                .unwrap(),
        )
    };
    let before_store = observe();
    let bad = Change::SnapSceneCutToMusic {
        scene_id: first,
        beat_index: 1,
        source_sha256: digest.clone(),
        base_revision: p.revision,
        acknowledge_absolute_anchors: true,
    };
    assert!(
        service
            .project(p.id)
            .unwrap()
            .inspect_music_cut(first, 1, &digest)
            .unwrap_err()
            .to_string()
            .contains("release tail")
    );
    assert!(
        service
            .apply(p.id, &p.stamp(), "denied-tail", &bad)
            .is_err()
    );
    assert_eq!(observe(), before_store);
    assert_eq!(service.project(p.id).unwrap(), p);
    assert_eq!(service.export_project(p.id).unwrap().events, before_events);

    let review = p.inspect_music_cut(first, 2, &digest).unwrap();
    assert_eq!(review.affected_sfx_cue_count, 2);
    let mut good = Change::SnapSceneCutToMusic {
        scene_id: first,
        beat_index: 2,
        source_sha256: digest.clone(),
        base_revision: p.revision,
        acknowledge_absolute_anchors: false,
    };
    assert!(
        service
            .apply(p.id, &p.stamp(), "unreviewed-sfx", &good)
            .is_err()
    );
    assert_eq!(observe(), before_store);
    if let Change::SnapSceneCutToMusic {
        acknowledge_absolute_anchors,
        ..
    } = &mut good
    {
        *acknowledge_absolute_anchors = true;
    }
    let updated = apply(&service, &p, "reviewed-sfx-cut", good.clone());
    assert_eq!(
        service
            .verified_semantic_sfx_wav(updated.id, &updated.stamp(), asset.id, &digest)
            .unwrap(),
        original_bytes
    );
    assert_eq!(updated.revision, p.revision + 1);
    assert_eq!(updated.generation, p.generation);
    assert_eq!(updated.assets, p.assets);
    assert_eq!(updated.audio.sfx_cues, p.audio.sfx_cues);
    assert_eq!(updated.scenes[1].id, second);
    assert_eq!(updated.scenes[1].start, RationalTime::new(8, 1).unwrap());
    assert_eq!(updated.scenes[1].status, SceneStatus::Draft);
    let after_schedule = updated.validated_sfx_schedule().unwrap();
    assert_eq!(after_schedule[0], before_schedule[0]);
    assert_eq!(
        after_schedule[1].first_sample,
        before_schedule[1].first_sample + 48_000
    );
    assert_eq!(
        after_schedule[1].sample_count,
        before_schedule[1].sample_count
    );
    assert_eq!(observe().1, before_store.1 + 1);
    let committed_store = observe();
    assert!(
        service
            .apply(p.id, &p.stamp(), "stale-sfx-cut", &good)
            .is_err()
    );
    assert_eq!(observe(), committed_store);
    drop(monitor);
    drop(service);
    let reopened_service = StudioService::open(&db).unwrap();
    let reopened = reopened_service.project(p.id).unwrap();
    assert_eq!(reopened, updated);
    assert_eq!(reopened.validated_sfx_schedule().unwrap(), after_schedule);
    assert_eq!(
        reopened_service
            .verified_semantic_sfx_wav(reopened.id, &reopened.stamp(), asset.id, &digest)
            .unwrap(),
        original_bytes
    );
    assert_eq!(std::fs::read(&source).unwrap(), original_bytes);
}
