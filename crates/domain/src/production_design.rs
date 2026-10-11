//! Persistent creative decisions. Execution and authorization remain in the existing service/SDK.
use crate::*;
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ProductionDesign {
    #[serde(default)]
    pub plan: Option<ProductionPlan>,
    #[serde(default)]
    pub heroes: Vec<ProductHeroInstance>,
    #[serde(default)]
    pub capsules: Vec<NativeCapsule>,
    #[serde(default)]
    pub patches: Vec<CreativePatchRecord>,
    #[serde(default)]
    pub brand_profile: Option<BrandProfile>,
    #[serde(default)]
    pub taste_profile: Option<TasteProfile>,
    #[serde(default)]
    pub brand_exceptions: Vec<BrandException>,
    #[serde(default)]
    pub project_decisions: Vec<CreativeDecision>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductionPlan {
    pub objective: String,
    pub audience: String,
    pub concept: String,
    pub reference_constraints: Vec<String>,
    pub exclusions: Vec<String>,
    pub shots: Vec<PlannedShot>,
    pub clock: ProductionClock,
    /// Approval is a user decision recorded through ordinary revisioned changes,
    /// not an authentication credential and never a technical/creative quality PASS.
    pub approval: Option<PlanApproval>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlanApproval {
    pub reviewer: String,
    pub note: String,
    pub content_sha256: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedShot {
    pub scene_id: Uuid,
    pub purpose: String,
    pub claim_ids: Vec<Uuid>,
    pub asset_ids: Vec<Uuid>,
    pub evidence_kind: NarrativeEvidenceKind,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NarrativeEvidenceKind {
    GraphicStudy,
    RealCapture,
    LicensedFootage,
    SyntheticLabeled,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ProductionClock {
    Timeline,
    Voice {
        voice_track_id: Uuid,
    },
    Music {
        asset_id: Uuid,
        beats: Vec<RationalTime>,
    },
}

pub(crate) fn design_text(value: &str, max: usize, label: &str) -> Result<()> {
    if value.trim().is_empty()
        || value.len() > max
        || value.chars().any(|c| c.is_control() && c != '\n')
    {
        return Err(DomainError::Invalid(format!("invalid {label}")));
    }
    Ok(())
}
fn text_list(values: &[String], max: usize) -> Result<()> {
    if values.len() > max {
        return Err(DomainError::Invalid(
            "creative decision list is too large".into(),
        ));
    }
    for value in values {
        design_text(value, 2000, "creative decision")?;
    }
    Ok(())
}
fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
impl ProductionPlan {
    pub fn content_digest(&self) -> Result<String> {
        let mut content = self.clone();
        content.approval = None;
        let encoded =
            serde_json::to_vec(&content).map_err(|e| DomainError::Invalid(e.to_string()))?;
        Ok(hex::encode(Sha256::digest(encoded)))
    }
    pub fn validate(
        &self,
        scenes: &[Scene],
        assets: &[Asset],
        brief: &Brief,
        audio: &AudioState,
    ) -> Result<()> {
        design_text(&self.objective, 8000, "production objective")?;
        design_text(&self.audience, 2000, "production audience")?;
        design_text(&self.concept, 12000, "production concept")?;
        text_list(&self.reference_constraints, 64)?;
        text_list(&self.exclusions, 64)?;
        if self.shots.is_empty() || self.shots.len() > 256 {
            return Err(DomainError::Invalid("plan requires 1..=256 shots".into()));
        }
        let mut seen = BTreeSet::new();
        for shot in &self.shots {
            if !scenes.iter().any(|scene| scene.id == shot.scene_id) || !seen.insert(shot.scene_id)
            {
                return Err(DomainError::Invalid(
                    "production plan scene is missing or duplicated".into(),
                ));
            }
            design_text(&shot.purpose, 4000, "shot purpose")?;
            if shot.claim_ids.len() > 128 || shot.asset_ids.len() > 128 {
                return Err(DomainError::Invalid(
                    "production shot references exceed budget".into(),
                ));
            }
            let claims: BTreeSet<_> = shot.claim_ids.iter().collect();
            let media: BTreeSet<_> = shot.asset_ids.iter().collect();
            if claims.len() != shot.claim_ids.len()
                || media.len() != shot.asset_ids.len()
                || claims
                    .iter()
                    .any(|id| !brief.claims.iter().any(|claim| claim.id == **id))
                || media
                    .iter()
                    .any(|id| !assets.iter().any(|asset| asset.id == **id))
            {
                return Err(DomainError::Invalid(
                    "production shot has duplicate or unknown evidence references".into(),
                ));
            }
            if matches!(
                shot.evidence_kind,
                NarrativeEvidenceKind::RealCapture | NarrativeEvidenceKind::LicensedFootage
            ) && (shot.asset_ids.is_empty()
                || shot.asset_ids.iter().any(|id| {
                    assets
                        .iter()
                        .find(|asset| asset.id == *id)
                        .is_none_or(|asset| {
                            asset
                                .content_sha256
                                .as_deref()
                                .is_none_or(|sha| !digest(sha))
                        })
                }))
            {
                return Err(DomainError::Invalid("real/footage evidence needs digest-bound assets; a graphic is not proof of product behavior".into()));
            }
        }
        match &self.clock {
            ProductionClock::Timeline => {}
            ProductionClock::Voice { voice_track_id } => {
                if !audio
                    .voice_tracks
                    .iter()
                    .any(|track| track.id == *voice_track_id)
                {
                    return Err(DomainError::Invalid(
                        "production voice clock is unavailable".into(),
                    ));
                }
            }
            ProductionClock::Music { asset_id, beats } => {
                if !assets.iter().any(|asset| {
                    asset.id == *asset_id
                        && asset.media_type.starts_with("audio/")
                        && asset.content_sha256.is_some()
                }) || beats.is_empty()
                    || beats.len() > 20000
                    || beats.iter().any(|at| !non_negative(*at))
                    || beats.windows(2).any(|pair| pair[0] >= pair[1])
                {
                    return Err(DomainError::Invalid(
                        "music clock needs a measured asset and strictly ordered nonnegative beats"
                            .into(),
                    ));
                }
            }
        }
        if let Some(approval) = &self.approval {
            design_text(&approval.reviewer, 200, "plan reviewer")?;
            design_text(&approval.note, 2000, "plan approval note")?;
            if approval.content_sha256 != self.content_digest()? {
                return Err(DomainError::Invalid(
                    "plan approval is stale; approve the current content digest".into(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Fidelity {
    Native,
    Translated,
    Baked,
    Approximate,
    Unavailable,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FidelityReport {
    pub renderer: String,
    pub renderer_version: String,
    pub visual: Fidelity,
    pub temporal: Fidelity,
    pub structural: Fidelity,
    pub editable: Fidelity,
    pub losses: Vec<String>,
    pub evidence_sha256: Option<String>,
}
impl FidelityReport {
    pub fn validate(&self) -> Result<()> {
        design_text(&self.renderer, 128, "renderer identity")?;
        design_text(&self.renderer_version, 128, "renderer version")?;
        text_list(&self.losses, 128)?;
        if self
            .evidence_sha256
            .as_deref()
            .is_some_and(|sha| !digest(sha))
        {
            return Err(DomainError::Invalid(
                "invalid fidelity evidence digest".into(),
            ));
        }
        if [self.visual, self.temporal, self.structural, self.editable]
            .iter()
            .any(|f| !matches!(f, Fidelity::Native | Fidelity::Translated))
            && self.losses.is_empty()
        {
            return Err(DomainError::Invalid(
                "lossy or unavailable fidelity must explain the losses".into(),
            ));
        }
        Ok(())
    }
    /// No implication that a native representation has been rendered or reviewed.
    pub fn usable_without_loss_consent(&self) -> bool {
        [self.visual, self.temporal, self.structural, self.editable]
            .iter()
            .all(|f| matches!(f, Fidelity::Native | Fidelity::Translated))
            && self.losses.is_empty()
            && self.evidence_sha256.is_some()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeCapsule {
    pub id: Uuid,
    pub scene_id: Uuid,
    /// Immutable application blob. A path, command or imported grant is not accepted.
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub label: String,
    pub fidelity: FidelityReport,
    pub editable_parameters: Vec<String>,
    pub native_editor_hint: String,
}
impl NativeCapsule {
    pub fn validate(&self, scenes: &[Scene], assets: &[Asset]) -> Result<()> {
        design_text(&self.label, 256, "native capsule label")?;
        design_text(&self.native_editor_hint, 256, "native editor label")?;
        self.fidelity.validate()?;
        text_list(&self.editable_parameters, 64)?;
        if !digest(&self.source_sha256)
            || !scenes.iter().any(|scene| scene.id == self.scene_id)
            || !assets.iter().any(|asset| {
                asset.id == self.source_asset_id
                    && asset.content_sha256.as_deref() == Some(&self.source_sha256)
            })
        {
            return Err(DomainError::Invalid(
                "native capsule must preserve an existing digest-bound source asset and scene"
                    .into(),
            ));
        }
        Ok(())
    }
}
impl ProductionDesign {
    pub fn validate(
        &self,
        scenes: &[Scene],
        assets: &[Asset],
        brief: &Brief,
        audio: &AudioState,
    ) -> Result<()> {
        crate::creative_revisions::validate_creative_patch_records(&self.patches)?;
        validate_brand_governance(
            &self.brand_profile,
            &self.brand_exceptions,
            &self.taste_profile,
            &self.project_decisions,
            scenes,
            &self.heroes,
        )?;
        if self.heroes.len() > 64 || self.capsules.len() > 256 {
            return Err(DomainError::Invalid(
                "production design collection is too large".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        let mut hero_scenes = BTreeSet::new();
        for hero in &self.heroes {
            hero.validate()?;
            if !ids.insert(hero.id)
                || !hero_scenes.insert(hero.scene_id)
                || !scenes.iter().any(|scene| scene.id == hero.scene_id)
            {
                return Err(DomainError::Invalid(
                    "duplicate or orphaned component instance".into(),
                ));
            }
        }
        ids.clear();
        for capsule in &self.capsules {
            capsule.validate(scenes, assets)?;
            if !ids.insert(capsule.id) {
                return Err(DomainError::Invalid("duplicate native capsule".into()));
            }
        }
        if let Some(plan) = &self.plan {
            plan.validate(scenes, assets, brief, audio)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativePatch {
    pub scene_id: Uuid,
    pub base_revision: u64,
    pub rationale: String,
    pub edits: Vec<ScopedCanvasEdit>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ScopedCanvasEdit {
    Text {
        node_id: Uuid,
        text: String,
    },
    Style {
        node_id: Uuid,
        style: NodeStyle,
    },
    Transform {
        node_id: Uuid,
        transform: CanvasTransform,
    },
    Keyframe {
        node_id: Uuid,
        keyframe: CanvasKeyframe,
    },
}
impl ScopedCanvasEdit {
    pub fn node_id(&self) -> Uuid {
        match self {
            Self::Text { node_id, .. }
            | Self::Style { node_id, .. }
            | Self::Transform { node_id, .. }
            | Self::Keyframe { node_id, .. } => *node_id,
        }
    }
    fn change(&self, scene_id: Uuid) -> Change {
        match self {
            Self::Text { node_id, text } => Change::UpdateCanvasText {
                scene_id,
                node_id: *node_id,
                text: Some(text.clone()),
            },
            Self::Style { node_id, style } => Change::UpdateCanvasStyle {
                scene_id,
                node_id: *node_id,
                style: style.clone(),
            },
            Self::Transform { node_id, transform } => Change::TransformCanvasNode {
                scene_id,
                node_id: *node_id,
                transform: *transform,
            },
            Self::Keyframe { node_id, keyframe } => Change::SetCanvasKeyframe {
                scene_id,
                node_id: *node_id,
                keyframe: keyframe.clone(),
            },
        }
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativePatchPreview {
    pub source_revision: u64,
    pub scene_id: Uuid,
    pub before: Vec<CanvasNode>,
    pub after: Vec<CanvasNode>,
    pub dirty_start: RationalTime,
    pub dirty_end: RationalTime,
    pub kind: String,
}
impl Project {
    pub fn preview_creative_patch(&self, patch: &CreativePatch) -> Result<CreativePatchPreview> {
        self.ensure_unlocked(
            &self.resource_key(),
            &[
                LockKind::Content,
                LockKind::Style,
                LockKind::Position,
                LockKind::Timing,
            ],
        )?;
        if patch.base_revision != self.revision {
            return Err(DomainError::Invalid("creative patch base is stale".into()));
        }
        design_text(&patch.rationale, 4000, "creative patch rationale")?;
        if patch.edits.is_empty() || patch.edits.len() > 128 {
            return Err(DomainError::Invalid(
                "creative patch requires 1..=128 scoped edits".into(),
            ));
        }
        let source = self
            .scenes
            .iter()
            .find(|scene| scene.id == patch.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", patch.scene_id)))?;
        let scope: BTreeSet<_> = patch.edits.iter().map(ScopedCanvasEdit::node_id).collect();
        if scope
            .iter()
            .any(|id| !source.nodes.iter().any(|node| node.id == *id))
        {
            return Err(DomainError::Invalid(
                "patch target is outside the selected scene".into(),
            ));
        }
        let mut candidate = self.clone();
        for edit in &patch.edits {
            candidate.apply_change(&edit.change(patch.scene_id))?;
        }
        let after = candidate
            .scenes
            .iter()
            .find(|scene| scene.id == patch.scene_id)
            .expect("scoped edits cannot delete a scene");
        Ok(CreativePatchPreview {
            source_revision: self.revision,
            scene_id: patch.scene_id,
            before: source
                .nodes
                .iter()
                .filter(|n| scope.contains(&n.id))
                .cloned()
                .collect(),
            after: after
                .nodes
                .iter()
                .filter(|n| scope.contains(&n.id))
                .cloned()
                .collect(),
            dirty_start: source.start,
            dirty_end: source
                .start
                .checked_add(source.duration)
                .map_err(|e| DomainError::Invalid(e.to_string()))?,
            kind: "semantic_diff_full_scene_invalidation_not_pixel_verification".into(),
        })
    }
    pub(crate) fn apply_creative_patch(&mut self, patch: &CreativePatch) -> Result<()> {
        let preview = self.preview_creative_patch(patch)?;
        self.commit_creative_preview(preview, patch.rationale.clone(), None)
    }
}
