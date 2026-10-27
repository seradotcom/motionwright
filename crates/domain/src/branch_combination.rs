//! Derive an inactive editable branch from two exact, existing authoring states.
use crate::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BranchCombinationField {
    Transform,
    Style,
    Motion,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BranchCombinationPick {
    pub node_id: Uuid,
    pub field: BranchCombinationField,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BranchCombination {
    /// Also retained in the ordinary Change journal; never a Broker grant.
    #[serde(deserialize_with = "deserialize_branch_combination_source")]
    pub source: RevisionStamp,
    pub base_branch_id: Uuid,
    pub base_head_revision: u64,
    pub donor_branch_id: Uuid,
    pub donor_head_revision: u64,
    /// Allocate once when the author starts this intent; retain across UNKNOWN.
    pub candidate_branch_id: Uuid,
    pub candidate_name: String,
    pub scene_id: Uuid,
    pub picks: Vec<BranchCombinationPick>,
    pub rationale: String,
}

fn deserialize_branch_combination_source<'de, D>(
    deserializer: D,
) -> std::result::Result<RevisionStamp, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct BranchCombinationSource {
        resource: String,
        generation: Uuid,
        revision: u64,
    }

    let source = BranchCombinationSource::deserialize(deserializer)?;
    Ok(RevisionStamp {
        resource: source.resource,
        generation: source.generation,
        revision: source.revision,
    })
}

impl Project {
    /// Source diff only. Rendering and creative judgement remain separate.
    pub fn preview_branch_combination(
        &self,
        input: &BranchCombination,
    ) -> Result<CreativePatchPreview> {
        self.prepare_branch_combination(input)
            .map(|(_, preview)| preview)
    }

    fn prepare_branch_combination(
        &self,
        input: &BranchCombination,
    ) -> Result<(Project, CreativePatchPreview)> {
        self.validate()?;
        if input.source != self.stamp() || input.base_branch_id != self.active_branch {
            return Err(DomainError::Invalid(
                "branch combination source is stale".into(),
            ));
        }
        if input.candidate_branch_id.is_nil()
            || self
                .branches
                .iter()
                .any(|b| b.id == input.candidate_branch_id)
            || input.candidate_name.trim().is_empty()
            || input.candidate_name.len() > 120
            || input.candidate_name.chars().any(char::is_control)
            || self
                .branches
                .iter()
                .any(|b| b.name.eq_ignore_ascii_case(&input.candidate_name))
            || self.branches.len() >= 256
            || self.branch_workspaces.len() >= 256
        {
            return Err(DomainError::Invalid(
                "candidate branch identity or name is unavailable".into(),
            ));
        }
        crate::production_design::design_text(&input.rationale, 4000, "combination rationale")?;
        let base = self
            .branches
            .iter()
            .find(|b| b.id == self.active_branch)
            .ok_or_else(|| DomainError::Invalid("active branch is missing".into()))?;
        let donor = self
            .branches
            .iter()
            .find(|b| b.id == input.donor_branch_id)
            .ok_or_else(|| DomainError::NotFound(format!("branch:{}", input.donor_branch_id)))?;
        if donor.id == base.id
            || base.head_revision != input.base_head_revision
            || donor.head_revision != input.donor_head_revision
        {
            return Err(DomainError::Invalid(
                "combination requires two exact branch heads".into(),
            ));
        }
        let saved = self
            .branch_workspaces
            .iter()
            .find(|w| w.branch_id == donor.id)
            .ok_or_else(|| {
                DomainError::Invalid("donor branch has no saved authoring state".into())
            })?;
        if saved.base_revision != donor.base_revision {
            return Err(DomainError::Invalid(
                "donor branch base and workspace disagree".into(),
            ));
        }
        let other = &saved.current_state;
        if self.brief != other.brief
            || self.audio != other.audio
            || self.deliverables != other.deliverables
            || self.locks != other.locks
            || self.production_design.narration_take_lock
                != other.production_design.narration_take_lock
        {
            return Err(DomainError::Invalid(
                "combine only alternatives with the same brief, voice, profiles and locks".into(),
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
        // A retained combination must not be born with an undo that is
        // already forbidden by the same preserved scene locks. Keep this
        // conservative guard aligned with preview_creative_patch_undo.
        self.ensure_unlocked(
            &format!("scene:{}", input.scene_id),
            &[
                LockKind::Content,
                LockKind::Style,
                LockKind::Position,
                LockKind::Timing,
            ],
        )?;
        let source = self
            .scenes
            .iter()
            .find(|s| s.id == input.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", input.scene_id)))?;
        let donor_scene = other
            .scenes
            .iter()
            .find(|s| s.id == input.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("donor scene:{}", input.scene_id)))?;
        if source.start != donor_scene.start
            || source.duration != donor_scene.duration
            || source.renderer != donor_scene.renderer
        {
            return Err(DomainError::Invalid(
                "combination requires the same scene window and renderer".into(),
            ));
        }
        if input.picks.is_empty() || input.picks.len() > 64 {
            return Err(DomainError::Invalid(
                "combination requires 1..=64 explicit field choices".into(),
            ));
        }
        let mut choices = BTreeSet::new();
        let mut scope = BTreeSet::new();
        let mut changes = Vec::new();
        let mut structural = false;
        for pick in &input.picks {
            if !choices.insert((pick.node_id, pick.field)) {
                return Err(DomainError::Invalid("duplicate node field choice".into()));
            }
            scope.insert(pick.node_id);
            let before = source
                .nodes
                .iter()
                .find(|n| n.id == pick.node_id)
                .ok_or_else(|| DomainError::NotFound(format!("base node:{}", pick.node_id)))?;
            let after = donor_scene
                .nodes
                .iter()
                .find(|n| n.id == pick.node_id)
                .ok_or_else(|| DomainError::NotFound(format!("donor node:{}", pick.node_id)))?;
            if before.kind != after.kind
                || before.parent_id != after.parent_id
                || before.coordinate_space != after.coordinate_space
                || before.relations != after.relations
                || before.property_locks != after.property_locks
            {
                return Err(DomainError::Invalid(
                    "chosen node identity, coordinate context or locks differ".into(),
                ));
            }
            match pick.field {
                BranchCombinationField::Transform => {
                    structural |= (
                        before.x,
                        before.y,
                        before.width,
                        before.height,
                        before.rotation_deg,
                        before.opacity,
                    ) != (
                        after.x,
                        after.y,
                        after.width,
                        after.height,
                        after.rotation_deg,
                        after.opacity,
                    );
                    changes.push(Change::TransformCanvasNode {
                        scene_id: source.id,
                        node_id: before.id,
                        transform: CanvasTransform {
                            x: after.x,
                            y: after.y,
                            width: after.width,
                            height: after.height,
                            rotation_deg: after.rotation_deg,
                            opacity: after.opacity,
                        },
                    });
                }
                BranchCombinationField::Style => changes.push(Change::UpdateCanvasStyle {
                    scene_id: source.id,
                    node_id: before.id,
                    style: after.style.clone(),
                }),
                BranchCombinationField::Motion => {
                    structural |= before.keyframes != after.keyframes;
                    // Replace the chosen motion schedule, including removals. Do not
                    // append a donor's keys on top of unrelated existing animation.
                    for key in &before.keyframes {
                        changes.push(Change::RemoveCanvasKeyframe {
                            scene_id: source.id,
                            node_id: before.id,
                            at: key.at,
                            property: key.property,
                        });
                    }
                    for key in &after.keyframes {
                        changes.push(Change::SetCanvasKeyframe {
                            scene_id: source.id,
                            node_id: before.id,
                            keyframe: key.clone(),
                        });
                    }
                }
            }
        }
        if !structural || changes.len() > 512 {
            return Err(DomainError::Invalid(
                "combination needs an explicit transform or motion choice within 512 edits".into(),
            ));
        }
        let mut candidate = self.clone();
        for change in changes {
            // Existing domain changes enforce locks, keyframe bounds, Graph,
            // managed components, narrative and all cross-resource invariants.
            candidate.apply_change(&change)?;
        }
        let after = candidate
            .scenes
            .iter()
            .find(|s| s.id == source.id)
            .ok_or_else(|| DomainError::Invalid("combination lost its scene".into()))?;
        let normalize = |scene: &Scene| {
            let mut s = scene.clone();
            s.status = SceneStatus::Draft;
            s
        };
        if normalize(after) == normalize(source) || normalize(after) == normalize(donor_scene) {
            return Err(DomainError::Invalid(
                "choose a mixture that differs from both source scenes".into(),
            ));
        }
        let preview = CreativePatchPreview {
            source_revision: self.revision,
            scene_id: source.id,
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
        };
        Ok((candidate, preview))
    }

    pub(crate) fn combine_creative_branches(&mut self, input: &BranchCombination) -> Result<()> {
        let (mut derived, preview) = self.prepare_branch_combination(input)?;
        // Start the candidate with the existing bounded, reversible creative
        // patch record. This also marks the changed scene Draft.
        derived.commit_creative_preview(preview, input.rationale.clone(), None)?;
        let mut result = self.clone();
        result.branches.push(Branch {
            id: input.candidate_branch_id,
            name: input.candidate_name.clone(),
            parent_branch: Some(self.active_branch),
            base_revision: self.revision,
            head_revision: self.revision,
            protected: false,
            created_at: Utc::now(),
        });
        result.branch_workspaces.push(BranchWorkspace {
            branch_id: input.candidate_branch_id,
            base_revision: self.revision,
            base_state: self.capture_branch_state(),
            current_state: derived.capture_branch_state(),
        });
        // Parent authoring states, reviews, active branch and approvals are not
        // changed. No review is created or reanchored onto the derived branch.
        result.validate()?;
        *self = result;
        Ok(())
    }
}
