//! Existing file-import APIs: durable identity, scope before source IO and CAS content.
//! No recovery API, provider consent or installed GUI is implied by this baseline.
use motionwright_domain::{Change, Project};
use motionwright_service::{StudioService, VoiceImportMetadata};
use motionwright_storage::{ApplyOutcome, ProjectBackup, StorageError};
use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf};
use uuid::Uuid;

const MEDIA_TYPE: &str = "audio/wav";
const ASSET_NAME: &str = "File import regression asset";
struct Files {
    _directory: tempfile::TempDir,
    database: PathBuf,
    source: PathBuf,
    bytes: Vec<u8>,
}
fn pcm_wav(sample: i16) -> Vec<u8> {
    let rate = 48_000_u32;
    let channels = 1_u16;
    let frames = 4_800_u32;
    let block_align = channels * 2;
    let data_len = frames * u32::from(block_align);
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_len).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&channels.to_le_bytes());
    bytes.extend_from_slice(&rate.to_le_bytes());
    bytes.extend_from_slice(&(rate * u32::from(block_align)).to_le_bytes());
    bytes.extend_from_slice(&block_align.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_len.to_le_bytes());
    for _ in 0..frames * u32::from(channels) {
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    bytes
}
fn files() -> Files {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("imports.sqlite3");
    let source = directory.path().join("source.wav");
    let bytes = pcm_wav(19);
    fs::write(&source, &bytes).unwrap();
    Files {
        _directory: directory,
        database,
        source,
        bytes,
    }
}
fn open_project(files: &Files, title: &str) -> (StudioService, Project) {
    let service = StudioService::open(&files.database).unwrap();
    let project = service.create_project(title).unwrap();
    (service, project)
}
fn voice_metadata() -> VoiceImportMetadata {
    VoiceImportMetadata {
        name: "File import regression voice".into(),
        media_type: MEDIA_TYPE.into(),
        label: "Regression narration".into(),
    }
}
fn snapshot(service: &StudioService, id: Uuid) -> ProjectBackup {
    service.export_project(id).unwrap()
}
#[track_caller]
fn assert_unchanged(service: &StudioService, expected: &ProjectBackup) {
    let actual = snapshot(service, expected.project.id);
    assert_eq!(actual.format_version, expected.format_version);
    assert_eq!(actual.source_storage_schema, expected.source_storage_schema);
    assert_eq!(actual.project, expected.project);
    assert_eq!(actual.events, expected.events);
    assert_eq!(actual.payload_sha256, expected.payload_sha256);
}
#[track_caller]
fn assert_replayed(original: &ApplyOutcome, replay: &ApplyOutcome) {
    assert!(replay.replayed);
    assert_eq!(replay.request_id, original.request_id);
    assert_eq!(replay.previous_revision, original.previous_revision);
    assert_eq!(replay.project, original.project);
}
fn assert_original_blob(service: &StudioService, bytes: &[u8]) {
    let digest = hex::encode(Sha256::digest(bytes));
    let shared_store = service.store();
    let store = shared_store.lock();
    assert_eq!(store.read_blob(&digest, 32_768).unwrap(), bytes);
}
fn import_asset(
    service: &StudioService,
    project: &Project,
    key: &str,
    path: &std::path::Path,
) -> Result<ApplyOutcome, StorageError> {
    service.import_asset_file(
        project.id,
        &project.stamp(),
        key,
        path,
        ASSET_NAME.into(),
        MEDIA_TYPE.into(),
    )
}

#[test]
fn asset_import_replays_identical_bytes_and_metadata_after_reopen() {
    let files = files();
    let (service, project) = open_project(&files, "Asset replay regression");
    let committed = import_asset(&service, &project, "asset-file-replay", &files.source).unwrap();
    assert!(!committed.replayed);
    let expected = snapshot(&service, project.id);
    drop(service);
    let reopened = StudioService::open(&files.database).unwrap();
    let replay = import_asset(&reopened, &project, "asset-file-replay", &files.source)
        .expect("identical asset input must replay the original receipt");
    assert_replayed(&committed, &replay);
    assert_unchanged(&reopened, &expected);
}

#[test]
fn voice_import_replays_identical_bytes_and_metadata_after_reopen() {
    let files = files();
    let (service, project) = open_project(&files, "Voice replay regression");
    let committed = service
        .import_voice_file(
            project.id,
            &project.stamp(),
            "voice-file-replay",
            &files.source,
            voice_metadata(),
        )
        .unwrap();
    assert!(!committed.replayed);
    let expected = snapshot(&service, project.id);
    drop(service);
    let reopened = StudioService::open(&files.database).unwrap();
    let replay = reopened
        .import_voice_file(
            project.id,
            &project.stamp(),
            "voice-file-replay",
            &files.source,
            voice_metadata(),
        )
        .expect("identical voice input must replay the original receipt");
    assert_replayed(&committed, &replay);
    assert_unchanged(&reopened, &expected);
}

#[test]
fn invalid_import_authority_takes_precedence_over_missing_source_io() {
    enum Wanted {
        Scope,
        Missing,
        Revision,
    }
    let files = files();
    let (service, project) = open_project(&files, "Import authority regression");
    let foreign = service.create_project("Foreign authority control").unwrap();
    let advanced = service
        .apply(
            project.id,
            &project.stamp(),
            "advance-before-import",
            &Change::RenameProject {
                title: "Current project revision".into(),
            },
        )
        .unwrap();
    let expected = snapshot(&service, project.id);
    let foreign_expected = snapshot(&service, foreign.id);
    let missing_source = files.source.with_file_name("absent.wav");
    let missing_id = Uuid::from_u128(0xd079_4af0_9c08_4e41_86d9_9054_9a8c_ae53);
    assert_ne!(missing_id, project.id);
    assert_ne!(missing_id, foreign.id);
    let mut wrong_generation = advanced.project.stamp();
    wrong_generation.generation = foreign.generation;
    let mut wrong_resource = advanced.project.stamp();
    wrong_resource.resource = foreign.resource_key();
    let asset = |id, stamp, key| {
        service.import_asset_file(
            id,
            stamp,
            key,
            &missing_source,
            ASSET_NAME.into(),
            MEDIA_TYPE.into(),
        )
    };
    let voice = |id, stamp, key| {
        service.import_voice_file(id, stamp, key, &missing_source, voice_metadata())
    };
    // Evaluate every attempt before asserting so all baseline errors are visible.
    let checks = [
        (
            "asset/generation",
            Wanted::Scope,
            asset(project.id, &wrong_generation, "asset-wrong-generation"),
        ),
        (
            "voice/generation",
            Wanted::Scope,
            voice(project.id, &wrong_generation, "voice-wrong-generation"),
        ),
        (
            "asset/resource",
            Wanted::Scope,
            asset(project.id, &wrong_resource, "asset-wrong-resource"),
        ),
        (
            "voice/resource",
            Wanted::Scope,
            voice(project.id, &wrong_resource, "voice-wrong-resource"),
        ),
        (
            "asset/missing",
            Wanted::Missing,
            asset(missing_id, &advanced.project.stamp(), "asset-missing"),
        ),
        (
            "voice/missing",
            Wanted::Missing,
            voice(missing_id, &advanced.project.stamp(), "voice-missing"),
        ),
        (
            "asset/revision",
            Wanted::Revision,
            asset(project.id, &project.stamp(), "asset-stale"),
        ),
        (
            "voice/revision",
            Wanted::Revision,
            voice(project.id, &project.stamp(), "voice-stale"),
        ),
    ];
    let mut failures = Vec::new();
    for (label, wanted, result) in checks {
        let correct = match (wanted, &result) {
            (Wanted::Scope, Err(StorageError::GenerationConflict))
            | (Wanted::Missing, Err(StorageError::NotFound)) => true,
            (Wanted::Revision, Err(StorageError::Conflict { expected, actual })) => {
                *expected == project.revision && *actual == advanced.project.revision
            }
            _ => false,
        };
        if !correct {
            failures.push(format!("{label}: {result:?}"));
        }
    }
    assert_unchanged(&service, &expected);
    assert_unchanged(&service, &foreign_expected);
    assert!(
        failures.is_empty(),
        "authority errors must precede source IO:\n{}",
        failures.join("\n")
    );
}

#[test]
fn changed_file_bytes_cannot_reuse_a_committed_asset_import_key() {
    let files = files();
    let (service, project) = open_project(&files, "Changed bytes control");
    import_asset(&service, &project, "asset-changed-bytes", &files.source).unwrap();
    let expected = snapshot(&service, project.id);
    fs::write(&files.source, pcm_wav(20)).unwrap();
    assert!(matches!(
        import_asset(&service, &project, "asset-changed-bytes", &files.source),
        Err(StorageError::RequestReuse)
    ));
    assert_unchanged(&service, &expected);
    assert_original_blob(&service, &files.bytes);
}

#[test]
fn committed_import_keeps_real_cas_content_when_the_original_file_disappears() {
    let files = files();
    let (service, project) = open_project(&files, "Missing source control");
    let committed =
        import_asset(&service, &project, "asset-source-removed", &files.source).unwrap();
    assert!(!committed.replayed);
    assert_eq!(committed.project.revision, project.revision + 1);
    let expected = snapshot(&service, project.id);
    assert_eq!(expected.events.len(), 1);
    assert_original_blob(&service, &files.bytes);
    fs::remove_file(&files.source).unwrap();
    // Import reads the supplied file; explicit receipt recovery is a separate API.
    assert!(
        matches!(import_asset(&service, &project, "asset-source-removed", &files.source),
        Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound)
    );
    assert_unchanged(&service, &expected);
    assert_original_blob(&service, &files.bytes);
    drop(service);
    let reopened = StudioService::open(&files.database).unwrap();
    assert_unchanged(&reopened, &expected);
    assert_original_blob(&reopened, &files.bytes);
}

use motionwright_service::FileImportIntent;
use std::sync::{Arc, Barrier};

fn import_test_digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn remove_import_test_blob(service: &StudioService, bytes: &[u8]) -> String {
    let digest = import_test_digest(bytes);
    let path = service.store().lock().verified_blob_path(&digest).unwrap();
    fs::remove_file(path).unwrap();
    digest
}
#[track_caller]
fn assert_import_blob_missing(service: &StudioService, digest: &str) {
    let shared_store = service.store();
    let store = shared_store.lock();
    assert!(matches!(store.verified_blob_path(digest),
        Err(StorageError::BlobMissing { digest: missing }) if missing == digest));
    let published = store
        .blob_root()
        .join("sha256")
        .join(&digest[..2])
        .join(digest);
    assert!(
        !published.try_exists().unwrap(),
        "import must not publish or recreate this blob"
    );
}
#[track_caller]
fn assert_import_staging_empty(service: &StudioService) {
    let staging = service.store().lock().blob_root().join(".staging");
    match fs::read_dir(staging) {
        Ok(entries) => {
            let remaining: Vec<_> = entries.map(|entry| entry.unwrap().file_name()).collect();
            assert!(
                remaining.is_empty(),
                "import staging contains leftovers: {remaining:?}"
            );
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot inspect import staging: {error}"),
    }
}

#[test]
fn historical_asset_and_voice_recovery_preserves_head_without_source_or_cas() {
    let files = files();
    let (service, project) = open_project(&files, "Historical import recovery");
    let asset_key = "historical-asset-import";
    let voice_key = "historical-voice-import";
    let asset = import_asset(&service, &project, asset_key, &files.source).unwrap();
    let voice_source = files.source.with_file_name("historical-voice.wav");
    let voice_bytes = pcm_wav(20);
    fs::write(&voice_source, &voice_bytes).unwrap();
    let metadata = voice_metadata();
    let voice = service
        .import_voice_file(
            project.id,
            &asset.project.stamp(),
            voice_key,
            &voice_source,
            metadata.clone(),
        )
        .unwrap();
    let later = service
        .apply(
            project.id,
            &voice.project.stamp(),
            "later-edit",
            &Change::RenameProject {
                title: "Current head after both imports".into(),
            },
        )
        .unwrap();
    let expected = snapshot(&service, project.id);
    assert_eq!(expected.project, later.project);
    let asset_digest = remove_import_test_blob(&service, &files.bytes);
    let voice_digest = remove_import_test_blob(&service, &voice_bytes);
    assert_ne!(asset_digest, voice_digest);
    fs::remove_file(&files.source).unwrap();
    fs::remove_file(&voice_source).unwrap();
    drop(service);

    let reopened = StudioService::open(&files.database).unwrap();
    let asset_intent = FileImportIntent::Asset {
        name: ASSET_NAME.into(),
        media_type: MEDIA_TYPE.into(),
    };
    let voice_intent = FileImportIntent::Voice {
        name: metadata.name,
        media_type: metadata.media_type,
        label: metadata.label,
    };
    let recovered_asset = reopened
        .recover_file_import(project.id, &project.stamp(), asset_key, &asset_intent)
        .unwrap()
        .expect("asset receipt");
    assert_replayed(&asset, &recovered_asset);
    let recovered_voice = reopened
        .recover_file_import(project.id, &asset.project.stamp(), voice_key, &voice_intent)
        .unwrap()
        .expect("voice receipt");
    assert_replayed(&voice, &recovered_voice);
    assert!(
        reopened
            .recover_file_import(
                project.id,
                &project.stamp(),
                "absent-unknown-request",
                &asset_intent,
            )
            .unwrap()
            .is_none()
    );
    let mut foreign = project.stamp();
    foreign.generation = Uuid::new_v4();
    assert!(matches!(
        reopened.recover_file_import(project.id, &foreign, asset_key, &asset_intent,),
        Err(StorageError::GenerationConflict)
    ));
    assert_unchanged(&reopened, &expected);
    assert_import_blob_missing(&reopened, &asset_digest);
    assert_import_blob_missing(&reopened, &voice_digest);
    assert_import_staging_empty(&reopened);
    assert!(!files.source.try_exists().unwrap());
    assert!(!voice_source.try_exists().unwrap());
}

#[test]
fn normal_asset_and_voice_replay_does_not_rehydrate_missing_cas() {
    let files = files();
    let (service, project) = open_project(&files, "Replay without CAS repair");
    let asset_key = "asset-replay-with-missing-cas";
    let voice_key = "voice-replay-with-missing-cas";
    let metadata = voice_metadata();
    let asset = import_asset(&service, &project, asset_key, &files.source).unwrap();
    let voice = service
        .import_voice_file(
            project.id,
            &asset.project.stamp(),
            voice_key,
            &files.source,
            metadata.clone(),
        )
        .unwrap();
    let expected = snapshot(&service, project.id);
    let digest = remove_import_test_blob(&service, &files.bytes);
    assert_eq!(fs::read(&files.source).unwrap(), files.bytes);
    drop(service);
    let reopened = StudioService::open(&files.database).unwrap();
    let replay = import_asset(&reopened, &project, asset_key, &files.source).unwrap();
    assert_replayed(&asset, &replay);
    assert_import_blob_missing(&reopened, &digest);
    assert_import_staging_empty(&reopened);
    let replay = reopened
        .import_voice_file(
            project.id,
            &asset.project.stamp(),
            voice_key,
            &files.source,
            metadata,
        )
        .unwrap();
    assert_replayed(&voice, &replay);
    assert_import_blob_missing(&reopened, &digest);
    assert_import_staging_empty(&reopened);
    assert_unchanged(&reopened, &expected);
}

#[test]
fn concurrent_connections_import_identical_bytes_with_one_event_and_one_outcome() {
    let files = files();
    let (first, project) = open_project(&files, "Concurrent file import");
    let second = StudioService::open(&files.database).unwrap();
    let before = snapshot(&first, project.id);
    let project_id = project.id;
    let first_stamp = project.stamp();
    let second_stamp = project.stamp();
    let first_source = files.source.clone();
    let second_source = files.source.clone();
    let request_id = "concurrent-identical-file-import";
    let start = Arc::new(Barrier::new(3));
    let first_start = Arc::clone(&start);
    let second_start = Arc::clone(&start);
    let first_worker = std::thread::spawn(move || {
        first_start.wait();
        first
            .import_asset_file(
                project_id,
                &first_stamp,
                request_id,
                first_source,
                ASSET_NAME.into(),
                MEDIA_TYPE.into(),
            )
            .expect("first concurrent import")
    });
    let second_worker = std::thread::spawn(move || {
        second_start.wait();
        second
            .import_asset_file(
                project_id,
                &second_stamp,
                request_id,
                second_source,
                ASSET_NAME.into(),
                MEDIA_TYPE.into(),
            )
            .expect("second concurrent import")
    });
    start.wait();
    let first = first_worker.join().unwrap();
    let second = second_worker.join().unwrap();
    let (committed, replay) = match (first.replayed, second.replayed) {
        (false, true) => (&first, &second),
        (true, false) => (&second, &first),
        flags => panic!("expected one commit and one replay, received {flags:?}"),
    };
    assert_replayed(committed, replay);
    assert_eq!(committed.request_id, request_id);
    assert_eq!(committed.previous_revision, project.revision);
    assert_eq!(committed.project.revision, project.revision + 1);
    let reopened = StudioService::open(&files.database).unwrap();
    let after = snapshot(&reopened, project_id);
    assert_eq!(after.project, committed.project);
    assert_eq!(after.events.len(), before.events.len() + 1);
    assert_original_blob(&reopened, &files.bytes);
    assert_import_staging_empty(&reopened);
}

#[test]
fn rejected_active_svg_and_undecodable_voice_leave_no_published_blob_or_staging() {
    let files = files();
    let (service, project) = open_project(&files, "Rejected content publication");
    let expected = snapshot(&service, project.id);
    let svg_source = files.source.with_file_name("active.svg");
    // The actual .svg extension must survive staging even when MIME is wrong
    // and the active XML is beyond the content sniffer's prefix.
    let mut svg_bytes = vec![b' '; 16 * 1024 + 1];
    svg_bytes.extend_from_slice(
        br#"<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>"#,
    );
    fs::write(&svg_source, &svg_bytes).unwrap();
    let voice_source = files.source.with_file_name("invalid-voice.wav");
    let voice_bytes = b"not a RIFF/WAVE audio stream";
    fs::write(&voice_source, voice_bytes).unwrap();
    let svg_result = service.import_asset_file(
        project.id,
        &project.stamp(),
        "reject-active-svg",
        &svg_source,
        "Active SVG regression asset".into(),
        "image/png".into(),
    );
    let voice_result = service.import_voice_file(
        project.id,
        &project.stamp(),
        "reject-invalid-voice",
        &voice_source,
        voice_metadata(),
    );
    assert!(matches!(svg_result, Err(StorageError::Domain(_))));
    assert!(
        voice_result.is_err(),
        "undecodable bytes cannot become a voice import"
    );
    assert_unchanged(&service, &expected);
    assert_import_blob_missing(&service, &import_test_digest(&svg_bytes));
    assert_import_blob_missing(&service, &import_test_digest(voice_bytes));
    assert_import_staging_empty(&service);
}
