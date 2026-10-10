use motionwright_creative_library::{narration_source_snapshot, propose_narration_replacement};
use motionwright_domain as d;
use uuid::Uuid;
fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}
fn time(num: i64, den: i64) -> d::RationalTime {
    d::RationalTime { num, den }
}
fn project() -> d::Project {
    let mut p = d::Project::new("Original narration source").unwrap();
    p.id = id(1);
    p.generation = id(2);
    p.revision = 11;
    let voice_sha = "ab".repeat(32);
    p.assets.push(d::Asset {
        id: id(10),
        name: "Original measured voice take".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some(voice_sha.clone()),
        source_revision: Some("owner-import-1".into()),
    });
    p.audio.voice_tracks.push(d::VoiceTrack {
        id: id(20),
        asset_id: id(10),
        label: "Original source voice".into(),
        sample_rate_hz: 48000,
        channels: 1,
        measured_duration: time(3, 1),
        source_sha256: voice_sha,
        loudness_lufs: Some(-18.0),
        true_peak_dbfs: Some(-3.0),
    });
    p.audio.active_voice_track_id = Some(id(20));
    p.audio.transcript = [
        d::TranscriptSegment {
            id: id(30),
            voice_track_id: id(20),
            start: time(0, 1),
            end: time(1, 1),
            text: "This is the original.".into(),
            speaker: Some("Owner voice".into()),
            alignment: d::AlignmentEvidence::Manual,
        },
        d::TranscriptSegment {
            id: id(31),
            voice_track_id: id(20),
            start: time(1, 1),
            end: time(2, 1),
            text: "Edit the original source.".into(),
            speaker: Some("Owner voice".into()),
            alignment: d::AlignmentEvidence::Manual,
        },
    ]
    .to_vec();
    p.audio.cues.push(d::AudioCue {
        id: id(40),
        label: "Scene cut cue".into(),
        at: time(1, 1),
        source_segment_id: Some(id(31)),
        evidence: d::CueEvidence::Manual,
    });
    p
}
#[test]
fn original_source_is_digest_bound_without_forged_approval_or_asr_truth() {
    let p = project();
    let a = narration_source_snapshot(&p).unwrap();
    let b = narration_source_snapshot(&p).unwrap();
    assert_eq!(a, b);
    assert_eq!(a.transcript.len(), 2);
    assert_eq!(a.cues.len(), 1);
    assert_eq!(a.voice_track.source_sha256, "ab".repeat(32));
    assert_eq!(
        a.timing_review,
        "SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY"
    );
    assert!(!a.human_owner_lock_authenticated);
    assert!(!a.audio_media_bytes_decoded);
    assert!(!a.publication_approved);
    assert_eq!(a.exact_content_sha256.len(), 64);
}
#[test]
fn one_changed_word_invalidates_the_correct_caption_cue_cuts_and_broll() {
    let before = project();
    let locked = narration_source_snapshot(&before).unwrap();
    let mut next = before.clone();
    next.revision += 1;
    next.audio.transcript[1].text = "Keep the owner's original source.".into();
    let proposed =
        propose_narration_replacement(&locked, &locked.exact_content_sha256, &next).unwrap();
    assert_eq!(proposed.changed_segment_ids, vec![id(31)]);
    assert_eq!(proposed.changed_cue_ids, vec![id(40)]);
    assert_eq!(proposed.affected_spans.len(), 1);
    assert!(
        proposed
            .affected_spans
            .iter()
            .all(|span| span.source_segment_id == id(31)
                && span.start == time(1, 1)
                && span.end == time(2, 1))
    );
    assert_eq!(
        proposed.caption_timing_invalidation,
        "CAPTIONS_MUST_REGENERATE_AND_HUMAN_REVIEW"
    );
    assert_eq!(
        proposed.cuts_broll_invalidation,
        "CUTS_AND_BROLL_REQUIRE_REEVALUATION"
    );
    assert_eq!(proposed.scene_dependency_validation, "NOT_RUN");
    assert!(!proposed.human_owner_approved && !proposed.candidate_committed);
    assert_eq!(before.audio.transcript[1].text, "Edit the original source.");
}
#[test]
fn replacing_the_measured_audio_invalidates_the_full_voice_clock_and_mix() {
    let original = project();
    let before = narration_source_snapshot(&original).unwrap();
    let mut after = original.clone();
    after.revision = 12;
    after.assets[0].content_sha256 = Some("cd".repeat(32));
    after.audio.voice_tracks[0].source_sha256 = "cd".repeat(32);
    let result =
        propose_narration_replacement(&before, &before.exact_content_sha256, &after).unwrap();
    assert!(result.voice_media_replaced);
    assert_eq!(result.changed_segment_ids.len(), 2);
    assert_eq!(
        result.sound_mix_invalidation,
        "MIX_VOICE_BUS_MUST_REMEASURE"
    );
}
#[test]
fn changed_cue_is_not_silently_considered_the_same_take() {
    let original = project();
    let before = narration_source_snapshot(&original).unwrap();
    let mut after = original.clone();
    after.revision = 12;
    after.audio.cues[0].at = time(3, 2);
    let result =
        propose_narration_replacement(&before, &before.exact_content_sha256, &after).unwrap();
    assert_eq!(result.changed_cue_ids, vec![id(40)]);
    assert!(result.affected_spans.is_empty());
    assert!(!result.voice_media_replaced);
    assert_eq!(result.scene_dependency_validation, "NOT_RUN");
}
#[test]
fn an_unknown_or_low_confidence_transcript_is_never_promoted_as_approved_truth() {
    let mut project = project();
    project.audio.transcript[0].alignment = d::AlignmentEvidence::Unknown;
    assert_eq!(
        narration_source_snapshot(&project).unwrap().timing_review,
        "REQUIRES_MANUAL_TIMING_REVIEW"
    );
    project.audio.transcript[0].alignment = d::AlignmentEvidence::Measured {
        engine: "Original research ASR".into(),
        source_sha256: "ab".repeat(32),
        confidence_millis: Some(500),
    };
    assert_eq!(
        narration_source_snapshot(&project).unwrap().timing_review,
        "REQUIRES_MANUAL_TIMING_REVIEW"
    );
}
#[test]
fn stale_project_cross_generation_and_unrelated_edits_cannot_replace_a_take() {
    let original = project();
    let before = narration_source_snapshot(&original).unwrap();
    let mut after = original.clone();
    after.revision = original.revision;
    after.audio.transcript[0].text = "New text".into();
    assert!(propose_narration_replacement(&before, &before.exact_content_sha256, &after).is_err());
    after.revision += 1;
    after.generation = id(9);
    assert!(propose_narration_replacement(&before, &before.exact_content_sha256, &after).is_err());
    after = original.clone();
    after.revision += 1;
    after.title = "Unrelated change".into();
    assert!(propose_narration_replacement(&before, &before.exact_content_sha256, &after).is_err());
}
#[test]
fn user_source_digest_must_match_original_snapshot_even_after_semantic_metadata_change() {
    let original = project();
    let mut snapshot = narration_source_snapshot(&original).unwrap();
    let mut after = original.clone();
    after.revision = 12;
    after.audio.transcript[0].text = "Owner changed word".into();
    assert!(propose_narration_replacement(&snapshot, &"aa".repeat(32), &after).is_err());
    snapshot.transcript[0].text = "Forged original".into();
    let original_digest = snapshot.exact_content_sha256.clone();
    assert!(propose_narration_replacement(&snapshot, &original_digest, &after).is_err());
}
#[test]
fn missing_measured_voice_or_modified_asset_and_source_timing_are_rejected() {
    let mut p = project();
    p.audio.active_voice_track_id = None;
    assert!(narration_source_snapshot(&p).is_err());
    p = project();
    p.assets[0].content_sha256 = Some("aa".repeat(32));
    assert!(narration_source_snapshot(&p).is_err());
    p = project();
    p.audio.transcript[1].end = time(4, 1);
    assert!(narration_source_snapshot(&p).is_err());
}
#[test]
fn no_unknown_json_fields_may_smuggle_owner_approval() {
    let mut value = serde_json::to_value(narration_source_snapshot(&project()).unwrap()).unwrap();
    value["owner_authenticated"] = serde_json::json!(true);
    assert!(
        serde_json::from_value::<motionwright_creative_library::NarrationSourceSnapshot>(value)
            .is_err()
    );
}
