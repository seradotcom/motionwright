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

/// Conditional reference change to an already retained, digest-bound asset.
/// No bytes, renderer grants, or claims of fidelity may be supplied here.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeCapsuleSourceRelink {
    pub capsule_id: Uuid,
    pub scene_id: Uuid,
    pub expected_source_asset_id: Uuid,
    pub expected_source_sha256: String,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
}

impl Project {
    pub(crate) fn relink_native_capsule_source(
        &mut self,
        relink: &NativeCapsuleSourceRelink,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        self.ensure_unlocked(&format!("scene:{}", relink.scene_id), &[LockKind::Content])?;
        let mut matches = self
            .production_design
            .capsules
            .iter()
            .enumerate()
            .filter(|(_, capsule)| capsule.id == relink.capsule_id);
        let (index, current) = matches
            .next()
            .ok_or_else(|| DomainError::Invalid("Native capsule no longer exists".into()))?;
        if matches.next().is_some()
            || current.scene_id != relink.scene_id
            || current.source_asset_id != relink.expected_source_asset_id
            || current.source_sha256 != relink.expected_source_sha256
        {
            return Err(DomainError::Invalid(
                "Native capsule source changed; review the saved binding".into(),
            ));
        }
        current.validate(&self.scenes, &self.assets)?;
        let find_asset = |id: Uuid, digest: &str| -> Result<&Asset> {
            let mut candidates = self.assets.iter().filter(|asset| asset.id == id);
            let asset = candidates.next().ok_or_else(|| {
                DomainError::Invalid("Retained source asset is unavailable".into())
            })?;
            if candidates.next().is_some() || asset.content_sha256.as_deref() != Some(digest) {
                return Err(DomainError::Invalid(
                    "Retained source asset identity or digest differs".into(),
                ));
            }
            asset.validate()?;
            Ok(asset)
        };
        let previous = find_asset(current.source_asset_id, &current.source_sha256)?;
        let replacement = find_asset(relink.source_asset_id, &relink.source_sha256)?;
        if replacement.id == previous.id || replacement.media_type != previous.media_type {
            return Err(DomainError::Invalid(
                "Choose a different retained asset with the same source media type".into(),
            ));
        }
        let mut capsule = current.clone();
        capsule.source_asset_id = replacement.id;
        capsule.source_sha256 = relink.source_sha256.clone();
        capsule.fidelity = FidelityReport {
            renderer: "source-relink".into(),
            renderer_version: "unverified".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Unavailable,
            editable: Fidelity::Unavailable,
            losses: vec!["Relinked source has no verified native realization or fidelity evidence. Review its editable parameters again.".into()],
            evidence_sha256: None,
        };
        capsule.editable_parameters.clear();
        capsule.property_fidelity.clear();
        capsule.validate(&self.scenes, &self.assets)?;
        self.production_design.capsules[index] = capsule;
        self.validate()
    }

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
