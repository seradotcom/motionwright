//! Recorded permission to copy a particular original font into a portable bundle.
//! These declarations are authoring data, not legal verification or native grants.
use crate::*;

pub const MAX_FONT_PORTABLE_COPY_DECLARATIONS: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FontPortableCopyPermission {
    Allowed,
    Prohibited,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FontPortableCopyDeclaration {
    pub version: u32,
    pub asset_id: Uuid,
    pub content_sha256: String,
    pub permission: FontPortableCopyPermission,
    pub origin: String,
    pub license: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

fn portable_copy_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn portable_copy_text(value: &str, maximum: usize, required: bool) -> Result<()> {
    if value.len() > maximum
        || value.trim() != value
        || value.chars().any(char::is_control)
        || (required && value.is_empty())
    {
        return Err(DomainError::Invalid(
            "font portable-copy declaration contains invalid source text".into(),
        ));
    }
    Ok(())
}

impl FontPortableCopyDeclaration {
    /// Shape validation does not establish current assets, cleared use rights,
    /// permission encoded in the original font, or permission to publish.
    pub fn validate(&self) -> Result<()> {
        if self.version != 1
            || self.asset_id.is_nil()
            || !portable_copy_digest(&self.content_sha256)
        {
            return Err(DomainError::Invalid(
                "font portable-copy declaration has an invalid version or identity".into(),
            ));
        }
        let required = self.permission == FontPortableCopyPermission::Allowed;
        portable_copy_text(&self.origin, 2048, required)?;
        portable_copy_text(&self.license, 2048, required)?;
        if let Some(note) = &self.note {
            portable_copy_text(note, 4096, true)?;
        }
        Ok(())
    }
}

impl ProductionDesign {
    pub(crate) fn validate_font_portable_copy_declarations(&self) -> Result<()> {
        if self.font_portable_copies.len() > MAX_FONT_PORTABLE_COPY_DECLARATIONS {
            return Err(DomainError::Invalid(
                "font portable-copy declaration collection exceeds its bound".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for declaration in &self.font_portable_copies {
            declaration.validate()?;
            if !ids.insert(declaration.asset_id) {
                return Err(DomainError::Invalid(
                    "font portable-copy declaration has a duplicate asset identity".into(),
                ));
            }
        }
        // Historical source bindings remain readable. In particular, deleting
        // an asset must not erase a recorded prohibition for its original bytes.
        Ok(())
    }
}

impl Project {
    pub(crate) fn upsert_font_portable_copy(
        &mut self,
        declaration: &FontPortableCopyDeclaration,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Style])?;
        self.production_design
            .validate_font_portable_copy_declarations()?;
        declaration.validate()?;
        let mut sources = self
            .assets
            .iter()
            .filter(|asset| asset.id == declaration.asset_id);
        let source = sources
            .next()
            .ok_or_else(|| DomainError::NotFound(format!("asset:{}", declaration.asset_id)))?;
        if sources.next().is_some()
            || source.content_sha256.as_deref() != Some(declaration.content_sha256.as_str())
        {
            return Err(DomainError::Invalid(
                "font portable-copy declaration must bind the current original asset bytes".into(),
            ));
        }
        if let Some(index) = self
            .production_design
            .font_portable_copies
            .iter()
            .position(|current| current.asset_id == declaration.asset_id)
        {
            self.production_design.font_portable_copies[index] = declaration.clone();
        } else {
            if self.production_design.font_portable_copies.len()
                >= MAX_FONT_PORTABLE_COPY_DECLARATIONS
            {
                return Err(DomainError::Invalid(
                    "font portable-copy declaration collection exceeds its bound".into(),
                ));
            }
            self.production_design
                .font_portable_copies
                .push(declaration.clone());
        }
        Ok(())
    }

    pub(crate) fn remove_font_portable_copy(&mut self, asset_id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Style])?;
        self.production_design
            .validate_font_portable_copy_declarations()?;
        let index = self
            .production_design
            .font_portable_copies
            .iter()
            .position(|current| current.asset_id == asset_id)
            .ok_or_else(|| DomainError::NotFound(format!("font-portable-copy:{asset_id}")))?;
        self.production_design.font_portable_copies.remove(index);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (Project, FontPortableCopyDeclaration) {
        let mut project = Project::new("Recorded font source").unwrap();
        let asset = Asset {
            id: Uuid::now_v7(),
            name: "Original font".into(),
            media_type: "font/ttf".into(),
            content_sha256: Some("a1".repeat(32)),
            source_revision: Some("author-selected-original".into()),
        };
        let declaration = FontPortableCopyDeclaration {
            version: 1,
            asset_id: asset.id,
            content_sha256: asset.content_sha256.clone().unwrap(),
            permission: FontPortableCopyPermission::Allowed,
            origin: "Original font supplied with its license".into(),
            license: "Apache-2.0".into(),
            note: None,
        };
        project.apply_change(&Change::AddAsset { asset }).unwrap();
        (project, declaration)
    }

    #[test]
    fn absent_copy_declarations_preserve_legacy_serialization() {
        let (project, _) = fixture();
        let value = serde_json::to_value(&project).unwrap();
        assert!(
            value["production_design"]
                .get("font_portable_copies")
                .is_none()
        );
        let recovered: Project = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(serde_json::to_value(&recovered).unwrap(), value);
        assert!(recovered.production_design.font_portable_copies.is_empty());
    }

    #[test]
    fn copy_declaration_has_closed_bounded_versioned_wire() {
        let (_, declaration) = fixture();
        declaration.validate().unwrap();
        let value = serde_json::to_value(&declaration).unwrap();
        for (field, replacement) in [
            ("permission", serde_json::json!("cleared")),
            ("version", serde_json::Value::Null),
            ("asset_id", serde_json::Value::Null),
            ("license", serde_json::Value::Null),
        ] {
            let mut bad = value.clone();
            bad[field] = replacement;
            assert!(serde_json::from_value::<FontPortableCopyDeclaration>(bad).is_err());
        }
        let mut unknown = value;
        unknown["publishing_authorized"] = serde_json::json!(true);
        assert!(serde_json::from_value::<FontPortableCopyDeclaration>(unknown).is_err());
        for invalid in [0, 2, u32::MAX] {
            let mut bad = declaration.clone();
            bad.version = invalid;
            assert!(bad.validate().is_err());
        }
        for invalid in [
            "".to_string(),
            " ".to_string(),
            "\nlicense".to_string(),
            "x".repeat(2049),
        ] {
            let mut bad = declaration.clone();
            bad.license = invalid;
            assert!(bad.validate().is_err());
        }
        let mut prohibited = declaration;
        prohibited.permission = FontPortableCopyPermission::Prohibited;
        prohibited.origin.clear();
        prohibited.license.clear();
        prohibited.validate().unwrap();
    }

    #[test]
    fn copy_change_rejects_foreign_or_changed_source_before_mutation() {
        let (mut project, declaration) = fixture();
        let before = project.clone();
        for wrong in [Uuid::now_v7(), Uuid::nil()] {
            let mut bad = declaration.clone();
            bad.asset_id = wrong;
            assert!(
                project
                    .apply_change(&Change::UpsertFontPortableCopy { declaration: bad })
                    .is_err()
            );
            assert_eq!(project, before);
        }
        let mut changed = declaration;
        changed.content_sha256 = "b2".repeat(32);
        assert!(
            project
                .apply_change(&Change::UpsertFontPortableCopy {
                    declaration: changed
                })
                .is_err()
        );
        assert_eq!(project, before);
    }

    #[test]
    fn use_rights_are_not_changed_by_recording_portable_copy_permission() {
        let (mut project, declaration) = fixture();
        let before_language = project.visual_language.clone();
        let before_assets = project.assets.clone();
        project
            .apply_change(&Change::UpsertFontPortableCopy {
                declaration: declaration.clone(),
            })
            .unwrap();
        assert_eq!(project.visual_language, before_language);
        assert_eq!(project.assets, before_assets);
        assert_eq!(
            project.production_design.font_portable_copies.as_slice(),
            std::slice::from_ref(&declaration)
        );
        project
            .apply_change(&Change::RemoveFontPortableCopy {
                asset_id: declaration.asset_id,
            })
            .unwrap();
        assert!(project.production_design.font_portable_copies.is_empty());
        assert_eq!(project.visual_language, before_language);
    }

    #[test]
    fn copy_changes_respect_both_content_and_style_locks() {
        for kind in [LockKind::Content, LockKind::Style] {
            let (mut project, declaration) = fixture();
            project
                .apply_change(&Change::UpsertFontPortableCopy {
                    declaration: declaration.clone(),
                })
                .unwrap();
            project
                .apply_change(&Change::SetLock {
                    resource: project.resource_key(),
                    kind,
                    note: "Preserve the recorded source decision".into(),
                })
                .unwrap();
            let before = project.clone();
            let mut prohibited = declaration.clone();
            prohibited.permission = FontPortableCopyPermission::Prohibited;
            assert!(matches!(
                project.apply_change(&Change::UpsertFontPortableCopy {
                    declaration: prohibited
                }),
                Err(DomainError::Locked(_))
            ));
            assert_eq!(project, before);
            assert!(matches!(
                project.apply_change(&Change::RemoveFontPortableCopy {
                    asset_id: declaration.asset_id
                }),
                Err(DomainError::Locked(_))
            ));
            assert_eq!(project, before);
        }
    }

    #[test]
    fn historical_copy_declarations_survive_missing_or_replaced_assets() {
        let (mut project, mut declaration) = fixture();
        declaration.permission = FontPortableCopyPermission::Prohibited;
        project
            .apply_change(&Change::UpsertFontPortableCopy {
                declaration: declaration.clone(),
            })
            .unwrap();
        project.assets.clear();
        project.validate().unwrap();
        let bytes = serde_json::to_vec(&project).unwrap();
        let reopened: Project = serde_json::from_slice(&bytes).unwrap();
        reopened.validate().unwrap();
        assert_eq!(
            reopened.production_design.font_portable_copies,
            [declaration]
        );
    }
}
