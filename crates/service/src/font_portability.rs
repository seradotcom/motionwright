//! Product font admission for the single storage bundle-export implementation.
//! The parser observes original bytes; the author declaration remains a claim.
use crate::{MAX_BRAND_FONT_BYTES, brand_font_source, invalid_import};
use motionwright_domain::{Asset, FontPortableCopyPermission, Project};
use motionwright_storage::{
    BlobDescriptor, BundleAssetAdmission, Result as StorageResult, StorageError,
};

pub(super) struct FontBundleExportAdmission;

impl BundleAssetAdmission for FontBundleExportAdmission {
    fn prefix_limit(&self) -> usize {
        // Export previously imposed only the storage credential/digest policy
        // on ordinary content. Do not change the separate SVG import policy.
        0
    }

    fn font_export_prefix_limit(&self) -> usize {
        MAX_BRAND_FONT_BYTES as usize
    }

    fn validate(
        &self,
        _prefix: &[u8],
        _actual_size: u64,
        _aliases: &[&Asset],
    ) -> StorageResult<()> {
        // This policy is used only by export. Non-font content keeps the
        // existing storage digest/credential policy; SVG import is unchanged.
        Ok(())
    }

    fn validate_font_export(
        &self,
        project: &Project,
        prefix: &[u8],
        descriptor: &BlobDescriptor,
        aliases: &[&Asset],
    ) -> StorageResult<()> {
        if descriptor.size_bytes > MAX_BRAND_FONT_BYTES {
            return Err(StorageError::BlobTooLarge {
                size: descriptor.size_bytes,
                limit: MAX_BRAND_FONT_BYTES,
            });
        }
        // fonts::inspect needs the complete original, not an arbitrary prefix.
        // Storage supplies these exact bytes from the stream that it hashed.
        if prefix.is_empty() || prefix.len() as u64 != descriptor.size_bytes {
            return Err(invalid_import(
                "portable font inspection requires its complete bounded original",
            ));
        }
        if aliases.is_empty()
            || aliases
                .iter()
                .any(|asset| asset.content_sha256.as_deref() != Some(descriptor.sha256.as_str()))
        {
            return Err(invalid_import(
                "portable font inspection lost its captured asset binding",
            ));
        }
        let metadata = semwright_driver_motion_canvas::fonts::inspect(prefix).map_err(|error| {
            invalid_import(format!("portable font binary inspection failed: {error}"))
        })?;
        let mut inspected_sources = 0_usize;
        for declaration in &project.production_design.font_portable_copies {
            if declaration.content_sha256 != descriptor.sha256
                || declaration.permission != FontPortableCopyPermission::Allowed
                || !aliases.iter().any(|asset| asset.id == declaration.asset_id)
            {
                continue;
            }
            let identifier = format!("asset:{}", declaration.asset_id.hyphenated());
            for right in project
                .visual_language
                .fonts
                .iter()
                .filter(|right| right.source == identifier)
            {
                // Reuse the existing canonical source spelling, ambiguity,
                // bundled-shadow, MIME, rights and digest checks. No second
                // asset: parser or renderer font registry is introduced here.
                let source = brand_font_source(project, &right.family)?;
                if source.asset_id != declaration.asset_id
                    || source.sha256 != descriptor.sha256
                    || !metadata.family_names.contains(&source.family)
                {
                    return Err(invalid_import(
                        "portable font declaration differs from its original family or asset",
                    ));
                }
                inspected_sources += 1;
            }
        }
        if inspected_sources == 0 {
            return Err(invalid_import(
                "portable font requires a current original font source",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::StudioService;
    use motionwright_domain::{
        Change, FontPortableCopyDeclaration, FontRight, RevisionStamp, RightsStatus,
    };
    use sha2::{Digest, Sha256};
    use std::{fs, path::Path};

    const FONT: &[u8] = include_bytes!("../tests/fixtures/managed-font-source.ttf");
    const LICENSE: &[u8] = include_bytes!("../tests/fixtures/managed-font-source-LICENSE");
    const FAMILY: &str = "COLRv1 Static Test Glyphs";

    fn fixture(
        root: &Path,
        bytes: &[u8],
        permission: Option<FontPortableCopyPermission>,
        status: RightsStatus,
        media_type: &str,
    ) -> (StudioService, Project) {
        let service = StudioService::open(root.join("original/state.sqlite3")).unwrap();
        let initial = service
            .create_project("Original editable font export")
            .unwrap();
        let source = root.join("original-source.ttf");
        fs::write(&source, bytes).unwrap();
        let mut project = service
            .import_asset_file(
                initial.id,
                &initial.stamp(),
                "import-original-font",
                &source,
                "Original SDK test font".into(),
                media_type.into(),
            )
            .unwrap()
            .project;
        let font = project.assets[0].clone();
        assert_eq!(
            font.content_sha256.as_deref(),
            Some(hex::encode(Sha256::digest(bytes)).as_str())
        );
        let mut visual_language = project.visual_language.clone();
        visual_language.fonts.push(FontRight {
            family: FAMILY.into(),
            source: format!("asset:{}", font.id.hyphenated()),
            rights_status: status,
        });
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "record-font-use",
                &Change::SetVisualLanguage { visual_language },
            )
            .unwrap()
            .project;
        if let Some(permission) = permission {
            let declaration = FontPortableCopyDeclaration { version: 1, asset_id: font.id, content_sha256: font.content_sha256.unwrap(), permission, origin: "Semwright SDK 01d4a99a8473273fda5c61b7220d6da1bee94786 managed-font-source.ttf test fixture".into(), license: "Apache-2.0; original adjacent license retained in the test fixture".into(), note: Some("Technical font fixture; no human acceptance or commercial-license verification claim".into()) };
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    "record-font-copy",
                    &Change::UpsertFontPortableCopy { declaration },
                )
                .unwrap()
                .project;
        }
        (service, project)
    }

    fn failed_without_publication(service: &StudioService, project: &Project, root: &Path) {
        for at_revision in [false, true] {
            let destination = root.join(format!("rejected-{at_revision}"));
            let result = if at_revision {
                service.export_project_bundle_at(project.id, &project.stamp(), &destination)
            } else {
                service.export_project_bundle(project.id, &destination)
            };
            assert!(result.is_err());
            assert!(!destination.exists());
            assert_eq!(service.project(project.id).unwrap(), *project);
            assert!(!fs::read_dir(root).unwrap().any(|entry| {
                entry
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".motionwright-export-")
            }));
        }
    }

    #[test]
    fn original_font_and_license_fixture_pins_are_exact() {
        assert_eq!(FONT.len(), 21_568);
        assert_eq!(
            hex::encode(Sha256::digest(FONT)),
            "0441303144c314d46ee12cf9742f1dd3fcbcb42700e7c51dba7b1b689c1dd1d7"
        );
        assert_eq!(LICENSE.len(), 11_357);
        assert_eq!(
            hex::encode(Sha256::digest(LICENSE)),
            "58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd"
        );
        let metadata = semwright_driver_motion_canvas::fonts::inspect(FONT).unwrap();
        assert!(metadata.family_names.iter().any(|family| family == FAMILY));
    }

    #[test]
    fn both_service_exports_copy_permitted_original_font_and_reopen_its_editable_source() {
        let root = tempfile::tempdir().unwrap();
        let (service, mut project) = fixture(
            root.path(),
            FONT,
            Some(FontPortableCopyPermission::Allowed),
            RightsStatus::Cleared,
            "font/ttf",
        );
        let mut descriptive_alias = project.assets[0].clone();
        descriptive_alias.id = uuid::Uuid::now_v7();
        descriptive_alias.name = "Second label for the exact same original font bytes".into();
        descriptive_alias.media_type = "application/octet-stream".into();
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "record-descriptive-alias",
                &Change::AddAsset {
                    asset: descriptive_alias,
                },
            )
            .unwrap()
            .project;
        let license_path = root.path().join("original-font-license.txt");
        fs::write(&license_path, LICENSE).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "import-font-license",
                &license_path,
                "Original SDK font license".into(),
                "text/plain".into(),
            )
            .unwrap()
            .project;
        for at_revision in [false, true] {
            let destination = root.path().join(format!("permitted-{at_revision}"));
            let manifest = if at_revision {
                service.export_project_bundle_at(project.id, &project.stamp(), &destination)
            } else {
                service.export_project_bundle(project.id, &destination)
            }
            .unwrap();
            assert_eq!(manifest.backup.project, project);
            assert_eq!(
                manifest.blobs.len(),
                2,
                "the two font aliases retain one original blob"
            );
            for (expected, digest) in [
                (FONT, hex::encode(Sha256::digest(FONT))),
                (LICENSE, hex::encode(Sha256::digest(LICENSE))),
            ] {
                let blob = manifest
                    .blobs
                    .iter()
                    .find(|blob| blob.sha256 == digest)
                    .unwrap();
                assert_eq!(blob.size_bytes, expected.len() as u64);
                assert_eq!(
                    fs::read(destination.join(&blob.relative_path)).unwrap(),
                    expected
                );
            }
            let database = root
                .path()
                .join(format!("restored-{at_revision}/state.sqlite3"));
            let restored_service = StudioService::open(&database).unwrap();
            let inspection = restored_service
                .inspect_project_bundle_with_fingerprint(&destination)
                .unwrap();
            let restored = restored_service
                .import_project_bundle_inspected(&destination, &inspection.fingerprint)
                .unwrap();
            assert_ne!(restored.generation, project.generation);
            assert_eq!(
                restored.visual_language.fonts,
                project.visual_language.fonts
            );
            assert_eq!(
                restored.production_design.font_portable_copies,
                project.production_design.font_portable_copies
            );
            drop(restored_service);
            let reopened = StudioService::open(&database).unwrap();
            assert_eq!(reopened.project(restored.id).unwrap(), restored);
            let original = reopened
                .verified_brand_font(restored.id, &RevisionStamp::from(&restored), FAMILY)
                .unwrap();
            assert_eq!(original.bytes, FONT);
            assert_eq!(original.source.sha256, hex::encode(Sha256::digest(FONT)));
        }
    }

    #[test]
    fn cleared_use_without_explicit_copy_permission_keeps_metadata_but_refuses_font_export() {
        let root = tempfile::tempdir().unwrap();
        let (service, project) =
            fixture(root.path(), FONT, None, RightsStatus::Cleared, "font/ttf");
        failed_without_publication(&service, &project, root.path());
        let backup = service
            .export_project_at(project.id, &project.stamp())
            .unwrap();
        assert_eq!(backup.project, project);
        assert!(
            backup
                .project
                .production_design
                .font_portable_copies
                .is_empty()
        );
    }

    #[test]
    fn prohibited_copy_unknown_rights_and_restricted_rights_are_not_promoted() {
        for (permission, status) in [
            (
                FontPortableCopyPermission::Prohibited,
                RightsStatus::Cleared,
            ),
            (FontPortableCopyPermission::Allowed, RightsStatus::Unknown),
            (
                FontPortableCopyPermission::Allowed,
                RightsStatus::Restricted,
            ),
        ] {
            let root = tempfile::tempdir().unwrap();
            let (service, project) =
                fixture(root.path(), FONT, Some(permission), status, "font/ttf");
            failed_without_publication(&service, &project, root.path());
        }
    }

    #[test]
    fn original_font_family_and_canonical_source_policy_are_reused_at_export() {
        for scenario in [
            "wrong-family",
            "duplicate-family",
            "bundled-shadow",
            "opaque-alias",
        ] {
            let root = tempfile::tempdir().unwrap();
            let media_type = if scenario == "opaque-alias" {
                "application/octet-stream"
            } else {
                "font/ttf"
            };
            let (service, mut project) = fixture(
                root.path(),
                FONT,
                Some(FontPortableCopyPermission::Allowed),
                RightsStatus::Cleared,
                media_type,
            );
            let mut visual_language = project.visual_language.clone();
            match scenario {
                "wrong-family" => {
                    visual_language.fonts[0].family = "Different recorded family".into()
                }
                "bundled-shadow" => {
                    visual_language.fonts[0].family = "Instrument Sans Variable".into()
                }
                "duplicate-family" => visual_language.fonts.push(visual_language.fonts[0].clone()),
                "opaque-alias" => {}
                _ => unreachable!(),
            }
            if scenario != "opaque-alias" {
                project = service
                    .apply(
                        project.id,
                        &project.stamp(),
                        "record-wrong-source",
                        &Change::SetVisualLanguage { visual_language },
                    )
                    .unwrap()
                    .project;
            }
            failed_without_publication(&service, &project, root.path());
        }
    }

    // Fixture-only table-directory lookup. Production font interpretation is
    // exclusively the public SDK parser above. No new decoder is introduced.
    fn with_embedding_flags(value: u16) -> Vec<u8> {
        let count = u16::from_be_bytes(FONT[4..6].try_into().unwrap()) as usize;
        let record = (0..count)
            .map(|index| 12 + 16 * index)
            .find(|at| &FONT[*at..*at + 4] == b"OS/2")
            .unwrap();
        let start = u32::from_be_bytes(FONT[record + 8..record + 12].try_into().unwrap()) as usize;
        let mut bytes = FONT.to_vec();
        bytes[start + 8..start + 10].copy_from_slice(&value.to_be_bytes());
        bytes
    }

    #[test]
    fn actual_original_font_embedding_restrictions_override_recorded_copy_permission() {
        for flags in [0x0002, 0x0004, 0x0200] {
            let root = tempfile::tempdir().unwrap();
            let changed = with_embedding_flags(flags);
            assert_ne!(changed, FONT);
            let (service, project) = fixture(
                root.path(),
                &changed,
                Some(FontPortableCopyPermission::Allowed),
                RightsStatus::Cleared,
                "font/ttf",
            );
            failed_without_publication(&service, &project, root.path());
        }
    }

    #[test]
    fn font_inspector_requires_a_complete_original_within_the_existing_brand_budget() {
        let root = tempfile::tempdir().unwrap();
        let (_, project) = fixture(
            root.path(),
            FONT,
            Some(FontPortableCopyPermission::Allowed),
            RightsStatus::Cleared,
            "font/ttf",
        );
        let descriptor = BlobDescriptor {
            sha256: hex::encode(Sha256::digest(FONT)),
            size_bytes: FONT.len() as u64,
        };
        assert!(
            FontBundleExportAdmission
                .validate_font_export(
                    &project,
                    &FONT[..FONT.len() - 1],
                    &descriptor,
                    &[&project.assets[0]]
                )
                .is_err()
        );
        let too_large = BlobDescriptor {
            sha256: descriptor.sha256,
            size_bytes: MAX_BRAND_FONT_BYTES + 1,
        };
        assert!(matches!(
            FontBundleExportAdmission.validate_font_export(
                &project,
                FONT,
                &too_large,
                &[&project.assets[0]]
            ),
            Err(StorageError::BlobTooLarge {
                limit: MAX_BRAND_FONT_BYTES,
                ..
            })
        ));
        assert_eq!(FontBundleExportAdmission.prefix_limit(), 0);
        assert_eq!(
            FontBundleExportAdmission.font_export_prefix_limit() as u64,
            MAX_BRAND_FONT_BYTES
        );
    }

    #[test]
    fn non_font_export_keeps_its_prior_policy_and_svg_import_keeps_its_original_limit() {
        use crate::portability::SvgBundleAdmission;
        assert_eq!(FontBundleExportAdmission.prefix_limit(), 0);
        assert_eq!(
            FontBundleExportAdmission.font_export_prefix_limit() as u64,
            MAX_BRAND_FONT_BYTES
        );
        assert_eq!(
            SvgBundleAdmission.prefix_limit() as u64,
            crate::MAX_SVG_IMPORT_BYTES + 1
        );
        // Existing export copies opaque registered assets after the credential
        // scan. SVG admission belongs to inspect/import and is not broadened or
        // silently introduced as a new export policy by the font extension.
        let start = b"<svg xmlns=\"http://www.w3.org/2000/svg\">";
        let end = b"</svg>";
        for excess in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let service = StudioService::open(root.path().join("original/state.sqlite3")).unwrap();
            let project = service.create_project("Original SVG boundary").unwrap();
            let mut bytes = start.to_vec();
            bytes.resize(crate::MAX_SVG_IMPORT_BYTES as usize - end.len(), b' ');
            bytes.extend_from_slice(end);
            if excess {
                bytes.push(b' ');
            }
            let source = root.path().join("registered-original.svg");
            fs::write(&source, &bytes).unwrap();
            let descriptor = service.ingest_blob_file(&source).unwrap();
            let asset = Asset {
                id: uuid::Uuid::now_v7(),
                name: "Original vector".into(),
                media_type: "image/svg+xml".into(),
                content_sha256: Some(descriptor.sha256),
                source_revision: Some("retained-test-original".into()),
            };
            let project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    "attach-original-svg",
                    &Change::AddAsset { asset },
                )
                .unwrap()
                .project;
            for at_revision in [false, true] {
                let destination = root.path().join(format!("ordinary-export-{at_revision}"));
                let manifest = if at_revision {
                    service.export_project_bundle_at(project.id, &project.stamp(), &destination)
                } else {
                    service.export_project_bundle(project.id, &destination)
                }
                .unwrap();
                assert_eq!(manifest.backup.project, project);
                assert_eq!(manifest.blobs.len(), 1);
                assert_eq!(
                    fs::read(destination.join(&manifest.blobs[0].relative_path)).unwrap(),
                    bytes
                );
                let inspector = StudioService::open(
                    root.path()
                        .join(format!("inspection-{at_revision}/state.sqlite3")),
                )
                .unwrap();
                let inspected = inspector.inspect_project_bundle_with_fingerprint(&destination);
                if excess {
                    assert!(matches!(
                        inspected,
                        Err(StorageError::BlobTooLarge {
                            limit: crate::MAX_SVG_IMPORT_BYTES,
                            ..
                        })
                    ));
                } else {
                    assert!(inspected.is_ok());
                }
            }
        }
    }
}
