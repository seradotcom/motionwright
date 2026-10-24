//! Source-only projection. Managed import and actual face/glyph verification
//! remain in the public Semwright Motion Canvas provider.
use super::{
    AssetRef, FontFallback, FontSpec, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
    NativeResult, Project, contract, uid, unsupported,
};
use motionwright_service::brand_font_source;
use semwright_semantic_composition::Digest;

pub(super) struct ProjectedFonts {
    pub primary: FontSpec,
    pub mono: FontSpec,
    pub assets: Vec<AssetRef>,
}

fn project_font(project: &Project, family: &str) -> NativeResult<(FontSpec, Option<AssetRef>)> {
    let bundled = [MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY].contains(&family);
    if bundled {
        // A source URI must not shadow the compiler's immutable bundled family.
        if project.visual_language.fonts.iter().any(|font| {
            font.family.eq_ignore_ascii_case(family) && font.source.starts_with("asset:")
        }) {
            return Err(unsupported(
                "Bundled font names cannot shadow a project font Asset",
            ));
        }
        return Ok((
            FontSpec {
                family: family.into(),
                asset_digest: None,
                fallback: FontFallback::AllowAndReport,
                permitted_fallbacks: vec![],
            },
            None,
        ));
    }
    let source = brand_font_source(project, family)
        .map_err(|error| unsupported(format!(
            "Font {family:?} for resource {} at revision {} requires a cleared digest-bound Asset source: {error}",
            project.resource_key(), project.revision
        )))?;
    let digest = Digest::parse(source.sha256)
        .map_err(|error| contract("Brand font digest is invalid", error))?;
    Ok((
        FontSpec {
            family: source.family,
            asset_digest: Some(digest.clone()),
            fallback: FontFallback::Deny,
            permitted_fallbacks: vec![],
        },
        Some(AssetRef {
            id: uid("asset", source.asset_id),
            sha256: digest,
            media_type: source.media_type,
            provenance: source.source_revision,
            // This is not a legal attestation. FontRight remains the editable
            // owner declaration; license evidence belongs in the retained input.
            license: None,
        }),
    ))
}

pub(super) fn project_fonts(
    project: &Project,
    primary: &str,
    mono: &str,
) -> NativeResult<ProjectedFonts> {
    let (primary, primary_asset) = project_font(project, primary)?;
    let (mono, mono_asset) = project_font(project, mono)?;
    if primary.family != mono.family
        && primary.asset_digest.is_some()
        && primary.asset_digest == mono.asset_digest
    {
        return Err(unsupported(
            "Selected font families cannot alias one managed face",
        ));
    }
    let mut assets = Vec::new();
    for asset in [primary_asset, mono_asset].into_iter().flatten() {
        if !assets
            .iter()
            .any(|existing: &AssetRef| existing.id == asset.id)
        {
            assets.push(asset);
        }
    }
    Ok(ProjectedFonts {
        primary,
        mono,
        assets,
    })
}

/// Negotiate the actual bundled readiness profile in the local source-only
/// Film projection, before prepare_motion_fonts can import any CAS asset.
/// CAS fonts keep their independent parsed face/axis checks in the provider.
pub(super) fn validate_bundled_weights(film: &super::Film) -> NativeResult<()> {
    fn weight(spec: &FontSpec, value: u16) -> NativeResult<()> {
        if spec.asset_digest.is_some() {
            return Ok(());
        }
        let observed: &[u16] = match spec.family.as_str() {
            MOTION_CANVAS_FONT_FAMILY => &[400, 500, 600, 700],
            MOTION_CANVAS_MONO_FONT_FAMILY => &[400],
            _ => return Err(unsupported("Unpinned bundled font family")),
        };
        if !observed.contains(&value) {
            return Err(unsupported(
                "Authored bundled font weight has no pinned native readiness support",
            ));
        }
        Ok(())
    }
    for subject in film.shots().flat_map(|shot| &shot.subjects) {
        match &subject.content {
            super::SubjectContent::Text { runs, .. } => {
                for run in runs {
                    weight(&film.editorial.font, run.weight)?;
                }
            }
            super::SubjectContent::Code { .. } => weight(&film.editorial.mono_font, 400)?,
            _ => {}
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Asset, FontRight, RightsStatus};
    use uuid::Uuid;

    fn fixture() -> Project {
        let mut project = Project::new("Brand font Film").unwrap();
        let id = Uuid::new_v4();
        project.assets.push(Asset {
            id,
            name: "Brand face".into(),
            media_type: "font/ttf".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: Some("licensed-input".into()),
        });
        project.visual_language.fonts.push(FontRight {
            family: "Brand Family".into(),
            source: format!("asset:{id}"),
            rights_status: RightsStatus::Cleared,
        });
        project
    }

    #[test]
    fn brand_film_uses_exact_asset_digest_and_forbids_fallback() {
        let project = fixture();
        let fonts =
            project_fonts(&project, "Brand Family", MOTION_CANVAS_MONO_FONT_FAMILY).unwrap();
        assert_eq!(fonts.primary.family, "Brand Family");
        assert_eq!(
            fonts.primary.asset_digest.as_ref().unwrap().as_str(),
            "a".repeat(64)
        );
        assert_eq!(fonts.primary.fallback, FontFallback::Deny);
        assert!(fonts.primary.permitted_fallbacks.is_empty());
        assert_eq!(fonts.assets.len(), 1);
        assert_eq!(fonts.assets[0].id, uid("asset", project.assets[0].id));
        assert_eq!(fonts.mono.asset_digest, None);
    }

    #[test]
    fn brand_film_deduplicates_selected_face_without_admitting_unused_assets() {
        let mut project = fixture();
        let mut unused = project.assets[0].clone();
        unused.id = Uuid::new_v4();
        project.assets.push(unused);
        let fonts = project_fonts(&project, "Brand Family", "Brand Family").unwrap();
        assert_eq!(fonts.assets.len(), 1);
    }

    #[test]
    fn brand_film_bundled_projection_stays_byte_compatible() {
        let project = Project::new("Bundled faces").unwrap();
        let fonts = project_fonts(
            &project,
            MOTION_CANVAS_FONT_FAMILY,
            MOTION_CANVAS_MONO_FONT_FAMILY,
        )
        .unwrap();
        assert!(fonts.assets.is_empty());
        assert_eq!(
            serde_json::to_value(fonts.primary).unwrap(),
            serde_json::json!({
                "family": MOTION_CANVAS_FONT_FAMILY, "asset_digest": null,
                "fallback":"allow_and_report", "permitted_fallbacks": []
            })
        );
    }

    #[test]
    fn brand_film_rejects_imported_asset_shadowing_bundled_family() {
        let mut project = fixture();
        project.visual_language.fonts[0].family = MOTION_CANVAS_FONT_FAMILY.into();
        assert!(
            project_fonts(
                &project,
                MOTION_CANVAS_FONT_FAMILY,
                MOTION_CANVAS_MONO_FONT_FAMILY
            )
            .is_err()
        );
    }
    #[test]
    fn selected_families_do_not_alias_one_managed_face() {
        let mut project = fixture();
        project.visual_language.fonts.push(FontRight {
            family: "Brand Mono".into(),
            source: format!("asset:{}", project.assets[0].id),
            rights_status: RightsStatus::Cleared,
        });
        assert!(project_fonts(&project, "Brand Family", "Brand Mono").is_err());
    }
}
