//! MW05-E08-03: original source-bound semantic SFX cue placements.
//!
//! The schedule is exact at 30 frames/s, anchored to authored scene/beat
//! content and sample-addressable at stereo 48 kHz. Every WAV must be
//! independently imported into the existing owner-local content-addressed
//! Asset store. A source digest is an identity, NOT verified rights,
//! measured audio loudness, legal permission, or a completed native mix.
use crate::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const SFX_CUE_SCHEMA: &str = "motionwright.semantic-sfx/1";
pub const SFX_NATIVE_FPS: i128 = 30;
pub const SFX_AUDIO_RATE: i128 = 48_000;
pub const SFX_SAMPLES_PER_FRAME: u64 = 1_600;
pub const MAX_SFX_CUES: usize = 64;
const MAX_SOURCE_FRAMES: u16 = 180;
const MAX_SFX_OVERLAP: usize = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SfxEvent {
    SceneReveal,
    BeatEmphasis,
    UiFocus,
    Confirmation,
    Impact,
    Transition,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SfxAnchor {
    SceneStart,
    BeatStart { beat_id: Uuid },
}

// Serde's internally tagged *unit* variants can ignore extra map keys even
// with deny_unknown_fields. An anchor is source authority, so accept only the
// canonical keys of its exact variant without changing its serialized shape.
impl<'de> Deserialize<'de> for SfxAnchor {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct StrictAnchorVisitor;

        impl<'de> serde::de::Visitor<'de> for StrictAnchorVisitor {
            type Value = SfxAnchor;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a source-bound SFX anchor with exact variant keys")
            }

            fn visit_map<A>(self, mut map: A) -> std::result::Result<Self::Value, A::Error>
            where
                A: serde::de::MapAccess<'de>,
            {
                let mut kind: Option<String> = None;
                let mut beat_id: Option<Uuid> = None;
                while let Some(field) = map.next_key::<String>()? {
                    match field.as_str() {
                        "kind" => {
                            if kind.is_some() {
                                return Err(serde::de::Error::duplicate_field("kind"));
                            }
                            kind = Some(map.next_value()?);
                        }
                        "beat_id" => {
                            if beat_id.is_some() {
                                return Err(serde::de::Error::duplicate_field("beat_id"));
                            }
                            beat_id = Some(map.next_value()?);
                        }
                        _ => {
                            return Err(serde::de::Error::unknown_field(
                                &field,
                                &["kind", "beat_id"],
                            ));
                        }
                    }
                }
                match kind.as_deref() {
                    Some("scene_start") if beat_id.is_none() => Ok(SfxAnchor::SceneStart),
                    Some("scene_start") => {
                        Err(serde::de::Error::unknown_field("beat_id", &["kind"]))
                    }
                    Some("beat_start") => Ok(SfxAnchor::BeatStart {
                        beat_id: beat_id
                            .ok_or_else(|| serde::de::Error::missing_field("beat_id"))?,
                    }),
                    Some(other) => Err(serde::de::Error::unknown_variant(
                        other,
                        &["scene_start", "beat_start"],
                    )),
                    None => Err(serde::de::Error::missing_field("kind")),
                }
            }
        }

        deserializer.deserialize_map(StrictAnchorVisitor)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum SfxTailPolicy {
    #[default]
    WithinScene,
    ContinueAcrossCuts,
}

impl SfxTailPolicy {
    pub fn is_within_scene(&self) -> bool {
        *self == Self::WithinScene
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticSfxCue {
    pub schema: String,
    pub id: Uuid,
    pub scene_id: Uuid,
    pub event: SfxEvent,
    pub anchor: SfxAnchor,
    /// Additional position in exactly 30 fps frames; no beat inference.
    pub offset_frames: i16,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    /// An explicitly authored source cut; the actual file is checked later.
    pub source_frames: u16,
    pub gain_milli_db: i32,
    pub attack_frames: u16,
    pub release_frames: u16,
    /// Opt in to preserving the complete source tail across authored cuts.
    #[serde(default, skip_serializing_if = "SfxTailPolicy::is_within_scene")]
    pub tail_policy: SfxTailPolicy,
    /// A declaration only, not third-party rights or source authentication.
    pub original_source_attestation: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScheduledSfx {
    pub cue_id: Uuid,
    pub absolute_start_frame: u64,
    pub absolute_end_frame: u64,
    pub first_sample: u64,
    pub sample_count: u64,
}

fn exact_frame(time: RationalTime) -> Result<i128> {
    if time.den <= 0 || time.num < 0 {
        return Err(DomainError::Invalid(
            "SFX scene and beat sources need nonnegative rational time".into(),
        ));
    }
    let frames = i128::from(time.num)
        .checked_mul(SFX_NATIVE_FPS)
        .ok_or_else(|| DomainError::Invalid("SFX frame index overflow".into()))?;
    if frames % i128::from(time.den) != 0 {
        return Err(DomainError::Invalid(
            "SFX source time must be representable exactly at 30 fps; no implicit snap".into(),
        ));
    }
    Ok(frames / i128::from(time.den))
}

fn content_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl SemanticSfxCue {
    pub fn validate_in(&self, project: &Project) -> Result<ScheduledSfx> {
        if self.schema != SFX_CUE_SCHEMA
            || self.id.is_nil()
            || self.scene_id.is_nil()
            || !(-15..=30).contains(&self.offset_frames)
            || !(1..=MAX_SOURCE_FRAMES).contains(&self.source_frames)
            || !(-36_000..=0).contains(&self.gain_milli_db)
            || self.attack_frames > 30
            || self.release_frames > 90
            || u32::from(self.attack_frames) + u32::from(self.release_frames)
                >= u32::from(self.source_frames)
            || self.original_source_attestation.trim().is_empty()
            || self.original_source_attestation.chars().count() > 160
            || self
                .original_source_attestation
                .chars()
                .any(char::is_control)
            || !content_sha256(&self.source_sha256)
        {
            return Err(DomainError::Invalid(
                "SFX requires bounded source, timing, envelope, digest and rights declaration"
                    .into(),
            ));
        }
        let asset = project.assets.iter().find(|asset| {
            asset.id == self.source_asset_id
                && matches!(
                    asset.media_type.as_str(),
                    "audio/wav" | "audio/x-wav" | "audio/wave"
                )
                && asset.content_sha256.as_deref() == Some(self.source_sha256.as_str())
        });
        if asset.is_none() {
            return Err(DomainError::Invalid(
                "SFX must reference an imported immutable WAV source in this project".into(),
            ));
        }
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == self.scene_id)
            .ok_or_else(|| DomainError::Invalid("SFX references missing scene".into()))?;
        let relative_frame = match self.anchor {
            SfxAnchor::SceneStart => 0_i128,
            SfxAnchor::BeatStart { beat_id } => {
                let beat = scene
                    .beats
                    .iter()
                    .find(|beat| beat.id == beat_id)
                    .ok_or_else(|| {
                        DomainError::Invalid("SFX beat is absent from its scene".into())
                    })?;
                exact_frame(beat.start)?
            }
        };
        let scene_frames = exact_frame(scene.duration)?;
        let at = relative_frame + i128::from(self.offset_frames);
        if at < 0 || at >= scene_frames {
            return Err(DomainError::Invalid(
                "SFX onset must stay inside its half-open authored scene interval".into(),
            ));
        }
        if self.tail_policy == SfxTailPolicy::WithinScene
            && at + i128::from(self.source_frames) > scene_frames
        {
            return Err(DomainError::Invalid(
                "SFX authored cue and release tail must stay inside the scene interval".into(),
            ));
        }
        let start = exact_frame(scene.start)?
            .checked_add(at)
            .ok_or_else(|| DomainError::Invalid("SFX schedule frame overflow".into()))?;
        let start: u64 = start
            .try_into()
            .map_err(|_| DomainError::Invalid("SFX start frame out of range".into()))?;
        let end = start
            .checked_add(u64::from(self.source_frames))
            .ok_or_else(|| DomainError::Invalid("SFX duration frame overflow".into()))?;
        if self.tail_policy == SfxTailPolicy::ContinueAcrossCuts {
            let cut_frames = exact_cut_frames(project.scenes.iter())?;
            if end > cut_frames {
                return Err(DomainError::Invalid(
                    "SFX tail exceeds the complete project cut; no implicit trim".into(),
                ));
            }
        }
        if end > 36_000 {
            return Err(DomainError::Invalid(
                "SFX native film exceeds bounded 20-minute limit".into(),
            ));
        }
        let first_sample = start
            .checked_mul(SFX_SAMPLES_PER_FRAME)
            .ok_or_else(|| DomainError::Invalid("SFX sample offset overflow".into()))?;
        let sample_count = u64::from(self.source_frames)
            .checked_mul(SFX_SAMPLES_PER_FRAME)
            .ok_or_else(|| DomainError::Invalid("SFX source sample count overflow".into()))?;
        Ok(ScheduledSfx {
            cue_id: self.id,
            absolute_start_frame: start,
            absolute_end_frame: end,
            first_sample,
            sample_count,
        })
    }
}

fn exact_cut_frames<'a>(scenes: impl IntoIterator<Item = &'a Scene>) -> Result<u64> {
    let mut total = 0_u64;
    for scene in scenes {
        let frames = u64::try_from(exact_frame(scene.duration)?)
            .map_err(|_| DomainError::Invalid("SFX cut duration out of range".into()))?;
        total = total
            .checked_add(frames)
            .ok_or_else(|| DomainError::Invalid("SFX cut frame overflow".into()))?;
    }
    Ok(total)
}

fn checked_schedule(mut schedule: Vec<ScheduledSfx>) -> Result<Vec<ScheduledSfx>> {
    schedule.sort_by_key(|record| (record.absolute_start_frame, record.cue_id));
    let mut ending: Vec<u64> = Vec::new();
    for item in &schedule {
        ending.retain(|end| *end > item.absolute_start_frame);
        if ending.len() >= MAX_SFX_OVERLAP {
            return Err(DomainError::Invalid(
                "More than four simultaneous SFX sources require an explicit new mix profile"
                    .into(),
            ));
        }
        ending.push(item.absolute_end_frame);
    }
    Ok(schedule)
}

impl Project {
    fn source_sfx_schedule(&self) -> Result<Vec<ScheduledSfx>> {
        if self.audio.sfx_cues.len() > MAX_SFX_CUES {
            return Err(DomainError::Invalid("SFX cue collection exceeds 64".into()));
        }
        let mut ids = BTreeSet::new();
        let mut schedule = Vec::with_capacity(self.audio.sfx_cues.len());
        for cue in &self.audio.sfx_cues {
            if !ids.insert(cue.id) {
                return Err(DomainError::Invalid("Duplicate SFX cue identity".into()));
            }
            schedule.push(cue.validate_in(self)?);
        }
        checked_schedule(schedule)
    }

    fn sfx_schedule_in_profile(
        &self,
        profile: &DeliverableProfile,
        source: &[ScheduledSfx],
    ) -> Result<Vec<ScheduledSfx>> {
        if profile.included_scene_ids.is_empty() {
            return Ok(source.to_vec());
        }
        let selected = crate::delivery::scenes_for_profile(&self.scenes, profile)?;
        if !self
            .audio
            .sfx_cues
            .iter()
            .any(|cue| selected.iter().any(|scene| scene.id == cue.scene_id))
        {
            return Ok(Vec::new());
        }
        let total = exact_cut_frames(selected.iter().copied())?;
        let mut cursor = 0_u64;
        let mut schedule = Vec::new();
        for scene in selected {
            let original_start = u64::try_from(exact_frame(scene.start)?)
                .map_err(|_| DomainError::Invalid("SFX scene start out of range".into()))?;
            for cue in self
                .audio
                .sfx_cues
                .iter()
                .filter(|cue| cue.scene_id == scene.id)
            {
                let placement = source
                    .iter()
                    .find(|placement| placement.cue_id == cue.id)
                    .ok_or_else(|| DomainError::Invalid("SFX source placement is absent".into()))?;
                let relative = placement
                    .absolute_start_frame
                    .checked_sub(original_start)
                    .ok_or_else(|| {
                        DomainError::Invalid("SFX onset precedes its source scene".into())
                    })?;
                let start = cursor
                    .checked_add(relative)
                    .ok_or_else(|| DomainError::Invalid("SFX cut onset overflow".into()))?;
                let end = start
                    .checked_add(u64::from(cue.source_frames))
                    .ok_or_else(|| DomainError::Invalid("SFX cut tail overflow".into()))?;
                if end > total || end > 36_000 {
                    return Err(DomainError::Invalid(
                        "SFX tail exceeds the selected delivery cut; no implicit trim".into(),
                    ));
                }
                schedule.push(ScheduledSfx {
                    cue_id: cue.id,
                    absolute_start_frame: start,
                    absolute_end_frame: end,
                    first_sample: start.checked_mul(SFX_SAMPLES_PER_FRAME).ok_or_else(|| {
                        DomainError::Invalid("SFX cut sample offset overflow".into())
                    })?,
                    sample_count: placement.sample_count,
                });
            }
            cursor =
                cursor
                    .checked_add(u64::try_from(exact_frame(scene.duration)?).map_err(|_| {
                        DomainError::Invalid("SFX scene duration out of range".into())
                    })?)
                    .ok_or_else(|| DomainError::Invalid("SFX cut cursor overflow".into()))?;
        }
        checked_schedule(schedule)
    }

    pub fn validated_sfx_schedule(&self) -> Result<Vec<ScheduledSfx>> {
        let schedule = self.source_sfx_schedule()?;
        // Legacy scene-local projects keep their domain admission. The new
        // opt-in tail must fit every authored cut that includes its scene.
        // A non-30-fps output profile can still be stored; native admission
        // decides whether its format can be realized without conversion.
        if self
            .audio
            .sfx_cues
            .iter()
            .any(|cue| cue.tail_policy == SfxTailPolicy::ContinueAcrossCuts)
        {
            for profile in &self.deliverables {
                self.sfx_schedule_in_profile(profile, &schedule)?;
            }
        }
        Ok(schedule)
    }

    pub fn validated_sfx_schedule_for_profile(
        &self,
        profile_id: Uuid,
    ) -> Result<Vec<ScheduledSfx>> {
        let profile = self
            .deliverables
            .iter()
            .find(|profile| profile.id == profile_id)
            .ok_or_else(|| DomainError::NotFound(format!("deliverable:{profile_id}")))?;
        self.sfx_schedule_in_profile(profile, &self.source_sfx_schedule()?)
    }

    /// Exact duration of the existing authored cut, before any native work.
    pub fn sfx_cut_frames(&self, profile_id: Uuid) -> Result<u64> {
        let profile = self
            .deliverables
            .iter()
            .find(|profile| profile.id == profile_id)
            .ok_or_else(|| DomainError::NotFound(format!("deliverable:{profile_id}")))?;
        exact_cut_frames(crate::delivery::scenes_for_profile(&self.scenes, profile)?)
    }

    pub(crate) fn upsert_semantic_sfx(&mut self, cue: &SemanticSfxCue) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        self.ensure_unlocked(
            &format!("scene:{}", cue.scene_id),
            &[LockKind::Content, LockKind::Timing],
        )?;
        let mut next = self.clone();
        if let Some(existing) = next
            .audio
            .sfx_cues
            .iter_mut()
            .find(|item| item.id == cue.id)
        {
            if existing.scene_id != cue.scene_id {
                return Err(DomainError::Invalid(
                    "SFX identity cannot move scenes by replacement".into(),
                ));
            }
            *existing = cue.clone();
        } else {
            if next.audio.sfx_cues.len() >= MAX_SFX_CUES {
                return Err(DomainError::Invalid(
                    "SFX cue collection exceeds budget".into(),
                ));
            }
            next.audio.sfx_cues.push(cue.clone());
        }
        next.validated_sfx_schedule()?;
        if let Some(scene) = next
            .scenes
            .iter_mut()
            .find(|scene| scene.id == cue.scene_id)
        {
            scene.status = SceneStatus::Draft;
        }
        *self = next;
        Ok(())
    }
    pub(crate) fn remove_semantic_sfx(&mut self, cue_id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        let old = self
            .audio
            .sfx_cues
            .iter()
            .find(|cue| cue.id == cue_id)
            .ok_or_else(|| DomainError::NotFound(format!("semantic-sfx:{cue_id}")))?;
        self.ensure_unlocked(
            &format!("scene:{}", old.scene_id),
            &[LockKind::Content, LockKind::Timing],
        )?;
        let scene_id = old.scene_id;
        self.audio.sfx_cues.retain(|cue| cue.id != cue_id);
        if let Some(scene) = self.scenes.iter_mut().find(|scene| scene.id == scene_id) {
            scene.status = SceneStatus::Draft;
        }
        Ok(())
    }
}
