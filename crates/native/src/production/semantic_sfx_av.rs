//! Original semantic SFX -> existing native MLT H.264/AAC master.
//! The SHA-256 of a supplied WAV alone is not evidence that it represents
//! the current project's scene-bound semantic SFX cues.
use super::{
    MltAudioArtifact, MltAvMasterEvidence, MltAvMasterRequest, ProductionCoordinator,
    read_verified_production_artifact, safe_metadata, storage_error,
};
use crate::sfx_mix::{MixedSfxWav, VerifiedSfxWav, mix_source_bound_semantic_sfx};
use motionwright_domain::{Project, RevisionStamp};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, fs, io::Write, path::Path};
use uuid::Uuid;

pub(super) const MAX_SFX_AUDIO_MASTER_BYTES: u64 = 6 * 1024 * 1024;

fn stale(reason: &str) -> Error {
    Error::new(ErrorCode::StaleReference, reason)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticSfxAvSourceProof {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub master_frames: u64,
    pub original_asset_sha256: Vec<String>,
    pub exact_pcm_sha256: String,
    pub sample_peak_pcm16: u16,
}

/// Reconstruct exact source PCM only from immutable owner-imported WAVs.
/// Shared by preparation and pre-mux admission so they cannot drift.
fn mix_owner_original_semantic_sfx(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
    master_frames: u64,
) -> NativeResult<(Project, MixedSfxWav, Vec<String>)> {
    let project = service.project(project_id).map_err(storage_error)?;
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(stale(
            "Semantic SFX source request does not match the owner project revision",
        ));
    }
    if project.audio.sfx_cues.is_empty() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Semantic SFX source requires an original project-bound cue",
        ));
    }
    let mut originals = BTreeMap::new();
    for cue in &project.audio.sfx_cues {
        match originals.insert(cue.source_asset_id, cue.source_sha256.as_str()) {
            Some(previous) if previous != cue.source_sha256 => {
                return Err(stale("Conflicting SFX source digest for an asset identity"));
            }
            _ => {}
        }
    }
    let mut verified = Vec::with_capacity(originals.len());
    let mut original_asset_sha256 = Vec::with_capacity(originals.len());
    for (asset_id, digest) in originals {
        let bytes = service
            .verified_semantic_sfx_wav(project_id, expected, asset_id, digest)
            .map_err(storage_error)?;
        verified.push(VerifiedSfxWav {
            asset_id,
            source_sha256: digest.to_owned(),
            bytes,
        });
        original_asset_sha256.push(digest.to_owned());
    }
    let result = mix_source_bound_semantic_sfx(&project, deliverable_id, &verified, master_frames)?;
    Ok((project, result, original_asset_sha256))
}

/// Source admission only: no output generation, session mutation or retry.
/// The source WAV must already exist inside the exact owner output root.
pub fn verify_semantic_sfx_av_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
    master_frames: u64,
    audio: &MltAudioArtifact,
    owner_output_root: &Path,
) -> NativeResult<SemanticSfxAvSourceProof> {
    if audio.sample_rate != 48_000 || audio.channels != 2 || master_frames == 0 {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Semantic SFX requires a 48k stereo PCM source and positive master duration",
        ));
    }
    let (project, result, original_asset_sha256) = mix_owner_original_semantic_sfx(
        service,
        project_id,
        expected,
        deliverable_id,
        master_frames,
    )?;
    if result.output_sha256 != audio.sha256 {
        return Err(stale(
            "AV audio SHA-256 differs from the original source-bound semantic SFX mix",
        ));
    }
    let bytes = read_verified_production_artifact(
        owner_output_root,
        &audio.relative_path,
        &audio.sha256,
        MAX_SFX_AUDIO_MASTER_BYTES,
    )?;
    if bytes != result.wav_bytes {
        return Err(stale(
            "AV PCM bytes differ from the owner's original SFX cue schedule",
        ));
    }
    Ok(SemanticSfxAvSourceProof {
        project_id,
        generation: project.generation,
        revision: project.revision,
        deliverable_id,
        master_frames,
        original_asset_sha256,
        exact_pcm_sha256: result.output_sha256,
        sample_peak_pcm16: result.sample_peak_pcm16,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparedSemanticSfxWav {
    /// The source-bound post-write admission, never native MP4 approval.
    pub proof: SemanticSfxAvSourceProof,
    /// The exact verified owner-output-root-relative source passed to MLT.
    pub audio: MltAudioArtifact,
}

/// Materialize an already-authored owner's scene-linked SFX cues as an actual
/// immutable stereo WAV. The only output target is a deterministic source-
/// digest-bound filename inside the trusted existing owner output root.
/// No native Driver Host operation is started by this function.
pub fn prepare_semantic_sfx_av_audio(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
    master_frames: u64,
    owner_output_root: &Path,
) -> NativeResult<PreparedSemanticSfxWav> {
    let (project, result, _) = mix_owner_original_semantic_sfx(
        service,
        project_id,
        expected,
        deliverable_id,
        master_frames,
    )?;
    if result.wav_bytes.len() as u64 > MAX_SFX_AUDIO_MASTER_BYTES
        || !safe_metadata(owner_output_root)?.is_dir()
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Owner SFX PCM source is not inside a bounded authorized output directory",
        ));
    }
    // Naming uses only trusted UUIDs, revision, bounded integer frame count
    // and SHA-256 already verified by the native PCM mixer. Neither an
    // untrusted user path nor a provider-returned job ref is part of it.
    let relative_path = format!(
        "mw-sfx-{}-{}-r{}-f{}-{}.wav",
        project.id.simple(),
        project.generation.simple(),
        project.revision,
        master_frames,
        &result.output_sha256[..16],
    );
    let audio = MltAudioArtifact {
        relative_path,
        sha256: result.output_sha256,
        sample_rate: 48_000,
        channels: 2,
    };
    let file = owner_output_root.join(&audio.relative_path);
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    match opts.open(&file) {
        Ok(mut new_file) => {
            new_file.write_all(&result.wav_bytes).map_err(|_| {
                Error::new(
                    ErrorCode::Unavailable,
                    "Owner SFX PCM artifact write failed",
                )
            })?;
            new_file.sync_all().map_err(|_| {
                Error::new(ErrorCode::Unavailable, "Owner SFX PCM artifact sync failed")
            })?;
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            // Reusing an existing digest-named file is safe ONLY after the
            // entire file is re-verified, including its trusted root.
            // Never overwrite, truncate or silently repair owner audio.
        }
        Err(_) => {
            return Err(Error::new(
                ErrorCode::Unavailable,
                "Owner SFX PCM artifact cannot be created",
            ));
        }
    }
    // A concurrent CAS edit, symlink swap, truncated file or alternate mix
    // leaves NO usable prepared receipt. The owner must reconcile the stale
    // source; the failed operation never proceeds to the native mux.
    let proof = verify_semantic_sfx_av_source(
        service,
        project_id,
        expected,
        deliverable_id,
        master_frames,
        &audio,
        owner_output_root,
    )?;
    Ok(PreparedSemanticSfxWav { proof, audio })
}

impl ProductionCoordinator {
    /// First-class owner-scoped PCM preparation for later native AV master
    /// requests. No external or parallel render engine is involved.
    pub fn prepare_native_semantic_sfx_av_audio(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        master_frames: u64,
    ) -> NativeResult<PreparedSemanticSfxWav> {
        prepare_semantic_sfx_av_audio(
            &self.service,
            project_id,
            expected,
            deliverable_id,
            master_frames,
            &self.client.connection().output_root,
        )
    }

    /// Owner-bound semantic SFX source admission before the existing MLT
    /// driver dispatch. UNKNOWN native outcomes retain normal reconciliation.
    pub async fn assemble_native_semantic_sfx_av_master(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltAvMasterRequest<'_>,
    ) -> NativeResult<MltAvMasterEvidence> {
        let frames = request
            .motion
            .segments
            .first()
            .ok_or_else(|| stale("Semantic SFX master has no motion segment"))?
            .frame_count;
        if request.motion.segments.len() != 1
            || request.motion.project_resource != expected.resource
            || request.motion.generation != expected.generation
            || request.motion.revision != expected.revision
            || request.motion.deliverable_id != request.deliverable_id
        {
            return Err(stale(
                "Semantic SFX master does not match its Motion Canvas revision",
            ));
        }
        verify_semantic_sfx_av_source(
            &self.service,
            project_id,
            expected,
            request.deliverable_id,
            frames,
            request.audio,
            &self.client.connection().output_root,
        )?;
        self.assemble_mlt_av_master(project_id, expected, request)
            .await
    }
}
