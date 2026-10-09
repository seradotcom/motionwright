use crate::{DomainError, RendererKind, Result, RightsStatus};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExtensionKind {
    RemotionRenderer,
    ManimGlRenderer,
    GenerativeAssets,
    CatalogPackage,
    HyperframesRenderer,
}

impl ExtensionKind {
    pub fn renderer(&self) -> Option<RendererKind> {
        match self {
            Self::HyperframesRenderer => Some(RendererKind::Hyperframes),
            Self::RemotionRenderer => Some(RendererKind::Remotion),
            Self::ManimGlRenderer => Some(RendererKind::ManimGl),
            Self::GenerativeAssets | Self::CatalogPackage => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExtensionProfile {
    pub id: Uuid,
    pub name: String,
    pub kind: ExtensionKind,
    pub package_version: String,
    pub digest_sha256: String,
    pub license: String,
    pub source: String,
    pub rights_status: RightsStatus,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub permissions: Vec<String>,
}

impl ExtensionProfile {
    pub fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid(
                "extension name is out of bounds".into(),
            ));
        }
        if self.package_version.trim().is_empty() || self.package_version.len() > 96 {
            return Err(DomainError::Invalid(
                "extension package version is out of bounds".into(),
            ));
        }
        if self.digest_sha256.len() != 64
            || !self
                .digest_sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(DomainError::Invalid("extension digest is invalid".into()));
        }
        if self.license.trim().is_empty()
            || self.license.len() > 128
            || self.source.trim().is_empty()
            || self.source.len() > 2_048
            || self.source.chars().any(char::is_control)
        {
            return Err(DomainError::Invalid(
                "extension source or license is invalid".into(),
            ));
        }
        if self.enabled && self.rights_status != RightsStatus::Cleared {
            return Err(DomainError::Invalid(
                "extension rights must be cleared before opt-in".into(),
            ));
        }
        if self.permissions.len() > 32 {
            return Err(DomainError::Invalid(
                "extension permission list is too large".into(),
            ));
        }
        let allowed = ["read_project", "read_assets", "write_artifacts", "network"];
        let mut unique = BTreeSet::new();
        for permission in &self.permissions {
            if !allowed.contains(&permission.as_str()) || !unique.insert(permission) {
                return Err(DomainError::Invalid(
                    "extension permission is invalid or duplicated".into(),
                ));
            }
        }
        Ok(())
    }
}

pub fn renderer_extension_enabled(
    renderer: &RendererKind,
    extensions: &[ExtensionProfile],
) -> bool {
    match renderer {
        RendererKind::MotionCanvas
        | RendererKind::Mlt
        | RendererKind::Blender
        | RendererKind::ManimCommunity => true,
        RendererKind::Remotion | RendererKind::ManimGl | RendererKind::Hyperframes => {
            extensions.iter().any(|extension| {
                extension.enabled
                    && extension.kind.renderer().as_ref() == Some(renderer)
                    && extension.rights_status == RightsStatus::Cleared
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Change, Project};

    fn extension(
        kind: ExtensionKind,
        rights_status: RightsStatus,
        enabled: bool,
    ) -> ExtensionProfile {
        ExtensionProfile {
            id: Uuid::now_v7(),
            name: "Reviewed optional backend".into(),
            kind,
            package_version: "1.0.0".into(),
            digest_sha256: "ab".repeat(32),
            license: "MIT".into(),
            source: "https://example.invalid/reviewed-package".into(),
            rights_status,
            enabled,
            permissions: vec!["read_project".into(), "write_artifacts".into()],
        }
    }

    #[test]
    fn optional_renderer_requires_explicit_project_opt_in() {
        let mut project = Project::new("Optional renderer gate").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Scene".into(),
                objective: "Test optional renderer authority".into(),
                duration_seconds: 5,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;

        let error = project
            .apply_change(&Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Remotion,
            })
            .unwrap_err();
        assert!(error.to_string().contains("explicitly enabled"));

        let remotion = extension(ExtensionKind::RemotionRenderer, RightsStatus::Cleared, true);
        project
            .apply_change(&Change::UpsertExtension {
                extension: remotion.clone(),
            })
            .unwrap();
        project
            .apply_change(&Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Remotion,
            })
            .unwrap();

        let error = project
            .apply_change(&Change::RemoveExtension {
                extension_id: remotion.id,
            })
            .unwrap_err();
        assert!(error.to_string().contains("renderer is in use"));
    }

    #[test]
    fn unresolved_rights_cannot_enable_an_extension() {
        let mut project = Project::new("Rights gate").unwrap();
        let error = project
            .apply_change(&Change::UpsertExtension {
                extension: extension(ExtensionKind::GenerativeAssets, RightsStatus::Unknown, true),
            })
            .unwrap_err();
        assert!(error.to_string().contains("rights must be cleared"));
    }

    #[test]
    fn manim_gl_identity_never_unlocks_manin_community_or_remotion_by_alias() {
        let mut project = Project::new("Distinct renderer identities").unwrap();
        project
            .apply_change(&Change::UpsertExtension {
                extension: extension(ExtensionKind::ManimGlRenderer, RightsStatus::Cleared, true),
            })
            .unwrap();

        assert!(renderer_extension_enabled(
            &RendererKind::ManimCommunity,
            &project.extensions
        ));
        assert!(renderer_extension_enabled(
            &RendererKind::ManimGl,
            &project.extensions
        ));
        assert!(!renderer_extension_enabled(
            &RendererKind::Remotion,
            &project.extensions
        ));
    }
}
