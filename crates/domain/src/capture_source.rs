//! Closed first-party screen-capture provenance in the existing project CAS.
//!
//! This is a declared mapping to imported immutable files and one authored
//! project-revision claim. It does not certify screenshot privacy, rights,
//! product behavior, artistic approval, or external software provenance.
//! Independent byte/pixel verification is an explicit separate acceptance.
use crate::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

pub const CAPTURE_RECORD_SCHEMA: &str = "motionwright.capture-source/1";
const FIRST_PARTY_ORIGIN: &str = "http://127.0.0.1:1420";
const FIRST_PARTY_APP: &str = "motionwright-studio";
const FIRST_PARTY_SCENARIO: &str = "production-original-ui-local-v1";

fn sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn commit_sha(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureMask {
    /// Only the explicitly admitted first-party ephemeral revision chip.
    pub selector: String,
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub mask_color: String,
}
impl CaptureMask {
    pub fn validate(&self, width: u32, height: u32) -> Result<()> {
        let area = u64::from(self.width) * u64::from(self.height);
        if self.selector != ".revision-chip"
            || self.mask_color != "#0B0B0B"
            || self.width < 12
            || self.height < 12
            || self.x.checked_add(self.width).is_none_or(|x| x > width)
            || self.y.checked_add(self.height).is_none_or(|y| y > height)
            || area >= u64::from(width) * u64::from(height) / 10
        {
            return Err(DomainError::Invalid(
                "Capture redaction is outside its reviewed first-party region".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureSourceRecord {
    pub schema: String,
    pub id: Uuid,
    pub screenshot_asset_id: Uuid,
    pub screenshot_sha256: String,
    pub manifest_asset_id: Uuid,
    pub manifest_sha256: String,
    pub fixture_state_asset_id: Uuid,
    /// Hash of the exact original fixture-data state, not a claim of factual truth.
    pub fixture_state_sha256: String,
    /// A claim about observable fixture state; NOT a verified product benefit.
    pub claim_id: Uuid,
    pub source_project_id: Uuid,
    pub source_generation: Uuid,
    pub source_revision: u64,
    pub app_id: String,
    pub app_version: String,
    pub source_commit: String,
    pub scenario_id: String,
    pub origin: String,
    pub route: String,
    pub viewport_width: u32,
    pub viewport_height: u32,
    pub device_scale_factor: u8,
    pub screenshot_repeat_sha256: String,
    pub redaction: CaptureMask,
    /// Always zero for this strictly isolated first-party capture admission.
    pub external_requests: u16,
    pub credential_fields: u16,
    /// Cannot be used to assert an actual rights or authenticity review.
    pub declared_scope: String,
}
impl CaptureSourceRecord {
    pub fn validate(&self, project: &Project) -> Result<()> {
        if self.schema != CAPTURE_RECORD_SCHEMA
            || self.source_project_id != project.id
            || self.source_revision == 0
            || self.source_revision > project.revision
            || self.source_generation.is_nil()
            || self.app_id != FIRST_PARTY_APP
            || self.scenario_id != FIRST_PARTY_SCENARIO
            || self.origin != FIRST_PARTY_ORIGIN
            || self.route != "/"
            || self.viewport_width != 1440
            || self.viewport_height != 940
            || self.device_scale_factor != 1
            || self.external_requests != 0
            || self.credential_fields != 0
            || self.declared_scope != "first-party-synthetic-fixture-only"
            || !commit_sha(&self.source_commit)
            || !sha256(&self.screenshot_sha256)
            || !sha256(&self.manifest_sha256)
            || !sha256(&self.fixture_state_sha256)
            || self.screenshot_repeat_sha256 != self.screenshot_sha256
        {
            return Err(DomainError::Invalid(
                "Capture claim is not pinned to the admitted first-party source, viewport and revision".into(),
            ));
        }
        let version: Vec<&str> = self.app_version.split('.').collect();
        if version.len() != 3
            || version.iter().any(|part| {
                part.is_empty() || part.len() > 5 || !part.bytes().all(|byte| byte.is_ascii_digit())
            })
        {
            return Err(DomainError::Invalid(
                "Capture application version is not pinned".into(),
            ));
        }
        self.redaction
            .validate(self.viewport_width, self.viewport_height)?;
        if self.screenshot_asset_id == self.manifest_asset_id
            || self.screenshot_asset_id == self.fixture_state_asset_id
            || self.manifest_asset_id == self.fixture_state_asset_id
            || !project.assets.iter().any(|asset| {
                asset.id == self.fixture_state_asset_id
                    && asset.media_type == "application/json"
                    && asset.content_sha256.as_deref() == Some(self.fixture_state_sha256.as_str())
            })
            || !project.assets.iter().any(|asset| {
                asset.id == self.screenshot_asset_id
                    && asset.media_type == "image/png"
                    && asset.content_sha256.as_deref() == Some(self.screenshot_sha256.as_str())
            })
            || !project.assets.iter().any(|asset| {
                asset.id == self.manifest_asset_id
                    && asset.media_type == "application/json"
                    && asset.content_sha256.as_deref() == Some(self.manifest_sha256.as_str())
            })
        {
            return Err(DomainError::Invalid(
                "Capture requires independently imported content-addressed PNG and JSON evidence assets".into(),
            ));
        }
        if !project.brief.claims.iter().any(|claim| {
            claim.id == self.claim_id
                && claim.context == "synthetic-first-party-capture-validation"
                && matches!(claim.source.as_ref(), Some(SourceReference::ProjectRevision { revision }) if *revision == self.source_revision)
        }) {
            return Err(DomainError::Invalid(
                "Capture must reference an existing first-party project-revision claim".into(),
            ));
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn register_capture_source(&mut self, record: &CaptureSourceRecord) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        if self.production_design.captures.len() >= 32
            || self
                .production_design
                .captures
                .iter()
                .any(|existing| existing.id == record.id)
        {
            return Err(DomainError::Invalid(
                "Capture evidence collection full or duplicate append-only identity".into(),
            ));
        }
        record.validate(self)?;
        if record.source_generation != self.generation {
            return Err(DomainError::Invalid(
                "new capture source must match the current project generation".into(),
            ));
        }
        self.production_design.captures.push(record.clone());
        Ok(())
    }
}
impl ProductionDesign {
    pub(crate) fn validate_capture_sources(&self, project: &Project) -> Result<()> {
        if self.captures.len() > 32 {
            return Err(DomainError::Invalid(
                "Capture provenance collection too large".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for record in &self.captures {
            if !ids.insert(record.id) {
                return Err(DomainError::Invalid(
                    "Duplicate capture provenance identity".into(),
                ));
            }
            record.validate(project)?;
        }
        Ok(())
    }
}
