//! Explicit musical, voice, imported-media or timeline clocks. Bound to samples,
//! not floating-point time. No independent scheduler or timeline implementation.
use crate::*;
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AuthoringClock {
    Timeline,
    Voice,
    Music,
    SourceMedia,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ClockAnchor {
    pub source_tick: u64,
    pub timeline_sample: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeClockMap {
    pub version: u32,
    pub source_clock: AuthoringClock,
    pub source_rate: native::FrameRate,
    pub timeline_sample_rate: u32,
    pub source_revision_sha256: String,
    pub anchors: Vec<ClockAnchor>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ExactSamplePosition {
    pub whole: u64,
    pub sub_num: u64,
    pub sub_den: u64,
}
impl ExactSamplePosition {
    pub fn is_exact_sample(self) -> bool {
        self.sub_num == 0
    }
    pub fn require_whole_sample(self) -> Result<u64> {
        check(
            self.is_exact_sample(),
            "Cue maps to a fractional sample. Retiming/resampling requires explicit authorization; do not round silently.",
        )?;
        Ok(self.whole)
    }
}
fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a
}
impl CreativeClockMap {
    pub fn validate(&self) -> Result<()> {
        check(
            self.version == 1 && self.timeline_sample_rate == 48000,
            "Only explicit 48 kHz original sound clocks have a verified PCM route",
        )?;
        check(
            self.source_rate.num > 0
                && self.source_rate.den > 0
                && self.source_rate.num <= 192000
                && self.source_rate.den <= 1000000,
            "Source timebase is outside the bounded rational clock profile",
        )?;
        check(
            valid_sha(&self.source_revision_sha256),
            "Creative clock requires a source-version digest, not an ambient timestamp",
        )?;
        check(
            (2..=256).contains(&self.anchors.len()),
            "Clock map requires two to 256 exact anchors",
        )?;
        check(
            self.anchors[0].source_tick == 0 && self.anchors[0].timeline_sample == 0,
            "Clock map must explicitly anchor source zero to output zero",
        )?;
        check(
            self.anchors.windows(2).all(|w| {
                w[0].source_tick < w[1].source_tick && w[0].timeline_sample < w[1].timeline_sample
            }),
            "Clock anchors must be strictly monotonic in both clocks",
        )?;
        let last = self.anchors.last().expect("validated nonempty anchor set");
        check(
            last.timeline_sample <= 48000 * 600 && last.source_tick <= 192000 * 600,
            "Bounded first-party audio previews cannot exceed ten minutes",
        )?;
        Ok(())
    }
    /// Piecewise linear rational mapping. Explicit sub-sample fractions remain
    /// fractions instead of being rounded segment by segment.
    pub fn map_source_tick(&self, source_tick: u64) -> Result<ExactSamplePosition> {
        self.validate()?;
        let last = self.anchors.last().expect("validated clock");
        check(
            source_tick <= last.source_tick,
            "Clock lookup cannot extrapolate beyond authorized anchors",
        )?;
        for segment in self.anchors.windows(2) {
            let a = segment[0];
            let b = segment[1];
            if source_tick <= b.source_tick {
                let diff = b.source_tick - a.source_tick;
                let position = source_tick - a.source_tick;
                let duration = b.timeline_sample - a.timeline_sample;
                let numerator = u128::from(position) * u128::from(duration);
                let denominator = u128::from(diff);
                let whole = a
                    .timeline_sample
                    .checked_add(u64::try_from(numerator / denominator).map_err(|_| {
                        CraftError("Clock conversion exceeded sample index bounds".into())
                    })?)
                    .ok_or_else(|| CraftError("Clock position overflow".into()))?;
                let remainder = u64::try_from(numerator % denominator)
                    .map_err(|_| CraftError("Clock precision budget was exceeded".into()))?;
                if remainder == 0 {
                    return Ok(ExactSamplePosition {
                        whole,
                        sub_num: 0,
                        sub_den: 1,
                    });
                }
                let d = gcd(remainder, diff);
                return Ok(ExactSamplePosition {
                    whole,
                    sub_num: remainder / d,
                    sub_den: diff / d,
                });
            }
        }
        Err(CraftError(
            "Clock tick lies outside its admitted output interval".into(),
        ))
    }
    pub fn as_integer_sample(&self, source_tick: u64) -> Result<u64> {
        self.map_source_tick(source_tick)?.require_whole_sample()
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SemanticSoundEvent {
    Focus,
    Transition,
    Tension,
    Release,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SoundCue {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub source_revision_sha256: String,
    pub description: String,
    pub source_tick: u64,
    pub semantic_event: SemanticSoundEvent,
    pub human_approved: bool,
}
impl SoundCue {
    pub fn validate(&self, clock: &CreativeClockMap) -> Result<()> {
        clock.validate()?;
        text(
            &self.description,
            256,
            "Sound cues require a short human-readable semantic purpose",
        )?;
        check(
            valid_sha(&self.source_revision_sha256)
                && self.source_revision_sha256 == clock.source_revision_sha256,
            "Sound cue belongs to another approved source revision",
        )?;
        check(
            self.source_tick <= clock.anchors.last().expect("validated clock").source_tick,
            "Cue is beyond the admitted source clock",
        )?;
        check(
            self.human_approved,
            "Unapproved semantic audio cues are proposals, never automatically mixed",
        )?;
        Ok(())
    }
}
/// Place an original first-party focus/transition score at a semantic event.
/// No voice, music or third-party media can be invented by scheduling it.
pub fn schedule_original_sound(
    source: &SoundPlan,
    cue: &SoundCue,
    clock: &CreativeClockMap,
    scene_samples: u64,
) -> Result<SoundPlan> {
    source.validate()?;
    clock.validate()?;
    cue.validate(clock)?;
    check(
        matches!(source.recipe, RecipeId::FocusHit | RecipeId::TransitionTail)
            && !source.applies_to_existing_bus,
        "Scheduling accepts only original focus or transition sound cues",
    )?;
    check(
        scene_samples >= 48000 && scene_samples <= 48000 * 600,
        "Sound cue scene duration is out of bounds",
    )?;
    check(
        matches!(
            (cue.semantic_event, source.recipe),
            (SemanticSoundEvent::Focus, RecipeId::FocusHit)
                | (SemanticSoundEvent::Transition, RecipeId::TransitionTail)
        ),
        "A cue cannot attach an unrelated sound effect just because an object moved",
    )?;
    let sample = clock.as_integer_sample(cue.source_tick)?;
    let mut placed = source.clone();
    placed.sample_frames = scene_samples;
    for voice in &mut placed.voices {
        voice.at_sample = sample
            .checked_add(voice.at_sample)
            .ok_or_else(|| CraftError("Audio cue offset overflow".into()))?;
    }
    for point in &mut placed.gain_envelope {
        point.sample = if point.sample == 0 {
            0
        } else if point.sample == source.sample_frames - 1 {
            scene_samples - 1
        } else {
            return Err(CraftError(
                "Nontrivial source envelope requires an explicit retiming choice".into(),
            ));
        };
    }
    placed.validate()?;
    Ok(placed)
}
