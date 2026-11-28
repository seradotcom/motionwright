//! Authored delivery intent. Render plans, source measurements and effect
//! authority are derived from the current project, never stored in a recipe.
use crate::{Asset, DeliverableProfile, DomainError, ProductionDesign, Result, Scene};
use serde::{Deserialize, Deserializer, Serialize, de};
use std::collections::{BTreeMap, HashSet};
use std::fmt;
use uuid::Uuid;

pub const MAX_DELIVERY_SCENE_INTENTS: usize = 4_096;
/// Existing full-cut PCM decode budget; authoring cannot expand it.
pub const MAX_MUSIC_SOURCE_SAMPLE_FRAMES: u64 = 48_000 * 180;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum DeliveryRecipeSchema {
    #[default]
    #[serde(rename = "motionwright.delivery-recipe/1")]
    V1,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct DeliveryRecipe {
    pub schema: DeliveryRecipeSchema,
    pub scene_intents: Vec<DeliverySceneIntent>,
    pub original_mix: Option<OriginalMixRecipe>,
    /// Optional `motionwright.delivery-audio-targets/1` extension to recipe/1.
    /// When present this requires a reader implementing that exact contract;
    /// earlier deny-unknown-fields readers reject it. Rollback must preserve
    /// the original project/targets/CAS, never strip intent to force a read.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_optional_audio_targets"
    )]
    pub audio_targets: Option<DeliveryAudioTargets>,
    /// Explicit original narration montage when no multi-bus mix is enabled.
    /// Absent retains the original whole-WAV route and legacy wire bytes.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_voice_cut_sha256"
    )]
    pub voice_only_cut_sha256: Option<String>,
    /// An explicit dissolve on the effective cut of this profile.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_mlt_dissolve"
    )]
    pub mlt_dissolve: Option<MltDissolveIntent>,
    /// Explicit opt-in. An absent policy preserves the historical Film source and receipt identity.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_motion_canvas_audio_cues"
    )]
    pub motion_canvas_audio_cues: Option<MotionCanvasAudioCuePolicy>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionCanvasAudioCuePolicy {
    PreserveAuthoredTime,
}

fn deserialize_motion_canvas_audio_cues<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<MotionCanvasAudioCuePolicy>, D::Error> {
    MotionCanvasAudioCuePolicy::deserialize(deserializer).map(Some)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MltDissolveMode {
    HoldOutgoingLastFrame,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltDissolveIntent {
    pub outgoing_scene_id: Uuid,
    pub incoming_scene_id: Uuid,
    pub duration_frames: u16,
    pub mode: MltDissolveMode,
}

fn deserialize_mlt_dissolve<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<MltDissolveIntent>, D::Error> {
    MltDissolveIntent::deserialize(deserializer).map(Some)
}

impl MltDissolveIntent {
    fn validate(&self) -> Result<()> {
        if self.outgoing_scene_id.is_nil()
            || self.incoming_scene_id.is_nil()
            || self.outgoing_scene_id == self.incoming_scene_id
            || !(2..=30).contains(&self.duration_frames)
        {
            return Err(DomainError::Invalid(
                "MLT dissolve needs two distinct scenes and 2 to 30 frames".into(),
            ));
        }
        Ok(())
    }

    /// Check the boundary on the authored output cut at the profile clock.
    pub fn validate_for_profile(
        &self,
        scenes: &[Scene],
        profile: &DeliverableProfile,
    ) -> Result<()> {
        self.validate()?;
        let selected = crate::delivery::scenes_for_profile(scenes, profile)?;
        let edge = selected
            .windows(2)
            .position(|pair| {
                pair[0].id == self.outgoing_scene_id && pair[1].id == self.incoming_scene_id
            })
            .ok_or_else(|| {
                DomainError::Invalid(
                    "MLT dissolve scenes must be consecutive in the selected cut".into(),
                )
            })?;
        let cut = selected[..=edge]
            .iter()
            .try_fold(crate::RationalTime::ZERO, |time, scene| {
                time.checked_add(scene.duration)
            })
            .map_err(|_| DomainError::Invalid("MLT dissolve cut time overflow".into()))?;
        let rate = profile.frame_rate;
        let frame_ratio = |time: crate::RationalTime| {
            (
                i128::from(time.num) * i128::from(rate.num),
                i128::from(time.den) * i128::from(rate.den),
            )
        };
        let (at, units) = frame_ratio(cut);
        if at <= 0 || units <= 0 || at % units != 0 {
            return Err(DomainError::Invalid(
                "MLT dissolve cut must lie exactly on a delivery frame boundary".into(),
            ));
        }
        for (scene, needed) in [
            (selected[edge], 1),
            (selected[edge + 1], i128::from(self.duration_frames)),
        ] {
            let (span, units) = frame_ratio(scene.duration);
            if units <= 0 || span / units < needed {
                return Err(DomainError::Invalid(
                    "MLT dissolve requires an outgoing frame and enough incoming frames".into(),
                ));
            }
        }
        Ok(())
    }
}

/// Profile-owned QC intent, independent of whether a music/SFX mix is saved.
/// Values are exact public meter milli units, never silently rounded or used
/// to normalize media.
///
/// Minimum reader contract: `motionwright.delivery-audio-targets/1` including
/// both required nullable keys, exact i32 milli resolution, and at least one
/// requested metric. The contract name is documentation, not an extra JSON
/// key. Keeping recipe schema/1 does not make present targets readable by old
/// strict readers. An absent field alone retains legacy byte compatibility. Native QC consumes the final decoded output waveform.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DeliveryAudioTargets {
    pub target_lufs: Option<f64>,
    pub target_true_peak_dbtp: Option<f64>,
}

impl DeliveryAudioTargets {
    pub fn validated_milli_targets(&self) -> Result<(Option<i32>, Option<i32>)> {
        fn exact(value: f64) -> Result<i32> {
            let milli = (value * 1000.0).round();
            if !value.is_finite()
                || !milli.is_finite()
                || milli < f64::from(i32::MIN)
                || milli > f64::from(i32::MAX)
                || milli / 1000.0 != value
            {
                return Err(DomainError::Invalid(
                    "delivery audio targets require finite exact 0.001 units within the public i32 milli range".into(),
                ));
            }
            Ok(milli as i32)
        }
        if self.target_lufs.is_none() && self.target_true_peak_dbtp.is_none() {
            return Err(DomainError::Invalid(
                "delivery audio targets require at least one non-null target".into(),
            ));
        }
        Ok((
            self.target_lufs.map(exact).transpose()?,
            self.target_true_peak_dbtp.map(exact).transpose()?,
        ))
    }
}

impl<'de> Deserialize<'de> for DeliveryAudioTargets {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        fn required_nullable<'de, D: Deserializer<'de>>(
            deserializer: D,
        ) -> std::result::Result<Option<f64>, D::Error> {
            Option::<f64>::deserialize(deserializer)
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Wire {
            // deserialize_with without default makes both keys mandatory even
            // though their explicit JSON values may be null.
            #[serde(deserialize_with = "required_nullable")]
            target_lufs: Option<f64>,
            #[serde(deserialize_with = "required_nullable")]
            target_true_peak_dbtp: Option<f64>,
        }
        let wire = Wire::deserialize(deserializer)?;
        let result = Self {
            target_lufs: wire.target_lufs,
            target_true_peak_dbtp: wire.target_true_peak_dbtp,
        };
        result
            .validated_milli_targets()
            .map_err(de::Error::custom)?;
        Ok(result)
    }
}

fn deserialize_optional_audio_targets<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<DeliveryAudioTargets>, D::Error> {
    // Absence uses the field's default. An explicitly present null is not the
    // object specified by the closed wire contract.
    DeliveryAudioTargets::deserialize(deserializer).map(Some)
}

/// Application wire enums deliberately keep domain independent of the native
/// authoring crate. Native adapters must convert every variant exhaustively.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryNarrativeRole {
    Hook,
    Problem,
    Mechanism,
    Evidence,
    Comparison,
    Reveal,
    Payoff,
    Cta,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryArchetype {
    Statement,
    SplitExplanation,
    ArchitectureReveal,
    Comparison,
    Metric,
    Timeline,
    CodeFocus,
    DiagramBuild,
    ObjectSpotlight,
    EvidenceFrame,
    ProductProof,
    Endcard,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliverySceneIntent {
    pub scene_id: Uuid,
    pub role: Option<DeliveryNarrativeRole>,
    pub archetype: Option<DeliveryArchetype>,
}

/// Per-bus gain envelope on the existing 48 kHz output sample clock.
/// Muting never removes the selected source or bypasses source verification.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixBusEnvelope {
    pub muted: bool,
    pub fade_in_samples: u32,
    pub fade_out_samples: u32,
}

impl OriginalMixBusEnvelope {
    pub fn is_neutral(&self) -> bool {
        !self.muted && !self.has_fades()
    }

    pub fn has_fades(&self) -> bool {
        self.fade_in_samples != 0 || self.fade_out_samples != 0
    }

    /// The spans may touch, but may never overlap or exceed the output.
    /// The native mixer repeats this check against the verified PCM length.
    pub fn validate_for_sample_frames(&self, sample_frames: u64) -> Result<()> {
        let incoming = u64::from(self.fade_in_samples);
        let outgoing = u64::from(self.fade_out_samples);
        if incoming > sample_frames
            || outgoing > sample_frames
            || incoming + outgoing > sample_frames
        {
            return Err(DomainError::Invalid(
                "original mix fades overlap or exceed the exact output sample range".into(),
            ));
        }
        Ok(())
    }
}

/// Explicit terminal editorial release on the complete original 48-kHz mix.
/// No silence is appended and no original source bytes are modified.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixSilenceRelease {
    pub fade_samples: u32,
    pub silence_samples: u32,
}

impl OriginalMixSilenceRelease {
    /// Exact inclusive fade start. At least one earlier sample remains
    /// unattenuated; the requested tail lies inside the existing cut.
    pub fn fade_start(&self, sample_frames: u64) -> Result<u64> {
        let length = u64::from(self.fade_samples) + u64::from(self.silence_samples);
        if self.fade_samples < 2
            || self.silence_samples == 0
            || !(48_000..=MAX_MUSIC_SOURCE_SAMPLE_FRAMES).contains(&sample_frames)
            || length >= sample_frames
        {
            return Err(DomainError::Invalid(
                "Silence release requires a gradual fade and positive silent tail inside the exact original cut".into(),
            ));
        }
        Ok(sample_frames - length)
    }
}

pub fn deserialize_silence_release<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<OriginalMixSilenceRelease>, D::Error> {
    let release = OriginalMixSilenceRelease::deserialize(deserializer)?;
    release
        .fade_start(MAX_MUSIC_SOURCE_SAMPLE_FRAMES)
        .map_err(de::Error::custom)?;
    Ok(Some(release))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixRecipe {
    pub music_asset_id: Option<Uuid>,
    /// Absent = legacy whole-source music. Present (including zero) selects a
    /// contiguous 1:1 source-sample window with the exact saved cut length.
    /// Source identity is the saved music asset; no resampling, looping or pad.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_music_source_start_sample"
    )]
    pub music_source_start_sample: Option<u32>,
    /// Explicit editorial binding to exact source voice/ordered sample spans.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_voice_cut_sha256"
    )]
    pub voice_cut_sha256: Option<String>,
    pub sfx_asset_id: Option<Uuid>,
    pub sfx_gain_db: f64,
    pub duck_attenuation_db: f64,
    pub duck_attack_samples: u32,
    pub duck_release_samples: u32,
    #[serde(default, skip_serializing_if = "OriginalMixBusEnvelope::is_neutral")]
    pub voice_envelope: OriginalMixBusEnvelope,
    #[serde(default, skip_serializing_if = "OriginalMixBusEnvelope::is_neutral")]
    pub music_envelope: OriginalMixBusEnvelope,
    #[serde(default, skip_serializing_if = "OriginalMixBusEnvelope::is_neutral")]
    pub sfx_envelope: OriginalMixBusEnvelope,
    /// Optional versioned reader extension: motionwright.original-mix-silence-release/1.
    /// Absence preserves the exact historical mix bytes and gain function.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_silence_release"
    )]
    pub silence_release: Option<OriginalMixSilenceRelease>,
}

impl DeliveryRecipe {
    pub fn voice_cut_binding(&self) -> Option<&str> {
        self.original_mix
            .as_ref()
            .and_then(|mix| mix.voice_cut_sha256.as_deref())
            .or(self.voice_only_cut_sha256.as_deref())
    }

    pub fn validate(&self, scenes: &[Scene], assets: &[Asset]) -> Result<()> {
        if self.scene_intents.len() > MAX_DELIVERY_SCENE_INTENTS {
            return Err(DomainError::Invalid(
                "delivery recipe scene intent collection is too large".into(),
            ));
        }
        let scene_ids: HashSet<_> = scenes.iter().map(|scene| scene.id).collect();
        let mut seen = HashSet::new();
        for intent in &self.scene_intents {
            if !scene_ids.contains(&intent.scene_id) || !seen.insert(intent.scene_id) {
                return Err(DomainError::Invalid(
                    "delivery recipe scene intents require existing, unique scene ids".into(),
                ));
            }
        }
        if self
            .voice_only_cut_sha256
            .as_deref()
            .is_some_and(|value| self.original_mix.is_some() || !valid_voice_cut_sha256(value))
        {
            return Err(DomainError::Invalid(
                "Voice-only cut requires an exact binding and no original mix".into(),
            ));
        }
        if let Some(mix) = &self.original_mix {
            mix.validate(assets)?;
        }
        if let Some(targets) = &self.audio_targets {
            targets.validated_milli_targets()?;
        }
        if let Some(dissolve) = &self.mlt_dissolve {
            dissolve.validate()?;
            if !scene_ids.contains(&dissolve.outgoing_scene_id)
                || !scene_ids.contains(&dissolve.incoming_scene_id)
            {
                return Err(DomainError::Invalid(
                    "MLT dissolve references a missing scene".into(),
                ));
            }
        }
        Ok(())
    }

    /// Sorting is a derived projection only. Never rewrite the stored list on
    /// read; excluded scenes retain their authored decisions for another cut.
    pub fn consumed_scene_intents(&self, scene_ids: &HashSet<Uuid>) -> Vec<&DeliverySceneIntent> {
        let mut intents = self
            .scene_intents
            .iter()
            .filter(|intent| {
                scene_ids.contains(&intent.scene_id)
                    && (intent.role.is_some() || intent.archetype.is_some())
            })
            .collect::<Vec<_>>();
        intents.sort_by_key(|intent| intent.scene_id);
        intents
    }
}

/// A present null must not silently become the legacy whole-source contract.
pub fn deserialize_music_source_start_sample<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<u32>, D::Error> {
    u32::deserialize(deserializer).map(Some)
}

pub fn deserialize_voice_cut_sha256<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<String>, D::Error> {
    let value = String::deserialize(deserializer)?;
    if !valid_voice_cut_sha256(&value) {
        return Err(de::Error::custom(
            "Voice cut requires a lower-case SHA-256 binding",
        ));
    }
    Ok(Some(value))
}
fn valid_voice_cut_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn exact_release_samples(time: crate::RationalTime) -> Result<u64> {
    let numerator = i128::from(time.num) * 48_000;
    let denominator = i128::from(time.den);
    if numerator < 0 || denominator <= 0 || numerator % denominator != 0 {
        return Err(DomainError::Invalid(
            "Silence release requires exact 48-kHz sample boundaries".into(),
        ));
    }
    u64::try_from(numerator / denominator)
        .ok()
        .filter(|frames| *frames <= MAX_MUSIC_SOURCE_SAMPLE_FRAMES)
        .ok_or_else(|| {
            DomainError::Invalid("Silence release exceeds the original mix sample budget".into())
        })
}

impl OriginalMixRecipe {
    /// Returns the explicit window's required length, not a measured source
    /// duration. Native decoding independently checks the complete CAS WAV.
    pub fn music_window_output_frames(
        &self,
        scenes: &[Scene],
        profile: &DeliverableProfile,
    ) -> Result<Option<u64>> {
        let Some(start) = self.music_source_start_sample else {
            return Ok(None);
        };
        if profile.audio_sample_rate_hz != 48_000
            || (self.voice_cut_sha256.is_none()
                && !profile.included_scene_ids.is_empty()
                && !profile
                    .included_scene_ids
                    .iter()
                    .copied()
                    .eq(scenes.iter().map(|scene| scene.id)))
        {
            return Err(DomainError::Invalid(
                "Music source windows require the full original cut at 48 kHz or a saved voice cut binding".into(),
            ));
        }
        let mut duration = crate::RationalTime::ZERO;
        for scene in crate::delivery::scenes_for_profile(scenes, profile)? {
            duration = duration.checked_add(scene.duration).map_err(|_| {
                DomainError::Invalid("Music source window cut duration overflow".into())
            })?;
        }
        if duration.num <= 0 || duration.den <= 0 {
            return Err(DomainError::Invalid(
                "Music source windows need a positive saved cut duration".into(),
            ));
        }
        let numerator = i128::from(duration.num) * 48_000;
        let denominator = i128::from(duration.den);
        if numerator % denominator != 0 {
            return Err(DomainError::Invalid(
                "Music source window requires an exact 48 kHz output clock; no implicit snap"
                    .into(),
            ));
        }
        let frames = u64::try_from(numerator / denominator).map_err(|_| {
            DomainError::Invalid("Music source window sample range overflow".into())
        })?;
        if frames < 48_000
            || u64::from(start)
                .checked_add(frames)
                .is_none_or(|end| end > MAX_MUSIC_SOURCE_SAMPLE_FRAMES)
        {
            return Err(DomainError::Invalid("Music source window must fit the existing 180-second decode budget and a cut of at least one second".into()));
        }
        Ok(Some(frames))
    }

    /// Check the authored output clock independently of actual source decoding.
    pub fn silence_release_output_frames(
        &self,
        scenes: &[Scene],
        profile: &DeliverableProfile,
    ) -> Result<Option<u64>> {
        let Some(release) = self.silence_release else {
            return Ok(None);
        };
        if profile.audio_sample_rate_hz != 48_000
            || (self.voice_cut_sha256.is_none()
                && !profile.included_scene_ids.is_empty()
                && !profile
                    .included_scene_ids
                    .iter()
                    .copied()
                    .eq(scenes.iter().map(|scene| scene.id)))
        {
            return Err(DomainError::Invalid(
                "Silence release requires the complete original 48-kHz cut or an explicit reviewed voice cut".into(),
            ));
        }
        let mut duration = crate::RationalTime::ZERO;
        for scene in crate::delivery::scenes_for_profile(scenes, profile)? {
            duration = duration.checked_add(scene.duration).map_err(|_| {
                DomainError::Invalid("Silence release output duration overflow".into())
            })?;
        }
        let frames = exact_release_samples(duration)?;
        release.fade_start(frames)?;
        Ok(Some(frames))
    }

    /// A release may resolve music and SFX, but must not attenuate any selected
    /// reviewed narration interval, even if the current voice is muted.
    /// Revalidate on Save and again before original source preparation.
    pub fn validate_silence_release_source(
        &self,
        project: &crate::Project,
        profile: &DeliverableProfile,
    ) -> Result<Option<u64>> {
        let Some(frames) = self.silence_release_output_frames(&project.scenes, profile)? else {
            return Ok(None);
        };
        let fade_start = self
            .silence_release
            .expect("release length checked")
            .fade_start(frames)?;
        crate::delivery::validate_localized_voice_authority(project, profile)?;
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|track| Some(track.id) == profile.voice_track_id)
            .ok_or_else(|| {
                DomainError::Invalid(
                    "Silence release requires an explicitly bound original voice".into(),
                )
            })?;
        if track.sample_rate_hz != 48_000
            || track.channels != 2
            || !project.assets.iter().any(|asset| {
                asset.id == track.asset_id
                    && asset.content_sha256.as_deref() == Some(track.source_sha256.as_str())
                    && asset.media_type.starts_with("audio/")
            })
        {
            return Err(DomainError::Invalid(
                "Silence release requires the exact imported stereo 48-kHz voice".into(),
            ));
        }
        let source_frames = exact_release_samples(track.measured_duration)?;
        let cut =
            crate::resolve_original_voice_cut(project, profile, self.voice_cut_sha256.as_deref())?;
        if cut.as_ref().map_or(source_frames, |cut| cut.output_frames) != frames {
            return Err(DomainError::Invalid(
                "Silence release voice and output cut sample counts differ".into(),
            ));
        }
        let mut original = Vec::new();
        for segment in project
            .audio
            .transcript
            .iter()
            .filter(|segment| segment.voice_track_id == track.id)
        {
            let reviewed = match &segment.alignment {
                crate::AlignmentEvidence::Manual => true,
                crate::AlignmentEvidence::Measured {
                    source_sha256,
                    confidence_millis: Some(confidence),
                    ..
                } => source_sha256 == &track.source_sha256 && *confidence >= 800,
                _ => false,
            };
            let start = exact_release_samples(segment.start)?;
            let end = exact_release_samples(segment.end)?;
            if !reviewed || start >= end || end > source_frames {
                return Err(DomainError::Invalid(
                    "Silence release requires source-bound reviewed voice timing".into(),
                ));
            }
            original.push((start, end));
        }
        let mut selected = Vec::new();
        if let Some(cut) = cut {
            for span in cut.spans {
                for &(start, end) in &original {
                    if start < span.source_end && end > span.source_start {
                        if start < span.source_start || end > span.source_end {
                            return Err(DomainError::Invalid(
                                "Silence release cannot cross a selected reviewed voice boundary"
                                    .into(),
                            ));
                        }
                        selected.push((
                            span.output_start + start - span.source_start,
                            span.output_start + end - span.source_start,
                        ));
                    }
                }
            }
        } else {
            selected = original;
        }
        selected.sort_unstable();
        if !(1..=64).contains(&selected.len())
            || selected.windows(2).any(|pair| pair[0].1 > pair[1].0)
            || selected.iter().any(|&(_, end)| end > fade_start)
        {
            return Err(DomainError::Invalid(
                "Silence release must follow every selected reviewed narration interval".into(),
            ));
        }
        Ok(Some(frames))
    }

    pub fn has_fades(&self) -> bool {
        self.voice_envelope.has_fades()
            || self.music_envelope.has_fades()
            || self.sfx_envelope.has_fades()
    }

    pub fn validate_envelopes_for_sample_frames(&self, sample_frames: u64) -> Result<()> {
        for envelope in [self.voice_envelope, self.music_envelope, self.sfx_envelope] {
            envelope.validate_for_sample_frames(sample_frames)?;
        }
        Ok(())
    }

    pub fn validate(&self, assets: &[Asset]) -> Result<()> {
        if let Some(release) = self.silence_release {
            release.fade_start(MAX_MUSIC_SOURCE_SAMPLE_FRAMES)?;
        }
        if self
            .voice_cut_sha256
            .as_deref()
            .is_some_and(|value| !valid_voice_cut_sha256(value))
        {
            return Err(DomainError::Invalid(
                "Voice cut requires a lower-case SHA-256 binding".into(),
            ));
        }
        if self.music_source_start_sample.is_some_and(|start| {
            self.music_asset_id.is_none() || u64::from(start) >= MAX_MUSIC_SOURCE_SAMPLE_FRAMES
        }) {
            return Err(DomainError::Invalid("Music source window requires a selected audio asset and a bounded exact start sample".into()));
        }
        if !self.sfx_gain_db.is_finite() || !(-80.0..=12.0).contains(&self.sfx_gain_db) {
            return Err(DomainError::Invalid(
                "delivery recipe SFX gain must be finite and within -80..12 dB".into(),
            ));
        }
        if !self.duck_attenuation_db.is_finite()
            || !(0.0..=30.0).contains(&self.duck_attenuation_db)
        {
            return Err(DomainError::Invalid(
                "delivery recipe duck attenuation must be finite and within 0..30 dB".into(),
            ));
        }
        if !(1..=48_000).contains(&self.duck_attack_samples)
            || !(1..=48_000).contains(&self.duck_release_samples)
        {
            return Err(DomainError::Invalid(
                "delivery recipe duck attack/release must be within 1..48000 samples".into(),
            ));
        }
        if self.music_asset_id.is_some() && self.music_asset_id == self.sfx_asset_id {
            return Err(DomainError::Invalid(
                "delivery recipe music and SFX require distinct source assets".into(),
            ));
        }
        for id in [self.music_asset_id, self.sfx_asset_id]
            .into_iter()
            .flatten()
        {
            let asset = assets.iter().find(|asset| asset.id == id).ok_or_else(|| {
                DomainError::Invalid("delivery recipe references an unknown source asset".into())
            })?;
            asset.validate()?;
            if !asset.media_type.starts_with("audio/") || asset.content_sha256.is_none() {
                return Err(DomainError::Invalid(
                    "delivery recipe sources require registered content-addressed audio assets"
                        .into(),
                ));
            }
        }
        Ok(())
    }
}

impl ProductionDesign {
    /// Each branch supplies its own scenes/profiles; assets remain global.
    pub fn validate_delivery_recipes(
        &self,
        scenes: &[Scene],
        assets: &[Asset],
        profiles: &[DeliverableProfile],
    ) -> Result<()> {
        if self.delivery_recipes.len() > crate::MAX_DELIVERABLE_PROFILES {
            return Err(DomainError::Invalid("too many delivery recipes".into()));
        }
        for (profile_id, recipe) in &self.delivery_recipes {
            let profile = profiles
                .iter()
                .find(|profile| profile.id == *profile_id)
                .ok_or_else(|| {
                    DomainError::Invalid(
                        "delivery recipe references an unknown deliverable profile".into(),
                    )
                })?;
            recipe.validate(scenes, assets)?;
            if let Some(dissolve) = &recipe.mlt_dissolve {
                dissolve.validate_for_profile(scenes, profile)?;
            }
            if let Some(mix) = &recipe.original_mix {
                mix.music_window_output_frames(scenes, profile)?;
                mix.silence_release_output_frames(scenes, profile)?;
            }
            if let Some(mix) = &recipe.original_mix
                && mix.has_fades()
            {
                // This is the authored cut bound. Native admission additionally
                // checks the actual verified PCM output, with no pad or trim.
                let selected = crate::delivery::scenes_for_profile(scenes, profile)?;
                let mut duration = crate::RationalTime::ZERO;
                for scene in selected {
                    duration = duration
                        .checked_add(scene.duration)
                        .map_err(|_| DomainError::Invalid("mix output duration overflow".into()))?;
                }
                if duration.den <= 0 || duration.num < 0 {
                    return Err(DomainError::Invalid("invalid mix output duration".into()));
                }
                let samples = i128::from(duration.num) * 48_000;
                if samples % i128::from(duration.den) != 0 {
                    return Err(DomainError::Invalid(
                        "mix fades require an exact 48 kHz output clock; no implicit snap".into(),
                    ));
                }
                let samples = u64::try_from(samples / i128::from(duration.den))
                    .map_err(|_| DomainError::Invalid("mix output sample range overflow".into()))?;
                mix.validate_envelopes_for_sample_frames(samples)?;
            }
        }
        Ok(())
    }

    pub(crate) fn delivery_recipe_references_asset(&self, asset_id: Uuid) -> bool {
        self.delivery_recipes.values().any(|recipe| {
            recipe.original_mix.as_ref().is_some_and(|mix| {
                mix.music_asset_id == Some(asset_id) || mix.sfx_asset_id == Some(asset_id)
            })
        })
    }
}

/// Run at typed raw-JSON boundaries. Passing through serde_json::Value first
/// has already discarded duplicate spellings and cannot provide this check.
pub(crate) fn deserialize_delivery_recipes<'de, D>(
    deserializer: D,
) -> std::result::Result<BTreeMap<Uuid, DeliveryRecipe>, D::Error>
where
    D: Deserializer<'de>,
{
    struct RecipesVisitor;
    impl<'de> de::Visitor<'de> for RecipesVisitor {
        type Value = BTreeMap<Uuid, DeliveryRecipe>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("a map with unique delivery profile UUID keys")
        }

        fn visit_map<A>(self, mut entries: A) -> std::result::Result<Self::Value, A::Error>
        where
            A: de::MapAccess<'de>,
        {
            let mut recipes = BTreeMap::new();
            while let Some(profile_id) = entries.next_key::<Uuid>()? {
                if recipes.contains_key(&profile_id) {
                    return Err(de::Error::custom(format!(
                        "duplicate delivery recipe profile UUID: {profile_id}"
                    )));
                }
                if recipes.len() >= crate::MAX_DELIVERABLE_PROFILES {
                    return Err(de::Error::custom("too many delivery recipes"));
                }
                recipes.insert(profile_id, entries.next_value::<DeliveryRecipe>()?);
            }
            Ok(recipes)
        }
    }
    deserializer.deserialize_map(RecipesVisitor)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Change, Project, variant_dependency_fingerprint};

    fn project() -> Project {
        let mut project = Project::new("Synthetic delivery recipe unit fixture").unwrap();
        for name in ["Opening", "Evidence"] {
            project
                .apply_change(&Change::AddScene {
                    name: name.into(),
                    objective: "Editable recipe source".into(),
                    duration_seconds: 2,
                })
                .unwrap();
        }
        project
    }

    fn recipe(project: &Project) -> DeliveryRecipe {
        DeliveryRecipe {
            schema: DeliveryRecipeSchema::V1,
            voice_only_cut_sha256: None,
            mlt_dissolve: None,
            motion_canvas_audio_cues: None,
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| DeliverySceneIntent {
                    scene_id: scene.id,
                    role: Some(DeliveryNarrativeRole::Evidence),
                    archetype: Some(DeliveryArchetype::EvidenceFrame),
                })
                .collect(),
            audio_targets: None,
            original_mix: None,
        }
    }

    fn mix() -> OriginalMixRecipe {
        OriginalMixRecipe {
            music_asset_id: None,
            sfx_asset_id: None,
            sfx_gain_db: -12.0,
            duck_attenuation_db: 8.0,
            duck_attack_samples: 960,
            duck_release_samples: 4_800,
            silence_release: None,
            voice_envelope: Default::default(),
            music_source_start_sample: None,
            voice_cut_sha256: None,
            music_envelope: Default::default(),
            sfx_envelope: Default::default(),
        }
    }

    fn set(project: &mut Project, profile_id: Uuid, recipe: Option<DeliveryRecipe>) {
        project
            .apply_change(&Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe,
            })
            .unwrap();
    }

    #[test]
    fn explicit_music_source_window_is_a_bounded_exact_saved_cut() {
        let mut p = project();
        let profile_id = p.deliverables[0].id;
        let asset_id = Uuid::now_v7();
        p.assets.push(Asset {
            id: asset_id,
            name: "music.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: None,
        });
        let mut intent = mix();
        intent.music_asset_id = Some(asset_id);
        assert!(
            serde_json::to_value(&intent)
                .unwrap()
                .get("music_source_start_sample")
                .is_none()
        );
        let legacy = variant_dependency_fingerprint(&p, profile_id).unwrap();
        intent.music_source_start_sample = Some(0);
        assert_eq!(
            intent
                .music_window_output_frames(&p.scenes, &p.deliverables[0])
                .unwrap(),
            Some(192_000)
        );
        assert_eq!(
            serde_json::from_value::<OriginalMixRecipe>(serde_json::to_value(&intent).unwrap())
                .unwrap(),
            intent
        );
        let mut null_wire = serde_json::to_value(&intent).unwrap();
        null_wire["music_source_start_sample"] = serde_json::Value::Null;
        assert!(serde_json::from_value::<OriginalMixRecipe>(null_wire).is_err());
        let mut saved = recipe(&p);
        saved.original_mix = Some(intent.clone());
        set(&mut p, profile_id, Some(saved.clone()));
        let first = variant_dependency_fingerprint(&p, profile_id).unwrap();
        assert_ne!(legacy.audio_inputs_sha256, first.audio_inputs_sha256);
        saved
            .original_mix
            .as_mut()
            .unwrap()
            .music_source_start_sample = Some(48_000);
        set(&mut p, profile_id, Some(saved.clone()));
        let second = variant_dependency_fingerprint(&p, profile_id).unwrap();
        assert_ne!(first.audio_inputs_sha256, second.audio_inputs_sha256);
        assert_eq!(first.frame_inputs_sha256, second.frame_inputs_sha256);
        let before = p.clone();
        for value in [Some(8_639_999), Some(8_640_000)] {
            let mut invalid = saved.clone();
            invalid
                .original_mix
                .as_mut()
                .unwrap()
                .music_source_start_sample = value;
            assert!(
                p.apply_change(&Change::SetDeliveryRecipe {
                    deliverable_profile_id: profile_id,
                    recipe: Some(invalid)
                })
                .is_err()
            );
            assert_eq!(p, before);
        }
        let mut partial = p.deliverables[0].clone();
        partial.included_scene_ids = vec![p.scenes[0].id];
        assert!(
            p.apply_change(&Change::UpsertDeliverable { profile: partial })
                .is_err()
        );
        assert_eq!(p, before);
    }

    #[test]
    fn absent_map_stays_absent_and_recipe_wire_is_strict() {
        let design: ProductionDesign = serde_json::from_str("{}").unwrap();
        assert!(design.delivery_recipes.is_empty());
        assert!(
            serde_json::to_value(design)
                .unwrap()
                .get("delivery_recipes")
                .is_none()
        );
        let valid = serde_json::to_string(&DeliveryRecipe::default()).unwrap();
        assert!(serde_json::from_str::<DeliveryRecipe>(&valid).is_ok());
        assert!(
            serde_json::from_str::<DeliveryRecipe>(&valid.replace(
                "motionwright.delivery-recipe/1",
                "motionwright.delivery-recipe/2"
            ))
            .is_err()
        );
        let mut unknown = serde_json::to_value(DeliveryRecipe::default()).unwrap();
        unknown["preview_approval"] = serde_json::json!(true);
        assert!(serde_json::from_value::<DeliveryRecipe>(unknown).is_err());
        let mut extra_mix = serde_json::to_value(mix()).unwrap();
        extra_mix["sample_peak_guard_dbfs"] = serde_json::json!(-1.0);
        assert!(serde_json::from_value::<OriginalMixRecipe>(extra_mix).is_err());
        let mut intent = serde_json::to_value(&recipe(&project()).scene_intents[0]).unwrap();
        intent["role"] = serde_json::json!("unregistered_role");
        assert!(serde_json::from_value::<DeliverySceneIntent>(intent).is_err());
    }

    #[test]
    fn raw_duplicate_keys_reject_all_equivalent_uuid_spellings() {
        let id = Uuid::parse_str("abcdefab-1234-4567-89ab-abcdef123456").unwrap();
        let recipe = serde_json::to_string(&DeliveryRecipe::default()).unwrap();
        for second in [
            id.to_string(),
            id.to_string().to_uppercase(),
            id.simple().to_string(),
            id.urn().to_string(),
        ] {
            let raw =
                format!("{{\"delivery_recipes\":{{\"{id}\":{recipe},\"{second}\":{recipe}}}}}");
            let error = serde_json::from_str::<ProductionDesign>(&raw).unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("duplicate delivery recipe profile UUID")
            );
        }
    }

    #[test]
    fn invalid_recipe_changes_leave_the_domain_document_exactly_unchanged() {
        let mut project = project();
        let profile_id = project.deliverables[0].id;
        let valid = recipe(&project);
        set(&mut project, profile_id, Some(valid.clone()));
        let mut invalid = Vec::new();
        let mut duplicate = valid.clone();
        duplicate
            .scene_intents
            .push(duplicate.scene_intents[0].clone());
        invalid.push(duplicate);
        let mut missing = valid.clone();
        missing.scene_intents[0].scene_id = Uuid::new_v4();
        invalid.push(missing);
        for value in [f64::NAN, f64::INFINITY, -80.01, 12.01] {
            let mut candidate = valid.clone();
            candidate.original_mix = Some(OriginalMixRecipe {
                sfx_gain_db: value,
                ..mix()
            });
            invalid.push(candidate);
        }
        for value in [f64::NAN, f64::NEG_INFINITY, -0.01, 30.01] {
            let mut candidate = valid.clone();
            candidate.original_mix = Some(OriginalMixRecipe {
                duck_attenuation_db: value,
                ..mix()
            });
            invalid.push(candidate);
        }
        for value in [0, 48_001] {
            for attack in [true, false] {
                let mut original_mix = mix();
                if attack {
                    original_mix.duck_attack_samples = value;
                } else {
                    original_mix.duck_release_samples = value;
                }
                let mut candidate = valid.clone();
                candidate.original_mix = Some(original_mix);
                invalid.push(candidate);
            }
        }
        for recipe in invalid {
            let before = project.clone();
            assert!(
                project
                    .apply_change(&Change::SetDeliveryRecipe {
                        deliverable_profile_id: profile_id,
                        recipe: Some(recipe),
                    })
                    .is_err()
            );
            assert_eq!(project, before);
        }
        let before = project.clone();
        assert!(
            project
                .apply_change(&Change::SetDeliveryRecipe {
                    deliverable_profile_id: Uuid::new_v4(),
                    recipe: None,
                })
                .is_err()
        );
        assert_eq!(project, before);
    }

    #[test]
    fn both_branch_snapshots_use_their_own_profiles_and_scenes() {
        let mut project = project();
        let profile_id = project.deliverables[0].id;
        let recipe = recipe(&project);
        set(&mut project, profile_id, Some(recipe.clone()));
        project
            .apply_change(&Change::CreateBranch {
                name: "Recipe branch".into(),
            })
            .unwrap();
        for base in [true, false] {
            let mut candidate = project.clone();
            let workspace = &mut candidate.branch_workspaces[0];
            let state = if base {
                &mut workspace.base_state
            } else {
                &mut workspace.current_state
            };
            let mut local_profile = state.deliverables[0].clone();
            local_profile.id = Uuid::new_v4();
            local_profile.name = "Only in this saved branch state".into();
            state
                .production_design
                .delivery_recipes
                .insert(local_profile.id, recipe.clone());
            state.deliverables.push(local_profile);
            candidate.validate().unwrap();
            let workspace = &mut candidate.branch_workspaces[0];
            let state = if base {
                &mut workspace.base_state
            } else {
                &mut workspace.current_state
            };
            state
                .deliverables
                .retain(|profile| profile.id != profile_id);
            assert!(
                candidate
                    .validate()
                    .unwrap_err()
                    .to_string()
                    .contains("unknown deliverable profile")
            );

            let mut candidate = project.clone();
            let workspace = &mut candidate.branch_workspaces[0];
            let state = if base {
                &mut workspace.base_state
            } else {
                &mut workspace.current_state
            };
            state.scenes.remove(0);
            assert!(
                candidate
                    .validate()
                    .unwrap_err()
                    .to_string()
                    .contains("unique scene ids")
            );
        }
        let mut active = project.clone();
        active.scenes.remove(0);
        assert!(active.validate().is_err());
    }

    #[test]
    fn fingerprints_normalize_intent_order_and_ignore_other_profiles_and_excluded_scenes() {
        let mut project = project();
        let a = project.deliverables[0].id;
        let b = project.deliverables[1].id;
        let recipe = recipe(&project);
        set(&mut project, a, Some(recipe.clone()));
        let before_a = variant_dependency_fingerprint(&project, a).unwrap();
        let before_b = variant_dependency_fingerprint(&project, b).unwrap();
        let mut reordered = recipe.clone();
        reordered.scene_intents.reverse();
        set(&mut project, a, Some(reordered.clone()));
        assert_eq!(
            variant_dependency_fingerprint(&project, a).unwrap(),
            before_a
        );
        assert_eq!(project.production_design.delivery_recipes[&a], reordered);

        let mut changed = recipe.clone();
        changed.scene_intents[0].role = Some(DeliveryNarrativeRole::Hook);
        set(&mut project, a, Some(changed));
        let after_a = variant_dependency_fingerprint(&project, a).unwrap();
        assert_ne!(after_a.frame_inputs_sha256, before_a.frame_inputs_sha256);
        assert_eq!(after_a.audio_inputs_sha256, before_a.audio_inputs_sha256);
        assert_eq!(
            variant_dependency_fingerprint(&project, b).unwrap(),
            before_b
        );

        project.deliverables[0].included_scene_ids = vec![project.scenes[0].id];
        let before = variant_dependency_fingerprint(&project, a).unwrap();
        project
            .production_design
            .delivery_recipes
            .get_mut(&a)
            .unwrap()
            .scene_intents[1]
            .archetype = Some(DeliveryArchetype::Metric);
        assert_eq!(variant_dependency_fingerprint(&project, a).unwrap(), before);
    }

    #[test]
    fn neutral_bus_envelopes_preserve_legacy_json_and_explicit_values_are_closed() {
        let expected = r#"{"music_asset_id":null,"sfx_asset_id":null,"sfx_gain_db":-12.0,"duck_attenuation_db":8.0,"duck_attack_samples":960,"duck_release_samples":4800}"#;
        assert_eq!(serde_json::to_string(&mix()).unwrap(), expected);
        let legacy = serde_json::to_value(mix()).unwrap();
        for key in ["voice_envelope", "music_envelope", "sfx_envelope"] {
            assert!(legacy.get(key).is_none());
            let mut explicit = legacy.clone();
            explicit[key] = serde_json::json!({
                "muted": false, "fade_in_samples": 0, "fade_out_samples": 0
            });
            let decoded: OriginalMixRecipe = serde_json::from_value(explicit).unwrap();
            assert_eq!(serde_json::to_value(decoded).unwrap(), legacy);
        }
        for invalid in [
            serde_json::json!({"muted": 0, "fade_in_samples": 0, "fade_out_samples": 0}),
            serde_json::json!({"muted": false, "fade_in_samples": -1, "fade_out_samples": 0}),
            serde_json::json!({"muted": false, "fade_in_samples": 0.5, "fade_out_samples": 0}),
            serde_json::json!({"muted": false, "fade_in_samples": 4294967296_u64, "fade_out_samples": 0}),
            serde_json::json!({"muted": true}),
            serde_json::json!({"muted": false, "fade_in_samples": 0, "fade_out_samples": 0, "approved": true}),
        ] {
            assert!(serde_json::from_value::<OriginalMixBusEnvelope>(invalid).is_err());
        }
        let repeated = r#"{"muted":false,"muted":true,"fade_in_samples":0,"fade_out_samples":0}"#;
        assert!(serde_json::from_str::<OriginalMixBusEnvelope>(repeated).is_err());
    }

    #[test]
    fn silence_release_is_a_closed_optional_wire_with_exact_in_cut_sample_bounds() {
        let legacy = mix();
        let wire = serde_json::to_value(&legacy).unwrap();
        assert!(wire.get("silence_release").is_none());
        assert_eq!(
            serde_json::from_value::<OriginalMixRecipe>(wire.clone()).unwrap(),
            legacy
        );
        let mut valid = wire.clone();
        valid["silence_release"] =
            serde_json::json!({"fade_samples":12000,"silence_samples":12000});
        let decoded = serde_json::from_value::<OriginalMixRecipe>(valid.clone()).unwrap();
        assert_eq!(
            decoded.silence_release.unwrap().fade_start(96000).unwrap(),
            72000
        );
        assert_eq!(serde_json::to_value(&decoded).unwrap(), valid);
        for bad in [
            serde_json::Value::Null,
            serde_json::json!({}),
            serde_json::json!({"fade_samples":1,"silence_samples":12000}),
            serde_json::json!({"fade_samples":12000,"silence_samples":0}),
            serde_json::json!({"fade_samples":12000.5,"silence_samples":12000}),
            serde_json::json!({"fade_samples":12000,"silence_samples":12000,"approved":true}),
            serde_json::json!({"fade_samples":8640000,"silence_samples":1}),
        ] {
            let mut forged = wire.clone();
            forged["silence_release"] = bad;
            assert!(serde_json::from_value::<OriginalMixRecipe>(forged).is_err());
        }
        assert!(
            OriginalMixSilenceRelease {
                fade_samples: 24000,
                silence_samples: 24000
            }
            .fade_start(48000)
            .is_err()
        );
        assert!(
            OriginalMixSilenceRelease {
                fade_samples: 12000,
                silence_samples: 12000
            }
            .fade_start(47999)
            .is_err()
        );
    }

    #[test]
    fn touching_fades_fit_exact_samples_and_muted_invalid_ranges_still_fail() {
        let touching = OriginalMixBusEnvelope {
            muted: false,
            fade_in_samples: 24_000,
            fade_out_samples: 24_000,
        };
        touching.validate_for_sample_frames(48_000).unwrap();
        assert!(touching.validate_for_sample_frames(47_999).is_err());
        for envelope in [
            OriginalMixBusEnvelope {
                muted: false,
                fade_in_samples: 48_001,
                fade_out_samples: 0,
            },
            OriginalMixBusEnvelope {
                muted: true,
                fade_in_samples: 0,
                fade_out_samples: 48_001,
            },
            OriginalMixBusEnvelope {
                muted: true,
                fade_in_samples: u32::MAX,
                fade_out_samples: u32::MAX,
            },
        ] {
            assert!(envelope.validate_for_sample_frames(48_000).is_err());
        }
    }

    #[test]
    fn persisted_fades_invalidate_audio_only_and_invalid_cut_or_retime_is_atomic() {
        let mut project = project();
        let profile_id = project.deliverables[0].id;
        let mut saved = recipe(&project);
        saved.original_mix = Some(mix());
        set(&mut project, profile_id, Some(saved.clone()));
        let neutral = variant_dependency_fingerprint(&project, profile_id).unwrap();
        saved.original_mix.as_mut().unwrap().music_envelope = OriginalMixBusEnvelope {
            muted: true,
            fade_in_samples: 96_000,
            fade_out_samples: 96_000,
        };
        set(&mut project, profile_id, Some(saved));
        let changed = variant_dependency_fingerprint(&project, profile_id).unwrap();
        assert_eq!(changed.frame_inputs_sha256, neutral.frame_inputs_sha256);
        assert_eq!(changed.caption_inputs_sha256, neutral.caption_inputs_sha256);
        assert_ne!(changed.audio_inputs_sha256, neutral.audio_inputs_sha256);
        let before = project.clone();
        let mut cut = project.deliverables[0].clone();
        cut.included_scene_ids = vec![project.scenes[0].id];
        for change in [
            Change::SetSceneDuration {
                scene_id: project.scenes[1].id,
                duration: crate::RationalTime::new(1, 1).unwrap(),
            },
            Change::UpsertDeliverable { profile: cut },
        ] {
            assert!(project.apply_change(&change).is_err());
            assert_eq!(project, before);
        }
    }

    #[test]
    fn audio_targets_require_both_nullable_keys_and_preserve_absent_legacy_bytes() {
        let legacy =
            r#"{"schema":"motionwright.delivery-recipe/1","scene_intents":[],"original_mix":null}"#;
        let decoded: DeliveryRecipe = serde_json::from_str(legacy).unwrap();
        assert!(decoded.audio_targets.is_none());
        assert_eq!(serde_json::to_string(&decoded).unwrap(), legacy);
        for raw in [
            r#"{}"#,
            r#"{"target_lufs":-23.0}"#,
            r#"{"target_true_peak_dbtp":-1.0}"#,
            r#"{"target_lufs":null,"target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":-23.0,"target_true_peak_dbtp":null,"approved":true}"#,
            r#"{"target_lufs":-23.0,"target_lufs":-14.0,"target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":"-23.0","target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":-23.0001,"target_true_peak_dbtp":null}"#,
        ] {
            assert!(
                serde_json::from_str::<DeliveryAudioTargets>(raw).is_err(),
                "{raw}"
            );
        }
        let explicit_null = legacy.replace(
            "\"original_mix\":null",
            "\"original_mix\":null,\"audio_targets\":null",
        );
        assert!(serde_json::from_str::<DeliveryRecipe>(&explicit_null).is_err());
        for raw in [
            r#"{"target_lufs":-23.001,"target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":null,"target_true_peak_dbtp":-1.0}"#,
            r#"{"target_lufs":-23.0,"target_true_peak_dbtp":-1.0}"#,
        ] {
            let targets: DeliveryAudioTargets = serde_json::from_str(raw).unwrap();
            let wire = serde_json::to_value(&targets).unwrap();
            assert!(wire.get("target_lufs").is_some());
            assert!(wire.get("target_true_peak_dbtp").is_some());
        }
    }

    #[test]
    fn audio_target_precision_is_exact_i32_milli_without_arbitrary_ranges_or_rounding() {
        for milli in [i32::MIN, -23_001, -1, 0, 1, 23_001, i32::MAX] {
            let targets = DeliveryAudioTargets {
                target_lufs: Some(f64::from(milli) / 1000.0),
                target_true_peak_dbtp: None,
            };
            assert_eq!(
                targets.validated_milli_targets().unwrap(),
                (Some(milli), None)
            );
            let reparsed: DeliveryAudioTargets =
                serde_json::from_str(&serde_json::to_string(&targets).unwrap()).unwrap();
            assert_eq!(reparsed, targets);
        }
        for bad in [
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::from(i32::MIN) / 1000.0 - 0.001,
            f64::from(i32::MAX) / 1000.0 + 0.001,
            -23.0001,
            -0.0001,
            0.0005,
            f64::MAX,
        ] {
            for lufs in [true, false] {
                let targets = DeliveryAudioTargets {
                    target_lufs: if lufs { Some(bad) } else { None },
                    target_true_peak_dbtp: if lufs { None } else { Some(bad) },
                };
                assert!(targets.validated_milli_targets().is_err(), "{bad}");
            }
        }
    }

    #[test]
    fn target_only_recipes_bind_only_consuming_audio_and_invalid_changes_are_atomic() {
        let mut project = project();
        let a = project.deliverables[0].id;
        let b = project.deliverables[1].id;
        let before_a = variant_dependency_fingerprint(&project, a).unwrap();
        let before_b = variant_dependency_fingerprint(&project, b).unwrap();
        let targets = DeliveryAudioTargets {
            target_lufs: Some(-23.0),
            target_true_peak_dbtp: Some(-1.0),
        };
        let authored = DeliveryRecipe {
            audio_targets: Some(targets.clone()),
            ..Default::default()
        };
        set(&mut project, a, Some(authored.clone()));
        assert_eq!(project.production_design.delivery_recipes[&a], authored);
        assert!(
            project.production_design.delivery_recipes[&a]
                .original_mix
                .is_none()
        );
        let after_a = variant_dependency_fingerprint(&project, a).unwrap();
        assert_eq!(after_a.frame_inputs_sha256, before_a.frame_inputs_sha256);
        assert_eq!(
            after_a.caption_inputs_sha256,
            before_a.caption_inputs_sha256
        );
        assert_ne!(after_a.audio_inputs_sha256, before_a.audio_inputs_sha256);
        assert_ne!(after_a.master_inputs_sha256, before_a.master_inputs_sha256);
        assert_eq!(
            variant_dependency_fingerprint(&project, b).unwrap(),
            before_b
        );
        let before = project.clone();
        for target in [
            DeliveryAudioTargets {
                target_lufs: None,
                target_true_peak_dbtp: None,
            },
            DeliveryAudioTargets {
                target_lufs: Some(-23.0001),
                target_true_peak_dbtp: None,
            },
        ] {
            assert!(
                project
                    .apply_change(&Change::SetDeliveryRecipe {
                        deliverable_profile_id: a,
                        recipe: Some(DeliveryRecipe {
                            audio_targets: Some(target),
                            ..Default::default()
                        }),
                    })
                    .is_err()
            );
            assert_eq!(project, before);
        }
        set(&mut project, a, None);
        assert_eq!(
            variant_dependency_fingerprint(&project, a).unwrap(),
            before_a
        );
    }
}

#[cfg(test)]
mod mlt_dissolve_wire_tests {
    use super::*;

    #[test]
    fn absent_mlt_dissolve_preserves_legacy_recipe_bytes_and_explicit_null_is_invalid() {
        let original =
            r#"{"schema":"motionwright.delivery-recipe/1","scene_intents":[],"original_mix":null}"#;
        let recipe: DeliveryRecipe = serde_json::from_str(original).unwrap();
        assert!(recipe.mlt_dissolve.is_none());
        assert_eq!(serde_json::to_string(&recipe).unwrap(), original);
        let mut malformed = serde_json::to_value(recipe).unwrap();
        malformed["mlt_dissolve"] = serde_json::Value::Null;
        assert!(serde_json::from_value::<DeliveryRecipe>(malformed).is_err());
    }

    #[test]
    fn explicit_motion_canvas_audio_cues_preserve_the_legacy_wire_when_absent() {
        let original =
            r#"{"schema":"motionwright.delivery-recipe/1","scene_intents":[],"original_mix":null}"#;
        let recipe: DeliveryRecipe = serde_json::from_str(original).unwrap();
        assert!(recipe.motion_canvas_audio_cues.is_none());
        assert_eq!(serde_json::to_string(&recipe).unwrap(), original);
        let mut wire = serde_json::to_value(recipe).unwrap();
        wire["motion_canvas_audio_cues"] = serde_json::Value::Null;
        assert!(serde_json::from_value::<DeliveryRecipe>(wire.clone()).is_err());
        wire["motion_canvas_audio_cues"] = serde_json::json!("preserve_authored_time");
        let opted_in = serde_json::from_value::<DeliveryRecipe>(wire.clone()).unwrap();
        assert_eq!(
            opted_in.motion_canvas_audio_cues,
            Some(MotionCanvasAudioCuePolicy::PreserveAuthoredTime)
        );
        assert_eq!(serde_json::to_value(opted_in).unwrap(), wire);
    }
}
