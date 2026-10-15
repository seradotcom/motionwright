use motionwright_domain::*;
use uuid::Uuid;

const AUDIO_SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn at(seconds: i64) -> RationalTime {
    RationalTime::new(seconds, 1).unwrap()
}
fn setup() -> (Project, Vec<Uuid>) {
    let mut project = Project::new("Source-locked original multi-scene music edit").unwrap();
    for (title, duration) in [("Opening", 7), ("Proof", 9), ("Closing", 6)] {
        project
            .apply_change(&Change::AddScene {
                name: title.into(),
                objective: format!("Keep the original editable {title} shot"),
                duration_seconds: duration,
            })
            .unwrap();
    }
    let scene_ids = project.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
    let source_id = Uuid::new_v4();
    project
        .apply_change(&Change::AddAsset {
            asset: Asset {
                id: source_id,
                name: "Owner-authored soundtrack source.wav".into(),
                media_type: "audio/wav".into(),
                content_sha256: Some(AUDIO_SHA.into()),
                source_revision: Some("manual-beat-authoring".into()),
            },
        })
        .unwrap();
    let shots = project
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
    project
        .apply_change(&Change::SetProductionPlan {
            plan: Some(ProductionPlan {
                objective: "Conform two precise storyboard cuts in one CAS".into(),
                audience: "Editors".into(),
                concept: "Review the exact saved original music beat boundaries".into(),
                reference_constraints: vec![],
                exclusions: vec!["No automatic audio mix or source rights assumption".into()],
                shots,
                clock: ProductionClock::Music {
                    asset_id: source_id,
                    beats: vec![at(0), at(5), at(8), at(13), at(18), at(25)],
                },
                approval: None,
            }),
        })
        .unwrap();
    (project, scene_ids)
}
fn targets(ids: &[Uuid]) -> Vec<MusicConformTarget> {
    vec![
        MusicConformTarget {
            scene_id: ids[0],
            beat_index: 2,
        },
        MusicConformTarget {
            scene_id: ids[1],
            beat_index: 4,
        },
    ]
}
fn change(project: &Project, ids: &[Uuid], ack: bool) -> Change {
    Change::ConformScenesToMusic {
        targets: targets(ids),
        source_sha256: AUDIO_SHA.into(),
        base_revision: project.revision,
        acknowledge_absolute_anchors: ack,
    }
}
#[test]
fn two_distinct_cut_boundaries_reflow_exactly_in_one_atomic_revision() {
    let (mut project, ids) = setup();
    let untouched = project.clone();
    let preview = project
        .inspect_music_conform(&targets(&ids), AUDIO_SHA)
        .unwrap();
    assert_eq!(project, untouched);
    assert_eq!(preview.changed_scene_count, 3);
    assert_eq!(preview.original_duration, at(22));
    assert_eq!(preview.conformed_duration, at(24));
    assert_eq!(preview.scenes[0].new_duration, at(8));
    assert_eq!(preview.scenes[1].new_start, at(8));
    assert_eq!(preview.scenes[1].new_duration, at(10));
    assert_eq!(preview.scenes[2].new_start, at(18));
    let revision = project.revision;
    project
        .apply_change(&change(&project, &ids, false))
        .unwrap();
    // Domain apply_change mutates the in-memory candidate but deliberately
    // does not mint a persistent revision. Exactly one revision is allocated
    // by StudioService's authenticated CAS transaction, which the separate
    // music_conform_persistence test verifies against real SQLite storage.
    assert_eq!(project.revision, revision);
    assert_eq!(project.scenes[0].duration, at(8));
    assert_eq!(project.scenes[1].duration, at(10));
    assert_eq!(project.scenes[2].start, at(18));
    assert!(
        project
            .scenes
            .iter()
            .all(|scene| scene.status == SceneStatus::Draft)
    );
    project.validate().unwrap();
    let reopened: Project =
        serde_json::from_str(&serde_json::to_string(&project).unwrap()).unwrap();
    assert_eq!(reopened, project);
}
#[test]
fn invalid_second_boundary_does_not_mutate_valid_first_boundary() {
    let (mut project, ids) = setup();
    let baseline = project.clone();
    let mut bad = targets(&ids);
    bad[1].beat_index = 2; // Both cuts would end at exactly 8s.
    assert!(project.inspect_music_conform(&bad, AUDIO_SHA).is_err());
    assert!(
        project
            .apply_change(&Change::ConformScenesToMusic {
                targets: bad,
                source_sha256: AUDIO_SHA.into(),
                base_revision: project.revision,
                acknowledge_absolute_anchors: true,
            })
            .is_err()
    );
    assert_eq!(project, baseline);
}
#[test]
fn source_digest_and_project_revision_are_authoritative() {
    let (mut project, ids) = setup();
    let baseline = project.clone();
    let mut stale = change(&project, &ids, true);
    if let Change::ConformScenesToMusic {
        ref mut base_revision,
        ..
    } = stale
    {
        *base_revision += 1;
    }
    assert!(project.apply_change(&stale).is_err());
    assert_eq!(project, baseline);
    assert!(
        project
            .inspect_music_conform(&targets(&ids), &"A".repeat(64))
            .is_err()
    );
    let wrong = Change::ConformScenesToMusic {
        targets: targets(&ids),
        source_sha256: "b".repeat(64),
        base_revision: project.revision,
        acknowledge_absolute_anchors: true,
    };
    assert!(project.apply_change(&wrong).is_err());
    assert_eq!(project, baseline);
}
#[test]
fn unordered_duplicate_and_missing_target_sources_fail_closed() {
    let (project, ids) = setup();
    let mut unordered = targets(&ids);
    unordered.swap(0, 1);
    assert!(
        project
            .inspect_music_conform(&unordered, AUDIO_SHA)
            .is_err()
    );
    let dup = vec![targets(&ids)[0].clone(), targets(&ids)[0].clone()];
    assert!(project.inspect_music_conform(&dup, AUDIO_SHA).is_err());
    let missing = vec![
        MusicConformTarget {
            scene_id: Uuid::new_v4(),
            beat_index: 0,
        },
        MusicConformTarget {
            scene_id: ids[1],
            beat_index: 4,
        },
    ];
    assert!(project.inspect_music_conform(&missing, AUDIO_SHA).is_err());
    assert!(
        project
            .inspect_music_conform(&targets(&ids)[..1], AUDIO_SHA)
            .is_err()
    );
    let excessive_targets = vec![targets(&ids)[0].clone(); 17];
    assert!(
        project
            .inspect_music_conform(&excessive_targets, AUDIO_SHA)
            .is_err()
    );
}
#[test]
fn all_downstream_timing_locks_and_delivery_profiles_are_protected() {
    let (mut project, ids) = setup();
    let base = project.clone();
    project.locks.push(ProjectLock {
        id: Uuid::new_v4(),
        resource: format!("scene:{}", ids[2]),
        kind: LockKind::Timing,
        note: "Human-approved closing scene timing".into(),
    });
    assert!(matches!(
        project.inspect_music_conform(&targets(&ids), AUDIO_SHA),
        Err(DomainError::Locked(_))
    ));
    project = base;
    if let Some(profile) = project.deliverables.first_mut() {
        profile.timing_locked = true;
        profile.included_scene_ids = vec![ids[2]];
        assert!(matches!(
            project.inspect_music_conform(&targets(&ids), AUDIO_SHA),
            Err(DomainError::Locked(_))
        ));
    }
}
#[test]
fn changing_boundaries_near_absolute_audio_requires_explicit_acknowledgement() {
    let (mut project, ids) = setup();
    project.audio.cues.push(AudioCue {
        id: Uuid::new_v4(),
        label: "Original voice entrance".into(),
        at: at(10),
        source_segment_id: None,
        evidence: CueEvidence::Manual,
    });
    project.markers.push(Marker {
        id: Uuid::new_v4(),
        at: at(20),
        label: "Original beat".into(),
    });
    let before = project.clone();
    let preview = project
        .inspect_music_conform(&targets(&ids), AUDIO_SHA)
        .unwrap();
    assert_eq!(preview.affected_cue_count, 1);
    assert_eq!(preview.affected_marker_count, 1);
    assert!(preview.anchors_need_acknowledgement());
    assert!(
        project
            .apply_change(&change(&project, &ids, false))
            .is_err()
    );
    assert_eq!(project, before);
    project.apply_change(&change(&project, &ids, true)).unwrap();
    assert_eq!(project.audio.cues, before.audio.cues);
    assert_eq!(project.markers, before.markers);
}
#[test]
fn local_keyframe_or_scene_beat_cannot_be_stranded_by_shortened_batch_cut() {
    let (mut project, ids) = setup();
    let mut shortening = targets(&ids);
    shortening[0].beat_index = 1; // Opening becomes 5 seconds.
    shortening[1].beat_index = 3; // Proof then ends at 13 seconds.
    project.scenes[0].beats.push(Beat {
        id: Uuid::new_v4(),
        label: "Original edited beat".into(),
        objective: "Preserve original shot idea".into(),
        start: at(4),
        duration: at(2),
    });
    assert!(
        project
            .inspect_music_conform(&shortening, AUDIO_SHA)
            .is_err()
    );
    project.scenes[0].beats.clear();
    assert_eq!(
        project
            .inspect_music_conform(&shortening, AUDIO_SHA)
            .unwrap()
            .scenes[0]
            .new_duration,
        at(5)
    );
}
#[test]
fn all_preexisting_aligned_boundaries_produce_no_new_mutation() {
    let (mut project, ids) = setup();
    let first = change(&project, &ids, false);
    project.apply_change(&first).unwrap();
    let snapshot = project.clone();
    assert!(
        project
            .inspect_music_conform(&targets(&ids), AUDIO_SHA)
            .is_err()
    );
    assert!(
        project
            .apply_change(&change(&project, &ids, false))
            .is_err()
    );
    assert_eq!(project, snapshot);
}
#[test]
fn semantic_change_roundtrip_has_fixed_bounded_fields() {
    let (project, ids) = setup();
    let change = change(&project, &ids, true);
    let encoded = serde_json::to_string(&change).unwrap();
    assert!(encoded.contains("conform_scenes_to_music"));
    assert!(encoded.contains("beat_index"));
    let decoded: Change = serde_json::from_str(&encoded).unwrap();
    let mut next = project;
    next.apply_change(&decoded).unwrap();
    assert_eq!(next.scenes[1].start, at(8));
}

fn scene_linked_sfx(project: &mut Project, scene_id: Uuid, offset: i16, frames: u16) -> Uuid {
    let id = Uuid::new_v4();
    let source_asset_id = project
        .assets
        .iter()
        .find(|asset| asset.content_sha256.as_deref() == Some(AUDIO_SHA))
        .unwrap()
        .id;
    project.audio.sfx_cues.push(SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id,
        scene_id,
        event: SfxEvent::Transition,
        anchor: SfxAnchor::SceneStart,
        offset_frames: offset,
        source_asset_id,
        source_sha256: AUDIO_SHA.into(),
        source_frames: frames,
        gain_milli_db: -18_000,
        attack_frames: 0,
        release_frames: 10,
        original_source_attestation: "Original owner-authored edit fixture".into(),
    });
    project.validate().unwrap();
    id
}

#[test]
fn shortening_batch_cut_rejects_truncated_semantic_sfx_tail_before_mutation() {
    let (mut project, ids) = setup();
    let cue_id = scene_linked_sfx(&mut project, ids[0], 30, 180);
    let original = project.clone();
    let shortening = vec![
        MusicConformTarget {
            scene_id: ids[0],
            beat_index: 1,
        }, // 7s -> 5s
        MusicConformTarget {
            scene_id: ids[1],
            beat_index: 3,
        }, // 16s -> 13s
    ];
    let err = project
        .inspect_music_conform(&shortening, AUDIO_SHA)
        .unwrap_err()
        .to_string();
    assert!(err.contains("semantic SFX tail"), "{err}");
    assert!(
        project
            .apply_change(&Change::ConformScenesToMusic {
                targets: shortening,
                source_sha256: AUDIO_SHA.into(),
                base_revision: project.revision,
                acknowledge_absolute_anchors: true,
            })
            .is_err()
    );
    assert_eq!(project, original, "no partial scene or cue mutation");
    assert_eq!(project.audio.sfx_cues[0].id, cue_id);
}

#[test]
fn valid_ripple_discloses_scene_bound_sfx_and_keeps_its_original_source() {
    let (mut project, ids) = setup();
    let cue_id = scene_linked_sfx(&mut project, ids[2], 0, 90);
    let original = project.clone();
    let before = project.validated_sfx_schedule().unwrap();
    assert_eq!(before[0].absolute_start_frame, 16 * 30);
    let preview = project
        .inspect_music_conform(&targets(&ids), AUDIO_SHA)
        .unwrap();
    assert_eq!(preview.affected_sfx_cue_count, 1);
    assert!(preview.anchors_need_acknowledgement());
    assert!(
        project
            .apply_change(&change(&project, &ids, false))
            .is_err()
    );
    assert_eq!(project, original);
    project.apply_change(&change(&project, &ids, true)).unwrap();
    assert_eq!(project.audio.sfx_cues, original.audio.sfx_cues);
    let after = project.validated_sfx_schedule().unwrap();
    assert_eq!(after[0].cue_id, cue_id);
    assert_eq!(after[0].absolute_start_frame, 18 * 30);
    assert_eq!(after[0].sample_count, before[0].sample_count);
}

#[test]
fn single_cut_preview_and_apply_preserve_sfx_tail_without_partial_write() {
    let (mut project, ids) = setup();
    scene_linked_sfx(&mut project, ids[0], 30, 180);
    let original = project.clone();
    assert!(
        project
            .inspect_music_cut(ids[0], 1, AUDIO_SHA)
            .unwrap_err()
            .to_string()
            .contains("semantic SFX tail")
    );
    assert!(
        project
            .apply_change(&Change::SnapSceneCutToMusic {
                scene_id: ids[0],
                beat_index: 1,
                source_sha256: AUDIO_SHA.into(),
                base_revision: project.revision,
                acknowledge_absolute_anchors: true,
            })
            .is_err()
    );
    assert_eq!(project, original);
}

#[test]
fn single_cut_discloses_rippled_sfx_and_requires_acknowledgement() {
    let (mut project, ids) = setup();
    scene_linked_sfx(&mut project, ids[2], 0, 30);
    let before = project.clone();
    let review = project.inspect_music_cut(ids[0], 2, AUDIO_SHA).unwrap();
    assert_eq!(review.affected_sfx_cue_count, 1);
    assert!(review.anchors_need_acknowledgement());
    assert!(
        project
            .apply_change(&Change::SnapSceneCutToMusic {
                scene_id: ids[0],
                beat_index: 2,
                source_sha256: AUDIO_SHA.into(),
                base_revision: project.revision,
                acknowledge_absolute_anchors: false,
            })
            .is_err()
    );
    assert_eq!(project, before);
    project
        .apply_change(&Change::SnapSceneCutToMusic {
            scene_id: ids[0],
            beat_index: 2,
            source_sha256: AUDIO_SHA.into(),
            base_revision: project.revision,
            acknowledge_absolute_anchors: true,
        })
        .unwrap();
    assert_eq!(project.audio.sfx_cues, before.audio.sfx_cues);
    assert_eq!(
        project.validated_sfx_schedule().unwrap()[0].absolute_start_frame,
        17 * 30
    );
}
