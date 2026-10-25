//! Structural controls only: these fixture references do not authenticate a
//! native invocation, measure speech or provide calibrated confidence.
use motionwright_domain::*;
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}
fn seconds(num: i64) -> RationalTime {
    RationalTime { num, den: 1 }
}
fn sha(text: &str) -> String {
    hex::encode(Sha256::digest(text.as_bytes()))
}
fn segment(value: u128, text: &str, start: i64, end: i64) -> TranscriptSegment {
    TranscriptSegment {
        id: id(value),
        voice_track_id: id(20),
        start: seconds(start),
        end: seconds(end),
        text: text.into(),
        speaker: None,
        alignment: AlignmentEvidence::Measured {
            engine: "controlled-fixture-not-native".into(),
            source_sha256: "ab".repeat(32),
            confidence_millis: None,
        },
    }
}
fn cue(value: u128, source: u128, at: i64) -> AudioCue {
    AudioCue {
        id: id(value),
        label: format!("Fixture cue {value}"),
        at: seconds(at),
        source_segment_id: Some(id(source)),
        evidence: CueEvidence::TranscriptAligned,
    }
}
fn fixture() -> (Project, VoiceAlignmentApplication) {
    let mut project = Project::new("Voice alignment structural fixture").unwrap();
    for (asset_id, track_id, label, hash) in [
        (10, 20, "Selected voice", "ab".repeat(32)),
        (11, 21, "Other voice", "cd".repeat(32)),
    ] {
        project.assets.push(Asset {
            id: id(asset_id),
            name: label.into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(hash.clone()),
            source_revision: None,
        });
        project.audio.voice_tracks.push(VoiceTrack {
            id: id(track_id),
            asset_id: id(asset_id),
            label: label.into(),
            sample_rate_hz: 16_000,
            channels: 1,
            measured_duration: seconds(3),
            source_sha256: hash,
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
    }
    project.audio.active_voice_track_id = Some(id(20));
    project.audio.mix.music_gain_db = -9.0;
    let mut prior = segment(40, "Preserve the other voice", 0, 1);
    prior.voice_track_id = id(21);
    prior.alignment = AlignmentEvidence::Manual;
    project.audio.transcript.push(prior);
    let mut prior_cue = cue(50, 40, 0);
    prior_cue.evidence = CueEvidence::Manual;
    project.audio.cues.push(prior_cue);
    project.audio.cues.push(AudioCue {
        id: id(51),
        label: "Independent manual marker at zero".into(),
        at: seconds(0),
        source_segment_id: None,
        evidence: CueEvidence::Manual,
    });
    project.validate().unwrap();
    let application = VoiceAlignmentApplication {
        profile: VOICE_ALIGNMENT_PROFILE_V1.into(),
        voice_track_id: id(20),
        asset_id: id(10),
        source_sha256: "ab".repeat(32),
        transcript_version: 1,
        transcript_text: "Exact words work".into(),
        transcript_sha256: sha("Exact words work"),
        receipt: VoiceAlignmentReceiptRef {
            id: id(900),
            request_id: id(901).to_string(),
            request_sha256: "12".repeat(32),
            command: "controlled.alignment.fixture".into(),
            receipt_sha256: "34".repeat(32),
        },
        segments: vec![
            segment(100, "Exact", 0, 1),
            segment(101, "words", 1, 2),
            segment(102, "work", 2, 3),
        ],
        cues: vec![cue(200, 100, 0), cue(201, 102, 2)],
    };
    (project, application)
}
fn reopens(project: &Project) {
    let bytes = serde_json::to_vec(project).unwrap();
    let reopened: Project = serde_json::from_slice(&bytes).unwrap();
    reopened.validate().unwrap();
    assert_eq!(&reopened, project);
}
fn rejected(mut project: Project, application: VoiceAlignmentApplication, case: &str) {
    project.validate().unwrap();
    let before = project.clone();
    assert!(
        project
            .apply_change(&Change::ApplyVoiceAlignment { application })
            .is_err(),
        "{case}"
    );
    assert_eq!(
        project, before,
        "{case}: rejected operation changed the project"
    );
    reopens(&project);
}

#[test]
fn whole_alignment_preserves_other_content_and_same_time_manual_marker() {
    let (mut project, application) = fixture();
    let before = project.clone();
    let expected_segments = application.segments.clone();
    let expected_cues = application.cues.clone();
    project
        .apply_change(&Change::ApplyVoiceAlignment { application })
        .unwrap();
    assert_eq!(project.audio.transcript.len(), 4);
    assert_eq!(project.audio.cues.len(), 4);
    assert_eq!(&project.audio.transcript[1..], expected_segments.as_slice());
    assert_eq!(&project.audio.cues[2..], expected_cues.as_slice());
    assert_eq!(
        project.audio.transcript[1..]
            .iter()
            .map(|s| s.text.as_str())
            .collect::<Vec<_>>(),
        ["Exact", "words", "work"]
    );
    assert_eq!(project.audio.cues[1].id, id(51));
    assert_eq!(project.audio.cues[1].at, seconds(0));
    assert_eq!(project.audio.cues[1].source_segment_id, None);
    assert_eq!(project.audio.cues[2].at, seconds(0));
    let mut preserved = project.clone();
    preserved
        .audio
        .transcript
        .retain(|item| ![id(100), id(101), id(102)].contains(&item.id));
    preserved
        .audio
        .cues
        .retain(|item| ![id(200), id(201)].contains(&item.id));
    // A successful domain edit may update the document modification time.
    preserved.updated_at = before.updated_at;
    assert_eq!(preserved, before);
    reopens(&project);
}

#[test]
fn empty_cue_subset_fractional_times_and_exact_maximum_version_are_supported() {
    for version in [1, MAX_VOICE_ALIGNMENT_TRANSCRIPT_VERSION] {
        let (mut project, mut application) = fixture();
        let prior_cues = project.audio.cues.clone();
        application.transcript_version = version;
        application.cues.clear();
        application.segments[0].end = RationalTime { num: 1, den: 2 };
        application.segments[1].start = RationalTime { num: 1, den: 2 };
        application.segments[1].end = RationalTime { num: 3, den: 2 };
        application.segments[2].start = RationalTime { num: 3, den: 2 };
        project
            .apply_change(&Change::ApplyVoiceAlignment { application })
            .unwrap();
        assert_eq!(project.audio.transcript.len(), 4);
        assert_eq!(project.audio.cues, prior_cues);
        reopens(&project);
    }
}

#[test]
fn selected_voice_asset_and_exact_source_cannot_be_crossed() {
    for case in [
        "missing_voice",
        "other_asset",
        "other_sha",
        "other_segment_voice",
        "not_active",
    ] {
        let (mut project, mut application) = fixture();
        match case {
            "missing_voice" => application.voice_track_id = id(999),
            "other_asset" => application.asset_id = id(11),
            "other_segment_voice" => application.segments[2].voice_track_id = id(21),
            "other_sha" => application.source_sha256 = "cd".repeat(32),
            "not_active" => project.audio.active_voice_track_id = Some(id(21)),
            _ => unreachable!(),
        }
        rejected(project, application, case);
    }
}

#[test]
fn existing_target_transcript_of_any_evidence_kind_cannot_be_replaced() {
    for alignment in [
        AlignmentEvidence::Manual,
        AlignmentEvidence::Unknown,
        AlignmentEvidence::Measured {
            engine: "previous-controlled-result".into(),
            source_sha256: "ab".repeat(32),
            confidence_millis: None,
        },
    ] {
        let (mut project, application) = fixture();
        let mut existing = segment(300, "Owner supplied wording", 0, 1);
        existing.alignment = alignment;
        project.audio.transcript.push(existing);
        rejected(project, application, "existing target transcript");
    }
}

#[test]
fn exact_ascii_text_digest_and_complete_ordered_word_roster_are_required() {
    for case in [
        "hash",
        "double_space",
        "missing_word",
        "joined_word",
        "unicode",
        "empty",
        "apostrophe",
    ] {
        let (project, mut application) = fixture();
        application.cues.clear();
        match case {
            "hash" => application.transcript_sha256 = sha("Different text"),
            "double_space" => {
                application.transcript_text = "Exact  words work".into();
                application.transcript_sha256 = sha(&application.transcript_text);
            }
            "missing_word" => {
                application.segments.pop();
            }
            "joined_word" => application.segments[0].text = "Exact words".into(),
            "unicode" => {
                application.transcript_text = "Exact words caf\u{00e9}".into();
                application.transcript_sha256 = sha(&application.transcript_text);
                application.segments[2].text = "caf\u{00e9}".into();
            }
            "empty" => {
                application.transcript_text.clear();
                application.transcript_sha256 = sha("");
                application.segments.clear();
            }
            "apostrophe" => {
                application.transcript_text = "Exact words' work".into();
                application.transcript_sha256 = sha(&application.transcript_text);
                application.segments[1].text = "words'".into();
            }
            _ => unreachable!(),
        }
        rejected(project, application, case);
    }
}

#[test]
fn bounded_text_and_word_roster_accept_the_limit_and_reject_the_next_value() {
    for length in [1024, 1025] {
        let (mut project, mut application) = fixture();
        application.transcript_text = "a".repeat(length);
        application.transcript_sha256 = sha(&application.transcript_text);
        application.segments.truncate(1);
        application.segments[0].text = application.transcript_text.clone();
        application.cues.clear();
        if length == 1024 {
            project
                .apply_change(&Change::ApplyVoiceAlignment { application })
                .unwrap();
            reopens(&project);
        } else {
            rejected(project, application, "text above 1024 bytes");
        }
    }
    for count in [128, 129] {
        let (mut project, mut application) = fixture();
        application.transcript_text = vec!["a"; count].join(" ");
        application.transcript_sha256 = sha(&application.transcript_text);
        application.segments = (0..count)
            .map(|index| {
                let mut word = segment(1000 + index as u128, "a", 0, 1);
                word.start = RationalTime::new(index as i64, 128).unwrap();
                word.end = RationalTime::new(index as i64 + 1, 128).unwrap();
                word
            })
            .collect();
        application.cues.clear();
        if count == 128 {
            project
                .apply_change(&Change::ApplyVoiceAlignment { application })
                .unwrap();
            reopens(&project);
        } else {
            rejected(project, application, "word count above 128");
        }
    }
}

#[test]
fn malformed_profile_version_or_receipt_reference_is_rejected() {
    for case in [
        "profile",
        "version_zero",
        "version_over",
        "nil_receipt",
        "request_id",
        "request_sha",
        "receipt_sha",
    ] {
        let (project, mut application) = fixture();
        match case {
            "profile" => application.profile = "unsupported.profile".into(),
            "version_zero" => application.transcript_version = 0,
            "version_over" => {
                application.transcript_version = MAX_VOICE_ALIGNMENT_TRANSCRIPT_VERSION + 1
            }
            "nil_receipt" => application.receipt.id = Uuid::nil(),
            "request_id" => application.receipt.request_id.clear(),
            "request_sha" => application.receipt.request_sha256 = "not-a-digest".into(),
            "receipt_sha" => application.receipt.receipt_sha256 = "34".repeat(31),
            _ => unreachable!(),
        }
        rejected(project, application, case);
    }
}

#[test]
fn invalid_resolved_intervals_cannot_install_any_word() {
    for case in [
        "zero_denominator",
        "empty_interval",
        "past_duration",
        "negative",
        "overlap",
    ] {
        let (project, mut application) = fixture();
        application.cues.clear();
        match case {
            "zero_denominator" => application.segments[2].end.den = 0,
            "empty_interval" => application.segments[2].end = seconds(2),
            "past_duration" => application.segments[2].end = RationalTime { num: 7, den: 2 },
            "negative" => application.segments[0].start = seconds(-1),
            "overlap" => application.segments[1].start = seconds(0),
            _ => unreachable!(),
        }
        rejected(project, application, case);
    }
}

#[test]
fn ids_cannot_alias_existing_or_other_new_words_and_cues() {
    for case in [
        "duplicate_segment",
        "existing_segment",
        "duplicate_cue",
        "existing_cue",
    ] {
        let (project, mut application) = fixture();
        match case {
            "duplicate_segment" | "existing_segment" => {
                application.cues.clear();
                application.segments[2].id = if case == "duplicate_segment" {
                    id(100)
                } else {
                    id(40)
                };
            }
            "duplicate_cue" => application.cues[1].id = id(200),
            "existing_cue" => application.cues[1].id = id(51),
            _ => unreachable!(),
        }
        rejected(project, application, case);
    }
}

#[test]
fn invalid_last_cue_including_post_append_validation_leaves_the_project_identical() {
    for case in [
        "missing_source",
        "foreign_source",
        "wrong_start",
        "duplicate_source",
        "manual",
        "denominator",
        "empty_label",
    ] {
        let (project, mut application) = fixture();
        match case {
            "missing_source" => application.cues[1].source_segment_id = Some(id(999)),
            "foreign_source" => application.cues[1].source_segment_id = Some(id(40)),
            "wrong_start" => application.cues[1].at = seconds(1),
            "duplicate_source" => {
                application.cues[1].source_segment_id = Some(id(100));
                application.cues[1].at = seconds(0);
            }
            "manual" => application.cues[1].evidence = CueEvidence::Manual,
            "denominator" => application.cues[1].at.den = 0,
            "empty_label" => application.cues[1].label.clear(),
            _ => unreachable!(),
        }
        rejected(project, application, case);
    }
}

#[test]
fn unknown_manual_or_invented_confidence_cannot_be_installed_as_qualified_words() {
    for case in [
        "manual",
        "unknown",
        "confidence",
        "wrong_source",
        "mixed_engine",
    ] {
        let (project, mut application) = fixture();
        application.segments[2].alignment = match case {
            "manual" => AlignmentEvidence::Manual,
            "unknown" => AlignmentEvidence::Unknown,
            _ => AlignmentEvidence::Measured {
                engine: if case == "mixed_engine" {
                    "another-engine"
                } else {
                    "controlled-fixture-not-native"
                }
                .into(),
                source_sha256: if case == "wrong_source" {
                    "cd".repeat(32)
                } else {
                    "ab".repeat(32)
                },
                confidence_millis: if case == "confidence" {
                    Some(1000)
                } else {
                    None
                },
            },
        };
        rejected(project, application, case);
    }
}

#[test]
fn content_timing_and_narration_locks_take_precedence_over_admission() {
    for kind in [LockKind::Content, LockKind::Timing] {
        let (mut project, application) = fixture();
        project
            .apply_change(&Change::SetLock {
                resource: project.resource_key(),
                kind,
                note: "Preserve authored state".into(),
            })
            .unwrap();
        let before = project.clone();
        assert!(matches!(
            project.apply_change(&Change::ApplyVoiceAlignment { application }),
            Err(DomainError::Locked(_))
        ));
        assert_eq!(project, before);
        reopens(&project);
    }
    let (mut project, application) = fixture();
    let mut original_take_word = segment(300, "Original", 0, 1);
    original_take_word.alignment = AlignmentEvidence::Manual;
    project.audio.transcript.push(original_take_word);
    let (_, _, _, fingerprint) =
        measured_narration_content(&project.audio, &project.assets).unwrap();
    project
        .apply_change(&Change::RecordNarrationTake {
            expected_source_sha256: fingerprint,
            expected_project_revision: project.revision,
            reviewer: "Controlled lock fixture".into(),
            reason: "Preserve this exact original take".into(),
        })
        .unwrap();
    let before = project.clone();
    // Without the narration guard this would be Invalid (the target is not
    // empty), so the error class specifically verifies the lock guard.
    assert!(matches!(
        project.apply_change(&Change::ApplyVoiceAlignment { application }),
        Err(DomainError::Locked(_))
    ));
    assert_eq!(project, before);
    reopens(&project);
}
