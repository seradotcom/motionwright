//! Atomic authored source binding, with no inherited native execution authority.
use crate::{
    Asset, DomainError, Fidelity, FidelityReport, LockKind, NativeCapsule, Project, Result,
    design_text,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Closed source-save metadata. Source digests, fidelity and grants are not inputs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeCapsuleSourceSpec {
    pub name: String,
    pub media_type: String,
    pub capsule_id: Uuid,
    pub scene_id: Uuid,
    pub label: String,
    pub editable_parameters: Vec<String>,
    pub native_editor_hint: String,
}

impl NativeCapsuleSourceSpec {
    /// Bind metadata to captured source bytes; project-dependent guards run on apply.
    pub fn bind_asset(&self, asset: &Asset) -> Result<NativeCapsule> {
        asset.validate()?;
        if asset.id.is_nil()
            || self.capsule_id.is_nil()
            || self.scene_id.is_nil()
            || asset.name != self.name
            || asset.media_type != self.media_type
            || asset.source_revision.as_deref() != Some("native-capsule-source-import")
        {
            return Err(DomainError::Invalid(
                "native source import must bind exact captured asset metadata and stable ids"
                    .into(),
            ));
        }
        let source_sha256 = asset.content_sha256.as_ref().ok_or_else(|| {
            DomainError::Invalid("native source import needs an immutable source digest".into())
        })?;
        design_text(&self.label, 256, "native capsule label")?;
        design_text(&self.native_editor_hint, 256, "native editor label")?;
        if self.editable_parameters.len() > 64 {
            return Err(DomainError::Invalid(
                "native source editable parameter list exceeds 64 entries".into(),
            ));
        }
        for parameter in &self.editable_parameters {
            design_text(parameter, 2000, "native editable parameter")?;
        }
        Ok(NativeCapsule {
            id: self.capsule_id,
            scene_id: self.scene_id,
            source_asset_id: asset.id,
            source_sha256: source_sha256.clone(),
            label: self.label.clone(),
            // Persisted receipt contract: keep these defaults stable. A future
            // version must retain historical decoding, including loss wording.
            fidelity: FidelityReport {
                renderer: "source-import".into(),
                renderer_version: "unverified".into(),
                visual: Fidelity::Unavailable,
                temporal: Fidelity::Unavailable,
                structural: Fidelity::Unavailable,
                editable: Fidelity::Unavailable,
                losses: vec![
                    "Imported source has no verified native realization or fidelity evidence."
                        .into(),
                ],
                evidence_sha256: None,
            },
            editable_parameters: self.editable_parameters.clone(),
            native_editor_hint: self.native_editor_hint.clone(),
            property_fidelity: Vec::new(),
        })
    }
}

impl Project {
    pub(crate) fn import_native_capsule_source(
        &mut self,
        asset: &Asset,
        capsule: &NativeCapsule,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        self.ensure_unlocked(&format!("scene:{}", capsule.scene_id), &[LockKind::Content])?;
        let existing = self
            .production_design
            .capsules
            .iter()
            .position(|item| item.id == capsule.id);
        if let Some(index) = existing {
            self.ensure_unlocked(
                &format!("scene:{}", self.production_design.capsules[index].scene_id),
                &[LockKind::Content],
            )?;
        } else if self.production_design.capsules.len() >= 256 {
            return Err(DomainError::Invalid("capsule budget exceeded".into()));
        }
        if self.assets.iter().any(|current| current.id == asset.id) {
            return Err(DomainError::Invalid(
                "native source import contains a duplicate asset id".into(),
            ));
        }

        // Public Change construction must not bypass the closed source-import
        // contract by carrying old fidelity, evidence or another source binding.
        let spec = NativeCapsuleSourceSpec {
            name: asset.name.clone(),
            media_type: asset.media_type.clone(),
            capsule_id: capsule.id,
            scene_id: capsule.scene_id,
            label: capsule.label.clone(),
            editable_parameters: capsule.editable_parameters.clone(),
            native_editor_hint: capsule.native_editor_hint.clone(),
        };
        if spec.bind_asset(asset)? != *capsule {
            return Err(DomainError::Invalid(
                "native source import cannot inherit fidelity or alter its source binding".into(),
            ));
        }
        let mut assets = self.assets.clone();
        assets.push(asset.clone());
        capsule.validate(&self.scenes, &assets)?;
        self.assets = assets;
        if let Some(index) = existing {
            self.production_design.capsules[index] = capsule.clone();
        } else {
            self.production_design.capsules.push(capsule.clone());
        }
        self.validate()
    }
}
