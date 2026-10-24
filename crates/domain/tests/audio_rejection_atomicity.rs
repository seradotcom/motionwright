use motionwright_domain::*;
use uuid::Uuid;

fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}

fn seconds(value: i64) -> RationalTime {
    RationalTime { num: value, den: 1 }
}

fn source_asset(asset_id: u128, digest_byte: &str) -> Asset {
    Asset {
        id: id(asset_id),
        name: format!("Measured source {asset_id}"),
        media_type: "audio/wav".into(),
        content_sha256: Some(digest_byte.repeat(32)),
        source_revision: Some("synthetic-domain-fixture".into()),
    }
}

fn measured_track(track_id: u128, asset_id: u128, digest_byte: &str) -> VoiceTrack {
    VoiceTrack {
        id: id(track_id),
        asset_id: id(asset_id),
        label: format!("Measured track {track_id}"),
        sample_rate_hz: 48_000,
        channels: 1,
        measured_duration: seconds(3),
        source_sha256: digest_byte.repeat(32),
        loudness_lufs: Some(-18.0),
        true_peak_dbfs: Some(-3.0),
    }
}

fn manual_segment(segment_id: u128, track_id: u128, start: i64, end: i64) -> TranscriptSegment {
    TranscriptSegment {
        id: id(segment_id),
        voice_track_id: id(track_id),
        start: seconds(start),
        end: seconds(end),
        text: "Keep original words".into(),
        speaker: None,
        alignment: AlignmentEvidence::Manual,
    }
}

fn manual_cue(cue_id: u128, segment_id: u128, at: i64) -> AudioCue {
    AudioCue {
        id: id(cue_id),
        label: "Original manual cue".into(),
        at: seconds(at),
        source_segment_id: Some(id(segment_id)),
        evidence: CueEvidence::Manual,
    }
}

fn project_with_manual_audio() -> Project {
    let mut project = Project::new("Audio rejection atomicity").unwrap();
    project.id = id(1);
    project.generation = id(2);
    project.assets.push(source_asset(10, "ab"));
    project
        .audio
        .voice_tracks
        .push(measured_track(20, 10, "ab"));
    project.audio.active_voice_track_id = Some(id(20));
    project.audio.transcript.push(manual_segment(30, 20, 0, 1));
    project.audio.cues.push(manual_cue(40, 30, 0));
    assert!(project.audio.sfx_cues.is_empty());
    assert!(project.production_design.delivery_recipes.is_empty());
    project.validate().unwrap();
    project
}

fn assert_valid_round_trip(project: &Project) {
    let encoded = serde_json::to_vec(project).unwrap();
    let reopened: Project = serde_json::from_slice(&encoded).unwrap();
    reopened.validate().unwrap();
    assert_eq!(&reopened, project);
}

fn assert_rejection_preserves_project(mut project: Project, change: Change) {
    project.validate().unwrap();
    let original = project.clone();

    let result = project.apply_change(&change);

    assert!(
        result.is_err(),
        "Invalid audio change unexpectedly succeeded"
    );
    assert_eq!(
        project, original,
        "Rejected audio change altered the live project"
    );
    assert_valid_round_trip(&project);
}

#[test]
fn invalid_measured_voice_import_preserves_assets_tracks_and_active_selection() {
    let project = project_with_manual_audio();
    let asset = source_asset(11, "cd");
    let mut track = measured_track(21, 11, "cd");
    track.sample_rate_hz = 0;

    assert_rejection_preserves_project(project, Change::ImportMeasuredVoice { asset, track });
}

#[test]
fn duplicate_voice_track_add_preserves_the_original_project() {
    let project = project_with_manual_audio();
    let track = project.audio.voice_tracks[0].clone();

    assert_rejection_preserves_project(project, Change::AddVoiceTrack { track });
}

#[test]
fn orphan_transcript_add_preserves_the_original_project() {
    let project = project_with_manual_audio();
    let segment = manual_segment(31, 999, 1, 2);

    assert_rejection_preserves_project(project, Change::AddTranscriptSegment { segment });
}

#[test]
fn invalid_existing_transcript_upsert_preserves_the_original_segment() {
    let project = project_with_manual_audio();
    let mut segment = project.audio.transcript[0].clone();
    segment.end = seconds(0);

    assert_rejection_preserves_project(project, Change::UpsertTranscriptSegment { segment });
}

#[test]
fn orphan_audio_cue_add_preserves_the_original_project() {
    let project = project_with_manual_audio();
    let cue = manual_cue(41, 999, 1);

    assert_rejection_preserves_project(project, Change::AddAudioCue { cue });
}

#[test]
fn invalid_existing_audio_cue_upsert_preserves_the_original_cue() {
    let project = project_with_manual_audio();
    let mut cue = project.audio.cues[0].clone();
    cue.at = seconds(-1);

    assert_rejection_preserves_project(project, Change::UpsertAudioCue { cue });
}

#[test]
fn invalid_finite_mix_gain_preserves_the_original_mix_and_project() {
    let project = project_with_manual_audio();
    let mut mix = project.audio.mix.clone();
    mix.voice_gain_db = 25.0;

    assert_rejection_preserves_project(project, Change::SetMixIntent { mix });
}

#[test]
fn valid_audio_import_selection_manual_edits_and_removals_remain_available() {
    let mut project = project_with_manual_audio();

    project
        .apply_change(&Change::ImportMeasuredVoice {
            asset: source_asset(11, "cd"),
            track: measured_track(21, 11, "cd"),
        })
        .unwrap();
    assert_eq!(project.audio.active_voice_track_id, Some(id(21)));
    assert!(project.assets.iter().any(|asset| asset.id == id(11)));

    project
        .apply_change(&Change::AddVoiceTrack {
            track: measured_track(22, 11, "cd"),
        })
        .unwrap();
    assert_eq!(project.audio.voice_tracks.len(), 3);

    project
        .apply_change(&Change::SetActiveVoiceTrack { track_id: id(20) })
        .unwrap();
    assert_eq!(project.audio.active_voice_track_id, Some(id(20)));

    let mut segment = manual_segment(31, 20, 1, 2);
    project
        .apply_change(&Change::AddTranscriptSegment {
            segment: segment.clone(),
        })
        .unwrap();

    let mut cue = manual_cue(41, 31, 1);
    project
        .apply_change(&Change::AddAudioCue { cue: cue.clone() })
        .unwrap();

    segment.text = "Updated manual words".into();
    segment.end = seconds(3);
    project
        .apply_change(&Change::UpsertTranscriptSegment {
            segment: segment.clone(),
        })
        .unwrap();
    assert_eq!(
        project
            .audio
            .transcript
            .iter()
            .find(|item| item.id == id(31)),
        Some(&segment)
    );
    assert_eq!(project.audio.transcript.len(), 2);

    cue.label = "Updated manual cue".into();
    cue.at = seconds(2);
    project
        .apply_change(&Change::UpsertAudioCue { cue: cue.clone() })
        .unwrap();
    assert_eq!(
        project.audio.cues.iter().find(|item| item.id == id(41)),
        Some(&cue)
    );
    assert_eq!(project.audio.cues.len(), 2);

    let mut mix = project.audio.mix.clone();
    mix.voice_gain_db = 6.0;
    project.apply_change(&Change::SetMixIntent { mix }).unwrap();
    assert_eq!(project.audio.mix.voice_gain_db, 6.0);

    project
        .apply_change(&Change::RemoveAudioCue { cue_id: id(41) })
        .unwrap();
    project
        .apply_change(&Change::RemoveTranscriptSegment { segment_id: id(31) })
        .unwrap();

    assert_eq!(project.audio.cues.len(), 1);
    assert_eq!(project.audio.cues[0].id, id(40));
    assert_eq!(project.audio.transcript.len(), 1);
    assert_eq!(project.audio.transcript[0].id, id(30));
    assert_eq!(project.audio.transcript[0].text, "Keep original words");
    project.validate().unwrap();
    assert_valid_round_trip(&project);
}
