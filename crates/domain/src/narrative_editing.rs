//! Explicit editorial intent on the existing Narrative and branch snapshots.
//!
//! This module never estimates speech duration, edits a recorded take, resolves
//! native continuity, or dispatches a renderer. Timing candidates use the same
//! guarded Change::SetSceneDuration as every other authoritative edit.
use crate::*;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const NARRATIVE_EDITING_VERSION: u32 = 1;
pub const MAX_EDITORIAL_SCRIPT_BYTES: usize = 128_000;
pub const MAX_NARRATIVE_TIMING_PROPOSALS: usize = 128;
const MAX_NARRATIVE_LINKS: usize = 8_192;
const MAX_NARRATIVE_EDITING_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeEditSource {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub branch_id: Uuid,
    pub base_revision: u64,
}
impl From<&Project> for NarrativeEditSource {
    fn from(project: &Project) -> Self {
        Self {
            project_id: project.id,
            generation: project.generation,
            branch_id: project.active_branch,
            base_revision: project.revision,
        }
    }
}
impl NarrativeEditSource {
    fn require_current(&self, project: &Project) -> Result<()> {
        ensure(
            self == &Self::from(project),
            "editorial source project/generation/branch/revision changed",
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum NarrativeRealizationTarget {
    Scene { scene_id: Uuid },
    SceneBeat { scene_id: Uuid, scene_beat_id: Uuid },
}
impl NarrativeRealizationTarget {
    pub fn scene_id(&self) -> Uuid {
        match self {
            Self::Scene { scene_id } | Self::SceneBeat { scene_id, .. } => *scene_id,
        }
    }
    fn interval(&self, scenes: &[Scene]) -> Result<(RationalTime, RationalTime)> {
        let id = self.scene_id();
        let scene = scenes
            .iter()
            .find(|scene| scene.id == id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{id}")))?;
        match self {
            Self::Scene { .. } => Ok((scene.start, add(scene.start, scene.duration)?)),
            Self::SceneBeat { scene_beat_id, .. } => {
                let beat = scene
                    .beats
                    .iter()
                    .find(|beat| beat.id == *scene_beat_id)
                    .ok_or_else(|| {
                        DomainError::NotFound(format!("scene:{id}/beat:{scene_beat_id}"))
                    })?;
                let start = add(scene.start, beat.start)?;
                Ok((start, add(start, beat.duration)?))
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeRealizationBinding {
    pub id: Uuid,
    pub beat_id: Uuid,
    pub target: NarrativeRealizationTarget,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NarrativeRelationKind {
    Precedes,
    Adjacent,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeRelation {
    pub id: Uuid,
    pub from_beat_id: Uuid,
    pub to_beat_id: Uuid,
    pub kind: NarrativeRelationKind,
    pub reason: String,
}
impl NarrativeRelation {
    fn satisfied(&self, order: &[Uuid]) -> Result<bool> {
        let from = order
            .iter()
            .position(|id| *id == self.from_beat_id)
            .ok_or_else(|| {
                DomainError::NotFound(format!("narrative-beat:{}", self.from_beat_id))
            })?;
        let to = order
            .iter()
            .position(|id| *id == self.to_beat_id)
            .ok_or_else(|| DomainError::NotFound(format!("narrative-beat:{}", self.to_beat_id)))?;
        Ok(match self.kind {
            NarrativeRelationKind::Precedes => from < to,
            NarrativeRelationKind::Adjacent => to == from + 1,
        })
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeMinimumHold {
    pub beat_id: Uuid,
    pub duration: RationalTime,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditorialScriptApproval {
    pub id: Uuid,
    pub script_id: Uuid,
    pub script_version: u64,
    pub script_sha256: String,
    pub source: NarrativeEditSource,
    pub reviewer: String,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditorialScriptRelease {
    pub approval: EditorialScriptApproval,
    pub source: NarrativeEditSource,
    pub reviewer: String,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditorialScript {
    pub id: Uuid,
    pub version: u64,
    pub text: String,
    pub sha256: String,
    pub approval: Option<EditorialScriptApproval>,
    pub last_release: Option<EditorialScriptRelease>,
}
impl EditorialScript {
    fn validate(&self) -> Result<()> {
        ensure(
            self.version > 0,
            "editorial script version must be positive",
        )?;
        ensure(
            self.text.len() <= MAX_EDITORIAL_SCRIPT_BYTES,
            "editorial script exceeds 128000 UTF-8 bytes",
        )?;
        ensure(
            self.sha256 == script_sha256(&self.text),
            "editorial script digest differs from its exact text",
        )?;
        if let Some(approval) = &self.approval {
            approval.validate()?;
            ensure(
                !self.text.trim().is_empty(),
                "empty editorial script cannot be approved",
            )?;
            ensure(
                approval.script_id == self.id
                    && approval.script_version == self.version
                    && approval.script_sha256 == self.sha256,
                "editorial approval does not identify the exact script",
            )?;
        }
        if let Some(release) = &self.last_release {
            release.approval.validate()?;
            text(&release.reviewer, 256, "editorial release reviewer")?;
            text(&release.reason, 4000, "editorial release reason")?;
            ensure(
                release.approval.script_id == self.id
                    && release.approval.script_version <= self.version,
                "editorial release belongs to another script or future version",
            )?;
        }
        Ok(())
    }
}
impl EditorialScriptApproval {
    fn validate(&self) -> Result<()> {
        ensure(
            self.script_version > 0 && is_sha256(&self.script_sha256),
            "invalid editorial approval source",
        )?;
        text(&self.reviewer, 256, "editorial reviewer")?;
        text(&self.reason, 4000, "editorial approval reason")
    }
}
pub fn script_sha256(raw: &str) -> String {
    format!("{:x}", Sha256::digest(raw.as_bytes()))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeRelationImpact {
    pub relation_id: Uuid,
    pub from_beat_id: Uuid,
    pub to_beat_id: Uuid,
    pub was_satisfied: bool,
    pub is_satisfied: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeReorderImpact {
    pub source: NarrativeEditSource,
    pub before_order: Vec<Uuid>,
    pub after_order: Vec<Uuid>,
    pub moved_beat_ids: Vec<Uuid>,
    pub affected_relations: Vec<NarrativeRelationImpact>,
    pub conflicting_relation_ids: Vec<Uuid>,
    pub affected_binding_ids: Vec<Uuid>,
    pub affected_scene_ids: Vec<Uuid>,
    pub unbound_moved_beat_ids: Vec<Uuid>,
    /// Fixed provenance string, not a renderer dependency assertion.
    pub dependency_scope: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeBeatTimingAssessment {
    pub beat_id: Uuid,
    pub binding_ids: Vec<Uuid>,
    pub allocated_duration: Option<RationalTime>,
    pub minimum_hold: Option<RationalTime>,
    pub preferred_duration: Option<RationalTime>,
    /// None when unbound or no minimum exists. Zero means no shortfall.
    pub minimum_shortfall: Option<RationalTime>,
    /// allocation minus preference; negative means shorter. None = unknown/not set.
    pub preferred_delta: Option<RationalTime>,
    pub contradictory_preferences: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeSceneTimingDelta {
    pub scene_id: Uuid,
    pub before_start: RationalTime,
    pub after_start: RationalTime,
    pub before_duration: RationalTime,
    pub after_duration: RationalTime,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeTimingFeasibility {
    pub command_allowed: bool,
    pub rejection: Option<String>,
    pub scene_changes: Vec<NarrativeSceneTimingDelta>,
    pub before_preferences: Vec<NarrativeBeatTimingAssessment>,
    pub after_preferences: Option<Vec<NarrativeBeatTimingAssessment>>,
    /// These are present locks, not a claim that each one blocks this candidate.
    pub timing_locks_present: Vec<ProjectLock>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NarrativeTimingDecision {
    KeepCurrent,
    ApplyDuration,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeTimingDecisionRecord {
    pub decision: NarrativeTimingDecision,
    pub source: NarrativeEditSource,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeTimingProposal {
    pub id: Uuid,
    pub source: NarrativeEditSource,
    pub scene_id: Uuid,
    pub original_duration: RationalTime,
    pub requested_duration: RationalTime,
    pub reason: String,
    pub script_snapshot: Option<EditorialScript>,
    pub feasibility: NarrativeTimingFeasibility,
    pub decision: Option<NarrativeTimingDecisionRecord>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeEditingState {
    pub version: u32,
    pub bindings: Vec<NarrativeRealizationBinding>,
    pub relations: Vec<NarrativeRelation>,
    pub minimum_holds: Vec<NarrativeMinimumHold>,
    pub script: Option<EditorialScript>,
    pub last_reorder: Option<NarrativeReorderImpact>,
    pub timing_proposals: Vec<NarrativeTimingProposal>,
}
impl Default for NarrativeEditingState {
    fn default() -> Self {
        Self {
            version: NARRATIVE_EDITING_VERSION,
            bindings: vec![],
            relations: vec![],
            minimum_holds: vec![],
            script: None,
            last_reorder: None,
            timing_proposals: vec![],
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum NarrativeEdit {
    UpdateBeat {
        beat: NarrativeBeat,
    },
    SetBeatProtection {
        beat_id: Uuid,
        protected: bool,
        reason: String,
    },
    SetMinimumHold {
        beat_id: Uuid,
        duration: Option<RationalTime>,
    },
    UpsertBinding {
        binding: NarrativeRealizationBinding,
    },
    RemoveBinding {
        binding_id: Uuid,
    },
    UpsertRelation {
        relation: NarrativeRelation,
    },
    RemoveRelation {
        relation_id: Uuid,
    },
    Reorder {
        expected_order: Vec<Uuid>,
        ordered_ids: Vec<Uuid>,
    },
    SetScript {
        script_id: Uuid,
        expected_version: Option<u64>,
        text: String,
    },
    ApproveScript {
        approval_id: Uuid,
        script_id: Uuid,
        expected_version: u64,
        expected_sha256: String,
        reviewer: String,
        reason: String,
    },
    ReleaseScriptApproval {
        approval_id: Uuid,
        expected_sha256: String,
        reviewer: String,
        reason: String,
    },
    ProposeDuration {
        proposal_id: Uuid,
        scene_id: Uuid,
        requested_duration: RationalTime,
        reason: String,
    },
    DecideDuration {
        proposal_id: Uuid,
        decision: NarrativeTimingDecision,
        reason: String,
    },
}

fn ensure(value: bool, message: &str) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(DomainError::Invalid(message.into()))
    }
}
fn text(value: &str, max: usize, label: &str) -> Result<()> {
    ensure(
        !value.trim().is_empty() && value.len() <= max,
        &format!("invalid {label}"),
    )
}
fn positive(value: RationalTime, label: &str) -> Result<()> {
    ensure(
        non_negative(value) && value > RationalTime::ZERO,
        &format!("invalid {label}"),
    )
}
fn add(a: RationalTime, b: RationalTime) -> Result<RationalTime> {
    a.checked_add(b)
        .map_err(|error| DomainError::Invalid(format!("editorial time: {error}")))
}
fn sub(a: RationalTime, b: RationalTime) -> Result<RationalTime> {
    a.checked_sub(b)
        .map_err(|error| DomainError::Invalid(format!("editorial time: {error}")))
}
fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn unique(ids: impl IntoIterator<Item = Uuid>, label: &str) -> Result<()> {
    let mut seen = BTreeSet::new();
    for id in ids {
        ensure(seen.insert(id), &format!("duplicate {label}:{id}"))?;
    }
    Ok(())
}
fn require_beat(beats: &[NarrativeBeat], id: Uuid) -> Result<&NarrativeBeat> {
    beats
        .iter()
        .find(|beat| beat.id == id)
        .ok_or_else(|| DomainError::NotFound(format!("narrative-beat:{id}")))
}

impl NarrativeEditingState {
    /// Historical records are validated against their own snapshots; they need
    /// not reference a resource that still exists in the current branch.
    pub fn validate(&self, beats: &[NarrativeBeat]) -> Result<()> {
        ensure(
            self.version == NARRATIVE_EDITING_VERSION,
            "unsupported narrative editing version",
        )?;
        ensure(
            self.bindings.len() <= MAX_NARRATIVE_LINKS
                && self.relations.len() <= MAX_NARRATIVE_LINKS
                && self.minimum_holds.len() <= 2048
                && self.timing_proposals.len() <= MAX_NARRATIVE_TIMING_PROPOSALS,
            "narrative editing collection budget exhausted; no retained record was removed",
        )?;
        unique(
            self.bindings.iter().map(|value| value.id),
            "narrative binding",
        )?;
        unique(
            self.relations.iter().map(|value| value.id),
            "narrative relation",
        )?;
        unique(
            self.minimum_holds.iter().map(|value| value.beat_id),
            "minimum hold",
        )?;
        unique(
            self.timing_proposals.iter().map(|value| value.id),
            "timing proposal",
        )?;
        let mut targets = BTreeSet::new();
        for binding in &self.bindings {
            require_beat(beats, binding.beat_id)?;
            let target = serde_json::to_string(&binding.target)
                .map_err(|error| DomainError::Invalid(error.to_string()))?;
            ensure(
                targets.insert((binding.beat_id, target)),
                "duplicate realization target for editorial beat",
            )?;
        }
        for relation in &self.relations {
            require_beat(beats, relation.from_beat_id)?;
            require_beat(beats, relation.to_beat_id)?;
            ensure(
                relation.from_beat_id != relation.to_beat_id,
                "editorial relation cannot reference itself",
            )?;
            text(&relation.reason, 4000, "editorial relation reason")?;
        }
        for hold in &self.minimum_holds {
            require_beat(beats, hold.beat_id)?;
            positive(hold.duration, "minimum hold")?;
        }
        if let Some(script) = &self.script {
            script.validate()?;
        }
        if let Some(impact) = &self.last_reorder {
            permutation(&impact.before_order, &impact.after_order)?;
            ensure(
                impact.dependency_scope
                    == "declared_editorial_relations_only_native_dependencies_unknown",
                "invalid editorial dependency scope",
            )?;
            for relation in &impact.affected_relations {
                ensure(
                    relation.was_satisfied != relation.is_satisfied,
                    "unaffected relation appears in reorder impact",
                )?;
            }
        }
        for proposal in &self.timing_proposals {
            positive(proposal.original_duration, "original proposal duration")?;
            positive(proposal.requested_duration, "requested proposal duration")?;
            text(&proposal.reason, 4000, "timing proposal reason")?;
            if let Some(script) = &proposal.script_snapshot {
                script.validate()?;
            }
            proposal.feasibility.validate()?;
            if let Some(decision) = &proposal.decision {
                text(&decision.reason, 4000, "timing decision reason")?;
            }
        }
        ensure(
            serde_json::to_vec(self)
                .map_err(|error| DomainError::Invalid(error.to_string()))?
                .len()
                <= MAX_NARRATIVE_EDITING_BYTES,
            "narrative editing history exceeds byte budget; prior work is retained",
        )
    }
}

impl NarrativeTimingFeasibility {
    fn validate(&self) -> Result<()> {
        ensure(
            self.command_allowed == self.rejection.is_none(),
            "timing feasibility result contradicts its diagnostic",
        )?;
        ensure(
            self.command_allowed == self.after_preferences.is_some(),
            "timing feasibility lacks its candidate assessment",
        )?;
        for delta in &self.scene_changes {
            ensure(
                non_negative(delta.before_start) && non_negative(delta.after_start),
                "invalid retained scene start",
            )?;
            positive(delta.before_duration, "retained original duration")?;
            positive(delta.after_duration, "retained candidate duration")?;
        }
        for value in self
            .before_preferences
            .iter()
            .chain(self.after_preferences.iter().flatten())
        {
            if let Some(time) = value.allocated_duration {
                positive(time, "retained allocation")?;
            }
            if let Some(time) = value.minimum_hold {
                positive(time, "retained minimum hold")?;
            }
            if let Some(time) = value.preferred_duration {
                positive(time, "retained preference")?;
            }
            if let Some(time) = value.minimum_shortfall {
                ensure(non_negative(time), "invalid retained shortfall")?;
            }
            if let Some(time) = value.preferred_delta {
                time.validate()
                    .map_err(|error| DomainError::Invalid(error.to_string()))?;
            }
        }
        Ok(())
    }
}

pub(crate) fn validate_narrative_realizations(
    narrative: &Narrative,
    scenes: &[Scene],
) -> Result<()> {
    if let Some(editing) = &narrative.editing {
        editing.validate(&narrative.beats)?;
        for binding in &editing.bindings {
            binding.target.interval(scenes)?;
        }
    }
    Ok(())
}

fn permutation(before: &[Uuid], after: &[Uuid]) -> Result<()> {
    ensure(
        before.len() == after.len(),
        "editorial reorder must contain every original beat exactly once",
    )?;
    unique(before.iter().copied(), "source narrative beat")?;
    unique(after.iter().copied(), "requested narrative beat")?;
    ensure(
        before.iter().copied().collect::<BTreeSet<_>>() == after.iter().copied().collect(),
        "editorial reorder contains a missing or foreign beat",
    )
}

pub fn inspect_narrative_reorder(
    project: &Project,
    expected_order: &[Uuid],
    ordered_ids: &[Uuid],
) -> Result<NarrativeReorderImpact> {
    let actual: Vec<_> = project.narrative.beats.iter().map(|beat| beat.id).collect();
    ensure(
        actual == expected_order,
        "original editorial beat order changed",
    )?;
    permutation(expected_order, ordered_ids)?;
    let empty = NarrativeEditingState::default();
    let state = project.narrative.editing.as_ref().unwrap_or(&empty);
    let moved: BTreeSet<_> = expected_order
        .iter()
        .enumerate()
        .filter(|(index, id)| ordered_ids[*index] != **id)
        .map(|(_, id)| *id)
        .collect();
    let mut affected_relations = Vec::new();
    let mut conflicting_relation_ids = Vec::new();
    for relation in &state.relations {
        let was = relation.satisfied(expected_order)?;
        let now = relation.satisfied(ordered_ids)?;
        if was != now {
            affected_relations.push(NarrativeRelationImpact {
                relation_id: relation.id,
                from_beat_id: relation.from_beat_id,
                to_beat_id: relation.to_beat_id,
                was_satisfied: was,
                is_satisfied: now,
            });
        }
        if !now {
            conflicting_relation_ids.push(relation.id);
        }
    }
    let bindings: Vec<_> = state
        .bindings
        .iter()
        .filter(|binding| moved.contains(&binding.beat_id))
        .collect();
    Ok(NarrativeReorderImpact {
        source: project.into(),
        before_order: expected_order.to_vec(),
        after_order: ordered_ids.to_vec(),
        moved_beat_ids: moved.iter().copied().collect(),
        affected_relations,
        conflicting_relation_ids,
        affected_binding_ids: bindings.iter().map(|binding| binding.id).collect(),
        affected_scene_ids: bindings
            .iter()
            .map(|binding| binding.target.scene_id())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
        unbound_moved_beat_ids: moved
            .into_iter()
            .filter(|id| !bindings.iter().any(|binding| binding.beat_id == *id))
            .collect(),
        dependency_scope: "declared_editorial_relations_only_native_dependencies_unknown".into(),
    })
}

fn union_duration(
    mut intervals: Vec<(RationalTime, RationalTime)>,
) -> Result<Option<RationalTime>> {
    if intervals.is_empty() {
        return Ok(None);
    }
    intervals.sort();
    let (mut start, mut end) = intervals[0];
    let mut total = RationalTime::ZERO;
    for (next_start, next_end) in intervals.into_iter().skip(1) {
        if next_start <= end {
            end = end.max(next_end);
        } else {
            total = add(total, sub(end, start)?)?;
            start = next_start;
            end = next_end;
        }
    }
    Ok(Some(add(total, sub(end, start)?)?))
}

pub fn assess_narrative_preferences(
    narrative: &Narrative,
    scenes: &[Scene],
) -> Result<Vec<NarrativeBeatTimingAssessment>> {
    let empty = NarrativeEditingState::default();
    let state = narrative.editing.as_ref().unwrap_or(&empty);
    let mut result = Vec::new();
    for beat in &narrative.beats {
        let bindings: Vec<_> = state
            .bindings
            .iter()
            .filter(|binding| binding.beat_id == beat.id)
            .collect();
        let allocated = union_duration(
            bindings
                .iter()
                .map(|binding| binding.target.interval(scenes))
                .collect::<Result<Vec<_>>>()?,
        )?;
        let minimum = state
            .minimum_holds
            .iter()
            .find(|hold| hold.beat_id == beat.id)
            .map(|hold| hold.duration);
        result.push(NarrativeBeatTimingAssessment {
            beat_id: beat.id, binding_ids: bindings.iter().map(|binding| binding.id).collect(),
            allocated_duration: allocated, minimum_hold: minimum, preferred_duration: beat.preferred_duration,
            minimum_shortfall: match (allocated, minimum) { (Some(a), Some(m)) => Some(if a < m { sub(m, a)? } else { RationalTime::ZERO }), _ => None },
            preferred_delta: match (allocated, beat.preferred_duration) { (Some(a), Some(p)) => Some(sub(a, p)?), _ => None },
            contradictory_preferences: matches!((minimum, beat.preferred_duration), (Some(m), Some(p)) if m > p),
        });
    }
    Ok(result)
}

/// Called by the Project::apply_change transactional wrapper after existing timing guards. Recording
/// a new intent may expose an existing conflict; changing realized time may not
/// newly leave an affected allocation below its unchanged minimum requirement.
pub(crate) fn preserve_narrative_minimum_holds(before: &Project, after: &Project) -> Result<()> {
    if before.scenes == after.scenes || before.narrative.editing.is_none() {
        return Ok(());
    }
    let old = assess_narrative_preferences(&before.narrative, &before.scenes)?;
    let new = assess_narrative_preferences(&after.narrative, &after.scenes)?;
    for previous in old {
        let Some(minimum) = previous.minimum_hold else {
            continue;
        };
        let current = new
            .iter()
            .find(|assessment| assessment.beat_id == previous.beat_id)
            .ok_or_else(|| {
                DomainError::Locked(format!(
                    "minimum-hold:narrative-beat:{} was removed by a timing edit",
                    previous.beat_id
                ))
            })?;
        if current.minimum_hold == Some(minimum)
            && previous.allocated_duration != current.allocated_duration
            && current
                .allocated_duration
                .is_none_or(|duration| duration < minimum)
        {
            return Err(DomainError::Locked(format!(
                "minimum-hold:narrative-beat:{} cannot fit its unchanged authored allocation requirement",
                previous.beat_id
            )));
        }
    }
    Ok(())
}

pub(crate) fn preserve_approved_editorial_script(
    before: &Project,
    after: &Project,
    change: &Change,
) -> Result<()> {
    let Some(script) = before
        .narrative
        .editing
        .as_ref()
        .and_then(|editing| editing.script.as_ref())
    else {
        return Ok(());
    };
    let Some(approval) = &script.approval else {
        return Ok(());
    };
    if after
        .narrative
        .editing
        .as_ref()
        .and_then(|editing| editing.script.as_ref())
        == Some(script)
    {
        return Ok(());
    }
    if let Change::EditNarrative { expected, edit } = change
        && let NarrativeEdit::ReleaseScriptApproval {
            approval_id,
            expected_sha256,
            ..
        } = edit.as_ref()
        && *approval_id == approval.id
        && *expected_sha256 == script.sha256
    {
        expected.require_current(before)?;
        let next = after
            .narrative
            .editing
            .as_ref()
            .and_then(|editing| editing.script.as_ref())
            .ok_or_else(|| {
                DomainError::Locked("editorial release cannot delete the script".into())
            })?;
        let mut permitted = script.clone();
        permitted.approval = None;
        permitted.last_release = next.last_release.clone();
        ensure(
            next == &permitted
                && next.last_release.as_ref().is_some_and(|release| {
                    release.approval == *approval && release.source == *expected
                }),
            "editorial release changed text or did not retain the exact original approval",
        )?;
        return Ok(());
    }
    Err(DomainError::Locked(format!(
        "editorial-script:{} approval:{} must be explicitly released before replacing its exact text/decision",
        script.id, approval.id
    )))
}

pub(crate) fn preserve_protected_editorial_beats(
    before: &Project,
    after: &Project,
    change: &Change,
) -> Result<()> {
    for section in &before.narrative.protected_sections {
        let Some(original) = before
            .narrative
            .beats
            .iter()
            .find(|beat| beat.id == section.beat_id)
        else {
            continue;
        };
        let explicit_release = matches!(change, Change::EditNarrative { edit, .. }
            if matches!(edit.as_ref(), NarrativeEdit::SetBeatProtection { beat_id, protected: false, .. } if *beat_id == original.id));
        let unchanged = after
            .narrative
            .beats
            .iter()
            .find(|beat| beat.id == original.id)
            .is_some_and(|next| {
                next.label == original.label
                    && next.objective == original.objective
                    && next.audience_takeaway == original.audience_takeaway
                    && next.claim_ids == original.claim_ids
            });
        if !unchanged
            || (!explicit_release
                && !after
                    .narrative
                    .protected_sections
                    .iter()
                    .any(|candidate| candidate.beat_id == original.id))
        {
            return Err(DomainError::Locked(format!(
                "protected-section:narrative-beat:{} must be explicitly released before changing content or dropping protection",
                original.id
            )));
        }
    }
    Ok(())
}

impl Project {
    fn narrative_beat_unlocked(&self, beat_id: Uuid, kinds: &[LockKind]) -> Result<()> {
        require_beat(&self.narrative.beats, beat_id)?;
        self.ensure_unlocked(&format!("narrative-beat:{beat_id}"), kinds)?;
        self.ensure_unlocked(&format!("beat:{beat_id}"), kinds)
    }

    pub fn inspect_narrative_duration(
        &self,
        scene_id: Uuid,
        requested: RationalTime,
    ) -> Result<NarrativeTimingFeasibility> {
        positive(requested, "requested scene duration")?;
        self.scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        let before_preferences = assess_narrative_preferences(&self.narrative, &self.scenes)?;
        let mut candidate = self.clone();
        let outcome = candidate
            .apply_change(&Change::SetSceneDuration {
                scene_id,
                duration: requested,
            })
            .and_then(|()| preserve_narrative_minimum_holds(self, &candidate));
        let locks = self
            .locks
            .iter()
            .filter(|lock| lock.kind == LockKind::Timing)
            .cloned()
            .collect();
        match outcome {
            Err(error) => Ok(NarrativeTimingFeasibility {
                command_allowed: false,
                rejection: Some(error.to_string()),
                scene_changes: vec![],
                before_preferences,
                after_preferences: None,
                timing_locks_present: locks,
            }),
            Ok(()) => {
                let mut changes = Vec::new();
                for before in &self.scenes {
                    let after = candidate
                        .scenes
                        .iter()
                        .find(|scene| scene.id == before.id)
                        .ok_or_else(|| {
                            DomainError::Invalid(
                                "duration candidate replaced scene identity".into(),
                            )
                        })?;
                    if before.start != after.start || before.duration != after.duration {
                        changes.push(NarrativeSceneTimingDelta {
                            scene_id: before.id,
                            before_start: before.start,
                            after_start: after.start,
                            before_duration: before.duration,
                            after_duration: after.duration,
                        });
                    }
                }
                Ok(NarrativeTimingFeasibility {
                    command_allowed: true,
                    rejection: None,
                    scene_changes: changes,
                    before_preferences,
                    after_preferences: Some(assess_narrative_preferences(
                        &candidate.narrative,
                        &candidate.scenes,
                    )?),
                    timing_locks_present: locks,
                })
            }
        }
    }

    pub(crate) fn apply_narrative_edit(
        &mut self,
        expected: &NarrativeEditSource,
        edit: &NarrativeEdit,
    ) -> Result<()> {
        expected.require_current(self)?;
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let mut candidate = self.clone();
        let mut state = self.narrative.editing.clone().unwrap_or_default();
        match edit {
            NarrativeEdit::UpdateBeat { beat } => {
                self.narrative_beat_unlocked(beat.id, &[LockKind::Content])?;
                let previous = require_beat(&self.narrative.beats, beat.id)?;
                if self
                    .narrative
                    .protected_sections
                    .iter()
                    .any(|section| section.beat_id == beat.id)
                    && (previous.label != beat.label
                        || previous.objective != beat.objective
                        || previous.audience_takeaway != beat.audience_takeaway
                        || previous.claim_ids != beat.claim_ids)
                {
                    return Err(DomainError::Locked(format!(
                        "protected-section:narrative-beat:{} must be explicitly released before editing content",
                        beat.id
                    )));
                }
                let index = candidate
                    .narrative
                    .beats
                    .iter()
                    .position(|value| value.id == beat.id)
                    .unwrap();
                candidate.narrative.beats[index] = beat.clone();
            }
            NarrativeEdit::SetBeatProtection {
                beat_id,
                protected,
                reason,
            } => {
                self.narrative_beat_unlocked(*beat_id, &[LockKind::Content])?;
                text(reason, 1000, "protected-section decision reason")?;
                candidate
                    .narrative
                    .protected_sections
                    .retain(|section| section.beat_id != *beat_id);
                if *protected {
                    candidate
                        .narrative
                        .protected_sections
                        .push(ProtectedSection {
                            beat_id: *beat_id,
                            reason: reason.clone(),
                        });
                }
            }
            NarrativeEdit::SetMinimumHold { beat_id, duration } => {
                self.narrative_beat_unlocked(*beat_id, &[LockKind::Content])?;
                if let Some(duration) = duration {
                    positive(*duration, "minimum hold")?;
                }
                state.minimum_holds.retain(|hold| hold.beat_id != *beat_id);
                if let Some(duration) = duration {
                    state.minimum_holds.push(NarrativeMinimumHold {
                        beat_id: *beat_id,
                        duration: *duration,
                    });
                }
            }
            NarrativeEdit::UpsertBinding { binding } => {
                self.narrative_beat_unlocked(binding.beat_id, &[LockKind::Content])?;
                binding.target.interval(&self.scenes)?;
                if let Some(previous) = state
                    .bindings
                    .iter_mut()
                    .find(|value| value.id == binding.id)
                {
                    ensure(
                        previous.beat_id == binding.beat_id,
                        "binding identity cannot be transferred to another editorial beat",
                    )?;
                    *previous = binding.clone();
                } else {
                    state.bindings.push(binding.clone());
                }
            }
            NarrativeEdit::RemoveBinding { binding_id } => {
                let binding = state
                    .bindings
                    .iter()
                    .find(|binding| binding.id == *binding_id)
                    .ok_or_else(|| {
                        DomainError::NotFound(format!("narrative-binding:{binding_id}"))
                    })?;
                self.narrative_beat_unlocked(binding.beat_id, &[LockKind::Content])?;
                state.bindings.retain(|binding| binding.id != *binding_id);
            }
            NarrativeEdit::UpsertRelation { relation } => {
                self.narrative_beat_unlocked(relation.from_beat_id, &[LockKind::Content])?;
                self.narrative_beat_unlocked(relation.to_beat_id, &[LockKind::Content])?;
                if let Some(previous) = state
                    .relations
                    .iter_mut()
                    .find(|value| value.id == relation.id)
                {
                    self.narrative_beat_unlocked(previous.from_beat_id, &[LockKind::Content])?;
                    self.narrative_beat_unlocked(previous.to_beat_id, &[LockKind::Content])?;
                    *previous = relation.clone();
                } else {
                    state.relations.push(relation.clone());
                }
            }
            NarrativeEdit::RemoveRelation { relation_id } => {
                let relation = state
                    .relations
                    .iter()
                    .find(|relation| relation.id == *relation_id)
                    .ok_or_else(|| {
                        DomainError::NotFound(format!("narrative-relation:{relation_id}"))
                    })?;
                self.narrative_beat_unlocked(relation.from_beat_id, &[LockKind::Content])?;
                self.narrative_beat_unlocked(relation.to_beat_id, &[LockKind::Content])?;
                state
                    .relations
                    .retain(|relation| relation.id != *relation_id);
            }
            NarrativeEdit::Reorder {
                expected_order,
                ordered_ids,
            } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                let impact = inspect_narrative_reorder(self, expected_order, ordered_ids)?;
                for beat in &impact.moved_beat_ids {
                    self.narrative_beat_unlocked(*beat, &[LockKind::Content, LockKind::Timing])?;
                }
                let mut beats: BTreeMap<_, _> = candidate
                    .narrative
                    .beats
                    .iter()
                    .cloned()
                    .map(|beat| (beat.id, beat))
                    .collect();
                candidate.narrative.beats = ordered_ids
                    .iter()
                    .map(|id| beats.remove(id).unwrap())
                    .collect();
                state.last_reorder = Some(impact);
            }
            NarrativeEdit::SetScript {
                script_id,
                expected_version,
                text: raw,
            } => {
                ensure(
                    raw.len() <= MAX_EDITORIAL_SCRIPT_BYTES,
                    "editorial script exceeds 128000 UTF-8 bytes",
                )?;
                match &state.script {
                    None => {
                        ensure(
                            expected_version.is_none(),
                            "new editorial script cannot claim an existing version",
                        )?;
                        state.script = Some(EditorialScript {
                            id: *script_id,
                            version: 1,
                            text: raw.clone(),
                            sha256: script_sha256(raw),
                            approval: None,
                            last_release: None,
                        });
                    }
                    Some(previous) => {
                        ensure(
                            previous.id == *script_id
                                && Some(previous.version) == *expected_version,
                            "editorial script identity/version changed",
                        )?;
                        if previous.approval.is_some() {
                            return Err(DomainError::Locked(format!(
                                "editorial-script:{script_id} is approved; explicitly release its approval first"
                            )));
                        }
                        let mut updated = previous.clone();
                        updated.version = previous.version.checked_add(1).ok_or_else(|| {
                            DomainError::Invalid("editorial script version overflow".into())
                        })?;
                        updated.text = raw.clone();
                        updated.sha256 = script_sha256(raw);
                        state.script = Some(updated);
                    }
                }
            }
            NarrativeEdit::ApproveScript {
                approval_id,
                script_id,
                expected_version,
                expected_sha256,
                reviewer,
                reason,
            } => {
                let script = state.script.as_mut().ok_or_else(|| {
                    DomainError::NotFound(format!("editorial-script:{script_id}"))
                })?;
                ensure(
                    script.id == *script_id
                        && script.version == *expected_version
                        && script.sha256 == *expected_sha256,
                    "editorial approval source changed",
                )?;
                ensure(
                    script.approval.is_none(),
                    "editorial script is already approved",
                )?;
                script.approval = Some(EditorialScriptApproval {
                    id: *approval_id,
                    script_id: *script_id,
                    script_version: *expected_version,
                    script_sha256: expected_sha256.clone(),
                    source: expected.clone(),
                    reviewer: reviewer.clone(),
                    reason: reason.clone(),
                });
            }
            NarrativeEdit::ReleaseScriptApproval {
                approval_id,
                expected_sha256,
                reviewer,
                reason,
            } => {
                let script = state
                    .script
                    .as_mut()
                    .ok_or_else(|| DomainError::NotFound("editorial-script".into()))?;
                let approval = script.approval.as_ref().ok_or_else(|| {
                    DomainError::Invalid("editorial script is not approved".into())
                })?;
                ensure(
                    approval.id == *approval_id && script.sha256 == *expected_sha256,
                    "editorial approval identity/digest changed",
                )?;
                text(reviewer, 256, "editorial release reviewer")?;
                text(reason, 4000, "editorial release reason")?;
                script.last_release = Some(EditorialScriptRelease {
                    approval: approval.clone(),
                    source: expected.clone(),
                    reviewer: reviewer.clone(),
                    reason: reason.clone(),
                });
                script.approval = None;
            }
            NarrativeEdit::ProposeDuration {
                proposal_id,
                scene_id,
                requested_duration,
                reason,
            } => {
                ensure(
                    !state
                        .timing_proposals
                        .iter()
                        .any(|proposal| proposal.id == *proposal_id),
                    "timing proposal identity already exists",
                )?;
                text(reason, 4000, "timing proposal reason")?;
                let original_duration = self
                    .scenes
                    .iter()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?
                    .duration;
                let feasibility =
                    self.inspect_narrative_duration(*scene_id, *requested_duration)?;
                state.timing_proposals.push(NarrativeTimingProposal {
                    id: *proposal_id,
                    source: expected.clone(),
                    scene_id: *scene_id,
                    original_duration,
                    requested_duration: *requested_duration,
                    reason: reason.clone(),
                    script_snapshot: state.script.clone(),
                    feasibility,
                    decision: None,
                });
            }
            NarrativeEdit::DecideDuration {
                proposal_id,
                decision,
                reason,
            } => {
                text(reason, 4000, "timing decision reason")?;
                let index = state
                    .timing_proposals
                    .iter()
                    .position(|proposal| proposal.id == *proposal_id)
                    .ok_or_else(|| {
                        DomainError::NotFound(format!("narrative-timing-proposal:{proposal_id}"))
                    })?;
                let proposal = state.timing_proposals[index].clone();
                ensure(
                    proposal.decision.is_none(),
                    "timing proposal already has an explicit decision",
                )?;
                if *decision == NarrativeTimingDecision::ApplyDuration {
                    ensure(
                        proposal.source.project_id == self.id
                            && proposal.source.generation == self.generation
                            && proposal.source.branch_id == self.active_branch
                            && proposal.source.base_revision.checked_add(1) == Some(self.revision),
                        "timing proposal is stale; retain it and request a fresh assessment",
                    )?;
                    ensure(
                        proposal.script_snapshot == state.script,
                        "editorial script/approval changed since timing proposal",
                    )?;
                    ensure(
                        self.scenes.iter().any(|scene| {
                            scene.id == proposal.scene_id
                                && scene.duration == proposal.original_duration
                        }),
                        "original scene duration changed since timing proposal",
                    )?;
                    // The retained report is evidence, never authority to bypass a
                    // current lock/containment rule. Re-execute the same real guard.
                    candidate.apply_change(&Change::SetSceneDuration {
                        scene_id: proposal.scene_id,
                        duration: proposal.requested_duration,
                    })?;
                    preserve_narrative_minimum_holds(self, &candidate)?;
                }
                state.timing_proposals[index].decision = Some(NarrativeTimingDecisionRecord {
                    decision: decision.clone(),
                    source: expected.clone(),
                    reason: reason.clone(),
                });
            }
        }
        candidate.narrative.editing = Some(state);
        candidate.validate()?;
        validate_narrative_realizations(&candidate.narrative, &candidate.scenes)?;
        *self = candidate;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn q(n: i64, d: i64) -> RationalTime {
        RationalTime::new(n, d).unwrap()
    }
    fn edit(project: &mut Project, action: NarrativeEdit) -> Result<()> {
        let change = Change::EditNarrative {
            expected: NarrativeEditSource::from(&*project),
            edit: Box::new(action),
        };
        project.apply_change(&change)?;
        project.commit_revision(project.revision + 1, &change)
    }
    fn sample() -> Project {
        let mut project = Project::new("Editorial identity and source authority").unwrap();
        for name in ["One", "Two"] {
            project
                .apply_change(&Change::AddScene {
                    name: name.into(),
                    objective: "An authored realization".into(),
                    duration_seconds: 6,
                })
                .unwrap();
        }
        for name in ["A", "B", "C", "D"] {
            project
                .apply_change(&Change::AddNarrativeBeat {
                    label: name.into(),
                    objective: format!("Understanding {name}"),
                    audience_takeaway: format!("Takeaway {name}"),
                    claim_ids: vec![],
                    preferred_duration: None,
                })
                .unwrap();
        }
        let ids: Vec<_> = project.narrative.beats.iter().map(|beat| beat.id).collect();
        let mut state = NarrativeEditingState::default();
        state.bindings.push(NarrativeRealizationBinding {
            id: Uuid::now_v7(),
            beat_id: ids[0],
            target: NarrativeRealizationTarget::Scene {
                scene_id: project.scenes[0].id,
            },
        });
        state.bindings.push(NarrativeRealizationBinding {
            id: Uuid::now_v7(),
            beat_id: ids[1],
            target: NarrativeRealizationTarget::Scene {
                scene_id: project.scenes[1].id,
            },
        });
        state.relations.push(NarrativeRelation {
            id: Uuid::now_v7(),
            from_beat_id: ids[0],
            to_beat_id: ids[1],
            kind: NarrativeRelationKind::Adjacent,
            reason: "A prepares B".into(),
        });
        state.relations.push(NarrativeRelation {
            id: Uuid::now_v7(),
            from_beat_id: ids[2],
            to_beat_id: ids[3],
            kind: NarrativeRelationKind::Precedes,
            reason: "Independent C then D".into(),
        });
        project.narrative.editing = Some(state);
        project.validate().unwrap();
        project
    }
    fn script(project: &mut Project, raw: &str) {
        edit(
            project,
            NarrativeEdit::SetScript {
                script_id: Uuid::now_v7(),
                expected_version: None,
                text: raw.into(),
            },
        )
        .unwrap();
    }
    fn approve(project: &mut Project) {
        let script = project
            .narrative
            .editing
            .as_ref()
            .unwrap()
            .script
            .as_ref()
            .unwrap()
            .clone();
        edit(
            project,
            NarrativeEdit::ApproveScript {
                approval_id: Uuid::now_v7(),
                script_id: script.id,
                expected_version: script.version,
                expected_sha256: script.sha256,
                reviewer: "Owner decision (not authenticated identity)".into(),
                reason: "Keep the exact editorial wording".into(),
            },
        )
        .unwrap();
    }

    #[test]
    fn editorial_reorder_retains_original_ids_and_only_changed_declared_relations() {
        let mut project = sample();
        let before = project.clone();
        let old: Vec<_> = project.narrative.beats.iter().map(|beat| beat.id).collect();
        let after = vec![old[1], old[0], old[2], old[3]];
        edit(
            &mut project,
            NarrativeEdit::Reorder {
                expected_order: old.clone(),
                ordered_ids: after.clone(),
            },
        )
        .unwrap();
        assert_eq!(
            project
                .narrative
                .beats
                .iter()
                .map(|beat| beat.id)
                .collect::<Vec<_>>(),
            after
        );
        for beat in &before.narrative.beats {
            assert_eq!(
                project
                    .narrative
                    .beats
                    .iter()
                    .find(|value| value.id == beat.id),
                Some(beat)
            );
        }
        assert_eq!(project.scenes, before.scenes);
        assert_eq!(project.audio, before.audio);
        assert_eq!(project.assets, before.assets);
        let state = project.narrative.editing.as_ref().unwrap();
        let original = before.narrative.editing.as_ref().unwrap();
        assert_eq!(state.relations, original.relations);
        assert_eq!(state.bindings, original.bindings);
        let impact = state.last_reorder.as_ref().unwrap();
        assert_eq!(impact.affected_relations.len(), 1);
        assert_eq!(
            impact.affected_relations[0].relation_id,
            original.relations[0].id
        );
        assert_eq!(
            impact.conflicting_relation_ids,
            vec![original.relations[0].id]
        );
        assert_eq!(impact.affected_binding_ids.len(), 2);
        assert_eq!(impact.affected_scene_ids.len(), 2);
        assert!(
            !impact
                .affected_relations
                .iter()
                .any(|row| row.relation_id == original.relations[1].id)
        );
    }
    #[test]
    fn reorder_rejects_missing_duplicate_foreign_and_stale_order_atomically() {
        let mut project = sample();
        let ids: Vec<_> = project.narrative.beats.iter().map(|beat| beat.id).collect();
        for order in [
            ids[..3].to_vec(),
            vec![ids[0], ids[0], ids[2], ids[3]],
            vec![ids[0], ids[1], ids[2], Uuid::now_v7()],
        ] {
            let before = project.clone();
            assert!(
                edit(
                    &mut project,
                    NarrativeEdit::Reorder {
                        expected_order: ids.clone(),
                        ordered_ids: order
                    }
                )
                .is_err()
            );
            assert_eq!(project, before);
        }
        let before = project.clone();
        let mut stale = ids.clone();
        stale.swap(0, 1);
        assert!(
            edit(
                &mut project,
                NarrativeEdit::Reorder {
                    expected_order: stale,
                    ordered_ids: ids
                }
            )
            .is_err()
        );
        assert_eq!(project, before);
    }
    #[test]
    fn editorial_edit_rejects_each_foreign_source_dimension_without_mutation() {
        let mut project = sample();
        let source = NarrativeEditSource::from(&project);
        for dimension in 0..4 {
            let mut bad = source.clone();
            match dimension {
                0 => bad.project_id = Uuid::now_v7(),
                1 => bad.generation = Uuid::now_v7(),
                2 => bad.branch_id = Uuid::now_v7(),
                _ => bad.base_revision += 1,
            }
            let before = project.clone();
            let change = Change::EditNarrative {
                expected: bad,
                edit: Box::new(NarrativeEdit::Reorder {
                    expected_order: project.narrative.beats.iter().map(|beat| beat.id).collect(),
                    ordered_ids: project
                        .narrative
                        .beats
                        .iter()
                        .rev()
                        .map(|beat| beat.id)
                        .collect(),
                }),
            };
            assert!(project.apply_change(&change).is_err());
            assert_eq!(project, before);
        }
    }
    #[test]
    fn editorial_reorder_checks_project_and_both_beat_lock_aliases() {
        for resource in ["project", "beat", "narrative-beat"] {
            let mut project = sample();
            let ids: Vec<_> = project.narrative.beats.iter().map(|beat| beat.id).collect();
            let key = if resource == "project" {
                project.resource_key()
            } else {
                format!("{resource}:{}", ids[0])
            };
            project.locks.push(ProjectLock {
                id: Uuid::now_v7(),
                resource: key,
                kind: LockKind::Timing,
                note: "Explicit timing authority".into(),
            });
            let before = project.clone();
            assert!(
                edit(
                    &mut project,
                    NarrativeEdit::Reorder {
                        expected_order: ids.clone(),
                        ordered_ids: ids.into_iter().rev().collect()
                    }
                )
                .is_err()
            );
            assert_eq!(project, before);
        }
    }
    #[test]
    fn protected_editorial_content_needs_explicit_release_but_preference_is_distinct() {
        let mut project = sample();
        let id = project.narrative.beats[0].id;
        edit(
            &mut project,
            NarrativeEdit::SetBeatProtection {
                beat_id: id,
                protected: true,
                reason: "Keep wording".into(),
            },
        )
        .unwrap();
        let before = project.clone();
        let mut beat = project.narrative.beats[0].clone();
        beat.objective = "Unrequested shortening".into();
        assert!(edit(&mut project, NarrativeEdit::UpdateBeat { beat }).is_err());
        assert_eq!(project, before);
        let mut beat = project.narrative.beats[0].clone();
        beat.preferred_duration = Some(q(13, 2));
        edit(&mut project, NarrativeEdit::UpdateBeat { beat }).unwrap();
        assert_eq!(
            project.narrative.beats[0].objective,
            before.narrative.beats[0].objective
        );
    }
    #[test]
    fn explicit_realizations_use_exact_interval_union_not_double_counting() {
        let mut project = sample();
        let id = project.narrative.beats[0].id;
        let scene = &mut project.scenes[0];
        let scene_beat = Beat {
            id: Uuid::now_v7(),
            label: "Overlap".into(),
            objective: "Same dwell".into(),
            start: q(1001, 30000),
            duration: q(1, 3),
        };
        let target = NarrativeRealizationTarget::SceneBeat {
            scene_id: scene.id,
            scene_beat_id: scene_beat.id,
        };
        scene.beats.push(scene_beat);
        edit(
            &mut project,
            NarrativeEdit::UpsertBinding {
                binding: NarrativeRealizationBinding {
                    id: Uuid::now_v7(),
                    beat_id: id,
                    target,
                },
            },
        )
        .unwrap();
        let report = assess_narrative_preferences(&project.narrative, &project.scenes).unwrap();
        assert_eq!(report[0].binding_ids.len(), 2);
        assert_eq!(report[0].allocated_duration, Some(q(6, 1)));
    }
    #[test]
    fn unbound_preference_stays_unknown_and_contradictory_intent_is_retained() {
        let mut project = sample();
        let id = project.narrative.beats[2].id;
        let mut beat = project.narrative.beats[2].clone();
        beat.preferred_duration = Some(q(1001, 30000));
        edit(&mut project, NarrativeEdit::UpdateBeat { beat }).unwrap();
        edit(
            &mut project,
            NarrativeEdit::SetMinimumHold {
                beat_id: id,
                duration: Some(q(1, 3)),
            },
        )
        .unwrap();
        let report = assess_narrative_preferences(&project.narrative, &project.scenes).unwrap();
        assert_eq!(report[2].allocated_duration, None);
        assert_eq!(report[2].minimum_shortfall, None);
        assert_eq!(report[2].preferred_delta, None);
        assert!(report[2].contradictory_preferences);
    }
    #[test]
    fn preference_can_be_retained_under_a_hard_timing_lock_without_moving_time() {
        let mut project = sample();
        let id = project.narrative.beats[0].id;
        project.locks.push(ProjectLock {
            id: Uuid::now_v7(),
            resource: project.resource_key(),
            kind: LockKind::Timing,
            note: "No timeline move".into(),
        });
        let scenes = project.scenes.clone();
        edit(
            &mut project,
            NarrativeEdit::SetMinimumHold {
                beat_id: id,
                duration: Some(q(8, 1)),
            },
        )
        .unwrap();
        assert_eq!(project.scenes, scenes);
        assert_eq!(
            assess_narrative_preferences(&project.narrative, &project.scenes).unwrap()[0]
                .minimum_shortfall,
            Some(q(2, 1))
        );
    }
    #[test]
    fn binding_rejects_foreign_scene_beat_and_identity_transfer_atomically() {
        let mut project = sample();
        let original = project.narrative.editing.as_ref().unwrap().bindings[0].clone();
        for binding in [
            NarrativeRealizationBinding {
                id: original.id,
                beat_id: project.narrative.beats[1].id,
                target: original.target.clone(),
            },
            NarrativeRealizationBinding {
                id: Uuid::now_v7(),
                beat_id: original.beat_id,
                target: NarrativeRealizationTarget::SceneBeat {
                    scene_id: project.scenes[0].id,
                    scene_beat_id: Uuid::now_v7(),
                },
            },
        ] {
            let before = project.clone();
            assert!(edit(&mut project, NarrativeEdit::UpsertBinding { binding }).is_err());
            assert_eq!(project, before);
        }
    }
    #[test]
    fn approved_editorial_script_is_exact_raw_text_and_distinct_from_audio_take() {
        let mut project = sample();
        let audio = project.audio.clone();
        let take = project.production_design.narration_take_lock.clone();
        script(
            &mut project,
            "  Keep this editorial wording.\nSilence is intentional.  \n",
        );
        approve(&mut project);
        let saved = project
            .narrative
            .editing
            .as_ref()
            .unwrap()
            .script
            .as_ref()
            .unwrap()
            .clone();
        let before = project.clone();
        assert!(
            edit(
                &mut project,
                NarrativeEdit::SetScript {
                    script_id: saved.id,
                    expected_version: Some(saved.version),
                    text: "Shortened".into()
                }
            )
            .is_err()
        );
        assert_eq!(project, before);
        assert_eq!(project.audio, audio);
        assert_eq!(project.production_design.narration_take_lock, take);
        assert_eq!(
            saved.sha256,
            script_sha256("  Keep this editorial wording.\nSilence is intentional.  \n")
        );
    }
    #[test]
    fn script_release_pins_exact_approval_and_retains_the_original_decision() {
        let mut project = sample();
        script(&mut project, "Approved wording");
        approve(&mut project);
        let script = project
            .narrative
            .editing
            .as_ref()
            .unwrap()
            .script
            .as_ref()
            .unwrap()
            .clone();
        let approval = script.approval.clone().unwrap();
        let before = project.clone();
        assert!(
            edit(
                &mut project,
                NarrativeEdit::ReleaseScriptApproval {
                    approval_id: Uuid::now_v7(),
                    expected_sha256: script.sha256.clone(),
                    reviewer: "Owner".into(),
                    reason: "Explicit correction".into()
                }
            )
            .is_err()
        );
        assert_eq!(project, before);
        edit(
            &mut project,
            NarrativeEdit::ReleaseScriptApproval {
                approval_id: approval.id,
                expected_sha256: script.sha256.clone(),
                reviewer: "Owner".into(),
                reason: "Explicit correction".into(),
            },
        )
        .unwrap();
        let released = project
            .narrative
            .editing
            .as_ref()
            .unwrap()
            .script
            .as_ref()
            .unwrap();
        assert_eq!(released.text, script.text);
        assert!(released.approval.is_none());
        assert_eq!(released.last_release.as_ref().unwrap().approval, approval);
    }
    #[test]
    fn duration_conflict_is_a_retained_proposal_and_never_shortens_approved_text() {
        let mut project = sample();
        script(
            &mut project,
            "The full approved editorial script remains exact.",
        );
        approve(&mut project);
        let scene_id = project.scenes[0].id;
        project.locks.push(ProjectLock {
            id: Uuid::now_v7(),
            resource: format!("scene:{scene_id}"),
            kind: LockKind::Timing,
            note: "Fixed approved duration".into(),
        });
        let before = project.clone();
        let id = Uuid::now_v7();
        edit(
            &mut project,
            NarrativeEdit::ProposeDuration {
                proposal_id: id,
                scene_id,
                requested_duration: q(4, 1),
                reason: "Inspect the conflict, preserve wording".into(),
            },
        )
        .unwrap();
        let state = project.narrative.editing.as_ref().unwrap();
        let proposal = &state.timing_proposals[0];
        assert!(!proposal.feasibility.command_allowed);
        assert!(
            proposal
                .feasibility
                .rejection
                .as_ref()
                .unwrap()
                .contains(&scene_id.to_string())
        );
        assert_eq!(
            state.script,
            before.narrative.editing.as_ref().unwrap().script
        );
        assert_eq!(project.scenes, before.scenes);
        assert_eq!(project.audio, before.audio);
        assert_eq!(proposal.script_snapshot, state.script);
        assert!(proposal.decision.is_none());
    }
    #[test]
    fn explicitly_applying_a_fresh_duration_proposal_uses_existing_guard_and_preserves_script() {
        let mut project = sample();
        script(&mut project, "Approved words");
        approve(&mut project);
        let scene_id = project.scenes[0].id;
        let id = Uuid::now_v7();
        let script = project.narrative.editing.as_ref().unwrap().script.clone();
        edit(
            &mut project,
            NarrativeEdit::ProposeDuration {
                proposal_id: id,
                scene_id,
                requested_duration: q(13, 2),
                reason: "More dwell".into(),
            },
        )
        .unwrap();
        assert_eq!(project.scenes[0].duration, q(6, 1));
        edit(
            &mut project,
            NarrativeEdit::DecideDuration {
                proposal_id: id,
                decision: NarrativeTimingDecision::ApplyDuration,
                reason: "Accept this exact scene duration".into(),
            },
        )
        .unwrap();
        assert_eq!(project.scenes[0].duration, q(13, 2));
        assert_eq!(project.scenes[1].start, q(13, 2));
        assert_eq!(project.narrative.editing.as_ref().unwrap().script, script);
    }
    #[test]
    fn advanced_revision_requires_a_new_proposal_and_never_replays_the_old_duration() {
        let mut project = sample();
        let scene_id = project.scenes[0].id;
        let id = Uuid::now_v7();
        edit(
            &mut project,
            NarrativeEdit::ProposeDuration {
                proposal_id: id,
                scene_id,
                requested_duration: q(7, 1),
                reason: "Candidate dwell".into(),
            },
        )
        .unwrap();
        let id_beat = project.narrative.beats[0].id;
        edit(
            &mut project,
            NarrativeEdit::SetMinimumHold {
                beat_id: id_beat,
                duration: Some(q(5, 1)),
            },
        )
        .unwrap();
        let before = project.clone();
        assert!(
            edit(
                &mut project,
                NarrativeEdit::DecideDuration {
                    proposal_id: id,
                    decision: NarrativeTimingDecision::ApplyDuration,
                    reason: "Old approval is not a new grant".into()
                }
            )
            .is_err()
        );
        assert_eq!(project, before);
    }
    #[test]
    fn general_duration_edit_cannot_bypass_an_affected_minimum_hold() {
        let mut project = sample();
        let id = project.narrative.beats[0].id;
        let scene_id = project.scenes[0].id;
        edit(
            &mut project,
            NarrativeEdit::SetMinimumHold {
                beat_id: id,
                duration: Some(q(11, 2)),
            },
        )
        .unwrap();
        let before = project.clone();
        assert!(
            project
                .apply_change(&Change::SetSceneDuration {
                    scene_id,
                    duration: q(5, 1)
                })
                .is_err()
        );
        assert_eq!(project, before);
        assert!(
            !project
                .inspect_narrative_duration(scene_id, q(5, 1))
                .unwrap()
                .command_allowed
        );
    }
    #[test]
    fn unrelated_timing_change_does_not_require_resolving_a_preexisting_hold_conflict() {
        let mut project = sample();
        let id = project.narrative.beats[0].id;
        let scene_id = project.scenes[1].id;
        edit(
            &mut project,
            NarrativeEdit::SetMinimumHold {
                beat_id: id,
                duration: Some(q(8, 1)),
            },
        )
        .unwrap();
        project
            .apply_change(&Change::SetSceneDuration {
                scene_id,
                duration: q(7, 1),
            })
            .unwrap();
        assert_eq!(project.scenes[0].duration, q(6, 1));
        assert_eq!(project.scenes[1].duration, q(7, 1));
    }
    #[test]
    fn branch_checkout_cannot_silently_drop_an_approved_editorial_script() {
        let mut project = sample();
        project
            .apply_change(&Change::CreateBranch {
                name: "Before editorial approval".into(),
            })
            .unwrap();
        let branch = project.branches.last().unwrap().id;
        script(&mut project, "Protected across branch changes");
        approve(&mut project);
        let before = project.clone();
        assert!(
            project
                .apply_change(&Change::CheckoutBranch { branch_id: branch })
                .is_err()
        );
        assert_eq!(project, before);
    }
    #[test]
    fn identical_approved_script_can_follow_an_explicit_branch_checkout() {
        let mut project = sample();
        script(&mut project, "Same script decision");
        approve(&mut project);
        project
            .apply_change(&Change::CreateBranch {
                name: "Identical approved script".into(),
            })
            .unwrap();
        let branch = project.branches.last().unwrap().id;
        let script = project.narrative.editing.as_ref().unwrap().script.clone();
        project
            .apply_change(&Change::CheckoutBranch { branch_id: branch })
            .unwrap();
        assert_eq!(project.active_branch, branch);
        assert_eq!(project.narrative.editing.as_ref().unwrap().script, script);
    }
    #[test]
    fn legacy_narrative_omits_new_metadata_and_new_state_roundtrips_without_loss() {
        let old = r#"{"premise":"Original","beats":[],"protected_sections":[]}"#;
        let narrative: Narrative = serde_json::from_str(old).unwrap();
        assert!(narrative.editing.is_none());
        assert_eq!(
            serde_json::to_value(narrative).unwrap(),
            serde_json::from_str::<serde_json::Value>(old).unwrap()
        );
        let project = sample();
        let bytes = serde_json::to_vec(&project).unwrap();
        let reopened: Project = serde_json::from_slice(&bytes).unwrap();
        reopened.validate().unwrap();
        assert_eq!(reopened, project);
    }
    #[test]
    fn canonical_time_validation_rejects_unreduced_and_overflowing_pairs() {
        assert!(
            serde_json::from_str::<NarrativeMinimumHold>(&format!(
                r#"{{"beat_id":"{}","duration":{{"num":"2","den":"6"}}}}"#,
                Uuid::now_v7()
            ))
            .is_err()
        );
        assert!(add(q(i64::MAX - 1, i64::MAX), q(1, i64::MAX - 1)).is_err());
        let mut project = sample();
        let before = project.clone();
        let id = project.narrative.beats[0].id;
        assert!(
            edit(
                &mut project,
                NarrativeEdit::SetMinimumHold {
                    beat_id: id,
                    duration: Some(q(0, 1))
                }
            )
            .is_err()
        );
        assert_eq!(project, before);
    }
}
