//! Durable file-import receipt inspection and validated CAS capture.
use super::{
    ApplyOutcome, Change, Result, RevisionStamp, StorageError, Store, TransactionBehavior,
    validate_digest,
};
use motionwright_domain::DomainError;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum FileImportIntent {
    Asset {
        name: String,
        media_type: String,
    },
    Voice {
        name: String,
        media_type: String,
        label: String,
    },
}

#[derive(Debug, Clone, Serialize)]
pub struct RecordedFileImport {
    pub outcome: ApplyOutcome,
    pub source_sha256: String,
}

fn invalid_receipt(reason: impl Into<String>) -> StorageError {
    StorageError::InvalidFileImportReceipt(reason.into())
}

fn verify_import_binding(
    change: &Change,
    outcome: &ApplyOutcome,
    intent: &FileImportIntent,
) -> Result<String> {
    let (asset, track) = match change {
        Change::AddAsset { asset } => (asset, None),
        Change::ImportMeasuredVoice { asset, track } => (asset, Some(track)),
        _ => return Err(StorageError::RequestReuse),
    };
    let mut assets = outcome
        .project
        .assets
        .iter()
        .filter(|recorded| recorded.id == asset.id);
    if assets.next() != Some(asset) || assets.next().is_some() {
        return Err(invalid_receipt(
            "event asset does not match exactly one recorded outcome asset",
        ));
    }
    if let Some(track) = track {
        let mut tracks = outcome
            .project
            .audio
            .voice_tracks
            .iter()
            .filter(|recorded| recorded.id == track.id);
        if tracks.next() != Some(track) || tracks.next().is_some() || track.asset_id != asset.id {
            return Err(invalid_receipt(
                "event voice track does not match its asset and recorded outcome",
            ));
        }
    }
    let purpose = if track.is_some() {
        "measured-audio-import"
    } else {
        "local-import"
    };
    if asset.source_revision.as_deref() != Some(purpose) {
        return Err(StorageError::RequestReuse);
    }
    let source_sha256 = asset
        .content_sha256
        .as_deref()
        .ok_or_else(|| invalid_receipt("import event has no source digest"))?;
    validate_digest(source_sha256).map_err(|_| invalid_receipt("invalid import source digest"))?;
    if let Some(track) = track
        && track.source_sha256 != source_sha256
    {
        return Err(invalid_receipt(
            "voice track and asset source digests differ",
        ));
    }
    let matches_intent = match (intent, track) {
        (FileImportIntent::Asset { name, media_type }, None) => {
            asset.name == *name && asset.media_type == *media_type
        }
        (
            FileImportIntent::Voice {
                name,
                media_type,
                label,
            },
            Some(track),
        ) => asset.name == *name && asset.media_type == *media_type && track.label == *label,
        _ => false,
    };
    if !matches_intent {
        return Err(StorageError::RequestReuse);
    }
    Ok(source_sha256.to_owned())
}

impl Store {
    /// Read a verified historical import result without source or CAS access.
    ///
    /// Absence does not authorize retry of an UNKNOWN effect. A new import
    /// requests a current-revision check only when no receipt was observed.
    pub fn inspect_file_import(
        &mut self,
        id: Uuid,
        expected: &RevisionStamp,
        key: &str,
        intent: &FileImportIntent,
        require_current_if_absent: bool,
    ) -> Result<Option<RecordedFileImport>> {
        if key.is_empty() || key.len() > 256 || key.chars().any(char::is_control) {
            return Err(DomainError::Invalid("invalid request id".into()).into());
        }
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)?;
        let current = super::load_project_from_row(&tx, id).map_err(|error| match error {
            StorageError::Serde(_) => invalid_receipt("invalid current project JSON"),
            StorageError::Domain(_) => {
                invalid_receipt("invalid current project document or authority columns")
            }
            other => other,
        })?;
        if current.id != id
            || current.resource_key() != expected.resource
            || current.generation != expected.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        let receipt = super::apply_receipt_binding::read_receipt(&tx, id, key)
            .map_err(super::apply_receipt_binding::ReceiptCheckError::into_import_error)?;
        let Some(receipt) = receipt else {
            if require_current_if_absent && expected.revision != current.revision {
                return Err(StorageError::Conflict {
                    expected: expected.revision,
                    actual: current.revision,
                });
            }
            tx.commit()?;
            return Ok(None);
        };
        if super::project_creation::is_creation_receipt(&receipt.result_json) {
            return Err(StorageError::RequestReuse);
        }
        let checked = super::apply_receipt_binding::check_receipt(&tx, &current, key, &receipt)
            .map_err(super::apply_receipt_binding::ReceiptCheckError::into_import_error)?;
        let mut outcome = checked.outcome;
        let source_sha256 = verify_import_binding(&checked.change, &outcome, intent)?;
        outcome.replayed = true;
        tx.commit()?;
        Ok(Some(RecordedFileImport {
            outcome,
            source_sha256,
        }))
    }
}

use super::{
    BlobDescriptor, create_checked_blob_directory, ensure_regular_directory, ensure_regular_file,
};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{ErrorKind, Read, Write},
    path::{Path, PathBuf},
};

#[cfg(test)]
type ImportSourceOpenHook = Box<dyn FnOnce(&Path)>;

#[cfg(test)]
std::thread_local! {
    static BEFORE_IMPORT_SOURCE_OPEN: std::cell::RefCell<Option<ImportSourceOpenHook>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
fn run_import_source_open_hook(source: &Path) {
    let hook = BEFORE_IMPORT_SOURCE_OPEN.with(|slot| slot.borrow_mut().take());
    if let Some(hook) = hook {
        hook(source);
    }
}

#[cfg(test)]
type ImportSourceReadHook = Box<dyn FnOnce(&Path)>;

#[cfg(test)]
std::thread_local! {
    static AFTER_FIRST_IMPORT_SOURCE_READ: std::cell::RefCell<Option<ImportSourceReadHook>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
fn run_import_source_read_hook(source: &Path) {
    let hook = AFTER_FIRST_IMPORT_SOURCE_READ.with(|slot| slot.borrow_mut().take());
    if let Some(hook) = hook {
        hook(source);
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn open_import_source_handle(source: &Path) -> std::io::Result<File> {
    use rustix::fs::{Mode, OFlags, open};
    let descriptor = open(
        source,
        OFlags::RDONLY | OFlags::CLOEXEC | OFlags::NOFOLLOW | OFlags::NONBLOCK,
        Mode::empty(),
    )
    .map_err(std::io::Error::from)?;
    Ok(File::from(descriptor))
}

#[cfg(windows)]
fn open_import_source_handle(source: &Path) -> std::io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT;
    OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(source)
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn open_import_source_handle(_source: &Path) -> std::io::Result<File> {
    Err(std::io::Error::new(
        ErrorKind::Unsupported,
        "safe file-import source opening is unavailable on this platform",
    ))
}

/// Validate and retain the opened source, not just a prior pathname snapshot.
/// Ancestor aliases and concurrent writes to regular files are outside this
/// leaf-admission boundary; the capture keeps its independent streaming limit.
fn open_regular_import_source(source: &Path, max_bytes: u64) -> Result<File> {
    let size = ensure_regular_file(source, "import source")?.len();
    if size > max_bytes {
        return Err(StorageError::BlobTooLarge {
            size,
            limit: max_bytes,
        });
    }
    #[cfg(test)]
    run_import_source_open_hook(source);

    let input = open_import_source_handle(source)?;

    let metadata = input.metadata()?;
    #[cfg(windows)]
    let reparse = {
        use std::os::windows::fs::MetadataExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    };
    #[cfg(not(windows))]
    let reparse = false;
    if !metadata.is_file() || metadata.file_type().is_symlink() || reparse {
        return Err(StorageError::UnsafeSourcePath(
            "file import requires a regular non-reparse opened source".into(),
        ));
    }
    if metadata.len() > max_bytes {
        return Err(StorageError::BlobTooLarge {
            size: metadata.len(),
            limit: max_bytes,
        });
    }
    Ok(input)
}

impl Store {
    /// Verify bytes for a known import replay without staging, CAS or database IO.
    pub fn hash_file_import_source(source: &Path) -> Result<String> {
        Self::hash_file_import_source_with_limit(source, u64::MAX)
    }

    /// Hash the retained regular source, rejecting excess bytes before hashing them.
    pub fn hash_file_import_source_with_limit(source: &Path, max_bytes: u64) -> Result<String> {
        let input = open_regular_import_source(source, max_bytes)?;
        let mut input = input.take(max_bytes.saturating_add(1));
        let mut digest = Sha256::new();
        let mut chunk = [0_u8; 128 * 1024];
        let mut size_bytes = 0_u64;
        #[cfg(test)]
        let mut first_read = true;
        loop {
            let count = match input.read(&mut chunk) {
                Err(error) if error.kind() == ErrorKind::Interrupted => continue,
                result => result?,
            };
            if count == 0 {
                break;
            }
            #[cfg(test)]
            if first_read {
                first_read = false;
                run_import_source_read_hook(source);
            }
            let next_size =
                size_bytes
                    .checked_add(count as u64)
                    .ok_or(StorageError::BlobTooLarge {
                        size: u64::MAX,
                        limit: max_bytes,
                    })?;
            if next_size > max_bytes {
                return Err(StorageError::BlobTooLarge {
                    size: next_size,
                    limit: max_bytes,
                });
            }
            digest.update(&chunk[..count]);
            size_bytes = next_size;
        }
        Ok(hex::encode(digest.finalize()))
    }
}

/// A private capture awaiting validation and optional CAS publication.
///
/// Drop removes only the private staging name, never a published CAS blob.
#[derive(Debug)]
pub struct StagedFileImport {
    path: PathBuf,
    descriptor: BlobDescriptor,
    blob_root: PathBuf,
}
impl StagedFileImport {
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn descriptor(&self) -> &BlobDescriptor {
        &self.descriptor
    }
}
impl Drop for StagedFileImport {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}
impl Store {
    /// Capture the source once without a SQLite writer transaction.
    pub fn stage_file_import(&self, source: &Path) -> Result<StagedFileImport> {
        self.stage_file_import_with_limit(source, u64::MAX)
    }

    /// Capture at most the admitted byte budget into one private staging file.
    /// Reading one extra byte detects growth after the metadata check; excess
    /// bytes are rejected before they can be written to that capture.
    pub fn stage_file_import_with_limit(
        &self,
        source: &Path,
        max_bytes: u64,
    ) -> Result<StagedFileImport> {
        ensure_regular_directory(&self.blob_root, "blob root")?;
        let input = open_regular_import_source(source, max_bytes)?;
        let staging_root = self.blob_root.join(".staging");
        create_checked_blob_directory(&staging_root)?;
        let path = staging_root.join(format!("{}.part", Uuid::now_v7().simple()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        // A create_new collision must never clean up another call's file.
        let output = options.open(&path)?;
        let mut staged = StagedFileImport {
            path,
            descriptor: BlobDescriptor {
                sha256: String::new(),
                size_bytes: 0,
            },
            blob_root: self.blob_root.clone(),
        };
        {
            // Handles close before the outer guard cleans up on any error.
            let mut input = input.take(max_bytes.saturating_add(1));
            let mut output = output;
            let mut hasher = Sha256::new();
            let mut credentials = super::CredentialScan::default();
            let mut buffer = [0_u8; 128 * 1024];
            let mut size_bytes = 0_u64;
            #[cfg(test)]
            let mut first_read = true;
            loop {
                let count = input.read(&mut buffer)?;
                if count == 0 {
                    break;
                }
                #[cfg(test)]
                if first_read {
                    first_read = false;
                    run_import_source_read_hook(source);
                }
                let next_size =
                    size_bytes
                        .checked_add(count as u64)
                        .ok_or(StorageError::BlobTooLarge {
                            size: u64::MAX,
                            limit: max_bytes,
                        })?;
                if next_size > max_bytes {
                    return Err(StorageError::BlobTooLarge {
                        size: next_size,
                        limit: max_bytes,
                    });
                }
                credentials.check(&buffer[..count], "file import source")?;
                output.write_all(&buffer[..count])?;
                hasher.update(&buffer[..count]);
                size_bytes = next_size;
            }
            output.flush()?;
            output.sync_all()?;
            staged.descriptor = BlobDescriptor {
                sha256: hex::encode(hasher.finalize()),
                size_bytes,
            };
        }
        Ok(staged)
    }

    /// Publish only the unchanged validated capture; never overwrite a blob.
    pub fn publish_file_import(&self, staged: &StagedFileImport) -> Result<BlobDescriptor> {
        publish_file_import_under_root(&self.blob_root, staged)
    }
}

/// Publish an already validated capture without borrowing the SQLite handle.
/// Callers may hold an SQLite IMMEDIATE transaction that serializes project ID
/// claims while this filesystem-only step runs.
pub(super) fn publish_file_import_under_root(
    blob_root: &Path,
    staged: &StagedFileImport,
) -> Result<BlobDescriptor> {
    if staged.blob_root.as_path() != blob_root {
        return Err(StorageError::UnsafeSourcePath(
            "staged import belongs to another blob root".into(),
        ));
    }
    ensure_regular_directory(blob_root, "blob root")?;
    let staging_root = blob_root.join(".staging");
    ensure_regular_directory(&staging_root, "blob staging directory")?;
    if staged.path.parent() != Some(staging_root.as_path()) {
        return Err(StorageError::UnsafeSourcePath(
            "staged import is outside the blob staging directory".into(),
        ));
    }
    let captured =
        super::portability::hash_portable_file(&staged.path, staged.descriptor.size_bytes)?;
    if captured != staged.descriptor {
        return Err(StorageError::InvalidBlobDigest);
    }
    super::validate_digest(&captured.sha256)?;
    let destination = blob_root
        .join("sha256")
        .join(&captured.sha256[..2])
        .join(&captured.sha256);
    let sha_root = blob_root.join("sha256");
    create_checked_blob_directory(&sha_root)?;
    create_checked_blob_directory(&sha_root.join(&captured.sha256[..2]))?;
    let verify_existing = match fs::symlink_metadata(&destination) {
        Ok(_) => true,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            match fs::hard_link(&staged.path, &destination) {
                Ok(()) => false,
                Err(error) if error.kind() == ErrorKind::AlreadyExists => true,
                Err(error) => return Err(error.into()),
            }
        }
        Err(error) => return Err(error.into()),
    };
    if verify_existing {
        let existing = super::checked_existing_blob_path_from_root(blob_root, &captured.sha256)?;
        if super::portability::hash_portable_file(&existing, captured.size_bytes)? != captured {
            return Err(StorageError::InvalidBlobDigest);
        }
    }
    Ok(captured)
}

#[cfg(test)]
mod tests {
    use super::FileImportIntent;
    use crate::{ApplyOutcome, StorageError, Store, request_digest};
    use motionwright_domain::{Asset, Change, Project};
    use rusqlite::{params, types::Value};
    use std::{fs, path::Path};
    use uuid::Uuid;

    const KEY: &str = "legacy-file-import";
    const NAME: &str = "original.wav";
    const MEDIA_TYPE: &str = "audio/wav";
    const SOURCE: &[u8] = b"RIFF\x26\0\0\0WAVEfmt \x10\0\0\0\
        \x01\0\x01\0\x40\x1f\0\0\x80\x3e\0\0\x02\0\x10\0\
        data\x02\0\0\0\0\0";

    fn asset_intent() -> FileImportIntent {
        FileImportIntent::Asset {
            name: NAME.into(),
            media_type: MEDIA_TYPE.into(),
        }
    }
    fn legacy_import(root: &Path) -> (Store, Project, ApplyOutcome, String) {
        let mut store = Store::open(root.join("state.sqlite3")).unwrap();
        let original = store.create_named_project("Recorded file import").unwrap();
        let source = root.join(NAME);
        fs::write(&source, SOURCE).unwrap();
        let descriptor = store.ingest_blob_file(&source).unwrap();
        let change = Change::AddAsset {
            asset: Asset {
                id: Uuid::now_v7(),
                name: NAME.into(),
                media_type: MEDIA_TYPE.into(),
                content_sha256: Some(descriptor.sha256.clone()),
                source_revision: Some("local-import".into()),
            },
        };
        let outcome = store
            .apply(original.id, &original.stamp(), KEY, &change)
            .unwrap();
        assert!(!outcome.replayed);
        (store, original, outcome, descriptor.sha256)
    }
    // Persisted SQL values are the oracle; WAL file layout is not.
    fn snapshot(store: &Store) -> Vec<Vec<Vec<Value>>> {
        [
            "SELECT * FROM projects ORDER BY id",
            "SELECT * FROM events ORDER BY project_id,revision",
            "SELECT * FROM receipts ORDER BY project_id,request_id",
        ]
        .into_iter()
        .map(|sql| {
            let mut statement = store.conn.prepare(sql).unwrap();
            let columns = statement.column_count();
            let rows = statement
                .query_map([], |row| {
                    (0..columns)
                        .map(|column| row.get::<_, Value>(column))
                        .collect::<rusqlite::Result<Vec<_>>>()
                })
                .unwrap();
            rows.map(|row| row.unwrap()).collect()
        })
        .collect()
    }

    #[test]
    fn original_import_remains_readable_without_sources_after_edit_and_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let (mut store, original, first, source_sha256) = legacy_import(directory.path());
        let stored_source = store.verified_blob_path(&source_sha256).unwrap();
        fs::remove_file(directory.path().join(NAME)).unwrap();
        fs::remove_file(&stored_source).unwrap();
        let latest = store
            .apply(
                original.id,
                &first.project.stamp(),
                "later-edit",
                &Change::RenameProject {
                    title: "A later project revision".into(),
                },
            )
            .unwrap();
        drop(store);
        let mut reopened = Store::open(directory.path().join("state.sqlite3")).unwrap();
        let before = snapshot(&reopened);
        let recorded = reopened
            .inspect_file_import(original.id, &original.stamp(), KEY, &asset_intent(), true)
            .unwrap()
            .expect("the original committed import must remain observable");
        assert!(recorded.outcome.replayed);
        assert_eq!(recorded.outcome.project, first.project);
        assert_eq!(recorded.outcome.previous_revision, first.previous_revision);
        assert_eq!(recorded.outcome.request_id, first.request_id);
        assert_eq!(recorded.source_sha256, source_sha256);
        assert_eq!(snapshot(&reopened), before);
        assert_eq!(
            reopened.export_project(original.id).unwrap().project,
            latest.project
        );
        assert!(!directory.path().join(NAME).exists());
        assert!(!stored_source.exists());
        assert!(matches!(
            reopened.read_blob(&source_sha256, 4096),
            Err(StorageError::BlobMissing { .. })
        ));
    }

    #[test]
    fn absent_receipt_requires_current_revision_only_when_requested() {
        let directory = tempfile::tempdir().unwrap();
        let (mut store, original, first, _) = legacy_import(directory.path());
        let before = snapshot(&store);
        assert!(
            store
                .inspect_file_import(
                    original.id,
                    &original.stamp(),
                    "absent-import",
                    &asset_intent(),
                    false
                )
                .unwrap()
                .is_none()
        );
        assert_eq!(snapshot(&store), before);
        assert!(
            matches!(store.inspect_file_import(original.id, &original.stamp(),
            "absent-import", &asset_intent(), true),
            Err(StorageError::Conflict { expected, actual })
            if expected == original.revision && actual == first.project.revision)
        );
        assert_eq!(snapshot(&store), before);
    }

    #[test]
    fn corrupt_receipt_hash_event_or_result_is_never_treated_as_absent() {
        for corruption in ["hash", "event", "result_asset"] {
            let directory = tempfile::tempdir().unwrap();
            let (mut store, original, first, _) = legacy_import(directory.path());
            match corruption {
                "hash" => {
                    assert_eq!(store.conn.execute(
                        "UPDATE receipts SET request_sha256=?3 WHERE project_id=?1 AND request_id=?2",
                        params![original.id.to_string(), KEY, "00".repeat(32)],
                    ).unwrap(), 1);
                }
                "event" => {
                    let mut asset = first.project.assets[0].clone();
                    asset.id = Uuid::now_v7();
                    let changed_event = Change::AddAsset { asset };
                    let changed_digest = request_digest(original.id, &changed_event).unwrap();
                    assert_eq!(store.conn.execute(
                        "UPDATE events SET payload_json=?3 WHERE project_id=?1 AND revision=?2",
                        params![original.id.to_string(), i64::try_from(first.project.revision).unwrap(),
                            serde_json::to_string(&changed_event).unwrap()],
                    ).unwrap(), 1);
                    assert_eq!(store.conn.execute(
                        "UPDATE receipts SET request_sha256=?3 WHERE project_id=?1 AND request_id=?2",
                        params![original.id.to_string(), KEY, changed_digest],
                    ).unwrap(), 1);
                }
                "result_asset" => {
                    let mut altered: ApplyOutcome = first.clone();
                    altered.project.assets[0].name = "different-result.wav".into();
                    altered.project.validate().unwrap();
                    assert_eq!(store.conn.execute(
                        "UPDATE receipts SET result_json=?3 WHERE project_id=?1 AND request_id=?2",
                        params![original.id.to_string(), KEY, serde_json::to_string(&altered).unwrap()],
                    ).unwrap(), 1);
                }
                _ => unreachable!(),
            }
            let before = snapshot(&store);
            let result = store.inspect_file_import(
                original.id,
                &original.stamp(),
                KEY,
                &asset_intent(),
                true,
            );
            assert!(
                matches!(result, Err(StorageError::InvalidFileImportReceipt(_))),
                "{corruption} corruption must be reported explicitly"
            );
            assert_eq!(
                snapshot(&store),
                before,
                "inspection cannot repair {corruption}"
            );
        }
    }

    #[test]
    fn changed_purpose_or_metadata_rejects_request_reuse_without_effects() {
        let directory = tempfile::tempdir().unwrap();
        let (mut store, original, _, _) = legacy_import(directory.path());
        let before = snapshot(&store);
        let incompatible = [
            FileImportIntent::Asset {
                name: "another-name.wav".into(),
                media_type: MEDIA_TYPE.into(),
            },
            FileImportIntent::Asset {
                name: NAME.into(),
                media_type: "audio/flac".into(),
            },
            FileImportIntent::Voice {
                name: NAME.into(),
                media_type: MEDIA_TYPE.into(),
                label: "Imported voice".into(),
            },
        ];
        for intent in incompatible {
            assert!(matches!(
                store.inspect_file_import(original.id, &original.stamp(), KEY, &intent, true,),
                Err(StorageError::RequestReuse)
            ));
            assert_eq!(snapshot(&store), before);
        }
        assert!(
            store
                .inspect_file_import(original.id, &original.stamp(), KEY, &asset_intent(), true,)
                .unwrap()
                .is_some()
        );
        assert_eq!(snapshot(&store), before);
    }
}

#[cfg(test)]
mod staging_tests {
    use super::*;
    use crate::hash_file;
    #[test]
    fn staged_import_rejects_changed_capture_without_publishing_or_leaking_stage() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().join("state.sqlite3")).unwrap();
        let source = temp.path().join("source.bin");
        fs::write(&source, b"snapshot-A").unwrap();
        let staged = store.stage_file_import(&source).unwrap();
        let stage_path = staged.path().to_path_buf();
        let staging_root = stage_path.parent().unwrap().to_path_buf();
        let captured = staged.descriptor().clone();
        fs::write(&stage_path, b"snapshot-B").unwrap();
        let altered = hash_file(&stage_path).unwrap();
        assert_eq!(altered.size_bytes, captured.size_bytes);
        assert_ne!(altered.sha256, captured.sha256);
        let result = store.publish_file_import(&staged);
        drop(staged);
        assert!(matches!(result, Err(StorageError::InvalidBlobDigest)));
        for digest in [&captured.sha256, &altered.sha256] {
            assert!(
                matches!(fs::symlink_metadata(store.blob_path(digest).unwrap()),
                Err(error) if error.kind() == ErrorKind::NotFound)
            );
        }
        assert!(!stage_path.try_exists().unwrap());
        assert!(fs::read_dir(&staging_root).unwrap().next().is_none());
        assert_eq!(fs::read(source).unwrap(), b"snapshot-A");
    }

    #[test]
    fn dropping_duplicate_stages_and_rejecting_stale_import_preserve_blob() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("state.sqlite3")).unwrap();
        let original = store.create_named_project("Staged import fixture").unwrap();
        let advanced = store
            .apply(
                original.id,
                &original.stamp(),
                "advance",
                &Change::RenameProject {
                    title: "Advanced project".into(),
                },
            )
            .unwrap();
        let source = temp.path().join("source.bin");
        let contents: &[u8] = b"same captured bytes for both import attempts";
        fs::write(&source, contents).unwrap();
        let first = store.stage_file_import(&source).unwrap();
        let second = store.stage_file_import(&source).unwrap();
        let first_path = first.path().to_path_buf();
        let second_path = second.path().to_path_buf();
        let descriptor = first.descriptor().clone();
        assert_ne!(first_path, second_path);
        assert_eq!(second.descriptor(), &descriptor);
        assert_eq!(store.publish_file_import(&first).unwrap(), descriptor);
        assert_eq!(store.publish_file_import(&second).unwrap(), descriptor);
        let cas_path = store.verified_blob_path(&descriptor.sha256).unwrap();
        drop(first);
        let after_first_drop = store.read_blob(&descriptor.sha256, descriptor.size_bytes);
        drop(second);
        let stale_import = store.apply(
            original.id,
            &original.stamp(),
            "stale-import",
            &Change::AddAsset {
                asset: motionwright_domain::Asset {
                    id: Uuid::now_v7(),
                    name: "source.bin".into(),
                    media_type: "application/octet-stream".into(),
                    content_sha256: Some(descriptor.sha256.clone()),
                    source_revision: Some("local-import".into()),
                },
            },
        );
        assert!(
            matches!(stale_import, Err(StorageError::Conflict { expected, actual })
            if expected == original.revision && actual == advanced.project.revision)
        );
        assert_eq!(after_first_drop.unwrap(), contents);
        assert_eq!(
            store
                .read_blob(&descriptor.sha256, descriptor.size_bytes)
                .unwrap(),
            contents
        );
        assert_eq!(
            store.verified_blob_path(&descriptor.sha256).unwrap(),
            cas_path
        );
        assert!(!first_path.try_exists().unwrap());
        assert!(!second_path.try_exists().unwrap());
        assert!(
            fs::read_dir(first_path.parent().unwrap())
                .unwrap()
                .next()
                .is_none()
        );
        assert_eq!(store.load_project(original.id).unwrap(), advanced.project);
    }
}

#[cfg(test)]
mod source_open_tests {
    use super::{BEFORE_IMPORT_SOURCE_OPEN, ImportSourceOpenHook};
    use crate::{StorageError, Store};
    use rusqlite::types::Value;
    use sha2::{Digest, Sha256};
    use std::{fs, path::Path};

    struct HookGuard;
    impl Drop for HookGuard {
        fn drop(&mut self) {
            BEFORE_IMPORT_SOURCE_OPEN.with(|slot| {
                slot.borrow_mut().take();
            });
        }
    }
    fn before_open(hook: impl FnOnce(&Path) + 'static) -> HookGuard {
        BEFORE_IMPORT_SOURCE_OPEN.with(|slot| {
            let previous = slot
                .borrow_mut()
                .replace(Box::new(hook) as ImportSourceOpenHook);
            assert!(previous.is_none(), "one hook per test thread");
        });
        HookGuard
    }
    fn rows(store: &Store) -> Vec<Vec<Vec<Value>>> {
        [
            "SELECT * FROM projects ORDER BY id",
            "SELECT * FROM events ORDER BY project_id,revision",
            "SELECT * FROM receipts ORDER BY project_id,request_id",
        ]
        .into_iter()
        .map(|sql| {
            let mut statement = store.conn.prepare(sql).unwrap();
            let columns = statement.column_count();
            let values = statement
                .query_map([], |row| {
                    (0..columns)
                        .map(|column| row.get::<_, Value>(column))
                        .collect::<rusqlite::Result<Vec<_>>>()
                })
                .unwrap();
            values.map(|row| row.unwrap()).collect()
        })
        .collect()
    }
    fn store_at(root: &Path) -> Store {
        let mut store = Store::open(root.join("import.sqlite3")).unwrap();
        store
            .create_named_project("Source handle boundary")
            .unwrap();
        assert!(!store.blob_root.join(".staging").exists());
        store
    }

    #[test]
    fn regular_source_capture_preserves_exact_bytes_and_sql() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let source = tmp.path().join("regular.bin");
        let bytes = b"ordinary source bytes";
        fs::write(&source, bytes).unwrap();
        let before = rows(&store);
        let staged = store.stage_file_import_with_limit(&source, 32).unwrap();
        let staged_path = staged.path().to_path_buf();
        assert_eq!(fs::read(&staged_path).unwrap(), bytes);
        assert_eq!(staged.descriptor().size_bytes, bytes.len() as u64);
        assert_eq!(
            staged.descriptor().sha256,
            hex::encode(Sha256::digest(bytes))
        );
        assert_eq!(rows(&store), before);
        drop(staged);
        assert!(!staged_path.exists(), "private capture is removed on drop");
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }

    #[test]
    #[cfg_attr(
        windows,
        ignore = "Requires symbolic-link privilege; explicit Windows runner execution is required"
    )]
    fn replaced_symlink_source_is_rejected_before_staging() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let incoming = tmp.path().join("incoming");
        fs::create_dir(&incoming).unwrap();
        let source = incoming.join("source.bin");
        let sentinel = tmp.path().join("outside-sentinel.bin");
        fs::write(&source, b"admitted ordinary leaf").unwrap();
        fs::write(&sentinel, b"outside bytes must not be captured").unwrap();
        let outside_before = fs::read(&sentinel).unwrap();
        let target = sentinel.clone();
        let before = rows(&store);
        let _guard = before_open(move |leaf| {
            fs::remove_file(leaf).unwrap();
            #[cfg(unix)]
            std::os::unix::fs::symlink(&target, leaf).unwrap();
            #[cfg(windows)]
            std::os::windows::fs::symlink_file(&target, leaf).unwrap();
        });
        let result = store.stage_file_import(&source);
        assert!(
            result.is_err(),
            "replaced leaf must never capture its target"
        );
        assert!(!store.blob_root.join(".staging").exists());
        assert_eq!(rows(&store), before);
        assert_eq!(fs::read(&sentinel).unwrap(), outside_before);
    }

    #[test]
    fn oversized_opened_source_is_rejected_before_staging() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let source = tmp.path().join("growing.bin");
        fs::write(&source, b"small").unwrap();
        let before = rows(&store);
        let _guard = before_open(|leaf| fs::write(leaf, [b'x'; 64]).unwrap());
        let result = store.stage_file_import_with_limit(&source, 32);
        assert!(matches!(
            result,
            Err(StorageError::BlobTooLarge { limit: 32, .. })
        ));
        assert!(!store.blob_root.join(".staging").exists());
        assert_eq!(rows(&store), before);
        assert_eq!(fs::read(&source).unwrap(), [b'x'; 64]);
    }

    #[test]
    fn replaced_directory_source_is_rejected_before_staging() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let source = tmp.path().join("becomes-directory.bin");
        fs::write(&source, b"small").unwrap();
        let before = rows(&store);
        let _guard = before_open(|leaf| {
            fs::remove_file(leaf).unwrap();
            fs::create_dir(leaf).unwrap();
        });
        assert!(store.stage_file_import(&source).is_err());
        assert!(!store.blob_root.join(".staging").exists());
        assert!(source.is_dir());
        assert_eq!(rows(&store), before);
    }

    #[test]
    fn replay_source_hash_preserves_bytes_without_staging_or_database() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("replay.bin");
        let bytes = b"known replay input";
        fs::write(&source, bytes).unwrap();
        assert_eq!(
            Store::hash_file_import_source(&source).unwrap(),
            hex::encode(Sha256::digest(bytes))
        );
        assert_eq!(fs::read(&source).unwrap(), bytes);
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
    }

    #[test]
    #[cfg_attr(
        windows,
        ignore = "Requires symbolic-link privilege; explicit Windows runner execution is required"
    )]
    fn replay_source_hash_rejects_replaced_symlink_without_creating_outputs() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("replay.bin");
        let sentinel = tmp.path().join("outside-sentinel.bin");
        fs::write(&source, b"original").unwrap();
        fs::write(&sentinel, b"outside replay bytes").unwrap();
        let before = fs::read(&sentinel).unwrap();
        let target = sentinel.clone();
        let _guard = before_open(move |leaf| {
            fs::remove_file(leaf).unwrap();
            #[cfg(unix)]
            std::os::unix::fs::symlink(&target, leaf).unwrap();
            #[cfg(windows)]
            std::os::windows::fs::symlink_file(&target, leaf).unwrap();
        });
        assert!(Store::hash_file_import_source(&source).is_err());
        assert_eq!(fs::read(&sentinel).unwrap(), before);
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 2);
    }

    // This post-repair case must not be copied onto the plain-open baseline:
    // that implementation can block at open. Run in an externally bounded CI
    // process, never as an unbounded host reproduction.
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn replaced_fifo_source_is_rejected_without_waiting_for_a_writer() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let source = tmp.path().join("becomes-fifo.bin");
        fs::write(&source, b"ordinary leaf").unwrap();
        let before = rows(&store);
        let _guard = before_open(|leaf| {
            fs::remove_file(leaf).unwrap();
            rustix::fs::mkfifoat(
                rustix::fs::CWD,
                leaf,
                rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
            )
            .unwrap();
        });
        assert!(store.stage_file_import(&source).is_err());
        assert!(!store.blob_root.join(".staging").exists());
        assert_eq!(rows(&store), before);
    }
}

#[cfg(test)]
mod source_budget_tests {
    use super::{AFTER_FIRST_IMPORT_SOURCE_READ, ImportSourceReadHook};
    use crate::{StorageError, Store};
    use motionwright_domain::Change;
    use rusqlite::types::Value;
    use sha2::{Digest, Sha256};
    use std::{
        cell::Cell,
        collections::BTreeMap,
        fs::{self, OpenOptions},
        io::Write,
        path::{Path, PathBuf},
        rc::Rc,
    };

    struct ReadHookGuard;

    impl Drop for ReadHookGuard {
        fn drop(&mut self) {
            AFTER_FIRST_IMPORT_SOURCE_READ.with(|slot| {
                let _ = slot.borrow_mut().take();
            });
        }
    }

    fn after_first_read(hook: impl FnOnce(&Path) + 'static) -> ReadHookGuard {
        AFTER_FIRST_IMPORT_SOURCE_READ.with(|slot| {
            let mut slot = slot.borrow_mut();
            assert!(slot.is_none(), "one read hook per test thread");
            *slot = Some(Box::new(hook) as ImportSourceReadHook);
        });
        ReadHookGuard
    }

    fn append_after_first_read(
        initial: &'static [u8],
        suffix: &'static [u8],
    ) -> (ReadHookGuard, Rc<Cell<usize>>) {
        let calls = Rc::new(Cell::new(0_usize));
        let observed = Rc::clone(&calls);
        let guard = after_first_read(move |source| {
            assert_eq!(fs::read(source).unwrap().as_slice(), initial);
            observed.set(observed.get() + 1);
            // Extend this same regular file after the retained handle was read.
            let mut output = OpenOptions::new().append(true).open(source).unwrap();
            output.write_all(suffix).unwrap();
        });
        (guard, calls)
    }

    fn store_at(root: &Path) -> Store {
        let mut store = Store::open(root.join("budget.sqlite3")).unwrap();
        let project = store
            .create_named_project("Source budget sentinel")
            .unwrap();
        store
            .apply(
                project.id,
                &project.stamp(),
                "retained-receipt",
                &Change::RenameProject {
                    title: "Retained project head".into(),
                },
            )
            .unwrap();
        store
    }

    fn rows(store: &Store) -> Vec<Vec<Vec<Value>>> {
        [
            "SELECT * FROM projects ORDER BY id",
            "SELECT * FROM events ORDER BY event_id",
            "SELECT * FROM receipts ORDER BY project_id,request_id",
        ]
        .into_iter()
        .map(|query| {
            let mut statement = store.conn.prepare(query).unwrap();
            let columns = statement.column_count();
            let values: Vec<Vec<Value>> = statement
                .query_map([], |row| {
                    (0..columns)
                        .map(|column| row.get::<_, Value>(column))
                        .collect::<rusqlite::Result<Vec<Value>>>()
                })
                .unwrap()
                .map(|row| row.unwrap())
                .collect();
            values
        })
        .collect()
    }

    fn retained_blob_files(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
        fn walk(root: &Path, directory: &Path, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
            for entry in fs::read_dir(directory).unwrap() {
                let path = entry.unwrap().path();
                let metadata = fs::symlink_metadata(&path).unwrap();
                assert!(!metadata.file_type().is_symlink());
                if metadata.is_dir() {
                    walk(root, &path, files);
                } else {
                    assert!(metadata.is_file());
                    files.insert(
                        path.strip_prefix(root).unwrap().to_path_buf(),
                        fs::read(&path).unwrap(),
                    );
                }
            }
        }
        let mut files = BTreeMap::new();
        walk(root, root, &mut files);
        files
    }

    #[test]
    fn bounded_replay_hash_accepts_exact_budget_and_rejects_initial_excess() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("replay.bin");
        fs::write(&source, b"abcd").unwrap();
        assert_eq!(
            Store::hash_file_import_source_with_limit(&source, 4).unwrap(),
            hex::encode(Sha256::digest(b"abcd")),
        );
        assert_eq!(fs::read(&source).unwrap(), b"abcd");
        fs::write(&source, b"abcde").unwrap();
        let calls = Rc::new(Cell::new(0_usize));
        let observed = Rc::clone(&calls);
        let guard = after_first_read(move |_| observed.set(observed.get() + 1));
        let result = Store::hash_file_import_source_with_limit(&source, 4);
        assert!(
            matches!(
                &result,
                Err(StorageError::BlobTooLarge { size: 5, limit: 4 })
            ),
            "expected initial source-size rejection, got {result:?}",
        );
        assert_eq!(
            calls.get(),
            0,
            "initial excess must be rejected before reading"
        );
        assert_eq!(fs::read(&source).unwrap(), b"abcde");
        drop(guard);
        assert_eq!(
            Store::hash_file_import_source(&source).unwrap(),
            hex::encode(Sha256::digest(b"abcde")),
        );
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
    }

    #[test]
    fn bounded_replay_hash_rejects_growth_after_first_read() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("replay.bin");
        fs::write(&source, b"abcd").unwrap();
        let (_guard, calls) = append_after_first_read(b"abcd", b"e");
        let result = Store::hash_file_import_source_with_limit(&source, 4);
        assert!(
            matches!(
                &result,
                Err(StorageError::BlobTooLarge { size: 5, limit: 4 })
            ),
            "expected stream-growth rejection, got {result:?}",
        );
        assert_eq!(calls.get(), 1);
        assert_eq!(fs::read(&source).unwrap(), b"abcde");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
    }

    #[test]
    fn bounded_replay_hash_includes_growth_to_exact_budget() {
        let tmp = tempfile::tempdir().unwrap();
        let source = tmp.path().join("replay.bin");
        fs::write(&source, b"abc").unwrap();
        let (_guard, calls) = append_after_first_read(b"abc", b"d");
        let actual = Store::hash_file_import_source_with_limit(&source, 4).unwrap();
        assert_eq!(actual, hex::encode(Sha256::digest(b"abcd")));
        assert_ne!(actual, hex::encode(Sha256::digest(b"abc")));
        assert_eq!(calls.get(), 1);
        assert_eq!(fs::read(&source).unwrap(), b"abcd");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
    }

    #[test]
    fn staging_rejects_growth_without_touching_sql_cas_or_another_capture() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let kept_source = tmp.path().join("kept.bin");
        let kept_bytes = b"existing retained bytes";
        fs::write(&kept_source, kept_bytes).unwrap();
        let kept = store.ingest_blob_file(&kept_source).unwrap();
        let held = store.stage_file_import(&kept_source).unwrap();
        let held_path = held.path().to_path_buf();
        let cas_path = store.verified_blob_path(&kept.sha256).unwrap();
        let source = tmp.path().join("growing.bin");
        fs::write(&source, b"abcd").unwrap();
        let before_rows = rows(&store);
        let before_files = retained_blob_files(&store.blob_root);
        let (_guard, calls) = append_after_first_read(b"abcd", b"e");
        let result = store.stage_file_import_with_limit(&source, 4);
        assert!(
            matches!(
                &result,
                Err(StorageError::BlobTooLarge { size: 5, limit: 4 })
            ),
            "expected stream-growth rejection, got {result:?}",
        );
        assert_eq!(calls.get(), 1);
        assert_eq!(rows(&store), before_rows);
        assert_eq!(retained_blob_files(&store.blob_root), before_files);
        assert_eq!(fs::read(&source).unwrap(), b"abcde");
        assert!(held_path.is_file(), "another capture must remain live");
        assert_eq!(fs::read(&held_path).unwrap(), kept_bytes);
        assert_eq!(fs::read(&cas_path).unwrap(), kept_bytes);
        assert_eq!(
            store.read_blob(&kept.sha256, kept.size_bytes).unwrap(),
            kept_bytes
        );
        drop(held);
        assert!(!held_path.exists());
        assert_eq!(fs::read(&cas_path).unwrap(), kept_bytes);
    }

    #[test]
    fn staging_captures_growth_to_exact_budget_then_removes_its_private_file() {
        let tmp = tempfile::tempdir().unwrap();
        let store = store_at(tmp.path());
        let source = tmp.path().join("growing.bin");
        fs::write(&source, b"abc").unwrap();
        let before_rows = rows(&store);
        let before_files = retained_blob_files(&store.blob_root);
        let (_guard, calls) = append_after_first_read(b"abc", b"d");
        let staged = store.stage_file_import_with_limit(&source, 4).unwrap();
        let staged_path = staged.path().to_path_buf();
        assert_eq!(calls.get(), 1);
        assert_eq!(staged.descriptor().size_bytes, 4);
        assert_eq!(
            staged.descriptor().sha256,
            hex::encode(Sha256::digest(b"abcd"))
        );
        assert_eq!(fs::read(&staged_path).unwrap(), b"abcd");
        assert_eq!(fs::read(&source).unwrap(), b"abcd");
        assert_eq!(rows(&store), before_rows);
        let mut expected_files = before_files.clone();
        assert!(
            expected_files
                .insert(
                    staged_path
                        .strip_prefix(&store.blob_root)
                        .unwrap()
                        .to_path_buf(),
                    b"abcd".to_vec(),
                )
                .is_none()
        );
        assert_eq!(retained_blob_files(&store.blob_root), expected_files);
        drop(staged);
        assert!(!staged_path.exists());
        assert_eq!(retained_blob_files(&store.blob_root), before_files);
        assert_eq!(rows(&store), before_rows);
    }
}
