use motionwright_domain::{
    AudioCodec, OutputColorSpace, OutputContainer, Project, VariantDependencyFingerprint,
    VideoCodec, variant_dependency_fingerprint,
};
use motionwright_native::original_mix::original_mix_plan_from_canonical_recipe;
use motionwright_native::production::{
    MltAvMasterEvidence, MltMultisegmentAvMasterEvidence, MltMultisegmentOriginalMixMasterEvidence,
    canonical_original_mix_film_options, original_mix_sample_clock,
    read_verified_production_artifact, verified_native_media_path,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, VecDeque},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, Write},
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex},
};
use uuid::Uuid;

const MAX_MASTER_BYTES: u64 = 1024 * 1024 * 1024;
/// Small-file review only. Long masters are delivered via streaming disk copy,
/// never piped wholesale through WebView IPC.
pub const MAX_BROWSER_REVIEW_BYTES: u64 = 16 * 1024 * 1024;
const MAX_DELIVERY_HANDLES: usize = 32;
const MAX_DESTINATION_BYTES: usize = 2048;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MasterExportRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub effect_grant: Uuid,
    pub export_token: Uuid,
    pub destination: String,
    #[serde(default)]
    pub include_integrity_manifest: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MasterReviewRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub export_token: Uuid,
}

#[derive(Debug, Serialize)]
pub struct MasterExportReceipt {
    pub destination: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub source_current: bool,
    /// Optional portable local SHA-256 evidence sidecar. No signature implied.
    pub integrity_manifest_path: Option<String>,
}

#[derive(Debug, Clone)]
struct DeliveryHandle {
    token: Uuid,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    frame_rate_num: u32,
    frame_rate_den: u32,
    frame_count: u64,
    owner_root: PathBuf,
    relative_path: String,
    sha256: String,
    /// Without the required device-local full-medium verification, native
    /// captioned/mixed sources remain reviewable and cannot authorize export.
    review_only: bool,
    /// Derived current-source evidence, never another saved mix plan or an IPC
    /// input. Kept inside this existing bounded session registry only.
    canonical_mix: Option<VariantDependencyFingerprint>,
}

#[derive(Clone, Default)]
pub struct NativeMasterDeliveryRegistry {
    entries: Arc<Mutex<VecDeque<DeliveryHandle>>>,
}

/// An original captioned master is reviewable, not exportable, until its
/// actual device-local decoded pixels are independently admitted. CI proof on
/// synthetic fixtures cannot grant production delivery authority.
fn multisegment_review_only(scope: &str, cue_count: Option<usize>) -> Option<bool> {
    match (scope, cue_count) {
        (
            "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved",
            None,
        ) => Some(false),
        (
            "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved",
            Some(1..=96),
        ) => Some(true),
        _ => None,
    }
}

fn valid_sha256(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn canonical_mix_binding_is_current(
    project: &Project,
    deliverable_id: Uuid,
    binding: &VariantDependencyFingerprint,
) -> bool {
    variant_dependency_fingerprint(project, deliverable_id).is_ok_and(|current| &current == binding)
}

/// Narrow desktop custody admission for a trusted native result. It does not
/// decode media or replace the coordinator's original-journal/Film checks.
fn canonical_mix_delivery_binding(
    project: &Project,
    evidence: &MltMultisegmentOriginalMixMasterEvidence,
) -> Option<VariantDependencyFingerprint> {
    project.validate().ok()?;
    let resource = project.resource_key();
    let profile = project
        .deliverables
        .iter()
        .find(|item| item.id == evidence.deliverable_id)?;
    let dependency = variant_dependency_fingerprint(project, profile.id).ok()?;
    let options = canonical_original_mix_film_options(project, profile.id).ok()?;
    let options_sha256 = hex::encode(Sha256::digest(serde_json::to_vec(&options).ok()?));
    let source = &evidence.original_mix;
    let pcm = &source.original_mix;
    let voice = &evidence.measured_voice;
    let video = &evidence.video;
    let plan = original_mix_plan_from_canonical_recipe(
        project,
        profile.id,
        source.measured_voice.clone(),
        source.original_music.clone(),
        source.original_sfx.clone(),
    )
    .ok()?;
    let plan_sha256 = hex::encode(Sha256::digest(serde_json::to_vec(&plan).ok()?));
    let samples = original_mix_sample_clock(
        evidence.frame_count,
        options.frame_rate.num,
        options.frame_rate.den,
    )
    .ok()?;
    let wav_bytes = samples.checked_mul(4)?.checked_add(44)?;
    let (scope, voice_scope) = if profile.burn_in_captions {
        if !matches!(voice.caption_cue_count, Some(1..=96)) {
            return None;
        }
        (
            "actual-native-canonical-original-mix-h264-aac-caption-source-not-decoded-pixel-or-human-approved",
            "verified-owner-audio-ffv1-native-captioned-source-not-muxed-mp4",
        )
    } else {
        if voice.caption_cue_count.is_some() {
            return None;
        }
        (
            "actual-native-canonical-original-mix-h264-aac-source-bound-not-mastered-or-human-approved",
            "verified-owner-audio-ffv1-inputs-not-muxed-mp4",
        )
    };
    if evidence.project_resource != resource
        || evidence.generation != project.generation || evidence.revision != project.revision
        || evidence.canonical_dependency != dependency
        || evidence.canonical_film_options_sha256 != options_sha256
        || evidence.evidence_scope != scope
        || project.audio.mix.target_lufs.is_some() || project.audio.mix.target_true_peak_dbfs.is_some()
        || profile.video_codec != VideoCodec::H264 || profile.audio_codec != AudioCodec::Aac
        || profile.container != OutputContainer::Mp4 || profile.color_space != OutputColorSpace::Rec709
        || profile.audio_sample_rate_hz != 48_000
        || !(1..=36_000).contains(&evidence.frame_count)
        || samples != plan.sample_frames
        || voice.project_resource != resource || voice.generation != project.generation
        || voice.revision != project.revision || voice.deliverable_id != profile.id
        || Some(voice.voice_track_id) != profile.voice_track_id
        || voice.total_frames != evidence.frame_count || voice.audio_pcm_frames != samples
        || voice.audio_sample_rate != 48_000 || voice.audio_channels != 2
        || voice.fps_num != options.frame_rate.num || voice.fps_den != options.frame_rate.den
        || voice.final_profile != "h264-aac-mp4" || voice.evidence_scope != voice_scope
        || !valid_sha256(&voice.source_fingerprint)
        || !(44..=MAX_MASTER_BYTES).contains(&voice.audio_size_bytes)
        // The original voice container is not the mixed WAV container.
        || voice.audio_sha256 != source.measured_voice.source_asset_sha256
        || source.staged_wav_sha256 != evidence.staged_audio.sha256
        || source.staged_wav_bytes != wav_bytes
        || !valid_sha256(&source.staged_wav_sha256)
        || evidence.staged_audio.sample_rate != 48_000 || evidence.staged_audio.channels != 2
        || source.project_resource != resource || source.generation != project.generation
        || source.revision != project.revision || source.deliverable_id != profile.id
        || source.frame_count != evidence.frame_count
        || source.fps_num != options.frame_rate.num || source.fps_den != options.frame_rate.den
        || source.semantic_sfx.is_some()
        || source.evidence_scope != "verified-owner-original-mix-source-only-not-canonical-delivery-or-native-master"
        || pcm.schema != "motionwright.original-mix-source-pcm/1"
        || pcm.source_plan_sha256 != plan_sha256 || !valid_sha256(&pcm.rendered_pcm_sha256)
        || pcm.language != profile.language || pcm.frames != samples
        || pcm.sample_rate != 48_000 || pcm.channels != 2
        || pcm.input_source_checks != "ALL_THREE_FULL_PCM_SHA256_VERIFIED"
        || pcm.rendered_media != "RAW_PCM_S16LE_ONLY"
        || pcm.measured_music_duck_windows != plan.voice_windows.len() as u32
        || !pcm.measured_pcm_sample_peak_dbfs.is_finite() || !pcm.measured_pcm_rms_dbfs.is_finite()
        || !pcm.minimum_music_duck_gain.is_finite()
        || pcm.minimum_music_duck_gain <= 0.0 || pcm.minimum_music_duck_gain > 1.0
        || pcm.independently_verified_lufs || pcm.independently_verified_true_peak
        || pcm.listening_intelligibility_approved || pcm.owner_release_approved
        || video.project_resource != resource || video.generation != project.generation
        || video.revision != project.revision || video.deliverable_id != profile.id
        || video.frame_count != evidence.frame_count
        || video.native_frame_count_observed != Some(evidence.frame_count)
        || video.transport_pcm_audio || video.native_render_profile != "lossless-video-only"
        || video.evidence_scope != "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
        || video.source.project_resource != resource || video.source.generation != project.generation
        || video.source.revision != project.revision || video.source.deliverable_id != profile.id
        || video.source.total_frames != evidence.frame_count
        || video.source.fps_num != options.frame_rate.num || video.source.fps_den != options.frame_rate.den
        || video.recipe_sha256 != video.source.input_sha256
        || voice.video_input_sha256 != video.source.input_sha256
        || voice.video_segment_count != video.source.verified_video_segments.len()
        || !(2..=64).contains(&voice.video_segment_count)
        || !valid_sha256(&video.recipe_sha256) || !valid_sha256(&video.artifact_sha256)
        || !(1..=MAX_MASTER_BYTES).contains(&video.artifact_bytes)
    {
        return None;
    }
    // The final coordinator result carries the original completed Film
    // chain. Join its typed segment identity to the verified mezzanines;
    // these shape checks do not authenticate caller-supplied journal JSON.
    if evidence.canonical_motion_source.len() != video.source.verified_video_segments.len() {
        return None;
    }
    let mut seen_receipts = BTreeSet::new();
    let mut seen_jobs = BTreeSet::new();
    let mut seen_segments = BTreeSet::new();
    for (motion, media) in evidence
        .canonical_motion_source
        .iter()
        .zip(&video.source.verified_video_segments)
    {
        if motion.segment_id != media.segment_id
            || motion.native_job_ref != media.native_render_job_ref
            || motion.segment_id.trim().is_empty()
            || motion.segment_id.len() > 256
            || motion.native_job_ref.trim().is_empty()
            || motion.native_job_ref.len() > 256
            || motion.native_job_ref.chars().any(char::is_control)
            || !valid_sha256(&motion.film_sha256)
            || !valid_sha256(&motion.plan_ref)
            || !valid_sha256(&motion.native_fingerprint)
            || !valid_sha256(&motion.connection_identity)
            || !seen_segments.insert(&motion.segment_id)
            || !seen_jobs.insert(&motion.native_job_ref)
            || motion
                .completed_receipt_ids
                .windows(2)
                .any(|ids| ids[0] >= ids[1])
            || motion
                .completed_receipt_ids
                .iter()
                .any(|id| id.is_nil() || !seen_receipts.insert(*id))
        {
            return None;
        }
    }
    // Preserve the coordinator's exact input and output bindings. These
    // existing SDK fields are custody metadata, not the pending full-PCM gate.
    if evidence.master.get("profile")?.as_str()? != "h264-aac-mp4"
        || evidence.master.get("frame_count")?.as_u64()? != evidence.frame_count
        || evidence.master.get("fps_num")?.as_u64()? != u64::from(options.frame_rate.num)
        || evidence.master.get("fps_den")?.as_u64()? != u64::from(options.frame_rate.den)
        || evidence.master.get("sample_rate")?.as_u64()? != 48_000
        || evidence.master.get("channels")?.as_u64()? != 2
        || evidence.master.get("video_sha256")?.as_str()? != video.artifact_sha256
        || evidence.master.get("audio_sha256")?.as_str()? != evidence.staged_audio.sha256
        || evidence.master.get("decoded_audio")? != &evidence.decoded_audio
    {
        return None;
    }
    Some(dependency)
}

impl NativeMasterDeliveryRegistry {
    /// Only the trusted post-coordinator path can mint an export token. The
    /// WebView can never supply master artifact paths, hashes or provenance.
    pub fn register(&self, owner_root: &Path, master: &MltAvMasterEvidence) -> Option<Uuid> {
        if master.master.get("profile")?.as_str()? != "h264-aac-mp4"
            || master.frame_count == 0
            || master.frame_count > 36_000
            || master.frame_rate.num == 0
            || master.frame_rate.den == 0
        {
            return None;
        }
        let id = Uuid::parse_str(master.project_resource.strip_prefix("project:")?).ok()?;
        let artifact = master.master.get("artifact")?;
        let relative_path = artifact.get("path")?.as_str()?;
        let sha256 = artifact.get("sha256")?.as_str()?;
        if !valid_sha256(sha256) || relative_path.is_empty() || relative_path.len() > 4096 {
            return None;
        }
        let token = Uuid::new_v4();
        let entry = DeliveryHandle {
            token,
            project_id: id,
            generation: master.generation,
            revision: master.revision,
            deliverable_id: master.deliverable_id,
            frame_rate_num: master.frame_rate.num,
            frame_rate_den: master.frame_rate.den,
            frame_count: master.frame_count,
            owner_root: owner_root.to_path_buf(),
            relative_path: relative_path.to_owned(),
            sha256: sha256.to_owned(),
            review_only: false,
            canonical_mix: None,
        };
        let mut entries = self.entries.lock().ok()?;
        if entries.len() >= MAX_DELIVERY_HANDLES {
            entries.pop_front();
        }
        entries.push_back(entry);
        Some(token)
    }

    /// Accept only trusted current exact-source multisegment production
    /// receipts. No JS/WebView input is an artifact path or a digest.
    /// An ordinary master receives a scoped export capability. A native
    /// captioned master receives a *review-only* token until actual original
    /// decoded MP4 glyph evidence exists on this owner device. Its CI fixture
    /// PASS does not authorize commercial export.
    pub fn register_multisegment(
        &self,
        owner_root: &Path,
        evidence: &MltMultisegmentAvMasterEvidence,
    ) -> Option<Uuid> {
        let review_only = multisegment_review_only(
            &evidence.evidence_scope,
            evidence.measured_voice.caption_cue_count,
        )?;
        if evidence.master.get("profile")?.as_str()? != "h264-aac-mp4"
            || evidence.video.project_resource != evidence.project_resource
            || evidence.video.generation != evidence.generation
            || evidence.video.revision != evidence.revision
            || evidence.video.deliverable_id != evidence.deliverable_id
            || evidence.video.frame_count != evidence.frame_count
            || evidence.video.native_frame_count_observed != Some(evidence.frame_count)
            || evidence.video.transport_pcm_audio
            || evidence.video.native_render_profile != "lossless-video-only"
            || evidence.measured_voice.project_resource != evidence.project_resource
            || evidence.measured_voice.generation != evidence.generation
            || evidence.measured_voice.revision != evidence.revision
            || evidence.measured_voice.deliverable_id != evidence.deliverable_id
            || evidence.measured_voice.total_frames != evidence.frame_count
            || evidence.measured_voice.audio_sha256 != evidence.staged_audio.sha256
            || evidence.measured_voice.audio_sample_rate != 48_000
            || evidence.measured_voice.audio_channels != 2
            || evidence.frame_count == 0
            || evidence.frame_count > 36_000
            || evidence.measured_voice.fps_num == 0
            || evidence.measured_voice.fps_den == 0
        {
            return None;
        }
        let id = Uuid::parse_str(evidence.project_resource.strip_prefix("project:")?).ok()?;
        let artifact = evidence.master.get("artifact")?;
        let path = artifact.get("path")?.as_str()?;
        let sha256 = artifact.get("sha256")?.as_str()?;
        let bytes = artifact.get("bytes")?.as_u64()?;
        if artifact.get("root")?.as_str()? != "output"
            || path.is_empty()
            || path.len() > 4096
            || !valid_sha256(sha256)
            || !(1..=MAX_MASTER_BYTES).contains(&bytes)
        {
            return None;
        }
        // Owner files are verified by ProductionCoordinator before this
        // function is called; the scoped export/stream path will verify again.
        let token = Uuid::new_v4();
        let entry = DeliveryHandle {
            token,
            project_id: id,
            generation: evidence.generation,
            revision: evidence.revision,
            deliverable_id: evidence.deliverable_id,
            frame_rate_num: evidence.measured_voice.fps_num,
            frame_rate_den: evidence.measured_voice.fps_den,
            frame_count: evidence.frame_count,
            owner_root: owner_root.to_path_buf(),
            relative_path: path.to_owned(),
            sha256: sha256.to_owned(),
            review_only,
            canonical_mix: None,
        };
        let mut entries = self.entries.lock().ok()?;
        if entries.len() >= MAX_DELIVERY_HANDLES {
            entries.pop_front();
        }
        entries.push_back(entry);
        Some(token)
    }

    /// Admit only the trusted canonical-mix coordinator result. Voice/CAS
    /// custody and mixed-WAV custody remain separate; neither UI JSON nor a
    /// claimed verifier PASS can promote this token into export authority.
    pub fn register_canonical_original_mix_review(
        &self,
        owner_root: &Path,
        project: &Project,
        evidence: &MltMultisegmentOriginalMixMasterEvidence,
    ) -> Option<Uuid> {
        let binding = canonical_mix_delivery_binding(project, evidence)?;
        let artifact = evidence.master.get("artifact")?;
        let path = artifact.get("path")?.as_str()?;
        let digest = artifact.get("sha256")?.as_str()?;
        let bytes = artifact.get("bytes")?.as_u64()?;
        if artifact.get("root")?.as_str()? != "output"
            || path.is_empty()
            || path.len() > 4096
            || !valid_sha256(digest)
            || !(1..=MAX_MASTER_BYTES).contains(&bytes)
        {
            return None;
        }
        // This is owner-byte identity, not a full-content or codec verifier.
        // Actual coordinator checks precede registration; review rehashes again.
        let original =
            verified_native_media_path(owner_root, path, digest, MAX_MASTER_BYTES).ok()?;
        if fs::metadata(original).ok()?.len() != bytes {
            return None;
        }
        let token = Uuid::new_v4();
        let entry = DeliveryHandle {
            token,
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            deliverable_id: evidence.deliverable_id,
            frame_rate_num: evidence.measured_voice.fps_num,
            frame_rate_den: evidence.measured_voice.fps_den,
            frame_count: evidence.frame_count,
            owner_root: owner_root.to_path_buf(),
            relative_path: path.to_owned(),
            sha256: digest.to_owned(),
            review_only: true,
            canonical_mix: Some(binding),
        };
        let mut entries = self.entries.lock().ok()?;
        if entries.len() >= MAX_DELIVERY_HANDLES {
            entries.pop_front();
        }
        entries.push_back(entry);
        Some(token)
    }

    /// Resolve a version-bound, already authenticated native master handle.
    /// This read-only step never reveals its owner root or output path to UI.
    pub fn resolve(&self, project: &Project, token: Uuid) -> Result<DeliverySource, String> {
        let entry = self
            .entries
            .lock()
            .map_err(|_| "Native AV delivery registry is unavailable.")?
            .iter()
            .find(|entry| entry.token == token)
            .cloned()
            .ok_or("No session-authorized native AV master is available.")?;
        if entry.project_id != project.id
            || entry.generation != project.generation
            || entry.revision != project.revision
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == entry.deliverable_id)
        {
            return Err(
                "Native master export requires the exact current project and output profile."
                    .into(),
            );
        }
        if let Some(binding) = &entry.canonical_mix
            && !canonical_mix_binding_is_current(project, entry.deliverable_id, binding)
        {
            return Err(
                "Canonical mix review requires the same saved recipe and source dependencies."
                    .into(),
            );
        }
        Ok(DeliverySource {
            owner_root: entry.owner_root,
            relative_path: entry.relative_path,
            sha256: entry.sha256,
            revision: entry.revision,
            deliverable_id: entry.deliverable_id,
            project_id: entry.project_id,
            generation: entry.generation,
            frame_rate_num: entry.frame_rate_num,
            frame_rate_den: entry.frame_rate_den,
            frame_count: entry.frame_count,
            review_only: entry.review_only,
        })
    }
}

pub struct DeliverySource {
    owner_root: PathBuf,
    relative_path: String,
    sha256: String,
    revision: u64,
    deliverable_id: Uuid,
    project_id: Uuid,
    generation: Uuid,
    frame_rate_num: u32,
    frame_rate_den: u32,
    frame_count: u64,
    review_only: bool,
}

fn validated_destination(value: &str) -> Result<PathBuf, String> {
    if value.is_empty()
        || value.len() > MAX_DESTINATION_BYTES
        || value.chars().any(char::is_control)
    {
        return Err("A bounded absolute MP4 export destination is required.".into());
    }
    let destination = PathBuf::from(value);
    if !destination.is_absolute()
        || destination
            .extension()
            .and_then(|part| part.to_str())
            .is_none_or(|extension| !extension.eq_ignore_ascii_case("mp4"))
    {
        return Err("Export destination must be an absolute path ending in .mp4.".into());
    }
    let parent = destination
        .parent()
        .ok_or("MP4 destination has no parent directory.")?;
    let mut path = PathBuf::new();
    for component in parent.components() {
        match component {
            Component::Prefix(prefix) => path.push(prefix.as_os_str()),
            Component::RootDir => path.push(component.as_os_str()),
            Component::Normal(name) => path.push(name),
            Component::CurDir | Component::ParentDir => {
                return Err("MP4 destination may not contain dot or parent traversal.".into());
            }
        }
        let metadata = fs::symlink_metadata(&path)
            .map_err(|_| "MP4 export parent directory does not exist.")?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err("MP4 export parent may not traverse symlinks or non-directories.".into());
        }
    }
    if destination
        .file_name()
        .and_then(|name| name.to_str())
        .is_none_or(|name| name.len() > 255 || name.trim().is_empty())
    {
        return Err("MP4 export filename is invalid.".into());
    }
    Ok(destination)
}

impl DeliverySource {
    /// Refuse delivery when the required original device-local full-medium
    /// verification has not admitted the actual master. Source/CI proof is insufficient.
    pub fn export_allowed(&self) -> bool {
        !self.review_only
    }

    /// Optional portable integrity metadata is written only after the exact
    /// destination MP4 has been exported and independently SHA-256 verified.
    /// An owner-scoped export grant authorizes the media destination and its
    /// deterministic sibling proof; the WebView cannot inject origin hashes.
    ///
    /// This is an unsigned content-integrity descriptor, NOT an authenticity
    /// signature or proof that someone reviewed the media.
    pub fn copy_with_integrity(
        &self,
        destination: &str,
        include_integrity_manifest: bool,
        native_sdk_revision: &str,
    ) -> Result<MasterExportReceipt, String> {
        if !self.export_allowed() {
            return Err("Native MP4 is review-only: required device-local image/audio content verification is incomplete.".into());
        }
        if !include_integrity_manifest {
            return self.copy_to(destination);
        }
        if native_sdk_revision.len() != 40
            || !native_sdk_revision
                .bytes()
                .all(|ch| ch.is_ascii_digit() || (b'a'..=b'f').contains(&ch))
        {
            return Err("Pinned Semwright Native SDK source revision is malformed.".into());
        }
        let target = validated_destination(destination)?;
        let name = target
            .file_name()
            .and_then(|part| part.to_str())
            .ok_or("Exported MP4 file name is not UTF-8.")?;
        let proof_name = format!("{name}.motionwright-integrity.json");
        if proof_name.len() > 255 {
            return Err(
                "MP4 file name is too long to create a portable verification receipt.".into(),
            );
        }
        let proof_path = target.with_file_name(proof_name);
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        // Reserve the sibling manifest without overwriting an existing
        // receipt. This happens *before* MP4 creation so a collision cannot
        // result in a new video with no accompanying requested evidence.
        let mut proof = options.open(&proof_path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "Verification receipt destination already exists; existing files are never overwritten."
                    .to_string()
            } else {
                "The verification receipt could not be created safely.".to_string()
            }
        })?;
        let transfer = self.copy_to(destination);
        let receipt = match transfer {
            Ok(receipt) => receipt,
            Err(error) => {
                drop(proof);
                let _ = fs::remove_file(&proof_path);
                return Err(error);
            }
        };
        let body = serde_json::json!({
            "schema": "motionwright-media-integrity-v1",
            "scope": "sha256-content-consistency-not-signed-attestation",
            "media": {
                "filename": name,
                "size_bytes": receipt.size_bytes,
                "sha256": receipt.sha256,
            },
            "origin": {
                "project_id": self.project_id,
                "generation": self.generation,
                "revision": self.revision,
                "deliverable_id": self.deliverable_id,
                "native_profile": "h264-aac-mp4",
                "frame_count": self.frame_count,
                "frame_rate": { "num": self.frame_rate_num, "den": self.frame_rate_den },
                "semwright_native_sdk_revision": native_sdk_revision,
            }
        });
        let write_result = (|| -> Result<(), String> {
            let bytes = serde_json::to_vec_pretty(&body)
                .map_err(|_| "Unable to serialize the portable MP4 verification receipt.")?;
            if bytes.len() > 12 * 1024 {
                return Err("Portable MP4 verification receipt exceeds its bounded size.".into());
            }
            proof
                .write_all(&bytes)
                .map_err(|_| "Portable MP4 verification receipt write failed.")?;
            proof
                .write_all(b"\n")
                .map_err(|_| "Portable MP4 verification receipt newline write failed.")?;
            proof
                .sync_all()
                .map_err(|_| "Portable MP4 verification receipt sync failed.")?;
            Ok(())
        })();
        drop(proof);
        if let Err(error) = write_result {
            // A successfully verified media export is never destroyed to
            // conceal a sidecar failure; remove only the incomplete sibling.
            let _ = fs::remove_file(&proof_path);
            return Err(format!(
                "MP4 was exported and verified, but its optional verification receipt failed: {error}. The MP4 was preserved; check its destination."
            ));
        }
        Ok(MasterExportReceipt {
            integrity_manifest_path: Some(proof_path.to_string_lossy().to_string()),
            ..receipt
        })
    }

    /// Review a *small* completed native master without exporting to a user
    /// path. One immutable session token selects the backend-only source;
    /// bytes are SHA-256 checked before binary IPC can expose them.
    pub fn review_bytes(&self) -> Result<Vec<u8>, String> {
        let bytes = read_verified_production_artifact(
            &self.owner_root,
            &self.relative_path,
            &self.sha256,
            MAX_BROWSER_REVIEW_BYTES,
        )
        .map_err(|_| {
            if self.review_only {
                "MP4 review needs a valid SHA-256-verified owner artifact of at most 16 MiB. Export remains unavailable until the original medium is fully verified."
                    .to_string()
            } else {
                "MP4 review needs a valid SHA-256-verified owner artifact of at most 16 MiB. For longer masters use Export verified MP4."
                    .to_string()
            }
        })?;
        if bytes.get(4..8) != Some(&b"ftyp"[..]) {
            return Err("Native master review rejected invalid MP4 file signature.".into());
        }
        Ok(bytes)
    }

    /// Copy only the SHA-256-verified canonical owner artifact. Existing user
    /// files are NEVER overwritten, and source paths stay backend-private.
    pub fn copy_to(&self, destination: &str) -> Result<MasterExportReceipt, String> {
        if !self.export_allowed() {
            return Err("Native MP4 is review-only: required device-local image/audio content verification is incomplete.".into());
        }
        let destination = validated_destination(destination)?;
        let original = verified_native_media_path(
            &self.owner_root,
            &self.relative_path,
            &self.sha256,
            MAX_MASTER_BYTES,
        )
        .map_err(|_| "Canonical MP4 is missing, unsafe or failed source SHA-256 verification.")?;
        let expected_bytes = fs::metadata(&original)
            .map_err(|_| "Verified MP4 size is unavailable.")?
            .len();

        let mut input = File::open(&original).map_err(|_| "Canonical MP4 cannot be opened.")?;
        let mut magic = [0u8; 12];
        input
            .read_exact(&mut magic)
            .map_err(|_| "Master MP4 header is incomplete.")?;
        if &magic[4..8] != b"ftyp" {
            return Err("Canonical AV master lacks the expected MP4 file signature.".into());
        }
        input
            .rewind()
            .map_err(|_| "Canonical MP4 could not be rewound.")?;

        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut output = options.open(&destination).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "MP4 destination already exists; Motionwright never overwrites user files."
                    .to_string()
            } else {
                "Unable to create the chosen MP4 export destination.".to_string()
            }
        })?;
        let transfer = (|| -> Result<u64, String> {
            let mut copied = 0_u64;
            let mut digest = Sha256::new();
            let mut buffer = [0u8; 128 * 1024];
            loop {
                let read = input
                    .read(&mut buffer)
                    .map_err(|_| "MP4 source read failed.")?;
                if read == 0 {
                    break;
                }
                copied = copied
                    .checked_add(read as u64)
                    .ok_or("MP4 export byte count overflow.")?;
                if copied > expected_bytes || copied > MAX_MASTER_BYTES {
                    return Err("MP4 source changed or exceeded its recorded byte budget.".into());
                }
                output
                    .write_all(&buffer[..read])
                    .map_err(|_| "MP4 export write failed.")?;
                digest.update(&buffer[..read]);
            }
            output
                .sync_all()
                .map_err(|_| "MP4 export durability sync failed.")?;
            if copied != expected_bytes || hex::encode(digest.finalize()) != self.sha256 {
                return Err("MP4 export SHA-256 differs from the verified native master.".into());
            }
            Ok(copied)
        })();
        drop(output);
        let size_bytes = match transfer {
            Ok(size) => size,
            Err(reason) => {
                // Only the freshly-created failed destination is removed;
                // nothing pre-existing is ever modified or deleted.
                let _ = fs::remove_file(&destination);
                return Err(reason);
            }
        };
        Ok(MasterExportReceipt {
            destination: destination.to_string_lossy().to_string(),
            size_bytes,
            sha256: self.sha256.clone(),
            revision: self.revision,
            deliverable_id: self.deliverable_id,
            source_current: true,
            integrity_manifest_path: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn artifact_for(project: &Project, content: &[u8]) -> MltAvMasterEvidence {
        serde_json::from_value(json!({
            "project_resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "deliverable_id": project.deliverables[0].id,
            "motion_segment_id": "rendered-scope",
            "frame_rate": {"num": 30, "den": 1},
            "frame_count": 60,
            "mezzanine": {"profile": "ffv1"},
            "source_audio": {"relative_path": "voice.wav", "sha256": "a".repeat(64), "sample_rate": 48000, "channels": 2},
            "master": {
                "profile": "h264-aac-mp4",
                "frame_count": 60,
                "artifact": {"path": "signed-master.mp4", "sha256": hex::encode(Sha256::digest(content))}
            },
            "decoded_audio": {"sha256": "b".repeat(64)},
            "sync": null
        })).unwrap()
    }

    fn project() -> Project {
        Project::new("Native AV delivery fixture").unwrap()
    }

    #[test]
    fn original_multisegment_master_can_export_but_captioned_master_never_inherits_the_grant() {
        assert_eq!(
            multisegment_review_only(
                "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved",
                None,
            ),
            Some(false)
        );
        assert_eq!(
            multisegment_review_only(
                "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved",
                Some(2),
            ),
            Some(true)
        );
        for count in [None, Some(0), Some(97)] {
            assert_eq!(
                multisegment_review_only(
                    "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved",
                    count,
                ),
                None,
                "missing or over-budget captions cannot mint a token",
            );
        }
        assert_eq!(
            multisegment_review_only(
                "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved",
                Some(2),
            ),
            None,
            "captioned source cannot adopt uncaptioned delivery authority",
        );
        assert_eq!(multisegment_review_only("unknown-source", Some(2)), None);
    }

    #[test]
    fn real_sha_bound_captioned_master_token_is_read_only_even_with_integrity_manifest() {
        let owner = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        let project = project();
        let media = b"\0\0\0\x18ftypisom\0\0\0\0private-native-frames";
        let file = owner.path().join("captioned-source-review.mp4");
        fs::write(&file, media).unwrap();
        let source = DeliverySource {
            owner_root: owner.path().to_path_buf(),
            relative_path: "captioned-source-review.mp4".into(),
            sha256: hex::encode(Sha256::digest(media)),
            revision: project.revision,
            deliverable_id: project.deliverables[0].id,
            project_id: project.id,
            generation: project.generation,
            frame_rate_num: 30,
            frame_rate_den: 1,
            frame_count: 33,
            review_only: true,
        };
        assert!(!source.export_allowed());
        assert_eq!(source.review_bytes().unwrap(), media);
        let target = destination.path().join("final.mp4");
        let filename = target.to_str().unwrap();
        assert!(
            source
                .copy_to(filename)
                .unwrap_err()
                .contains("review-only")
        );
        assert!(
            source
                .copy_with_integrity(filename, true, "a".repeat(40).as_str())
                .unwrap_err()
                .contains("review-only")
        );
        assert!(!target.exists());
        assert!(
            !destination
                .path()
                .join("final.mp4.motionwright-integrity.json")
                .exists()
        );
        // A forged producer-side change without a fresh matching SHA cannot
        // alter even review-only bytes.
        fs::write(&file, b"forged MP4 payload").unwrap();
        assert!(source.review_bytes().is_err());
        assert!(!target.exists());
    }

    #[test]
    fn exact_native_master_can_export_verified_bytes_without_overwriting() {
        let owner = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let project = project();
        let content = b"\0\0\0\x18ftypisom\0\0\0\0video-bytes";
        fs::write(owner.path().join("signed-master.mp4"), content).unwrap();
        let evidence = artifact_for(&project, content);
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry.register(owner.path(), &evidence).unwrap();
        let source = registry.resolve(&project, token).unwrap();
        assert_eq!(source.review_bytes().unwrap(), content);
        let destination = target.path().join("my-native-master.mp4");
        let first = source.copy_to(destination.to_str().unwrap()).unwrap();
        assert_eq!(first.revision, project.revision);
        assert_eq!(first.sha256, hex::encode(Sha256::digest(content)));
        assert_eq!(fs::read(&destination).unwrap(), content);
        assert!(source.copy_to(destination.to_str().unwrap()).is_err());
        assert_eq!(fs::read(&destination).unwrap(), content);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&destination).unwrap().permissions().mode() & 0o077,
                0
            );
        }
    }

    const SDK_SOURCE_FIXTURE: &str = "8fa191250ae68274182570c65f067f7a60f85625";

    #[test]
    fn optional_portable_integrity_proof_binds_exact_exported_media_and_revision() {
        let owner = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        let project = project();
        let bytes = b"\0\0\0\x18ftypisom\0\0\0\0video-bytes";
        fs::write(owner.path().join("signed-master.mp4"), bytes).unwrap();
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry
            .register(owner.path(), &artifact_for(&project, bytes))
            .unwrap();
        let source = registry.resolve(&project, token).unwrap();

        let name = destination.path().join("real-output.mp4");
        let receipt = source
            .copy_with_integrity(name.to_str().unwrap(), true, SDK_SOURCE_FIXTURE)
            .unwrap();
        assert_eq!(receipt.size_bytes, bytes.len() as u64);
        assert_eq!(fs::read(&name).unwrap(), bytes);
        assert_eq!(receipt.sha256, hex::encode(Sha256::digest(bytes)));
        let proof = PathBuf::from(receipt.integrity_manifest_path.clone().unwrap());
        assert_eq!(
            proof.file_name().unwrap().to_str().unwrap(),
            "real-output.mp4.motionwright-integrity.json"
        );
        let json: serde_json::Value = serde_json::from_slice(&fs::read(&proof).unwrap()).unwrap();
        assert_eq!(json["schema"], "motionwright-media-integrity-v1");
        assert_eq!(
            json["scope"],
            "sha256-content-consistency-not-signed-attestation"
        );
        assert_eq!(json["media"]["filename"], "real-output.mp4");
        assert_eq!(json["media"]["sha256"], receipt.sha256);
        assert_eq!(json["media"]["size_bytes"], receipt.size_bytes);
        assert_eq!(json["origin"]["project_id"], project.id.to_string());
        assert_eq!(json["origin"]["generation"], project.generation.to_string());
        assert_eq!(
            json["origin"]["deliverable_id"],
            project.deliverables[0].id.to_string()
        );
        assert_eq!(json["origin"]["revision"], project.revision);
        assert_eq!(json["origin"]["native_profile"], "h264-aac-mp4");
        assert_eq!(json["origin"]["frame_count"], 60);
        assert_eq!(json["origin"]["frame_rate"], json!({"num":30,"den":1}));
        assert_eq!(
            json["origin"]["semwright_native_sdk_revision"],
            SDK_SOURCE_FIXTURE
        );
        assert!(!json.to_string().contains(owner.path().to_str().unwrap()));
        assert!(
            !json
                .to_string()
                .contains(destination.path().to_str().unwrap())
        );
        assert!(
            source
                .copy_with_integrity(name.to_str().unwrap(), true, SDK_SOURCE_FIXTURE,)
                .is_err()
        );
        assert_eq!(
            fs::read(&proof).unwrap(),
            serde_json::to_vec_pretty(&json)
                .unwrap()
                .into_iter()
                .chain(std::iter::once(b'\n'))
                .collect::<Vec<_>>()
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&proof).unwrap().permissions().mode() & 0o077,
                0
            );
        }
    }

    #[test]
    fn integrity_sidecar_collision_or_tampered_master_never_overwrites_user_files() {
        let owner = tempfile::tempdir().unwrap();
        let destination = tempfile::tempdir().unwrap();
        let project = project();
        let bytes = b"\0\0\0\x18ftypisom\0\0\0\0video-bytes";
        fs::write(owner.path().join("signed-master.mp4"), bytes).unwrap();
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry
            .register(owner.path(), &artifact_for(&project, bytes))
            .unwrap();
        let source = registry.resolve(&project, token).unwrap();
        let target = destination.path().join("final.mp4");
        let sidecar = destination
            .path()
            .join("final.mp4.motionwright-integrity.json");
        fs::write(&sidecar, b"previously issued receipt").unwrap();
        assert!(
            source
                .copy_with_integrity(target.to_str().unwrap(), true, SDK_SOURCE_FIXTURE,)
                .unwrap_err()
                .contains("already exists")
        );
        assert!(!target.exists());
        assert_eq!(fs::read(&sidecar).unwrap(), b"previously issued receipt");
        fs::remove_file(&sidecar).unwrap();

        fs::write(owner.path().join("signed-master.mp4"), b"tampered").unwrap();
        assert!(
            source
                .copy_with_integrity(target.to_str().unwrap(), true, SDK_SOURCE_FIXTURE,)
                .is_err()
        );
        assert!(!target.exists());
        assert!(!sidecar.exists());
        assert!(
            source
                .copy_with_integrity(target.to_str().unwrap(), true, "not-a-real-commit",)
                .is_err()
        );
        assert!(!target.exists());
        assert!(!sidecar.exists());
    }

    #[test]
    fn rejects_forged_session_wrong_revision_and_changed_canonical_bytes() {
        let owner = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let project = project();
        let content = b"\0\0\0\x18ftypisom\0\0\0\0video-bytes";
        fs::write(owner.path().join("signed-master.mp4"), content).unwrap();
        let evidence = artifact_for(&project, content);
        let registry = NativeMasterDeliveryRegistry::default();
        let mut forged = evidence.clone();
        forged.master["profile"] = json!("arbitrary-codec");
        assert!(registry.register(owner.path(), &forged).is_none());
        forged = evidence.clone();
        forged.master["artifact"]["sha256"] = json!("not-a-digest");
        assert!(registry.register(owner.path(), &forged).is_none());
        let token = registry.register(owner.path(), &evidence).unwrap();
        assert!(registry.resolve(&project, Uuid::new_v4()).is_err());
        let mut changed = project.clone();
        changed.revision += 1;
        assert!(registry.resolve(&changed, token).is_err());

        fs::write(owner.path().join("signed-master.mp4"), b"bad-source").unwrap();
        let source = registry.resolve(&project, token).unwrap();
        assert!(source.review_bytes().is_err());
        assert!(
            source
                .copy_to(
                    target
                        .path()
                        .join("should-not-export.mp4")
                        .to_str()
                        .unwrap()
                )
                .is_err()
        );
        assert!(!target.path().join("should-not-export.mp4").exists());
        assert!(validated_destination("../relative.mp4").is_err());
    }

    #[test]
    fn browser_review_refuses_oversized_or_invalid_mp4_without_reading_it() {
        let owner = tempfile::tempdir().unwrap();
        let project = project();
        let invalid = b"this is not an MP4 but has a valid SHA-256 hash";
        let source_file = owner.path().join("signed-master.mp4");
        fs::write(&source_file, invalid).unwrap();
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry
            .register(owner.path(), &artifact_for(&project, invalid))
            .unwrap();
        let media = registry.resolve(&project, token).unwrap();
        assert!(media.review_bytes().is_err());

        let much_larger = MAX_BROWSER_REVIEW_BYTES + 1;
        File::options()
            .write(true)
            .open(&source_file)
            .unwrap()
            .set_len(much_larger)
            .unwrap();
        let error = media.review_bytes().unwrap_err();
        assert!(error.contains("16 MiB"));
        assert!(error.contains("Export verified MP4"));
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_destination_parents_and_target_collisions() {
        use std::os::unix::fs::symlink;
        let target = tempfile::tempdir().unwrap();
        let linked = target.path().join("parent-link");
        let real = target.path().join("real");
        fs::create_dir(&real).unwrap();
        symlink(&real, &linked).unwrap();
        assert!(validated_destination(linked.join("master.mp4").to_str().unwrap()).is_err());
        assert!(validated_destination(real.join("master.mp4").to_str().unwrap()).is_ok());
    }

    /// Typed desktop-boundary fixture only: real canonical CAS/mixed WAV, but
    /// synthetic video/MP4 metadata and bytes. It is not a native renderer run,
    /// physical codec verification, or a claim that two scenes make two Films.
    fn canonical_mix_fixture_master(
        f: &crate::original_mix_native::test_support::MixFixture,
    ) -> MltMultisegmentOriginalMixMasterEvidence {
        use motionwright_native::production::{
            OriginalMixSourceRequest, prepare_original_mix_av_audio,
        };
        let project = &f.project;
        let id = project.deliverables[0].id;
        let preview = crate::original_mix_native::prepare_canonical_mix_pcm_preview(
            &f.service,
            project.id,
            &project.stamp(),
            id,
        )
        .unwrap();
        let prepared = prepare_original_mix_av_audio(
            &f.service,
            project.id,
            &project.stamp(),
            OriginalMixSourceRequest {
                plan: &preview.plan,
                frame_count: 60,
                fps_num: 30,
                fps_den: 1,
            },
            &f.owner_root,
        )
        .unwrap();
        let content = b"\0\0\0\x18ftypisom\0\0\0\0synthetic-review-source-not-native-media";
        fs::write(f.owner_root.join("canonical-mix-review.mp4"), content).unwrap();
        let resource = project.resource_key();
        let input_sha = "1".repeat(64);
        let video_sha = "2".repeat(64);
        let verified_video_segments = (0..2)
            .map(|index| {
                json!({
                    "segment_id":format!("synthetic-segment-{index}"),
                    "scene_ids":[project.scenes[index].id], "output_start_frame":index * 30,
                    "frame_count":30, "native_render_job_ref":format!("synthetic-job-{index}"),
                    "source_manifest_sha256":"3".repeat(64),
                    "artifact_path":format!("synthetic-segment-{index}.mkv"),
                    "artifact_sha256":"4".repeat(64), "artifact_bytes":20,
                })
            })
            .collect::<Vec<_>>();
        let video = serde_json::from_value(json!({
            "project_resource":resource, "generation":project.generation, "revision":project.revision,
            "deliverable_id":id, "recipe_sha256":input_sha, "frame_count":60,
            "native_frame_count_observed":60, "transport_pcm_audio":false,
            "native_render_profile":"lossless-video-only",
            "provider_project_cleanup":"not_requested_requires_foreground_broker_consent",
            "native_job_ref":"render:synthetic-review-fixture", "provider_revision":"5".repeat(64),
            "artifact_path":"synthetic-video-only.mkv", "artifact_sha256":video_sha, "artifact_bytes":40,
            "source":{
                "project_resource":resource, "generation":project.generation, "revision":project.revision,
                "deliverable_id":id, "input_sha256":input_sha, "fps_num":30, "fps_den":1,
                "total_frames":60, "verified_video_segments":verified_video_segments,
                "evidence_scope":"real-source-ffv1-segments-not-composited",
            },
            "evidence_scope":"actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master",
        })).unwrap();
        let captioned = project.deliverables[0].burn_in_captions;
        let measured_voice = serde_json::from_value(json!({
            "project_resource":resource, "generation":project.generation, "revision":project.revision,
            "deliverable_id":id, "voice_track_id":project.deliverables[0].voice_track_id.unwrap(),
            "audio_sha256":preview.plan.measured_voice.source_asset_sha256,
            "audio_size_bytes":44 + preview.plan.sample_frames * 4,
            "audio_pcm_frames":preview.plan.sample_frames, "audio_sample_rate":48000, "audio_channels":2,
            "video_input_sha256":input_sha, "video_segment_count":2, "total_frames":60,
            "fps_num":30, "fps_den":1, "final_profile":"h264-aac-mp4",
            "caption_cue_count":if captioned { Some(1) } else { None },
            "source_fingerprint":"6".repeat(64),
            "evidence_scope":if captioned {
                "verified-owner-audio-ffv1-native-captioned-source-not-muxed-mp4"
            } else { "verified-owner-audio-ffv1-inputs-not-muxed-mp4" },
        })).unwrap();
        let decoded_audio = json!({"root":"output", "path":"synthetic-decoded.wav",
            "sha256":"7".repeat(64), "bytes":44 + preview.plan.sample_frames * 4});
        let options = canonical_original_mix_film_options(project, id).unwrap();
        MltMultisegmentOriginalMixMasterEvidence {
            project_resource: resource, generation: project.generation, revision: project.revision,
            deliverable_id: id, frame_count: 60, video, measured_voice,
            original_mix: prepared.proof, staged_audio: prepared.audio.clone(),
            master: json!({"profile":"h264-aac-mp4", "frame_count":60, "fps_num":30, "fps_den":1,
                "sample_rate":48000, "channels":2, "video_sha256":video_sha,
                "audio_sha256":prepared.audio.sha256, "decoded_audio":decoded_audio,
                "artifact":{"root":"output", "path":"canonical-mix-review.mp4",
                    "sha256":hex::encode(Sha256::digest(content)), "bytes":content.len()}}),
            decoded_audio, canonical_dependency: preview.dependency,
            canonical_film_options_sha256: hex::encode(Sha256::digest(serde_json::to_vec(&options).unwrap())),
            canonical_motion_source: (0..2).map(|index| motionwright_native::production::CanonicalMotionCanvasFilmSource {
                // Typed boundary fixture, not a journal or native Film proof.
                segment_id: format!("synthetic-segment-{index}"), film_sha256: "a".repeat(64),
                plan_ref: "b".repeat(64), native_fingerprint: "c".repeat(64),
                native_job_ref: format!("synthetic-job-{index}"), connection_identity: "d".repeat(64),
                provider_generation: 42,
                completed_receipt_ids: std::array::from_fn(|stage|
                    Uuid::from_u128((index * 5 + stage + 1) as u128)),
            }).collect(),
            evidence_scope: if captioned {
                "actual-native-canonical-original-mix-h264-aac-caption-source-not-decoded-pixel-or-human-approved"
            } else {
                "actual-native-canonical-original-mix-h264-aac-source-bound-not-mastered-or-human-approved"
            }.into(),
        }
    }

    #[test]
    fn canonical_mix_keeps_voice_and_wav_distinct_and_never_mints_export_authority() {
        use crate::original_mix_native::test_support::{apply, fixture};
        use motionwright_domain::Change;
        let mut f = fixture();
        for captioned in [false, true] {
            if captioned {
                let mut profile = f.project.deliverables[0].clone();
                profile.captions = true;
                profile.burn_in_captions = true;
                f.project = apply(
                    &f.service,
                    &f.project,
                    "captioned-review-fixture",
                    Change::UpsertDeliverable { profile },
                );
            }
            let evidence = canonical_mix_fixture_master(&f);
            assert_ne!(
                evidence.measured_voice.audio_sha256,
                evidence.staged_audio.sha256
            );
            assert_eq!(
                evidence.measured_voice.audio_sha256,
                evidence.original_mix.measured_voice.source_asset_sha256
            );
            assert_eq!(
                evidence.original_mix.staged_wav_sha256,
                evidence.staged_audio.sha256
            );
            let registry = NativeMasterDeliveryRegistry::default();
            let token = registry
                .register_canonical_original_mix_review(&f.owner_root, &f.project, &evidence)
                .unwrap();
            let source = registry.resolve(&f.project, token).unwrap();
            assert!(!source.export_allowed());
            assert!(
                source
                    .review_bytes()
                    .unwrap()
                    .ends_with(b"synthetic-review-source-not-native-media")
            );
            let target = f.temporary.path().join(if captioned {
                "captioned-export.mp4"
            } else {
                "mix-export.mp4"
            });
            assert!(
                source
                    .copy_to(target.to_str().unwrap())
                    .unwrap_err()
                    .contains("review-only")
            );
            assert!(
                source
                    .copy_with_integrity(target.to_str().unwrap(), true, SDK_SOURCE_FIXTURE)
                    .unwrap_err()
                    .contains("review-only")
            );
            assert!(!target.exists());
            assert!(
                !PathBuf::from(format!("{}.motionwright-integrity.json", target.display()))
                    .exists()
            );
            assert!(
                f.service
                    .production_receipts(f.project.id, 256)
                    .unwrap()
                    .is_empty()
            );
        }
    }

    #[test]
    fn canonical_mix_rejects_wrong_receipts_source_hashes_clocks_and_authority_flags() {
        let f = crate::original_mix_native::test_support::fixture();
        let original = canonical_mix_fixture_master(&f);
        let registry = NativeMasterDeliveryRegistry::default();
        for case in 0..20 {
            let mut forged = original.clone();
            match case {
                0 => forged.measured_voice.audio_sha256 = forged.staged_audio.sha256.clone(),
                1 => forged.original_mix.staged_wav_sha256 = forged.measured_voice.audio_sha256.clone(),
                2 => forged.original_mix.original_music.source_asset_sha256 = "8".repeat(64),
                3 => forged.original_mix.original_mix.source_plan_sha256 = "8".repeat(64),
                4 => forged.canonical_dependency.audio_inputs_sha256 = "8".repeat(64),
                5 => forged.canonical_film_options_sha256 = "8".repeat(64),
                6 => forged.measured_voice.audio_pcm_frames -= 1,
                7 => forged.video.native_frame_count_observed = None,
                8 => forged.video.transport_pcm_audio = true,
                9 => forged.original_mix.original_mix.owner_release_approved = true,
                10 => forged.original_mix.original_mix.independently_verified_lufs = true,
                11 => forged.master["audio_sha256"] = json!(forged.measured_voice.audio_sha256),
                12 => forged.master["artifact"]["root"] = json!("foreign"),
                13 => forged.master["artifact"]["bytes"] = json!(1),
                14 => forged.evidence_scope = "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved".into(),
                15 => forged.canonical_motion_source.clear(),
                16 => forged.canonical_motion_source[0].native_job_ref = "foreign-motion-job".into(),
                17 => forged.canonical_motion_source[0].film_sha256.clear(),
                18 => {
                    let repeated = forged.canonical_motion_source[0].completed_receipt_ids;
                    forged.canonical_motion_source[1].completed_receipt_ids = repeated;
                }
                19 => forged.canonical_motion_source[0].completed_receipt_ids.swap(0, 1),
                _ => unreachable!(),
            }
            assert!(
                registry
                    .register_canonical_original_mix_review(&f.owner_root, &f.project, &forged)
                    .is_none(),
                "{case}"
            );
        }
        assert!(registry.entries.lock().unwrap().is_empty());
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
    }

    #[test]
    fn canonical_mix_review_token_rechecks_recipe_profile_and_physical_owner_bytes() {
        let f = crate::original_mix_native::test_support::fixture();
        let evidence = canonical_mix_fixture_master(&f);
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry
            .register_canonical_original_mix_review(&f.owner_root, &f.project, &evidence)
            .unwrap();
        assert!(registry.resolve(&f.project, Uuid::new_v4()).is_err());
        let mut foreign = f.project.clone();
        foreign.id = Uuid::new_v4();
        assert!(registry.resolve(&foreign, token).is_err());
        let mut stale = f.project.clone();
        stale.generation = Uuid::new_v4();
        assert!(registry.resolve(&stale, token).is_err());
        let mut stale = f.project.clone();
        stale.revision += 1;
        assert!(registry.resolve(&stale, token).is_err());
        let mut changed = f.project.clone();
        changed
            .production_design
            .delivery_recipes
            .get_mut(&evidence.deliverable_id)
            .unwrap()
            .original_mix
            .as_mut()
            .unwrap()
            .sfx_gain_db = -20.0;
        assert!(
            registry.resolve(&changed, token).is_err(),
            "same numeric stamp cannot hide a changed recipe dependency"
        );
        let mut changed = f.project.clone();
        changed.deliverables[0].language = "es".into();
        assert!(registry.resolve(&changed, token).is_err());
        fs::write(
            f.owner_root.join("canonical-mix-review.mp4"),
            b"tampered owner bytes",
        )
        .unwrap();
        let unavailable = registry
            .resolve(&f.project, token)
            .unwrap()
            .review_bytes()
            .unwrap_err();
        assert!(!unavailable.contains("use Export verified MP4"));
        assert!(unavailable.contains("Export remains unavailable"));
        assert!(
            registry
                .register_canonical_original_mix_review(&f.owner_root, &f.project, &evidence)
                .is_none()
        );
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
    }
}
