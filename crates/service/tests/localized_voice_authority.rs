//! Real SQLite, measured synthetic PCM import and CAS preservation for locale authority.
//! Synthetic samples prove source/timing isolation; they do not prove spoken language.
use motionwright_domain::*;
use motionwright_service::{StudioService, VoiceImportMetadata};
use motionwright_storage::ProjectBackup;
use std::{fs, path::Path};
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}

fn wav(seed: i16, seconds: u32) -> Vec<u8> {
    let frames = 48_000 * seconds;
    let data_bytes = frames * 4;
    let mut bytes = Vec::with_capacity(data_bytes as usize + 44);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&192_000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for index in 0..frames {
        let sample = ((index % 97) as i16 - 48) * seed;
        bytes.extend_from_slice(&sample.to_le_bytes());
        bytes.extend_from_slice(&(-sample).to_le_bytes());
    }
    bytes
}

fn import_voice(
    service: &StudioService,
    project: &Project,
    folder: &Path,
    label: &str,
    bytes: &[u8],
) -> (Project, Uuid) {
    let path = folder.join(format!("{label}.wav"));
    fs::write(&path, bytes).unwrap();
    let project = service
        .import_voice_file(
            project.id,
            &project.stamp(),
            &format!("import-{label}"),
            &path,
            VoiceImportMetadata {
                name: format!("Synthetic {label}"),
                label: format!("Measured synthetic {label}"),
                media_type: "audio/wav".into(),
            },
        )
        .unwrap()
        .project;
    fs::remove_file(path).unwrap();
    let track = project.audio.active_voice_track_id.unwrap();
    assert_source(service, &project, track, bytes);
    (project, track)
}

fn assert_source(service: &StudioService, project: &Project, track_id: Uuid, bytes: &[u8]) {
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == track_id)
        .unwrap();
    assert_eq!(
        service
            .store()
            .lock()
            .read_blob(&track.source_sha256, 1024 * 1024)
            .unwrap(),
        bytes
    );
}

fn transcript(track_id: Uuid, text: &str) -> TranscriptSegment {
    TranscriptSegment {
        id: Uuid::new_v4(),
        voice_track_id: track_id,
        start: RationalTime { num: 1, den: 10 },
        end: RationalTime { num: 4, den: 5 },
        text: text.into(),
        speaker: None,
        alignment: AlignmentEvidence::Manual,
    }
}

struct Fixture {
    folder: tempfile::TempDir,
    service: StudioService,
    project: Project,
    source_voice: Uuid,
    source_bytes: Vec<u8>,
}

fn fixture() -> Fixture {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("studio.sqlite3")).unwrap();
    let project = service.create_project("Locale source authority").unwrap();
    let source_bytes = wav(11, 1);
    let (project, source_voice) =
        import_voice(&service, &project, folder.path(), "original", &source_bytes);
    let project = apply(
        &service,
        &project,
        "manual-original-transcript",
        Change::AddTranscriptSegment {
            segment: transcript(source_voice, "Original English caption."),
        },
    );
    assert_eq!(project.deliverables[0].voice_track_id, None);
    assert_eq!(project.audio.active_voice_track_id, Some(source_voice));
    assert_eq!(
        project.audio.voice_tracks[0].measured_duration,
        RationalTime { num: 1, den: 1 }
    );
    Fixture {
        folder,
        service,
        project,
        source_voice,
        source_bytes,
    }
}

fn localized_profile(project: &Project, voice_track_id: Option<Uuid>) -> DeliverableProfile {
    let parent = &project.deliverables[0];
    let mut profile = parent.clone();
    profile.id = Uuid::new_v4();
    profile.name = "Spanish delivery".into();
    profile.language = "es-MX".into();
    profile.parent_profile_id = Some(parent.id);
    profile.source_revision = Some(project.revision);
    profile.adaptation_notes = vec!["Explicit locale text and source selection".into()];
    profile.voice_track_id = voice_track_id;
    profile.timing_locked = true;
    profile.captions = true;
    profile.caption_format = CaptionFormat::WebVtt;
    profile.text_overrides = project
        .scenes
        .iter()
        .flat_map(|scene| &scene.nodes)
        .filter(|node| node.kind == "text")
        .map(|node| (node.id, "Texto localizado".into()))
        .collect();
    profile
}

fn assert_unchanged(service: &StudioService, before: &ProjectBackup) {
    assert_eq!(service.project(before.project.id).unwrap(), before.project);
    let after = service.export_project(before.project.id).unwrap();
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
}

#[test]
fn missing_locale_voice_rejects_without_revision_journal_or_cas_changes_after_reopen() {
    let f = fixture();
    let before = f.service.export_project(f.project.id).unwrap();
    let original = caption_sidecar(&f.project, f.project.deliverables[0].id).unwrap();
    for timing_locked in [true, false] {
        let mut profile = localized_profile(&f.project, None);
        profile.timing_locked = timing_locked;
        let error = f
            .service
            .apply(
                f.project.id,
                &f.project.stamp(),
                &format!("missing-locale-voice-{timing_locked}"),
                &Change::UpsertDeliverable { profile },
            )
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("requires an explicit locale voice track")
        );
        assert_unchanged(&f.service, &before);
        assert_source(&f.service, &f.project, f.source_voice, &f.source_bytes);
    }
    let path = f.folder.path().join("studio.sqlite3");
    drop(f.service);
    let reopened = StudioService::open(path).unwrap();
    assert_unchanged(&reopened, &before);
    let project = reopened.project(f.project.id).unwrap();
    assert_eq!(
        caption_sidecar(&project, project.deliverables[0].id).unwrap(),
        original
    );
    assert_source(&reopened, &project, f.source_voice, &f.source_bytes);
}

#[test]
fn explicit_locale_selection_cannot_reuse_the_implicit_parent_voice() {
    let f = fixture();
    let before = f.service.export_project(f.project.id).unwrap();
    let profile = localized_profile(&f.project, Some(f.source_voice));
    let error = f
        .service
        .apply(
            f.project.id,
            &f.project.stamp(),
            "same-parent-voice",
            &Change::UpsertDeliverable { profile },
        )
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("cannot reuse the parent language voice track")
    );
    assert_unchanged(&f.service, &before);
    let path = f.folder.path().join("studio.sqlite3");
    drop(f.service);
    let reopened = StudioService::open(path).unwrap();
    assert_unchanged(&reopened, &before);
    assert_source(&reopened, &before.project, f.source_voice, &f.source_bytes);
}

#[test]
fn measured_duration_conflict_uses_the_active_parent_until_timing_is_explicitly_unlocked() {
    let f = fixture();
    let localized_bytes = wav(17, 2);
    let (project, localized_voice) = import_voice(
        &f.service,
        &f.project,
        f.folder.path(),
        "longer-locale",
        &localized_bytes,
    );
    let project = apply(
        &f.service,
        &project,
        "retain-original-active-source",
        Change::SetActiveVoiceTrack {
            track_id: f.source_voice,
        },
    );
    assert_eq!(project.deliverables[0].voice_track_id, None);
    assert_eq!(
        project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == localized_voice)
            .unwrap()
            .measured_duration,
        RationalTime { num: 2, den: 1 }
    );
    let before = f.service.export_project(project.id).unwrap();
    let mut profile = localized_profile(&project, Some(localized_voice));
    let error = f
        .service
        .apply(
            project.id,
            &project.stamp(),
            "locked-duration-conflict",
            &Change::UpsertDeliverable {
                profile: profile.clone(),
            },
        )
        .unwrap_err();
    assert!(error.to_string().contains("measured voice duration"));
    assert_unchanged(&f.service, &before);
    profile.timing_locked = false;
    let project = apply(
        &f.service,
        &project,
        "explicitly-unlock-localized-timing",
        Change::UpsertDeliverable {
            profile: profile.clone(),
        },
    );
    assert_eq!(project.deliverables[0], before.project.deliverables[0]);
    assert_eq!(project.deliverables.last().unwrap(), &profile);
    assert_source(&f.service, &project, f.source_voice, &f.source_bytes);
    assert_source(&f.service, &project, localized_voice, &localized_bytes);
    let path = f.folder.path().join("studio.sqlite3");
    drop(f.service);
    assert_eq!(
        StudioService::open(path)
            .unwrap()
            .project(project.id)
            .unwrap(),
        project
    );
}

#[test]
fn independent_measured_locale_and_manual_captions_preserve_parent_sources_through_reopen() {
    let f = fixture();
    let original = caption_sidecar(&f.project, f.project.deliverables[0].id).unwrap();
    let mut parent = f.project.deliverables[0].clone();
    parent.voice_track_id = Some(f.source_voice);
    let project = apply(
        &f.service,
        &f.project,
        "select-original-parent-source",
        Change::UpsertDeliverable {
            profile: parent.clone(),
        },
    );
    let localized_bytes = wav(23, 1);
    let (project, localized_voice) = import_voice(
        &f.service,
        &project,
        f.folder.path(),
        "localized",
        &localized_bytes,
    );
    let project = apply(
        &f.service,
        &project,
        "manual-localized-transcript",
        Change::AddTranscriptSegment {
            segment: transcript(localized_voice, "Subtitulo local independiente."),
        },
    );
    let profile = localized_profile(&project, Some(localized_voice));
    let project = apply(
        &f.service,
        &project,
        "explicit-localized-profile",
        Change::UpsertDeliverable {
            profile: profile.clone(),
        },
    );
    assert_eq!(project.deliverables[0], parent);
    assert_eq!(
        caption_sidecar(&project, parent.id).unwrap().body,
        original.body
    );
    let localized = caption_sidecar(&project, profile.id).unwrap();
    assert_eq!(localized.cue_count, 1);
    assert!(localized.body.contains("Subtitulo local independiente."));
    assert!(!localized.body.contains("Original English caption."));
    assert_ne!(localized.body, original.body);
    let before = f.service.export_project(project.id).unwrap();
    assert_source(&f.service, &project, f.source_voice, &f.source_bytes);
    assert_source(&f.service, &project, localized_voice, &localized_bytes);
    let path = f.folder.path().join("studio.sqlite3");
    drop(f.service);
    let reopened = StudioService::open(path).unwrap();
    assert_unchanged(&reopened, &before);
    let project = reopened.project(project.id).unwrap();
    assert_eq!(caption_sidecar(&project, profile.id).unwrap(), localized);
    assert_eq!(
        caption_sidecar(&project, parent.id).unwrap().body,
        original.body
    );
    assert_source(&reopened, &project, f.source_voice, &f.source_bytes);
    assert_source(&reopened, &project, localized_voice, &localized_bytes);
}

#[test]
fn historical_unbound_locales_can_be_opened_and_repaired_one_profile_at_a_time() {
    let f = fixture();
    let original = caption_sidecar(&f.project, f.project.deliverables[0].id).unwrap();
    let localized_bytes = wav(29, 1);
    let (project, localized_voice) = import_voice(
        &f.service,
        &f.project,
        f.folder.path(),
        "legacy-localized",
        &localized_bytes,
    );
    let project = apply(
        &f.service,
        &project,
        "legacy-retain-source",
        Change::SetActiveVoiceTrack {
            track_id: f.source_voice,
        },
    );
    let mut legacy = apply(
        &f.service,
        &project,
        "legacy-manual-localized-transcript",
        Change::AddTranscriptSegment {
            segment: transcript(localized_voice, "Texto recuperado de forma explicita."),
        },
    );
    // This serialized document shape was accepted by the previous domain and
    // storage APIs. Seed it through the read-compatible storage boundary, not
    // through the newly strict UpsertDeliverable mutation.
    // An initial historical snapshot has no journal, no previous branch
    // revisions and no inherited review/merge workspace to fabricate.
    assert_eq!(legacy.branches.len(), 1);
    assert!(legacy.branch_workspaces.is_empty());
    assert!(legacy.reviews.is_empty() && legacy.merges.is_empty());
    legacy.revision = 0;
    legacy.branches[0].base_revision = 0;
    legacy.branches[0].head_revision = 0;
    let first = localized_profile(&legacy, None);
    let mut second = localized_profile(&legacy, None);
    second.name = "Another historical locale".into();
    second.language = "fr-FR".into();
    legacy.deliverables.extend([first.clone(), second.clone()]);
    // Never inherit events from a different project/source: this document
    // starts as an initial, legitimate revision-zero serialized snapshot. The
    // current storage backup requires exactly one event per later revision.
    legacy.validate().unwrap();
    let legacy_path = f.folder.path().join("legacy.sqlite3");
    let service = StudioService::open(&legacy_path).unwrap();
    for (label, bytes) in [
        ("retained-original", f.source_bytes.as_slice()),
        ("retained-localized", localized_bytes.as_slice()),
    ] {
        let path = f.folder.path().join(format!("{label}.wav"));
        fs::write(&path, bytes).unwrap();
        service.ingest_blob_file(&path).unwrap();
        fs::remove_file(path).unwrap();
    }
    service.store().lock().create_project(&legacy).unwrap();
    let before = service.export_project(legacy.id).unwrap();
    drop(service);
    let service = StudioService::open(&legacy_path).unwrap();
    assert_unchanged(&service, &before);
    assert_eq!(
        service.store().lock().list_projects(100).unwrap(),
        vec![legacy.clone()]
    );
    // Readability must not grant delivery authority to either old profile.
    assert!(
        caption_sidecar(&legacy, first.id)
            .unwrap_err()
            .to_string()
            .contains("requires an explicit locale voice track")
    );
    assert!(caption_sidecar(&legacy, second.id).is_err());
    assert!(variant_dependency_fingerprint(&legacy, first.id).is_err());
    let mut repaired = first.clone();
    repaired.voice_track_id = Some(localized_voice);
    let project = apply(
        &service,
        &legacy,
        "repair-first-historical-locale",
        Change::UpsertDeliverable {
            profile: repaired.clone(),
        },
    );
    assert_eq!(project.deliverables[0], legacy.deliverables[0]);
    assert!(project.deliverables.contains(&second));
    let sidecar = caption_sidecar(&project, first.id).unwrap();
    assert!(
        sidecar
            .body
            .contains("Texto recuperado de forma explicita.")
    );
    assert!(!sidecar.body.contains("Original English caption."));
    assert!(variant_dependency_fingerprint(&project, first.id).is_ok());
    assert!(caption_sidecar(&project, second.id).is_err());
    let repaired_backup = service.export_project(project.id).unwrap();
    drop(service);
    let service = StudioService::open(&legacy_path).unwrap();
    assert_unchanged(&service, &repaired_backup);
    assert_eq!(caption_sidecar(&project, first.id).unwrap(), sidecar);
    assert_source(&service, &project, f.source_voice, &f.source_bytes);
    assert_source(&service, &project, localized_voice, &localized_bytes);
    let project = apply(
        &service,
        &project,
        "remove-second-incompatible-locale",
        Change::RemoveDeliverable {
            profile_id: second.id,
        },
    );
    let mut parent = project.deliverables[0].clone();
    parent.voice_track_id = Some(f.source_voice);
    let project = apply(
        &service,
        &project,
        "restore-explicit-parent-authority",
        Change::UpsertDeliverable { profile: parent },
    );
    assert_eq!(
        caption_sidecar(&project, project.deliverables[0].id)
            .unwrap()
            .body,
        original.body
    );
    assert_eq!(caption_sidecar(&project, first.id).unwrap(), sidecar);
    assert_source(&service, &project, f.source_voice, &f.source_bytes);
    assert_source(&service, &project, localized_voice, &localized_bytes);
}
