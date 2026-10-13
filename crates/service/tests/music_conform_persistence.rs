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
