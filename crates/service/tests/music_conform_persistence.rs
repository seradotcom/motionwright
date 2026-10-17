use motionwright_domain::*;
use motionwright_service::StudioService;

fn at(n: i64) -> RationalTime {
    RationalTime::new(n, 1).unwrap()
}
fn change(service: &StudioService, p: &Project, id: &str, cmd: Change) -> Project {
    service.apply(p.id, &p.stamp(), id, &cmd).unwrap().project
}
fn real_wave(path: &std::path::Path) {
    // 100 original PCM16 samples with a real RIFF data header, admitted
    // by the CAS service rather than inventing a content-addressed digest.
    let data_bytes = 200_u32;
    let mut data = Vec::with_capacity(44 + data_bytes as usize);
    data.extend_from_slice(b"RIFF");
    data.extend_from_slice(&(data_bytes + 36).to_le_bytes());
    data.extend_from_slice(b"WAVEfmt ");
    data.extend_from_slice(&16_u32.to_le_bytes());
    data.extend_from_slice(&1_u16.to_le_bytes());
    data.extend_from_slice(&1_u16.to_le_bytes());
    data.extend_from_slice(&48_000_u32.to_le_bytes());
    data.extend_from_slice(&96_000_u32.to_le_bytes());
    data.extend_from_slice(&2_u16.to_le_bytes());
    data.extend_from_slice(&16_u16.to_le_bytes());
    data.extend_from_slice(b"data");
    data.extend_from_slice(&data_bytes.to_le_bytes());
    for n in 0..100 {
        data.extend_from_slice(&(n as i16).to_le_bytes());
    }
    std::fs::write(path, data).unwrap();
}
fn seeded_service(db: &std::path::Path, source: &std::path::Path) -> (StudioService, Project) {
    let service = StudioService::open(db).unwrap();
    let mut p = service
        .create_project("Original owner music sequence")
        .unwrap();
    for (i, (name, duration)) in [("Opening", 7), ("Proof", 9), ("Final scene", 6)]
        .into_iter()
        .enumerate()
    {
        p = change(
            &service,
            &p,
            &format!("scene-{i}"),
            Change::AddScene {
                name: name.into(),
                objective: "Original editable scene".into(),
                duration_seconds: duration,
            },
        );
    }
    real_wave(source);
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "source-bytes",
            source,
            "Original owner song.wav".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let source_asset = p
        .assets
        .iter()
        .find(|asset| asset.name == "Original owner song.wav")
        .unwrap();
    assert!(source_asset.content_sha256.as_deref().unwrap().len() == 64);
    let source_id = source_asset.id;
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
    p = change(
        &service,
        &p,
        "plan",
        Change::SetProductionPlan {
            plan: Some(ProductionPlan {
                objective: "Two original scene boundaries in one revision".into(),
                audience: "Editor".into(),
                concept: "Conform time without touching the source song bytes".into(),
                reference_constraints: vec![],
                exclusions: vec!["Never make up audio detection".into()],
                shots,
                clock: ProductionClock::Music {
                    asset_id: source_id,
                    beats: vec![at(0), at(5), at(8), at(13), at(18), at(25)],
                },
                approval: None,
            }),
        },
    );
    (service, p)
}
fn conform(p: &Project, invalid: bool) -> Change {
    Change::ConformScenesToMusic {
        targets: vec![
            MusicConformTarget {
                scene_id: p.scenes[0].id,
                beat_index: 2,
            },
            MusicConformTarget {
                scene_id: p.scenes[1].id,
                beat_index: if invalid { 2 } else { 4 },
            },
        ],
        source_sha256: p
            .assets
            .iter()
            .find(|asset| asset.name == "Original owner song.wav")
            .unwrap()
            .content_sha256
            .clone()
            .unwrap(),
        base_revision: p.revision,
        acknowledge_absolute_anchors: false,
    }
}
#[test]
fn one_transaction_changes_two_cuts_and_reopens_same_content_addressed_source() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("project.sqlite3");
    let source = temp.path().join("admitted.wav");
    let (service, p) = seeded_service(&db, &source);
    let baseline = p.clone();
    let prior = p.stamp();
    let command = conform(&p, false);
    let updated = change(&service, &p, "multi-music-conform", command.clone());
    assert_eq!(updated.revision, baseline.revision + 1);
    assert_eq!(
        updated
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>(),
        baseline
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>()
    );
    assert_eq!(updated.scenes[0].duration, at(8));
    assert_eq!(updated.scenes[1].start, at(8));
    assert_eq!(updated.scenes[1].duration, at(10));
    assert_eq!(updated.scenes[2].start, at(18));
    assert_eq!(updated.scenes[2].duration, at(6));
    assert_eq!(updated.assets, baseline.assets);
    assert!(
        service
            .apply(updated.id, &prior, "stale-other-agent", &command)
            .is_err()
    );
    drop(service);
    let reopened = StudioService::open(&db)
        .unwrap()
        .project(updated.id)
        .unwrap();
    assert_eq!(reopened, updated);
    assert_eq!(std::fs::read(&source).unwrap().len(), 244);
}
#[test]
fn failing_second_boundary_never_publishes_any_first_boundary_or_partial_asset() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("rollback.sqlite3");
    let source = temp.path().join("source.wav");
    let (service, p) = seeded_service(&db, &source);
    let original_source = std::fs::read(&source).unwrap();
    let bad = conform(&p, true);
    assert!(service.apply(p.id, &p.stamp(), "bad-batch", &bad).is_err());
    assert_eq!(service.project(p.id).unwrap(), p);
    assert_eq!(std::fs::read(&source).unwrap(), original_source);
}

fn imported_stereo_sfx(
    service: &StudioService,
    project: &Project,
    path: &std::path::Path,
    scene_index: usize,
    start_frame: i16,
    length_frames: u16,
) -> Project {
    use uuid::Uuid;
    let samples = u32::from(length_frames) * 1_600;
    let size = samples * 4;
    let mut bytes = Vec::with_capacity(44 + size as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(size + 36).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes()); // stereo
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&192_000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&size.to_le_bytes());
    for _ in 0..samples {
        bytes.extend_from_slice(&1200_i16.to_le_bytes());
        bytes.extend_from_slice(&(-1200_i16).to_le_bytes());
    }
    std::fs::write(path, bytes).unwrap();
    let recorded = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "import-original-sfx",
            path,
            "Owner original stereo sound.wav".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let asset = recorded
        .assets
        .iter()
        .find(|a| a.name == "Owner original stereo sound.wav")
        .unwrap();
    let cue = SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id: Uuid::new_v4(),
        scene_id: recorded.scenes[scene_index].id,
        event: SfxEvent::Transition,
        anchor: SfxAnchor::SceneStart,
        offset_frames: start_frame,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        source_frames: length_frames,
        gain_milli_db: -18_000,
        attack_frames: 0,
        release_frames: 10,
        original_source_attestation: "Original stereo PCM16 generated from sample values".into(),
    };
    service
        .apply(
            recorded.id,
            &recorded.stamp(),
            "attach-original-sfx",
            &Change::UpsertSemanticSfx { cue },
        )
        .unwrap()
        .project
}

#[test]
fn durable_music_conform_rejects_source_bound_sfx_tail_without_partial_sqlite_write() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("music-sfx-tail.sqlite3");
    let soundtrack = temp.path().join("source-music.wav");
    let original_sfx = temp.path().join("source-sfx.wav");
    let (service, prior) = seeded_service(&db, &soundtrack);
    let project = imported_stereo_sfx(&service, &prior, &original_sfx, 0, 30, 180);
    let contents = std::fs::read(&original_sfx).unwrap();
    assert_eq!(
        project.validated_sfx_schedule().unwrap()[0].absolute_end_frame,
        7 * 30
    );
    let audio = project
        .assets
        .iter()
        .find(|a| a.name == "Owner original stereo sound.wav")
        .unwrap();

    let mut bad = conform(&project, false);
    if let Change::ConformScenesToMusic {
        ref mut targets, ..
    } = bad
    {
        targets[0].beat_index = 1; // 7-second scene would end at 5s
        targets[1].beat_index = 3;
    }
    assert!(
        service
            .apply(project.id, &project.stamp(), "invalid-sfx-tail", &bad)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(std::fs::read(&original_sfx).unwrap(), contents);
    assert_eq!(
        service
            .verified_semantic_sfx_wav(
                project.id,
                &project.stamp(),
                audio.id,
                audio.content_sha256.as_deref().unwrap()
            )
            .unwrap(),
        contents
    );
    drop(service);
    let reopened = StudioService::open(&db)
        .unwrap()
        .project(project.id)
        .unwrap();
    assert_eq!(reopened, project);
}

#[test]
fn durable_music_conform_requires_sfx_review_and_repositions_exact_samples() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("music-sfx-success.sqlite3");
    let soundtrack = temp.path().join("source-music.wav");
    let original_sfx = temp.path().join("scene-sfx.wav");
    let (service, prior) = seeded_service(&db, &soundtrack);
    let project = imported_stereo_sfx(&service, &prior, &original_sfx, 2, 0, 30);
    let source_sfx = project.audio.sfx_cues.clone();
    let old_schedule = project.validated_sfx_schedule().unwrap();
    assert_eq!(old_schedule[0].absolute_start_frame, 16 * 30);
    assert_eq!(old_schedule[0].first_sample, 16 * 30 * 1600);
    let forbidden = conform(&project, false);
    assert!(
        service
            .apply(project.id, &project.stamp(), "missing-sfx-ack", &forbidden)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);

    let mut approved = conform(&project, false);
    if let Change::ConformScenesToMusic {
        ref mut acknowledge_absolute_anchors,
        ..
    } = approved
    {
        *acknowledge_absolute_anchors = true;
    }
    let updated = change(&service, &project, "approved-semantic-sfx-ripple", approved);
    assert_eq!(updated.revision, project.revision + 1);
    assert_eq!(updated.audio.sfx_cues, source_sfx);
    assert_eq!(
        updated.validated_sfx_schedule().unwrap()[0].absolute_start_frame,
        18 * 30
    );
    assert_eq!(
        updated.validated_sfx_schedule().unwrap()[0].first_sample,
        18 * 30 * 1600
    );
    drop(service);
    let reopened = StudioService::open(&db)
        .unwrap()
        .project(updated.id)
        .unwrap();
    assert_eq!(reopened, updated);
}
