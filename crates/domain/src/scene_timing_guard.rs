//! Atomic authority for the two general scene timing operations.
//! A selected scene is not the only affected resource: reflow can move later
//! scenes. Compare the complete proposed timing before publishing the clone.
use crate::{Change, DomainError, LockKind, Project, Result};
use std::collections::BTreeMap;

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
            }
        }
        *self = candidate;
        Ok(())
    }
}
