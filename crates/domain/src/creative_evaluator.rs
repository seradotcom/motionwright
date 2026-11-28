//! MW05-E13-01: reusable, versioned original creative evaluation recipes.
//!
//! This grammar is data-only. A playbook is editorial content, not an executable
//! skill, trusted instruction, provider, shader, or automation permission.
//! Outcomes are bounded technical observations over owned Canvas scene state;
//! subjective/creative questions always abstain for human review.
use crate::*;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const CREATIVE_EVALUATOR_SCHEMA: &str = "motionwright.creative-evaluator/1";
pub const EVALUATION_REPORT_SCHEMA: &str = "motionwright.creative-evaluation/1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CreativeCriterion {
    MinimumNodes { id: Uuid, minimum: u16 },
    TextContains { id: Uuid, phrase: String },
    ProjectPixelSafeArea { id: Uuid, margin_permille: u16 },
    HumanQuestion { id: Uuid, question: String },
}
impl CreativeCriterion {
    pub fn id(&self) -> Uuid {
        match self {
            Self::MinimumNodes { id, .. }
            | Self::TextContains { id, .. }
            | Self::ProjectPixelSafeArea { id, .. }
            | Self::HumanQuestion { id, .. } => *id,
        }
    }
    fn is_human(&self) -> bool {
        matches!(self, Self::HumanQuestion { .. })
    }
    fn validate(&self) -> Result<()> {
        if self.id().is_nil() {
            return Err(DomainError::Invalid(
                "evaluator criterion identity is required".into(),
            ));
        }
        match self {
            Self::MinimumNodes { minimum, .. } if !(1..=256).contains(minimum) => Err(
                DomainError::Invalid("evaluator node count must be 1..=256".into()),
            ),
            Self::TextContains { phrase, .. } => safe_text(phrase, 120, "text requirement"),
            Self::ProjectPixelSafeArea {
                margin_permille, ..
            } if *margin_permille > 150 => Err(DomainError::Invalid(
                "evaluator safe-area margin exceeds 15 percent".into(),
            )),
            Self::HumanQuestion { question, .. } => {
                safe_text(question, 250, "human creative question")
            }
            _ => Ok(()),
        }
    }
}
fn safe_text(value: &str, budget: usize, label: &str) -> Result<()> {
    if value.trim().is_empty() || value.len() > budget || value.chars().any(char::is_control) {
        return Err(DomainError::Invalid(format!("invalid {label}")));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvaluatorInputDependency {
    CanvasNodeRoster,
    CanvasText,
    CanvasPixelBounds,
    HumanReview,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorExample {
    pub label: String,
    pub criterion_id: Uuid,
    pub expected: CriterionOutcome,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorExamplesArtifact {
    pub version: u32,
    pub cases: Vec<EvaluatorExample>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorDependenciesArtifact {
    pub version: u32,
    pub interpreter: String,
    pub inputs: Vec<EvaluatorInputDependency>,
}

/// Independently versioned data consumed or displayed by the first-party evaluator.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorDataArtifacts {
    pub instructions_version: u32,
    pub examples: EvaluatorExamplesArtifact,
    pub dependencies: EvaluatorDependenciesArtifact,
}

fn present_artifacts<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<EvaluatorDataArtifacts>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    EvaluatorDataArtifacts::deserialize(deserializer).map(Some)
}

impl EvaluatorDataArtifacts {
    fn validate(&self, criteria: &[CreativeCriterion]) -> Result<()> {
        let expected = criteria
            .iter()
            .map(|criterion| match criterion {
                CreativeCriterion::MinimumNodes { .. } => {
                    EvaluatorInputDependency::CanvasNodeRoster
                }
                CreativeCriterion::TextContains { .. } => EvaluatorInputDependency::CanvasText,
                CreativeCriterion::ProjectPixelSafeArea { .. } => {
                    EvaluatorInputDependency::CanvasPixelBounds
                }
                CreativeCriterion::HumanQuestion { .. } => EvaluatorInputDependency::HumanReview,
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if self.instructions_version == 0
            || self.examples.version == 0
            || self.dependencies.version == 0
            || self.dependencies.interpreter != CREATIVE_EVALUATOR_SCHEMA
            || self.dependencies.inputs != expected
            || !(1..=16).contains(&self.examples.cases.len())
        {
            return Err(DomainError::Invalid(
                "invalid evaluator artifact versions, examples or interpreter dependencies".into(),
            ));
        }
        let mut labels = BTreeSet::new();
        for example in &self.examples.cases {
            safe_text(&example.label, 180, "evaluator example label")?;
            let criterion = criteria
                .iter()
                .find(|criterion| criterion.id() == example.criterion_id);
            if !labels.insert(example.label.as_str())
                || criterion.is_none_or(|criterion| {
                    criterion.is_human() && example.expected != CriterionOutcome::NotAssessed
                })
            {
                return Err(DomainError::Invalid(
                    "example must identify a distinct original case for a supported criterion"
                        .into(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativeEvaluatorBundle {
    pub schema: String,
    pub id: Uuid,
    pub version: u32,
    pub label: String,
    pub purpose: String,
    /// Untrusted author declaration. Not a signature or verified rights grant.
    pub author_attestation: String,
    /// Data-only editorial guidance: never executed, installed or delegated.
    pub playbook: Vec<String>,
    pub criteria: Vec<CreativeCriterion>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "present_artifacts"
    )]
    pub artifacts: Option<EvaluatorDataArtifacts>,
}
impl CreativeEvaluatorBundle {
    pub fn content_digest(&self) -> Result<String> {
        let bytes =
            serde_json::to_vec(self).map_err(|error| DomainError::Invalid(error.to_string()))?;
        let mut hasher = Sha256::new();
        hasher.update(b"motionwright.creative-evaluator/1\0");
        hasher.update(bytes);
        Ok(hex::encode(hasher.finalize()))
    }
    pub fn validate(&self) -> Result<()> {
        if self.schema != CREATIVE_EVALUATOR_SCHEMA
            || self.id.is_nil()
            || self.version == 0
            || self.playbook.is_empty()
            || self.playbook.len() > 12
            || self.criteria.is_empty()
            || self.criteria.len() > 16
        {
            return Err(DomainError::Invalid(
                "creative evaluator schema, version or source budget invalid".into(),
            ));
        }
        safe_text(&self.label, 100, "evaluator label")?;
        safe_text(&self.purpose, 280, "evaluator purpose")?;
        safe_text(
            &self.author_attestation,
            240,
            "evaluator author declaration",
        )?;
        for instruction in &self.playbook {
            safe_text(instruction, 500, "non-executable playbook instruction")?;
        }
        let mut ids = BTreeSet::new();
        for criterion in &self.criteria {
            criterion.validate()?;
            if !ids.insert(criterion.id()) {
                return Err(DomainError::Invalid(
                    "duplicate creative criterion identity".into(),
                ));
            }
        }
        if let Some(artifacts) = &self.artifacts {
            artifacts.validate(&self.criteria)?;
        }
        Ok(())
    }

    fn validate_artifact_transition(&self, previous: Option<&Self>) -> Result<()> {
        let prior = previous.and_then(|bundle| {
            bundle
                .artifacts
                .as_ref()
                .map(|artifacts| (bundle, artifacts))
        });
        let Some(current) = &self.artifacts else {
            return if prior.is_none() {
                Ok(())
            } else {
                Err(DomainError::Invalid(
                    "evaluator history must retain its independent artifacts".into(),
                ))
            };
        };
        let valid = if let Some((previous, prior)) = prior {
            let next = |version: u32, changed: bool| version.checked_add(u32::from(changed));
            Some(current.instructions_version)
                == next(
                    prior.instructions_version,
                    self.playbook != previous.playbook,
                )
                && Some(current.examples.version)
                    == next(
                        prior.examples.version,
                        current.examples.cases != prior.examples.cases,
                    )
                && Some(current.dependencies.version)
                    == next(
                        prior.dependencies.version,
                        current.dependencies.inputs != prior.dependencies.inputs,
                    )
        } else {
            current.instructions_version == 1
                && current.examples.version == 1
                && current.dependencies.version == 1
        };
        if !valid {
            return Err(DomainError::Invalid(
                "evaluator artifact versions must advance once only when their own content changes"
                    .into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CriterionOutcome {
    Pass,
    Fail,
    NotAssessed,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TechnicalConclusion {
    PassTechnicalOnly,
    Fail,
    NeedsHumanReview,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CriterionObservation {
    pub criterion_id: Uuid,
    pub outcome: CriterionOutcome,
    pub explanation: String,
}
/// Conservative, source-bound candidates. Nothing here asserts that repeated
/// content is poor, that scene pixels were rendered, or that motion is pointless.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CreativeStyleKind {
    Repetition,
    Density,
    MotionPurpose,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativeStyleAdvisory {
    pub kind: CreativeStyleKind,
    pub scene_id: Uuid,
    /// Exact scene-relative half-open interval, not a decoded/rendered frame.
    pub interval_start: RationalTime,
    pub interval_end: RationalTime,
    pub source_node_ids: Vec<Uuid>,
    pub rationale: String,
    pub suggested_action: String,
    /// Deliberately cannot become creative acceptance through an evaluator.
    pub human_review_required: bool,
}
impl CreativeStyleAdvisory {
    fn validate(&self, scene_id: Uuid) -> Result<()> {
        if self.scene_id != scene_id
            || self.source_node_ids.is_empty()
            || self.source_node_ids.len() > 24
            || self.source_node_ids.iter().any(Uuid::is_nil)
            || self
                .source_node_ids
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .len()
                != self.source_node_ids.len()
            || self.interval_start.validate().is_err()
            || self.interval_end.validate().is_err()
            || self.interval_start < RationalTime::ZERO
            || self.interval_end <= self.interval_start
            || !self.human_review_required
        {
            return Err(DomainError::Invalid(
                "creative style advisory has invalid source, interval or reviewer status".into(),
            ));
        }
        safe_text(&self.rationale, 300, "creative style rationale")?;
        safe_text(
            &self.suggested_action,
            240,
            "creative style suggested action",
        )?;
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativeEvaluationReport {
    pub schema: String,
    pub id: Uuid,
    pub evaluator_id: Uuid,
    pub evaluator_version: u32,
    pub evaluator_sha256: String,
    pub scene_id: Uuid,
    pub source_generation: Uuid,
    pub source_revision: u64,
    pub observations: Vec<CriterionObservation>,
    /// Backward-compatible: historical reports keep their exact old bytes.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub style_advisories: Vec<CreativeStyleAdvisory>,
    pub conclusion: TechnicalConclusion,
    /// This report never establishes human creative or intellectual-property approval.
    pub creative_approval: String,
}
fn conclusion(
    observations: &[CriterionObservation],
    style_advisories: &[CreativeStyleAdvisory],
) -> TechnicalConclusion {
    if observations
        .iter()
        .any(|result| result.outcome == CriterionOutcome::Fail)
    {
        TechnicalConclusion::Fail
    } else if observations
        .iter()
        .any(|result| result.outcome == CriterionOutcome::NotAssessed)
        || !style_advisories.is_empty()
    {
        TechnicalConclusion::NeedsHumanReview
    } else {
        TechnicalConclusion::PassTechnicalOnly
    }
}
impl CreativeEvaluationReport {
    pub fn validate(&self, bundles: &[CreativeEvaluatorBundle]) -> Result<()> {
        let bundle = bundles
            .iter()
            .find(|bundle| {
                bundle.id == self.evaluator_id && bundle.version == self.evaluator_version
            })
            .ok_or_else(|| {
                DomainError::Invalid("evaluation references missing historical evaluator".into())
            })?;
        if self.schema != EVALUATION_REPORT_SCHEMA
            || self.id.is_nil()
            || self.scene_id.is_nil()
            || self.source_generation.is_nil()
            || self.evaluator_sha256 != bundle.content_digest()?
            || self.observations.len() != bundle.criteria.len()
            || self.conclusion != conclusion(&self.observations, &self.style_advisories)
            || self.creative_approval != "not_assessed"
        {
            return Err(DomainError::Invalid(
                "creative evaluation is not a bound technical report".into(),
            ));
        }
        for (observation, criterion) in self.observations.iter().zip(&bundle.criteria) {
            safe_text(&observation.explanation, 400, "evaluation result reason")?;
            if observation.criterion_id != criterion.id()
                || (criterion.is_human() && observation.outcome != CriterionOutcome::NotAssessed)
            {
                return Err(DomainError::Invalid(
                    "creative evaluation cannot assert a human-review PASS".into(),
                ));
            }
        }
        if self.style_advisories.len() > 32 {
            return Err(DomainError::Invalid(
                "creative style advisory budget exceeded".into(),
            ));
        }
        for advisory in &self.style_advisories {
            advisory.validate(self.scene_id)?;
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn register_creative_evaluator(
        &mut self,
        bundle: &CreativeEvaluatorBundle,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        bundle.validate()?;
        if self.production_design.evaluator_bundles.len() >= 64 {
            return Err(DomainError::Invalid(
                "creative evaluator version budget exceeded".into(),
            ));
        }
        let previous = self
            .production_design
            .evaluator_bundles
            .iter()
            .filter(|prior| prior.id == bundle.id)
            .max_by_key(|prior| prior.version);
        let next = previous
            .map_or(0, |prior| prior.version)
            .checked_add(1)
            .ok_or_else(|| DomainError::Invalid("evaluator version overflow".into()))?;
        if bundle.version != next {
            return Err(DomainError::Invalid(
                "creative evaluator version must increase exactly once; past recipes are immutable"
                    .into(),
            ));
        }
        bundle.validate_artifact_transition(previous)?;
        self.production_design
            .evaluator_bundles
            .push(bundle.clone());
        Ok(())
    }
    pub(crate) fn evaluate_creative_scene(
        &mut self,
        evaluator_id: Uuid,
        evaluator_version: u32,
        scene_id: Uuid,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        self.ensure_unlocked(&format!("scene:{scene_id}"), &[LockKind::Content])?;
        if self.production_design.evaluation_reports.len() >= 256 {
            return Err(DomainError::Invalid(
                "creative evaluation report budget exceeded".into(),
            ));
        }
        let bundle = self
            .production_design
            .evaluator_bundles
            .iter()
            .find(|bundle| bundle.id == evaluator_id && bundle.version == evaluator_version)
            .ok_or_else(|| {
                DomainError::NotFound(format!(
                    "creative evaluator:{evaluator_id}:{evaluator_version}"
                ))
            })?;
        let scene = self
            .scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        let observations = bundle
            .criteria
            .iter()
            .map(|criterion| observe(scene, criterion))
            .collect::<Vec<_>>();
        let style_advisories = observe_style_advisories(scene);
        let report = CreativeEvaluationReport {
            schema: EVALUATION_REPORT_SCHEMA.into(),
            id: Uuid::new_v4(),
            evaluator_id,
            evaluator_version,
            evaluator_sha256: bundle.content_digest()?,
            scene_id,
            source_generation: self.generation,
            source_revision: self.revision,
            conclusion: conclusion(&observations, &style_advisories),
            observations,
            style_advisories,
            creative_approval: "not_assessed".into(),
        };
        report.validate(&self.production_design.evaluator_bundles)?;
        self.production_design.evaluation_reports.push(report);
        Ok(())
    }
}
/// Deterministic, bounded authoring diagnostics. These are editorial
/// candidates, not objective quality scores or native-render observations.
pub fn observe_style_advisories(scene: &Scene) -> Vec<CreativeStyleAdvisory> {
    let mut result = Vec::new();
    let full = (RationalTime::ZERO, scene.duration);
    let mut repeated: BTreeMap<String, Vec<Uuid>> = BTreeMap::new();
    for node in &scene.nodes {
        if node.kind != "text" || node.opacity <= 0.01 {
            continue;
        }
        if let Some(text) = node.text.as_deref() {
            if text.len() > 512 {
                continue;
            }
            let normalized = text
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .to_lowercase();
            if !normalized.is_empty() && normalized.len() <= 240 {
                repeated.entry(normalized).or_default().push(node.id);
            }
        }
    }
    for ids in repeated.values().filter(|ids| ids.len() >= 3).take(12) {
        result.push(CreativeStyleAdvisory {
            kind: CreativeStyleKind::Repetition,
            scene_id: scene.id,
            interval_start: full.0,
            interval_end: full.1,
            source_node_ids: ids.iter().copied().take(24).collect(),
            rationale: format!(
                "{} visible editable text nodes repeat identical normalized wording; this is a candidate pattern, not an aesthetic verdict.",
                ids.len()
            ),
            suggested_action: "Review whether repetition serves the scene; vary or remove redundant copies if not.".into(),
            human_review_required: true,
        });
    }

    // Baseline authored 1920x1080 project-pixel boxes only. Summed clipped
    // area is an overdraw proxy, not a native composite or actual pixel count.
    let mut visible = Vec::new();
    let mut summed_area = 0.0;
    for node in &scene.nodes {
        if node.coordinate_space != CoordinateSpace::ProjectPixels || node.opacity <= 0.01 {
            continue;
        }
        let width = (node.x + node.width).clamp(0.0, 1920.0) - node.x.clamp(0.0, 1920.0);
        let height = (node.y + node.height).clamp(0.0, 1080.0) - node.y.clamp(0.0, 1080.0);
        if width > 0.0 && height > 0.0 {
            visible.push(node.id);
            summed_area += width * height;
        }
    }
    let overdraw_index = summed_area / (1920.0 * 1080.0);
    if visible.len() >= 4 && (overdraw_index >= 1.2 || visible.len() >= 12 && overdraw_index >= 0.5)
    {
        result.push(CreativeStyleAdvisory {
            kind: CreativeStyleKind::Density,
            scene_id: scene.id,
            interval_start: full.0,
            interval_end: full.1,
            source_node_ids: visible.iter().copied().take(24).collect(),
            rationale: format!(
                "{} visible project-pixel bounds have baseline overdraw index {:.2}; animation and rendered pixels were not measured.",
                visible.len(), overdraw_index
            ),
            suggested_action: "Inspect hierarchy and overlaps; simplify competing visible objects where appropriate.".into(),
            human_review_required: true,
        });
    }

    for node in &scene.nodes {
        if result.len() >= 32 {
            break;
        }
        let changed = node
            .keyframes
            .iter()
            .filter(|keyframe| {
                let baseline = match keyframe.property {
                    MotionProperty::X => node.x,
                    MotionProperty::Y => node.y,
                    MotionProperty::Width => node.width,
                    MotionProperty::Height => node.height,
                    MotionProperty::RotationDeg => node.rotation_deg,
                    MotionProperty::Opacity => node.opacity,
                };
                (keyframe.value - baseline).abs() > 0.0001
            })
            .collect::<Vec<_>>();
        if changed.is_empty() {
            continue;
        }
        let end = changed
            .iter()
            .map(|keyframe| keyframe.at)
            .filter(|time| *time > RationalTime::ZERO)
            .min()
            .unwrap_or(scene.duration);
        result.push(CreativeStyleAdvisory {
            kind: CreativeStyleKind::MotionPurpose,
            scene_id: scene.id,
            interval_start: RationalTime::ZERO,
            interval_end: end,
            source_node_ids: vec![node.id],
            rationale: "A saved node animates an authored property, but the creative purpose of that motion is not machine-verifiable.".into(),
            suggested_action: "Review its timed narrative purpose; explain the motion or remove redundant keyframes.".into(),
            human_review_required: true,
        });
    }
    result
}
pub fn observe(scene: &Scene, criterion: &CreativeCriterion) -> CriterionObservation {
    let (outcome, explanation) = match criterion {
        CreativeCriterion::MinimumNodes { minimum, .. } => {
            let passed = scene.nodes.len() >= usize::from(*minimum);
            (
                if passed {
                    CriterionOutcome::Pass
                } else {
                    CriterionOutcome::Fail
                },
                format!(
                    "Authored nodes: {} (minimum {})",
                    scene.nodes.len(),
                    minimum
                ),
            )
        }
        CreativeCriterion::TextContains { phrase, .. } => {
            let passed = scene.nodes.iter().any(|node| {
                node.kind == "text"
                    && node
                        .text
                        .as_deref()
                        .is_some_and(|text| text.contains(phrase))
            });
            (
                if passed {
                    CriterionOutcome::Pass
                } else {
                    CriterionOutcome::Fail
                },
                if passed {
                    "Required authored Canvas text is present".into()
                } else {
                    "Required authored Canvas text is absent".into()
                },
            )
        }
        CreativeCriterion::ProjectPixelSafeArea {
            margin_permille, ..
        } => {
            let xmargin = 1920.0 * f64::from(*margin_permille) / 1000.0;
            let ymargin = 1080.0 * f64::from(*margin_permille) / 1000.0;
            // No claim for Blender/native opaque/media nodes or unknown coordinates.
            if scene.nodes.is_empty()
                || scene
                    .nodes
                    .iter()
                    .any(|node| node.coordinate_space != CoordinateSpace::ProjectPixels)
            {
                (
                    CriterionOutcome::NotAssessed,
                    "No measurable original project-pixel geometry".into(),
                )
            } else {
                let passed = scene.nodes.iter().all(|node| {
                    node.x.is_finite()
                        && node.y.is_finite()
                        && node.width.is_finite()
                        && node.height.is_finite()
                        && node.x >= xmargin
                        && node.y >= ymargin
                        && node.x + node.width <= 1920.0 - xmargin
                        && node.y + node.height <= 1080.0 - ymargin
                });
                (
                    if passed {
                        CriterionOutcome::Pass
                    } else {
                        CriterionOutcome::Fail
                    },
                    if passed {
                        "Authored project-pixel bounding boxes fit the technical 1920x1080 safe area".into()
                    } else {
                        "At least one authored bounding box extends beyond the technical safe area"
                            .into()
                    },
                )
            }
        }
        CreativeCriterion::HumanQuestion { .. } => (
            CriterionOutcome::NotAssessed,
            "Requires a human creative reviewer; automated acceptance is prohibited".into(),
        ),
    };
    CriterionObservation {
        criterion_id: criterion.id(),
        outcome,
        explanation,
    }
}
impl ProductionDesign {
    pub(crate) fn validate_creative_evaluators(&self, current_revision: u64) -> Result<()> {
        if self.evaluator_bundles.len() > 64 || self.evaluation_reports.len() > 256 {
            return Err(DomainError::Invalid(
                "creative evaluator collections exceed budget".into(),
            ));
        }
        let mut version_ids = BTreeSet::new();
        for bundle in &self.evaluator_bundles {
            bundle.validate()?;
            if !version_ids.insert((bundle.id, bundle.version)) {
                return Err(DomainError::Invalid(
                    "duplicate historical creative evaluator version".into(),
                ));
            }
        }
        // Full sequential history must be present; no overwritten or invented version gaps.
        let ids = self
            .evaluator_bundles
            .iter()
            .map(|bundle| bundle.id)
            .collect::<BTreeSet<_>>();
        for id in ids {
            let mut versions = self
                .evaluator_bundles
                .iter()
                .filter(|bundle| bundle.id == id)
                .collect::<Vec<_>>();
            versions.sort_unstable_by_key(|bundle| bundle.version);
            let mut previous = None;
            for (index, bundle) in versions.into_iter().enumerate() {
                if bundle.version != index as u32 + 1 {
                    return Err(DomainError::Invalid(
                        "creative evaluator history skips a source version".into(),
                    ));
                }
                bundle.validate_artifact_transition(previous)?;
                previous = Some(bundle);
            }
        }
        let mut reports = BTreeSet::new();
        for report in &self.evaluation_reports {
            report.validate(&self.evaluator_bundles)?;
            if report.source_revision > current_revision {
                return Err(DomainError::Invalid(
                    "creative evaluator report references a future project revision".into(),
                ));
            }
            if !reports.insert(report.id) {
                return Err(DomainError::Invalid(
                    "duplicate evaluation report identity".into(),
                ));
            }
        }
        Ok(())
    }
}
