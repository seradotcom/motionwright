//! Snapshot-bound exports and inspected portable bundle admission.
use super::*;

const BUNDLE_FINGERPRINT_DOMAIN: &[u8] = b"motionwright.bundle-inspection.v1\0";

/// Identifies the exact manifest bytes that were inspected. This is an input
/// precondition, not an authorization token or permission to retry an operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct BundleFingerprint(String);

impl BundleFingerprint {
    pub fn from_sha256(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        validate_digest(&value).map_err(|_| {
            StorageError::InvalidBackup("bundle fingerprint must be a lowercase SHA-256".into())
        })?;
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    fn of_manifest_bytes(bytes: &[u8]) -> Self {
        Self(hex::encode(
            Sha256::new()
                .chain_update(BUNDLE_FINGERPRINT_DOMAIN)
                .chain_update(bytes)
                .finalize(),
        ))
    }
}

impl TryFrom<String> for BundleFingerprint {
    type Error = StorageError;

    fn try_from(value: String) -> Result<Self> {
        Self::from_sha256(value)
    }
}

impl From<BundleFingerprint> for String {
    fn from(value: BundleFingerprint) -> Self {
        value.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InspectedProjectBundle {
    pub plan: BundleImportPlan,
    pub fingerprint: BundleFingerprint,
}

/// Product content admission over the exact bytes that storage has hashed.
/// The prefix is bounded, immutable, and from that same complete credential
/// scan. Every asset alias comes from the captured manifest. Implementations
/// must use actual_size to distinguish a bounded prefix from a complete file.
/// This is an input policy, never an authorization grant or retry permission.
pub trait BundleAssetAdmission {
    fn prefix_limit(&self) -> usize;

    fn validate(
        &self,
        prefix: &[u8],
        actual_size: u64,
        aliases: &[&motionwright_domain::Asset],
    ) -> Result<()>;
}

// The legacy Store APIs enforce storage structure, digests and credentials.
// Product entry points must explicitly supply their content admission policy.
pub(super) struct StructuralBundleAdmission;

impl BundleAssetAdmission for StructuralBundleAdmission {
    fn prefix_limit(&self) -> usize {
        0
    }

    fn validate(
        &self,
        _prefix: &[u8],
        _actual_size: u64,
        _aliases: &[&motionwright_domain::Asset],
    ) -> Result<()> {
        Ok(())
    }
}

impl Store {
    /// Read the authorized document and its journal in one SQLite snapshot.
    pub fn export_project_at(
        &mut self,
        id: Uuid,
        expected: &RevisionStamp,
    ) -> Result<ProjectBackup> {
        self.export_project_snapshot(id, Some(expected))
    }

    /// Export precisely the checked snapshot, even if another connection edits
    /// the project while its immutable source bytes are being copied.
    pub fn export_project_bundle_at(
        &mut self,
        id: Uuid,
        expected: &RevisionStamp,
        destination: impl AsRef<Path>,
    ) -> Result<ProjectBundleManifest> {
        let backup = self.export_project_snapshot(id, Some(expected))?;
        self.export_captured_bundle(backup, destination.as_ref())
    }

    pub fn inspect_project_bundle_with_fingerprint(
        &self,
        source: impl AsRef<Path>,
    ) -> Result<InspectedProjectBundle> {
        self.inspect_project_bundle_with_fingerprint_and_admission(
            source,
            &StructuralBundleAdmission,
        )
    }

    /// Inspect without staging files, applying content policy to each member.
    pub fn inspect_project_bundle_with_admission(
        &self,
        source: impl AsRef<Path>,
        admission: &impl BundleAssetAdmission,
    ) -> Result<BundleImportPlan> {
        self.inspect_project_bundle_with_fingerprint_and_admission(source, admission)
            .map(|inspected| inspected.plan)
    }

    pub fn inspect_project_bundle_with_fingerprint_and_admission(
        &self,
        source: impl AsRef<Path>,
        admission: &impl BundleAssetAdmission,
    ) -> Result<InspectedProjectBundle> {
        let source = source.as_ref();
        let bytes = read_bundle_manifest_bytes(source)?;
        let fingerprint = BundleFingerprint::of_manifest_bytes(&bytes);
        let manifest: ProjectBundleManifest = serde_json::from_slice(&bytes)?;
        let plan = self.validate_bundle_with_admission(source, &manifest, admission)?;
        Ok(InspectedProjectBundle { plan, fingerprint })
    }

    /// Revalidate all private captures before publishing any member.
    pub fn import_project_bundle_with_admission(
        &mut self,
        source: impl AsRef<Path>,
        admission: &impl BundleAssetAdmission,
    ) -> Result<Project> {
        self.import_bundle_capture_with_admission(source.as_ref(), None, admission)
    }

    pub fn import_project_bundle_inspected_with_admission(
        &mut self,
        source: impl AsRef<Path>,
        expected: &BundleFingerprint,
        admission: &impl BundleAssetAdmission,
    ) -> Result<Project> {
        self.import_bundle_capture_with_admission(source.as_ref(), Some(expected), admission)
    }

    /// Compare the original inspection before staging or publishing any bytes.
    /// A missing result from an earlier call never authorizes a retry here.
    pub fn import_project_bundle_inspected(
        &mut self,
        source: impl AsRef<Path>,
        expected: &BundleFingerprint,
    ) -> Result<Project> {
        self.import_bundle_capture(source.as_ref(), Some(expected))
    }

    pub(super) fn import_bundle_capture(
        &mut self,
        source: &Path,
        expected: Option<&BundleFingerprint>,
    ) -> Result<Project> {
        self.import_bundle_capture_with_admission(source, expected, &StructuralBundleAdmission)
    }

    fn import_bundle_capture_with_admission(
        &mut self,
        source: &Path,
        expected: Option<&BundleFingerprint>,
        admission: &impl BundleAssetAdmission,
    ) -> Result<Project> {
        let bytes = read_bundle_manifest_bytes(source)?;
        if let Some(expected) = expected
            && BundleFingerprint::of_manifest_bytes(&bytes) != *expected
        {
            return Err(StorageError::InvalidBackup(
                "bundle manifest changed after inspection".into(),
            ));
        }
        // Fingerprinting and parsing consume the same capture, never a reload.
        let manifest: ProjectBundleManifest = serde_json::from_slice(&bytes)?;
        self.validate_bundle_with_admission(source, &manifest, admission)?;
        self.import_validated_bundle(source, &manifest, admission)
    }

    fn import_validated_bundle(
        &mut self,
        source: &Path,
        manifest: &ProjectBundleManifest,
        admission: &impl BundleAssetAdmission,
    ) -> Result<Project> {
        self.import_validated_bundle_after_check(source, manifest, admission, || {})
    }

    // The seam lets a test drive a second SQLite writer exactly after the
    // final destination inspection, at the CAS publication boundary.
    fn import_validated_bundle_after_check(
        &mut self,
        source: &Path,
        manifest: &ProjectBundleManifest,
        admission: &impl BundleAssetAdmission,
        after_check: impl FnOnce(),
    ) -> Result<Project> {
        // Capture and validate every member before publishing even the first
        // member. A path can change after the earlier inspection.
        let staged = self.capture_bundle_blobs(source, manifest, admission)?;
        // Reject pre-existing destination collisions across the entire
        // captured set BEFORE publishing its first content-addressed member.
        // Never delete a foreign or pre-existing blob on failure.
        self.preflight_bundle_blob_destinations(&staged)?;
        // The final project-ID check and the CAS publication share the same
        // IMMEDIATE SQLite writer transaction. A peer cannot insert the same
        // project ID after the check but before the project/journal commit.
        // This serializes database writers; it does NOT make CAS and SQLite
        // crash-atomic if publication or commit is interrupted.
        self.import_project_with_publication(&manifest.backup, |blob_root| {
            after_check();
            for captured in &staged {
                file_import::publish_file_import_under_root(blob_root, captured)?;
                #[cfg(test)]
                crate::apply_crash_tests::bundle_checkpoint(
                    manifest.backup.project.id,
                    "after_member_publication",
                );
            }
            Ok(())
        })
    }

    fn preflight_bundle_blob_destinations(&self, staged: &[StagedFileImport]) -> Result<()> {
        for captured in staged {
            let expected = captured.descriptor();
            match self.checked_existing_blob_path(&expected.sha256) {
                Ok(path) => {
                    if hash_portable_file(&path, expected.size_bytes)? != *expected {
                        return Err(StorageError::InvalidBlobDigest);
                    }
                }
                Err(StorageError::BlobMissing { .. }) => {}
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }

    fn capture_bundle_blobs(
        &self,
        source: &Path,
        manifest: &ProjectBundleManifest,
        admission: &impl BundleAssetAdmission,
    ) -> Result<Vec<StagedFileImport>> {
        let mut staged = Vec::with_capacity(manifest.blobs.len());
        for blob in &manifest.blobs {
            let path = bundle_member_path(source, &blob.relative_path, "portable bundle blob")?;
            let captured = self.stage_file_import_with_limit(&path, blob.size_bytes)?;
            if captured.descriptor().sha256 != blob.sha256
                || captured.descriptor().size_bytes != blob.size_bytes
            {
                return Err(StorageError::InvalidBackup(
                    "captured blob differs from bundle manifest".into(),
                ));
            }
            // Rehash and inspect the same retained stream from this private
            // capture. A prior source-path inspection cannot authorize it.
            let (descriptor, prefix) = inspect_portable_file_with_prefix(
                captured.path(),
                "captured portable asset",
                blob.size_bytes,
                admission.prefix_limit(),
            )?;
            if descriptor != *captured.descriptor() {
                return Err(StorageError::InvalidBackup(
                    "captured blob changed during content admission".into(),
                ));
            }
            admit_bundle_blob(admission, &prefix, &descriptor, manifest)?;
            staged.push(captured);
        }
        Ok(staged)
    }

    pub(super) fn export_project_snapshot(
        &mut self,
        id: Uuid,
        expected: Option<&RevisionStamp>,
    ) -> Result<ProjectBackup> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (generation, revision, title, json): (String, i64, String, String) = tx
            .query_row(
                "SELECT generation,revision,title,document_json FROM projects WHERE id=?1",
                [id.to_string()],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .optional()?
            .ok_or(StorageError::NotFound)?;
        let project: Project = serde_json::from_str(&json)?;
        project.validate()?;
        if project.id != id
            || project.generation.is_nil()
            || project.generation.to_string() != generation
            || i64::try_from(project.revision).ok() != Some(revision)
            || project.title != title
        {
            return Err(StorageError::InvalidBackup(
                "stored project authority columns differ from the document".into(),
            ));
        }
        if let Some(expected) = expected {
            if project.resource_key() != expected.resource
                || project.generation != expected.generation
            {
                return Err(StorageError::GenerationConflict);
            }
            if project.revision != expected.revision {
                return Err(StorageError::Conflict {
                    expected: expected.revision,
                    actual: project.revision,
                });
            }
        }

        let events = {
            let mut stmt = tx.prepare(
                "SELECT revision,payload_json,created_at FROM events
                 WHERE project_id=?1
                 ORDER BY revision ASC",
            )?;
            let mapped = stmt.query_map([id.to_string()], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })?;
            let mut events = Vec::new();
            for row in mapped {
                let (revision, payload, created_at) = row?;
                events.push(ProjectEvent {
                    revision: u64::try_from(revision).map_err(|_| {
                        StorageError::InvalidBackup("event revision is negative".into())
                    })?,
                    change: serde_json::from_str(&payload)?,
                    created_at,
                });
            }
            events
        };

        let payload_sha256 = backup_digest(
            PROJECT_BACKUP_FORMAT_VERSION,
            STORAGE_SCHEMA_VERSION,
            &project,
            &events,
        )?;
        let backup = ProjectBackup {
            format_version: PROJECT_BACKUP_FORMAT_VERSION,
            source_storage_schema: STORAGE_SCHEMA_VERSION,
            exported_at: chrono::Utc::now().to_rfc3339(),
            project,
            events,
            payload_sha256,
        };
        validate_backup(&backup)?;
        tx.commit()?;
        Ok(backup)
    }

    pub(super) fn export_captured_bundle(
        &self,
        backup: ProjectBackup,
        destination: &Path,
    ) -> Result<ProjectBundleManifest> {
        self.export_captured_bundle_before_publication(backup, destination, |_| {})
    }

    // Interleave an independent destination creator after all content is staged.
    // The public API supplies a no-op; the actual publication operation is shared.
    fn export_captured_bundle_before_publication(
        &self,
        backup: ProjectBackup,
        destination: &Path,
        before_publication: impl FnOnce(&Path),
    ) -> Result<ProjectBundleManifest> {
        match fs::symlink_metadata(destination) {
            Ok(_) => return Err(StorageError::DestinationExists),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        validate_backup(&backup)?;
        ensure_backup_secret_free(&backup, Some(MAX_BUNDLE_MANIFEST_BYTES))?;
        let mut digests = BTreeSet::new();
        for asset in &backup.project.assets {
            let digest = asset.content_sha256.as_deref().ok_or_else(|| {
                StorageError::InvalidBackup(format!("asset {} has no content digest", asset.id))
            })?;
            validate_digest(digest)?;
            digests.insert(digest.to_owned());
        }

        if digests.len() > MAX_BUNDLE_BLOBS {
            return Err(StorageError::InvalidBackup(
                "portable bundle contains too many blobs".into(),
            ));
        }
        let mut blobs = Vec::with_capacity(digests.len());
        let mut total_blob_bytes = 0_u64;
        for digest in digests {
            let source = self.checked_existing_blob_path(&digest)?;
            let descriptor = inspect_portable_file(
                &source,
                "portable asset",
                MAX_BUNDLE_TOTAL_BYTES - total_blob_bytes,
            )?;
            if descriptor.sha256 != digest {
                return Err(StorageError::InvalidBackup(
                    "stored blob digest differs from its content-addressed path".into(),
                ));
            }
            total_blob_bytes += descriptor.size_bytes;
            blobs.push(PortableBlob {
                relative_path: blob_relative_path(&digest)?,
                sha256: digest,
                size_bytes: descriptor.size_bytes,
            });
        }

        let manifest = ProjectBundleManifest {
            format_version: PROJECT_BUNDLE_FORMAT_VERSION,
            created_at: chrono::Utc::now().to_rfc3339(),
            backup,
            blobs,
        };

        let manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
        if manifest_bytes.len() as u64 > MAX_BUNDLE_MANIFEST_BYTES {
            return Err(StorageError::BlobTooLarge {
                size: manifest_bytes.len() as u64,
                limit: MAX_BUNDLE_MANIFEST_BYTES,
            });
        }
        ensure_secret_free_bytes(&manifest_bytes, "portable bundle metadata")?;
        let destination_name = destination.file_name().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "bundle destination must name a new directory",
            )
        })?;
        let parent = destination
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent)?;
        // Resolve the existing parent once, before creating either entry.
        // Windows canonical paths retain long-path and Unicode support at FFI.
        let parent = fs::canonicalize(parent)?;
        let publication_destination = parent.join(destination_name);
        let staging = parent.join(format!(".motionwright-export-{}", Uuid::now_v7()));
        fs::create_dir(&staging)?;
        let mut owned_stage = BundleExportStage(Some(staging.clone()));

        for blob in &manifest.blobs {
            let source = self.checked_existing_blob_path(&blob.sha256)?;
            let target = staging.join(&blob.relative_path);
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            let captured = copy_portable_file(&source, &target, blob.size_bytes)?;
            if captured.sha256 != blob.sha256 || captured.size_bytes != blob.size_bytes {
                return Err(StorageError::InvalidBackup(
                    "export source changed after blob validation".into(),
                ));
            }
            let copied = hash_portable_file(&target, blob.size_bytes)?;
            if copied.sha256 != blob.sha256 || copied.size_bytes != blob.size_bytes {
                return Err(StorageError::InvalidBackup(
                    "exported blob failed readback verification".into(),
                ));
            }
        }

        let manifest_path = staging.join("manifest.json");
        let mut manifest_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&manifest_path)?;
        manifest_file.write_all(&manifest_bytes)?;
        manifest_file.flush()?;
        manifest_file.sync_all()?;
        drop(manifest_file);
        before_publication(&staging);
        publish_bundle_directory(&staging, &publication_destination)?;
        owned_stage.0 = None;
        Ok(manifest)
    }
}

/// Publish the complete sibling directory only if its destination is still free.
/// Unsupported filesystem operations are errors; there is no replacing fallback.
fn publish_bundle_directory(staging: &Path, destination: &Path) -> Result<()> {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    let result = {
        use rustix::fs::{CWD, RenameFlags, renameat_with};
        renameat_with(CWD, staging, CWD, destination, RenameFlags::NOREPLACE)
            .map_err(std::io::Error::from)
    };
    #[cfg(windows)]
    let result = {
        use windows_sys::Win32::Storage::FileSystem::MoveFileExW;
        let source = bundle_windows_path(staging)?;
        let target = bundle_windows_path(destination)?;
        // SAFETY: both checked buffers have no interior NUL and own their final
        // terminator. They remain alive for the synchronous call. Flags zero
        // prohibit replacement, deferred moves and cross-volume copy fallback.
        let moved = unsafe { MoveFileExW(source.as_ptr(), target.as_ptr(), 0) };
        if moved == 0 {
            Err(std::io::Error::last_os_error())
        } else {
            Ok(())
        }
    };
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    let result = {
        let _ = (staging, destination);
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "exclusive bundle directory publication is unsupported on this platform",
        ))
    };
    match result {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            Err(StorageError::DestinationExists)
        }
        Err(error) => Err(error.into()),
    }
}

#[cfg(windows)]
fn bundle_windows_path(path: &Path) -> std::io::Result<Vec<u16>> {
    use std::os::windows::ffi::OsStrExt;
    let mut encoded: Vec<_> = path.as_os_str().encode_wide().collect();
    if encoded.contains(&0) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "bundle publication path contains an interior NUL",
        ));
    }
    encoded.push(0);
    Ok(encoded)
}

/// Only the directory created for this attempt is removed on failure.
struct BundleExportStage(Option<PathBuf>);

impl Drop for BundleExportStage {
    fn drop(&mut self) {
        if let Some(path) = self.0.take() {
            let _ = fs::remove_dir_all(path);
        }
    }
}

/// The byte budget constrains the actual read, including growth after stat.
fn transfer_portable_bytes(
    input: impl Read,
    output: impl Write,
    limit: u64,
) -> Result<BlobDescriptor> {
    transfer_portable_bytes_with_credential_check(input, output, limit, None)
}

fn transfer_portable_bytes_with_credential_check(
    input: impl Read,
    mut output: impl Write,
    limit: u64,
    credential_context: Option<&str>,
) -> Result<BlobDescriptor> {
    let mut input = input.take(limit.saturating_add(1));
    let mut hasher = Sha256::new();
    let mut size_bytes = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    let mut credentials = CredentialScan::default();
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        size_bytes = size_bytes.checked_add(read as u64).ok_or_else(|| {
            StorageError::InvalidBackup("portable source byte count overflowed".into())
        })?;
        if size_bytes > limit {
            return Err(StorageError::BlobTooLarge {
                size: size_bytes,
                limit,
            });
        }
        if let Some(context) = credential_context {
            credentials.check(&buffer[..read], context)?;
        }
        output.write_all(&buffer[..read])?;
        hasher.update(&buffer[..read]);
    }
    Ok(BlobDescriptor {
        sha256: hex::encode(hasher.finalize()),
        size_bytes,
    })
}

// Retain only the content-policy prefix while acknowledging every write.
// The complete stream still establishes the digest, byte budget and secrets.
struct PortablePrefix {
    bytes: Vec<u8>,
    limit: usize,
}

impl Write for PortablePrefix {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        let retained = bytes.len().min(self.limit.saturating_sub(self.bytes.len()));
        self.bytes.extend_from_slice(&bytes[..retained]);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

pub(super) fn inspect_portable_file_with_prefix(
    path: &Path,
    context: &str,
    limit: u64,
    prefix_limit: usize,
) -> Result<(BlobDescriptor, Vec<u8>)> {
    if prefix_limit as u64 > MAX_BUNDLE_MANIFEST_BYTES {
        return Err(StorageError::InvalidBackup(
            "portable content admission prefix exceeds its memory budget".into(),
        ));
    }
    // Retain a regular non-reparse handle; do not follow a replacement leaf.
    let input = file_import::open_regular_import_source(path, limit)?;
    let mut prefix = PortablePrefix {
        bytes: Vec::with_capacity(limit.min(prefix_limit as u64) as usize),
        limit: prefix_limit,
    };
    let descriptor =
        transfer_portable_bytes_with_credential_check(input, &mut prefix, limit, Some(context))?;
    Ok((descriptor, prefix.bytes))
}

pub(super) fn admit_bundle_blob(
    admission: &impl BundleAssetAdmission,
    prefix: &[u8],
    descriptor: &BlobDescriptor,
    manifest: &ProjectBundleManifest,
) -> Result<()> {
    let aliases: Vec<_> = manifest
        .backup
        .project
        .assets
        .iter()
        .filter(|asset| asset.content_sha256.as_deref() == Some(descriptor.sha256.as_str()))
        .collect();
    admission.validate(prefix, descriptor.size_bytes, &aliases)
}

fn checked_portable_source(path: &Path, context: &str, limit: u64) -> Result<File> {
    let size = ensure_regular_file(path, context)?.len();
    if size > limit {
        return Err(StorageError::BlobTooLarge { size, limit });
    }
    Ok(File::open(path)?)
}

pub(super) fn hash_portable_file(path: &Path, limit: u64) -> Result<BlobDescriptor> {
    let input = checked_portable_source(path, "hashed portable source", limit)?;
    transfer_portable_bytes(input, std::io::sink(), limit)
}

pub(super) fn inspect_portable_file(
    path: &Path,
    context: &str,
    limit: u64,
) -> Result<BlobDescriptor> {
    let input = checked_portable_source(path, context, limit)?;
    transfer_portable_bytes_with_credential_check(input, std::io::sink(), limit, Some(context))
}

fn copy_portable_file(source: &Path, target: &Path, limit: u64) -> Result<BlobDescriptor> {
    let input = checked_portable_source(source, "copied portable source", limit)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut output = options.open(target)?;
    let descriptor = transfer_portable_bytes_with_credential_check(
        input,
        &mut output,
        limit,
        Some("copied portable asset"),
    )?;
    output.flush()?;
    output.sync_all()?;
    Ok(descriptor)
}

pub(super) fn read_bundle_manifest_bytes(source: &Path) -> Result<Vec<u8>> {
    let path = bundle_member_path(source, "manifest.json", "portable bundle manifest")?;
    let size = ensure_regular_file(&path, "portable bundle manifest")?.len();
    if size > MAX_BUNDLE_MANIFEST_BYTES {
        return Err(StorageError::BlobTooLarge {
            size,
            limit: MAX_BUNDLE_MANIFEST_BYTES,
        });
    }
    // The file can grow after metadata inspection. Never perform an unbounded
    // read and never parse a truncated prefix as a complete manifest.
    let mut bytes = Vec::with_capacity(size as usize);
    File::open(path)?
        .take(MAX_BUNDLE_MANIFEST_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_BUNDLE_MANIFEST_BYTES {
        return Err(StorageError::BlobTooLarge {
            size: bytes.len() as u64,
            limit: MAX_BUNDLE_MANIFEST_BYTES,
        });
    }
    ensure_secret_free_bytes(&bytes, "portable bundle metadata")?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::Asset;
    use rusqlite::types::Value;

    #[test]
    fn portable_prefix_does_not_truncate_hash_or_byte_count() {
        let directory = tempfile::tempdir().unwrap();
        let input = directory.path().join("source.bin");
        fs::write(&input, b"abc").unwrap();
        let (descriptor, prefix) =
            inspect_portable_file_with_prefix(&input, "prefix test", 3, 1).unwrap();
        assert_eq!(prefix, b"a");
        assert_eq!(descriptor.size_bytes, 3);
        assert_eq!(descriptor.sha256, hex::encode(Sha256::digest(b"abc")));
        let mut split = PortablePrefix {
            bytes: Vec::new(),
            limit: 1,
        };
        assert_eq!(split.write(b"a").unwrap(), 1);
        assert_eq!(split.write(b"bc").unwrap(), 2);
        assert_eq!(split.bytes, b"a");
    }

    #[test]
    fn portable_prefix_still_rejects_credentials_beyond_retained_bytes() {
        let directory = tempfile::tempdir().unwrap();
        let input = directory.path().join("source.bin");
        let mut bytes = vec![0xff; 128 * 1024 - 2];
        bytes.extend_from_slice(PRIVATE_KEY_MARKER);
        fs::write(&input, &bytes).unwrap();
        assert!(matches!(
            inspect_portable_file_with_prefix(&input, "prefix test", bytes.len() as u64, 1),
            Err(StorageError::InvalidBackup(_))
        ));
    }

    #[test]
    fn final_private_capture_policy_refusal_publishes_no_bundle_member() {
        struct RefuseFourth(std::cell::Cell<usize>);
        impl BundleAssetAdmission for RefuseFourth {
            fn prefix_limit(&self) -> usize {
                64
            }
            fn validate(&self, prefix: &[u8], size: u64, aliases: &[&Asset]) -> Result<()> {
                assert_eq!(prefix.len() as u64, size);
                assert_eq!(aliases.len(), 1);
                let next = self.0.get() + 1;
                self.0.set(next);
                if next == 4 {
                    Err(StorageError::InvalidBackup(
                        "private capture policy refusal".into(),
                    ))
                } else {
                    Ok(())
                }
            }
        }
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        target.create_named_project("Existing sentinel").unwrap();
        let before = snapshot(&target);
        let admission = RefuseFourth(std::cell::Cell::new(0));
        assert!(matches!(
            target.import_project_bundle_with_admission(&bundle, &admission),
            Err(StorageError::InvalidBackup(message)) if message == "private capture policy refusal"
        ));
        assert_eq!(
            admission.0.get(),
            4,
            "source and every private capture must be checked"
        );
        assert_eq!(snapshot(&target), before);
        for blob in &manifest.blobs {
            assert!(matches!(
                target.read_blob(&blob.sha256, 4096),
                Err(StorageError::BlobMissing { .. })
            ));
        }
        assert!(
            fs::read_dir(target.blob_root().join(".staging"))
                .unwrap()
                .next()
                .is_none()
        );
    }

    #[test]
    fn bundle_policy_and_publication_use_capture_after_original_replacement() {
        struct ReplaceAfterCapture {
            calls: std::cell::Cell<usize>,
            bundle: PathBuf,
        }
        impl BundleAssetAdmission for ReplaceAfterCapture {
            fn prefix_limit(&self) -> usize {
                64
            }
            fn validate(&self, prefix: &[u8], size: u64, aliases: &[&Asset]) -> Result<()> {
                assert_eq!(prefix.len() as u64, size);
                let digest = aliases[0].content_sha256.as_ref().unwrap();
                assert_eq!(&hex::encode(Sha256::digest(prefix)), digest);
                let next = self.calls.get() + 1;
                self.calls.set(next);
                if next == 3 {
                    fs::write(
                        self.bundle.join(blob_relative_path(digest)?),
                        b"changed original",
                    )?;
                }
                Ok(())
            }
        }
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        let expected: Vec<_> = manifest
            .blobs
            .iter()
            .map(|blob| {
                (
                    blob.sha256.clone(),
                    fs::read(bundle.join(&blob.relative_path)).unwrap(),
                )
            })
            .collect();
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let admission = ReplaceAfterCapture {
            calls: std::cell::Cell::new(0),
            bundle: bundle.clone(),
        };
        let imported = target
            .import_project_bundle_with_admission(&bundle, &admission)
            .unwrap();
        assert_eq!(admission.calls.get(), 4);
        assert_eq!(imported.assets, manifest.backup.project.assets);
        for (digest, bytes) in expected {
            assert_eq!(target.read_blob(&digest, 4096).unwrap(), bytes);
        }
        assert_eq!(
            fs::read(bundle.join(&manifest.blobs[0].relative_path)).unwrap(),
            b"changed original"
        );
        assert!(
            fs::read_dir(target.blob_root().join(".staging"))
                .unwrap()
                .next()
                .is_none()
        );
    }

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
            statement
                .query_map([], |row| {
                    (0..columns)
                        .map(|column| row.get::<_, Value>(column))
                        .collect()
                })
                .unwrap()
                .map(|row| row.unwrap())
                .collect()
        })
        .collect()
    }

    fn bundle_fixture(root: &Path) -> (PathBuf, ProjectBundleManifest) {
        let mut source = Store::open(root.join("source/state.sqlite3")).unwrap();
        let mut project = source.create_named_project("Captured bundle").unwrap();
        for (index, bytes) in [b"alpha immutable bytes".as_slice(), b"beta immutable bytes"]
            .into_iter()
            .enumerate()
        {
            let input = root.join(format!("input-{index}.bin"));
            fs::write(&input, bytes).unwrap();
            let descriptor = source.ingest_blob_file(&input).unwrap();
            let asset = Asset {
                id: Uuid::now_v7(),
                name: format!("original-{index}.bin"),
                media_type: "application/octet-stream".into(),
                content_sha256: Some(descriptor.sha256),
                source_revision: Some("local-import".into()),
            };
            project = source
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("asset-{index}"),
                    &Change::AddAsset { asset },
                )
                .unwrap()
                .project;
        }
        let bundle = root.join("portable");
        let manifest = source
            .export_project_bundle_at(project.id, &project.stamp(), &bundle)
            .unwrap();
        (bundle, manifest)
    }

    fn bundle_export_source(root: &Path) -> (Store, ProjectBackup, Vec<(String, Vec<u8>)>) {
        let (bundle, manifest) = bundle_fixture(root);
        let bytes = manifest
            .blobs
            .iter()
            .map(|blob| {
                (
                    blob.sha256.clone(),
                    fs::read(bundle.join(&blob.relative_path)).unwrap(),
                )
            })
            .collect();
        let mut store = Store::open(root.join("source/state.sqlite3")).unwrap();
        let project = &manifest.backup.project;
        let backup = store
            .export_project_at(project.id, &project.stamp())
            .unwrap();
        (store, backup, bytes)
    }

    #[test]
    fn bundle_export_rejects_empty_destination_created_at_publication() {
        let directory = tempfile::tempdir().unwrap();
        let (store, backup, sources) = bundle_export_source(directory.path());
        let before = snapshot(&store);
        let output_parent = directory.path().join("outputs");
        fs::create_dir(&output_parent).unwrap();
        let foreign_stage = output_parent.join(".motionwright-export-other-owner");
        fs::create_dir(&foreign_stage).unwrap();
        fs::write(foreign_stage.join("sentinel"), b"other export").unwrap();
        let destination = output_parent.join("backup");
        let mut peer_metadata = None;
        let mut own_staging = None;
        let result =
            store.export_captured_bundle_before_publication(backup, &destination, |staging| {
                assert!(staging.join("manifest.json").is_file());
                assert!(!destination.exists());
                own_staging = Some(staging.to_path_buf());
                let peer_destination = destination.clone();
                peer_metadata = Some(
                    std::thread::spawn(move || {
                        fs::create_dir(&peer_destination).unwrap();
                        fs::symlink_metadata(&peer_destination).unwrap()
                    })
                    .join()
                    .unwrap(),
                );
            });
        let peer_metadata = peer_metadata.expect("publication boundary did not run");
        assert!(peer_metadata.is_dir());
        assert!(
            result.is_err(),
            "export replaced the peer's empty directory; manifest_published={}",
            destination.join("manifest.json").exists()
        );
        assert!(fs::read_dir(&destination).unwrap().next().is_none());
        let retained = fs::symlink_metadata(&destination).unwrap();
        assert!(retained.is_dir());
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            assert_eq!(
                (retained.dev(), retained.ino()),
                (peer_metadata.dev(), peer_metadata.ino()),
                "the destination must be the peer's original directory"
            );
        }
        assert!(!own_staging.unwrap().exists());
        assert_eq!(
            fs::read(foreign_stage.join("sentinel")).unwrap(),
            b"other export"
        );
        assert_eq!(snapshot(&store), before);
        for (digest, bytes) in sources {
            assert_eq!(store.read_blob(&digest, bytes.len() as u64).unwrap(), bytes);
        }
    }

    #[test]
    fn bundle_export_rejects_file_created_at_publication() {
        let directory = tempfile::tempdir().unwrap();
        let (store, backup, _) = bundle_export_source(directory.path());
        let before = snapshot(&store);
        let destination = directory.path().join("peer-file");
        let mut own_staging = None;
        let result =
            store.export_captured_bundle_before_publication(backup, &destination, |staging| {
                own_staging = Some(staging.to_path_buf());
                let peer_destination = destination.clone();
                std::thread::spawn(move || {
                    let mut file = OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(peer_destination)
                        .unwrap();
                    file.write_all(b"foreign destination bytes").unwrap();
                    file.sync_all().unwrap();
                })
                .join()
                .unwrap();
            });
        assert!(result.is_err(), "a peer file must never be replaced");
        assert_eq!(
            fs::read(&destination).unwrap(),
            b"foreign destination bytes"
        );
        assert!(!own_staging.unwrap().exists());
        assert_eq!(snapshot(&store), before);
    }

    #[test]
    fn bundle_export_preserves_preexisting_destinations() {
        let directory = tempfile::tempdir().unwrap();
        let (mut store, backup, _) = bundle_export_source(directory.path());
        let before = snapshot(&store);
        for kind in ["empty-directory", "populated-directory", "regular-file"] {
            let destination = directory.path().join(kind);
            if kind == "regular-file" {
                fs::write(&destination, b"preexisting file").unwrap();
            } else {
                fs::create_dir(&destination).unwrap();
                if kind == "populated-directory" {
                    fs::write(destination.join("sentinel"), b"preexisting directory").unwrap();
                }
            }
            let result = store.export_project_bundle_at(
                backup.project.id,
                &backup.project.stamp(),
                &destination,
            );
            assert!(matches!(result, Err(StorageError::DestinationExists)));
            match kind {
                "regular-file" => {
                    assert_eq!(fs::read(&destination).unwrap(), b"preexisting file");
                }
                "populated-directory" => {
                    assert_eq!(
                        fs::read(destination.join("sentinel")).unwrap(),
                        b"preexisting directory"
                    );
                }
                _ => assert!(fs::read_dir(&destination).unwrap().next().is_none()),
            }
        }
        assert_eq!(snapshot(&store), before);
        assert!(!fs::read_dir(directory.path()).unwrap().any(|entry| {
            entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".motionwright-export-")
        }));
    }

    #[test]
    fn bundle_export_unclaimed_destination_restores_complete_original_bytes() {
        let directory = tempfile::tempdir().unwrap();
        let (mut store, backup, sources) = bundle_export_source(directory.path());
        let before = snapshot(&store);
        let destination = directory.path().join("unclaimed-backup");
        let manifest = store
            .export_project_bundle_at(backup.project.id, &backup.project.stamp(), &destination)
            .unwrap();
        assert_eq!(manifest.backup.project, backup.project);
        assert_eq!(manifest.backup.events, backup.events);
        assert_eq!(snapshot(&store), before);
        let database = directory.path().join("restored/state.sqlite3");
        let mut target = Store::open(&database).unwrap();
        let inspected = target
            .inspect_project_bundle_with_fingerprint(&destination)
            .unwrap();
        let imported = target
            .import_project_bundle_inspected(&destination, &inspected.fingerprint)
            .unwrap();
        let mut expected = backup.project;
        assert_ne!(expected.generation, imported.generation);
        expected.generation = imported.generation;
        expected.updated_at = imported.updated_at;
        assert_eq!(imported, expected);
        drop(target);
        let mut reopened = Store::open(&database).unwrap();
        assert_eq!(reopened.load_project(imported.id).unwrap(), imported);
        assert_eq!(
            reopened
                .export_project_at(imported.id, &imported.stamp())
                .unwrap()
                .events,
            backup.events
        );
        for (digest, bytes) in sources {
            assert_eq!(
                reopened.read_blob(&digest, bytes.len() as u64).unwrap(),
                bytes
            );
            assert_eq!(store.read_blob(&digest, bytes.len() as u64).unwrap(), bytes);
        }
        assert!(!fs::read_dir(directory.path()).unwrap().any(|entry| {
            entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".motionwright-export-")
        }));
    }

    #[cfg(unix)]
    #[test]
    fn bundle_export_preserves_dangling_symlink_created_at_publication() {
        use std::os::unix::fs::{MetadataExt, symlink};
        let directory = tempfile::tempdir().unwrap();
        let (store, backup, _) = bundle_export_source(directory.path());
        let destination = directory.path().join("peer-link");
        let missing = directory.path().join("never-created");
        let mut peer_identity = None;
        let result = store.export_captured_bundle_before_publication(backup, &destination, |_| {
            symlink(&missing, &destination).unwrap();
            let metadata = fs::symlink_metadata(&destination).unwrap();
            peer_identity = Some((metadata.dev(), metadata.ino()));
        });
        assert!(result.is_err());
        assert_eq!(fs::read_link(&destination).unwrap(), missing);
        let retained = fs::symlink_metadata(&destination).unwrap();
        assert!(retained.file_type().is_symlink());
        assert_eq!(Some((retained.dev(), retained.ino())), peer_identity);
        assert!(!missing.exists());
    }

    #[cfg(windows)]
    #[test]
    fn bundle_export_windows_long_unicode_destination_remains_operational() {
        use std::os::windows::ffi::OsStrExt;
        let directory = tempfile::tempdir().unwrap();
        let (mut store, backup, sources) = bundle_export_source(directory.path());
        let ordinary_root = directory
            .path()
            .to_str()
            .expect("test temporary path must be Unicode")
            .strip_prefix(r"\\?\")
            .unwrap_or_else(|| directory.path().to_str().unwrap());
        let mut parent = PathBuf::from(ordinary_root).join("respaldo con espacios");
        while parent.as_os_str().encode_wide().count() < 300 {
            parent.push("carpeta para proyecto \u{65e5}\u{672c}\u{8a9e}");
        }
        let destination = parent.join("exportaci\u{f3}n completa");
        assert!(!destination.to_string_lossy().starts_with(r"\\?\"));
        assert!(destination.as_os_str().encode_wide().count() > 260);
        let exported = store
            .export_project_bundle_at(backup.project.id, &backup.project.stamp(), &destination)
            .unwrap();
        assert_eq!(exported.backup.project, backup.project);
        let database = directory.path().join("long-path-restored/state.sqlite3");
        let mut target = Store::open(&database).unwrap();
        let inspected = target
            .inspect_project_bundle_with_fingerprint(&destination)
            .unwrap();
        let restored = target
            .import_project_bundle_inspected(&destination, &inspected.fingerprint)
            .unwrap();
        drop(target);
        let reopened = Store::open(&database).unwrap();
        assert_eq!(reopened.load_project(restored.id).unwrap(), restored);
        assert_eq!(restored.assets, backup.project.assets);
        for (digest, bytes) in sources {
            assert_eq!(
                reopened.read_blob(&digest, bytes.len() as u64).unwrap(),
                bytes
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn bundle_export_windows_nul_paths_fail_before_publication() {
        let directory = tempfile::tempdir().unwrap();
        let staging = directory.path().join("original-stage");
        fs::create_dir(&staging).unwrap();
        fs::write(staging.join("sentinel"), b"owned original bytes").unwrap();
        let destination = directory.path().join("must-not-be-created");
        for invalid_source in [true, false] {
            let invalid = PathBuf::from(format!(
                "{}\0suffix",
                if invalid_source {
                    &staging
                } else {
                    &destination
                }
                .display()
            ));
            let (source, target) = if invalid_source {
                (invalid.as_path(), destination.as_path())
            } else {
                (staging.as_path(), invalid.as_path())
            };
            assert!(matches!(
                publish_bundle_directory(source, target),
                Err(StorageError::Io(error))
                    if error.kind() == std::io::ErrorKind::InvalidInput
            ));
            assert_eq!(
                fs::read(staging.join("sentinel")).unwrap(),
                b"owned original bytes"
            );
            assert!(!destination.exists());
        }
    }

    #[test]
    fn fingerprint_deserialization_requires_canonical_sha256() {
        let valid = "ab".repeat(32);
        assert_eq!(
            BundleFingerprint::from_sha256(&valid).unwrap().as_str(),
            valid
        );
        for invalid in [
            String::new(),
            "A".repeat(64),
            "g".repeat(64),
            "0".repeat(63),
            "0".repeat(65),
            format!("{}\n", "0".repeat(64)),
        ] {
            assert!(BundleFingerprint::from_sha256(&invalid).is_err());
            assert!(
                serde_json::from_str::<BundleFingerprint>(
                    &serde_json::to_string(&invalid).unwrap()
                )
                .is_err()
            );
        }
    }

    #[test]
    fn bundle_inspection_is_readonly_and_hashes_the_same_manifest_bytes() {
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        let target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let before = snapshot(&target);
        let changes = target.conn.total_changes();
        target.conn.execute_batch("PRAGMA query_only=ON").unwrap();
        let bytes = fs::read(bundle.join("manifest.json")).unwrap();
        let inspected = target
            .inspect_project_bundle_with_fingerprint(&bundle)
            .unwrap();
        assert_eq!(
            inspected.plan.project.project_id,
            manifest.backup.project.id
        );
        assert_eq!(inspected.plan.blob_count, 2);
        let independently_hashed = hex::encode(
            Sha256::new()
                .chain_update(b"motionwright.bundle-inspection.v1\0")
                .chain_update(&bytes)
                .finalize(),
        );
        assert_eq!(inspected.fingerprint.as_str(), independently_hashed);
        assert_eq!(
            target.inspect_project_bundle(&bundle).unwrap(),
            inspected.plan
        );
        assert_eq!(snapshot(&target), before);
        assert_eq!(target.conn.total_changes(), changes);
        assert_eq!(fs::read(bundle.join("manifest.json")).unwrap(), bytes);
        assert!(fs::read_dir(target.blob_root()).unwrap().next().is_none());
    }

    #[test]
    fn export_rejects_corrupt_authority_columns_without_publication() {
        for corruption in [
            "generation",
            "revision",
            "title",
            "document_id",
            "nil_generation",
        ] {
            let directory = tempfile::tempdir().unwrap();
            let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
            let original = store.create_named_project("Authority columns").unwrap();
            match corruption {
                "generation" => {
                    store
                        .conn
                        .execute(
                            "UPDATE projects SET generation=?1",
                            [Uuid::now_v7().to_string()],
                        )
                        .unwrap();
                }
                "revision" => {
                    store
                        .conn
                        .execute("UPDATE projects SET revision=1", [])
                        .unwrap();
                }
                "title" => {
                    store
                        .conn
                        .execute("UPDATE projects SET title='Other title'", [])
                        .unwrap();
                }
                "document_id" => {
                    let mut different = original.clone();
                    different.id = Uuid::now_v7();
                    store
                        .conn
                        .execute(
                            "UPDATE projects SET document_json=?1",
                            [serde_json::to_string(&different).unwrap()],
                        )
                        .unwrap();
                }
                "nil_generation" => {
                    let mut different = original.clone();
                    different.generation = Uuid::nil();
                    store
                        .conn
                        .execute(
                            "UPDATE projects SET generation=?1,document_json=?2",
                            params![
                                different.generation.to_string(),
                                serde_json::to_string(&different).unwrap()
                            ],
                        )
                        .unwrap();
                }
                _ => unreachable!(),
            }
            let before = snapshot(&store);
            let destination = directory.path().join("rejected");
            assert!(
                matches!(
                    store.export_project_at(original.id, &original.stamp()),
                    Err(StorageError::InvalidBackup(_))
                ),
                "{corruption}"
            );
            assert!(
                matches!(
                    store.export_project(original.id),
                    Err(StorageError::InvalidBackup(_))
                ),
                "{corruption}"
            );
            assert!(
                matches!(
                    store.export_project_bundle_at(original.id, &original.stamp(), &destination),
                    Err(StorageError::InvalidBackup(_))
                ),
                "{corruption}"
            );
            assert!(!destination.exists());
            assert_eq!(snapshot(&store), before);
        }
    }

    #[test]
    fn captured_export_keeps_authorized_revision_after_peer_advances() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("source/state.sqlite3");
        let mut source = Store::open(&database).unwrap();
        let original = source.create_named_project("Authorized snapshot").unwrap();
        let captured = source
            .export_project_at(original.id, &original.stamp())
            .unwrap();
        let mut peer = Store::open(&database).unwrap();
        let advanced = peer
            .apply(
                original.id,
                &original.stamp(),
                "peer-edit",
                &Change::RenameProject {
                    title: "Later snapshot".into(),
                },
            )
            .unwrap()
            .project;
        let destination = directory.path().join("captured");
        let exported = source
            .export_captured_bundle(captured.clone(), &destination)
            .unwrap();
        assert_eq!(exported.backup, captured);
        assert_eq!(exported.backup.project, original);
        assert_eq!(source.load_project(original.id).unwrap(), advanced);
        let actual = load_bundle_manifest(&destination).unwrap();
        assert_eq!(actual, exported);
    }

    #[test]
    fn newly_visible_existing_project_id_must_not_publish_any_bundle_blobs() {
        // Reproduce the time window between an accepted initial bundle
        // inspection and the later media capture. A second SQLite connection
        // legitimately creates another project using the bundle's ID.
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        assert_eq!(manifest.blobs.len(), 2);
        let database = directory.path().join("target/state.sqlite3");
        let mut target = Store::open(&database).unwrap();
        let initial = target
            .inspect_project_bundle_with_fingerprint(&bundle)
            .unwrap();
        assert_eq!(initial.plan.blob_count, 2);
        for blob in &manifest.blobs {
            assert!(
                target.verify_blob(&blob.sha256).is_err(),
                "target unexpectedly already contains a source CAS member"
            );
        }

        let mut peer = Store::open(&database).unwrap();
        let mut current = Project::new("Independent local project").unwrap();
        current.id = manifest.backup.project.id;
        peer.create_project(&current).unwrap();
        drop(peer);

        let before = snapshot(&target);
        let rejected =
            target.import_validated_bundle(&bundle, &manifest, &StructuralBundleAdmission);
        assert!(
            matches!(rejected, Err(StorageError::ProjectExists)),
            "second connection's valid project ID collision was not rejected"
        );
        assert_eq!(
            snapshot(&target),
            before,
            "journal, project or receipts changed"
        );
        assert_eq!(target.load_project(current.id).unwrap(), current);
        for blob in &manifest.blobs {
            assert!(
                target.verify_blob(&blob.sha256).is_err(),
                "a bundle CAS member was published after an already-visible
                 project collision rejected its import"
            );
        }
        let stage = target.blob_root().join(".staging");
        assert!(
            fs::read_dir(stage).unwrap().next().is_none(),
            "rejected bundle left private captured import bytes"
        );
        for blob in &manifest.blobs {
            assert!(bundle.join(&blob.relative_path).is_file());
        }
    }

    #[test]
    fn late_sqlite_writer_cannot_claim_project_during_portable_publication() {
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        assert_eq!(manifest.blobs.len(), 2);
        let database = directory.path().join("target/state.sqlite3");
        let mut target = Store::open(&database).unwrap();
        let mut peer = Store::open(&database).unwrap();
        peer.conn.busy_timeout(Duration::from_millis(150)).unwrap();

        let inspected = target
            .inspect_project_bundle_with_fingerprint(&bundle)
            .unwrap();
        assert_eq!(inspected.plan.blob_count, 2);
        let mut competitor = Project::new("Second client destination").unwrap();
        competitor.id = manifest.backup.project.id;

        let stage = target.blob_root().join(".staging");
        fs::create_dir_all(&stage).unwrap();
        let foreign_capture = stage.join("untouched-other-capture.part");
        fs::write(&foreign_capture, b"foreign capture must remain untouched").unwrap();

        // Inject the other writer at the exact boundary between the final
        // project-ID check and the first CAS publication. Merely rechecking
        // the ID in a SELECT cannot protect this interval.
        let mut other_writer = None;
        let result = target.import_validated_bundle_after_check(
            &bundle,
            &manifest,
            &StructuralBundleAdmission,
            || {
                other_writer = Some(peer.create_project(&competitor));
            },
        );
        let other_writer = other_writer.expect("boundary hook did not execute");
        assert!(
            matches!(
                &other_writer,
                Err(StorageError::Sql(rusqlite::Error::SqliteFailure(error, _)))
                    if matches!(
                        error.code,
                        rusqlite::ErrorCode::DatabaseBusy
                            | rusqlite::ErrorCode::DatabaseLocked
                    )
            ),
            "second writer claimed the ID while portable CAS publication was admitted: {other_writer:?}"
        );
        let imported = result.expect("admitted source should commit after peer writer is blocked");
        assert_eq!(target.load_project(imported.id).unwrap(), imported);
        assert!(
            peer.create_project(&competitor).is_err(),
            "competing project overwrote the already committed import"
        );
        for blob in &manifest.blobs {
            assert_eq!(
                target.read_blob(&blob.sha256, blob.size_bytes).unwrap(),
                fs::read(bundle.join(&blob.relative_path)).unwrap()
            );
        }
        let staged_files: Vec<_> = fs::read_dir(&stage)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        assert_eq!(staged_files, vec![foreign_capture.clone()]);
        assert_eq!(
            fs::read(foreign_capture).unwrap(),
            b"foreign capture must remain untouched"
        );
    }

    #[test]
    fn rejected_sqlite_project_or_event_write_does_not_publish_captured_cas() {
        for rejected_table in ["projects", "events"] {
            let directory = tempfile::tempdir().unwrap();
            let (bundle, manifest) = bundle_fixture(directory.path());
            assert_eq!(manifest.backup.events.len(), 2);
            let database = directory.path().join("target/state.sqlite3");
            let mut target = Store::open(&database).unwrap();
            let inspected = target
                .inspect_project_bundle_with_fingerprint(&bundle)
                .unwrap();

            // A real SQLite trigger rejects a late row/event write after the
            // bytes and manifest were valid, not an artificially bad bundle.
            target
                .conn
                .execute_batch(&format!(
                    "CREATE TRIGGER reject_import BEFORE INSERT ON {rejected_table}
                     BEGIN SELECT RAISE(ABORT, 'fixture-sqlite-insert-rejected'); END;"
                ))
                .unwrap();
            let before = snapshot(&target);
            let staging = target.blob_root().join(".staging");
            fs::create_dir_all(&staging).unwrap();
            let foreign = staging.join("separate-staging-operation.part");
            fs::write(&foreign, b"foreign staging bytes").unwrap();

            assert!(
                matches!(
                    target.import_project_bundle_inspected(&bundle, &inspected.fingerprint),
                    Err(StorageError::Sql(_))
                ),
                "SQLite {rejected_table} trigger did not reject import"
            );
            assert_eq!(snapshot(&target), before);
            for blob in &manifest.blobs {
                assert!(
                    target.verify_blob(&blob.sha256).is_err(),
                    "SQLite {rejected_table} rejection orphaned a captured CAS member"
                );
            }
            let retained: Vec<_> = fs::read_dir(&staging)
                .unwrap()
                .map(|entry| entry.unwrap().path())
                .collect();
            assert_eq!(retained, vec![foreign.clone()]);
            assert_eq!(fs::read(foreign).unwrap(), b"foreign staging bytes");
            drop(target);
            let reopened = Store::open(&database).unwrap();
            assert_eq!(snapshot(&reopened), before);
            assert!(reopened.list_projects(10).unwrap().is_empty());
        }
    }

    #[test]
    fn existing_later_cas_conflict_must_not_publish_unreferenced_first_member() {
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        assert_eq!(manifest.blobs.len(), 2);
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let inspected = target
            .inspect_project_bundle_with_fingerprint(&bundle)
            .unwrap();
        let before = snapshot(&target);
        let first = &manifest.blobs[0];
        let last = &manifest.blobs[1];
        let foreign_path = target
            .blob_root()
            .join("sha256")
            .join(&last.sha256[..2])
            .join(&last.sha256);
        fs::create_dir_all(foreign_path.parent().unwrap()).unwrap();
        let foreign_original = b"foreign pre-existing CAS leaf; preserve";
        fs::write(&foreign_path, foreign_original).unwrap();

        let outcome = target.import_project_bundle_inspected(&bundle, &inspected.fingerprint);
        assert!(
            outcome.is_err(),
            "conflicting destination CAS must fail closed"
        );
        assert_eq!(
            snapshot(&target),
            before,
            "project/journal/receipts changed"
        );
        assert!(target.list_projects(10).unwrap().is_empty());
        assert_eq!(fs::read(&foreign_path).unwrap(), foreign_original);
        assert!(
            target.verify_blob(&first.sha256).is_err(),
            "an earlier blob was published although the entire project import failed"
        );
        let stage = target.blob_root().join(".staging");
        assert!(
            fs::read_dir(stage).unwrap().next().is_none(),
            "private captures leaked after failed import"
        );
    }

    #[test]
    fn verified_matching_existing_cas_still_allows_complete_bundle_import() {
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let first = &manifest.blobs[0];
        let prior = fs::read(bundle.join(&first.relative_path)).unwrap();
        let descriptor = target
            .ingest_blob_file(bundle.join(&first.relative_path))
            .unwrap();
        assert_eq!(descriptor.sha256, first.sha256);
        let inspected = target
            .inspect_project_bundle_with_fingerprint(&bundle)
            .unwrap();
        let imported = target
            .import_project_bundle_inspected(&bundle, &inspected.fingerprint)
            .unwrap();
        assert_eq!(imported.id, manifest.backup.project.id);
        assert_ne!(imported.generation, manifest.backup.project.generation);
        assert_eq!(target.read_blob(&first.sha256, 4096).unwrap(), prior);
        for blob in &manifest.blobs {
            assert_eq!(
                target.read_blob(&blob.sha256, 4096).unwrap(),
                fs::read(bundle.join(&blob.relative_path)).unwrap()
            );
        }
    }

    #[test]
    fn late_member_change_discards_all_private_captures_without_publishing() {
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        target.validate_bundle(&bundle, &manifest).unwrap();
        let before = snapshot(&target);
        let stage_root = target.blob_root().join(".staging");
        fs::create_dir(&stage_root).unwrap();
        let foreign = stage_root.join("another-operation.part");
        fs::write(&foreign, b"preserve other operation").unwrap();
        let last = manifest.blobs.last().unwrap();
        let changed = vec![b'Z'; last.size_bytes as usize];
        let changed_digest = hex::encode(Sha256::digest(&changed));
        assert_ne!(changed_digest, last.sha256);
        fs::write(bundle.join(&last.relative_path), changed).unwrap();
        assert!(matches!(
            target.import_validated_bundle(&bundle, &manifest, &StructuralBundleAdmission),
            Err(StorageError::InvalidBackup(_))
        ));
        assert_eq!(snapshot(&target), before);
        for blob in &manifest.blobs {
            assert!(target.read_blob(&blob.sha256, 4096).is_err());
        }
        assert!(target.read_blob(&changed_digest, 4096).is_err());
        let remaining: Vec<_> = fs::read_dir(&stage_root)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        assert_eq!(remaining, vec![foreign.clone()]);
        assert_eq!(fs::read(foreign).unwrap(), b"preserve other operation");
    }

    #[test]
    fn validated_private_captures_do_not_reload_removed_bundle_members() {
        let directory = tempfile::tempdir().unwrap();
        let (bundle, manifest) = bundle_fixture(directory.path());
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        target.validate_bundle(&bundle, &manifest).unwrap();
        let expected: Vec<_> = manifest
            .blobs
            .iter()
            .map(|blob| {
                (
                    blob.sha256.clone(),
                    fs::read(bundle.join(&blob.relative_path)).unwrap(),
                )
            })
            .collect();
        let captures = target
            .capture_bundle_blobs(&bundle, &manifest, &StructuralBundleAdmission)
            .unwrap();
        fs::remove_dir_all(&bundle).unwrap();
        for capture in &captures {
            target.publish_file_import(capture).unwrap();
        }
        drop(captures);
        let imported = target.import_project(&manifest.backup).unwrap();
        assert_eq!(imported.assets, manifest.backup.project.assets);
        assert_ne!(imported.generation, manifest.backup.project.generation);
        for (digest, bytes) in expected {
            assert_eq!(target.read_blob(&digest, 4096).unwrap(), bytes);
        }
        assert!(
            fs::read_dir(target.blob_root().join(".staging"))
                .unwrap()
                .next()
                .is_none()
        );
    }

    #[test]
    fn oversized_manifest_is_rejected_before_parsing_or_publication() {
        let directory = tempfile::tempdir().unwrap();
        let bundle = directory.path().join("oversized");
        fs::create_dir(&bundle).unwrap();
        File::create(bundle.join("manifest.json"))
            .unwrap()
            .set_len(MAX_BUNDLE_MANIFEST_BYTES + 1)
            .unwrap();
        let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let expected = BundleFingerprint::from_sha256("0".repeat(64)).unwrap();
        assert!(matches!(
            target.inspect_project_bundle_with_fingerprint(&bundle),
            Err(StorageError::BlobTooLarge { .. })
        ));
        assert!(matches!(
            target.import_project_bundle_inspected(&bundle, &expected),
            Err(StorageError::BlobTooLarge { .. })
        ));
        assert!(target.list_projects(10).unwrap().is_empty());
        assert!(fs::read_dir(target.blob_root()).unwrap().next().is_none());
    }

    #[test]
    fn bounded_capture_handles_exact_empty_and_excess_sources_without_publication() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let stage_root = store.blob_root().join(".staging");
        fs::create_dir(&stage_root).unwrap();
        let foreign = stage_root.join("another-operation.part");
        fs::write(&foreign, b"other operation remains").unwrap();
        let source = directory.path().join("source.bin");
        fs::write(&source, b"12345").unwrap();
        assert!(matches!(
            store.stage_file_import_with_limit(&source, 4),
            Err(StorageError::BlobTooLarge { size: 5, limit: 4 }),
        ));
        let unrestricted = store.stage_file_import(&source).unwrap();
        assert_eq!(unrestricted.descriptor().size_bytes, 5);
        assert_eq!(fs::read(unrestricted.path()).unwrap(), b"12345");
        drop(unrestricted);
        fs::write(&source, b"1234").unwrap();
        let exact = store.stage_file_import_with_limit(&source, 4).unwrap();
        let digest = exact.descriptor().sha256.clone();
        assert_eq!(exact.descriptor().size_bytes, 4);
        assert_eq!(fs::read(exact.path()).unwrap(), b"1234");
        assert!(store.read_blob(&digest, 16).is_err());
        drop(exact);
        fs::write(&source, b"").unwrap();
        let empty = store.stage_file_import_with_limit(&source, 0).unwrap();
        assert_eq!(empty.descriptor().size_bytes, 0);
        assert_eq!(empty.descriptor().sha256, hex::encode(Sha256::digest(b"")));
        drop(empty);
        let remaining: Vec<_> = fs::read_dir(&stage_root)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        assert_eq!(remaining, vec![foreign.clone()]);
        assert_eq!(fs::read(foreign).unwrap(), b"other operation remains");
        assert!(store.list_projects(10).unwrap().is_empty());
    }

    #[test]
    fn late_short_and_growing_members_fail_before_any_cas_publication() {
        for growing in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let (bundle, manifest) = bundle_fixture(directory.path());
            let mut target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
            target.validate_bundle(&bundle, &manifest).unwrap();
            let before = snapshot(&target);
            let member = manifest.blobs.last().unwrap();
            let changed_len = if growing {
                member.size_bytes + 1
            } else {
                member.size_bytes - 1
            };
            let changed = vec![b'X'; changed_len as usize];
            let changed_digest = hex::encode(Sha256::digest(&changed));
            fs::write(bundle.join(&member.relative_path), changed).unwrap();
            let result =
                target.import_validated_bundle(&bundle, &manifest, &StructuralBundleAdmission);
            if growing {
                assert!(matches!(
                    result, Err(StorageError::BlobTooLarge { size, limit })
                    if size == changed_len && limit == member.size_bytes
                ));
            } else {
                assert!(matches!(result, Err(StorageError::InvalidBackup(_))));
            }
            assert_eq!(snapshot(&target), before);
            for blob in &manifest.blobs {
                assert!(target.read_blob(&blob.sha256, 4096).is_err());
            }
            assert!(target.read_blob(&changed_digest, 4096).is_err());
            let staging = target.blob_root().join(".staging");
            assert!(!staging.exists() || fs::read_dir(staging).unwrap().next().is_none());
        }
    }

    #[test]
    fn portable_transfer_limits_growth_during_the_actual_file_read() {
        struct GrowingFile {
            input: File,
            append: File,
            grown: bool,
            bytes_read: usize,
        }
        impl Read for GrowingFile {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                if !self.grown {
                    self.append.write_all(&[b'X'; 4096])?;
                    self.append.flush()?;
                    self.grown = true;
                }
                let count = self.input.read(buffer)?;
                self.bytes_read += count;
                Ok(count)
            }
        }
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("growing.bin");
        fs::write(&path, [b'A'; 64]).unwrap();
        let limit = fs::metadata(&path).unwrap().len();
        let mut source = GrowingFile {
            input: File::open(&path).unwrap(),
            append: OpenOptions::new().append(true).open(&path).unwrap(),
            grown: false,
            bytes_read: 0,
        };
        let mut captured = Vec::new();
        let result = transfer_portable_bytes(&mut source, &mut captured, limit);
        assert!(matches!(
            result,
            Err(StorageError::BlobTooLarge {
                size: 65,
                limit: 64
            })
        ));
        assert!(source.grown);
        assert_eq!(fs::metadata(&path).unwrap().len(), 4160);
        assert_eq!(source.bytes_read, 65);
        assert!(captured.len() as u64 <= limit);
    }

    #[test]
    fn publication_rechecks_private_and_existing_cas_with_the_captured_size_limit() {
        for changed_existing in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let store = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
            let input = directory.path().join("input.bin");
            let original: &[u8] = b"original immutable bytes";
            fs::write(&input, original).unwrap();

            let first_capture = store.stage_file_import(&input).unwrap();
            let descriptor = first_capture.descriptor().clone();
            let first_stage_path = first_capture.path().to_path_buf();
            let destination = store.blob_path(&descriptor.sha256).unwrap();
            assert_eq!(descriptor.size_bytes, original.len() as u64);
            assert_eq!(fs::read(&first_stage_path).unwrap(), original);

            let captured = if changed_existing {
                store.publish_file_import(&first_capture).unwrap();
                assert_eq!(fs::read(&destination).unwrap(), original);

                // The first publication may hard-link staging into CAS. Capture
                // the unchanged input independently before changing the CAS file.
                let second_capture = store.stage_file_import(&input).unwrap();
                assert_ne!(second_capture.path(), first_stage_path.as_path());
                assert_eq!(&second_capture.descriptor().sha256, &descriptor.sha256);
                assert_eq!(
                    second_capture.descriptor().size_bytes,
                    descriptor.size_bytes
                );
                assert_eq!(fs::read(second_capture.path()).unwrap(), original);
                drop(first_capture);
                assert!(!first_stage_path.exists());
                second_capture
            } else {
                first_capture
            };

            let stage_path = captured.path().to_path_buf();
            assert_ne!(stage_path, destination);
            assert_eq!(&captured.descriptor().sha256, &descriptor.sha256);
            assert_eq!(captured.descriptor().size_bytes, descriptor.size_bytes);
            assert_eq!(fs::read(&stage_path).unwrap(), original);
            assert_eq!(fs::read(&input).unwrap(), original);

            let mut changed = original.to_vec();
            changed.extend_from_slice(b" appended after capture");
            let changed_path = if changed_existing {
                &destination
            } else {
                &stage_path
            };
            fs::write(changed_path, &changed).unwrap();
            assert_eq!(fs::read(changed_path).unwrap(), changed);
            assert!(fs::metadata(changed_path).unwrap().len() > descriptor.size_bytes);
            if changed_existing {
                // Prove the next call can pass its private-capture check and
                // reaches validation of the distinct, changed existing CAS file.
                assert_eq!(fs::read(&stage_path).unwrap(), original);
                assert_eq!(
                    fs::metadata(&stage_path).unwrap().len(),
                    descriptor.size_bytes
                );
            } else {
                assert!(!destination.exists());
            }

            let result = store.publish_file_import(&captured);
            assert!(matches!(
                result,
                Err(StorageError::BlobTooLarge { limit, .. })
                    if limit == descriptor.size_bytes
            ));
            assert_eq!(&captured.descriptor().sha256, &descriptor.sha256);
            assert_eq!(captured.descriptor().size_bytes, descriptor.size_bytes);
            assert_eq!(fs::read(&input).unwrap(), original);
            if changed_existing {
                assert_eq!(fs::read(&stage_path).unwrap(), original);
                assert_eq!(
                    fs::metadata(&stage_path).unwrap().len(),
                    descriptor.size_bytes
                );
                assert_eq!(fs::read(&destination).unwrap(), changed);
            } else {
                assert_eq!(fs::read(&stage_path).unwrap(), changed);
                assert!(!destination.exists());
            }

            drop(captured);
            assert!(!stage_path.exists());
            assert!(!first_stage_path.exists());
            if changed_existing {
                // The rejected attempt cannot remove or repair a shared object.
                assert_eq!(fs::read(&destination).unwrap(), changed);
            } else {
                assert!(!destination.exists());
            }
            assert!(store.list_projects(10).unwrap().is_empty());
        }
    }

    // The same regression bodies now call the bounded product helpers.
    fn reviewed_hash_source(path: &Path, limit: u64) -> Result<BlobDescriptor> {
        hash_portable_file(path, limit)
    }

    fn reviewed_text_source(path: &Path, limit: u64) -> Result<()> {
        inspect_portable_file(path, "portable text-like asset", limit).map(|_| ())
    }

    fn reviewed_copy_source(source: &Path, target: &Path, limit: u64) -> Result<()> {
        copy_portable_file(source, target, limit).map(|_| ())
    }

    fn corrupt_journal_export_case(corruption: &str) {
        let directory = tempfile::tempdir().unwrap();
        let mut source = Store::open(directory.path().join("source/state.sqlite3")).unwrap();
        let target = Store::open(directory.path().join("target/state.sqlite3")).unwrap();
        let mut project = source.create_named_project("Journal integrity").unwrap();
        for index in 0..2 {
            project = source
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("rename-{index}"),
                    &Change::RenameProject {
                        title: format!("Journal revision {}", index + 1),
                    },
                )
                .unwrap()
                .project;
        }
        let valid = source
            .export_project_at(project.id, &project.stamp())
            .unwrap();
        target.inspect_import(&valid).unwrap();
        match corruption {
            "missing" => {
                source
                    .conn
                    .execute("DELETE FROM events WHERE revision=1", [])
                    .unwrap();
            }
            "gap" => {
                source
                    .conn
                    .execute("UPDATE events SET revision=99 WHERE revision=1", [])
                    .unwrap();
            }
            "timestamp" => {
                source
                    .conn
                    .execute("UPDATE events SET created_at='' WHERE revision=1", [])
                    .unwrap();
            }
            _ => unreachable!(),
        }
        let before = snapshot(&source);
        let destination = directory.path().join("stamped-bundle");
        let legacy_destination = directory.path().join("legacy-bundle");
        let raw = source.export_project_at(project.id, &project.stamp());
        let legacy_raw = source.export_project(project.id);
        let bundle = source.export_project_bundle_at(project.id, &project.stamp(), &destination);
        let legacy_bundle = source.export_project_bundle(project.id, &legacy_destination);
        for backup in [&raw, &legacy_raw]
            .into_iter()
            .filter_map(|result| result.as_ref().ok())
        {
            assert!(matches!(
                target.inspect_import(backup),
                Err(StorageError::InvalidBackup(_))
            ));
            eprintln!("PORTABILITY_OBSERVED_UNIMPORTABLE_BACKUP corruption={corruption}");
        }
        for manifest in [&bundle, &legacy_bundle]
            .into_iter()
            .filter_map(|result| result.as_ref().ok())
        {
            assert!(matches!(
                target.inspect_import(&manifest.backup),
                Err(StorageError::InvalidBackup(_))
            ));
            eprintln!("PORTABILITY_OBSERVED_UNIMPORTABLE_BUNDLE corruption={corruption}");
        }
        assert_eq!(snapshot(&source), before);
        assert!(target.list_projects(10).unwrap().is_empty());
        assert!(
            matches!(raw, Err(StorageError::InvalidBackup(_)))
                && matches!(legacy_raw, Err(StorageError::InvalidBackup(_)))
                && matches!(bundle, Err(StorageError::InvalidBackup(_)))
                && matches!(legacy_bundle, Err(StorageError::InvalidBackup(_))),
            "exports must reject a journal that their own import validator rejects: {corruption}"
        );
        assert!(!destination.exists() && !legacy_destination.exists());
    }

    #[test]
    fn export_rejects_missing_journal_entry() {
        corrupt_journal_export_case("missing");
    }

    #[test]
    fn export_rejects_noncontiguous_journal() {
        corrupt_journal_export_case("gap");
    }

    #[test]
    fn export_rejects_untimestamped_journal() {
        corrupt_journal_export_case("timestamp");
    }

    fn append_after_size_review(path: &Path) -> u64 {
        fs::write(path, b"ordinary portable source").unwrap();
        let reviewed_size = fs::metadata(path).unwrap().len();
        let mut writer = OpenOptions::new().append(true).open(path).unwrap();
        writer
            .write_all(b" bytes appended after size review")
            .unwrap();
        writer.sync_all().unwrap();
        reviewed_size
    }

    #[test]
    fn portable_hash_rejects_source_growth_past_reviewed_budget() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.bin");
        fs::write(&source, b"control").unwrap();
        let control = reviewed_hash_source(&source, 7).unwrap();
        assert_eq!(control.size_bytes, 7);
        assert_eq!(control.sha256, hex::encode(Sha256::digest(b"control")));
        let limit = append_after_size_review(&source);
        let result = reviewed_hash_source(&source, limit);
        if let Ok(observed) = &result {
            eprintln!(
                "PORTABILITY_OBSERVED_UNBOUNDED_HASH reviewed={limit} read={}",
                observed.size_bytes
            );
            assert!(observed.size_bytes > limit);
        }
        assert!(
            matches!(result, Err(StorageError::BlobTooLarge { limit: actual, .. }) if actual == limit)
        );
    }

    #[test]
    fn portable_text_scan_rejects_source_growth_past_reviewed_budget() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.txt");
        fs::write(&source, b"control").unwrap();
        reviewed_text_source(&source, 7).unwrap();
        let limit = append_after_size_review(&source);
        let result = reviewed_text_source(&source, limit);
        if result.is_ok() {
            eprintln!(
                "PORTABILITY_OBSERVED_UNBOUNDED_TEXT_SCAN reviewed={limit} accepted={}",
                fs::metadata(&source).unwrap().len()
            );
        }
        assert!(
            matches!(result, Err(StorageError::BlobTooLarge { limit: actual, .. }) if actual == limit)
        );
    }

    #[test]
    fn portable_copy_rejects_source_growth_past_reviewed_budget() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.bin");
        let control = directory.path().join("control.bin");
        fs::write(&source, b"control").unwrap();
        reviewed_copy_source(&source, &control, 7).unwrap();
        assert_eq!(fs::read(control).unwrap(), b"control");
        let limit = append_after_size_review(&source);
        let target = directory.path().join("rejected.bin");
        let result = reviewed_copy_source(&source, &target, limit);
        if result.is_ok() {
            let copied = fs::metadata(&target).unwrap().len();
            eprintln!("PORTABILITY_OBSERVED_UNBOUNDED_COPY reviewed={limit} copied={copied}");
            assert!(copied > limit);
        }
        assert!(
            matches!(result, Err(StorageError::BlobTooLarge { limit: actual, .. }) if actual == limit)
        );
    }
}

#[cfg(test)]
mod portable_credential_stream_tests {
    use super::*;

    struct ShortReads<'a> {
        remaining: &'a [u8],
        stride: usize,
    }

    impl Read for ShortReads<'_> {
        fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
            let count = output.len().min(self.stride).min(self.remaining.len());
            output[..count].copy_from_slice(&self.remaining[..count]);
            self.remaining = &self.remaining[count..];
            Ok(count)
        }
    }

    #[test]
    fn existing_credential_patterns_are_rejected_under_every_short_read_width() {
        let mut markers = CREDENTIAL_PREFIXES
            .iter()
            .map(|(prefix, suffix)| {
                let mut marker = prefix.to_vec();
                marker.extend(std::iter::repeat_n(b'A', *suffix));
                marker
            })
            .collect::<Vec<_>>();
        markers.push(PRIVATE_KEY_MARKER.to_vec());
        markers.push([b"AKIA".as_slice(), &[b'A'; ACCESS_KEY_WIDTH - 4]].concat());
        for marker in markers {
            assert!(contains_credential_like_material(&marker));
            for stride in 1..=credential_pattern_width() + 1 {
                let mut bytes = vec![0xff; 17];
                bytes.extend_from_slice(&marker);
                bytes.extend_from_slice(&[0, 0x80]);
                let input = ShortReads {
                    remaining: &bytes,
                    stride,
                };
                let result = transfer_portable_bytes_with_credential_check(
                    input,
                    std::io::sink(),
                    bytes.len() as u64,
                    Some("portable asset"),
                );
                assert!(matches!(result, Err(StorageError::InvalidBackup(_))));
            }
        }
    }
}
