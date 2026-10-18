//! Admission regression at the real Store/StudioService file boundaries.
//! Synthetic credential-shaped bytes are test data, never real credentials.
//! The portable-export regressions retain separate legacy-state fixtures.
use motionwright_domain::{Asset, Change, Project, RationalTime};
use motionwright_service::{StudioService, VoiceImportMetadata};
use motionwright_storage::{ApplyOutcome, FileImportIntent, ProjectBackup, StorageError, Store};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn canary() -> String {
    format!("ghp_{}", "A".repeat(24))
}

fn rejected<T>(result: Result<T, StorageError>, marker: &str) {
    let error = match result {
        Err(error) => error,
        Ok(_) => panic!("credential-shaped file was admitted as a successful new input"),
    };
    assert!(matches!(&error, StorageError::InvalidBackup(_)));
    assert!(!error.to_string().contains(marker));
}

// Compare retained files by exact digest and length. Empty directory creation
// is allowed; removing somebody else's staging capture or a CAS file is not.
fn retained_files(root: &Path) -> BTreeMap<PathBuf, (usize, String)> {
    fn walk(root: &Path, directory: &Path, result: &mut BTreeMap<PathBuf, (usize, String)>) {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            if metadata.is_dir() {
                walk(root, &path, result);
            } else {
                assert!(metadata.is_file());
                let bytes = fs::read(&path).unwrap();
                result.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    (bytes.len(), digest(&bytes)),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    walk(root, root, &mut result);
    result
}

fn snapshot(service: &StudioService, project: Uuid) -> ProjectBackup {
    service.export_project(project).unwrap()
}

fn assert_unchanged(service: &StudioService, before: &ProjectBackup) {
    let after = snapshot(service, before.project.id);
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
}

fn assert_replayed(original: &ApplyOutcome, replay: &ApplyOutcome) {
    assert!(replay.replayed);
    assert_eq!(replay.request_id, original.request_id);
    assert_eq!(replay.previous_revision, original.previous_revision);
    assert_eq!(replay.project, original.project);
}

fn late_bytes(marker: &str, split: usize) -> Vec<u8> {
    assert!(split > 0 && split < marker.len());
    let mut bytes = vec![0x80; 128 * 1024 - split];
    bytes.extend_from_slice(marker.as_bytes());
    bytes.extend_from_slice(&[0, 0xff, 0x80]);
    bytes
}

fn voice_metadata() -> VoiceImportMetadata {
    VoiceImportMetadata {
        name: "Original narration".into(),
        media_type: "audio/wav".into(),
        label: "Measured voice".into(),
    }
}

fn voice_intent() -> FileImportIntent {
    let metadata = voice_metadata();
    FileImportIntent::Voice {
        name: metadata.name,
        media_type: metadata.media_type,
        label: metadata.label,
    }
}

// Every two-byte payload is a valid PCM16 sample. A marker changes samples,
// not the RIFF structure, codec, sample count or metadata parser validity.
fn pcm16_wav(marker: Option<&str>) -> Vec<u8> {
    let rate = 48_000_u32;
    let frames = 4_800_u32;
    let data_len = frames * 2;
    let mut bytes = Vec::with_capacity((44 + data_len) as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_len).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&rate.to_le_bytes());
    bytes.extend_from_slice(&(rate * 2).to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_len.to_le_bytes());
    for _ in 0..frames {
        bytes.extend_from_slice(&19_i16.to_le_bytes());
    }
    if let Some(marker) = marker {
        let start = 44 + 100;
        assert!(start + marker.len() < bytes.len());
        bytes[start..start + marker.len()].copy_from_slice(marker.as_bytes());
    }
    bytes
}

#[test]
fn store_and_service_raw_ingestion_reject_late_credentials_and_preserve_existing_files() {
    for service_wrapper in [false, true] {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("state.sqlite3")).unwrap();
        let ordinary = temp.path().join("ordinary.bin");
        fs::write(&ordinary, b"unrelated original asset").unwrap();
        let kept = service.ingest_blob_file(&ordinary).unwrap();
        let shared = service.store();
        let other_capture = shared.lock().stage_file_import(&ordinary).unwrap();
        let root = shared.lock().blob_root().to_path_buf();
        let before = retained_files(&root);
        let marker = canary();
        let bytes = late_bytes(&marker, 1);
        let source = temp.path().join("new.bin");
        fs::write(&source, &bytes).unwrap();

        let result = if service_wrapper {
            service.ingest_blob_file(&source)
        } else {
            shared.lock().ingest_blob_file(&source)
        };
        rejected(result, &marker);

        assert_eq!(retained_files(&root), before);
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert_eq!(
            shared
                .lock()
                .read_blob(&kept.sha256, kept.size_bytes)
                .unwrap(),
            b"unrelated original asset",
        );
        assert!(other_capture.path().is_file());
        drop(other_capture);
    }
}

#[test]
fn unbounded_staging_rejects_patterns_split_across_capture_reads() {
    let patterns = [
        format!("ghp_{}", "A".repeat(20)),
        format!("gho_{}", "B".repeat(20)),
        format!("ghu_{}", "C".repeat(20)),
        format!("ghs_{}", "D".repeat(20)),
        format!("xoxb-{}", "E".repeat(20)),
        format!("xoxa-{}", "F".repeat(20)),
        format!("xoxp-{}", "G".repeat(20)),
        format!("xoxr-{}", "H".repeat(20)),
        format!("xoxs-{}", "I".repeat(20)),
        format!("sk-{}", "J".repeat(24)),
        format!("AKIA{}", "K".repeat(16)),
        concat!("-----BEGIN ", "PRIVATE KEY-----").to_owned(),
    ];
    for marker in patterns {
        for split in [1, marker.len() - 1] {
            let temp = tempfile::tempdir().unwrap();
            let store = Store::open(temp.path().join("state.sqlite3")).unwrap();
            let ordinary = temp.path().join("other.bin");
            fs::write(&ordinary, b"another owner's live staging capture").unwrap();
            let other = store.stage_file_import(&ordinary).unwrap();
            let before = retained_files(store.blob_root());
            let bytes = late_bytes(&marker, split);
            let source = temp.path().join("source.bin");
            fs::write(&source, &bytes).unwrap();

            let result = store.stage_file_import(&source);
            rejected(result, &marker);

            assert_eq!(retained_files(store.blob_root()), before);
            assert_eq!(fs::read(&source).unwrap(), bytes);
            assert!(other.path().is_file());
            drop(other);
        }
    }
}

#[test]
fn canonical_asset_import_rejects_bytes_before_project_journal_or_receipt_changes() {
    for media_type in ["text/plain", "application/octet-stream"] {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("state.sqlite3")).unwrap();
        let initial = service.create_project("Import boundary").unwrap();
        let ordinary = temp.path().join("ordinary.bin");
        fs::write(&ordinary, b"existing project-owned original").unwrap();
        let project = service
            .import_asset_file(
                initial.id,
                &initial.stamp(),
                "ordinary-original",
                &ordinary,
                "Ordinary source".into(),
                "application/octet-stream".into(),
            )
            .unwrap()
            .project;
        let before = snapshot(&service, project.id);
        let shared = service.store();
        let other = shared.lock().stage_file_import(&ordinary).unwrap();
        let root = shared.lock().blob_root().to_path_buf();
        let files_before = retained_files(&root);
        let marker = canary();
        let bytes = late_bytes(&marker, marker.len() - 1);
        let source = temp.path().join("notes.txt");
        fs::write(&source, &bytes).unwrap();
        let intent = FileImportIntent::Asset {
            name: "Imported note".into(),
            media_type: media_type.into(),
        };

        rejected(
            service.import_asset_file(
                project.id,
                &project.stamp(),
                "refused-secret-asset",
                &source,
                "Imported note".into(),
                media_type.into(),
            ),
            &marker,
        );

        assert_unchanged(&service, &before);
        assert_eq!(retained_files(&root), files_before);
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert!(
            service
                .recover_file_import(
                    project.id,
                    &project.stamp(),
                    "refused-secret-asset",
                    &intent
                )
                .unwrap()
                .is_none()
        );
        assert!(other.path().is_file());
        drop(other);
    }
}

#[test]
fn canonical_voice_import_rejects_credential_shaped_pcm_before_commit() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("state.sqlite3")).unwrap();
    let initial = service.create_project("Voice boundary").unwrap();
    let ordinary = temp.path().join("ordinary.wav");
    let ordinary_bytes = pcm16_wav(None);
    fs::write(&ordinary, &ordinary_bytes).unwrap();
    let project = service
        .import_voice_file(
            initial.id,
            &initial.stamp(),
            "ordinary-voice",
            &ordinary,
            voice_metadata(),
        )
        .unwrap()
        .project;
    let track = &project.audio.voice_tracks[0];
    assert_eq!(track.sample_rate_hz, 48_000);
    assert_eq!(track.channels, 1);
    assert_eq!(
        track.measured_duration,
        RationalTime::new(4_800, 48_000).unwrap()
    );
    let before = snapshot(&service, project.id);
    let root = service.store().lock().blob_root().to_path_buf();
    let files_before = retained_files(&root);
    let marker = canary();
    let bytes = pcm16_wav(Some(&marker));
    assert!(
        bytes
            .windows(marker.len())
            .any(|window| window == marker.as_bytes())
    );
    assert_eq!(bytes.len(), ordinary_bytes.len());
    let source = temp.path().join("new-voice.wav");
    fs::write(&source, &bytes).unwrap();

    rejected(
        service.import_voice_file(
            project.id,
            &project.stamp(),
            "refused-secret-voice",
            &source,
            voice_metadata(),
        ),
        &marker,
    );

    assert_unchanged(&service, &before);
    assert_eq!(retained_files(&root), files_before);
    assert_eq!(fs::read(&source).unwrap(), bytes);
    assert_eq!(fs::read(&ordinary).unwrap(), ordinary_bytes);
    assert!(
        service
            .recover_file_import(
                project.id,
                &project.stamp(),
                "refused-secret-voice",
                &voice_intent(),
            )
            .unwrap()
            .is_none()
    );
}

#[test]
fn derived_cache_input_cannot_bypass_new_blob_admission_policy() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("state.sqlite3")).unwrap();
    let root = service.store().lock().blob_root().to_path_buf();
    let before = retained_files(&root);
    let marker = canary();
    let bytes = late_bytes(&marker, 1);
    let source = temp.path().join("cache-source.bin");
    fs::write(&source, &bytes).unwrap();
    let fingerprint = "ab".repeat(32);

    rejected(
        service.put_derived_cache_file("fixture-scope", "waveform", &fingerprint, &source, 1),
        &marker,
    );

    assert_eq!(retained_files(&root), before);
    assert_eq!(fs::read(&source).unwrap(), bytes);
    assert!(
        service
            .lookup_derived_cache("fixture-scope", "waveform", &fingerprint)
            .unwrap()
            .is_none()
    );
}

// This is an explicit pre-policy fixture, not a product admission API. Only
// fixture-owned CAS bytes are seeded directly; Store::apply still builds and
// verifies the actual project row, event and typed receipt.
fn seed_legacy_import(
    database: &Path,
    source: &Path,
) -> (Project, ApplyOutcome, FileImportIntent, PathBuf) {
    let mut store = Store::open(database).unwrap();
    let initial = store.create_named_project("Legacy original").unwrap();
    let bytes = fs::read(source).unwrap();
    let sha256 = digest(&bytes);
    let leaf = store
        .blob_root()
        .join("sha256")
        .join(&sha256[..2])
        .join(&sha256);
    fs::create_dir_all(leaf.parent().unwrap()).unwrap();
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut output = options.open(&leaf).unwrap();
    output.write_all(&bytes).unwrap();
    output.sync_all().unwrap();
    drop(output);
    assert_eq!(
        store.verify_blob(&sha256).unwrap().size_bytes,
        bytes.len() as u64
    );
    let intent = FileImportIntent::Asset {
        name: "Legacy note".into(),
        media_type: "application/octet-stream".into(),
    };
    let outcome = store
        .apply(
            initial.id,
            &initial.stamp(),
            "legacy-file-import",
            &Change::AddAsset {
                asset: Asset {
                    id: Uuid::now_v7(),
                    name: "Legacy note".into(),
                    media_type: "application/octet-stream".into(),
                    content_sha256: Some(sha256),
                    source_revision: Some("local-import".into()),
                },
            },
        )
        .unwrap();
    (initial, outcome, intent, leaf)
}

#[test]
fn known_legacy_import_keeps_source_binding_and_source_free_receipt_recovery() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("state.sqlite3");
    let source = temp.path().join("legacy.txt");
    let marker = canary();
    let bytes = format!("legacy fixture={marker}\n").into_bytes();
    fs::write(&source, &bytes).unwrap();
    let (initial, committed, intent, cas_path) = seed_legacy_import(&database, &source);
    let service = StudioService::open(&database).unwrap();
    assert_eq!(service.project(initial.id).unwrap(), committed.project);
    let advanced = service
        .apply(
            initial.id,
            &committed.project.stamp(),
            "later-edit",
            &Change::RenameProject {
                title: "Current canonical project".into(),
            },
        )
        .unwrap()
        .project;
    let before = snapshot(&service, initial.id);

    // A known import verifies the supplied source digest but never repairs CAS.
    fs::remove_file(&cas_path).unwrap();
    let root = service.store().lock().blob_root().to_path_buf();
    let files_before = retained_files(&root);
    let replay = service
        .import_asset_file(
            initial.id,
            &initial.stamp(),
            "legacy-file-import",
            &source,
            "Legacy note".into(),
            "application/octet-stream".into(),
        )
        .unwrap();
    assert_replayed(&committed, &replay);
    assert_eq!(service.project(initial.id).unwrap(), advanced);
    assert_eq!(retained_files(&root), files_before);
    assert_eq!(fs::read(&source).unwrap(), bytes);

    // Deliberately change this fixture source to prove normal replay still
    // checks it; changing the shared scanner must not erase that distinction.
    fs::write(&source, b"different known-request input").unwrap();
    assert!(matches!(
        service.import_asset_file(
            initial.id,
            &initial.stamp(),
            "legacy-file-import",
            &source,
            "Legacy note".into(),
            "application/octet-stream".into(),
        ),
        Err(StorageError::RequestReuse)
    ));
    fs::remove_file(&source).unwrap();
    assert!(matches!(
        service.import_asset_file(
            initial.id,
            &initial.stamp(),
            "legacy-file-import",
            &source,
            "Legacy note".into(),
            "application/octet-stream".into(),
        ),
        Err(StorageError::Io(_))
    ));
    let recovered = service
        .recover_file_import(initial.id, &initial.stamp(), "legacy-file-import", &intent)
        .unwrap()
        .unwrap();
    assert_replayed(&committed, &recovered);
    assert_unchanged(&service, &before);
    assert_eq!(retained_files(&root), files_before);
    assert!(!source.exists());
    assert!(!cas_path.exists());
}

#[test]
fn ordinary_inputs_preserve_original_bytes_measurement_and_reopened_state() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("state.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let initial = service.create_project("Ordinary import control").unwrap();
    // One byte below the existing suffix threshold is ordinary text.
    let mut ordinary_bytes = vec![0, 0x80, 0xff];
    ordinary_bytes.extend_from_slice(format!(" ghp_{}.", "A".repeat(19)).as_bytes());
    let ordinary = temp.path().join("ordinary.bin");
    fs::write(&ordinary, &ordinary_bytes).unwrap();
    let asset = service
        .import_asset_file(
            initial.id,
            &initial.stamp(),
            "ordinary-asset",
            &ordinary,
            "Ordinary bytes".into(),
            "application/octet-stream".into(),
        )
        .unwrap()
        .project;
    let voice = temp.path().join("ordinary.wav");
    let voice_bytes = pcm16_wav(None);
    fs::write(&voice, &voice_bytes).unwrap();
    let outcome = service
        .import_voice_file(
            asset.id,
            &asset.stamp(),
            "ordinary-voice",
            &voice,
            voice_metadata(),
        )
        .unwrap();
    let track = &outcome.project.audio.voice_tracks[0];
    assert_eq!(
        track.measured_duration,
        RationalTime::new(4_800, 48_000).unwrap()
    );
    assert_eq!(track.sample_rate_hz, 48_000);
    assert_eq!(track.channels, 1);
    let expected = snapshot(&service, initial.id);
    drop(service);
    let reopened = StudioService::open(&database).unwrap();

    assert_unchanged(&reopened, &expected);
    let replay = reopened
        .import_voice_file(
            asset.id,
            &asset.stamp(),
            "ordinary-voice",
            &voice,
            voice_metadata(),
        )
        .unwrap();
    assert_replayed(&outcome, &replay);
    let shared = reopened.store();
    let store = shared.lock();
    for bytes in [&ordinary_bytes, &voice_bytes] {
        assert_eq!(
            store.read_blob(&digest(bytes), bytes.len() as u64).unwrap(),
            *bytes,
        );
    }
    assert_eq!(fs::read(&ordinary).unwrap(), ordinary_bytes);
    assert_eq!(fs::read(&voice).unwrap(), voice_bytes);
}
