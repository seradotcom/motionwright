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
        for alias in aliases {
            validate_importable_asset_reader(
                std::io::Cursor::new(prefix),
                actual_size,
                &alias.media_type,
                false,
            )?;
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
        self.store
            .lock()
            .export_project_bundle_at(id, expected, destination)
    }

    pub fn inspect_project_bundle_with_fingerprint(
        &self,
        source: impl AsRef<Path>,
    ) -> StorageResult<InspectedProjectBundle> {
        self.store
            .lock()
            .inspect_project_bundle_with_fingerprint_and_admission(source, &SvgBundleAdmission)
    }

    pub fn import_project_bundle_inspected(
        &self,
        source: impl AsRef<Path>,
        expected: &BundleFingerprint,
    ) -> StorageResult<Project> {
        self.store
            .lock()
            .import_project_bundle_inspected_with_admission(source, expected, &SvgBundleAdmission)
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
