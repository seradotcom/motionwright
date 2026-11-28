//! Native original SFX PCM16 mixer, backed by owner-local StudioService WAV bytes.
//! This prepares actual stereo samples for the existing Semwright MLT AV
//! mux; it is not an independent backend, a completed MP4, rights
//! clearance, LUFS master or proof of real customer audio.
use motionwright_domain::{Project, RationalTime, SFX_SAMPLES_PER_FRAME, SemanticSfxCue};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, ops::Range, sync::Arc, time::Instant};
use uuid::Uuid;

pub const SFX_MAX_MASTER_FRAMES: u64 = 900;

#[derive(Clone)]
pub struct VerifiedSfxWav {
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub bytes: Vec<u8>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MixedSfxWav {
    pub wav_bytes: Vec<u8>,
    pub output_sha256: String,
    pub rendered_frames: u64,
    pub original_sources: usize,
    pub sample_peak_pcm16: u16,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SemanticSfxMixWork {
    pub synthesized_sample_frames: u64,
    pub reused_sample_frames: u64,
    pub cas_source_bytes_read: Option<u64>,
    pub cas_read_and_verify_micros: Option<u64>,
    pub source_bytes_verified: u64,
    pub pcm_bytes_encoded: u64,
    pub complete_wav_bytes_hashed: u64,
    pub validation_micros: u64,
    pub synthesis_and_encoding_micros: u64,
    pub complete_wav_pack_micros: u64,
    pub complete_wav_hash_micros: u64,
}

#[derive(Default, Clone)]
pub struct SemanticSfxAuditionCache {
    retained: Option<Arc<RetainedAuditionBus>>,
    last_work: Option<SemanticSfxMixWork>,
}

#[derive(PartialEq, Eq)]
struct AuditionScope {
    project_id: Uuid,
    generation: Uuid,
    branch: Uuid,
    profile: Uuid,
    included_scene_ids: Vec<Uuid>,
    timeline: Vec<(Uuid, RationalTime, RationalTime)>,
    total_frames: u64,
}

#[derive(PartialEq, Eq)]
struct CueSnapshot {
    cue: SemanticSfxCue,
    samples: Range<usize>,
}

struct RetainedAuditionBus {
    scope: AuditionScope,
    revision: u64,
    cues: Vec<CueSnapshot>,
    wav_bytes: Vec<u8>,
}

struct MixCue<'a> {
    cue: &'a SemanticSfxCue,
    samples: Range<usize>,
    wav_bytes: &'a [u8],
}

impl SemanticSfxAuditionCache {
    /// A process-local audition only. Every call validates the complete
    /// current source inventory before consulting the retained PCM.
    pub fn mix(
        &mut self,
        project: &Project,
        deliverable_id: Uuid,
        sources: &[VerifiedSfxWav],
        total_frames: u64,
    ) -> NativeResult<MixedSfxWav> {
        mix_source_bound_semantic_sfx_bounded(
            project,
            deliverable_id,
            sources,
            total_frames,
            15 * 30,
            Some(self),
        )
    }

    pub fn last_work(&self) -> Option<&SemanticSfxMixWork> {
        self.last_work.as_ref()
    }

    pub(crate) fn record_cas_read(&mut self, bytes: u64, micros: u64) {
        if let Some(work) = &mut self.last_work {
            work.cas_source_bytes_read = Some(bytes);
            work.cas_read_and_verify_micros = Some(micros);
        }
    }
}

fn invalid(s: &str) -> Error {
    Error::new(ErrorCode::InvalidArgument, s)
}
fn unsupported(s: &str) -> Error {
    Error::new(ErrorCode::Unsupported, s)
}
fn u16at(b: &[u8], i: usize) -> u16 {
    u16::from_le_bytes([b[i], b[i + 1]])
}
fn u32at(b: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
}
fn verify_source(bytes: &[u8], frames: usize) -> NativeResult<()> {
    let size = frames
        .checked_mul(4)
        .ok_or_else(|| invalid("SFX source frame size overflow"))?;
    if bytes.len() != 44 + size
        || &bytes[0..4] != b"RIFF"
        || u32at(bytes, 4) != u32::try_from(bytes.len() - 8).unwrap_or(u32::MAX)
        || &bytes[8..16] != b"WAVEfmt "
        || u32at(bytes, 16) != 16
        || u16at(bytes, 20) != 1
        || u16at(bytes, 22) != 2
        || u32at(bytes, 24) != 48000
        || u32at(bytes, 28) != 192000
        || u16at(bytes, 32) != 4
        || u16at(bytes, 34) != 16
        || &bytes[36..40] != b"data"
        || usize::try_from(u32at(bytes, 40)).unwrap_or(usize::MAX) != size
    {
        return Err(unsupported(
            "SFX source must be one exact 48 kHz stereo PCM16 RIFF with no implicit resampling, trimming or codec conversion",
        ));
    }
    Ok(())
}
pub fn mix_source_bound_semantic_sfx(
    project: &Project,
    deliverable_id: Uuid,
    sources: &[VerifiedSfxWav],
    total_frames: u64,
) -> NativeResult<MixedSfxWav> {
    mix_source_bound_semantic_sfx_bounded(
        project,
        deliverable_id,
        sources,
        total_frames,
        SFX_MAX_MASTER_FRAMES,
        None,
    )
}

pub fn mix_source_bound_semantic_sfx_with_budget(
    project: &Project,
    deliverable_id: Uuid,
    sources: &[VerifiedSfxWav],
    total_frames: u64,
    budget: motionwright_service::OriginalMixDecodeBudget,
) -> NativeResult<MixedSfxWav> {
    let max_frames = match budget {
        motionwright_service::OriginalMixDecodeBudget::Audition15Seconds => 15 * 30,
        motionwright_service::OriginalMixDecodeBudget::FullCut180Seconds => 180 * 30,
    };
    mix_source_bound_semantic_sfx_bounded(
        project,
        deliverable_id,
        sources,
        total_frames,
        max_frames,
        None,
    )
}

fn mix_source_bound_semantic_sfx_bounded(
    project: &Project,
    deliverable_id: Uuid,
    sources: &[VerifiedSfxWav],
    total_frames: u64,
    max_frames: u64,
    cache: Option<&mut SemanticSfxAuditionCache>,
) -> NativeResult<MixedSfxWav> {
    let validation_started = Instant::now();
    if !(1..=max_frames).contains(&total_frames) {
        return Err(unsupported(
            "Native semantic SFX exceeds its explicit bounded source policy",
        ));
    }
    project
        .validate()
        .map_err(|_| invalid("Invalid SFX source project"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|p| p.id == deliverable_id)
        .ok_or_else(|| invalid("Missing source-bound audio delivery profile"))?;
    if profile.frame_rate.num != 30
        || profile.frame_rate.den != 1
        || profile.audio_sample_rate_hz != 48000
    {
        return Err(unsupported(
            "SFX only supports 30 fps and 48 kHz without implicit conversion",
        ));
    }
    let expected_frames = project
        .sfx_cut_frames(deliverable_id)
        .map_err(|_| unsupported("SFX master cut must use exact 30 fps source times"))?;
    if total_frames != expected_frames {
        return Err(unsupported(
            "SFX master duration differs from the exact selected cut; no implicit padding or trim",
        ));
    }
    let schedule = project
        .validated_sfx_schedule_for_profile(deliverable_id)
        .map_err(|_| invalid("Invalid source SFX schedule"))?;
    if schedule.is_empty() || schedule.iter().any(|p| p.absolute_end_frame > total_frames) {
        return Err(unsupported(
            "SFX sources exceed selected native master or no cue was authored",
        ));
    }
    let mut ids = HashSet::new();
    for source in sources {
        if !ids.insert(source.asset_id)
            || hex::encode(Sha256::digest(&source.bytes)) != source.source_sha256
        {
            return Err(invalid(
                "Original WAV asset identity or contents differ from imported SHA-256",
            ));
        }
    }
    let expected_sources = project
        .audio
        .sfx_cues
        .iter()
        .filter(|cue| schedule.iter().any(|placement| placement.cue_id == cue.id))
        .map(|cue| cue.source_asset_id)
        .collect::<HashSet<_>>();
    if ids != expected_sources {
        return Err(invalid(
            "Verified SFX source inventory differs from the selected authored cues",
        ));
    }
    let total_samples = usize::try_from(total_frames)
        .ok()
        .and_then(|n| n.checked_mul(SFX_SAMPLES_PER_FRAME as usize))
        .ok_or_else(|| invalid("SFX output sample count overflow"))?;
    let cues = project
        .audio
        .sfx_cues
        .iter()
        .filter_map(|cue| {
            schedule
                .iter()
                .find(|placement| placement.cue_id == cue.id)
                .map(|placement| (cue, placement))
        })
        .map(|(cue, placement)| {
            let source = sources
                .iter()
                .find(|source| {
                    source.asset_id == cue.source_asset_id
                        && source.source_sha256 == cue.source_sha256
                })
                .ok_or_else(|| invalid("No owner-verified WAV bytes for authored semantic cue"))?;
            let count = usize::try_from(placement.sample_count)
                .map_err(|_| invalid("Cue source frames exceed memory budget"))?;
            verify_source(&source.bytes, count)?;
            let start = usize::try_from(placement.first_sample)
                .map_err(|_| invalid("Cue offset exceeds native sample budget"))?;
            let end = start
                .checked_add(count)
                .filter(|end| *end <= total_samples)
                .ok_or_else(|| invalid("Cue source exceeds its exact output cut"))?;
            Ok(MixCue {
                cue,
                samples: start..end,
                wav_bytes: &source.bytes,
            })
        })
        .collect::<NativeResult<Vec<_>>>()?;
    let scope = cache.as_ref().map(|_| AuditionScope {
        project_id: project.id,
        generation: project.generation,
        branch: project.active_branch,
        profile: deliverable_id,
        included_scene_ids: profile.included_scene_ids.clone(),
        timeline: project
            .scenes
            .iter()
            .map(|scene| (scene.id, scene.start, scene.duration))
            .collect(),
        total_frames,
    });
    let snapshot = cache.as_ref().map(|_| {
        cues.iter()
            .map(|cue| CueSnapshot {
                cue: cue.cue.clone(),
                samples: cue.samples.clone(),
            })
            .collect::<Vec<_>>()
    });
    let same_head = cache
        .as_ref()
        .and_then(|cache| cache.retained.as_deref())
        .filter(|old| scope.as_ref() == Some(&old.scope) && old.revision <= project.revision);
    let reuse = same_head.and_then(|old| {
        dirty_sample_ranges(&old.cues, snapshot.as_deref()?).map(|ranges| (old, ranges))
    });
    let dirty = reuse.as_ref().map_or_else(
        || std::iter::once(0..total_samples).collect(),
        |(_, ranges)| ranges.clone(),
    );
    let validation_micros = elapsed_micros(validation_started);
    let synthesis_started = Instant::now();
    let mut synthesized_samples = 0;
    let mut pcm = reuse.map_or_else(
        || vec![0; total_samples * 4],
        |(old, _)| old.wav_bytes[44..].to_vec(),
    );
    let mut peak = 0;
    for range in &dirty {
        peak = peak.max(synthesize_pcm(
            &cues,
            range,
            &mut pcm[range.start * 4..range.end * 4],
        )?);
        synthesized_samples += range.len() as u64;
    }
    let synthesis_and_encoding_micros = elapsed_micros(synthesis_started);
    if synthesized_samples < total_samples as u64 {
        // Reused samples also contribute to the complete output peak.
        peak = pcm
            .as_chunks::<2>()
            .0
            .iter()
            .map(|bytes| i16::from_le_bytes(*bytes).unsigned_abs())
            .max()
            .unwrap_or(0);
    }
    let pack_started = Instant::now();
    let data_bytes = u32::try_from(pcm.len()).map_err(|_| invalid("WAV too large"))?;
    let mut wav = Vec::with_capacity(44 + data_bytes as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(data_bytes + 36).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&48000_u32.to_le_bytes());
    wav.extend_from_slice(&192000_u32.to_le_bytes());
    wav.extend_from_slice(&4_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_bytes.to_le_bytes());
    wav.extend_from_slice(&pcm);
    let complete_wav_pack_micros = elapsed_micros(pack_started);
    let hash_started = Instant::now();
    let sha = hex::encode(Sha256::digest(&wav));
    let complete_wav_hash_micros = elapsed_micros(hash_started);
    if let (Some(cache), Some(scope), Some(cues)) = (cache, scope, snapshot) {
        cache.last_work = Some(SemanticSfxMixWork {
            synthesized_sample_frames: synthesized_samples,
            reused_sample_frames: total_samples as u64 - synthesized_samples,
            cas_source_bytes_read: None,
            cas_read_and_verify_micros: None,
            source_bytes_verified: sources.iter().map(|source| source.bytes.len() as u64).sum(),
            pcm_bytes_encoded: synthesized_samples * 4,
            complete_wav_bytes_hashed: wav.len() as u64,
            validation_micros,
            synthesis_and_encoding_micros,
            complete_wav_pack_micros,
            complete_wav_hash_micros,
        });
        cache.retained = Some(Arc::new(RetainedAuditionBus {
            scope,
            revision: project.revision,
            cues,
            wav_bytes: wav.clone(),
        }));
    }
    Ok(MixedSfxWav {
        wav_bytes: wav,
        output_sha256: sha,
        rendered_frames: total_frames,
        original_sources: ids.len(),
        sample_peak_pcm16: peak,
    })
}

fn elapsed_micros(start: Instant) -> u64 {
    start.elapsed().as_micros().min(u128::from(u64::MAX)) as u64
}

fn dirty_sample_ranges(before: &[CueSnapshot], after: &[CueSnapshot]) -> Option<Vec<Range<usize>>> {
    let common_ids = |from: &[CueSnapshot], in_other: &[CueSnapshot]| {
        from.iter()
            .filter(|cue| in_other.iter().any(|other| other.cue.id == cue.cue.id))
            .map(|cue| cue.cue.id)
            .collect::<Vec<_>>()
    };
    // Floating-point accumulation preserves the saved authored cue order.
    // A changed relative order invalidates the entire retained bus.
    if common_ids(before, after) != common_ids(after, before) {
        return None;
    }
    let mut dirty = Vec::new();
    for (from, in_other) in [(before, after), (after, before)] {
        dirty.extend(
            from.iter()
                .filter(|cue| !in_other.contains(cue))
                .map(|cue| cue.samples.clone()),
        );
    }
    dirty.sort_unstable_by_key(|range| (range.start, range.end));
    let mut merged: Vec<Range<usize>> = Vec::new();
    for range in dirty {
        if let Some(last) = merged.last_mut().filter(|last| last.end >= range.start) {
            last.end = last.end.max(range.end);
        } else {
            merged.push(range);
        }
    }
    Some(merged)
}

fn synthesize_pcm(cues: &[MixCue<'_>], range: &Range<usize>, pcm: &mut [u8]) -> NativeResult<u16> {
    let mut mixed = vec![0_f64; range.len() * 2];
    for entry in cues {
        let start = range.start.max(entry.samples.start);
        let end = range.end.min(entry.samples.end);
        if start >= end {
            continue;
        }
        let attack = usize::from(entry.cue.attack_frames) * SFX_SAMPLES_PER_FRAME as usize;
        let release = usize::from(entry.cue.release_frames) * SFX_SAMPLES_PER_FRAME as usize;
        let gain = 10_f64.powf(f64::from(entry.cue.gain_milli_db) / 20_000.0);
        for at in start..end {
            let i = at - entry.samples.start;
            let incoming = if attack == 0 {
                1.0
            } else {
                (i as f64 / attack as f64).min(1.0)
            };
            let outgoing = if release == 0 {
                1.0
            } else {
                ((entry.samples.end - at - 1) as f64 / release as f64).min(1.0)
            };
            let scale = gain * incoming.min(outgoing);
            for channel in 0..2 {
                let k = (i * 2 + channel) * 2 + 44;
                let sample = i16::from_le_bytes([entry.wav_bytes[k], entry.wav_bytes[k + 1]]);
                mixed[(at - range.start) * 2 + channel] += f64::from(sample) * scale;
            }
        }
    }
    let mut peak = 0;
    for (out, value) in pcm.as_chunks_mut::<2>().0.iter_mut().zip(mixed) {
        // Signed PCM16 includes -32768. Reject only values outside its
        // exact representable range after nearest-sample quantization.
        let rounded = value.round();
        if !rounded.is_finite() || !(-32768.0..=32767.0).contains(&rounded) {
            return Err(unsupported(
                "SFX source mix would clip; reduce gain or overlap, no hidden limiter",
            ));
        }
        let sample = rounded as i16;
        peak = peak.max(sample.unsigned_abs());
        *out = sample.to_le_bytes();
    }
    Ok(peak)
}
