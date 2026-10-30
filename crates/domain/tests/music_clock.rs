use motionwright_domain::*;
use uuid::Uuid;

const MUSIC_DIGEST: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn at(num: i64, den: i64) -> RationalTime {
    RationalTime::new(num, den).unwrap()
}
fn test_project() -> (Project, Uuid, Uuid) {
    let mut p = Project::new("Music-synced revision ten").unwrap();
    for (name, seconds) in [("First scene", 7), ("Second scene", 9), ("Outro", 6)] {
        p.apply_change(&Change::AddScene {
            name: name.into(),
            objective: format!("Original editorial beat for {name}"),
            duration_seconds: seconds,
        })
        .unwrap();
    }
    let audio = Uuid::new_v4();
    p.apply_change(&Change::AddAsset {
        asset: Asset {
            id: audio,
            name: "Authorized voice-and-music source.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(MUSIC_DIGEST.into()),
            source_revision: Some("owner-entered-beat-grid".into()),
        },
    })
    .unwrap();
    let first = p.scenes[0].id;
    let second = p.scenes[1].id;
    p.apply_change(&Change::SetProductionPlan {
        plan: Some(ProductionPlan {
            objective: "Keep the source-bound cut aligned to editor-marked musical beats".into(),
            audience: "Creative operators".into(),
            concept: "Edit a real persistent timeline on a rational clock".into(),
            reference_constraints: vec![],
            exclusions: vec!["Do not infer beat detection or license consent".into()],
            shots: p
                .scenes
                .iter()
                .map(|scene| PlannedShot {
                    scene_id: scene.id,
                    purpose: scene.objective.clone(),
                    claim_ids: vec![],
                    asset_ids: vec![],
                    evidence_kind: NarrativeEvidenceKind::GraphicStudy,
                })
                .collect(),
            clock: ProductionClock::Music {
                asset_id: audio,
                beats: vec![at(0, 1), at(5, 1), at(8, 1), at(13, 1), at(19, 1)],
            },
            approval: None,
        }),
    })
    .unwrap();
    (p, first, second)
}
fn change(project: &Project, first: Uuid, beat_index: usize, acknowledge: bool) -> Change {
    Change::SnapSceneCutToMusic {
        scene_id: first,
        beat_index,
        source_sha256: MUSIC_DIGEST.into(),
        base_revision: project.revision,
        acknowledge_absolute_anchors: acknowledge,
    }
}

#[test]
fn exact_rational_cut_ripples_subsequent_scenes_and_roundtrips() {
    let (mut p, first, second) = test_project();
    let snapshot = p.clone();
    let preview = p.inspect_music_cut(first, 2, MUSIC_DIGEST).unwrap();
    assert_eq!(preview.original_end, at(7, 1));
    assert_eq!(preview.target_end, at(8, 1));
    assert_eq!(preview.new_duration, at(8, 1));
    assert_eq!(preview.ripple, at(1, 1));
    assert_eq!(preview.downstream_scene_ids, vec![second, p.scenes[2].id]);
    assert!(!preview.anchors_need_acknowledgement());
    assert_eq!(p, snapshot, "read-only preview must never mutate a project");
    p.apply_change(&change(&p, first, 2, false)).unwrap();
    assert_eq!(p.scenes[0].duration, at(8, 1));
    assert_eq!(p.scenes[1].start, at(8, 1));
    assert_eq!(p.scenes[2].start, at(17, 1));
    assert_eq!(p.scenes[0].status, SceneStatus::Draft);
    let reopened: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    assert_eq!(reopened, p);
    reopened.validate().unwrap();
}

#[test]
fn single_cut_invalidates_only_the_edited_and_rippled_scene_statuses() {
    for beat_index in [3, 4] {
        let (mut p, _, second) = test_project();
        for scene in &mut p.scenes {
            scene.status = SceneStatus::Approved;
        }
        let original = p.clone();
        p.apply_change(&change(&p, second, beat_index, false))
            .unwrap();
        assert_eq!(p.scenes[0], original.scenes[0]);
        assert_eq!(p.scenes[1].start, original.scenes[1].start);
        assert_ne!(p.scenes[1].duration, original.scenes[1].duration);
        assert_eq!(p.scenes[1].status, SceneStatus::Draft);
        assert_ne!(p.scenes[2].start, original.scenes[2].start);
        assert_eq!(p.scenes[2].duration, original.scenes[2].duration);
        assert_eq!(p.scenes[2].status, SceneStatus::Draft);
        assert_eq!(p.revision, original.revision + 1);
        p.validate().unwrap();
    }
}

#[test]
fn explicit_source_and_revision_guards_reject_stale_or_swapped_audio() {
    let (mut p, first, _) = test_project();
    let prior = p.clone();
    let mut stale = change(&p, first, 2, false);
    if let Change::SnapSceneCutToMusic {
        ref mut base_revision,
        ..
    } = stale
    {
        *base_revision += 1;
    }
    assert!(p.apply_change(&stale).is_err());
    assert_eq!(p, prior);
    assert!(p.inspect_music_cut(first, 2, &"B".repeat(64)).is_err());
    let wrong = Change::SnapSceneCutToMusic {
        scene_id: first,
        beat_index: 2,
        source_sha256: "b".repeat(64),
        base_revision: p.revision,
        acknowledge_absolute_anchors: false,
    };
    assert!(p.apply_change(&wrong).is_err());
    assert_eq!(p, prior);
    p.assets
        .iter_mut()
        .find(|asset| asset.content_sha256.is_some())
        .unwrap()
        .content_sha256 = Some("b".repeat(64));
    assert!(p.apply_change(&change(&p, first, 2, false)).is_err());
}

#[test]
fn absolute_audio_cues_and_markers_require_acknowledgement_without_auto_shift() {
    let (mut p, first, _) = test_project();
    p.audio.cues.push(AudioCue {
        id: Uuid::new_v4(),
        label: "Voice entrance".into(),
        at: at(10, 1),
        source_segment_id: None,
        evidence: CueEvidence::Manual,
    });
    p.markers.push(Marker {
        id: Uuid::new_v4(),
        at: at(11, 1),
        label: "Locked to audio timeline".into(),
    });
    p.audio.transcript.clear();
    let before = p.clone();
    let preview = p.inspect_music_cut(first, 2, MUSIC_DIGEST).unwrap();
    assert_eq!(preview.affected_cue_count, 1);
    assert_eq!(preview.affected_marker_count, 1);
    assert!(preview.anchors_need_acknowledgement());
    assert!(p.apply_change(&change(&p, first, 2, false)).is_err());
    assert_eq!(p, before);
    p.apply_change(&change(&p, first, 2, true)).unwrap();
    assert_eq!(p.audio.cues, before.audio.cues);
    assert_eq!(p.markers, before.markers);
    assert_eq!(p.scenes[1].start, at(8, 1));
}

#[test]
fn protects_every_downstream_timing_lock_and_delivery_profile() {
    let (mut p, first, second) = test_project();
    p.locks.push(ProjectLock {
        id: Uuid::new_v4(),
        resource: format!("scene:{second}"),
        kind: LockKind::Timing,
        note: "Downstream shot must stay fixed".into(),
    });
    assert!(matches!(
        p.inspect_music_cut(first, 2, MUSIC_DIGEST),
        Err(DomainError::Locked(_))
    ));
    p.locks.clear();
    if let Some(profile) = p.deliverables.first_mut() {
        profile.timing_locked = true;
        profile.included_scene_ids = vec![second];
        assert!(matches!(
            p.inspect_music_cut(first, 2, MUSIC_DIGEST),
            Err(DomainError::Locked(_))
        ));
    }
}

#[test]
fn rejects_stranded_scene_beats_and_exact_zero_length_boundaries() {
    let (mut p, first, second) = test_project();
    assert!(p.inspect_music_cut(second, 1, MUSIC_DIGEST).is_err());
    p.scenes[0].beats.push(Beat {
        id: Uuid::new_v4(),
        label: "Protected late beat".into(),
        objective: "Do not truncate".into(),
        start: at(5, 1),
        duration: at(1, 1),
    });
    assert!(p.inspect_music_cut(first, 1, MUSIC_DIGEST).is_err());
    p.scenes[0].beats.clear();
    let preview = p.inspect_music_cut(first, 1, MUSIC_DIGEST).unwrap();
    assert_eq!(preview.ripple, at(-2, 1));
    assert_eq!(preview.new_duration, at(5, 1));
}

#[test]
fn save_plan_music_grid_stays_regular_revisioned_change() {
    let (mut p, first, _) = test_project();
    let prior_clock = p.production_design.plan.as_ref().unwrap().clock.clone();
    let command = change(&p, first, 2, false);
    let encoded = serde_json::to_string(&command).unwrap();
    assert!(encoded.contains("snap_scene_cut_to_music"));
    let reopened: Change = serde_json::from_str(&encoded).unwrap();
    p.apply_change(&reopened).unwrap();
    assert_eq!(
        p.production_design.plan.as_ref().unwrap().clock,
        prior_clock
    );
}
