//! MW05-E12-04 · Consent/provenance boundaries for proposed synthetic identities.
//!
//! These are rights declarations, NOT authenticated identity certificates.
//! Registering an identity never grants a generation provider permission to
//! synthesize a real person's voice, face, or presenter. Owner review and
//! independent source/consent verification remain separate prerequisites.
use crate::*;
use chrono::{DateTime, SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

pub const IDENTITY_SOURCE_SCHEMA: &str = "motionwright.identity-origin/1";
pub const IDENTITY_DISCLOSURE_FICTIONAL: &str = "SYNTHETIC ORIGINAL CHARACTER / NOT A REAL PERSON";
pub const IDENTITY_DISCLOSURE_HUMAN: &str =
    "SYNTHETIC HUMAN LIKENESS / CONSENT REQUIRES INDEPENDENT REVIEW";
pub const IDENTITY_MAX_RECORDS: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityOrigin {
    OriginalFictional,
    HumanLikeness,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityModality {
    Voice,
    Face,
    Presenter,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityPurpose {
    ProductDemonstration,
    Marketing,
    Education,
    InternalReview,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityOriginRecord {
    pub schema: String,
    pub id: Uuid,
    pub source_project_id: Uuid,
    /// Historical generation does not change when a portable project imports.
    pub source_generation: Uuid,
    pub origin: IdentityOrigin,
    /// A project-local name, never an assertion that a human is authenticated.
    pub display_label: String,
    pub origin_description: String,
    pub created_by_declaration: String,
    pub created_at_utc: String,
    pub expires_at_utc: String,
    pub allowed_modalities: Vec<IdentityModality>,
    pub allowed_purposes: Vec<IdentityPurpose>,
    /// Imported JSON provenance manifest. The Asset SHA-256 is a byte identity,
    /// not proof that its author has the rights being claimed.
    pub provenance_asset_id: Uuid,
    pub provenance_sha256: String,
    /// Required together for a human likeness, but still not verified consent.
    pub consent_asset_id: Option<Uuid>,
    pub consent_sha256: Option<String>,
    pub consent_subject_declaration: Option<String>,
    pub consent_scope_declaration: Option<String>,
    /// A fixed output label; no creative tool may remove it while source-bound.
    pub synthetic_disclosure: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityRevocation {
    pub id: Uuid,
    pub identity_id: Uuid,
    pub revoked_at_utc: String,
    pub reason: String,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdentityReviewOutcome {
    Revoked,
    OutsideValidPeriod,
    OutsideDeclaredScope,
    HumanConsentNeedsIndependentReview,
    FictionalSourceNeedsOwnerApproval,
}

fn text_is_bounded(value: &str, limit: usize) -> bool {
    !value.trim().is_empty()
        && value.chars().count() <= limit
        && !value.chars().any(char::is_control)
}
fn is_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn canonical_utc(value: &str) -> Option<DateTime<Utc>> {
    let dt = DateTime::parse_from_rfc3339(value)
        .ok()?
        .with_timezone(&Utc);
    (value == dt.to_rfc3339_opts(SecondsFormat::Secs, true)).then_some(dt)
}
impl IdentityOriginRecord {
    pub fn validate(&self, project: &Project) -> Result<()> {
        let issued = canonical_utc(&self.created_at_utc).ok_or_else(|| {
            DomainError::Invalid("identity provenance needs an exact UTC issued time".into())
        })?;
        let expires = canonical_utc(&self.expires_at_utc).ok_or_else(|| {
            DomainError::Invalid("identity provenance needs an exact UTC expiry time".into())
        })?;
        if self.schema != IDENTITY_SOURCE_SCHEMA
            || self.source_project_id != project.id
            || self.source_generation.is_nil()
            || !text_is_bounded(&self.display_label, 96)
            || !text_is_bounded(&self.origin_description, 300)
            || !text_is_bounded(&self.created_by_declaration, 160)
            || expires <= issued
            || (expires - issued) > chrono::Duration::days(366)
        {
            return Err(DomainError::Invalid(
                "identity provenance must identify a bounded source, owner declaration and one-year maximum window".into(),
            ));
        }
        let modalities = self
            .allowed_modalities
            .iter()
            .copied()
            .collect::<BTreeSet<_>>();
        let purposes = self
            .allowed_purposes
            .iter()
            .copied()
            .collect::<BTreeSet<_>>();
        if modalities.is_empty()
            || modalities.len() != self.allowed_modalities.len()
            || purposes.is_empty()
            || purposes.len() != self.allowed_purposes.len()
            || self.allowed_modalities.len() > 3
            || self.allowed_purposes.len() > 4
        {
            return Err(DomainError::Invalid(
                "identity media modalities and purposes must be explicit and nonduplicated".into(),
            ));
        }
        if !is_digest(&self.provenance_sha256)
            || !project.assets.iter().any(|asset| {
                asset.id == self.provenance_asset_id
                    && asset.media_type == "application/json"
                    && asset.content_sha256.as_deref() == Some(&self.provenance_sha256)
            })
        {
            return Err(DomainError::Invalid(
                "identity source requires a content-addressed imported JSON provenance Asset"
                    .into(),
            ));
        }
        match self.origin {
            IdentityOrigin::OriginalFictional => {
                if self.consent_asset_id.is_some()
                    || self.consent_sha256.is_some()
                    || self.consent_subject_declaration.is_some()
                    || self.consent_scope_declaration.is_some()
                    || self.synthetic_disclosure != IDENTITY_DISCLOSURE_FICTIONAL
                {
                    return Err(DomainError::Invalid(
                        "fictional original source must not impersonate a human consent receipt or omit disclosure".into(),
                    ));
                }
            }
            IdentityOrigin::HumanLikeness => {
                let Some(consent_id) = self.consent_asset_id else {
                    return Err(DomainError::Invalid(
                        "human likeness requires consent evidence".into(),
                    ));
                };
                let Some(consent_digest) = self.consent_sha256.as_ref() else {
                    return Err(DomainError::Invalid(
                        "human likeness requires consent digest".into(),
                    ));
                };
                if self.provenance_asset_id == consent_id
                    || !is_digest(consent_digest)
                    || !self
                        .consent_subject_declaration
                        .as_deref()
                        .is_some_and(|v| text_is_bounded(v, 160))
                    || !self
                        .consent_scope_declaration
                        .as_deref()
                        .is_some_and(|v| text_is_bounded(v, 300))
                    || self.synthetic_disclosure != IDENTITY_DISCLOSURE_HUMAN
                    || !project.assets.iter().any(|asset| {
                        asset.id == consent_id
                            && asset.media_type == "application/json"
                            && asset.content_sha256.as_deref() == Some(consent_digest.as_str())
                    })
                {
                    return Err(DomainError::Invalid(
                        "human likeness must cite distinct consent bytes, named subject and narrowly declared scope".into(),
                    ));
                }
            }
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn register_identity_origin(&mut self, record: &IdentityOriginRecord) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        if self.production_design.identity_origins.len() >= IDENTITY_MAX_RECORDS
            || self.production_design.identity_origins.iter().any(|item| {
                item.id == record.id
                    || item.provenance_asset_id == record.provenance_asset_id
                    || (item.consent_sha256.is_some()
                        && item.consent_sha256 == record.consent_sha256)
            })
        {
            return Err(DomainError::Invalid(
                "identity source capacity or duplicate immutable ID".into(),
            ));
        }
        record.validate(self)?;
        if record.source_generation != self.generation {
            return Err(DomainError::Invalid(
                "new identity declaration must use this project's current generation".into(),
            ));
        }
        self.production_design.identity_origins.push(record.clone());
        Ok(())
    }
    pub(crate) fn revoke_identity_origin(&mut self, revocation: &IdentityRevocation) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        if self.production_design.identity_revocations.len() >= IDENTITY_MAX_RECORDS
            || self
                .production_design
                .identity_revocations
                .iter()
                .any(|item| item.id == revocation.id || item.identity_id == revocation.identity_id)
        {
            return Err(DomainError::Invalid(
                "identity already revoked or revocation capacity exhausted".into(),
            ));
        }
        let origin = self
            .production_design
            .identity_origins
            .iter()
            .find(|item| item.id == revocation.identity_id)
            .ok_or_else(|| DomainError::NotFound(format!("identity:{}", revocation.identity_id)))?;
        let time = canonical_utc(&revocation.revoked_at_utc).ok_or_else(|| {
            DomainError::Invalid("revocation needs an exact UTC timestamp".into())
        })?;
        if canonical_utc(&origin.created_at_utc).is_none_or(|issued| time < issued)
            || !text_is_bounded(&revocation.reason, 240)
        {
            return Err(DomainError::Invalid(
                "revocation reason/time must not precede original source declaration".into(),
            ));
        }
        self.production_design
            .identity_revocations
            .push(revocation.clone());
        Ok(())
    }
    /// Advisory guard only. This is never a permission to run a presenter
    /// generator: even original fictional sources require separate owner
    /// approval and humans require independent consent validation.
    pub fn review_identity_use(
        &self,
        identity_id: Uuid,
        as_of_utc: &str,
        modality: IdentityModality,
        purpose: IdentityPurpose,
    ) -> Result<IdentityReviewOutcome> {
        let at = canonical_utc(as_of_utc)
            .ok_or_else(|| DomainError::Invalid("usage time must be exact UTC".into()))?;
        let origin = self
            .production_design
            .identity_origins
            .iter()
            .find(|identity| identity.id == identity_id)
            .ok_or_else(|| DomainError::NotFound(format!("identity:{identity_id}")))?;
        if self
            .production_design
            .identity_revocations
            .iter()
            .any(|item| item.identity_id == identity_id)
        {
            return Ok(IdentityReviewOutcome::Revoked);
        }
        let issued =
            canonical_utc(&origin.created_at_utc).expect("project source already validated");
        let expiry =
            canonical_utc(&origin.expires_at_utc).expect("project source already validated");
        if at < issued || at >= expiry {
            return Ok(IdentityReviewOutcome::OutsideValidPeriod);
        }
        if !origin.allowed_modalities.contains(&modality)
            || !origin.allowed_purposes.contains(&purpose)
        {
            return Ok(IdentityReviewOutcome::OutsideDeclaredScope);
        }
        Ok(match origin.origin {
            IdentityOrigin::OriginalFictional => {
                IdentityReviewOutcome::FictionalSourceNeedsOwnerApproval
            }
            IdentityOrigin::HumanLikeness => {
                IdentityReviewOutcome::HumanConsentNeedsIndependentReview
            }
        })
    }
}
impl ProductionDesign {
    pub(crate) fn validate_identity_origins(&self, project: &Project) -> Result<()> {
        if self.identity_origins.len() > IDENTITY_MAX_RECORDS
            || self.identity_revocations.len() > IDENTITY_MAX_RECORDS
        {
            return Err(DomainError::Invalid(
                "identity evidence collection capacity exceeded".into(),
            ));
        }
        let mut identities = BTreeSet::new();
        let mut manifests = BTreeSet::new();
        let mut consent_digests = BTreeSet::new();
        for origin in &self.identity_origins {
            if !identities.insert(origin.id)
                || !manifests.insert(origin.provenance_asset_id)
                || origin
                    .consent_sha256
                    .as_ref()
                    .is_some_and(|sha| !consent_digests.insert(sha.clone()))
            {
                return Err(DomainError::Invalid(
                    "duplicate identity origin or reused consent source".into(),
                ));
            }
            origin.validate(project)?;
        }
        let mut revoked = BTreeSet::new();
        let mut event_ids = BTreeSet::new();
        for item in &self.identity_revocations {
            if !event_ids.insert(item.id)
                || !revoked.insert(item.identity_id)
                || !identities.contains(&item.identity_id)
                || canonical_utc(&item.revoked_at_utc).is_none()
                || !text_is_bounded(&item.reason, 240)
            {
                return Err(DomainError::Invalid(
                    "invalid immutable identity revocation history".into(),
                ));
            }
            let origin = self
                .identity_origins
                .iter()
                .find(|origin| origin.id == item.identity_id)
                .expect("record identity present");
            if canonical_utc(&item.revoked_at_utc) < canonical_utc(&origin.created_at_utc) {
                return Err(DomainError::Invalid(
                    "revocation precedes identity creation".into(),
                ));
            }
        }
        Ok(())
    }
}
