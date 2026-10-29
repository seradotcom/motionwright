//! A source-locked, revision-bound musical cut. The beats are **author-marked**
//! timestamps on an imported audio CAS asset, not automatically measured onset
//! evidence. Native render, soundtrack resampling and shifting absolute audio
//! anchors are deliberately *not* inferred from this editorial operation.
use crate::*;
use serde::{Deserialize, Serialize};

fn sha256_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}

/// Read-only preflight. The same Rust review is repeated within mutation,
/// never relying on the WebView's earlier preview or client-made timestamps.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MusicCutReview {
    pub scene_id: Uuid,
    pub beat_index: usize,
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub original_end: RationalTime,
    pub target_end: RationalTime,
    pub old_duration: RationalTime,
    pub new_duration: RationalTime,
    pub ripple: RationalTime,
    pub downstream_scene_ids: Vec<Uuid>,
    pub affected_cue_count: usize,
    pub affected_sfx_cue_count: usize,
    pub affected_marker_count: usize,
    pub affected_transcript_count: usize,
    pub approval_invalidated: bool,
}

impl MusicCutReview {
    pub fn anchors_need_acknowledgement(&self) -> bool {
        self.affected_cue_count > 0
            || self.affected_sfx_cue_count > 0
            || self.affected_marker_count > 0
            || self.affected_transcript_count > 0
    }
}

impl Project {
    pub fn inspect_music_cut(
        &self,
        scene_id: Uuid,
        beat_index: usize,
        expected_source_sha256: &str,
    ) -> Result<MusicCutReview> {
        if !sha256_digest(expected_source_sha256) {
            return Err(DomainError::Invalid(
                "music cut requires a lowercase owner asset digest".into(),
            ));
        }
        let Some(plan) = self.production_design.plan.as_ref() else {
            return Err(DomainError::Invalid(
                "music cut requires a saved production plan".into(),
            ));
        };
        let ProductionClock::Music { asset_id, beats } = &plan.clock else {
            return Err(DomainError::Invalid(
                "music cut requires a saved music clock".into(),
            ));
        };
        let source = self
            .assets
            .iter()
            .find(|asset| asset.id == *asset_id)
            .filter(|asset| asset.media_type.starts_with("audio/"))
            .and_then(|asset| asset.content_sha256.as_deref());
        if source != Some(expected_source_sha256) {
            return Err(DomainError::Invalid(
                "music source bytes changed after the beat grid was reviewed".into(),
            ));
        }
        let Some(&target_end) = beats.get(beat_index) else {
            return Err(DomainError::Invalid(
                "music beat index is out of range".into(),
            ));
        };
        let Some(scene_index) = self.scenes.iter().position(|scene| scene.id == scene_id) else {
            return Err(DomainError::NotFound(format!("scene:{scene_id}")));
        };
        let scene = &self.scenes[scene_index];
        let old_end = scene
            .start
            .checked_add(scene.duration)
            .map_err(|_| DomainError::Invalid("scene end exceeds rational clock".into()))?;
        let new_duration = target_end
            .checked_sub(scene.start)
            .map_err(|_| DomainError::Invalid("music cut exceeds rational clock".into()))?;
        if new_duration <= RationalTime::ZERO || new_duration > whole_seconds(86_400) {
            return Err(DomainError::Invalid(
                "music beat must produce a positive scene duration <= 86400s".into(),
            ));
        }
        let ripple = new_duration
            .checked_sub(scene.duration)
            .map_err(|_| DomainError::Invalid("music cut ripple exceeds rational clock".into()))?;
        if ripple == RationalTime::ZERO {
            return Err(DomainError::Invalid(
                "music beat does not change the scene cut".into(),
            ));
        }

        // A cut ripples *all subsequent* scene start times. Honor the locks
        // of every object affected, not just the scene whose length changes.
        if self.locks.iter().any(|lock| {
            (lock.resource == self.resource_key()
                || self.scenes[scene_index..]
                    .iter()
                    .any(|affected| lock.resource == format!("scene:{}", affected.id)))
                && lock.kind == LockKind::Timing
        }) {
            return Err(DomainError::Locked(
                "musical cut intersects a protected timing scope".into(),
            ));
        }
        if self.deliverables.iter().any(|profile| {
            profile.timing_locked
                && (profile.included_scene_ids.is_empty()
                    || self.scenes[scene_index..]
                        .iter()
                        .any(|affected| profile.included_scene_ids.contains(&affected.id)))
        }) {
            return Err(DomainError::Locked(
                "musical cut intersects a timing-locked delivery profile".into(),
            ));
        }
        if scene
            .nodes
            .iter()
            .flat_map(|node| &node.keyframes)
            .any(|keyframe| keyframe.at >= new_duration)
        {
            return Err(DomainError::Invalid(
                "music cut would strand an authored canvas keyframe".into(),
            ));
        }
        if scene.beats.iter().any(|beat| {
            beat.start
                .checked_add(beat.duration)
                .map_or(true, |end| end > new_duration)
        }) {
            return Err(DomainError::Invalid(
                "music cut would strand an authored scene beat".into(),
            ));
        }
        // Only global absolute-time anchors are affected. Do not silently
        // move them when this single scene cut is edited. Even a post-cut
        // event that stays numerically unchanged can now belong to a
        // different scene. Require human acknowledgement before applying.
        let first_changed_at = if target_end < old_end {
            target_end
        } else {
            old_end
        };
        Ok(MusicCutReview {
            scene_id,
            beat_index,
            asset_id: *asset_id,
            source_sha256: expected_source_sha256.to_owned(),
            original_end: old_end,
            target_end,
            old_duration: scene.duration,
            new_duration,
            ripple,
            downstream_scene_ids: self.scenes[scene_index + 1..]
                .iter()
                .map(|item| item.id)
                .collect(),
            affected_cue_count: self
                .audio
                .cues
                .iter()
                .filter(|cue| cue.at >= first_changed_at)
                .count(),
            affected_sfx_cue_count: self
                .audio
                .sfx_cues
                .iter()
                .filter(|cue| {
                    self.scenes[scene_index..]
                        .iter()
                        .any(|scene| scene.id == cue.scene_id)
                })
                .count(),
            affected_marker_count: self
                .markers
                .iter()
                .filter(|marker| marker.at >= first_changed_at)
                .count(),
            affected_transcript_count: self
                .audio
                .transcript
                .iter()
                .filter(|segment| segment.end > first_changed_at)
                .count(),
            approval_invalidated: plan.approval.is_some(),
        })
    }

    pub(crate) fn snap_scene_cut_to_music(
        &mut self,
        scene_id: Uuid,
        beat_index: usize,
        source_sha256: &str,
        base_revision: u64,
        acknowledge_absolute_anchors: bool,
    ) -> Result<()> {
        if self.revision != base_revision {
            return Err(DomainError::Invalid(
                "music cut proposal is stale; refresh the observed revision".into(),
            ));
        }
        let reviewed = self.inspect_music_cut(scene_id, beat_index, source_sha256)?;
        if reviewed.anchors_need_acknowledgement() && !acknowledge_absolute_anchors {
            return Err(DomainError::Invalid("music cut affects fixed timeline anchors or scene-linked SFX; acknowledge retained absolute anchors and any SFX retiming".into()));
        }
        let scene = self
            .scenes
            .iter_mut()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        scene.duration = reviewed.new_duration;
        scene.status = SceneStatus::Draft;
        self.reflow_scene_starts()?;
        // An approval digest of a concept is not approval for re-timed shots.
        if let Some(plan) = self.production_design.plan.as_mut() {
            plan.approval = None;
        }
        Ok(())
    }
}
