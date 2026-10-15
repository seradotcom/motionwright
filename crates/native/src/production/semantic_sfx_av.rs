//! Original semantic SFX -> existing native MLT H.264/AAC master.
//! The SHA-256 of a supplied WAV alone is not evidence that it represents
//! the current project's scene-bound semantic SFX cues.
use super::{
    MltAudioArtifact, MltAvMasterEvidence, MltAvMasterRequest, ProductionCoordinator,
    read_verified_production_artifact, storage_error,
};
use crate::sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx};
use motionwright_domain::RevisionStamp;
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path};
use uuid::Uuid;

const MAX_SFX_AUDIO_MASTER_BYTES: u64 = 6 * 1024 * 1024;

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
    let project = service.project(project_id).map_err(storage_error)?;
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(stale(
            "Semantic SFX source request does not match the owner project revision",
        ));
    }
    if audio.sample_rate != 48_000 || audio.channels != 2 || master_frames == 0 {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Semantic SFX requires a 48k stereo PCM source and positive master duration",
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

impl ProductionCoordinator {
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
