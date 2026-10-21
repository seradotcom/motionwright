use super::{
    Result, StorageError, Store, create_checked_blob_directory, ensure_regular_directory,
    ensure_regular_file, validate_digest,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

pub const DERIVED_CACHE_FORMAT_VERSION: u32 = 1;
const MAX_DERIVED_CACHE_MANIFEST_BYTES: u64 = 64 * 1024;
const MAX_CACHE_KIND_BYTES: usize = 96;
const MAX_CACHE_SCOPE_BYTES: usize = 4096;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DerivedCacheRecord {
    pub format_version: u32,
    pub scope_sha256: String,
    pub kind: String,
    pub fingerprint_sha256: String,
    pub blob_sha256: String,
    pub size_bytes: u64,
    pub work_units: u64,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DerivedCacheHit {
    pub record: DerivedCacheRecord,
    pub blob_path: PathBuf,
    pub validated_bytes: u64,
}

fn invalid(message: impl Into<String>) -> StorageError {
    StorageError::InvalidDerivedCache(message.into())
}

fn digest_text(value: &str) -> String {
    hex::encode(Sha256::digest(value.as_bytes()))
}

fn validate_scope(scope: &str) -> Result<()> {
    if scope.is_empty() || scope.len() > MAX_CACHE_SCOPE_BYTES || scope.contains('\0') {
        return Err(invalid("cache scope is empty, too large, or contains NUL"));
    }
    Ok(())
}

fn validate_kind(kind: &str) -> Result<()> {
    if kind.is_empty()
        || kind.len() > MAX_CACHE_KIND_BYTES
        || !kind.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'-' | b'_' | b'.')
        })
    {
        return Err(invalid(
            "cache kind must be bounded lowercase ASCII using [a-z0-9._-]",
        ));
    }
    Ok(())
}

fn cache_key(scope_sha256: &str, kind: &str, fingerprint_sha256: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(scope_sha256.as_bytes());
    hasher.update([0]);
    hasher.update(kind.as_bytes());
    hasher.update([0]);
    hasher.update(fingerprint_sha256.as_bytes());
    hex::encode(hasher.finalize())
}

impl Store {
    fn derived_cache_root(&self) -> PathBuf {
        self.blob_root
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("derived-cache")
    }

    /// No derived-cache operation may traverse an application-owned symlink
    /// at the cache root, shard or owner directory. Lookup stays read-only.
    fn checked_derived_cache_dirs(&self, scope_sha256: &str, create: bool) -> Result<bool> {
        validate_digest(scope_sha256)?;
        let root = self.derived_cache_root();
        let parent = root
            .parent()
            .ok_or_else(|| invalid("cache root has no parent"))?;
        ensure_regular_directory(parent, "derived cache trusted parent")?;
        let dirs = [
            root.clone(),
            root.join("sha256"),
            root.join("sha256").join(&scope_sha256[..2]),
            root.join("sha256")
                .join(&scope_sha256[..2])
                .join(scope_sha256),
        ];
        for directory in dirs {
            if create {
                create_checked_blob_directory(&directory)?;
            } else {
                match ensure_regular_directory(&directory, "derived cache directory") {
                    Ok(_) => {}
                    Err(StorageError::Io(error))
                        if error.kind() == std::io::ErrorKind::NotFound =>
                    {
                        return Ok(false);
                    }
                    Err(error) => return Err(error),
                }
            }
        }
        Ok(true)
    }

    fn derived_cache_manifest_path(
        &self,
        scope_sha256: &str,
        kind: &str,
        fingerprint_sha256: &str,
    ) -> Result<PathBuf> {
        validate_digest(scope_sha256)?;
        validate_digest(fingerprint_sha256)?;
        validate_kind(kind)?;
        let key = cache_key(scope_sha256, kind, fingerprint_sha256);
        Ok(self
            .derived_cache_root()
            .join("sha256")
            .join(&scope_sha256[..2])
            .join(scope_sha256)
            .join(format!("{key}.json")))
    }

    pub fn put_derived_cache_file(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
        source: impl AsRef<Path>,
        work_units: u64,
    ) -> Result<DerivedCacheRecord> {
        validate_scope(scope)?;
        validate_kind(kind)?;
        validate_digest(fingerprint_sha256)?;
        let scope_sha256 = digest_text(scope);
        let destination =
            self.derived_cache_manifest_path(&scope_sha256, kind, fingerprint_sha256)?;
        self.checked_derived_cache_dirs(&scope_sha256, true)?;
        let retained = self.read_derived_cache_record(&scope_sha256, kind, fingerprint_sha256)?;
        // Capture once, but do not publish rejected bytes into shared CAS.
        let staged = self.stage_file_import(source.as_ref())?;
        if let Some(existing) = &retained
            && (existing.blob_sha256 != staged.descriptor().sha256
                || existing.size_bytes != staged.descriptor().size_bytes)
        {
            return Err(invalid(
                "same scope/kind/fingerprint produced different verified bytes",
            ));
        }
        let blob = self.publish_file_import(&staged)?;
        // An evicted blob does not revoke the retained manifest's identity.
        // Exact repair preserves the first publisher's timestamp and work.
        let record = retained.unwrap_or_else(|| DerivedCacheRecord {
            format_version: DERIVED_CACHE_FORMAT_VERSION,
            scope_sha256,
            kind: kind.to_owned(),
            fingerprint_sha256: fingerprint_sha256.to_owned(),
            blob_sha256: blob.sha256,
            size_bytes: blob.size_bytes,
            work_units,
            created_at: Utc::now().to_rfc3339(),
        });
        let encoded = serde_json::to_vec_pretty(&record)?;
        if encoded.len() as u64 > MAX_DERIVED_CACHE_MANIFEST_BYTES {
            return Err(invalid("cache manifest exceeds bounded manifest size"));
        }
        let parent = destination
            .parent()
            .ok_or_else(|| invalid("cache manifest has no parent directory"))?;
        // No create_dir_all: that silently follows an existing symlink.
        self.checked_derived_cache_dirs(&record.scope_sha256, true)?;
        let staging = parent.join(format!(".{}.part", Uuid::now_v7()));
        let output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)?;
        let written = {
            // Close the handle before removing our staging name on failure.
            let mut output = output;
            output
                .write_all(&encoded)
                .and_then(|()| output.flush())
                .and_then(|()| output.sync_all())
        };
        if let Err(error) = written {
            let _ = fs::remove_file(&staging);
            return Err(error.into());
        }
        self.publish_new_derived_cache_manifest(&staging, &destination, record)
    }

    fn publish_new_derived_cache_manifest(
        &self,
        staging: &Path,
        destination: &Path,
        record: DerivedCacheRecord,
    ) -> Result<DerivedCacheRecord> {
        let result = (|| match fs::hard_link(staging, destination) {
            Ok(()) => Ok(record),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                let winner = self
                    .read_derived_cache_record(
                        &record.scope_sha256,
                        &record.kind,
                        &record.fingerprint_sha256,
                    )?
                    .ok_or_else(|| invalid("cache publication changed before validation"))?;
                if winner.blob_sha256 != record.blob_sha256
                    || winner.size_bytes != record.size_bytes
                {
                    return Err(invalid(
                        "same scope/kind/fingerprint produced different verified bytes",
                    ));
                }
                let verified = self.verify_blob(&winner.blob_sha256)?;
                if verified.size_bytes != winner.size_bytes {
                    return Err(invalid(
                        "cache manifest byte length differs from verified blob length",
                    ));
                }
                Ok(winner)
            }
            Err(error) => Err(error.into()),
        })();
        // This name was created exclusively by this publication attempt.
        // Never remove the destination or a potentially shared CAS object.
        let _ = fs::remove_file(staging);
        result
    }

    fn read_derived_cache_record(
        &self,
        scope_sha256: &str,
        kind: &str,
        fingerprint_sha256: &str,
    ) -> Result<Option<DerivedCacheRecord>> {
        let manifest = self.derived_cache_manifest_path(scope_sha256, kind, fingerprint_sha256)?;
        if !self.checked_derived_cache_dirs(scope_sha256, false)? {
            return Ok(None);
        }
        let size = match fs::symlink_metadata(&manifest) {
            Ok(_) => ensure_regular_file(&manifest, "derived cache manifest")?.len(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        if size > MAX_DERIVED_CACHE_MANIFEST_BYTES {
            return Err(StorageError::BlobTooLarge {
                size,
                limit: MAX_DERIVED_CACHE_MANIFEST_BYTES,
            });
        }
        let record: DerivedCacheRecord = serde_json::from_slice(&fs::read(&manifest)?)?;
        if record.format_version != DERIVED_CACHE_FORMAT_VERSION
            || record.scope_sha256 != scope_sha256
            || record.kind != kind
            || record.fingerprint_sha256 != fingerprint_sha256
        {
            return Err(invalid(
                "cache manifest identity does not match requested scope/kind/fingerprint",
            ));
        }
        validate_digest(&record.blob_sha256)?;
        Ok(Some(record))
    }

    pub fn lookup_derived_cache(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
    ) -> Result<Option<DerivedCacheHit>> {
        validate_scope(scope)?;
        validate_kind(kind)?;
        validate_digest(fingerprint_sha256)?;
        let scope_sha256 = digest_text(scope);
        let record =
            match self.read_derived_cache_record(&scope_sha256, kind, fingerprint_sha256)? {
                Some(record) => record,
                None => return Ok(None),
            };
        let descriptor = match self.verify_blob(&record.blob_sha256) {
            Ok(value) => value,
            Err(StorageError::BlobMissing { .. }) => return Ok(None),
            Err(error) => return Err(error),
        };
        if descriptor.size_bytes != record.size_bytes {
            return Err(invalid(
                "cache manifest byte length differs from verified blob length",
            ));
        }
        let blob_path = self.verified_blob_path(&record.blob_sha256)?;
        Ok(Some(DerivedCacheHit {
            record,
            blob_path,
            validated_bytes: descriptor.size_bytes,
        }))
    }
}

impl Store {
    /// Evict exactly one verified *derived manifest*, never its CAS blob.
    /// A corrupt or symlinked entry is an error instead of a silent purge.
    pub fn evict_derived_cache_exact(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
    ) -> Result<bool> {
        validate_scope(scope)?;
        validate_kind(kind)?;
        validate_digest(fingerprint_sha256)?;
        let digest = digest_text(scope);
        let location = self.derived_cache_manifest_path(&digest, kind, fingerprint_sha256)?;
        if !self.checked_derived_cache_dirs(&digest, false)? {
            return Ok(false);
        }
        match fs::symlink_metadata(&location) {
            Ok(_) => {
                // Deleting a derived manifest cannot grant access to bytes:
                // a corrupt manifest is still safe to delete, while symlinks
                // and special files must fail closed.
                ensure_regular_file(&location, "derived cache eviction manifest")?;
                fs::remove_file(location)?;
                Ok(true)
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error.into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn digest(label: &str) -> String {
        digest_text(label)
    }

    #[test]
    fn cache_hit_requires_exact_scope_kind_fingerprint_and_verified_bytes() {
        let temp = tempdir().unwrap();
        let store = Store::open(temp.path().join("studio.sqlite3")).unwrap();
        let source = temp.path().join("preview.bin");
        fs::write(&source, b"derived-preview-v1").unwrap();
        let fingerprint = digest("project-generation-profile-and-renderer");

        let record = store
            .put_derived_cache_file(
                "project:018f:generation:0190",
                "preview-index",
                &fingerprint,
                &source,
                37,
            )
            .unwrap();
        let hit = store
            .lookup_derived_cache(
                "project:018f:generation:0190",
                "preview-index",
                &fingerprint,
            )
            .unwrap()
            .unwrap();

        assert_eq!(hit.record, record);
        assert_eq!(hit.record.work_units, 37);
        assert_eq!(hit.validated_bytes, b"derived-preview-v1".len() as u64);
        assert_eq!(fs::read(hit.blob_path).unwrap(), b"derived-preview-v1");

        assert!(
            store
                .lookup_derived_cache(
                    "project:other:generation:0190",
                    "preview-index",
                    &fingerprint,
                )
                .unwrap()
                .is_none()
        );
        assert!(
            store
                .lookup_derived_cache(
                    "project:018f:generation:0190",
                    "caption-sidecar",
                    &fingerprint,
                )
                .unwrap()
                .is_none()
        );
        assert!(
            store
                .lookup_derived_cache(
                    "project:018f:generation:0190",
                    "preview-index",
                    &digest("changed-font-or-renderer"),
                )
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn same_fingerprint_cannot_silently_alias_different_output() {
        let temp = tempdir().unwrap();
        let store = Store::open(temp.path().join("studio.sqlite3")).unwrap();
        let first = temp.path().join("first.bin");
        let second = temp.path().join("second.bin");
        fs::write(&first, b"first-output").unwrap();
        fs::write(&second, b"different-output").unwrap();
        let fingerprint = digest("same-input-fingerprint");

        store
            .put_derived_cache_file("project:a", "master", &fingerprint, &first, 1)
            .unwrap();
        let error = store
            .put_derived_cache_file("project:a", "master", &fingerprint, &second, 1)
            .unwrap_err();

        assert!(matches!(error, StorageError::InvalidDerivedCache(_)));
        assert!(error.to_string().contains("different verified bytes"));
    }

    #[test]
    fn malformed_kind_and_fingerprint_fail_closed() {
        let temp = tempdir().unwrap();
        let store = Store::open(temp.path().join("studio.sqlite3")).unwrap();

        assert!(matches!(
            store.lookup_derived_cache("scope", "../master", &digest("ok")),
            Err(StorageError::InvalidDerivedCache(_))
        ));
        assert!(matches!(
            store.lookup_derived_cache("scope", "master", "not-a-digest"),
            Err(StorageError::InvalidBlobDigest)
        ));
    }
}

#[cfg(all(test, unix))]
mod cache_symlink_tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use tempfile::tempdir;

    #[test]
    fn cache_shard_symlink_cannot_exfiltrate_manifest_or_become_a_hit() {
        let dir = tempdir().unwrap();
        let store = Store::open(dir.path().join("project.sqlite3")).unwrap();
        let scope = "project:owner";
        let fingerprint = digest_text("source-recipe");
        let source = dir.path().join("derived.bin");
        fs::write(&source, b"derived bytes").unwrap();
        let outside = dir.path().join("foreign");
        fs::create_dir(&outside).unwrap();
        let root = dir.path().join("derived-cache");
        fs::create_dir(&root).unwrap();
        symlink(&outside, root.join("sha256")).unwrap();
        let put =
            store.put_derived_cache_file(scope, "render-window-proxy", &fingerprint, &source, 30);
        assert!(matches!(put, Err(StorageError::UnsafeSourcePath(_))));
        assert!(matches!(
            store.lookup_derived_cache(scope, "render-window-proxy", &fingerprint),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(matches!(
            store.evict_derived_cache_exact(scope, "render-window-proxy", &fingerprint),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert_eq!(fs::read_dir(&outside).unwrap().count(), 0);
    }

    #[test]
    fn dangling_manifest_symlink_is_never_silent_miss_or_repaired_through_link() {
        let dir = tempdir().unwrap();
        let store = Store::open(dir.path().join("project.sqlite3")).unwrap();
        let scope = "project:owner";
        let fingerprint = digest_text("manifest-recipe");
        let source = dir.path().join("source.bin");
        fs::write(&source, b"source bytes").unwrap();
        store
            .put_derived_cache_file(scope, "render-window-proxy", &fingerprint, &source, 1)
            .unwrap();
        let manifest = store
            .derived_cache_manifest_path(&digest_text(scope), "render-window-proxy", &fingerprint)
            .unwrap();
        fs::remove_file(&manifest).unwrap();
        let foreign = dir.path().join("not-created");
        symlink(&foreign, &manifest).unwrap();
        assert!(matches!(
            store.lookup_derived_cache(scope, "render-window-proxy", &fingerprint),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(matches!(
            store.put_derived_cache_file(scope, "render-window-proxy", &fingerprint, &source, 1),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(matches!(
            store.evict_derived_cache_exact(scope, "render-window-proxy", &fingerprint),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(!foreign.exists());
    }
}

#[cfg(test)]
mod cache_missing_path_tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn read_only_miss_does_not_create_derived_cache_directories() {
        let dir = tempdir().unwrap();
        let store = Store::open(dir.path().join("project.sqlite3")).unwrap();
        assert!(
            store
                .lookup_derived_cache(
                    "project:missing",
                    "render-window-proxy",
                    &digest_text("missing")
                )
                .unwrap()
                .is_none()
        );
        assert!(!dir.path().join("derived-cache").exists());
    }
}

#[cfg(test)]
mod publication_interleaving_tests {
    use super::*;
    use tempfile::tempdir;

    const SCOPE: &str = "project:publication:generation:one";
    const KIND: &str = "render-window-proxy";

    fn prepare_candidate(
        store: &Store,
        root: &Path,
        bytes: &[u8],
    ) -> (PathBuf, PathBuf, DerivedCacheRecord) {
        let source = root.join("candidate.bin");
        fs::write(&source, bytes).unwrap();
        let blob = store.ingest_blob_file(&source).unwrap();
        let scope_sha256 = digest_text(SCOPE);
        let fingerprint_sha256 = digest_text("same-admitted-input");
        store
            .checked_derived_cache_dirs(&scope_sha256, true)
            .unwrap();
        let destination = store
            .derived_cache_manifest_path(&scope_sha256, KIND, &fingerprint_sha256)
            .unwrap();
        let record = DerivedCacheRecord {
            format_version: DERIVED_CACHE_FORMAT_VERSION,
            scope_sha256,
            kind: KIND.into(),
            fingerprint_sha256,
            blob_sha256: blob.sha256,
            size_bytes: blob.size_bytes,
            work_units: 999,
            created_at: "2026-01-01T00:00:00+00:00".into(),
        };
        let staging = destination
            .parent()
            .unwrap()
            .join(".prepared-candidate.part");
        fs::write(&staging, serde_json::to_vec_pretty(&record).unwrap()).unwrap();
        assert!(
            !destination.exists(),
            "candidate observed an absent publication"
        );
        (staging, destination, record)
    }

    #[test]
    fn late_publication_cannot_replace_a_different_winner() {
        let dir = tempdir().unwrap();
        let store = Store::open(dir.path().join("project.sqlite3")).unwrap();
        let (staging, destination, candidate) =
            prepare_candidate(&store, dir.path(), b"candidate bytes");
        let source = dir.path().join("winner.bin");
        fs::write(&source, b"winner bytes").unwrap();
        let winner = store
            .put_derived_cache_file(SCOPE, KIND, &candidate.fingerprint_sha256, &source, 7)
            .unwrap();
        let before = fs::read(&destination).unwrap();
        let result = store.publish_new_derived_cache_manifest(&staging, &destination, candidate);
        let after = fs::read(&destination).unwrap();
        assert!(
            after == before,
            "late publication replaced the real Store winner"
        );
        assert!(result.is_err(), "different winner must conflict");
        assert!(!staging.exists(), "only the contender's staging is cleaned");
        assert_eq!(
            store
                .lookup_derived_cache(SCOPE, KIND, &digest_text("same-admitted-input"))
                .unwrap()
                .unwrap()
                .record,
            winner
        );
        assert_eq!(fs::read(source).unwrap(), b"winner bytes");
    }

    #[test]
    fn equivalent_late_publication_preserves_the_winner_record() {
        let dir = tempdir().unwrap();
        let store = Store::open(dir.path().join("project.sqlite3")).unwrap();
        let (staging, destination, candidate) =
            prepare_candidate(&store, dir.path(), b"identical derived bytes");
        let source = dir.path().join("winner.bin");
        fs::write(&source, b"identical derived bytes").unwrap();
        let winner = store
            .put_derived_cache_file(SCOPE, KIND, &candidate.fingerprint_sha256, &source, 7)
            .unwrap();
        assert_ne!(winner.created_at, candidate.created_at);
        assert_ne!(winner.work_units, candidate.work_units);
        let before = fs::read(&destination).unwrap();
        let actual = store
            .publish_new_derived_cache_manifest(&staging, &destination, candidate)
            .unwrap();
        assert!(
            fs::read(&destination).unwrap() == before,
            "equivalent contender must not rewrite winner metadata"
        );
        assert_eq!(actual, winner);
        assert!(!staging.exists());
    }

    #[test]
    fn late_publication_preserves_an_unowned_regular_destination() {
        let dir = tempdir().unwrap();
        let store = Store::open(dir.path().join("project.sqlite3")).unwrap();
        let (staging, destination, candidate) =
            prepare_candidate(&store, dir.path(), b"candidate bytes");
        let foreign = b"unrelated regular file: do not overwrite";
        fs::write(&destination, foreign).unwrap();
        let result = store.publish_new_derived_cache_manifest(&staging, &destination, candidate);
        assert_eq!(fs::read(&destination).unwrap(), foreign);
        assert!(result.is_err());
        assert!(!staging.exists());
    }
}
