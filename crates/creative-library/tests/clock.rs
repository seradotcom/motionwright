use motionwright_creative_library::{
    self as craft, AuthoringClock, ClockAnchor, ComponentRequest, CopyPack, CreativeClockMap,
    Locale, ProceduralOptions, RecipeId, SemanticSoundEvent, SoundCue, native,
    schedule_original_sound, sound_component,
};
use uuid::Uuid;
fn clock() -> CreativeClockMap {
    CreativeClockMap {
        version: 1,
        source_clock: AuthoringClock::Timeline,
        source_rate: native::FrameRate {
            num: 30000,
            den: 1001,
        },
        timeline_sample_rate: 48000,
        source_revision_sha256: "ab".repeat(32),
        anchors: vec![
            ClockAnchor {
                source_tick: 0,
                timeline_sample: 0,
            },
            ClockAnchor {
                source_tick: 3000,
                timeline_sample: 4_804_800,
            },
        ],
    }
}
fn fixture(recipe: RecipeId) -> ComponentRequest {
    ComponentRequest {
        instance_id: Uuid::from_u128(42),
        recipe,
        version: 1,
        output: native::Canvas {
            width: 640,
            height: 360,
            rate: native::FrameRate { num: 30, den: 1 },
            frames: 90,
            background: None,
        },
        copy: CopyPack::editorial(Locale::En),
        locale: Locale::En,
        seed: 17,
        motion: true,
        data: None,
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    }
}
fn cue(at: u64, approved: bool, kind: SemanticSoundEvent) -> SoundCue {
    SoundCue {
        id: Uuid::from_u128(2),
        scene_id: Uuid::from_u128(3),
        source_revision_sha256: "ab".repeat(32),
        description: "A deliberate focus on an edited product relationship".into(),
        source_tick: at,
        semantic_event: kind,
        human_approved: approved,
    }
}
#[test]
fn rational_frame_to_sample_alignment_never_accumulates_fractional_rounding() {
    let map = clock();
    map.validate().unwrap();
    let first = map.map_source_tick(1).unwrap();
    assert_eq!((first.whole, first.sub_num, first.sub_den), (1601, 3, 5));
    assert!(first.require_whole_sample().is_err());
    let fifth = map.map_source_tick(5).unwrap();
    assert_eq!(fifth.require_whole_sample().unwrap(), 8008);
    assert_eq!(
        map.map_source_tick(3000)
            .unwrap()
            .require_whole_sample()
            .unwrap(),
        4_804_800
    );
    // Equal source segments preserve the original rational timeline exactly.
    let mut split = map.clone();
    split.anchors.insert(
        1,
        ClockAnchor {
            source_tick: 1500,
            timeline_sample: 2_402_400,
        },
    );
    for index in [0, 1, 5, 17, 100, 1555, 3000] {
        assert_eq!(
            map.map_source_tick(index).unwrap(),
            split.map_source_tick(index).unwrap()
        );
    }
}
#[test]
fn explicit_original_sound_cue_is_at_an_exact_sample_and_does_not_create_a_new_clock() {
    let source = sound_component(&fixture(RecipeId::FocusHit)).unwrap();
    let before = source.clone();
    let map = clock();
    let positioned = schedule_original_sound(
        &source,
        &cue(5, true, SemanticSoundEvent::Focus),
        &map,
        48000 * 10,
    )
    .unwrap();
    assert_eq!(positioned.sample_frames, 480000);
    assert_eq!(positioned.voices[0].at_sample, 8008);
    assert_eq!(
        source, before,
        "Scheduling must not mutate the authored original score"
    );
    assert!(
        positioned
            .voices
            .iter()
            .all(|voice| voice.at_sample >= 8008)
    );
    assert_eq!(positioned.gain_envelope.last().unwrap().sample, 480000 - 1);
    positioned.validate().unwrap();
}
#[test]
fn fractional_samples_and_unapproved_or_stale_cues_are_explicit_conflicts() {
    let source = sound_component(&fixture(RecipeId::FocusHit)).unwrap();
    let map = clock();
    assert!(
        schedule_original_sound(
            &source,
            &cue(1, true, SemanticSoundEvent::Focus),
            &map,
            48000 * 10
        )
        .is_err()
    );
    assert!(
        schedule_original_sound(
            &source,
            &cue(5, false, SemanticSoundEvent::Focus),
            &map,
            48000 * 10
        )
        .is_err()
    );
    assert!(
        schedule_original_sound(
            &source,
            &cue(5, true, SemanticSoundEvent::Transition),
            &map,
            48000 * 10
        )
        .is_err()
    );
    let mut stale = cue(5, true, SemanticSoundEvent::Focus);
    stale.source_revision_sha256 = "cd".repeat(32);
    assert!(schedule_original_sound(&source, &stale, &map, 48000 * 10).is_err());
}
#[test]
fn clock_does_not_extrapolate_or_accept_decreasing_or_forged_rate() {
    let mut map = clock();
    assert!(map.map_source_tick(3001).is_err());
    map.anchors[1].timeline_sample = 0;
    assert!(map.validate().is_err());
    let mut map = clock();
    map.source_rate.den = 0;
    assert!(map.validate().is_err());
}
