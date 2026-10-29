//! Atomic multi-scene edit against an *owner-marked* music clock.
//!
//! This is editorial timing: no automatic beat detection, source playback,
//! waveform listening, audio resampling, or native renderer dispatch.
//! Every selected boundary, downstream scene and absolute anchor is reviewed
//! again by the authoritative domain before a single StudioService CAS write.
use crate::*;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

pub const MAX_MUSIC_CONFORM_TARGETS: usize = 16;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MusicConformTarget {
    pub scene_id: Uuid,
    pub beat_index: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MusicConformSceneChange {
    pub scene_id: Uuid,
    pub old_start: RationalTime,
    pub new_start: RationalTime,
    pub old_duration: RationalTime,
    pub new_duration: RationalTime,
    pub beat_index: Option<usize>,
    pub changed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MusicConformReview {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub base_revision: u64,
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub targets: Vec<MusicConformTarget>,
    pub scenes: Vec<MusicConformSceneChange>,
    pub changed_scene_count: usize,
    pub original_duration: RationalTime,
    pub conformed_duration: RationalTime,
    pub affected_cue_count: usize,
    pub affected_sfx_cue_count: usize,
    pub affected_marker_count: usize,
    pub affected_transcript_count: usize,
    pub approval_invalidated: bool,
}

impl MusicConformReview {
    pub fn anchors_need_acknowledgement(&self) -> bool {
        self.affected_cue_count > 0
            || self.affected_sfx_cue_count > 0
            || self.affected_marker_count > 0
            || self.affected_transcript_count > 0
    }
}

impl Project {
    pub fn inspect_music_conform(
        &self,
        targets: &[MusicConformTarget],
        expected_source_sha256: &str,
    ) -> Result<MusicConformReview> {
        if targets.len() < 2 || targets.len() > MAX_MUSIC_CONFORM_TARGETS {
            return Err(DomainError::Invalid(
                "multi-scene music conform requires 2–16 explicit boundaries".into(),
            ));
        }
        if expected_source_sha256.len() != 64
            || !expected_source_sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(DomainError::Invalid(
                "multi-scene conform requires a lowercase content SHA-256".into(),
            ));
        }
        let Some(plan) = self.production_design.plan.as_ref() else {
            return Err(DomainError::Invalid(
                "save the production music plan first".into(),
            ));
        };
        let ProductionClock::Music { asset_id, beats } = &plan.clock else {
            return Err(DomainError::Invalid(
                "saved plan is not an author-marked music clock".into(),
            ));
        };
        let source = self
            .assets
            .iter()
            .find(|asset| asset.id == *asset_id && asset.media_type.starts_with("audio/"))
            .and_then(|asset| asset.content_sha256.as_deref());
        if source != Some(expected_source_sha256) {
            return Err(DomainError::Invalid(
                "owner audio source identity has changed; reconfirm the grid".into(),
            ));
        }
        let indices: HashMap<Uuid, usize> = self
            .scenes
            .iter()
            .enumerate()
            .map(|(index, scene)| (scene.id, index))
            .collect();
        let mut seen = HashSet::new();
        let mut previous_index: Option<usize> = None;
        let mut selected = HashMap::new();
        for target in targets {
            let index = *indices
                .get(&target.scene_id)
                .ok_or_else(|| DomainError::NotFound(format!("scene:{}", target.scene_id)))?;
            if !seen.insert(target.scene_id)
                || previous_index.is_some_and(|previous| index <= previous)
            {
                return Err(DomainError::Invalid(
                    "music conform targets must be unique in the original scene order".into(),
                ));
            }
            let Some(&beat) = beats.get(target.beat_index) else {
                return Err(DomainError::Invalid(
                    "selected music beat is out of bounds".into(),
                ));
            };
            if beat < RationalTime::ZERO || beat > RationalTime::new(86_400, 1).unwrap() {
                return Err(DomainError::Invalid(
                    "selected music beat exceeds clock bounds".into(),
                ));
            }
            previous_index = Some(index);
            selected.insert(target.scene_id, (target.beat_index, beat));
        }

        let mut cursor = RationalTime::ZERO;
        let mut source_end = RationalTime::ZERO;
        let mut changes = Vec::with_capacity(self.scenes.len());
        let mut earliest_cut_change: Option<RationalTime> = None;
        for scene in &self.scenes {
            let original_end = scene.start.checked_add(scene.duration).map_err(|_| {
                DomainError::Invalid("source scene end overflows rational clock".into())
            })?;
            source_end = original_end;
            let chosen = selected.get(&scene.id);
            let duration = if let Some((_, beat)) = chosen {
                beat.checked_sub(cursor).map_err(|_| {
                    DomainError::Invalid("music grid boundary overflows rational clock".into())
                })?
            } else {
                scene.duration
            };
            if duration <= RationalTime::ZERO || duration > RationalTime::new(86_400, 1).unwrap() {
                return Err(DomainError::Invalid(
                    "each conformed scene must have a positive duration no longer than one day"
                        .into(),
                ));
            }
            if chosen.is_some() {
                if scene
                    .nodes
                    .iter()
                    .flat_map(|node| &node.keyframes)
                    .any(|keyframe| keyframe.at >= duration)
                {
                    return Err(DomainError::Invalid(
                        "musical conform would strand a scene-local canvas keyframe".into(),
                    ));
                }
                if scene.beats.iter().any(|beat| {
                    beat.start
                        .checked_add(beat.duration)
                        .map_or(true, |end| end > duration)
                }) {
                    return Err(DomainError::Invalid(
                        "musical conform would strand an authored scene beat".into(),
                    ));
                }
            }
            let new_end = cursor
                .checked_add(duration)
                .map_err(|_| DomainError::Invalid("multi-scene music clock overflows".into()))?;
            if let Some((_, target_beat)) = chosen {
                if new_end != *target_beat {
                    return Err(DomainError::Invalid(
                        "conformed scene end is not the selected beat".into(),
                    ));
                }
                if new_end != original_end {
                    let first = if new_end < original_end {
                        new_end
                    } else {
                        original_end
                    };
                    earliest_cut_change = Some(earliest_cut_change.map_or(first, |current| {
                        if first < current { first } else { current }
                    }));
                }
            }
            let changed = cursor != scene.start || duration != scene.duration;
            changes.push(MusicConformSceneChange {
                scene_id: scene.id,
                old_start: scene.start,
                new_start: cursor,
                old_duration: scene.duration,
                new_duration: duration,
                beat_index: chosen.map(|(index, _)| *index),
                changed,
            });
            cursor = new_end;
        }
        let changed_count = changes.iter().filter(|item| item.changed).count();
        if changed_count == 0 {
            return Err(DomainError::Invalid(
                "all selected music cuts already match".into(),
            ));
        }
        if self.locks.iter().any(|lock| {
            lock.kind == LockKind::Timing
                && (lock.resource == self.resource_key()
                    || changes.iter().any(|change| {
                        change.changed && lock.resource == format!("scene:{}", change.scene_id)
                    }))
        }) {
            return Err(DomainError::Locked(
                "multi-scene music conform intersects a protected project or scene".into(),
            ));
        }
        if self.deliverables.iter().any(|profile| {
            profile.timing_locked
                && (profile.included_scene_ids.is_empty()
                    || changes.iter().any(|change| {
                        change.changed && profile.included_scene_ids.contains(&change.scene_id)
                    }))
        }) {
            return Err(DomainError::Locked(
                "multi-scene music conform intersects a protected delivery profile".into(),
            ));
        }
        let threshold = earliest_cut_change
            .ok_or_else(|| DomainError::Invalid("no selected music boundary changes".into()))?;
        let affected_sfx_cue_count = self
            .audio
            .sfx_cues
            .iter()
            .filter(|cue| {
                changes
                    .iter()
                    .any(|change| change.changed && change.scene_id == cue.scene_id)
            })
            .count();
        Ok(MusicConformReview {
            project_id: self.id,
            generation: self.generation,
            base_revision: self.revision,
            asset_id: *asset_id,
            source_sha256: expected_source_sha256.to_owned(),
            targets: targets.to_vec(),
            scenes: changes,
            changed_scene_count: changed_count,
            original_duration: source_end,
            conformed_duration: cursor,
            affected_cue_count: self
                .audio
                .cues
                .iter()
                .filter(|cue| cue.at >= threshold)
                .count(),
            affected_sfx_cue_count,
            affected_marker_count: self
                .markers
                .iter()
                .filter(|marker| marker.at >= threshold)
                .count(),
            affected_transcript_count: self
                .audio
                .transcript
                .iter()
                .filter(|segment| segment.end > threshold)
                .count(),
            approval_invalidated: plan.approval.is_some(),
        })
    }

    pub(crate) fn conform_scenes_to_music(
        &mut self,
        targets: &[MusicConformTarget],
        source_sha256: &str,
        base_revision: u64,
        acknowledge_absolute_anchors: bool,
    ) -> Result<()> {
        if base_revision != self.revision {
            return Err(DomainError::Invalid(
                "musical conform proposal stale; refresh the source revision".into(),
            ));
        }
        let review = self.inspect_music_conform(targets, source_sha256)?;
        if review.anchors_need_acknowledgement() && !acknowledge_absolute_anchors {
            return Err(DomainError::Invalid(
                "music conform affects fixed timeline anchors or scene-linked SFX; acknowledge retained absolute anchors and any SFX retiming"
                    .into(),
            ));
        }

        // Stage and validate the ENTIRE edit before publishing it, even for
        // direct in-memory callers. A failed edit must leave project bytes
        // untouched; the service CAS then commits exactly one revision.
        let mut staged = self.clone();
        for (scene, mapped) in staged.scenes.iter_mut().zip(&review.scenes) {
            debug_assert_eq!(scene.id, mapped.scene_id);
            scene.start = mapped.new_start;
            scene.duration = mapped.new_duration;
            if mapped.changed {
                scene.status = SceneStatus::Draft;
            }
        }
        if let Some(plan) = staged.production_design.plan.as_mut() {
            plan.approval = None;
        }
        staged.validate()?;
        *self = staged;
        Ok(())
    }
}
