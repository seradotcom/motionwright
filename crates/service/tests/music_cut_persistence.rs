use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;

const DIGEST: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn write_original_wav(path: &std::path::Path) {
    // Real, valid PCM16 RIFF source; no forged CAS digest or bypass of asset
    // admission. The service computes the hash from these exact bytes.
    let sample_rate = 48_000_u32;
    let frames = 480_u32;
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
    write_original_wav(&source);
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
