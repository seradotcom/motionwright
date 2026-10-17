//! Original-mix sources and native AV from the current canonical delivery recipe.
//! Raw source preparation stays separate from native effect authority. Only
//! the canonical coordinator entry admits the current Film, original measured
//! voice/captions and mixed WAV before the existing Semwright native mux.
use super::{
    MltAudioArtifact, MltVerifiedLosslessTimeline, MotionCanvasRenderEvidence,
    ProductionCoordinator,
    mlt_multisegment_av::{VerifiedMuxInputs, verify_multisegment_visual_source},
    read_verified_production_artifact, safe_metadata, storage_error,
};
use crate::{
    film::{
        FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
        SceneFilmIntent,
    },
    mlt_av_audio::{MultiSegmentAudioReadiness, preflight_multi_segment_voice_source},
    original_mix::{
        OriginalMixEvidence, OriginalMixPlan, PcmStemReceipt,
        original_mix_plan_from_canonical_recipe, write_mix_wav,
    },
    sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx},
};
use motionwright_domain::{
    AudioCodec, DeliveryArchetype, DeliveryNarrativeRole, OutputColorSpace, OutputContainer,
    Project, RevisionStamp, VariantDependencyFingerprint, VideoCodec,
    variant_dependency_fingerprint,
};
use motionwright_service::{OriginalMixDecodedPcm, StudioService};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult, Value};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, io::Write, path::Path};
use uuid::Uuid;

const SAMPLE_RATE: u64 = 48_000;
const MAX_SAMPLE_FRAMES: u64 = SAMPLE_RATE * 15;
const MAX_MIX_WAV_BYTES: u64 = 44 + MAX_SAMPLE_FRAMES * 4;
const MAX_SFX_SOURCE_ASSETS: usize = 64;
const MAX_SFX_SOURCE_BYTES: u64 = 16 * 1024 * 1024;
const SOURCE_SCOPE: &str =
    "verified-owner-original-mix-source-only-not-canonical-delivery-or-native-master";

fn unsupported(reason: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, reason)
}

fn stale(reason: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, reason)
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

/// Exact requested video/PCM clock. This does not prove that video exists.
pub fn original_mix_sample_clock(
    frame_count: u64,
    fps_num: u32,
    fps_den: u32,
) -> NativeResult<u64> {
    if frame_count == 0 || fps_num == 0 || fps_den == 0 {
        return Err(unsupported(
            "Original mix requires a positive rational frame clock",
        ));
    }
    let numerator = u128::from(frame_count)
        .checked_mul(u128::from(SAMPLE_RATE))
        .and_then(|value| value.checked_mul(u128::from(fps_den)))
        .ok_or_else(|| unsupported("Original mix sample clock overflow"))?;
    let denominator = u128::from(fps_num);
    if !numerator.is_multiple_of(denominator) {
        return Err(unsupported(
            "Original mix frame clock crosses a fractional PCM sample; retiming is required",
        ));
    }
    let samples = u64::try_from(numerator / denominator)
        .map_err(|_| unsupported("Original mix sample clock exceeds its bounded duration"))?;
    if !(SAMPLE_RATE..=MAX_SAMPLE_FRAMES).contains(&samples) {
        return Err(unsupported(
            "Original mix sources support exactly one to fifteen seconds",
        ));
    }
    Ok(samples)
}

/// Typed native source request. It is intentionally not deserializable from
/// a WebView and contains no input/output paths or supplied PCM bytes.
#[derive(Clone, Copy)]
pub struct OriginalMixSourceRequest<'a> {
    pub plan: &'a OriginalMixPlan,
    pub frame_count: u64,
    pub fps_num: u32,
    pub fps_den: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OriginalMixSfxSourceReceipt {
    pub asset_id: Uuid,
    pub source_asset_sha256: String,
}

/// The selected SFX bus was compared with every sample of the current
/// authored cue schedule, reconstructed by the existing native SFX mixer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OriginalMixSemanticSfxProof {
    pub cue_count: usize,
    pub sources: Vec<OriginalMixSfxSourceReceipt>,
    pub cue_schedule_sha256: String,
    pub reconstructed_wav_sha256: String,
    pub reconstructed_pcm_sha256: String,
}

/// Physical source proof, not a canonical recipe grant or a video receipt.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct OriginalMixSourceReadiness {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub frame_count: u64,
    pub fps_num: u32,
    pub fps_den: u32,
    pub measured_voice: PcmStemReceipt,
    pub original_music: PcmStemReceipt,
    pub original_sfx: PcmStemReceipt,
    /// None proves only the selected explicit SFX bus. It does not certify
    /// that audio from a former semantic cue schedule has been removed.
    pub semantic_sfx: Option<OriginalMixSemanticSfxProof>,
    pub original_mix: OriginalMixEvidence,
    /// Hash of the whole RIFF file, distinct from original_mix's PCM hash.
    pub staged_wav_sha256: String,
    pub staged_wav_bytes: u64,
    pub evidence_scope: String,
}

#[derive(Debug, Clone)]
pub struct PreparedOriginalMixWav {
    pub proof: OriginalMixSourceReadiness,
    pub audio: MltAudioArtifact,
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = service.project(project_id).map_err(storage_error)?;
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(stale(
            "Original mix source belongs to a different project version",
        ));
    }
    project
        .validate()
        .map_err(|_| unsupported("Original mix project failed semantic validation"))?;
    Ok(project)
}

fn check_request(project: &Project, request: OriginalMixSourceRequest<'_>) -> NativeResult<()> {
    let plan = request.plan;
    plan.validate(project)
        .map_err(|_| unsupported("Original mix plan does not match the current source project"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == plan.deliverable_profile_id)
        .ok_or_else(|| stale("Original mix selected delivery profile is absent"))?;
    if profile.video_codec != VideoCodec::H264
        || profile.audio_codec != AudioCodec::Aac
        || profile.container != OutputContainer::Mp4
        || profile.color_space != OutputColorSpace::Rec709
        || profile.audio_sample_rate_hz != 48_000
        || profile.frame_rate.num != i64::from(request.fps_num)
        || profile.frame_rate.den != i64::from(request.fps_den)
        || profile.voice_track_id.is_none()
        || profile.voice_track_id != project.audio.active_voice_track_id
        || plan.sample_peak_guard_dbfs != -1.0
        || !project.audio.cues.is_empty()
    {
        return Err(unsupported(
            "Original mix AV source requires the selected 48 kHz H264/AAC profile, measured voice and fixed sample guard without additional audio cues",
        ));
    }
    if original_mix_sample_clock(request.frame_count, request.fps_num, request.fps_den)?
        != plan.sample_frames
    {
        return Err(unsupported(
            "Original mix samples differ from the exact requested video duration",
        ));
    }
    if !project.audio.sfx_cues.is_empty()
        && (request.fps_num != 30 || request.fps_den != 1 || !profile.included_scene_ids.is_empty())
    {
        return Err(unsupported(
            "Authored semantic SFX requires the complete original 30 fps scene order",
        ));
    }
    Ok(())
}

fn checked_stem(
    service: &StudioService,
    project: &Project,
    receipt: &PcmStemReceipt,
) -> NativeResult<OriginalMixDecodedPcm> {
    let decoded = service
        .decoded_stem_for_original_mix(project.id, project.revision, receipt.asset_id)
        .map_err(storage_error)?;
    if decoded.original_asset_sha256 != receipt.source_asset_sha256
        || decoded.decoded_pcm_sha256 != receipt.decoded_pcm_sha256
        || decoded.sample_frames != receipt.sample_frames
        || decoded.interleaved_s16le.len() as u64 != receipt.sample_frames * 4
        || sha256(&decoded.interleaved_s16le) != receipt.decoded_pcm_sha256
    {
        return Err(stale(
            "Original mix receipt differs from actual project CAS audio",
        ));
    }
    Ok(decoded)
}

fn verify_semantic_sfx_bus(
    service: &StudioService,
    project: &Project,
    expected: &RevisionStamp,
    request: OriginalMixSourceRequest<'_>,
    decoded_sfx: &OriginalMixDecodedPcm,
) -> NativeResult<Option<OriginalMixSemanticSfxProof>> {
    if project.audio.sfx_cues.is_empty() {
        return Ok(None);
    }
    let mut sources = BTreeMap::new();
    for cue in &project.audio.sfx_cues {
        if sources
            .insert(cue.source_asset_id, cue.source_sha256.clone())
            .is_some_and(|previous| previous != cue.source_sha256)
        {
            return Err(stale(
                "Original SFX asset has conflicting cue source digests",
            ));
        }
    }
    if sources.len() > MAX_SFX_SOURCE_ASSETS {
        return Err(unsupported(
            "Original mix semantic SFX exceeds the source asset budget",
        ));
    }
    let mut bytes_total = 0_u64;
    let mut verified = Vec::with_capacity(sources.len());
    let mut receipts = Vec::with_capacity(sources.len());
    for (asset_id, digest) in sources {
        let bytes = service
            .verified_semantic_sfx_wav(project.id, expected, asset_id, &digest)
            .map_err(storage_error)?;
        bytes_total = bytes_total
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| unsupported("Original mix SFX source byte count overflow"))?;
        if bytes_total > MAX_SFX_SOURCE_BYTES {
            return Err(unsupported(
                "Original mix semantic SFX exceeds the source byte budget",
            ));
        }
        receipts.push(OriginalMixSfxSourceReceipt {
            asset_id,
            source_asset_sha256: digest.clone(),
        });
        verified.push(VerifiedSfxWav {
            asset_id,
            source_sha256: digest,
            bytes,
        });
    }
    let mixed = mix_source_bound_semantic_sfx(
        project,
        request.plan.deliverable_profile_id,
        &verified,
        request.frame_count,
    )?;
    let pcm = mixed
        .wav_bytes
        .get(44..)
        .ok_or_else(|| stale("Original semantic SFX mixer returned incomplete audio"))?;
    if mixed.rendered_frames != request.frame_count
        || mixed.original_sources != verified.len()
        || mixed.output_sha256 != sha256(&mixed.wav_bytes)
        || pcm != decoded_sfx.interleaved_s16le.as_slice()
    {
        return Err(unsupported(
            "Selected original SFX bus does not reproduce the current authored cue schedule",
        ));
    }
    let schedule = project
        .validated_sfx_schedule()
        .map_err(|_| unsupported("Original SFX schedule is no longer valid"))?;
    let placements = schedule
        .iter()
        .map(|place| {
            (
                place.cue_id,
                place.absolute_start_frame,
                place.absolute_end_frame,
                place.first_sample,
                place.sample_count,
            )
        })
        .collect::<Vec<_>>();
    let cue_bytes = serde_json::to_vec(&(&project.audio.sfx_cues, placements, request.frame_count))
        .map_err(|_| unsupported("Original SFX cue source cannot be fingerprinted"))?;
    Ok(Some(OriginalMixSemanticSfxProof {
        cue_count: project.audio.sfx_cues.len(),
        sources: receipts,
        cue_schedule_sha256: sha256(&cue_bytes),
        reconstructed_wav_sha256: mixed.output_sha256,
        reconstructed_pcm_sha256: sha256(pcm),
    }))
}

fn render_current_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request: OriginalMixSourceRequest<'_>,
) -> NativeResult<(OriginalMixSourceReadiness, Vec<u8>)> {
    let project = current_project(service, project_id, expected)?;
    check_request(&project, request)?;
    let voice = checked_stem(service, &project, &request.plan.measured_voice)?;
    let music = checked_stem(service, &project, &request.plan.original_music)?;
    let sfx = checked_stem(service, &project, &request.plan.original_sfx)?;
    let semantic_sfx = verify_semantic_sfx_bus(service, &project, expected, request, &sfx)?;
    let mut wav = Vec::new();
    let evidence = write_mix_wav(
        request.plan,
        &project,
        &voice.interleaved_s16le,
        &music.interleaved_s16le,
        &sfx.interleaved_s16le,
        &mut wav,
    )
    .map_err(|_| unsupported("Original source mix cannot be rendered within its sample guard"))?;
    let source_plan = serde_json::to_vec(request.plan)
        .map_err(|_| unsupported("Original mix plan cannot be fingerprinted"))?;
    let pcm = wav
        .get(44..)
        .ok_or_else(|| stale("Original mix returned an incomplete WAV"))?;
    if wav.len() as u64 != 44 + request.plan.sample_frames * 4
        || wav.len() as u64 > MAX_MIX_WAV_BYTES
        || evidence.source_plan_sha256 != sha256(&source_plan)
        || evidence.rendered_pcm_sha256 != sha256(pcm)
        || evidence.frames != request.plan.sample_frames
        || evidence.sample_rate != 48_000
        || evidence.channels != 2
        || evidence.owner_release_approved
        || evidence.listening_intelligibility_approved
        || evidence.independently_verified_lufs
        || evidence.independently_verified_true_peak
    {
        return Err(stale(
            "Original mixed WAV differs from its exact source PCM evidence",
        ));
    }
    current_project(service, project_id, expected)?;
    let proof = OriginalMixSourceReadiness {
        project_resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
        deliverable_id: request.plan.deliverable_profile_id,
        frame_count: request.frame_count,
        fps_num: request.fps_num,
        fps_den: request.fps_den,
        measured_voice: request.plan.measured_voice.clone(),
        original_music: request.plan.original_music.clone(),
        original_sfx: request.plan.original_sfx.clone(),
        semantic_sfx,
        original_mix: evidence,
        staged_wav_sha256: sha256(&wav),
        staged_wav_bytes: wav.len() as u64,
        evidence_scope: SOURCE_SCOPE.into(),
    };
    Ok((proof, wav))
}

fn source_path(project_id: Uuid, proof: &OriginalMixSourceReadiness) -> String {
    format!(
        "mw-original-mix-{}-{}-r{}-p{}-{}-{}.wav",
        project_id.simple(),
        proof.generation.simple(),
        proof.revision,
        proof.deliverable_id.simple(),
        &proof.original_mix.source_plan_sha256[..16],
        &proof.staged_wav_sha256[..16],
    )
}

/// Reconstruct all three original sources and compare the complete staged
/// WAV. This read-only gate does not trust a supplied readiness receipt.
pub fn verify_original_mix_av_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request: OriginalMixSourceRequest<'_>,
    audio: &MltAudioArtifact,
    owner_output_root: &Path,
) -> NativeResult<OriginalMixSourceReadiness> {
    let (proof, wav) = render_current_source(service, project_id, expected, request)?;
    if audio.sample_rate != 48_000
        || audio.channels != 2
        || audio.relative_path != source_path(project_id, &proof)
        || audio.sha256 != proof.staged_wav_sha256
    {
        return Err(stale(
            "Staged original mix identity differs from the current source WAV",
        ));
    }
    let actual = read_verified_production_artifact(
        owner_output_root,
        &audio.relative_path,
        &audio.sha256,
        MAX_MIX_WAV_BYTES,
    )?;
    if actual != wav {
        return Err(stale(
            "Staged WAV samples differ from the current original source mix",
        ));
    }
    current_project(service, project_id, expected)?;
    Ok(proof)
}

/// Prepare a bounded physical WAV for later canonical recipe/visual admission.
/// No Driver Host operation, delivery grant or Project mutation occurs here.
/// A pre-existing file is reusable only after complete source reconstruction
/// and readback; it is never overwritten or repaired following a failed check.
pub fn prepare_original_mix_av_audio(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request: OriginalMixSourceRequest<'_>,
    owner_output_root: &Path,
) -> NativeResult<PreparedOriginalMixWav> {
    if !safe_metadata(owner_output_root)?.is_dir() {
        return Err(unsupported(
            "Original mix requires an authorized owner output directory",
        ));
    }
    let (proof, wav) = render_current_source(service, project_id, expected, request)?;
    let audio = MltAudioArtifact {
        relative_path: source_path(project_id, &proof),
        sha256: proof.staged_wav_sha256,
        sample_rate: 48_000,
        channels: 2,
    };
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    match options.open(owner_output_root.join(&audio.relative_path)) {
        Ok(mut file) => {
            file.write_all(&wav)
                .map_err(|_| Error::new(ErrorCode::Unavailable, "Original mix WAV write failed"))?;
            file.sync_all()
                .map_err(|_| Error::new(ErrorCode::Unavailable, "Original mix WAV sync failed"))?;
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => {
            return Err(Error::new(
                ErrorCode::Unavailable,
                "Original mix WAV cannot be created in the owner output directory",
            ));
        }
    }
    let proof = verify_original_mix_av_source(
        service,
        project_id,
        expected,
        request,
        &audio,
        owner_output_root,
    )?;
    Ok(PreparedOriginalMixWav { proof, audio })
}

/// Derive the saved scene intent and exact bundled-font profile. No caller
/// options or default role/archetype can replace an authored consumed value.
/// Excluded scenes keep their saved intent without affecting this projection.
pub fn canonical_original_mix_film_options(
    project: &Project,
    deliverable_id: Uuid,
) -> NativeResult<FilmBuildOptions> {
    project
        .validate()
        .map_err(|_| unsupported("Canonical mix Film project failed semantic validation"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == deliverable_id)
        .ok_or_else(|| stale("Canonical mix delivery profile is absent"))?;
    let recipe = project
        .production_design
        .delivery_recipes
        .get(&deliverable_id)
        .ok_or_else(|| {
            unsupported("Save a delivery recipe before preparing a canonical original mix")
        })?;
    let fps_num = u32::try_from(profile.frame_rate.num)
        .map_err(|_| unsupported("Canonical mix FPS numerator exceeds the native frame clock"))?;
    let fps_den = u32::try_from(profile.frame_rate.den)
        .map_err(|_| unsupported("Canonical mix FPS denominator exceeds the native frame clock"))?;
    let frame_rate = Rate::new(fps_num, fps_den)
        .map_err(|_| unsupported("Canonical mix requires a valid saved rational frame rate"))?;
    let selected = if profile.included_scene_ids.is_empty() {
        project
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>()
    } else {
        profile.included_scene_ids.clone()
    };
    if selected.is_empty() {
        return Err(unsupported(
            "Canonical original mix requires a nonempty saved scene cut",
        ));
    }
    let authored = recipe
        .scene_intents
        .iter()
        .map(|intent| (intent.scene_id, intent))
        .collect::<BTreeMap<_, _>>();
    let mut scene_intents = Vec::with_capacity(selected.len());
    for scene_id in selected {
        let intent = authored.get(&scene_id).ok_or_else(|| {
            unsupported(
                "Every consumed scene requires an explicit saved narrative role and archetype",
            )
        })?;
        let role = match intent.role.ok_or_else(|| {
            unsupported(
                "Every consumed scene requires an explicit saved narrative role and archetype",
            )
        })? {
            DeliveryNarrativeRole::Hook => NarrativeRole::Hook,
            DeliveryNarrativeRole::Problem => NarrativeRole::Problem,
            DeliveryNarrativeRole::Mechanism => NarrativeRole::Mechanism,
            DeliveryNarrativeRole::Evidence => NarrativeRole::Evidence,
            DeliveryNarrativeRole::Comparison => NarrativeRole::Comparison,
            DeliveryNarrativeRole::Reveal => NarrativeRole::Reveal,
            DeliveryNarrativeRole::Payoff => NarrativeRole::Payoff,
            DeliveryNarrativeRole::Cta => NarrativeRole::Cta,
        };
        let archetype = match intent.archetype.ok_or_else(|| {
            unsupported(
                "Every consumed scene requires an explicit saved narrative role and archetype",
            )
        })? {
            DeliveryArchetype::Statement => Archetype::Statement,
            DeliveryArchetype::SplitExplanation => Archetype::SplitExplanation,
            DeliveryArchetype::ArchitectureReveal => Archetype::ArchitectureReveal,
            DeliveryArchetype::Comparison => Archetype::Comparison,
            DeliveryArchetype::Metric => Archetype::Metric,
            DeliveryArchetype::Timeline => Archetype::Timeline,
            DeliveryArchetype::CodeFocus => Archetype::CodeFocus,
            DeliveryArchetype::DiagramBuild => Archetype::DiagramBuild,
            DeliveryArchetype::ObjectSpotlight => Archetype::ObjectSpotlight,
            DeliveryArchetype::EvidenceFrame => Archetype::EvidenceFrame,
            DeliveryArchetype::ProductProof => Archetype::ProductProof,
            DeliveryArchetype::Endcard => Archetype::Endcard,
        };
        scene_intents.push(SceneFilmIntent {
            scene_id,
            role,
            archetype,
        });
    }
    Ok(FilmBuildOptions {
        frame_rate,
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents,
    })
}

fn canonical_dependency(
    project: &Project,
    deliverable_id: Uuid,
) -> NativeResult<VariantDependencyFingerprint> {
    variant_dependency_fingerprint(project, deliverable_id)
        .map_err(|_| unsupported("Canonical mix selected variant dependencies are invalid"))
}

fn recheck_canonical_dependency(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
    dependency: &VariantDependencyFingerprint,
) -> NativeResult<()> {
    let current = current_project(service, project_id, expected)?;
    if canonical_dependency(&current, deliverable_id)? != *dependency {
        return Err(stale(
            "Canonical mix selected delivery intent changed during source verification",
        ));
    }
    Ok(())
}

fn decoded_canonical_receipt(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
) -> NativeResult<PcmStemReceipt> {
    let decoded = service
        .decoded_stem_for_original_mix(project.id, project.revision, asset_id)
        .map_err(storage_error)?;
    if decoded.sample_frames.checked_mul(4) != Some(decoded.interleaved_s16le.len() as u64)
        || sha256(&decoded.interleaved_s16le) != decoded.decoded_pcm_sha256
    {
        return Err(stale(
            "Canonical source receipt differs from the actual decoded CAS samples",
        ));
    }
    Ok(PcmStemReceipt {
        asset_id,
        source_asset_sha256: decoded.original_asset_sha256,
        decoded_pcm_sha256: decoded.decoded_pcm_sha256,
        sample_frames: decoded.sample_frames,
    })
}

/// Resolve the current saved audio recipe using actual owner-CAS decoded
/// receipts. This read-only plan cannot grant native dispatch or approval.
pub fn resolve_canonical_original_mix_plan(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
) -> NativeResult<OriginalMixPlan> {
    let project = current_project(service, project_id, expected)?;
    let dependency = canonical_dependency(&project, deliverable_id)?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == deliverable_id)
        .ok_or_else(|| stale("Canonical original mix profile is absent"))?;
    let mix = project
        .production_design
        .delivery_recipes
        .get(&deliverable_id)
        .and_then(|recipe| recipe.original_mix.as_ref())
        .ok_or_else(|| unsupported("Save an original mix recipe for this delivery profile"))?;
    let voice_id = profile
        .voice_track_id
        .ok_or_else(|| unsupported("Canonical original mix requires a selected measured voice"))?;
    let voice_asset_id = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == voice_id)
        .map(|track| track.asset_id)
        .ok_or_else(|| stale("Canonical original mix selected voice is absent"))?;
    let music_id = mix.music_asset_id.ok_or_else(|| {
        unsupported("Select the original music asset in the saved delivery recipe")
    })?;
    let sfx_id = mix
        .sfx_asset_id
        .ok_or_else(|| unsupported("Select the original SFX asset in the saved delivery recipe"))?;
    let voice = decoded_canonical_receipt(service, &project, voice_asset_id)?;
    let music = decoded_canonical_receipt(service, &project, music_id)?;
    let sfx = decoded_canonical_receipt(service, &project, sfx_id)?;
    let plan = original_mix_plan_from_canonical_recipe(&project, deliverable_id, voice, music, sfx)
        .map_err(|_| {
            unsupported("Canonical original mix recipe or reviewed source windows are unsupported")
        })?;
    recheck_canonical_dependency(service, project_id, expected, deliverable_id, &dependency)?;
    Ok(plan)
}

/// Current identity and trusted native output references only. The coordinator
/// derives the plan, Film options, FPS, fonts and source receipts from storage.
/// Never deserialize this request from a browser or accept a caller's plan.
pub struct MltMultisegmentOriginalMixMasterRequest<'a> {
    pub request_id: &'a str,
    pub deliverable_id: Uuid,
    pub rendered: &'a MotionCanvasRenderEvidence,
    pub visual: &'a MltVerifiedLosslessTimeline,
    pub audio: &'a MltAudioArtifact,
}

#[derive(Debug, Clone, Serialize)]
pub struct MltMultisegmentOriginalMixMasterEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub frame_count: u64,
    pub video: MltVerifiedLosslessTimeline,
    pub measured_voice: MultiSegmentAudioReadiness,
    pub original_mix: OriginalMixSourceReadiness,
    pub staged_audio: MltAudioArtifact,
    pub master: Value,
    pub decoded_audio: Value,
    pub canonical_dependency: VariantDependencyFingerprint,
    pub canonical_film_options_sha256: String,
    pub evidence_scope: String,
}

impl ProductionCoordinator {
    /// Stage the actual current three-bus WAV for audition or later native
    /// source admission. This method performs no Driver Host operation.
    pub fn prepare_native_canonical_original_mix_av_audio(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        frame_count: u64,
    ) -> NativeResult<PreparedOriginalMixWav> {
        let project = current_project(&self.service, project_id, expected)?;
        if self.client.connection().resource != project.resource_key() {
            return Err(stale("Canonical original mix Broker owner root is foreign"));
        }
        let dependency = canonical_dependency(&project, deliverable_id)?;
        let options = canonical_original_mix_film_options(&project, deliverable_id)?;
        let plan = resolve_canonical_original_mix_plan(
            &self.service,
            project_id,
            expected,
            deliverable_id,
        )?;
        let prepared = prepare_original_mix_av_audio(
            &self.service,
            project_id,
            expected,
            OriginalMixSourceRequest {
                plan: &plan,
                frame_count,
                fps_num: options.frame_rate.num,
                fps_den: options.frame_rate.den,
            },
            &self.client.connection().output_root,
        )?;
        recheck_canonical_dependency(
            &self.service,
            project_id,
            expected,
            deliverable_id,
            &dependency,
        )?;
        Ok(prepared)
    }

    /// Finalize only the current canonical mix and the existing verified
    /// no-audio semantic MLT cut. This reuses the one native AV mux and never
    /// retries an outcome-unknown operation or mints human/mastering approval.
    pub async fn assemble_native_mlt_multisegment_original_mix_master(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltMultisegmentOriginalMixMasterRequest<'_>,
    ) -> NativeResult<MltMultisegmentOriginalMixMasterEvidence> {
        let MltMultisegmentOriginalMixMasterRequest {
            request_id,
            deliverable_id,
            rendered,
            visual,
            audio,
        } = request;
        if request_id.is_empty()
            || request_id.len() > 54
            || !request_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(Error::new(
                ErrorCode::InvalidArgument,
                "Canonical original mix requires bounded stable native request identity",
            ));
        }
        let project = current_project(&self.service, project_id, expected)?;
        if self.client.connection().resource != project.resource_key() {
            return Err(stale("Canonical original mix Broker owner root is foreign"));
        }
        // Source preparation can report unfinished mastering intent, but an
        // actual native master must never silently ignore that saved request.
        if project.audio.mix.target_lufs.is_some()
            || project.audio.mix.target_true_peak_dbfs.is_some()
        {
            return Err(unsupported(
                "Canonical original mix mastering does not support LUFS or true-peak targets",
            ));
        }
        let canonical_dependency = canonical_dependency(&project, deliverable_id)?;
        let options = canonical_original_mix_film_options(&project, deliverable_id)?;
        let canonical_film_options_sha256 = sha256(
            &serde_json::to_vec(&options)
                .map_err(|_| unsupported("Canonical Film options cannot be fingerprinted"))?,
        );
        let plan = resolve_canonical_original_mix_plan(
            &self.service,
            project_id,
            expected,
            deliverable_id,
        )?;
        let output_root = &self.client.connection().output_root;
        let recipe = verify_multisegment_visual_source(
            &project,
            deliverable_id,
            &options,
            rendered,
            visual,
            output_root,
        )?;
        let voice_track_id = project
            .deliverables
            .iter()
            .find(|profile| profile.id == deliverable_id)
            .and_then(|profile| profile.voice_track_id)
            .ok_or_else(|| unsupported("Canonical mix requires its selected measured voice"))?;
        let voice = self
            .service
            .verified_master_voice(project_id, expected, voice_track_id)
            .map_err(storage_error)?;
        let measured_voice = preflight_multi_segment_voice_source(
            &project,
            deliverable_id,
            &recipe,
            &visual.source,
            &voice,
            output_root,
        )?;
        let source_request = OriginalMixSourceRequest {
            plan: &plan,
            frame_count: recipe.total_frames,
            fps_num: recipe.provider_profile.fps_num,
            fps_den: recipe.provider_profile.fps_den,
        };
        let original_mix = verify_original_mix_av_source(
            &self.service,
            project_id,
            expected,
            source_request,
            audio,
            output_root,
        )?;
        if measured_voice.audio_sha256 != original_mix.measured_voice.source_asset_sha256
            || measured_voice.audio_pcm_frames != original_mix.measured_voice.sample_frames
            || measured_voice.total_frames != original_mix.frame_count
            || original_mix.staged_wav_sha256 != audio.sha256
        {
            return Err(stale(
                "Canonical mixed audio differs from the independently verified voice and video clock",
            ));
        }
        recheck_canonical_dependency(
            &self.service,
            project_id,
            expected,
            deliverable_id,
            &canonical_dependency,
        )?;
        let (master, decoded_audio) = self
            .mux_verified_multisegment_av_source(VerifiedMuxInputs {
                project_id,
                expected,
                request_id,
                project: &project,
                recipe: &recipe,
                visual,
                audio,
            })
            .await?;
        // The existing mux verifies MP4 and decoded WAV bytes. Reconstruct the
        // staged mix again so CAS substitution during dispatch cannot leave a
        // successful receipt bound only to old source metadata.
        let after = verify_original_mix_av_source(
            &self.service,
            project_id,
            expected,
            source_request,
            audio,
            output_root,
        )?;
        if after != original_mix {
            return Err(stale(
                "Canonical original mix sources changed during native mux",
            ));
        }
        recheck_canonical_dependency(
            &self.service,
            project_id,
            expected,
            deliverable_id,
            &canonical_dependency,
        )?;
        let captioned = measured_voice.caption_cue_count.is_some();
        Ok(MltMultisegmentOriginalMixMasterEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_count: recipe.total_frames,
            video: visual.clone(),
            measured_voice,
            original_mix,
            staged_audio: audio.clone(),
            master,
            decoded_audio,
            canonical_dependency,
            canonical_film_options_sha256,
            evidence_scope: if captioned {
                "actual-native-canonical-original-mix-h264-aac-caption-source-not-decoded-pixel-or-human-approved"
            } else {
                "actual-native-canonical-original-mix-h264-aac-source-bound-not-mastered-or-human-approved"
            }.into(),
        })
    }
}
