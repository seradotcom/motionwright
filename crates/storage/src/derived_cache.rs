use super::{
    BlobDescriptor, Result, StorageError, Store, create_checked_blob_directory,
    ensure_regular_directory, ensure_regular_file, validate_digest,
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

    /// All paths retain the legacy root/shard checks. Managed roots additionally
    /// use their immutable root UUID, so sibling CAS roots cannot share a key.
    fn checked_derived_cache_dirs(&self, scope_sha256: &str, create: bool) -> Result<bool> {
        self.checked_derived_cache_dirs_at(scope_sha256, create, true)
    }

    fn derived_cache_data_root(&self, managed: bool) -> Result<PathBuf> {
        if managed
            && let Some(root) = super::cas_retention::derived::manifest_root(&self.blob_root)?
        {
            return Ok(root);
        }
        Ok(self.derived_cache_root().join("sha256"))
    }

    fn checked_derived_cache_dirs_at(
        &self,
        scope_sha256: &str,
        create: bool,
        managed: bool,
    ) -> Result<bool> {
        validate_digest(scope_sha256)?;
        let root = self.derived_cache_root();
        let parent = root
            .parent()
            .ok_or_else(|| invalid("cache root has no parent"))?;
        ensure_regular_directory(parent, "derived cache trusted parent")?;
        let data = self.derived_cache_data_root(managed)?;
        let mut dirs = vec![root.clone(), root.join("sha256")];
        if data != root.join("sha256") {
            dirs.push(data.clone());
        }
        dirs.push(data.join(&scope_sha256[..2]));
        dirs.push(data.join(&scope_sha256[..2]).join(scope_sha256));
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

    pub(super) fn derived_cache_manifest_path(
        &self,
        scope_sha256: &str,
        kind: &str,
        fingerprint_sha256: &str,
    ) -> Result<PathBuf> {
        self.derived_cache_manifest_path_at(scope_sha256, kind, fingerprint_sha256, true)
    }

    fn derived_cache_manifest_path_at(
        &self,
        scope_sha256: &str,
        kind: &str,
        fingerprint_sha256: &str,
        managed: bool,
    ) -> Result<PathBuf> {
        validate_digest(scope_sha256)?;
        validate_digest(fingerprint_sha256)?;
        validate_kind(kind)?;
        let key = cache_key(scope_sha256, kind, fingerprint_sha256);
        Ok(self
            .derived_cache_data_root(managed)?
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
        self.put_derived_cache_file_with_admission(
            scope,
            kind,
            fingerprint_sha256,
            source,
            work_units,
            None,
        )
    }

    pub(crate) fn put_derived_cache_file_with_admission(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
        source: impl AsRef<Path>,
        work_units: u64,
        admission: Option<&super::cas_retention::derived::proxy::Admission>,
    ) -> Result<DerivedCacheRecord> {
        self.put_derived_cache_file_with_source_admission(
            scope,
            kind,
            fingerprint_sha256,
            (source, None),
            work_units,
            admission,
        )
    }

    pub(crate) fn put_derived_cache_file_with_source_admission<S: AsRef<Path>>(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
        source: (S, Option<(&BlobDescriptor, u64)>),
        work_units: u64,
        admission: Option<&super::cas_retention::derived::proxy::Admission>,
    ) -> Result<DerivedCacheRecord> {
        let (source, expected_source) = source;
        validate_scope(scope)?;
        validate_kind(kind)?;
        validate_digest(fingerprint_sha256)?;
        if let Some((expected, max_bytes)) = expected_source {
            validate_digest(&expected.sha256)?;
            if expected.size_bytes > max_bytes {
                return Err(StorageError::BlobTooLarge {
                    size: expected.size_bytes,
                    limit: max_bytes,
                });
            }
        }
        let managed = super::cas_retention::derived::manifest_root(&self.blob_root)?.is_some();
        let scope_sha256 = digest_text(scope);
        let destination =
            self.derived_cache_manifest_path(&scope_sha256, kind, fingerprint_sha256)?;
        self.checked_derived_cache_dirs(&scope_sha256, true)?;
        let retained = self.read_derived_cache_record(&scope_sha256, kind, fingerprint_sha256)?;
        // Capture once, but do not publish rejected bytes into shared CAS.
        let source = source.as_ref();
        let staged = match (managed, expected_source) {
            (true, Some((_, max_bytes))) => {
                self.stage_derived_file_import_with_limit(source, max_bytes)?
            }
            (false, Some((_, max_bytes))) => {
                self.stage_file_import_with_limit(source, max_bytes)?
            }
            (true, None) => self.stage_derived_file_import(source)?,
            (false, None) => self.stage_file_import(source)?,
        };
        // Compare the descriptor from this same bounded capture before CAS,
        // manifest, proxy binding, index admission, or unrelated maintenance.
        if let Some((expected, _)) = expected_source
            && staged.descriptor() != expected
        {
            return Err(invalid(
                "captured cache source differs from its expected SHA-256 or byte count",
            ));
        }
        if let Some(existing) = &retained
            && (existing.blob_sha256 != staged.descriptor().sha256
                || existing.size_bytes != staged.descriptor().size_bytes)
        {
            return Err(invalid(
                "same scope/kind/fingerprint produced different verified bytes",
            ));
        }
        let blob = if managed {
            staged.descriptor().clone()
        } else {
            self.publish_file_import(&staged)?
        };
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
        if managed {
            let record = super::cas_retention::derived::publish(
                self,
                &staged,
                &destination,
                record,
                admission,
            )?;
            drop(staged);
            // Rejected input must not trigger unrelated cleanup or change
            // retained cache metadata. Admission succeeds before maintenance.
            if admission.is_none() {
                let _ = super::cas_retention::derived::maintain(
                    &self.blob_root,
                    super::CasRetentionPolicy::default(),
                );
            }
            return Ok(record);
        }
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
        let mut previous = None;
        for managed in [true, false] {
            let manifest = self.derived_cache_manifest_path_at(
                scope_sha256,
                kind,
                fingerprint_sha256,
                managed,
            )?;
            if previous.as_ref() == Some(&manifest) {
                continue;
            }
            previous = Some(manifest.clone());
            if !self.checked_derived_cache_dirs_at(scope_sha256, false, managed)? {
                continue;
            }
            match fs::symlink_metadata(&manifest) {
                Ok(_) => {
                    ensure_regular_file(&manifest, "derived cache manifest")?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => return Err(error.into()),
            }
            if let Some(record) = super::cas_retention::derived::read_record(
                &manifest,
                scope_sha256,
                kind,
                fingerprint_sha256,
            )? {
                return Ok(Some(record));
            }
        }
        Ok(None)
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
    /// Revoke the exact manifest and journal any owned derived reference.
    /// Physical CAS deletion remains a separate bounded retention operation.
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
        let proxy_guard =
            super::cas_retention::derived::proxy::explicit_eviction_guard(&self.blob_root)?;
        let mut previous = None;
        let mut removed = false;
        // Remove the fallback key as well, so evicting a managed key cannot
        // accidentally reveal a retained legacy manifest for the same key.
        for managed in [true, false] {
            let location =
                self.derived_cache_manifest_path_at(&digest, kind, fingerprint_sha256, managed)?;
            if previous.as_ref() == Some(&location) {
                continue;
            }
            previous = Some(location.clone());
            let dirs_exist = self.checked_derived_cache_dirs_at(&digest, false, managed)?;
            if dirs_exist {
                match fs::symlink_metadata(&location) {
                    Ok(_) => {
                        ensure_regular_file(&location, "derived cache eviction manifest")?;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                    Err(error) => return Err(error.into()),
                }
            }
            if managed
                && let Some(result) = super::cas_retention::derived::evict(
                    &self.blob_root,
                    &location,
                    proxy_guard.as_ref(),
                )?
            {
                removed |= result;
                continue;
            }
            if !dirs_exist {
                continue;
            }
            match fs::symlink_metadata(&location) {
                Ok(_) => {
                    ensure_regular_file(&location, "derived cache eviction manifest")?;
                    fs::remove_file(&location)?;
                    removed = true;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        Ok(removed)
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
    fn evicted_unborrowed_derived_blob_is_reclaimed_after_reopen() {
        let temp = tempdir().unwrap();
        let database = temp.path().join("studio.sqlite3");
        let source = temp.path().join("owned-derived-preview.bin");
        let contents = b"owned derived preview that was never handed to a consumer";
        fs::write(&source, contents).unwrap();
        let scope = "project:retention-fixture:generation:one";
        let kind = "preview-index";
        let fingerprint = digest("unborrowed-derived-renderer-fonts-and-source");
        let mut store = Store::open(&database).unwrap();
        let record = store
            .put_derived_cache_file(scope, kind, &fingerprint, &source, 37)
            .unwrap();
        assert_eq!(record.blob_sha256, hex::encode(Sha256::digest(contents)));
        assert_eq!(record.size_bytes, contents.len() as u64);

        // Observe fixture bytes directly. A lookup/verified path would lend
        // them to a legacy consumer and must conservatively prevent cleanup.
        let blob = store.blob_path(&record.blob_sha256).unwrap();
        let manifest = store
            .derived_cache_manifest_path(&record.scope_sha256, kind, &fingerprint)
            .unwrap();
        let manifest_bytes = fs::read(&manifest).unwrap();
        assert_eq!(
            serde_json::from_slice::<DerivedCacheRecord>(&manifest_bytes).unwrap(),
            record
        );
        assert_eq!(fs::read(&blob).unwrap(), contents);
        let active = store
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(active.root_managed);
        assert!(!active.truncated);
        assert_eq!(active.removed, 0);
        assert_eq!(fs::read(&blob).unwrap(), contents);
        assert_eq!(fs::read(&manifest).unwrap(), manifest_bytes);

        assert!(
            store
                .evict_derived_cache_exact(scope, kind, &fingerprint)
                .unwrap()
        );
        assert!(matches!(
            fs::symlink_metadata(&manifest),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound
        ));
        drop(store);

        let mut reopened = Store::open(&database).unwrap();
        let retained = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::from_secs(24 * 60 * 60),
                max_objects: 64,
            })
            .unwrap();
        assert!(retained.root_managed);
        assert!(!retained.truncated);
        assert_eq!(retained.removed, 0);
        assert_eq!(fs::read(&blob).unwrap(), contents);
        assert_eq!(fs::read(&source).unwrap(), contents);

        let collected = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(collected.root_managed);
        assert!(!collected.truncated);
        assert!(
            matches!(
                fs::symlink_metadata(&blob),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound
            ),
            "an evicted, unborrowed derived publication must be reclaimed: {collected:?}"
        );
        assert_eq!(collected.removed, 1);
        assert_eq!(fs::read(&source).unwrap(), contents);
        assert!(matches!(
            fs::symlink_metadata(&manifest),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound
        ));
        let repeated = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert_eq!(repeated.removed, 0);
        assert!(!repeated.truncated);
    }

    #[test]
    fn shared_derived_blob_is_reclaimed_only_after_its_last_manifest_is_evicted() {
        let temp = tempdir().unwrap();
        let database = temp.path().join("studio.sqlite3");
        let source = temp.path().join("shared-derived.bin");
        let contents = b"one physical derived output with two live cache references";
        fs::write(&source, contents).unwrap();
        let scope = "project:two-derived-references:generation:one";
        let kind = "preview-index";
        let first_key = digest("first-derived-fingerprint");
        let second_key = digest("second-derived-fingerprint");
        let mut store = Store::open(&database).unwrap();
        let first = store
            .put_derived_cache_file(scope, kind, &first_key, &source, 37)
            .unwrap();
        let second = store
            .put_derived_cache_file(scope, kind, &second_key, &source, 37)
            .unwrap();
        assert_eq!(first.blob_sha256, second.blob_sha256);
        let blob = store.blob_path(&first.blob_sha256).unwrap();
        let first_manifest = store
            .derived_cache_manifest_path(&first.scope_sha256, kind, &first_key)
            .unwrap();
        let second_manifest = store
            .derived_cache_manifest_path(&second.scope_sha256, kind, &second_key)
            .unwrap();
        assert_ne!(first_manifest, second_manifest);
        let second_bytes = fs::read(&second_manifest).unwrap();
        assert_eq!(fs::read(&blob).unwrap(), contents);

        assert!(
            store
                .evict_derived_cache_exact(scope, kind, &first_key)
                .unwrap()
        );
        let retained = store
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(retained.root_managed);
        assert!(!retained.truncated);
        assert_eq!(retained.removed, 0);
        assert_eq!(fs::read(&blob).unwrap(), contents);
        assert_eq!(fs::read(&second_manifest).unwrap(), second_bytes);
        assert!(matches!(
            fs::symlink_metadata(&first_manifest),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound
        ));

        assert!(
            store
                .evict_derived_cache_exact(scope, kind, &second_key)
                .unwrap()
        );
        drop(store);
        let mut reopened = Store::open(&database).unwrap();
        let collected = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(collected.root_managed);
        assert!(!collected.truncated);
        assert!(
            matches!(
                fs::symlink_metadata(&blob),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound
            ),
            "both derived references were explicitly evicted: {collected:?}"
        );
        assert_eq!(collected.removed, 1);
        assert_eq!(fs::read(&source).unwrap(), contents);
        assert!(matches!(
            fs::symlink_metadata(&second_manifest),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound
        ));
    }

    #[test]
    fn explicit_blob_lease_survives_reopen_and_release_enables_derived_cleanup() {
        let temp = tempdir().unwrap();
        let database = temp.path().join("studio.sqlite3");
        let source = temp.path().join("leased-derived.bin");
        let contents = b"derived bytes retained by an explicitly acquired durable lease";
        fs::write(&source, contents).unwrap();
        let scope = "project:leased-derived:generation:one";
        let kind = "preview-index";
        let fingerprint = digest("leased-derived-fingerprint");
        let store = Store::open(&database).unwrap();
        let record = store
            .put_derived_cache_file(scope, kind, &fingerprint, &source, 37)
            .unwrap();
        let blob = store.blob_path(&record.blob_sha256).unwrap();
        let lease = store.lease_blob(&record.blob_sha256).unwrap();
        assert!(
            store
                .evict_derived_cache_exact(scope, kind, &fingerprint)
                .unwrap()
        );
        drop(store);

        let mut reopened = Store::open(&database).unwrap();
        let retained = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(retained.root_managed);
        assert!(!retained.truncated);
        assert_eq!(retained.removed, 0);
        assert_eq!(fs::read(&blob).unwrap(), contents);
        reopened.release_blob_lease(lease).unwrap();
        let collected = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(collected.root_managed);
        assert!(!collected.truncated);
        assert!(
            matches!(
                fs::symlink_metadata(&blob),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound
            ),
            "only explicit lease release permits this cleanup: {collected:?}"
        );
        assert_eq!(collected.removed, 1);
        assert_eq!(fs::read(&source).unwrap(), contents);
    }

    #[test]
    fn legacy_lookup_path_survives_derived_eviction_and_store_reopen() {
        let temp = tempdir().unwrap();
        let database = temp.path().join("studio.sqlite3");
        let source = temp.path().join("borrowed-derived.bin");
        let contents = b"derived bytes already lent through a legacy PathBuf";
        fs::write(&source, contents).unwrap();
        let scope = "project:borrowed-derived:generation:one";
        let kind = "preview-index";
        let fingerprint = digest("borrowed-derived-fingerprint");
        let store = Store::open(&database).unwrap();
        let record = store
            .put_derived_cache_file(scope, kind, &fingerprint, &source, 37)
            .unwrap();
        let hit = store
            .lookup_derived_cache(scope, kind, &fingerprint)
            .unwrap()
            .unwrap();
        assert_eq!(hit.record, record);
        let borrowed = hit.blob_path.clone();
        drop(hit);
        assert!(
            store
                .evict_derived_cache_exact(scope, kind, &fingerprint)
                .unwrap()
        );
        drop(store);

        let mut reopened = Store::open(&database).unwrap();
        let report = reopened
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(report.root_managed);
        assert!(!report.truncated);
        assert_eq!(report.removed, 0);
        assert_eq!(fs::read(&borrowed).unwrap(), contents);
        assert_eq!(fs::read(&source).unwrap(), contents);
        assert!(
            !reopened
                .evict_derived_cache_exact(scope, kind, &fingerprint)
                .unwrap()
        );
    }

    #[test]
    fn peer_project_source_adoption_survives_derived_eviction_and_reopen() {
        let temp = tempdir().unwrap();
        let owner_database = temp.path().join("owner.sqlite3");
        let peer_database = temp.path().join("peer.sqlite3");
        let blob_root = temp.path().join("shared-cas");
        let source = temp.path().join("adopted-derived.bin");
        let contents = b"shared derived bytes independently adopted as a project source";
        fs::write(&source, contents).unwrap();
        let scope = "project:derived-owner:generation:one";
        let kind = "preview-index";
        let fingerprint = digest("peer-adopted-derived-fingerprint");
        let owner = Store::open_with_blob_root(&owner_database, &blob_root).unwrap();
        let record = owner
            .put_derived_cache_file(scope, kind, &fingerprint, &source, 37)
            .unwrap();
        let blob = owner.blob_path(&record.blob_sha256).unwrap();

        // Adopt through the real project mutation; no lookup or blob ingest
        // may supply an unrelated permanent pin and conceal this boundary.
        let mut peer = Store::open_with_blob_root(&peer_database, &blob_root).unwrap();
        let project = peer
            .create_named_project("Independent source owner")
            .unwrap();
        let adopted = peer
            .apply(
                project.id,
                &project.stamp(),
                "adopt-shared-derived-as-source",
                &motionwright_domain::Change::AddAsset {
                    asset: motionwright_domain::Asset {
                        id: Uuid::now_v7(),
                        name: "Adopted source".into(),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(record.blob_sha256.clone()),
                        source_revision: Some("peer-project-admission".into()),
                    },
                },
            )
            .unwrap()
            .project;
        drop(peer);
        assert!(
            owner
                .evict_derived_cache_exact(scope, kind, &fingerprint)
                .unwrap()
        );
        drop(owner);

        let mut reopened_owner = Store::open_with_blob_root(&owner_database, &blob_root).unwrap();
        let report = reopened_owner
            .recover_cas(crate::CasRetentionPolicy {
                retention: std::time::Duration::ZERO,
                max_objects: 64,
            })
            .unwrap();
        assert!(report.root_managed);
        assert!(!report.truncated);
        assert_eq!(report.removed, 0);
        assert_eq!(fs::read(&blob).unwrap(), contents);
        assert_eq!(fs::read(&source).unwrap(), contents);
        let reopened_peer = Store::open_with_blob_root(&peer_database, &blob_root).unwrap();
        assert_eq!(reopened_peer.load_project(adopted.id).unwrap(), adopted);
        assert_eq!(
            reopened_peer
                .read_blob(&record.blob_sha256, contents.len() as u64)
                .unwrap(),
            contents
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
