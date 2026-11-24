use motionwright_domain::*;
use uuid::Uuid;

fn fixture() -> Project {
    let mut p = Project::new("Source-bound caption emphasis").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Authored caption source".into(),
        objective: "Keep the same words, source and exact cue time".into(),
        duration_seconds: 3,
    })
    .unwrap();
    let asset_id = Uuid::new_v4();
    let voice_id = Uuid::new_v4();
    p.assets.push(Asset {
        id: asset_id,
        name: "Synthetic domain fixture.wav".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some("ab".repeat(32)),
        source_revision: Some("domain-fixture-only".into()),
    });
    p.audio.voice_tracks.push(VoiceTrack {
        id: voice_id,
        asset_id,
        label: "Synthetic source metadata".into(),
        sample_rate_hz: 48_000,
        channels: 1,
        measured_duration: RationalTime::new(3, 1).unwrap(),
        source_sha256: "ab".repeat(32),
        loudness_lufs: None,
        true_peak_dbfs: None,
    });
    p.audio.active_voice_track_id = Some(voice_id);
    p.audio.transcript.push(TranscriptSegment {
        id: Uuid::new_v4(),
        voice_track_id: voice_id,
        start: RationalTime::new(1, 3).unwrap(),
        end: RationalTime::new(5, 3).unwrap(),
        text: "Una decisión clara".into(),
        speaker: Some("Author".into()),
        alignment: AlignmentEvidence::Manual,
        emphasis: vec![],
    });
    p.deliverables[0].captions = true;
    p.deliverables[0].burn_in_captions = false;
    p.deliverables[0].voice_track_id = Some(voice_id);
    p.validate().unwrap();
    p
}
fn selection() -> Vec<TranscriptEmphasis> {
    vec![TranscriptEmphasis {
        start: 4,
        end: 12,
        text: "decisión".into(),
    }]
}

#[test]
fn exact_unicode_word_runs_round_trip_without_changing_identity_or_time() {
    let mut p = fixture();
    let original = p.clone();
    let legacy = serde_json::to_value(&p).unwrap();
    assert!(legacy["audio"]["transcript"][0].get("emphasis").is_none());
    let decoded: Project = serde_json::from_value(legacy).unwrap();
    assert_eq!(decoded, p);
    let mut segment = p.audio.transcript[0].clone();
    segment.emphasis = selection();
    p.apply_change(&Change::UpsertTranscriptSegment { segment })
        .unwrap();
    let saved = &p.audio.transcript[0];
    assert_eq!(saved.id, original.audio.transcript[0].id);
    assert_eq!(saved.start, original.audio.transcript[0].start);
    assert_eq!(saved.end, original.audio.transcript[0].end);
    assert_eq!(p.audio.voice_tracks, original.audio.voice_tracks);
    assert_eq!(
        saved.caption_runs().unwrap(),
        vec![
            ("Una ".into(), false),
            ("decisión".into(), true),
            (" clara".into(), false),
        ]
    );
    let reopened: Project = serde_json::from_slice(&serde_json::to_vec(&p).unwrap()).unwrap();
    reopened.validate().unwrap();
    assert_eq!(reopened, p);
    let mut unicode = saved.clone();
    unicode.text = "  Año 👩‍🔧 très clair\nfin  ".into();
    unicode.emphasis = vec![TranscriptEmphasis {
        start: 6,
        end: 14,
        text: "👩‍🔧 très".into(),
    }];
    let runs = unicode.caption_runs().unwrap();
    assert_eq!(
        runs.iter()
            .map(|(text, _)| text.as_str())
            .collect::<String>(),
        unicode.text.trim()
    );
    assert_eq!(runs.iter().filter(|(_, emphasis)| *emphasis).count(), 1);
    assert_eq!(runs[1], ("👩‍🔧 très".into(), true));
}

#[test]
fn caption_style_changes_invalidate_captions_and_only_burned_video() {
    for burn_in in [false, true] {
        let mut p = fixture();
        p.deliverables[0].burn_in_captions = burn_in;
        let before = variant_dependency_fingerprint(&p, p.deliverables[0].id).unwrap();
        let mut segment = p.audio.transcript[0].clone();
        segment.emphasis = selection();
        p.apply_change(&Change::UpsertTranscriptSegment { segment })
            .unwrap();
        let after = variant_dependency_fingerprint(&p, p.deliverables[0].id).unwrap();
        assert_ne!(before.caption_inputs_sha256, after.caption_inputs_sha256);
        assert_eq!(before.audio_inputs_sha256, after.audio_inputs_sha256);
        assert_eq!(
            before.frame_inputs_sha256 == after.frame_inputs_sha256,
            !burn_in
        );
    }
}

#[test]
fn stale_partial_overlapping_and_excessive_ranges_are_rejected_atomically() {
    let original = fixture();
    let valid = selection()[0].clone();
    let cases = [
        vec![TranscriptEmphasis {
            text: "different".into(),
            ..valid.clone()
        }],
        vec![TranscriptEmphasis {
            start: 5,
            text: "ecisión".into(),
            ..valid.clone()
        }],
        vec![TranscriptEmphasis {
            end: 60,
            ..valid.clone()
        }],
        vec![valid.clone(), valid.clone()],
        vec![valid.clone(); 9],
    ];
    for emphasis in cases {
        let mut p = original.clone();
        let mut segment = p.audio.transcript[0].clone();
        segment.emphasis = emphasis;
        assert!(
            p.apply_change(&Change::UpsertTranscriptSegment { segment })
                .is_err()
        );
        assert_eq!(p, original);
    }
    let mut segment = original.audio.transcript[0].clone();
    segment.emphasis = selection();
    segment.text = "Una decisión\rc lara".into();
    assert!(segment.caption_runs().is_err());
    let mut encoded = serde_json::to_value(&valid).unwrap();
    encoded["unapproved_style"] = serde_json::json!(true);
    assert!(serde_json::from_value::<TranscriptEmphasis>(encoded).is_err());
}

#[test]
fn webvtt_preserves_emphasis_and_literal_markup_while_subrip_refuses_style_loss() {
    let mut p = fixture();
    p.audio.transcript[0].text = "Una decisión <clara> & real".into();
    p.audio.transcript[0].emphasis = selection();
    let original = p.clone();
    let sidecar = caption_sidecar(&p, p.deliverables[0].id).unwrap();
    assert!(sidecar.body.contains("00:00:00.333 --> 00:00:01.667"));
    assert!(
        sidecar
            .body
            .contains("Una <b>decisión</b> &lt;clara&gt; &amp; real")
    );
    assert_eq!(p, original);
    p.deliverables[0].caption_format = CaptionFormat::SubRip;
    assert!(
        caption_sidecar(&p, p.deliverables[0].id)
            .unwrap_err()
            .to_string()
            .contains("SubRip styling is not admitted")
    );
    p.audio.transcript[0].emphasis.clear();
    assert!(
        caption_sidecar(&p, p.deliverables[0].id)
            .unwrap()
            .body
            .contains("Una decisión")
    );
}
