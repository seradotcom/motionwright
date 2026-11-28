use crate::{
    DomainError, LockKind, Project, RationalTime, RendererKind, Result, SceneBoundaryInput,
    SceneStatus,
};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

fn split_identity(right_scene: Uuid, kind: &[u8], original: Uuid) -> Uuid {
    let digest = Sha256::new()
        .chain_update(b"motionwright.scene-split/1\0")
        .chain_update(right_scene.as_bytes())
        .chain_update(kind)
        .chain_update(original.as_bytes())
        .finalize();
    let mut bytes = [0; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x80;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Uuid::from_bytes(bytes)
}

impl Project {
    pub(crate) fn split_scene(
        &mut self,
        scene_id: Uuid,
        right_id: Uuid,
        at: RationalTime,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        self.ensure_unlocked(
            &format!("scene:{scene_id}"),
            &[
                LockKind::Content,
                LockKind::Timing,
                LockKind::Renderer,
                LockKind::Position,
                LockKind::Style,
            ],
        )?;
        let index = self
            .scenes
            .iter()
            .position(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        if right_id.is_nil()
            || self.scenes.iter().any(|scene| scene.id == right_id)
            || self.branch_workspaces.iter().any(|workspace| {
                [&workspace.base_state, &workspace.current_state]
                    .iter()
                    .any(|state| state.scenes.iter().any(|scene| scene.id == right_id))
            })
        {
            return Err(DomainError::Invalid(
                "The continuation scene identity already exists or is invalid.".into(),
            ));
        }

        let scene = &self.scenes[index];
        if scene.renderer != RendererKind::MotionCanvas || scene.status == SceneStatus::Approved {
            return Err(DomainError::Invalid(
                "Split requires an editable Motion Canvas scene without an existing approval."
                    .into(),
            ));
        }
        if scene.nodes.iter().any(|node| !node.keyframes.is_empty()) {
            return Err(DomainError::Invalid("This scene contains animation. Its motion tracks must be preserved before it can be split.".into()));
        }
        let timing_error = |_| DomainError::Invalid("Scene split time is out of bounds.".into());
        at.validate().map_err(timing_error)?;
        let end = scene
            .start
            .checked_add(scene.duration)
            .map_err(timing_error)?;
        if at <= scene.start || at >= end {
            return Err(DomainError::Invalid(
                "Choose a frame strictly inside the selected scene.".into(),
            ));
        }
        let left_duration = at.checked_sub(scene.start).map_err(timing_error)?;
        let right_duration = end.checked_sub(at).map_err(timing_error)?;
        self.require_unbound_split_scene(scene_id)?;

        let mut left = scene.clone();
        let mut right = scene.clone();
        left.duration = left_duration;
        right.id = right_id;
        right.start = at;
        right.duration = right_duration;
        right.status = SceneStatus::Draft;
        let suffix = " (continued)";
        let mut name_end = scene.name.len().min(160 - suffix.len());
        while !scene.name.is_char_boundary(name_end) {
            name_end -= 1;
        }
        right.name = format!("{}{suffix}", &scene.name[..name_end]);
        left.beats.clear();
        right.beats.clear();
        for beat in &scene.beats {
            if beat.start >= left_duration {
                let mut shifted = beat.clone();
                shifted.start = beat
                    .start
                    .checked_sub(left_duration)
                    .map_err(timing_error)?;
                right.beats.push(shifted);
            } else if beat
                .start
                .checked_add(beat.duration)
                .map_err(timing_error)?
                <= left_duration
            {
                left.beats.push(beat.clone());
            } else {
                return Err(DomainError::Invalid(format!(
                    "The cut crosses the authored beat “{}”. Choose a frame outside that beat.",
                    beat.label
                )));
            }
        }

        let ids: BTreeMap<_, _> = scene
            .nodes
            .iter()
            .map(|node| (node.id, split_identity(right_id, b"node", node.id)))
            .collect();
        let existing: BTreeSet<_> = self
            .scenes
            .iter()
            .flat_map(|other| other.nodes.iter().map(|node| node.id))
            .collect();
        if ids.values().any(|id| existing.contains(id)) {
            return Err(DomainError::Invalid(
                "A continuation object identity already exists.".into(),
            ));
        }
        let remap = |id: Uuid| {
            ids.get(&id).copied().ok_or_else(|| {
                DomainError::Invalid("A scene object reference is outside the split source.".into())
            })
        };
        for node in &mut right.nodes {
            let original = node.id;
            self.ensure_unlocked(
                &format!("node:{original}"),
                &[
                    LockKind::Content,
                    LockKind::Timing,
                    LockKind::Renderer,
                    LockKind::Position,
                    LockKind::Style,
                ],
            )?;
            self.ensure_unlocked(
                &format!("canvas-node:{original}"),
                &[
                    LockKind::Content,
                    LockKind::Timing,
                    LockKind::Renderer,
                    LockKind::Position,
                    LockKind::Style,
                ],
            )?;
            node.id = remap(original)?;
            node.parent_id = node.parent_id.map(remap).transpose()?;
            for relation in &mut node.relations {
                relation.id = split_identity(right_id, b"relation", relation.id);
                relation.target_id = remap(relation.target_id)?;
            }
        }

        for profile in &mut self.deliverables {
            if let Some(index) = profile
                .included_scene_ids
                .iter()
                .position(|id| *id == scene_id)
            {
                profile.included_scene_ids.insert(index + 1, right_id);
            }
            for (original, copied) in &ids {
                if let Some(text) = profile.text_overrides.get(original).cloned() {
                    profile.text_overrides.insert(*copied, text);
                }
            }
        }
        self.scenes.splice(index..=index, [left, right]);
        Ok(())
    }

    fn require_unbound_split_scene(&self, scene_id: Uuid) -> Result<()> {
        let design = &self.production_design;
        let bound = design.plan.as_ref().is_some_and(|plan| plan.shots.iter().any(|shot| shot.scene_id == scene_id))
            || design.heroes.iter().any(|item| item.scene_id == scene_id)
            || design.capsules.iter().any(|item| item.scene_id == scene_id)
            || design.stages.iter().any(|item| item.scene_id == scene_id)
            || design.procedural_fields.iter().any(|item| item.scene_id == scene_id)
            || design.camera_reframes.iter().any(|item| item.scene_id == scene_id)
            || design.semantic_graphs.iter().any(|item| item.scene_id == scene_id)
            || design.measured_bindings.iter().any(|item| item.scene_id == scene_id)
            || design.brand_exceptions.iter().any(|item| item.scene_id == scene_id)
            || design.scene_boundaries.iter().any(|item| item.scene_id == scene_id
                || matches!(&item.input, SceneBoundaryInput::Predecessor { scene_id: Some(source), .. } if *source == scene_id))
            || self.paid_generation.jobs.iter().any(|job| job.spec.scene_id == scene_id)
            || self.audio.sfx_cues.iter().any(|cue| cue.scene_id == scene_id)
            || self.narrative.editing.as_ref().is_some_and(|editing| editing.bindings.iter().any(|binding| binding.target.scene_id() == scene_id));
        if bound {
            return Err(DomainError::Invalid("This scene has a bound source, production decision, or timing dependency. Resolve it before splitting.".into()));
        }
        if self.deliverables.iter().any(|profile| {
            profile.protected_scene_ids.contains(&scene_id)
                || (profile.timing_locked
                    && (profile.included_scene_ids.is_empty()
                        || profile.included_scene_ids.contains(&scene_id)))
        }) || design.delivery_recipes.values().any(|recipe| {
            recipe
                .scene_intents
                .iter()
                .any(|intent| intent.scene_id == scene_id)
                || recipe.mlt_dissolve.as_ref().is_some_and(|cut| {
                    cut.incoming_scene_id == scene_id || cut.outgoing_scene_id == scene_id
                })
        }) {
            return Err(DomainError::Invalid(
                "A delivery profile has protected or scene-specific timing intent for this scene."
                    .into(),
            ));
        }
        Ok(())
    }
}
