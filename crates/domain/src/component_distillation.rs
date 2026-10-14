//! MW05-E05-04: immutable original-Canvas component distillation **proposals**.
//!
//! Examples are resolved from the owner's current project by canonical CAS.
//! The model never installs a skill, executes content, or admits a new renderer.
//! A human compatibility review can mark a proposal ready for a separate
//! admission decision; it is not by itself permission or creative approval.
use crate::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

pub const DISTILLATION_SCHEMA: &str = "motionwright.component-distillation/1";
pub const DISTILLATION_RENDERER: &str = "motion_canvas_original_nodes_v1";

fn bounded(s: &str, max: usize) -> bool {
    !s.trim().is_empty() && s.len() <= max && !s.chars().any(char::is_control)
}
fn role_key(s: &str) -> bool {
    (2..=32).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
        && s.as_bytes()[0].is_ascii_lowercase()
}
fn supported(kind: &str) -> bool {
    matches!(kind, "text" | "rectangle" | "circle")
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DistillationRole {
    pub key: String,
    pub kind: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DistillationSelector {
    pub scene_id: Uuid,
    /// Ordered by the role set, never arbitrary evaluation/execution input.
    pub node_ids: Vec<Uuid>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DistillationRejection {
    NonMotionCanvas,
    LockedObject,
    UnsupportedKind,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DistillationNegative {
    pub scene_id: Uuid,
    pub node_id: Option<Uuid>,
    pub rejection: DistillationRejection,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ComponentDistillationInput {
    pub id: Uuid,
    pub label: String,
    pub author: String,
    /// Human declaration; not a legal license verification.
    pub original_rights_attestation: String,
    pub source_project_id: Uuid,
    pub source_generation: Uuid,
    pub source_revision: u64,
    pub roles: Vec<DistillationRole>,
    pub positives: Vec<DistillationSelector>,
    pub negatives: Vec<DistillationNegative>,
    pub requirements: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DistillationSample {
    pub scene_id: Uuid,
    /// Frozen source-owned Canvas objects. Source changes do not rewrite proof.
    pub nodes: Vec<CanvasNode>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DistillationVerdict {
    CompatibleProposal,
    Rejected,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DistillationReview {
    pub reviewer: String,
    pub rationale: String,
    pub verdict: DistillationVerdict,
    pub source_revision: u64,
    /// Narrow supported renderer only; not a signed executable permission.
    pub compatibility: String,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ComponentDistillationProposal {
    pub schema: String,
    pub input: ComponentDistillationInput,
    pub samples: Vec<DistillationSample>,
    pub review: Option<DistillationReview>,
}
impl ComponentDistillationProposal {
    pub fn validate(&self) -> Result<()> {
        let input = &self.input;
        if self.schema != DISTILLATION_SCHEMA
            || input.id.is_nil()
            || input.source_project_id.is_nil()
            || input.source_generation.is_nil()
            || !bounded(&input.label, 120)
            || !bounded(&input.author, 120)
            || !bounded(&input.original_rights_attestation, 480)
            || !(2..=12).contains(&input.roles.len())
            || !(2..=4).contains(&input.positives.len())
            || !(1..=4).contains(&input.negatives.len())
            || !(1..=12).contains(&input.requirements.len())
            || input.positives.len() != self.samples.len()
        {
            return Err(DomainError::Invalid(
                "invalid bounded component distillation proposal".into(),
            ));
        }
        let mut role_ids = BTreeSet::new();
        for role in &input.roles {
            if !role_key(&role.key) || !supported(&role.kind) || !role_ids.insert(role.key.as_str())
            {
                return Err(DomainError::Invalid(
                    "distillation roles must be unique original semantic Canvas shapes".into(),
                ));
            }
        }
        let mut requirements = BTreeSet::new();
        for requirement in &input.requirements {
            if !bounded(requirement, 240) || !requirements.insert(requirement.as_str()) {
                return Err(DomainError::Invalid(
                    "distillation must retain distinct bounded requirements".into(),
                ));
            }
        }
        let mut scenes = BTreeSet::new();
        let mut variations = BTreeSet::new();
        for (selector, sample) in input.positives.iter().zip(&self.samples) {
            if selector.scene_id != sample.scene_id
                || !scenes.insert(selector.scene_id)
                || sample.nodes.len() != input.roles.len()
                || selector.node_ids.len() != input.roles.len()
            {
                return Err(DomainError::Invalid(
                    "positive distillation samples need separate compatible scenes".into(),
                ));
            }
            let mut ids = BTreeSet::new();
            let mut semantic = Vec::new();
            for ((id, node), role) in selector
                .node_ids
                .iter()
                .zip(&sample.nodes)
                .zip(&input.roles)
            {
                if *id != node.id
                    || !ids.insert(node.id)
                    || node.kind != role.kind
                    || node.parent_id.is_some()
                    || !node.relations.is_empty()
                    || !node.property_locks.is_empty()
                    || node.keyframes.len() > 8
                {
                    return Err(DomainError::Invalid(
                        "distillation source contains locked, related or incompatible Canvas nodes"
                            .into(),
                    ));
                }
                // Deliberately omit node identity/name: two copies of the
                // same composition cannot masquerade as two variations.
                semantic.push(
                    serde_json::to_string(&(
                        &node.kind,
                        &node.text,
                        node.x,
                        node.y,
                        node.width,
                        node.height,
                        node.opacity,
                        &node.style,
                        &node.keyframes,
                    ))
                    .map_err(|_| DomainError::Invalid("invalid semantic source sample".into()))?,
                );
            }
            if !variations.insert(semantic) {
                return Err(DomainError::Invalid(
                    "positive component examples have no actual authored variation".into(),
                ));
            }
        }
        let mut excluded = BTreeSet::new();
        for negative in &input.negatives {
            if scenes.contains(&negative.scene_id)
                || !excluded.insert((negative.scene_id, negative.node_id))
                || matches!(negative.rejection, DistillationRejection::NonMotionCanvas)
                    != negative.node_id.is_none()
            {
                return Err(DomainError::Invalid(
                    "negative distillation example is not an independent rejected source".into(),
                ));
            }
        }
        if let Some(review) = &self.review
            && (!bounded(&review.reviewer, 120)
                || !bounded(&review.rationale, 1600)
                || review.reviewer.eq_ignore_ascii_case(&input.author)
                || review.source_revision != input.source_revision
                || review.compatibility != DISTILLATION_RENDERER)
        {
            return Err(DomainError::Invalid(
                "distillation review must be independently authored and version-bound".into(),
            ));
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn propose_component_distillation(
        &mut self,
        input: &ComponentDistillationInput,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        if self.production_design.distillations.len() >= 32
            || self
                .production_design
                .distillations
                .iter()
                .any(|item| item.input.id == input.id)
        {
            return Err(DomainError::Invalid(
                "distillation proposal budget or duplicate identity".into(),
            ));
        }
        if input.source_project_id != self.id
            || input.source_generation != self.generation
            || input.source_revision != self.revision
        {
            return Err(DomainError::Invalid(
                "distillation source must match the current owned project generation and revision"
                    .into(),
            ));
        }
        let mut samples = Vec::with_capacity(input.positives.len());
        for selector in &input.positives {
            self.ensure_unlocked(
                &format!("scene:{}", selector.scene_id),
                &[LockKind::Content],
            )?;
            let scene = self
                .scenes
                .iter()
                .find(|s| s.id == selector.scene_id && s.renderer == RendererKind::MotionCanvas)
                .ok_or_else(|| {
                    DomainError::Invalid("positive source must be a Motion Canvas scene".into())
                })?;
            let nodes = selector
                .node_ids
                .iter()
                .map(|id| {
                    scene
                        .nodes
                        .iter()
                        .find(|node| node.id == *id)
                        .cloned()
                        .ok_or_else(|| {
                            DomainError::Invalid("positive source object does not exist".into())
                        })
                })
                .collect::<Result<Vec<_>>>()?;
            samples.push(DistillationSample {
                scene_id: selector.scene_id,
                nodes,
            });
        }
        for negative in &input.negatives {
            let scene = self
                .scenes
                .iter()
                .find(|scene| scene.id == negative.scene_id)
                .ok_or_else(|| DomainError::Invalid("negative example scene is absent".into()))?;
            let valid = match negative.rejection {
                DistillationRejection::NonMotionCanvas => {
                    negative.node_id.is_none() && scene.renderer != RendererKind::MotionCanvas
                }
                DistillationRejection::LockedObject => negative
                    .node_id
                    .and_then(|id| scene.nodes.iter().find(|node| node.id == id))
                    .is_some_and(|node| !node.property_locks.is_empty()),
                DistillationRejection::UnsupportedKind => negative
                    .node_id
                    .and_then(|id| scene.nodes.iter().find(|node| node.id == id))
                    .is_some_and(|node| !supported(&node.kind)),
            };
            if !valid {
                return Err(DomainError::Invalid(
                    "claimed negative sample is not actually rejected by the source grammar".into(),
                ));
            }
        }
        let proposal = ComponentDistillationProposal {
            schema: DISTILLATION_SCHEMA.into(),
            input: input.clone(),
            samples,
            review: None,
        };
        proposal.validate()?;
        self.production_design.distillations.push(proposal);
        Ok(())
    }
    pub(crate) fn review_component_distillation(
        &mut self,
        id: Uuid,
        review: &DistillationReview,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let existing = self
            .production_design
            .distillations
            .iter_mut()
            .find(|item| item.input.id == id)
            .ok_or_else(|| DomainError::NotFound(format!("distillation proposal:{id}")))?;
        if existing.review.is_some() {
            return Err(DomainError::Invalid(
                "distillation review is immutable; submit a new source proposal to reconsider"
                    .into(),
            ));
        }
        existing.review = Some(review.clone());
        existing.validate()?;
        Ok(())
    }
}
impl ProductionDesign {
    pub(crate) fn validate_distillations(&self, project: &Project) -> Result<()> {
        if self.distillations.len() > 32 {
            return Err(DomainError::Invalid(
                "too many component distillation proposals".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for candidate in &self.distillations {
            candidate.validate()?;
            if candidate.input.source_project_id != project.id
                || candidate.input.source_revision > project.revision
                || !ids.insert(candidate.input.id)
            {
                return Err(DomainError::Invalid(
                    "component distillation project source or identity is not valid".into(),
                ));
            }
        }
        Ok(())
    }
}
