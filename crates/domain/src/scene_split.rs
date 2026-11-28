use crate::{
    CanvasKeyframe, CanvasNode, DomainError, LockKind, MotionInterpolation, MotionProperty,
    Project, RationalTime, RendererKind, Result, SceneBoundaryInput, SceneStatus,
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

const MOTION_PROPERTIES: [MotionProperty; 6] = [
    MotionProperty::X,
    MotionProperty::Y,
    MotionProperty::Width,
    MotionProperty::Height,
    MotionProperty::RotationDeg,
    MotionProperty::Opacity,
];

fn motion_value(node: &mut CanvasNode, property: MotionProperty) -> &mut f64 {
    match property {
        MotionProperty::X => &mut node.x,
        MotionProperty::Y => &mut node.y,
        MotionProperty::Width => &mut node.width,
        MotionProperty::Height => &mut node.height,
        MotionProperty::RotationDeg => &mut node.rotation_deg,
        MotionProperty::Opacity => &mut node.opacity,
    }
}

fn split_node_motion(
    left: &mut CanvasNode,
    right: &mut CanvasNode,
    cut: RationalTime,
) -> Result<()> {
    if left.keyframes.is_empty() {
        return Ok(());
    }
    right.keyframes.clear();
    let keys = std::mem::take(&mut left.keyframes);
    for property in MOTION_PROPERTIES {
        let mut channel: Vec<_> = keys.iter().filter(|key| key.property == property).collect();
        if channel.is_empty() {
            continue;
        }
        if left.property_locks.contains(&property.node_property()) {
            return Err(DomainError::Locked(format!(
                "node:{} motion property",
                left.id
            )));
        }
        channel.sort_by_key(|key| key.at);
        if channel.windows(2).any(|pair| pair[0].at == pair[1].at) {
            return Err(DomainError::Invalid(format!(
                "“{}” has duplicate motion keys.",
                left.name
            )));
        }
        let middle = channel.partition_point(|key| key.at < cut);
        let hold_value = channel[..middle]
            .last()
            .map_or(*motion_value(left, property), |key| key.value);
        let next = channel.get(middle).copied();
        if let Some(next) = next
            && next.interpolation != MotionInterpolation::Hold
            && next.value != hold_value
        {
            return Err(DomainError::Invalid(format!(
                "The cut crosses a changing motion curve on “{}”. Choose a frame where that property holds.",
                left.name
            )));
        }
        left.keyframes
            .extend(channel[..middle].iter().map(|key| (**key).clone()));

        if next.is_none_or(|key| key.at > cut) {
            right.keyframes.push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property,
                value: hold_value,
                interpolation: MotionInterpolation::Linear,
            });
        }
        for key in &channel[middle..] {
            let mut shifted = (**key).clone();
            shifted.at = shifted
                .at
                .checked_sub(cut)
                .map_err(|_| DomainError::Invalid("Motion time is out of bounds.".into()))?;
            if shifted.at == RationalTime::ZERO {
                // The first pose applies immediately. Its incoming curve has no interval.
                shifted.interpolation = MotionInterpolation::Linear;
            }
            right.keyframes.push(shifted);
        }
    }
    normalize_split_pose(left);
    normalize_split_pose(right);
    Ok(())
}

fn normalize_split_pose(node: &mut CanvasNode) {
    let mut keys = std::mem::take(&mut node.keyframes);
    keys.sort_by_key(|key| (key.at, key.property));
    for group in [&MOTION_PROPERTIES[..5], &MOTION_PROPERTIES[5..]] {
        let mut static_values = Vec::new();
        let mut all_static = true;
        for &property in group {
            let mut channel = keys.iter().filter(|key| key.property == property);
            let Some(first) = channel.next() else {
                continue;
            };
            let value = motion_value(node, property);
            let start = if first.at == RationalTime::ZERO {
                first.value
            } else {
                *value
            };
            let mut last = first;
            let mut constant = start == first.value;
            for key in channel {
                constant &= key.value == start;
                last = key;
            }
            all_static &= constant;
            static_values.push((property, start));
            // At a zero key, the base value is never sampled. Keeping the terminal
            // geometry in the base also preserves the native two-pose contract.
            if property != MotionProperty::Opacity && first.at == RationalTime::ZERO {
                *value = last.value;
            }
        }
        if all_static {
            for (property, value) in static_values {
                *motion_value(node, property) = value;
            }
            keys.retain(|key| !group.contains(&key.property));
        }
    }
    node.keyframes = keys;
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
        if scene.nodes.iter().any(|node| !node.keyframes.is_empty()) {
            for profile in self.deliverables.iter().filter(|profile| {
                profile.included_scene_ids.is_empty()
                    || profile.included_scene_ids.contains(&scene_id)
            }) {
                let fps = profile.frame_rate;
                let numerator = i128::from(left_duration.num) * i128::from(fps.num);
                let denominator = i128::from(left_duration.den) * i128::from(fps.den);
                if denominator <= 0 || numerator % denominator != 0 {
                    return Err(DomainError::Invalid(format!(
                        "Choose a cut on an output frame of “{}” to preserve motion.",
                        profile.name
                    )));
                }
            }
            let relation_targets: BTreeSet<_> = scene
                .nodes
                .iter()
                .flat_map(|node| node.relations.iter().map(|relation| relation.target_id))
                .collect();
            if scene.nodes.iter().any(|node| {
                !node.keyframes.is_empty()
                    && (!node.relations.is_empty() || relation_targets.contains(&node.id))
            }) {
                return Err(DomainError::Invalid(
                    "The split cannot preserve an animated object relation at this cut.".into(),
                ));
            }
        }

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
        for (source, node) in left.nodes.iter_mut().zip(&mut right.nodes) {
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
            split_node_motion(source, node, left_duration)?;
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
