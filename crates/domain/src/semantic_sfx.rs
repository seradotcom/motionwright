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
        if at < 0 || at + i128::from(self.source_frames) > scene_frames {
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

impl Project {
    pub fn validated_sfx_schedule(&self) -> Result<Vec<ScheduledSfx>> {
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
