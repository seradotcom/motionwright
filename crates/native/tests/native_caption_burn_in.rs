//! Native Semwright caption overlay: authored, source-bound Film evidence only.
//! Passing these tests never certifies real pixel readability or a final MP4.
use motionwright_domain::{
    AlignmentEvidence, Asset, CanvasNode, Change, CoordinateSpace, NodeStyle, Project,
    RationalTime, RendererKind, TranscriptSegment, VoiceTrack, caption_sidecar,
};
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::{Rate, ResolvedCue};
use semwright_motion_authoring::{
    Archetype, NarrativeRole, SpatialIntent, SubjectContent, realize,
};
use std::collections::BTreeSet;
use uuid::Uuid;

fn fixture() -> Project {
    let mut p = Project::new("Original captions from measured voice").unwrap();
    for name in ["First native scene", "Second native scene"] {
        p.apply_change(&Change::AddScene {
            name: name.into(),
            objective: "Native Film retains editable shapes with measured text timing".into(),
            duration_seconds: 2,
        })
        .unwrap();
    }
    for scene in p.scenes.clone() {
        p.apply_change(&Change::AddCanvasNode {
            scene_id: scene.id,
            node: CanvasNode {
                id: Uuid::new_v4(),
                name: "Original editable panel".into(),
                kind: "rectangle".into(),
                parent_id: None,
                x: 400.0,
                y: 300.0,
                width: 480.0,
                height: 300.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: None,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 2,
                style: NodeStyle {
                    fill: Some("#577888".into()),
                    ..Default::default()
                },
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            },
        })
        .unwrap();
    }
    let asset_id = Uuid::new_v4();
    let voice_id = Uuid::new_v4();
    let hash = "ab".repeat(32);
    p.assets.push(Asset {
        id: asset_id,
        name: "Owner-admitted measured voice.wav".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some(hash.clone()),
        source_revision: Some("source-import-fixture".into()),
    });
    p.audio.voice_tracks.push(VoiceTrack {
        id: voice_id,
        asset_id,
        label: "Owner-authorized voice".into(),
        sample_rate_hz: 48_000,
        channels: 2,
        measured_duration: RationalTime::new(4, 1).unwrap(),
        source_sha256: hash,
        loudness_lufs: None,
        true_peak_dbfs: None,
    });
    for (start, end, text) in [
        (
            RationalTime::new(1, 2).unwrap(),
            RationalTime::new(3, 2).unwrap(),
            "Original first caption",
        ),
        (
            RationalTime::new(5, 2).unwrap(),
            RationalTime::new(7, 2).unwrap(),
            "Original second caption",
        ),
    ] {
        p.audio.transcript.push(TranscriptSegment {
            id: Uuid::new_v4(),
            voice_track_id: voice_id,
            start,
            end,
            text: text.into(),
            speaker: Some("Narrator".into()),
            alignment: AlignmentEvidence::Manual,
        });
    }
    p.deliverables[0].captions = true;
    p.deliverables[0].burn_in_captions = true;
    p.deliverables[0].voice_track_id = Some(voice_id);
    p
}
fn opts(p: &Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: p
            .scenes
            .iter()
            .map(|scene| SceneFilmIntent {
                scene_id: scene.id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            })
            .collect(),
    }
}
fn output(p: &Project) -> Result<Vec<motionwright_native::film::MotionCanvasSegment>, String> {
    build_motion_canvas_segments(p, p.deliverables[0].id, &opts(p)).map_err(|err| err.message)
}

#[test]
fn transcript_enters_native_film_as_timed_drawable_subjects_without_changing_frames() {
    let p = fixture();
    p.validate().unwrap();
    let rendered = output(&p).unwrap();
    assert_eq!(rendered.len(), 1);
    assert_eq!(rendered[0].frame_count, 120);
    let film = &rendered[0].film;
    assert_eq!(film.cues.cues.len(), 2);
    assert!(
        film.assets.is_empty(),
        "a measured WAV is NOT an unregistered visual asset"
    );
    assert!(
        film.editorial
            .type_scale
            .contains_key("mw-native-caption-v1")
    );
    let shots = film.sequences[0]
        .beats
        .iter()
        .flat_map(|b| b.shots.iter())
        .collect::<Vec<_>>();
    assert_eq!(shots.len(), 2);
    let resolved = film.cues.resolve().unwrap();
    for (index, shot) in shots.iter().enumerate() {
        assert_eq!(shot.captions.len(), 1);
        let c = &shot.captions[0];
        let node = shot.subjects.iter().find(|s| s.id == c.subject).unwrap();
        assert_eq!(node.role, "caption");
        assert!(!node.initially_visible);
        let native_parent = node
            .parent
            .as_ref()
            .expect("text must not shift original root flow");
        let wrapper = shot
            .subjects
            .iter()
            .find(|subject| &subject.id == native_parent)
            .unwrap();
        assert!(matches!(&wrapper.content, SubjectContent::Group));
        assert!(matches!(&wrapper.layout, SpatialIntent::Fixed { .. }));
        assert!(wrapper.initially_visible);
        match &node.content {
            SubjectContent::Text { runs, style, .. } => {
                assert_eq!(style, "mw-native-caption-v1");
                assert_eq!(runs[0].text, c.text);
            }
            other => panic!("caption must be native text, got {other:?}"),
        }
        let expected = if index == 0 {
            RationalTime::new(1, 2).unwrap()
        } else {
            RationalTime::new(5, 2).unwrap()
        };
        match resolved.get(&c.cue_id).unwrap() {
            ResolvedCue::Resolved { start, end } => {
                assert_eq!(*start, expected);
                assert_eq!(
                    *end,
                    expected
                        .checked_add(RationalTime::new(1, 1).unwrap())
                        .unwrap()
                );
            }
            _ => panic!("manual timing cannot become an UNKNOWN native cue"),
        }
    }
    assert_eq!(
        realize(film)
            .unwrap()
            .schedule
            .frame_count(&film.output)
            .unwrap(),
        120
    );
    let sidecar = caption_sidecar(&p, p.deliverables[0].id).unwrap();
    assert!(sidecar.body.contains("00:00:00.500 --> 00:00:01.500"));
    assert!(sidecar.body.contains("00:00:02.500 --> 00:00:03.500"));
}

#[test]
fn sidecar_only_preserves_exact_legacy_native_film_semantics() {
    let mut p = fixture();
    p.deliverables[0].burn_in_captions = false;
    let legacy = output(&p).unwrap();
    assert!(legacy.iter().all(|s| s.film.cues.cues.is_empty()));
    assert!(
        legacy
            .iter()
            .flat_map(|s| &s.film.sequences)
            .flat_map(|s| &s.beats)
            .flat_map(|b| &b.shots)
            .all(|shot| shot.captions.is_empty())
    );
    let baseline = serde_json::to_value(&legacy[0].film).unwrap();
    p.audio.transcript[0].text = "A different sidecar-only transcription".into();
    let still = output(&p).unwrap();
    assert_eq!(serde_json::to_value(&still[0].film).unwrap(), baseline);
}

#[test]
fn explicitly_cut_second_scene_rebases_both_native_caption_and_sidecar() {
    let mut p = fixture();
    p.deliverables[0].included_scene_ids = vec![p.scenes[1].id];
    let output = output(&p).unwrap();
    assert_eq!(output.len(), 1);
    assert_eq!(output[0].frame_count, 60);
    assert_eq!(output[0].film.cues.cues.len(), 1);
    let only = output[0].film.cues.resolve().unwrap();
    let ResolvedCue::Resolved { start, end } = only.values().next().unwrap() else {
        panic!("unknown cue")
    };
    assert_eq!(*start, RationalTime::new(1, 2).unwrap());
    assert_eq!(*end, RationalTime::new(3, 2).unwrap());
    let sidecar = caption_sidecar(&p, p.deliverables[0].id).unwrap();
    assert_eq!(sidecar.cue_count, 1);
    assert!(sidecar.body.contains("00:00:00.500 --> 00:00:01.500"));
}

#[test]
fn refuses_incorrect_voice_rights_unmeasured_alignment_and_stale_source() {
    let mut p = fixture();
    p.deliverables[0].voice_track_id = None;
    assert!(
        output(&p)
            .unwrap_err()
            .contains("explicitly selected voice")
    );
    p = fixture();
    p.assets[0].content_sha256 = Some("cd".repeat(32));
    // The Project-wide source custody check can reject forged asset bytes
    // before the narrower Film caption-voice check is reached. Both fail closed.
    assert!(output(&p).is_err());
    p = fixture();
    p.audio.transcript[0].alignment = AlignmentEvidence::Unknown;
    assert!(output(&p).is_err());
    p = fixture();
    p.audio.transcript[0].alignment = AlignmentEvidence::Measured {
        engine: "fixture-aligner".into(),
        source_sha256: "cd".repeat(32),
        confidence_millis: Some(100),
    };
    assert!(
        output(&p)
            .unwrap_err()
            .contains("different voice source digest")
    );
}

#[test]
fn refuses_overlapping_caption_intervals() {
    let mut p = fixture();
    p.audio.transcript[1].start = RationalTime::new(1, 1).unwrap();
    assert!(output(&p).is_err());
}

#[test]
fn crossing_a_native_shot_boundary_preserves_source_and_exact_fragment_coverage() {
    let mut p = fixture();
    p.audio.transcript[0].end = RationalTime::new(5, 2).unwrap();
    let source = serde_json::to_value(&p).unwrap();
    let rendered = output(&p).unwrap();
    assert_eq!(rendered.len(), 1);
    assert_eq!(rendered[0].frame_count, 120);
    let film = &rendered[0].film;
    let shots = film
        .sequences
        .iter()
        .flat_map(|sequence| &sequence.beats)
        .flat_map(|beat| &beat.shots)
        .collect::<Vec<_>>();
    assert_eq!(shots.len(), 2);
    assert_eq!(shots[0].captions.len(), 1);
    assert_eq!(shots[1].captions.len(), 2);
    assert_eq!(film.cues.cues.len(), 3);
    let first_id = p.audio.transcript[0].id.simple().to_string();
    let second_id = p.audio.transcript[1].id.simple().to_string();
    let expected = [
        (
            0,
            0,
            format!("mw-caption-cue-{first_id}-p1"),
            "Original first caption",
            (1, 2),
            (2, 1),
        ),
        (
            1,
            0,
            format!("mw-caption-cue-{first_id}-p2"),
            "Original first caption",
            (2, 1),
            (5, 2),
        ),
        (
            1,
            1,
            format!("mw-caption-cue-{second_id}"),
            "Original second caption",
            (5, 2),
            (7, 2),
        ),
    ];
    let resolved = film.cues.resolve().unwrap();
    for (shot, index, id, text, start, end) in expected {
        let caption = &shots[shot].captions[index];
        assert_eq!(caption.cue_id, id);
        assert_eq!(caption.text, text);
        let cue = film.cues.cues.iter().find(|cue| cue.id == id).unwrap();
        assert_eq!(
            cue.source,
            semwright_semantic_composition::Digest::parse("ab".repeat(32)).unwrap()
        );
        let subject = shots[shot]
            .subjects
            .iter()
            .find(|node| node.id == caption.subject)
            .unwrap();
        let SubjectContent::Text { runs, .. } = &subject.content else {
            panic!("caption fragments must remain editable native text");
        };
        assert_eq!(runs[0].text, text);
        let ResolvedCue::Resolved {
            start: actual_start,
            end: actual_end,
        } = resolved.get(&id).unwrap()
        else {
            panic!("explicit caption timing must resolve");
        };
        assert_eq!(*actual_start, RationalTime::new(start.0, start.1).unwrap());
        assert_eq!(*actual_end, RationalTime::new(end.0, end.1).unwrap());
    }
    assert_eq!(
        realize(film)
            .unwrap()
            .schedule
            .frame_count(&film.output)
            .unwrap(),
        120
    );
    let sidecar = caption_sidecar(&p, p.deliverables[0].id).unwrap();
    assert_eq!(sidecar.cue_count, 2);
    assert!(sidecar.body.contains("00:00:00.500 --> 00:00:02.500"));
    assert!(sidecar.body.contains("00:00:02.500 --> 00:00:03.500"));
    assert_eq!(serde_json::to_value(&p).unwrap(), source);
}

#[test]
fn refuses_caption_times_between_output_frames() {
    let mut p = fixture();
    p.audio.transcript[0].start = RationalTime::new(53, 100).unwrap();
    assert!(
        output(&p)
            .unwrap_err()
            .contains("exact output-frame boundaries")
    );
}

#[test]
fn refuses_caption_intervals_owned_by_an_unrendered_renderer() {
    let mut p = fixture();
    p.scenes[1].renderer = RendererKind::Blender;
    assert!(
        output(&p)
            .unwrap_err()
            .contains("complete contiguous native Motion Canvas coverage")
    );
}

#[test]
fn rejects_oversized_two_line_and_nonprintable_caption_content() {
    let mut p = fixture();
    p.audio.transcript[0].text =
        "An absurdly long caption line intentionally exceeding the bounded authoring budget".into();
    assert!(output(&p).unwrap_err().contains("42 code points"));
    p = fixture();
    p.audio.transcript[0].text = "One\nTwo\nThree".into();
    assert!(output(&p).unwrap_err().contains("one or two lines"));
    p = fixture();
    p.audio.transcript[0].text = "Bad\tcontrol".into();
    assert!(output(&p).is_err());
}
