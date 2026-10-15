//! One source-bound, owner-reviewed color/wordmark rollout to existing
//! editable semantic components. Not a new campaign renderer or brand IAM.
use crate::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

pub const BRAND_ROLLOUT_SCHEMA: &str = "motionwright.brand-campaign-rollout/1";
pub const MAX_BRAND_ROLLOUT_SCENES: usize = 8;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrandCampaignRollout {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub expected_revision: u64,
    pub brand_sha256: String,
    pub campaign: String,
    pub scene_ids: Vec<Uuid>,
    /// Only existing BrandProfile.allowed_accents may be propagated.
    pub accent: Option<String>,
    /// Only existing BrandProfile.required_wordmark may be propagated.
    pub wordmark: Option<String>,
    pub reviewer_declaration: String,
    pub rationale: String,
    pub accepts_semantic_component_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrandCampaignChange {
    pub scene_id: Uuid,
    pub hero_id: Uuid,
    pub scene_name: String,
    pub from_accent: String,
    pub to_accent: String,
    pub from_wordmark: String,
    pub to_wordmark: String,
    pub changed: bool,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrandCampaignPreview {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub brand_sha256: String,
    pub rows: Vec<BrandCampaignChange>,
    pub changed_count: usize,
    pub owner_review_required: bool,
    pub committed: bool,
    pub output_rendered: bool,
    pub publication_approved: bool,
}
fn bounded(text: &str, max: usize) -> bool {
    !text.trim().is_empty() && text.len() <= max && !text.chars().any(char::is_control)
}
fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl Project {
    /// Read-only preflight; no project mutation or rerender occurs.
    pub fn preview_brand_campaign(
        &self,
        proposal: &BrandCampaignRollout,
    ) -> Result<BrandCampaignPreview> {
        let profile = self
            .production_design
            .brand_profile
            .as_ref()
            .ok_or_else(|| {
                DomainError::Invalid("Brand campaign needs a current, saved BrandProfile".into())
            })?;
        profile.validate()?;
        let sha = profile.content_digest()?;
        if proposal.schema != BRAND_ROLLOUT_SCHEMA
            || proposal.project_id != self.id
            || proposal.generation != self.generation
            || proposal.expected_revision != self.revision
            || !digest(&proposal.brand_sha256)
            || proposal.brand_sha256 != sha
            || !bounded(&proposal.campaign, 160)
            || !bounded(&proposal.reviewer_declaration, 160)
            || !bounded(&proposal.rationale, 1200)
            || !proposal.accepts_semantic_component_rebuild
            || proposal.scene_ids.is_empty()
            || proposal.scene_ids.len() > MAX_BRAND_ROLLOUT_SCENES
            || (proposal.accent.is_none() && proposal.wordmark.is_none())
        {
            return Err(DomainError::Invalid(
                "Brand rollout must be explicitly reviewed, source-bound and limited to eight scenes".into()
            ));
        }
        self.ensure_unlocked(
            &self.resource_key(),
            &[
                LockKind::Content,
                LockKind::Style,
                LockKind::Position,
                LockKind::Timing,
            ],
        )?;
        let mut unique = BTreeSet::new();
        if proposal.scene_ids.iter().any(|id| !unique.insert(id)) {
            return Err(DomainError::Invalid(
                "Brand campaign lists a scene twice".into(),
            ));
        }
        let accent_rule = profile.rules.iter().find_map(|rule| match rule {
            BrandRule::AllowedAccents { id, colors } => Some((*id, colors)),
            _ => None,
        });
        let mark_rule = profile.rules.iter().find_map(|rule| match rule {
            BrandRule::RequiredWordmark { id, text } => Some((*id, text)),
            _ => None,
        });
        if let Some(accent) = &proposal.accent {
            let Some((_, palette)) = accent_rule else {
                return Err(DomainError::Invalid(
                    "No saved palette authorizes brand accent rollout".into(),
                ));
            };
            if !palette
                .iter()
                .any(|color| color.eq_ignore_ascii_case(accent))
            {
                return Err(DomainError::Invalid(
                    "Proposed accent is not in the exact saved BrandProfile".into(),
                ));
            }
        }
        if let Some(mark) = &proposal.wordmark {
            let Some((_, required)) = mark_rule else {
                return Err(DomainError::Invalid(
                    "No saved required wordmark authorizes rollout".into(),
                ));
            };
            if required != mark || mark.chars().count() > 8 {
                return Err(DomainError::Invalid(
                    "Wordmark must exactly match the saved mandatory eight-character Hero value"
                        .into(),
                ));
            }
        }
        let mut rows = Vec::with_capacity(proposal.scene_ids.len());
        for scene_id in &proposal.scene_ids {
            let scene = self
                .scenes
                .iter()
                .find(|scene| scene.id == *scene_id)
                .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
            if matches!(scene.status, SceneStatus::Approved | SceneStatus::Review)
                || self.reviews.iter().any(|review| {
                    review.anchor.resource == format!("scene:{scene_id}")
                        && matches!(
                            review.status,
                            ReviewStatus::Open | ReviewStatus::NeedsRecheck
                        )
                })
            {
                return Err(DomainError::Invalid(
                    "Protected scene/review must be resolved before brand rollout".into(),
                ));
            }
            let hero = self
                .production_design
                .heroes
                .iter()
                .find(|hero| hero.scene_id == *scene_id)
                .ok_or_else(|| {
                    DomainError::Invalid(
                        "Brand rollout accepts existing attached Hero components only".into(),
                    )
                })?;
            for rule in [
                accent_rule
                    .map(|v| v.0)
                    .filter(|_| proposal.accent.is_some()),
                mark_rule
                    .map(|v| v.0)
                    .filter(|_| proposal.wordmark.is_some()),
            ]
            .into_iter()
            .flatten()
            {
                if self
                    .production_design
                    .brand_exceptions
                    .iter()
                    .any(|exception| exception.scene_id == *scene_id && exception.rule_id == rule)
                {
                    return Err(DomainError::Invalid(
                        "Source scene has a brand exception; revoke or resolve it first".into(),
                    ));
                }
            }
            let to_accent = proposal
                .accent
                .clone()
                .unwrap_or_else(|| hero.config.accent.clone());
            let to_wordmark = proposal
                .wordmark
                .clone()
                .unwrap_or_else(|| hero.config.wordmark.clone());
            rows.push(BrandCampaignChange {
                scene_id: *scene_id,
                hero_id: hero.id,
                scene_name: scene.name.clone(),
                from_accent: hero.config.accent.clone(),
                to_accent: to_accent.clone(),
                from_wordmark: hero.config.wordmark.clone(),
                to_wordmark: to_wordmark.clone(),
                changed: hero.config.accent != to_accent || hero.config.wordmark != to_wordmark,
            });
        }
        let count = rows.iter().filter(|row| row.changed).count();
        if count == 0 {
            return Err(DomainError::Invalid(
                "No brand fields would change in the selected original scenes".into(),
            ));
        }
        Ok(BrandCampaignPreview {
            schema: BRAND_ROLLOUT_SCHEMA.into(),
            project_id: self.id,
            generation: self.generation,
            revision: self.revision,
            brand_sha256: sha,
            rows,
            changed_count: count,
            owner_review_required: true,
            committed: false,
            output_rendered: false,
            publication_approved: false,
        })
    }
    pub fn apply_brand_campaign(&mut self, proposal: &BrandCampaignRollout) -> Result<()> {
        let preview = self.preview_brand_campaign(proposal)?;
        let mut candidate = self.clone();
        for row in &preview.rows {
            if !row.changed {
                continue;
            }
            let hero = candidate
                .production_design
                .heroes
                .iter()
                .find(|item| item.id == row.hero_id)
                .ok_or_else(|| DomainError::NotFound(format!("hero:{}", row.hero_id)))?;
            let mut config = hero.config.clone();
            config.accent = row.to_accent.clone();
            config.wordmark = row.to_wordmark.clone();
            // Reuse existing semantic object three-way merge: protected author
            // overrides or a late scene/style lock refuse the *entire* rollout.
            candidate.upsert_product_hero(row.hero_id, row.scene_id, &config)?;
            if candidate.production_design.project_decisions.len() >= 256 {
                return Err(DomainError::Invalid(
                    "Campaign decision history capacity exceeded".into(),
                ));
            }
            candidate
                .production_design
                .project_decisions
                .push(CreativeDecision {
                    id: Uuid::new_v4(),
                    scene_id: row.scene_id,
                    author: proposal.reviewer_declaration.clone(),
                    decision: format!(
                        "Campaign {}: brand accent {} to {}, wordmark {} to {}; policy SHA-256 {}",
                        proposal.campaign,
                        row.from_accent,
                        row.to_accent,
                        row.from_wordmark,
                        row.to_wordmark,
                        proposal.brand_sha256
                    ),
                    rationale: proposal.rationale.clone(),
                });
        }
        candidate.validate()?;
        *self = candidate;
        Ok(())
    }
}
