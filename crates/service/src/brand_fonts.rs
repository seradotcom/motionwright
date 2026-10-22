//! Owner-only source resolution for brand fonts. This module does not load,
//! install or render fonts. The managed Semwright provider owns those effects.
use crate::{StudioService, invalid_import};
use motionwright_domain::{Asset, Project, RevisionStamp, RightsStatus};
use motionwright_storage::{Result as StorageResult, StorageError};
use uuid::Uuid;

pub const MAX_BRAND_FONT_BYTES: u64 = 16 * 1024 * 1024;

/// A source reference bound to a current project Asset, never an OS font name,
/// URL, path or authority to invoke a native operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrandFontSource {
    pub family: String,
    pub asset_id: Uuid,
    pub sha256: String,
    pub media_type: String,
    pub source_revision: Option<String>,
}

/// Backend-only verified bytes. Intentionally not Serialize or a WebView DTO.
#[derive(Debug)]
pub struct VerifiedBrandFont {
    pub source: BrandFontSource,
    pub bytes: Vec<u8>,
}

fn safe_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// Resolve the existing FontRight.source = asset:<canonical UUID> convention.
/// Cleared is the owner's recorded declaration, not an independent legal verdict.
pub fn brand_font_source(project: &Project, family: &str) -> StorageResult<BrandFontSource> {
    if family.is_empty()
        || family.trim() != family
        || family.len() > 128
        || family.chars().any(char::is_control)
    {
        return Err(invalid_import("Brand font family is invalid"));
    }
    if ["Instrument Sans Variable", "IBM Plex Mono"].contains(&family) {
        return Err(invalid_import(
            "Bundled font family names cannot be shadowed by imported assets",
        ));
    }
    let folded = family.to_lowercase();
    let mut matching = project
        .visual_language
        .fonts
        .iter()
        .filter(|right| right.family.to_lowercase() == folded);
    let right = matching
        .next()
        .ok_or_else(|| invalid_import("External font requires one explicit project FontRight"))?;
    if matching.next().is_some() || right.family != family {
        return Err(invalid_import(
            "Brand font family is ambiguous or differs from its exact declaration",
        ));
    }
    if right.rights_status != RightsStatus::Cleared {
        return Err(invalid_import(
            "Brand font rights are not recorded as cleared",
        ));
    }
    let identifier = right
        .source
        .strip_prefix("asset:")
        .ok_or_else(|| invalid_import("Brand font source must identify a project Asset"))?;
    let asset_id = Uuid::parse_str(identifier)
        .map_err(|_| invalid_import("Brand font asset identifier is invalid"))?;
    if identifier != asset_id.hyphenated().to_string() {
        return Err(invalid_import(
            "Brand font source must use a canonical UUID",
        ));
    }
    let mut assets = project.assets.iter().filter(|asset| asset.id == asset_id);
    let asset: &Asset = assets
        .next()
        .ok_or_else(|| invalid_import("Brand font Asset is not attached to this project"))?;
    if assets.next().is_some() {
        return Err(invalid_import("Brand font Asset is ambiguous"));
    }
    if !["font/ttf", "font/otf"].contains(&asset.media_type.as_str()) {
        return Err(invalid_import(
            "Brand font requires an admitted TTF or OpenType source",
        ));
    }
    let digest = asset
        .content_sha256
        .as_deref()
        .filter(|digest| safe_digest(digest))
        .ok_or_else(|| invalid_import("Brand font Asset has no canonical content digest"))?;
    Ok(BrandFontSource {
        family: family.to_owned(),
        asset_id,
        sha256: digest.to_owned(),
        media_type: asset.media_type.clone(),
        source_revision: asset.source_revision.clone(),
    })
}

impl StudioService {
    /// Resolve the source and read CAS while the same project revision is locked.
    /// The provider still derives face/glyph metadata from these exact bytes.
    pub fn verified_brand_font(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        family: &str,
    ) -> StorageResult<VerifiedBrandFont> {
        let store = self.store.lock();
        let project = store.load_project(project_id)?;
        if expected.resource != project.resource_key() || expected.generation != project.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if expected.revision != project.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }
        let source = brand_font_source(&project, family)?;
        let bytes = store.read_blob(&source.sha256, MAX_BRAND_FONT_BYTES)?;
        if bytes.is_empty() {
            return Err(invalid_import("Brand font CAS object is empty"));
        }
        Ok(VerifiedBrandFont { source, bytes })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, FontRight};
    use std::fs;

    fn source_project() -> Project {
        let mut project = Project::new("Font source").unwrap();
        let asset_id = Uuid::parse_str("9abe75fb-5b24-435c-9276-c2df1c0312e6").unwrap();
        project.assets.push(Asset {
            id: asset_id,
            name: "Brand family regular".into(),
            media_type: "font/ttf".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: Some("owner-font-asset-v1".into()),
        });
        project.visual_language.fonts.push(FontRight {
            family: "Brand Family".into(),
            source: format!("asset:{asset_id}"),
            rights_status: RightsStatus::Cleared,
        });
        project
    }

    #[test]
    fn brand_font_source_preserves_digest_identity_and_owner_declaration() {
        let project = source_project();
        let before = serde_json::to_vec(&project).unwrap();
        let resolved = brand_font_source(&project, "Brand Family").unwrap();
        assert_eq!(resolved.asset_id, project.assets[0].id);
        assert_eq!(resolved.sha256, "a".repeat(64));
        assert_eq!(resolved.media_type, "font/ttf");
        assert_eq!(
            resolved.source_revision.as_deref(),
            Some("owner-font-asset-v1")
        );
        assert_eq!(serde_json::to_vec(&project).unwrap(), before);
    }

    #[test]
    fn brand_font_source_never_promotes_unknown_or_restricted_rights() {
        for rights in [RightsStatus::Unknown, RightsStatus::Restricted] {
            let mut project = source_project();
            project.visual_language.fonts[0].rights_status = rights;
            assert!(brand_font_source(&project, "Brand Family").is_err());
        }
    }

    #[test]
    fn brand_font_source_rejects_paths_urls_noncanonical_and_foreign_assets() {
        for source in [
            "https://example.invalid/font.ttf",
            "E:\\Fonts\\Brand.ttf",
            "../font.ttf",
            "asset:9ABE75FB-5B24-435C-9276-C2DF1C0312E6",
            "asset:9abe75fb5b24435c9276c2df1c0312e6",
            "asset:00000000-0000-0000-0000-000000000000",
        ] {
            let mut project = source_project();
            project.visual_language.fonts[0].source = source.into();
            assert!(
                brand_font_source(&project, "Brand Family").is_err(),
                "{source}"
            );
        }
    }

    #[test]
    fn brand_font_source_rejects_wrong_type_digest_alias_or_duplicate() {
        let original = source_project();
        for media_type in ["audio/wav", "font/woff2", "application/octet-stream"] {
            let mut project = original.clone();
            project.assets[0].media_type = media_type.into();
            assert!(brand_font_source(&project, "Brand Family").is_err());
        }
        for digest in [None, Some("A".repeat(64)), Some("a".repeat(63))] {
            let mut project = original.clone();
            project.assets[0].content_sha256 = digest;
            assert!(brand_font_source(&project, "Brand Family").is_err());
        }
        let mut project = original.clone();
        let mut alias = project.visual_language.fonts[0].clone();
        alias.family = "brand family".into();
        project.visual_language.fonts.push(alias);
        assert!(brand_font_source(&project, "Brand Family").is_err());
        assert!(brand_font_source(&original, "brand family").is_err());
        assert!(brand_font_source(&original, "Instrument Sans Variable").is_err());
    }

    #[test]
    fn brand_font_cas_checks_revision_and_survives_fresh_service_readback() {
        // Deliberately not a font. This test proves CAS custody only; font
        // parsing, face load and glyph coverage are separate provider tests.
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("font.sqlite3");
        let source = temp.path().join("source.ttf");
        let original_bytes = b"source-custody-test-not-font-evidence";
        fs::write(&source, original_bytes).unwrap();
        let service = StudioService::open(&database).unwrap();
        let initial = service.create_project("Font CAS custody").unwrap();
        let imported = service
            .import_asset_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "font-cas-import",
                &source,
                "Brand font source".into(),
                "font/ttf".into(),
            )
            .unwrap()
            .project;
        let mut visual_language = imported.visual_language.clone();
        visual_language.fonts.push(FontRight {
            family: "Brand Family".into(),
            source: format!("asset:{}", imported.assets[0].id),
            rights_status: RightsStatus::Cleared,
        });
        let current = service
            .apply(
                imported.id,
                &RevisionStamp::from(&imported),
                "font-right-save",
                &Change::SetVisualLanguage { visual_language },
            )
            .unwrap()
            .project;
        assert!(
            service
                .verified_brand_font(current.id, &RevisionStamp::from(&imported), "Brand Family")
                .is_err()
        );
        let other = service.create_project("Other source").unwrap();
        assert!(
            service
                .verified_brand_font(current.id, &RevisionStamp::from(&other), "Brand Family")
                .is_err()
        );
        drop(service);
        fs::remove_file(&source).unwrap();
        let reopened = StudioService::open(&database).unwrap();
        let verified = reopened
            .verified_brand_font(current.id, &RevisionStamp::from(&current), "Brand Family")
            .unwrap();
        assert_eq!(verified.bytes, original_bytes);
        assert_eq!(
            verified.source.sha256,
            current.assets[0].content_sha256.clone().unwrap()
        );
        let readback = reopened.project(current.id).unwrap();
        assert_eq!(
            readback.visual_language.fonts,
            current.visual_language.fonts
        );
    }
}
