use domain::{Change, NarrationTakeLock, Project, RationalTime};
use motionwright_domain as domain;
use uuid::Uuid;

fn id(number: u128) -> Uuid {
    Uuid::from_u128(number)
}
fn sec(n: i64) -> RationalTime {
    RationalTime { num: n, den: 1 }
}
fn fixture() -> Project {
    let mut project = Project::new("Owner approved source voice").unwrap();
    project.id = id(1);
    project.generation = id(2);
    let asset_sha = "ab".repeat(32);
    project.assets.push(domain::Asset {
        id: id(10),
        name: "Owner original voice".into(),
        media_type: "audio/wav".into(),
        content_sha256: Some(asset_sha.clone()),
        source_revision: Some("owned-v1".into()),
    });
    project.audio.voice_tracks.push(domain::VoiceTrack {
        id: id(20),
        asset_id: id(10),
        label: "One original measured take".into(),
        sample_rate_hz: 48000,
        channels: 1,
        measured_duration: sec(3),
        source_sha256: asset_sha,
        loudness_lufs: Some(-18.0),
        true_peak_dbfs: Some(-3.0),
    });
    project.audio.active_voice_track_id = Some(id(20));
    project.audio.transcript.extend([
        domain::TranscriptSegment {
            id: id(30),
            voice_track_id: id(20),
            start: sec(0),
            end: sec(1),
            text: "Keep the original words".into(),
            speaker: None,
            alignment: domain::AlignmentEvidence::Manual,
        },
        domain::TranscriptSegment {
            id: id(31),
            voice_track_id: id(20),
            start: sec(1),
            end: sec(2),
            text: "Here is the source".into(),
            speaker: None,
            alignment: domain::AlignmentEvidence::Manual,
        },
    ]);
    project.audio.cues.push(domain::AudioCue {
        id: id(40),
        label: "Original owner cut".into(),
        at: sec(1),
        source_segment_id: Some(id(31)),
        evidence: domain::CueEvidence::Manual,
    });
    project.validate().unwrap();
    project
}
fn approval(project: &Project) -> Change {
    let (_, _, _, fingerprint) =
        domain::measured_narration_content(&project.audio, &project.assets).unwrap();
    Change::RecordNarrationTake {
        expected_source_sha256: fingerprint,
        expected_project_revision: project.revision,
        reviewer: "Owner recorded reviewer".into(),
        reason: "Preserve the approved original words and timing while designs evolve".into(),
    }
}
#[test]
fn original_take_can_be_recorded_through_an_explicit_revisioned_domain_change() {
    let mut project = fixture();
    let change = approval(&project);
    project.apply_change(&change).unwrap();
    let locked = project
        .production_design
        .narration_take_lock
        .as_ref()
        .unwrap();
    assert_eq!(locked.voice_track_id, id(20));
    assert_eq!(locked.source_project_revision, 0);
    assert_eq!(locked.voice_asset_sha256, "ab".repeat(32));
    assert_eq!(locked.source_content_sha256.len(), 64);
    assert!(!locked.reviewer_identity_authenticated);
    assert!(!locked.media_bytes_independently_decoded);
    assert!(!locked.artistic_quality_approved);
    assert!(!locked.publication_approved);
    project.validate().unwrap();
    let encoded = serde_json::to_vec(&project).unwrap();
    let restored: Project = serde_json::from_slice(&encoded).unwrap();
    restored.validate().unwrap();
    assert_eq!(
        restored.production_design.narration_take_lock,
        project.production_design.narration_take_lock
    );
    assert!(
        project.apply_change(&approval(&project)).is_err(),
        "Duplicate approval should be denied"
    );
}
#[test]
fn a_recorded_take_refuses_mutating_voice_words_and_cues_but_allows_new_mix() {
    let mut project = fixture();
    project.apply_change(&approval(&project)).unwrap();
    let baseline = project.clone();
    let mut segment = project.audio.transcript[0].clone();
    segment.text = "Agent rewrote source".into();
    let invalid = [
        Change::UpsertTranscriptSegment { segment },
        Change::RemoveTranscriptSegment { segment_id: id(31) },
        Change::AddAudioCue {
            cue: domain::AudioCue {
                id: id(42),
                label: "Unreviewed after cue".into(),
                at: sec(2),
                source_segment_id: Some(id(31)),
                evidence: domain::CueEvidence::Manual,
            },
        },
        Change::RemoveAudioCue { cue_id: id(40) },
        Change::SetActiveVoiceTrack { track_id: id(20) },
    ];
    for change in invalid {
        let before = project.clone();
        assert!(matches!(
            project.apply_change(&change),
            Err(domain::DomainError::Locked(_))
        ));
        assert_eq!(
            before, project,
            "Denied narration edit must not mutate a recorded source"
        );
    }
    assert_eq!(baseline.audio, project.audio);
    let mut mix = project.audio.mix.clone();
    mix.music_gain_db = -10.0;
    project
        .apply_change(&Change::SetMixIntent { mix: mix.clone() })
        .unwrap();
    assert_eq!(project.audio.mix, mix);
    project.validate().unwrap();
}
#[test]
fn explicit_release_needs_recorded_reason_and_exact_original_digest() {
    let mut project = fixture();
    project.apply_change(&approval(&project)).unwrap();
    let locked = project
        .production_design
        .narration_take_lock
        .as_ref()
        .unwrap()
        .clone();
    for reason in ["", "  "] {
        assert!(
            project
                .apply_change(&Change::ReleaseNarrationTake {
                    expected_source_sha256: locked.source_content_sha256.clone(),
                    reviewer: "Owner".into(),
                    reason: reason.into()
                })
                .is_err()
        );
    }
    assert!(
        project
            .apply_change(&Change::ReleaseNarrationTake {
                expected_source_sha256: "bc".repeat(32),
                reviewer: "Owner".into(),
                reason: "I want a new authorized take".into()
            })
            .is_err()
    );
    project
        .apply_change(&Change::ReleaseNarrationTake {
            expected_source_sha256: locked.source_content_sha256,
            reviewer: "Recorded owner".into(),
            reason: "I explicitly release the old source to revise the script".into(),
        })
        .unwrap();
    assert!(project.production_design.narration_take_lock.is_none());
    let mut segment = project.audio.transcript[0].clone();
    segment.text = "New original revised words".into();
    project
        .apply_change(&Change::UpsertTranscriptSegment {
            segment: segment.clone(),
        })
        .unwrap();
    assert_eq!(project.audio.transcript[0], segment);
}
#[test]
fn direct_project_snapshot_cannot_silently_mutate_or_forge_recorded_take() {
    let mut project = fixture();
    project.apply_change(&approval(&project)).unwrap();
    let mut altered = project.clone();
    altered.audio.transcript[0].text = "Silent unauthorized edit".into();
    assert!(matches!(
        altered.validate(),
        Err(domain::DomainError::Locked(_))
    ));
    let mut altered = project.clone();
    altered.audio.voice_tracks[0].source_sha256 = "cd".repeat(32);
    assert!(altered.validate().is_err());
    let mut altered = project.clone();
    altered
        .production_design
        .narration_take_lock
        .as_mut()
        .unwrap()
        .artistic_quality_approved = true;
    assert!(altered.validate().is_err());
    let mut altered = project.clone();
    altered.schema_version = 2;
    assert!(
        altered.validate().is_err(),
        "Old schema cannot drop protected source"
    );
}
#[test]
fn missing_original_source_or_uncertain_asr_timing_cannot_be_approved() {
    let mut project = fixture();
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Unknown;
    assert!(project.apply_change(&approval(&project)).is_err());
    let mut project = fixture();
    project.assets[0].content_sha256 = Some("bc".repeat(32));
    assert!(domain::measured_narration_content(&project.audio, &project.assets).is_err());
    let mut project = fixture();
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Measured {
        engine: "Unreviewed ASR".into(),
        source_sha256: "ab".repeat(32),
        confidence_millis: Some(499),
    };
    assert!(domain::measured_narration_content(&project.audio, &project.assets).is_err());
}
#[test]
fn fingerprint_is_independent_of_transcript_and_cue_array_order() {
    let project = fixture();
    let before = domain::measured_narration_content(&project.audio, &project.assets).unwrap();
    let mut reversed = project.clone();
    reversed.audio.transcript.reverse();
    let next = domain::measured_narration_content(&reversed.audio, &reversed.assets).unwrap();
    assert_eq!(
        before, next,
        "Reordering JSON collections must not release or forge a source decision"
    );
}
#[test]
fn stale_fingerprint_or_version_never_installs_a_lock() {
    let mut project = fixture();
    let mut stale = approval(&project);
    if let Change::RecordNarrationTake {
        expected_source_sha256,
        ..
    } = &mut stale
    {
        *expected_source_sha256 = "cd".repeat(32);
    }
    assert!(project.apply_change(&stale).is_err());
    let mut stale = approval(&project);
    if let Change::RecordNarrationTake {
        expected_project_revision,
        ..
    } = &mut stale
    {
        *expected_project_revision = 5;
    }
    assert!(project.apply_change(&stale).is_err());
    assert!(project.production_design.narration_take_lock.is_none());
}
#[test]
fn unknown_fields_cannot_forge_authenticated_content_acceptance() {
    let mut project = fixture();
    project.apply_change(&approval(&project)).unwrap();
    let lock = project
        .production_design
        .narration_take_lock
        .as_ref()
        .unwrap();
    let mut value = serde_json::to_value(lock).unwrap();
    value["owner_e_signature_authenticated"] = serde_json::json!(true);
    assert!(serde_json::from_value::<NarrationTakeLock>(value).is_err());
}
