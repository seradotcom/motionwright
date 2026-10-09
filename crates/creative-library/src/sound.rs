use crate::*;
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Oscillator {
    Sine,
    Triangle,
    SoftNoise,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SynthVoice {
    pub id: Uuid,
    pub at_sample: u64,
    pub duration_samples: u64,
    pub oscillator: Oscillator,
    pub frequency_start_hz: f64,
    pub frequency_end_hz: f64,
    pub amplitude: f64,
    pub attack_samples: u32,
    pub release_samples: u32,
    pub pan: f64,
    pub seed: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct GainPoint {
    pub sample: u64,
    pub amplitude: f64,
    pub curve: native::Curve,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SoundPlan {
    pub version: u32,
    pub instance_id: Uuid,
    pub recipe: RecipeId,
    pub sample_rate: u32,
    pub channels: u8,
    pub sample_frames: u64,
    pub voices: Vec<SynthVoice>,
    pub gain_envelope: Vec<GainPoint>,
    pub applies_to_existing_bus: bool,
    pub source_classification: String,
}
impl SoundPlan {
    pub fn validate(&self) -> Result<()> {
        check(
            self.version == 1
                && self.sample_rate == 48000
                && self.channels == 2
                && self.sample_frames > 0
                && self.sample_frames <= 48000 * 600,
            "Unsupported or unbounded native sound clock",
        )?;
        check(
            self.voices.len() <= 32
                && self.gain_envelope.len() >= 2
                && self.gain_envelope.len() <= 256,
            "Sound voice or envelope budget exceeded",
        )?;
        check(
            self.applies_to_existing_bus == self.voices.is_empty(),
            "Silence-control is an envelope on an existing bus, never synthetic audible source",
        )?;
        let mut ids = std::collections::BTreeSet::new();
        for voice in &self.voices {
            check(
                ids.insert(voice.id)
                    && voice.duration_samples > 0
                    && voice
                        .at_sample
                        .checked_add(voice.duration_samples)
                        .is_some_and(|end| end <= self.sample_frames),
                "Duplicate or out-of-bounds sound event",
            )?;
            check(
                voice.amplitude.is_finite()
                    && (0.0..=0.5).contains(&voice.amplitude)
                    && voice.pan.is_finite()
                    && (-1.0..=1.0).contains(&voice.pan),
                "Unsafe sound amplitude/pan",
            )?;
            for frequency in [voice.frequency_start_hz, voice.frequency_end_hz] {
                check(
                    frequency.is_finite() && (20.0..=10000.0).contains(&frequency),
                    "Unbounded sound frequency",
                )?;
            }
            check(
                u64::from(voice.attack_samples) + u64::from(voice.release_samples)
                    <= voice.duration_samples
                    && voice.attack_samples > 0
                    && voice.release_samples > 0,
                "Native sound needs nonoverlapping click-free attack/release",
            )?;
        }
        check(
            self.gain_envelope[0].sample == 0
                && self
                    .gain_envelope
                    .last()
                    .is_some_and(|p| p.sample == self.sample_frames - 1),
            "Envelope must span the complete sample interval",
        )?;
        check(
            self.gain_envelope
                .windows(2)
                .all(|pair| pair[0].sample < pair[1].sample),
            "Envelope keys must be strictly ordered",
        )?;
        for p in &self.gain_envelope {
            check(
                p.amplitude.is_finite() && (0.0..=1.0).contains(&p.amplitude),
                "Envelope gain outside [0,1]",
            )?;
        }
        Ok(())
    }
}
pub fn sound_component(request: &ComponentRequest) -> Result<SoundPlan> {
    request.validate()?;
    check(
        request.recipe.definition().backend == RecipeBackend::AudioScore,
        "Recipe does not produce a native sound plan",
    )?;
    let samples = (u64::from(request.output.frames) * 48000 * u64::from(request.output.rate.den))
        .div_ceil(u64::from(request.output.rate.num));
    check(
        (48000..=48000 * 600).contains(&samples),
        "Sound requires 1..600 seconds of explicit context",
    )?;
    let envelope = |sample, amplitude| GainPoint {
        sample,
        amplitude,
        curve: native::Curve::EaseInOut,
    };
    let voice = |index: u64,
                 at: u64,
                 duration: u64,
                 oscillator,
                 frequency_start_hz,
                 frequency_end_hz,
                 amplitude,
                 pan| SynthVoice {
        id: stable_id(request.instance_id, &format!("sound-{index}")),
        at_sample: at,
        duration_samples: duration,
        oscillator,
        frequency_start_hz,
        frequency_end_hz,
        amplitude,
        attack_samples: (duration / 16).clamp(1, 240) as u32,
        release_samples: (duration / 2).clamp(1, 12000) as u32,
        pan,
        seed: request.seed.wrapping_add(index),
    };
    let (voices, gain, applies) = match request.recipe {
        RecipeId::FocusHit => (
            vec![
                voice(0, 0, 9600, Oscillator::Sine, 520.0, 220.0, 0.09, -0.08),
                voice(
                    1,
                    720,
                    7200,
                    Oscillator::Triangle,
                    1040.0,
                    780.0,
                    0.012,
                    0.12,
                ),
            ],
            vec![envelope(0, 1.0), envelope(samples - 1, 1.0)],
            false,
        ),
        RecipeId::TransitionTail => {
            let duration = samples.min(72000);
            (
                vec![
                    voice(
                        0,
                        0,
                        duration,
                        Oscillator::SoftNoise,
                        240.0,
                        90.0,
                        0.065,
                        -0.1,
                    ),
                    voice(1, 0, duration, Oscillator::Sine, 155.0, 110.0, 0.028, 0.12),
                ],
                vec![envelope(0, 1.0), envelope(samples - 1, 1.0)],
                false,
            )
        }
        RecipeId::EnergyBed => (
            vec![
                voice(0, 0, samples, Oscillator::Sine, 130.81, 130.81, 0.024, -0.4),
                voice(1, 0, samples, Oscillator::Sine, 196.0, 196.0, 0.016, 0.4),
                voice(2, 0, samples, Oscillator::Sine, 261.63, 261.63, 0.010, 0.0),
            ],
            vec![
                envelope(0, 0.0),
                envelope(samples / 5, 1.0),
                envelope(samples * 4 / 5, 1.0),
                envelope(samples - 1, 0.0),
            ],
            false,
        ),
        RecipeId::SilenceRelease => (
            vec![],
            vec![
                envelope(0, 1.0),
                envelope(samples / 3, 1.0),
                envelope(samples * 2 / 3, 0.0),
                envelope(samples - 1, 0.0),
            ],
            true,
        ),
        _ => return Err(CraftError("Unknown sound component".into())),
    };
    let plan = SoundPlan {
        version: 1,
        instance_id: request.instance_id,
        recipe: request.recipe,
        sample_rate: 48000,
        channels: 2,
        sample_frames: samples,
        voices,
        gain_envelope: gain,
        applies_to_existing_bus: applies,
        source_classification: if applies {
            "editable_gain_automation_requires_an_existing_bus"
        } else {
            "original_deterministic_synthetic_sound_not_licensed_music"
        }
        .into(),
    };
    plan.validate()?;
    Ok(plan)
}
fn interpolate(t: f64, curve: native::Curve) -> f64 {
    match curve {
        native::Curve::Hold => 0.0,
        native::Curve::Linear => t,
        native::Curve::EaseOutCubic => 1.0 - (1.0 - t).powi(3),
        native::Curve::EaseInOut => t * t * (3.0 - 2.0 * t),
        native::Curve::CubicBezier { x1, y1, x2, y2 } => {
            let cubic = |u: f64, a: f64, b: f64| {
                3.0 * (1.0 - u).powi(2) * u * a + 3.0 * (1.0 - u) * u * u * b + u * u * u
            };
            let (mut lo, mut hi) = (0.0, 1.0);
            for _ in 0..40 {
                let mid = (lo + hi) / 2.0;
                if cubic(mid, x1, x2) < t {
                    lo = mid
                } else {
                    hi = mid
                }
            }
            cubic((lo + hi) / 2.0, y1, y2)
        }
    }
}
pub fn gain_at(points: &[GainPoint], sample: u64) -> f64 {
    let Some(first) = points.first() else {
        return 1.0;
    };
    if sample <= first.sample {
        return first.amplitude;
    }
    for pair in points.windows(2) {
        if sample < pair[1].sample {
            let t = (sample - pair[0].sample) as f64 / (pair[1].sample - pair[0].sample) as f64;
            return pair[0].amplitude
                + (pair[1].amplitude - pair[0].amplitude) * interpolate(t, pair[1].curve);
        }
    }
    points.last().map_or(1.0, |p| p.amplitude)
}
fn noise(seed: u64, sample: u64) -> f64 {
    let mut x = seed.wrapping_add(sample.wrapping_mul(0x9e3779b97f4a7c15));
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    x ^= x >> 31;
    (x >> 11) as f64 / 4503599627370496.0 - 1.0
}
pub fn render_sound_block(plan: &SoundPlan, start: u64, frames: usize) -> Result<Vec<[f32; 2]>> {
    plan.validate()?;
    check(
        !plan.applies_to_existing_bus,
        "A silence-release envelope must be applied to a named existing bus",
    )?;
    check(
        frames <= 65536
            && start
                .checked_add(frames as u64)
                .is_some_and(|end| end <= plan.sample_frames),
        "Sound block outside the admitted sample interval",
    )?;
    let mut output = vec![[0.0_f32; 2]; frames];
    for (offset, frame) in output.iter_mut().enumerate() {
        let absolute = start + offset as u64;
        let mut stereo = [0.0_f64; 2];
        for voice in &plan.voices {
            if absolute < voice.at_sample || absolute >= voice.at_sample + voice.duration_samples {
                continue;
            }
            let local = absolute - voice.at_sample;
            let duration = voice.duration_samples as f64 / 48000.0;
            let time = local as f64 / 48000.0;
            let phase = std::f64::consts::TAU
                * (voice.frequency_start_hz * time
                    + (voice.frequency_end_hz - voice.frequency_start_hz) * time * time
                        / (2.0 * duration));
            let raw = match voice.oscillator {
                Oscillator::Sine => phase.sin(),
                Oscillator::Triangle => (2.0 / std::f64::consts::PI) * phase.sin().asin(),
                Oscillator::SoftNoise => (0..16)
                    .map(|tap| {
                        noise(voice.seed, local.saturating_sub(tap)) * (16 - tap) as f64 / 136.0
                    })
                    .sum::<f64>(),
            };
            let attack = (local as f64 / f64::from(voice.attack_samples)).clamp(0.0, 1.0);
            let release = ((voice.duration_samples - 1 - local) as f64
                / f64::from(voice.release_samples))
            .clamp(0.0, 1.0);
            let sample = raw
                * voice.amplitude
                * (attack * std::f64::consts::FRAC_PI_2).sin().powi(2)
                * (release * std::f64::consts::FRAC_PI_2).sin().powi(2);
            let angle = (voice.pan + 1.0) * std::f64::consts::FRAC_PI_4;
            stereo[0] += sample * angle.cos();
            stereo[1] += sample * angle.sin();
        }
        let gain = gain_at(&plan.gain_envelope, absolute);
        for channel in 0..2 {
            let sample = stereo[channel] * gain;
            check(
                sample.abs() < 1.0 && sample.is_finite(),
                "Synthetic sound would clip",
            )?;
            frame[channel] = ((sample * 1_000_000_000.0).round() / 1_000_000_000.0) as f32;
        }
    }
    Ok(output)
}
