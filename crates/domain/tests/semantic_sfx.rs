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
