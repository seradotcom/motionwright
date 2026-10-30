use motionwright_domain::*;
use uuid::Uuid;

fn fixture() -> (Project, Uuid, Asset) {
    let mut p = Project::new("Original SFX choreography").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Owned sonic scene".into(),
        objective: "Semantically anchored original sound".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scene = p.scenes[0].id;
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "Owner original WAV".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: Some("fixture-original-not-rights-verified".into()),
    };
    p.apply_change(&Change::AddAsset {
        asset: asset.clone(),
    })
    .unwrap();
    (p, scene, asset)
}
fn cue(scene: Uuid, asset: &Asset) -> SemanticSfxCue {
    SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id: Uuid::new_v4(),
        scene_id: scene,
        event: SfxEvent::UiFocus,
        anchor: SfxAnchor::SceneStart,
        offset_frames: 5,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        source_frames: 30,
        gain_milli_db: -12000,
        attack_frames: 3,
        release_frames: 8,
        tail_policy: Default::default(),
        original_source_attestation: "I authored this original WAV for the test scene".into(),
    }
}
#[test]
fn exact_48k_stereo_cue_placement_is_editable_and_revisioned() {
    let (mut p, scene, asset) = fixture();
    let sfx = cue(scene, &asset);
    let schedule = sfx.validate_in(&p).unwrap();
    assert_eq!(
        (schedule.absolute_start_frame, schedule.absolute_end_frame),
        (5, 35)
    );
    assert_eq!(schedule.first_sample, 8000);
    assert_eq!(schedule.sample_count, 48000);
    let original = p.scenes[0].clone();
    p.apply_change(&Change::UpsertSemanticSfx { cue: sfx.clone() })
        .unwrap();
    assert_eq!(p.audio.sfx_cues, vec![sfx.clone()]);
    assert_eq!(p.scenes[0].nodes, original.nodes);
    assert_eq!(p.scenes[0].status, SceneStatus::Draft);
    assert_eq!(p.validated_sfx_schedule().unwrap(), vec![schedule]);
    let copy: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(copy, p);
    let mut louder = sfx.clone();
    louder.gain_milli_db = -14000;
    p.apply_change(&Change::UpsertSemanticSfx {
        cue: louder.clone(),
    })
    .unwrap();
    assert_eq!(p.audio.sfx_cues, vec![louder]);
    assert!(
        p.apply_change(&Change::RemoveAsset { asset_id: asset.id })
            .unwrap_err()
            .to_string()
            .contains("SFX cue")
    );
    p.apply_change(&Change::RemoveSemanticSfx { cue_id: sfx.id })
        .unwrap();
    assert!(p.audio.sfx_cues.is_empty());
    p.apply_change(&Change::RemoveAsset { asset_id: asset.id })
        .unwrap();
}
#[test]
fn beat_relative_audio_anchors_have_sample_precise_timing() {
    let (mut p, scene, asset) = fixture();
    let beat = Uuid::new_v4();
    p.scenes[0].beats.push(Beat {
        id: beat,
        label: "Visible UI focus".into(),
        objective: "Focal semantic event".into(),
        start: RationalTime::new(4, 5).unwrap(),
        duration: RationalTime::new(2, 5).unwrap(),
    });
    let c = SemanticSfxCue {
        anchor: SfxAnchor::BeatStart { beat_id: beat },
        offset_frames: -4,
        ..cue(scene, &asset)
    };
    let schedule = c.validate_in(&p).unwrap();
    assert_eq!(schedule.absolute_start_frame, 20);
    assert_eq!(schedule.first_sample, 32000);
    let mut invalid = c.clone();
    invalid.anchor = SfxAnchor::BeatStart {
        beat_id: Uuid::new_v4(),
    };
    assert!(
        invalid
            .validate_in(&p)
            .unwrap_err()
            .to_string()
            .contains("beat is absent")
    );
    p.scenes[0].beats[0].start = RationalTime::new(1, 43).unwrap();
    assert!(
        c.validate_in(&p)
            .unwrap_err()
            .to_string()
            .contains("exactly at 30 fps")
    );
}
#[test]
fn invalid_source_rights_gain_release_position_and_unknown_keys_are_denied() {
    let (p, scene, asset) = fixture();
    let base = cue(scene, &asset);
    for c in [
        SemanticSfxCue {
            source_sha256: "b".repeat(64),
            ..base.clone()
        },
        SemanticSfxCue {
            source_asset_id: Uuid::new_v4(),
            ..base.clone()
        },
        SemanticSfxCue {
            original_source_attestation: "".into(),
            ..base.clone()
        },
        SemanticSfxCue {
            gain_milli_db: 1,
            ..base.clone()
        },
        SemanticSfxCue {
            release_frames: 30,
            ..base.clone()
        },
        SemanticSfxCue {
            source_frames: 0,
            ..base.clone()
        },
        SemanticSfxCue {
            offset_frames: -10,
            ..base.clone()
        },
        SemanticSfxCue {
            offset_frames: 31,
            ..base.clone()
        },
        SemanticSfxCue {
            scene_id: Uuid::new_v4(),
            ..base.clone()
        },
    ] {
        assert!(c.validate_in(&p).is_err());
    }
    let mut external = serde_json::to_value(&base).unwrap();
    external["unlicensed_audio_url"] = serde_json::json!("https://audio.invalid/file.wav");
    assert!(serde_json::from_value::<SemanticSfxCue>(external).is_err());
    let mut bad_anchor = serde_json::to_value(&base).unwrap();
    bad_anchor["anchor"]["callback"] = serde_json::json!("unsafe");
    assert!(serde_json::from_value::<SemanticSfxCue>(bad_anchor).is_err());
}
#[test]
fn duplicate_and_more_than_four_overlapping_source_events_fail_closed() {
    let (mut p, scene, asset) = fixture();
    let c = cue(scene, &asset);
    p.apply_change(&Change::UpsertSemanticSfx { cue: c.clone() })
        .unwrap();
    p.audio.sfx_cues.push(c.clone());
    assert!(p.validate().unwrap_err().to_string().contains("Duplicate"));
    p.audio.sfx_cues.pop();
    for _ in 0..3 {
        let c = cue(scene, &asset);
        p.apply_change(&Change::UpsertSemanticSfx { cue: c })
            .unwrap();
    }
    let snapshot = p.clone();
    assert_eq!(p.audio.sfx_cues.len(), 4);
    assert!(
        p.apply_change(&Change::UpsertSemanticSfx {
            cue: cue(scene, &asset)
        })
        .unwrap_err()
        .to_string()
        .contains("four simultaneous")
    );
    assert_eq!(p, snapshot);
}
#[test]
fn legacy_project_deserializes_without_sfx_sources_or_manufactured_mix() {
    let (p, _, _) = fixture();
    let mut legacy = serde_json::to_value(p).unwrap();
    legacy["audio"].as_object_mut().unwrap().remove("sfx_cues");
    let read: Project = serde_json::from_value(legacy).unwrap();
    assert!(read.audio.sfx_cues.is_empty());
    assert!(read.validated_sfx_schedule().unwrap().is_empty());
}

#[test]
fn sfx_anchor_wire_accepts_only_exact_variant_fields() {
    let beat_id = Uuid::new_v4();
    let canonical = [
        (
            SfxAnchor::SceneStart,
            serde_json::json!({"kind":"scene_start"}),
        ),
        (
            SfxAnchor::BeatStart { beat_id },
            serde_json::json!({"kind":"beat_start","beat_id":beat_id}),
        ),
    ];
    for (anchor, expected) in canonical {
        assert_eq!(serde_json::to_value(&anchor).unwrap(), expected);
        assert_eq!(
            serde_json::from_value::<SfxAnchor>(expected).unwrap(),
            anchor
        );
    }

    for invalid in [
        serde_json::json!({"kind":"scene_start","callback":"unsafe"}),
        serde_json::json!({"kind":"scene_start","beat_id":beat_id}),
        serde_json::json!({"kind":"beat_start"}),
        serde_json::json!({"kind":"beat_start","beat_id":beat_id,"callback":"unsafe"}),
        serde_json::json!({"kind":"beat_start","beat_id":null}),
        serde_json::json!({"kind":"untrusted"}),
        serde_json::json!({"beat_id":beat_id}),
    ] {
        assert!(
            serde_json::from_value::<SfxAnchor>(invalid.clone()).is_err(),
            "accepted invalid anchor: {invalid}"
        );
    }

    assert!(
        serde_json::from_str::<SfxAnchor>(r#"{"kind":"scene_start","kind":"scene_start"}"#)
            .is_err()
    );
    let duplicate_beat =
        format!(r#"{{"kind":"beat_start","beat_id":"{beat_id}","beat_id":"{beat_id}"}}"#);
    assert!(serde_json::from_str::<SfxAnchor>(&duplicate_beat).is_err());
}

fn cross_cut_fixture() -> (Project, SemanticSfxCue) {
    let (mut project, scene, asset) = fixture();
    project
        .apply_change(&Change::SetSceneDuration {
            scene_id: scene,
            duration: RationalTime::new(1, 1).unwrap(),
        })
        .unwrap();
    for name in ["Tail continuation", "Final room"] {
        project
            .apply_change(&Change::AddScene {
                name: name.into(),
                objective: "Preserve the full authored tail".into(),
                duration_seconds: 1,
            })
            .unwrap();
    }
    let mut source = cue(scene, &asset);
    source.offset_frames = 15;
    source.tail_policy = SfxTailPolicy::ContinueAcrossCuts;
    (project, source)
}

#[test]
fn tail_is_opt_in_and_onset_and_final_project_bound_remain_exact() {
    let (mut project, source) = cross_cut_fixture();
    let mut legacy = source.clone();
    legacy.tail_policy = SfxTailPolicy::WithinScene;
    assert!(legacy.validate_in(&project).is_err());
    let placement = source.validate_in(&project).unwrap();
    assert_eq!(
        (placement.absolute_start_frame, placement.absolute_end_frame),
        (15, 45)
    );
    assert_eq!(
        (placement.first_sample, placement.sample_count),
        (24_000, 48_000)
    );
    let old_json = serde_json::to_value(&legacy).unwrap();
    assert!(old_json.get("tail_policy").is_none());
    assert_eq!(
        serde_json::from_value::<SemanticSfxCue>(old_json).unwrap(),
        legacy
    );
    let encoded = serde_json::to_value(&source).unwrap();
    assert_eq!(encoded["tail_policy"], "continue_across_cuts");
    for value in [
        serde_json::json!(true),
        serde_json::json!("trim"),
        serde_json::Value::Null,
    ] {
        let mut invalid = encoded.clone();
        invalid["tail_policy"] = value;
        assert!(serde_json::from_value::<SemanticSfxCue>(invalid).is_err());
    }
    let mut outside = source.clone();
    outside.offset_frames = 30;
    assert!(outside.validate_in(&project).is_err()); // onset is not inside [0,30)
    project.scenes.truncate(1);
    assert!(source.validate_in(&project).is_err()); // full 45-frame tail does not fit
}

#[test]
fn profile_schedule_uses_authored_selection_order_and_only_omits_excluded_scene_cues() {
    let (mut project, source) = cross_cut_fixture();
    project
        .apply_change(&Change::UpsertSemanticSfx {
            cue: source.clone(),
        })
        .unwrap();
    let profile_id = project.deliverables[0].id;
    let mut profile = project.deliverables[0].clone();
    let ids = project
        .scenes
        .iter()
        .map(|scene| scene.id)
        .collect::<Vec<_>>();
    // Author this order on the project; a delivery selection cannot reorder it.
    project
        .apply_change(&Change::MoveScene {
            scene_id: source.scene_id,
            to_index: 1,
        })
        .unwrap();
    profile.included_scene_ids = vec![ids[1], ids[0], ids[2]];
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: profile.clone(),
        })
        .unwrap();
    let schedule = project
        .validated_sfx_schedule_for_profile(profile_id)
        .unwrap();
    assert_eq!(schedule.len(), 1);
    assert_eq!(
        (
            schedule[0].absolute_start_frame,
            schedule[0].absolute_end_frame
        ),
        (45, 75)
    );
    assert_eq!(
        (schedule[0].first_sample, schedule[0].sample_count),
        (72_000, 48_000)
    );
    assert_eq!(project.sfx_cut_frames(profile_id).unwrap(), 90);
    assert_eq!(project.audio.sfx_cues, vec![source.clone()]);
    profile.included_scene_ids = vec![ids[1], ids[2]];
    project
        .apply_change(&Change::UpsertDeliverable { profile })
        .unwrap();
    assert!(
        project
            .validated_sfx_schedule_for_profile(profile_id)
            .unwrap()
            .is_empty()
    );
    assert_eq!(project.audio.sfx_cues, vec![source]);
}

#[test]
fn invalid_tail_cut_retime_reorder_beat_removal_and_asset_removal_are_atomic() {
    let (mut project, mut source) = cross_cut_fixture();
    let beat = Beat {
        id: Uuid::new_v4(),
        label: "Tail onset".into(),
        objective: "Explicit onset".into(),
        start: RationalTime::new(1, 2).unwrap(),
        duration: RationalTime::new(1, 4).unwrap(),
    };
    project
        .apply_change(&Change::UpsertSceneBeat {
            scene_id: source.scene_id,
            beat: beat.clone(),
        })
        .unwrap();
    source.source_frames = 75; // The full tail ends at the 90-frame project boundary.
    source.anchor = SfxAnchor::BeatStart { beat_id: beat.id };
    source.offset_frames = 0;
    project
        .apply_change(&Change::UpsertSemanticSfx {
            cue: source.clone(),
        })
        .unwrap();
    let mut profile = project.deliverables[0].clone();
    profile.included_scene_ids = vec![source.scene_id];
    let before = project.clone();
    for change in [
        Change::MoveScene {
            scene_id: source.scene_id,
            to_index: 2,
        },
        Change::SetSceneDuration {
            scene_id: project.scenes[1].id,
            duration: RationalTime::new(1, 2).unwrap(),
        },
        Change::UpsertDeliverable { profile },
        Change::RemoveSceneBeat {
            scene_id: source.scene_id,
            beat_id: beat.id,
        },
        Change::RemoveAsset {
            asset_id: source.source_asset_id,
        },
    ] {
        assert!(
            project.apply_change(&change).is_err(),
            "accepted invalid change: {change:?}"
        );
        assert_eq!(project, before);
    }
    let mut invalid = source;
    invalid.source_sha256 = "f".repeat(64);
    assert!(
        project
            .apply_change(&Change::UpsertSemanticSfx { cue: invalid })
            .is_err()
    );
    assert_eq!(project, before);
}

#[test]
fn tail_and_position_changes_are_bound_into_the_consuming_profile_audio_fingerprint() {
    let (mut project, source) = cross_cut_fixture();
    project
        .apply_change(&Change::UpsertSemanticSfx {
            cue: source.clone(),
        })
        .unwrap();
    let profile_id = project.deliverables[0].id;
    let before = variant_dependency_fingerprint(&project, profile_id).unwrap();
    let mut quieter = source.clone();
    quieter.gain_milli_db -= 1000;
    project
        .apply_change(&Change::UpsertSemanticSfx { cue: quieter })
        .unwrap();
    let after = variant_dependency_fingerprint(&project, profile_id).unwrap();
    assert_eq!(before.frame_inputs_sha256, after.frame_inputs_sha256);
    assert_ne!(before.audio_inputs_sha256, after.audio_inputs_sha256);
    let mut profile = project.deliverables[0].clone();
    profile.included_scene_ids = vec![project.scenes[1].id];
    project
        .apply_change(&Change::UpsertDeliverable { profile })
        .unwrap();
    let excluded = variant_dependency_fingerprint(&project, profile_id).unwrap();
    project
        .apply_change(&Change::UpsertSemanticSfx { cue: source })
        .unwrap();
    assert_eq!(
        variant_dependency_fingerprint(&project, profile_id).unwrap(),
        excluded
    );
}

#[test]
fn a_stored_output_format_is_not_native_realization_authority() {
    let (mut project, source) = cross_cut_fixture();
    project
        .apply_change(&Change::UpsertSemanticSfx { cue: source })
        .unwrap();
    let mut profile = project.deliverables[0].clone();
    profile.frame_rate = RationalTime::new(24, 1).unwrap();
    project
        .apply_change(&Change::UpsertDeliverable { profile })
        .unwrap();
    project.validate().unwrap(); // native SFX admission separately returns Unsupported
}
