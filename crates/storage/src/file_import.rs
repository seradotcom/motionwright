//! Durable file-import receipt inspection and validated CAS capture.
use super::{
    ApplyOutcome, Change, OptionalExtension, Project, Result, RevisionStamp, StorageError, Store,
    TransactionBehavior, params, request_digest, validate_digest,
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
        let current_json: String = tx
            .query_row(
                "SELECT document_json FROM projects WHERE id=?1",
                params![id.to_string()],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(StorageError::NotFound)?;
        let current: Project = serde_json::from_str(&current_json)
            .map_err(|_| invalid_receipt("invalid current project JSON"))?;
        current
            .validate()
            .map_err(|_| invalid_receipt("invalid current project document"))?;
        if current.id != id
            || current.resource_key() != expected.resource
            || current.generation != expected.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        let receipt: Option<(String, String)> = tx.query_row(
            "SELECT request_sha256,result_json FROM receipts WHERE project_id=?1 AND request_id=?2",
            params![id.to_string(), key], |row| Ok((row.get(0)?, row.get(1)?)),
        ).optional()?;
        let Some((stored_digest, result_json)) = receipt else {
            if require_current_if_absent && expected.revision != current.revision {
                return Err(StorageError::Conflict {
                    expected: expected.revision,
                    actual: current.revision,
                });
            }
            tx.commit()?;
            return Ok(None);
        };
        if super::project_creation::is_creation_receipt(&result_json) {
            return Err(StorageError::RequestReuse);
        }
        let mut outcome: ApplyOutcome = serde_json::from_str(&result_json)
            .map_err(|_| invalid_receipt("invalid receipt outcome JSON"))?;
        outcome
            .project
            .validate()
            .map_err(|_| invalid_receipt("invalid receipt outcome project"))?;
        if outcome.request_id != key
            || outcome.project.id != id
            || outcome.project.generation != current.generation
            || outcome.project.resource_key() != expected.resource
            || outcome.replayed
            || outcome.previous_revision.checked_add(1) != Some(outcome.project.revision)
            || outcome.project.revision > current.revision
        {
            return Err(invalid_receipt(
                "inconsistent receipt identity, generation, revision or replay state",
            ));
        }
        let event_revision = i64::try_from(outcome.project.revision)
            .map_err(|_| invalid_receipt("receipt revision exceeds SQLite integer range"))?;
        let (count, event_json): (i64, Option<String>) = tx.query_row(
            "SELECT COUNT(*),MIN(payload_json) FROM events WHERE project_id=?1 AND revision=?2",
            params![id.to_string(), event_revision],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        if count != 1 {
            return Err(invalid_receipt("receipt must reference exactly one event"));
        }
        let event_json =
            event_json.ok_or_else(|| invalid_receipt("receipt event has no payload"))?;
        let change: Change = serde_json::from_str(&event_json)
            .map_err(|_| invalid_receipt("invalid receipt event JSON"))?;
        let event_digest = request_digest(id, &change)
            .map_err(|_| invalid_receipt("receipt event cannot be hashed"))?;
        if event_digest != stored_digest {
            return Err(invalid_receipt(
                "receipt request digest differs from its recorded event",
            ));
        }
        let source_sha256 = verify_import_binding(&change, &outcome, intent)?;
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
    hash_file,
};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{ErrorKind, Read, Write},
    path::{Path, PathBuf},
};

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
        let size = ensure_regular_file(source, "import source")?.len();
        if size > max_bytes {
            return Err(StorageError::BlobTooLarge {
                size,
                limit: max_bytes,
            });
        }
        let staging_root = self.blob_root.join(".staging");
        create_checked_blob_directory(&staging_root)?;
        let input = File::open(source)?;
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
            let mut buffer = [0_u8; 128 * 1024];
            let mut size_bytes = 0_u64;
            loop {
                let count = input.read(&mut buffer)?;
                if count == 0 {
                    break;
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
        if staged.blob_root != self.blob_root {
            return Err(StorageError::UnsafeSourcePath(
                "staged import belongs to another blob root".into(),
            ));
        }
        ensure_regular_directory(&self.blob_root, "blob root")?;
        let staging_root = self.blob_root.join(".staging");
        ensure_regular_directory(&staging_root, "blob staging directory")?;
        if staged.path.parent() != Some(staging_root.as_path()) {
            return Err(StorageError::UnsafeSourcePath(
                "staged import is outside the blob staging directory".into(),
            ));
        }
        let captured = hash_file(&staged.path)?;
        if captured != staged.descriptor {
            return Err(StorageError::InvalidBlobDigest);
        }
        let destination = self.blob_path(&captured.sha256)?;
        let sha_root = self.blob_root.join("sha256");
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
            let existing = self.checked_existing_blob_path(&captured.sha256)?;
            if hash_file(&existing)? != captured {
                return Err(StorageError::InvalidBlobDigest);
            }
        }
        Ok(captured)
    }
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
