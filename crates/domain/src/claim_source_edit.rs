//! In-place claim identity with an exact reviewed preimage.
//! The existing Project/Store revision and receipt remain the write authority.
use crate::{Claim, DomainError, LockKind, Project, Result, SourceReference};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClaimEditableFields {
    pub text: String,
    #[serde(deserialize_with = "required_option")]
    pub source: Option<SourceReference>,
    pub context: String,
    #[serde(deserialize_with = "required_option")]
    pub source_revision: Option<String>,
}

// Replacement and preimage are complete records: null is an explicit value,
// while an omitted nullable field must never silently remove source provenance.
fn required_option<'de, D, T>(deserializer: D) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

impl From<&Claim> for ClaimEditableFields {
    fn from(claim: &Claim) -> Self {
        Self {
            text: claim.text.clone(),
            source: claim.source.clone(),
            context: claim.context.clone(),
            source_revision: claim.source_revision.clone(),
        }
    }
}

impl Project {
    pub(crate) fn update_claim_source(
        &mut self,
        claim_id: Uuid,
        expected: &ClaimEditableFields,
        replacement: &ClaimEditableFields,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        self.ensure_unlocked(&format!("claim:{claim_id}"), &[LockKind::Content])?;
        let index = self
            .brief
            .claims
            .iter()
            .position(|claim| claim.id == claim_id)
            .ok_or_else(|| DomainError::NotFound(format!("claim:{claim_id}")))?;
        if ClaimEditableFields::from(&self.brief.claims[index]) != *expected {
            return Err(DomainError::Invalid(
                "claim source edit preimage differs from the current claim".into(),
            ));
        }
        if expected == replacement {
            return Err(DomainError::Invalid(
                "claim source edit has no changes".into(),
            ));
        }
        // Asset references need this project's context; the general claim
        // validator only checks the source's wire shape.
        if let Some(SourceReference::Asset { asset_id }) = &replacement.source
            && !self.assets.iter().any(|asset| asset.id == *asset_id)
        {
            return Err(DomainError::NotFound(format!("asset:{asset_id}")));
        }
        if let Some(SourceReference::ProjectRevision { revision }) = &replacement.source
            && *revision > self.revision
        {
            return Err(DomainError::Invalid(
                "claim source revision must exist in the authored project".into(),
            ));
        }
        let mut candidate = self.clone();
        candidate.brief.claims[index] = Claim {
            id: claim_id,
            text: replacement.text.clone(),
            source: replacement.source.clone(),
            context: replacement.context.clone(),
            source_revision: replacement.source_revision.clone(),
        };
        // Validate before publishing any field. This includes existing asset
        // references, branch invariants and immutable capture-to-claim links.
        candidate.validate()?;
        *self = candidate;
        Ok(())
    }
}
