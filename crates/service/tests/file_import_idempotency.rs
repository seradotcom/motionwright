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
