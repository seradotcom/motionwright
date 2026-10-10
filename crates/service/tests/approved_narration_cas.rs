//! Real SQLite/CAS persistence with an actual original decoded PCM file.
use domain::{Change, RationalTime, RevisionStamp};
use motionwright_domain as domain;
use motionwright_service::{StudioService, VoiceImportMetadata};
use std::{fs::File, io::Write, path::Path};
use uuid::Uuid;

fn time(n: i64) -> RationalTime {
    RationalTime { num: n, den: 1 }
}
fn wav(path: &Path) {
    const FRAMES: u32 = 96_000;
    let mut out = File::create(path).unwrap();
    let bytes = FRAMES * 2;
    out.write_all(b"RIFF").unwrap();
    out.write_all(&(36 + bytes).to_le_bytes()).unwrap();
    out.write_all(b"WAVEfmt ").unwrap();
    out.write_all(&16_u32.to_le_bytes()).unwrap();
    out.write_all(&1_u16.to_le_bytes()).unwrap();
    out.write_all(&1_u16.to_le_bytes()).unwrap();
    out.write_all(&48_000_u32.to_le_bytes()).unwrap();
    out.write_all(&96_000_u32.to_le_bytes()).unwrap();
    out.write_all(&2_u16.to_le_bytes()).unwrap();
    out.write_all(&16_u16.to_le_bytes()).unwrap();
    out.write_all(b"data").unwrap();
    out.write_all(&bytes.to_le_bytes()).unwrap();
    for _ in 0..FRAMES {
        out.write_all(&0_i16.to_le_bytes()).unwrap();
    }
    out.sync_all().unwrap();
}
fn apply(service: &StudioService, p: &domain::Project, key: &str, edit: Change) -> domain::Project {
    service
        .apply(p.id, &RevisionStamp::from(p), key, &edit)
        .unwrap()
        .project
}
#[test]
fn exact_approved_narration_is_persistent_and_cannot_be_changed_silently() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("owner-voice.wav");
    wav(&path);
    let db = temp.path().join("studio.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service.create_project("Verified retained source").unwrap();
    p = service
        .import_voice_file(
            p.id,
            &RevisionStamp::from(&p),
            "approved:measured-input",
            &path,
            VoiceImportMetadata {
                name: "voice.wav".into(),
                label: "Original approved voice".into(),
                media_type: "audio/wav".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(p.audio.voice_tracks[0].sample_rate_hz, 48_000);
    let active = p.audio.active_voice_track_id.unwrap();
    let one = Uuid::from_u128(11);
    let two = Uuid::from_u128(12);
    for (id, start, end, copy, index) in [
        (one, 0, 1, "Keep the original source", "first"),
        (two, 1, 2, "Preserve human editorial work", "second"),
    ] {
        p = apply(
            &service,
            &p,
            &format!("approved:segment:{index}"),
            Change::AddTranscriptSegment {
                segment: domain::TranscriptSegment {
                    id,
                    voice_track_id: active,
                    start: time(start),
                    end: time(end),
                    text: copy.into(),
                    speaker: None,
                    alignment: domain::AlignmentEvidence::Manual,
                },
            },
        );
    }
    p = apply(
        &service,
        &p,
        "approved:cue",
        Change::AddAudioCue {
            cue: domain::AudioCue {
                id: Uuid::from_u128(31),
                label: "Approved source cue".into(),
                at: time(1),
                source_segment_id: Some(two),
                evidence: domain::CueEvidence::Manual,
            },
        },
    );
    let before_lock = p.clone();
    let (_, _, _, source_sha) = domain::measured_narration_content(&p.audio, &p.assets).unwrap();
    p = apply(
        &service,
        &p,
        "approved:record",
        Change::RecordNarrationTake {
            expected_source_sha256: source_sha.clone(),
            expected_project_revision: p.revision,
            reviewer: "Owner in local project session".into(),
            reason: "Approved original narration words, timing and cues for editorial continuity"
                .into(),
        },
    );
    assert_eq!(p.revision, before_lock.revision + 1);
    assert_eq!(
        p.production_design
            .narration_take_lock
            .as_ref()
            .unwrap()
            .source_content_sha256,
        source_sha
    );
    let reopened = StudioService::open(&db).unwrap().project(p.id).unwrap();
    assert_eq!(
        reopened.production_design.narration_take_lock,
        p.production_design.narration_take_lock
    );
    reopened.validate().unwrap();
    let mut changed = p.audio.transcript[0].clone();
    changed.text = "Unauthorized new claim".into();
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "approved:agent-stale-source",
                &Change::UpsertTranscriptSegment {
                    segment: changed.clone()
                }
            )
            .is_err()
    );
    let unchanged = service.project(p.id).unwrap();
    assert_eq!(unchanged.revision, p.revision);
    assert_eq!(unchanged.audio.transcript, p.audio.transcript);
    let mut mix = p.audio.mix.clone();
    mix.music_gain_db = -7.0;
    p = apply(
        &service,
        &p,
        "approved:edit-original-mix",
        Change::SetMixIntent { mix: mix.clone() },
    );
    assert_eq!(p.audio.mix, mix);
    assert_eq!(
        p.production_design.narration_take_lock,
        unchanged.production_design.narration_take_lock
    );
    // An older writer cannot unprotect a newer committed project revision.
    assert!(
        service
            .apply(
                p.id,
                &before_lock.stamp(),
                "approved:old-record",
                &Change::RecordNarrationTake {
                    expected_source_sha256: source_sha.clone(),
                    expected_project_revision: before_lock.revision,
                    reviewer: "Forged previous owner".into(),
                    reason: "Replay old approval".into()
                }
            )
            .is_err()
    );
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "approved:incorrect-unlock",
                &Change::ReleaseNarrationTake {
                    expected_source_sha256: "ab".repeat(32),
                    reviewer: "Owner".into(),
                    reason: "New revision".into()
                }
            )
            .is_err()
    );
    p = apply(
        &service,
        &p,
        "approved:release-for-correction",
        Change::ReleaseNarrationTake {
            expected_source_sha256: source_sha,
            reviewer: "Recorded owner".into(),
            reason: "An explicit new script correction was reviewed and is required".into(),
        },
    );
    assert!(p.production_design.narration_take_lock.is_none());
    changed = p.audio.transcript[0].clone();
    changed.text = "A separately authored corrected narration".into();
    p = apply(
        &service,
        &p,
        "approved:replace-words",
        Change::UpsertTranscriptSegment {
            segment: changed.clone(),
        },
    );
    assert_eq!(p.audio.transcript[0], changed);
    assert_eq!(
        StudioService::open(&db)
            .unwrap()
            .project(p.id)
            .unwrap()
            .revision,
        p.revision
    );
}
