use super::*;

// Every service bundle entry uses the same static-SVG policy as direct
// file import. Every MIME declaration is checked, and bytes are sniffed
// regardless of metadata. Asset names remain display labels, not source paths;
// neither metadata nor an earlier inspection grants an exemption.
pub(super) struct SvgBundleAdmission;

impl motionwright_storage::BundleAssetAdmission for SvgBundleAdmission {
    fn prefix_limit(&self) -> usize {
        (MAX_SVG_IMPORT_BYTES + 1) as usize
    }

    fn validate(
        &self,
        prefix: &[u8],
        actual_size: u64,
        aliases: &[&motionwright_domain::Asset],
    ) -> StorageResult<()> {
        if prefix.len() as u64 != actual_size.min(MAX_SVG_IMPORT_BYTES + 1) {
            return Err(invalid_import(
                "portable asset inspection prefix is incomplete",
            ));
        }
        if aliases.is_empty() {
            return Err(invalid_import(
                "portable asset has no declared project alias",
            ));
        }
        // These are exactly the MIME-dependent branches of the shared
        // reader: declared SVG, text permitted by the UTF-16/32 sniffer,
        // and opaque media. Preserve every restriction without reparsing
        // up to eight MiB once for every equivalent alias in a manifest.
        let mut validated_classes = [false; 3];
        for alias in aliases {
            let class = if alias.media_type.eq_ignore_ascii_case("image/svg+xml") {
                0
            } else if alias.media_type.starts_with("text/") {
                1
            } else {
                2
            };
            if validated_classes[class] {
                continue;
            }
            validate_importable_asset_reader(
                std::io::Cursor::new(prefix),
                actual_size,
                &alias.media_type,
                false,
            )?;
            validated_classes[class] = true;
        }
        Ok(())
    }
}

impl StudioService {
    pub fn export_project_at(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
    ) -> StorageResult<ProjectBackup> {
        self.store.lock().export_project_at(id, expected)
    }

    pub fn export_project_bundle_at(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        destination: impl AsRef<Path>,
    ) -> StorageResult<ProjectBundleManifest> {
        self.export_project_bundle_at_checked(id, expected, destination, || Ok(()))
    }

    pub fn export_project_bundle_at_checked<P>(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        destination: impl AsRef<Path>,
        admit_publication: impl FnOnce() -> std::io::Result<P>,
    ) -> StorageResult<ProjectBundleManifest> {
        self.export_bundle_snapshot_checked(
            id,
            Some(expected),
            destination.as_ref(),
            &crate::font_portability::FontBundleExportAdmission,
            admit_publication,
        )
    }

    pub(super) fn export_bundle_snapshot(
        &self,
        id: Uuid,
        expected: Option<&RevisionStamp>,
        destination: &Path,
        admission: &impl motionwright_storage::BundleAssetAdmission,
    ) -> StorageResult<ProjectBundleManifest> {
        self.export_bundle_snapshot_checked(id, expected, destination, admission, || Ok(()))
    }

    fn export_bundle_snapshot_checked<P>(
        &self,
        id: Uuid,
        expected: Option<&RevisionStamp>,
        destination: &Path,
        admission: &impl motionwright_storage::BundleAssetAdmission,
        admit_publication: impl FnOnce() -> std::io::Result<P>,
    ) -> StorageResult<ProjectBundleManifest> {
        // The snapshot and CAS location survive the guard. Content inspection,
        // file copying and publication must not serialize unrelated Studio work.
        let prepared = self
            .store
            .lock()
            .prepare_project_bundle_export(id, expected)?;
        prepared.publish_with_admission_checked(destination, admission, admit_publication)
    }

    pub fn verify_project_bundle_export_at(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        source: impl AsRef<Path>,
    ) -> StorageResult<motionwright_storage::VerifiedProjectBundle> {
        self.verify_bundle_snapshot(id, expected, source.as_ref(), &SvgBundleAdmission)
    }

    fn verify_bundle_snapshot(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        source: &Path,
        admission: &impl motionwright_storage::BundleAssetAdmission,
    ) -> StorageResult<motionwright_storage::VerifiedProjectBundle> {
        let prepared = self
            .store
            .lock()
            .prepare_project_bundle_export(id, Some(expected))?;
        prepared.verify_existing_with_admission(source, admission)
    }

    pub fn inspect_project_bundle_with_fingerprint(
        &self,
        source: impl AsRef<Path>,
    ) -> StorageResult<InspectedProjectBundle> {
        let source = source.as_ref();
        // Serialize the bounded archive capture and conversion with other Store work.
        let store = self.store.lock();
        if std::fs::symlink_metadata(source)?.is_file() {
            let migration = crate::legacy_project_archive::from_bytes(
                Store::read_project_archive_source(source)?,
                None,
            )?;
            return store.inspect_migrated_project_archive(
                migration.project,
                &migration.originals,
                migration.fingerprint,
                migration.notices,
                &SvgBundleAdmission,
            );
        }
        store.inspect_project_bundle_with_fingerprint_and_admission(source, &SvgBundleAdmission)
    }

    pub fn recover_project_bundle_import(
        &self,
        request: &BundleImportRecoveryRequest,
    ) -> StorageResult<Option<BundleImportCommit>> {
        self.store.lock().recover_project_bundle_import(request)
    }

    pub fn import_project_bundle_inspected(
        &self,
        source: impl AsRef<Path>,
        expected: &BundleFingerprint,
    ) -> StorageResult<Project> {
        let source = source.as_ref();
        let mut store = self.store.lock();
        if std::fs::symlink_metadata(source)?.is_file() {
            let migration = crate::legacy_project_archive::from_bytes(
                Store::read_project_archive_source(source)?,
                Some(expected),
            )?;
            return store.import_migrated_project_archive(
                migration.project,
                &migration.originals,
                expected,
                &SvgBundleAdmission,
            );
        }
        store.import_project_bundle_inspected_with_admission(source, expected, &SvgBundleAdmission)
    }
}

#[cfg(test)]
mod svg_bundle_admission_tests {
    use super::*;
    use motionwright_domain::Asset;
    use motionwright_storage::BundleAssetAdmission;

    fn alias(name: &str, media_type: &str) -> Asset {
        Asset {
            id: Uuid::now_v7(),
            name: name.into(),
            media_type: media_type.into(),
            content_sha256: None,
            source_revision: None,
        }
    }

    #[test]
    fn bundle_copy_allows_a_new_revision_without_changing_the_captured_backup() {
        struct EditDuringAdmission<'a> {
            service: &'a StudioService,
            original: &'a Project,
        }

        impl BundleAssetAdmission for EditDuringAdmission<'_> {
            fn prefix_limit(&self) -> usize {
                SvgBundleAdmission.prefix_limit()
            }

            fn validate(&self, prefix: &[u8], size: u64, aliases: &[&Asset]) -> StorageResult<()> {
                SvgBundleAdmission.validate(prefix, size, aliases)?;
                // Probe the exact shared mutex so this case fails promptly if
                // a future exporter still holds it during the real copy path.
                let store_is_available = self.service.store.try_lock().is_some();
                assert!(
                    store_is_available,
                    "portable blob validation holds the Store"
                );
                self.service.apply(
                    self.original.id,
                    &self.original.stamp(),
                    &format!("peer-edit-during-portable-copy-r{}", self.original.revision),
                    &Change::AddScene {
                        name: format!("Peer edit r{}", self.original.revision),
                        objective: "Changes only the live project".into(),
                        duration_seconds: 2,
                    },
                )?;
                Ok(())
            }
        }

        let root = tempfile::tempdir().unwrap();
        let state = root.path().join("live.sqlite3");
        let service = StudioService::open(&state).unwrap();
        let project = service.create_project("Portable snapshot").unwrap();
        let asset = root.path().join("source.txt");
        std::fs::write(&asset, b"immutable original in the backup").unwrap();
        let original = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "import-portable-original",
                &asset,
                "source.txt".into(),
                "text/plain".into(),
            )
            .unwrap()
            .project;
        let captured = service
            .export_project_at(original.id, &original.stamp())
            .unwrap();
        let bundle = root.path().join("exported");
        let exported = service
            .export_bundle_snapshot(
                original.id,
                Some(&original.stamp()),
                &bundle,
                &EditDuringAdmission {
                    service: &service,
                    original: &original,
                },
            )
            .unwrap();
        let current = service.project(original.id).unwrap();
        assert_eq!(current.revision, original.revision + 1);
        assert_eq!(
            current.scenes.last().unwrap().name,
            format!("Peer edit r{}", original.revision)
        );
        assert_eq!(exported.backup.project, captured.project);
        assert_eq!(exported.backup.events, captured.events);
        assert_eq!(exported.blobs.len(), 1);
        assert_eq!(
            std::fs::read(bundle.join(&exported.blobs[0].relative_path)).unwrap(),
            b"immutable original in the backup"
        );

        let restored_path = root.path().join("isolated").join("restored.sqlite3");
        let restored = StudioService::open(&restored_path)
            .unwrap()
            .import_project_bundle(&bundle)
            .unwrap();
        let reopened = StudioService::open(&restored_path)
            .unwrap()
            .project(restored.id)
            .unwrap();
        assert_eq!(restored, reopened);
        assert_eq!(reopened.revision, original.revision);
        assert_eq!(reopened.scenes, original.scenes);
        assert_ne!(reopened.generation, original.generation);

        let next_bundle = root.path().join("current-revision");
        service
            .export_project_bundle_at(current.id, &current.stamp(), &next_bundle)
            .unwrap();
        let verified = service
            .verify_bundle_snapshot(
                current.id,
                &current.stamp(),
                &next_bundle,
                &EditDuringAdmission {
                    service: &service,
                    original: &current,
                },
            )
            .unwrap();
        assert_eq!(verified.revision, current.revision);
        assert_eq!(verified.generation, current.generation);
        assert_eq!(verified.blob_count, 1);
        assert_eq!(
            service.project(current.id).unwrap().revision,
            current.revision + 1
        );
    }

    #[test]
    fn every_alias_can_tighten_svg_policy_in_either_order() {
        let bytes = b"ordinary opaque bytes";
        let opaque = alias("opaque.bin", "application/octet-stream");
        assert!(
            SvgBundleAdmission
                .validate(bytes, bytes.len() as u64, &[&opaque])
                .is_ok()
        );
        let restricted = alias("declared.bin", "image/svg+xml");
        for aliases in [[&opaque, &restricted], [&restricted, &opaque]] {
            assert!(
                SvgBundleAdmission
                    .validate(bytes, bytes.len() as u64, &aliases)
                    .is_err()
            );
        }
        // Direct import permits an independently chosen display name. A
        // bundle must not reinterpret that label as an original filesystem path.
        let label_only = alias("display-label.svg", "application/octet-stream");
        assert!(
            SvgBundleAdmission
                .validate(bytes, bytes.len() as u64, &[&label_only])
                .is_ok()
        );
    }

    #[test]
    fn encoded_text_alias_cannot_exempt_a_restrictive_alias() {
        let bytes: Vec<_> = "  ordinary encoded text"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect();
        let text = alias("text-label", "text/plain");
        let opaque = alias("opaque-label", "application/octet-stream");
        let svg = alias("vector-label", "image/svg+xml");
        assert!(
            SvgBundleAdmission
                .validate(&bytes, bytes.len() as u64, &[&text, &text])
                .is_ok()
        );
        for aliases in [
            [&text, &opaque],
            [&opaque, &text],
            [&text, &svg],
            [&svg, &text],
        ] {
            assert!(
                SvgBundleAdmission
                    .validate(&bytes, bytes.len() as u64, &aliases)
                    .is_err()
            );
        }
    }

    #[test]
    fn static_svg_accepts_exact_budget_and_rejects_one_byte_more() {
        let start = b"<svg xmlns=\"http://www.w3.org/2000/svg\">";
        let end = b"</svg>";
        let mut bytes = start.to_vec();
        bytes.resize(MAX_SVG_IMPORT_BYTES as usize - end.len(), b' ');
        bytes.extend_from_slice(end);
        for metadata in [
            alias("vector.svg", "image/svg+xml"),
            alias("mislabeled.bin", "application/octet-stream"),
        ] {
            assert!(
                SvgBundleAdmission
                    .validate(&bytes, bytes.len() as u64, &[&metadata])
                    .is_ok()
            );
            let mut excess = bytes.clone();
            excess.push(b' ');
            assert!(matches!(
                SvgBundleAdmission.validate(&excess, excess.len() as u64, &[&metadata]),
                Err(StorageError::BlobTooLarge { .. })
            ));
        }
    }

    #[test]
    fn truncated_whitespace_or_bom_prefix_never_admits_later_svg_as_binary() {
        let metadata = alias("opaque.bin", "application/octet-stream");
        for bom in [false, true] {
            let mut prefix = vec![b' '; (MAX_SVG_IMPORT_BYTES + 1) as usize];
            if bom {
                prefix[..3].copy_from_slice(&[0xef, 0xbb, 0xbf]);
            }
            assert!(
                SvgBundleAdmission
                    .validate(&prefix, MAX_SVG_IMPORT_BYTES + 100, &[&metadata])
                    .is_err()
            );
        }
    }

    #[test]
    fn bounded_prefix_preserves_large_opaque_binary_admission() {
        let metadata = alias("opaque.bin", "application/octet-stream");
        let mut prefix = vec![0xff; (MAX_SVG_IMPORT_BYTES + 1) as usize];
        prefix[..4].copy_from_slice(b"\x89PNG");
        assert!(
            SvgBundleAdmission
                .validate(&prefix, MAX_SVG_IMPORT_BYTES * 32, &[&metadata])
                .is_ok()
        );
    }

    #[test]
    fn incomplete_prefix_is_not_treated_as_complete_asset() {
        let metadata = alias("vector.svg", "image/svg+xml");
        assert!(
            SvgBundleAdmission
                .validate(b"<svg/>", 200, &[&metadata])
                .is_err()
        );
    }
}
