//! One conditional, bounded edit of a scene's authored beat timeline.
//! Uses the existing Project Change journal, CAS, locks and final dependency
//! validation. The inverse is another conditional Change, not snapshot rollback.
use crate::{Beat, DomainError, LockKind, NarrativeEditSource, Project, RationalTime, Result};
use uuid::Uuid;

impl Project {
    pub(crate) fn edit_scene_beat_timeline(
        &mut self,
        expected: &NarrativeEditSource,
        scene_id: Uuid,
        expected_duration: RationalTime,
        expected_beats: &[Beat],
        beats: &[Beat],
        rationale: &str,
    ) -> Result<()> {
        if expected.project_id != self.id
            || expected.generation != self.generation
            || expected.branch_id != self.active_branch
            || expected.base_revision != self.revision
        {
            return Err(DomainError::Invalid(
                "scene beat timeline source is stale or foreign".into(),
            ));
        }
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        self.ensure_unlocked(
            &format!("scene:{scene_id}"),
            &[LockKind::Content, LockKind::Timing],
        )?;
        if rationale.trim().is_empty()
            || rationale.len() > 4000
            || expected_beats.len() > 32
            || beats.len() > 32
        {
            return Err(DomainError::Invalid(
                "scene beat timeline requires a reason and at most 32 beats".into(),
            ));
        }
        let index = self
            .scenes
            .iter()
            .position(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        let original = &self.scenes[index];
        if original.duration != expected_duration || original.beats != expected_beats {
            return Err(DomainError::Invalid(
                "scene beat timeline no longer matches its conditional source".into(),
            ));
        }
        if original.beats == beats {
            return Err(DomainError::Invalid(
                "scene beat timeline already matches the proposal".into(),
            ));
        }
        for beat in beats {
            if self.narrative.beats.iter().any(|item| item.id == beat.id)
                || self
                    .scenes
                    .iter()
                    .filter(|scene| scene.id != scene_id)
                    .any(|scene| scene.beats.iter().any(|item| item.id == beat.id))
            {
                return Err(DomainError::Invalid(
                    "beat identity already belongs to another project resource".into(),
                ));
            }
        }
        let mut candidate = original.clone();
        candidate.beats = beats.to_vec();
        candidate.status = crate::SceneStatus::Draft;
        candidate.validate()?;
        let mut end = RationalTime::ZERO;
        for beat in &candidate.beats {
            if beat.start < end {
                return Err(DomainError::Invalid(
                    "scene beat timeline intervals overlap".into(),
                ));
            }
            end = beat
                .start
                .checked_add(beat.duration)
                .map_err(|error| DomainError::Invalid(format!("beat time overflow: {error}")))?;
        }
        // The outer Project transaction validates graph, audio anchors,
        // editorial holds, branches and every other dependency before publish.
        self.scenes[index] = candidate;
        Ok(())
    }
}
