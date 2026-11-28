//! Atomic authority for scene timing edits that can reflow later scenes.
use crate::{
    Change, DomainError, LockKind, Project, RationalTime, RendererKind, Result, SceneStatus,
};
use std::collections::BTreeMap;
use uuid::Uuid;

impl Project {
    pub(crate) fn apply_general_scene_timing(&mut self, change: &Change) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
        let mut candidate = self.clone();
        // Existing target content/timing locks, containment, rational overflow,
        // and all final project validation remain authoritative.
        candidate.apply_change_in_place(change)?;
        let proposed: BTreeMap<_, _> = candidate
            .scenes
            .iter()
            .map(|scene| (scene.id, scene))
            .collect();
        for original in &self.scenes {
            let changed = proposed.get(&original.id).ok_or_else(|| {
                DomainError::Invalid("general timing cannot replace a scene identity".into())
            })?;
            if changed.start != original.start || changed.duration != original.duration {
                self.ensure_unlocked(&format!("scene:{}", original.id), &[LockKind::Timing])?;
                if matches!(change, Change::TrimSceneEnd { .. }) {
                    if original.status == SceneStatus::Approved {
                        return Err(DomainError::Invalid(
                            "Scene trim would change approved scene timing.".into(),
                        ));
                    }
                    if self.deliverables.iter().any(|profile| {
                        profile.protected_scene_ids.contains(&original.id)
                            || (profile.timing_locked
                                && (profile.included_scene_ids.is_empty()
                                    || profile.included_scene_ids.contains(&original.id)))
                    }) {
                        return Err(DomainError::Invalid(
                            "Scene trim would change protected delivery timing.".into(),
                        ));
                    }
                }
            }
        }
        *self = candidate;
        Ok(())
    }

    pub(crate) fn set_scene_duration(
        &mut self,
        scene_id: Uuid,
        duration: RationalTime,
    ) -> Result<()> {
        let resource = format!("scene:{scene_id}");
        self.ensure_unlocked(&resource, &[LockKind::Timing])?;
        if duration.num <= 0 || duration > crate::whole_seconds(86_400) {
            return Err(DomainError::Invalid(
                "scene duration is out of bounds".into(),
            ));
        }
        let scene = self
            .scenes
            .iter_mut()
            .find(|scene| scene.id == scene_id)
            .ok_or(DomainError::NotFound(resource))?;
        if scene
            .nodes
            .iter()
            .flat_map(|node| &node.keyframes)
            .any(|keyframe| keyframe.at >= duration)
        {
            return Err(DomainError::Invalid(
                "scene duration would strand an authored keyframe".into(),
            ));
        }
        if scene.beats.iter().any(|beat| {
            beat.start
                .checked_add(beat.duration)
                .map_or(true, |end| end > duration)
        }) {
            return Err(DomainError::Invalid(
                "scene duration would strand an authored beat".into(),
            ));
        }
        scene.duration = duration;
        Ok(())
    }

    pub(crate) fn trim_scene_end(&mut self, scene_id: Uuid, at: RationalTime) -> Result<()> {
        let timing_error = |_| DomainError::Invalid("Scene trim time is out of bounds.".into());
        at.validate().map_err(timing_error)?;
        let index = self
            .scenes
            .iter()
            .position(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        let scene = &self.scenes[index];
        if scene.renderer != RendererKind::MotionCanvas
            || self
                .production_design
                .capsules
                .iter()
                .any(|item| item.scene_id == scene_id)
        {
            return Err(DomainError::Invalid(
                "Trimming requires an editable scene without a bound capsule.".into(),
            ));
        }
        let duration = at.checked_sub(scene.start).map_err(timing_error)?;
        if duration.num <= 0 || duration >= scene.duration {
            return Err(DomainError::Invalid(
                "Position the playhead inside the selected scene to trim its end.".into(),
            ));
        }
        if self
            .deliverables
            .iter()
            .filter(|profile| {
                profile.included_scene_ids.is_empty()
                    || profile.included_scene_ids.contains(&scene_id)
            })
            .any(|profile| {
                let frames_num = i128::from(duration.num) * i128::from(profile.frame_rate.num);
                let frames_den = i128::from(duration.den) * i128::from(profile.frame_rate.den);
                frames_den <= 0 || frames_num % frames_den != 0
            })
        {
            return Err(DomainError::Invalid(
                "The trimmed duration must align to the scene's delivery frame grids.".into(),
            ));
        }
        let removed = scene.duration.checked_sub(duration).map_err(timing_error)?;
        self.set_scene_duration(scene_id, duration)?;
        for following in &mut self.scenes[index + 1..] {
            following.start = following.start.checked_sub(removed).map_err(timing_error)?;
        }
        Ok(())
    }
}
