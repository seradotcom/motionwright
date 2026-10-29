use motionwright_domain::*;
use uuid::Uuid;

const SOURCE: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn time(seconds: i64) -> RationalTime {
    RationalTime::new(seconds, 1).unwrap()
}

fn project_with_downstream_sfx() -> (Project, Vec<Uuid>) {
    let mut p = Project::new("Exact scene-linked SFX impact review").unwrap();
    for (name, seconds) in [("Opening", 7), ("Middle", 9), ("Final", 6)] {
        p.apply_change(&Change::AddScene {
            name: name.into(),
            objective: format!("Retain the authored {name} scene"),
            duration_seconds: seconds,
        })
        .unwrap();
    }
    let scenes = p.scenes.iter().map(|scene| scene.id).collect::<Vec<_>>();
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "Owner-authored music and SFX WAV".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some(SOURCE.into()),
        source_revision: Some("original-owner-source".into()),
    };
    p.apply_change(&Change::AddAsset {
        asset: asset.clone(),
    })
    .unwrap();
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
    p.apply_change(&Change::SetProductionPlan {
        plan: Some(ProductionPlan {
            objective: "Review valid scene-linked SFX retiming".into(),
            audience: "Editors".into(),
            concept: "Owner marked music grid".into(),
            reference_constraints: vec![],
            exclusions: vec!["No automatic playback acceptance".into()],
            shots,
            clock: ProductionClock::Music {
                asset_id: asset.id,
                beats: vec![time(0), time(5), time(8), time(13), time(18), time(25)],
            },
            approval: None,
        }),
    })
    .unwrap();
    p.apply_change(&Change::UpsertSemanticSfx {
        cue: SemanticSfxCue {
            schema: SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id: scenes[2],
            event: SfxEvent::UiFocus,
            anchor: SfxAnchor::SceneStart,
            offset_frames: 5,
            source_asset_id: asset.id,
            source_sha256: SOURCE.into(),
            source_frames: 30,
            gain_milli_db: -12000,
            attack_frames: 3,
            release_frames: 8,
            tail_policy: Default::default(),
            original_source_attestation: "I authored this original WAV".into(),
        },
    })
    .unwrap();
    assert!(p.audio.cues.is_empty());
    assert!(p.markers.is_empty());
    assert!(p.audio.transcript.is_empty());
    p.validate().unwrap();
    (p, scenes)
}

#[test]
fn valid_single_cut_retimes_downstream_sfx_only_after_ack() {
    let (mut p, scenes) = project_with_downstream_sfx();
    let before = p.validated_sfx_schedule().unwrap();
    let review = p.inspect_music_cut(scenes[0], 2, SOURCE).unwrap();
    assert_eq!(review.affected_cue_count, 0);
    assert_eq!(review.affected_marker_count, 0);
    assert_eq!(review.affected_transcript_count, 0);
    assert_eq!(review.affected_sfx_cue_count, 1);
    assert!(review.anchors_need_acknowledgement());
    let original = p.clone();
    let denial = p
        .apply_change(&Change::SnapSceneCutToMusic {
            scene_id: scenes[0],
            beat_index: 2,
            source_sha256: SOURCE.into(),
            base_revision: p.revision,
            acknowledge_absolute_anchors: false,
        })
        .unwrap_err();
    assert!(denial.to_string().contains("SFX"));
    assert_eq!(p, original);
    p.apply_change(&Change::SnapSceneCutToMusic {
        scene_id: scenes[0],
        beat_index: 2,
        source_sha256: SOURCE.into(),
        base_revision: p.revision,
        acknowledge_absolute_anchors: true,
    })
    .unwrap();
    p.validate().unwrap();
    let after = p.validated_sfx_schedule().unwrap();
    assert_eq!(
        after[0].absolute_start_frame,
        before[0].absolute_start_frame + 30
    );
    assert_eq!(p.audio.sfx_cues, original.audio.sfx_cues);
}

#[test]
fn valid_multi_scene_conform_retimes_sfx_only_after_ack() {
    let (mut p, scenes) = project_with_downstream_sfx();
    let before = p.validated_sfx_schedule().unwrap();
    let targets = vec![
        MusicConformTarget {
            scene_id: scenes[0],
            beat_index: 2,
        },
        MusicConformTarget {
            scene_id: scenes[1],
            beat_index: 4,
        },
    ];
    let review = p.inspect_music_conform(&targets, SOURCE).unwrap();
    assert_eq!(review.affected_cue_count, 0);
    assert_eq!(review.affected_marker_count, 0);
    assert_eq!(review.affected_transcript_count, 0);
    assert_eq!(review.affected_sfx_cue_count, 1);
    assert!(review.anchors_need_acknowledgement());
    let original = p.clone();
    let denial = p
        .apply_change(&Change::ConformScenesToMusic {
            targets: targets.clone(),
            source_sha256: SOURCE.into(),
            base_revision: p.revision,
            acknowledge_absolute_anchors: false,
        })
        .unwrap_err();
    assert!(denial.to_string().contains("SFX"));
    assert_eq!(p, original);
    p.apply_change(&Change::ConformScenesToMusic {
        targets,
        source_sha256: SOURCE.into(),
        base_revision: p.revision,
        acknowledge_absolute_anchors: true,
    })
    .unwrap();
    p.validate().unwrap();
    let after = p.validated_sfx_schedule().unwrap();
    assert_eq!(
        after[0].absolute_start_frame,
        before[0].absolute_start_frame + 60
    );
    assert_eq!(p.audio.sfx_cues, original.audio.sfx_cues);
}
