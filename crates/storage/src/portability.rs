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
        let source = source.as_ref();
        let bytes = read_bundle_manifest_bytes(source)?;
        let fingerprint = BundleFingerprint::of_manifest_bytes(&bytes);
        let manifest: ProjectBundleManifest = serde_json::from_slice(&bytes)?;
        let plan = self.validate_bundle(source, &manifest)?;
        Ok(InspectedProjectBundle { plan, fingerprint })
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
        self.validate_bundle(source, &manifest)?;
        self.import_validated_bundle(source, &manifest)
    }

    fn import_validated_bundle(
        &mut self,
        source: &Path,
        manifest: &ProjectBundleManifest,
    ) -> Result<Project> {
        // Capture and validate every member before publishing even the first
        // member. A path can change after the earlier inspection.
        let staged = self.capture_bundle_blobs(source, manifest)?;
        for captured in &staged {
            self.publish_file_import(captured)?;
        }
        drop(staged);
        self.import_project(&manifest.backup)
    }

    fn capture_bundle_blobs(
        &self,
        source: &Path,
        manifest: &ProjectBundleManifest,
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
            if project_blob_requires_secret_scan(&manifest.backup.project, &blob.sha256) {
                ensure_secret_free_file(captured.path(), "captured portable text-like asset")?;
            }
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
        tx.commit()?;
        Ok(backup)
    }

    pub(super) fn export_captured_bundle(
        &self,
        backup: ProjectBackup,
        destination: &Path,
    ) -> Result<ProjectBundleManifest> {
        if destination.exists() {
            return Err(StorageError::DestinationExists);
        }
        ensure_backup_secret_free(&backup)?;
        let mut digests = BTreeSet::new();
        for asset in &backup.project.assets {
            let digest = asset.content_sha256.as_deref().ok_or_else(|| {
                StorageError::InvalidBackup(format!("asset {} has no content digest", asset.id))
            })?;
            validate_digest(digest)?;
            digests.insert(digest.to_owned());
        }

        let mut blobs = Vec::with_capacity(digests.len());
        for digest in digests {
            let source = self.checked_existing_blob_path(&digest)?;
            if project_blob_requires_secret_scan(&backup.project, &digest) {
                ensure_secret_free_file(&source, "portable text-like asset")?;
            }
            let descriptor = hash_file(&source)?;
            if descriptor.sha256 != digest {
                return Err(StorageError::InvalidBackup(
                    "stored blob digest differs from its content-addressed path".into(),
                ));
            }
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

        let parent = destination.parent().unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent)?;
        let staging = parent.join(format!(".motionwright-export-{}", Uuid::now_v7()));
        fs::create_dir(&staging)?;

        for blob in &manifest.blobs {
            let source = self.checked_existing_blob_path(&blob.sha256)?;
            let target = staging.join(&blob.relative_path);
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(&source, &target)?;
            let copied = hash_file(&target)?;
            if copied.sha256 != blob.sha256 || copied.size_bytes != blob.size_bytes {
                return Err(StorageError::InvalidBackup(
                    "exported blob failed readback verification".into(),
                ));
            }
        }

        let manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
        let manifest_path = staging.join("manifest.json");
        let mut manifest_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&manifest_path)?;
        manifest_file.write_all(&manifest_bytes)?;
        manifest_file.flush()?;
        manifest_file.sync_all()?;
        fs::rename(&staging, destination)?;
        Ok(manifest)
    }
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
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::Asset;
    use rusqlite::types::Value;

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
            target.import_validated_bundle(&bundle, &manifest),
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
        let captures = target.capture_bundle_blobs(&bundle, &manifest).unwrap();
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
            let result = target.import_validated_bundle(&bundle, &manifest);
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
}
