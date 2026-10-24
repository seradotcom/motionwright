use motionwright_domain::{Project, RevisionStamp};
use motionwright_native::production::{
    AudioAnalysisLane, MltAvMasterEvidence, MltMultisegmentAvMasterEvidence,
    ProductionConnection, ProductionCoordinator, ProductionReceiptCustody,
    VerifiedDeliveryAudioQcReport, VerifiedOriginalMixAudioMeasurements,
    read_verified_production_artifact, verified_native_media_path,
};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error as NativeError, ErrorCode};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
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

/// Public QC is always re-derived by the native proof reader. It is never a
/// cache, export permission, or a WebView-authored measurement/provenance object.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NativeAvAudioQcResponse {
    Report {
        report: Box<VerifiedDeliveryAudioQcReport>,
    },
    NotRequested,
    Unavailable {
        reason: AudioQcUnavailableReason,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioQcUnavailableReason {
    SessionUnavailable,
    SourceChanged,
    ConnectionUnavailable,
    ArtifactChanged,
    MeasurementUnavailable,
    UnresolvedReceipt,
    UnsupportedPlatform,
}

impl NativeAvAudioQcResponse {
    pub fn unavailable(reason: AudioQcUnavailableReason) -> Self {
        Self::Unavailable { reason }
    }

    /// Only the existing opaque native proof can produce a report. The public
    /// projection is inspected for its parent hash; the typed report itself is
    /// returned unchanged. No private receipt payload, path or selector crosses IPC.
    pub fn from_measurements(
        measurements: &VerifiedOriginalMixAudioMeasurements,
        coordinator: &ProductionCoordinator,
        expected_master_sha256: Option<&str>,
    ) -> Self {
        let Some(expected) = expected_master_sha256.filter(|digest| valid_sha256(digest)) else {
            return Self::unavailable(AudioQcUnavailableReason::ArtifactChanged);
        };
        let report = match measurements.delivery_audio_qc_report(coordinator) {
            Ok(report) => report,
            Err(error) => return Self::unavailable(audio_qc_error_reason(&error)),
        };
        let public = match serde_json::to_value(&report) {
            Ok(value) => value,
            Err(_) => return Self::unavailable(AudioQcUnavailableReason::MeasurementUnavailable),
        };
        if public.get("schema").and_then(serde_json::Value::as_str)
            != Some("motionwright.delivery-audio-qc-report/2")
            || public
                .get("master_artifact_sha256")
                .and_then(serde_json::Value::as_str)
                != Some(expected)
        {
            return Self::unavailable(AudioQcUnavailableReason::ArtifactChanged);
        }
        Self::Report {
            report: Box::new(report),
        }
    }
}

fn audio_qc_error_reason(error: &NativeError) -> AudioQcUnavailableReason {
    match &error.code {
        // Missing/inconsistent/UNKNOWN original receipts all remain unresolved;
        // this is not a claim that every Conflict was a dispatched UNKNOWN.
        ErrorCode::Conflict => AudioQcUnavailableReason::UnresolvedReceipt,
        ErrorCode::StaleReference => AudioQcUnavailableReason::SourceChanged,
        _ => AudioQcUnavailableReason::MeasurementUnavailable,
    }
}

pub fn audio_targets_requested(project: &Project, deliverable_id: Uuid) -> bool {
    project
        .production_design
        .delivery_recipes
        .get(&deliverable_id)
        .is_some_and(|recipe| recipe.audio_targets.is_some())
}

/// Preflight before RenderLocal consumption, staging, or multisegment visual
/// production. A targeted profile must never fall back to an unmeasured mux.
pub fn require_audio_qc_host(targeted: bool) -> Result<(), &'static str> {
    if targeted && !cfg!(target_os = "linux") {
        return Err("Saved audio targets require the supported Linux native meter; no master production was started.");
    }
    Ok(())
}


#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioQcInventoryRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioQcRecoveryRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub decoded_measurement_receipt_id: Uuid,
    pub expected_master_sha256: String,
}

#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NativeAvAudioQcInventoryResponse {
    Inventory {
        inventory: motionwright_native::production::AudioQcReceiptInventory,
    },
    Unavailable {
        reason: AudioQcUnavailableReason,
    },
}

fn with_audio_connection<T>(
    project: &Project,
    service: StudioService,
    connection_path: Option<&Path>,
    custody: &ProductionReceiptCustody,
    read: impl FnOnce(&ProductionCoordinator) -> T,
) -> Result<T, AudioQcUnavailableReason> {
    let path = connection_path.ok_or(AudioQcUnavailableReason::ConnectionUnavailable)?;
    let connection = ProductionConnection::load(path)
        .map_err(|_| AudioQcUnavailableReason::ConnectionUnavailable)?;
    if connection.resource != project.resource_key() {
        return Err(AudioQcUnavailableReason::ConnectionUnavailable);
    }
    let identity = connection
        .identity()
        .map_err(|_| AudioQcUnavailableReason::ConnectionUnavailable)?;
    let coordinator = ProductionCoordinator::with_receipt_custody(service, connection, custody)
        .map_err(|_| AudioQcUnavailableReason::ConnectionUnavailable)?;
    let result = read(&coordinator);
    let current = ProductionConnection::load(path)
        .map_err(|_| AudioQcUnavailableReason::ConnectionUnavailable)?;
    if current.identity().ok().as_deref() != Some(identity.as_str()) {
        return Err(AudioQcUnavailableReason::ConnectionUnavailable);
    }
    Ok(result)
}

pub fn read_audio_qc_inventory(
    project: &Project,
    deliverable_id: Uuid,
    service: StudioService,
    connection_path: Option<&Path>,
    custody: &ProductionReceiptCustody,
) -> NativeAvAudioQcInventoryResponse {
    let result = with_audio_connection(project, service, connection_path, custody, |coordinator| {
        coordinator
            .original_mix_audio_qc_receipt_inventory(project.id, &project.stamp(), deliverable_id)
            .map_err(|error| audio_qc_error_reason(&error))
    })
    .and_then(|result| result);
    match result {
        Ok(inventory) => NativeAvAudioQcInventoryResponse::Inventory { inventory },
        Err(reason) => NativeAvAudioQcInventoryResponse::Unavailable { reason },
    }
}

pub fn recover_audio_qc(
    project: &Project,
    request: &AudioQcRecoveryRequest,
    service: StudioService,
    connection_path: Option<&Path>,
    custody: &ProductionReceiptCustody,
) -> NativeAvAudioQcResponse {
    if project.id != request.project_id
        || project.generation != request.generation
        || project.revision != request.revision
        || !valid_sha256(&request.expected_master_sha256)
    {
        return NativeAvAudioQcResponse::unavailable(AudioQcUnavailableReason::SourceChanged);
    }
    if !cfg!(target_os = "linux") {
        return NativeAvAudioQcResponse::unavailable(AudioQcUnavailableReason::UnsupportedPlatform);
    }
    with_audio_connection(project, service, connection_path, custody, |coordinator| {
        match coordinator.recover_original_mix_audio_measurements_from_receipt(
            project.id,
            &project.stamp(),
            request.decoded_measurement_receipt_id,
            request.deliverable_id,
            &request.expected_master_sha256,
        ) {
            Ok(proof) => NativeAvAudioQcResponse::from_measurements(
                &proof,
                coordinator,
                Some(&request.expected_master_sha256),
            ),
            Err(error) => NativeAvAudioQcResponse::unavailable(audio_qc_error_reason(&error)),
        }
    })
    .unwrap_or_else(NativeAvAudioQcResponse::unavailable)
}

/// Trusted original caller identity, not the native measurement namespace.
/// Deliberately not serializable/deserializable and never accepted over IPC.
#[derive(Debug, Clone, PartialEq, Eq)]
struct AudioAnalysisSelector {
    request_id: String,
    lane: AudioAnalysisLane,
    connection_identity: String,
}

impl AudioAnalysisSelector {
    fn new(request_id: &str, lane: AudioAnalysisLane, connection_identity: &str) -> Option<Self> {
        if request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
            || !valid_sha256(connection_identity)
        {
            return None;
        }
        Some(Self {
            request_id: request_id.to_owned(),
            lane,
            connection_identity: connection_identity.to_owned(),
        })
    }
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
    /// A captioned master without *device-local decoded pixel verification*
    /// can be previewed from the owner root, NEVER exported as verified.
    review_only: bool,
    /// Session recovery hint only; every read must reopen the durable proof.
    audio_analysis: Option<AudioAnalysisSelector>,
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

impl NativeMasterDeliveryRegistry {
    /// Only the trusted post-coordinator path can mint an export token. The
    /// WebView can never supply master artifact paths, hashes or provenance.
    pub fn register(&self, owner_root: &Path, master: &MltAvMasterEvidence) -> Option<Uuid> {
        self.register_single(owner_root, master, None)
    }

    pub fn register_measured(
        &self,
        owner_root: &Path,
        master: &MltAvMasterEvidence,
        original_request_id: &str,
        connection_identity: &str,
    ) -> Option<Uuid> {
        let selector = AudioAnalysisSelector::new(
            original_request_id,
            AudioAnalysisLane::SingleSegment,
            connection_identity,
        )?;
        self.register_single(owner_root, master, Some(selector))
    }

    fn register_single(
        &self,
        owner_root: &Path,
        master: &MltAvMasterEvidence,
        audio_analysis: Option<AudioAnalysisSelector>,
    ) -> Option<Uuid> {
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
            audio_analysis,
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
        self.register_multiple(owner_root, evidence, None)
    }

    pub fn register_measured_multisegment(
        &self,
        owner_root: &Path,
        evidence: &MltMultisegmentAvMasterEvidence,
        original_request_id: &str,
        connection_identity: &str,
    ) -> Option<Uuid> {
        let selector = AudioAnalysisSelector::new(
            original_request_id,
            AudioAnalysisLane::Multisegment,
            connection_identity,
        )?;
        self.register_multiple(owner_root, evidence, Some(selector))
    }

    fn register_multiple(
        &self,
        owner_root: &Path,
        evidence: &MltMultisegmentAvMasterEvidence,
        audio_analysis: Option<AudioAnalysisSelector>,
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
            audio_analysis,
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
            audio_analysis: entry.audio_analysis,
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
    audio_analysis: Option<AudioAnalysisSelector>,
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
    fn audio_qc_source(
        &self,
        project: &Project,
    ) -> Result<Option<&AudioAnalysisSelector>, AudioQcUnavailableReason> {
        if self.project_id != project.id
            || self.generation != project.generation
            || self.revision != project.revision
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == self.deliverable_id)
        {
            return Err(AudioQcUnavailableReason::SourceChanged);
        }
        verified_native_media_path(
            &self.owner_root,
            &self.relative_path,
            &self.sha256,
            MAX_MASTER_BYTES,
        )
        .map_err(|_| AudioQcUnavailableReason::ArtifactChanged)?;
        match (
            audio_targets_requested(project, self.deliverable_id),
            self.audio_analysis.as_ref(),
        ) {
            (false, None) => Ok(None),
            (true, Some(selector)) => Ok(Some(selector)),
            (true, None) => Err(AudioQcUnavailableReason::MeasurementUnavailable),
            (false, Some(_)) => Err(AudioQcUnavailableReason::SourceChanged),
        }
    }

    fn audio_connection_matches(
        &self,
        connection: &ProductionConnection,
        selector: &AudioAnalysisSelector,
    ) -> bool {
        connection.resource == format!("project:{}", self.project_id)
            && connection.output_root == self.owner_root
            && connection.identity().ok().as_deref() == Some(selector.connection_identity.as_str())
    }

    /// A session handle selects an existing completed chain. This reader has
    /// no effect-grant input and does not dispatch: it cannot render, measure,
    /// append a receipt, retry an UNKNOWN, or mint a replacement handle.
    /// Process-restart lookup remains a separate bounded native contract.
    pub fn read_audio_qc(
        &self,
        project: &Project,
        service: StudioService,
        connection_path: Option<&Path>,
        custody: &ProductionReceiptCustody,
    ) -> NativeAvAudioQcResponse {
        let selector = match self.audio_qc_source(project) {
            Ok(Some(selector)) => selector,
            Ok(None) => return NativeAvAudioQcResponse::NotRequested,
            Err(reason) => return NativeAvAudioQcResponse::unavailable(reason),
        };
        if !cfg!(target_os = "linux") {
            return NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::UnsupportedPlatform,
            );
        }
        let Some(connection_path) = connection_path else {
            return NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::ConnectionUnavailable,
            );
        };
        let connection = match ProductionConnection::load(connection_path) {
            Ok(connection) if self.audio_connection_matches(&connection, selector) => connection,
            _ => {
                return NativeAvAudioQcResponse::unavailable(
                    AudioQcUnavailableReason::ConnectionUnavailable,
                );
            }
        };
        let coordinator =
            match ProductionCoordinator::with_receipt_custody(service, connection, custody) {
                Ok(coordinator) => coordinator,
                Err(_) => {
                    return NativeAvAudioQcResponse::unavailable(
                        AudioQcUnavailableReason::ConnectionUnavailable,
                    );
                }
            };
        let expected = RevisionStamp {
            resource: project.resource_key(),
            generation: self.generation,
            revision: self.revision,
        };
        let proof = match coordinator.reopen_original_mix_audio_measurements(
            self.project_id,
            &expected,
            &selector.request_id,
            self.deliverable_id,
            selector.lane,
        ) {
            Ok(proof) => proof,
            Err(error) => return NativeAvAudioQcResponse::unavailable(audio_qc_error_reason(&error)),
        };
        let report =
            NativeAvAudioQcResponse::from_measurements(&proof, &coordinator, Some(&self.sha256));
        // The selected connection file must still name the original owner and
        // runtime identity. A changed file is not a new read authority.
        match ProductionConnection::load(connection_path) {
            Ok(connection) if self.audio_connection_matches(&connection, selector) => report,
            _ => NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::ConnectionUnavailable,
            ),
        }
    }

    /// Explicitly refuse a delivery grant for a master whose native caption
    /// source is verified but whose *actual decoded master pixels* are not.
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
            return Err("Captioned native MP4 is review-only: independently decoded device-local caption pixels are not yet verified for export.".into());
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
            "MP4 review needs a valid SHA-256-verified owner artifact of at most 16 MiB. For longer masters use Export verified MP4."
                .to_string()
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
            return Err("Captioned native MP4 is review-only: source glyphs are not an exportable master pixel attestation.".into());
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

    // These are registry/read-boundary contract cases using explicit synthetic
    // bytes. They do not manufacture a meter report or native/media acceptance.
    #[test]
    fn measured_selector_retains_original_identity_across_registry_clone_only() {
        let owner = tempfile::tempdir().unwrap();
        let project = project();
        let bytes = b"\0\0\0\x18ftypisom\0\0\0\0selector-fixture";
        let evidence = artifact_for(&project, bytes);
        let registry = NativeMasterDeliveryRegistry::default();
        let request_id = format!("mwav-{}", "1".repeat(64));
        let identity = "2".repeat(64);
        let token = registry.register_measured(owner.path(), &evidence, &request_id, &identity).unwrap();
        let restored_view = registry.clone();
        let source = restored_view.resolve(&project, token).unwrap();
        let selector = source.audio_analysis.as_ref().unwrap();
        assert_eq!(selector.request_id, request_id);
        assert_eq!(selector.lane, AudioAnalysisLane::SingleSegment);
        assert_eq!(selector.connection_identity, identity);
        assert!(source.export_allowed());
        assert!(NativeMasterDeliveryRegistry::default().resolve(&project, token).is_err(),
            "a fresh app registry cannot invent restart recovery");
        assert!(restored_view.resolve(&project, Uuid::new_v4()).is_err());
        for _ in 0..MAX_DELIVERY_HANDLES {
            registry.register(owner.path(), &evidence).unwrap();
        }
        assert!(restored_view.resolve(&project, token).is_err(), "evicted token is not reusable");
    }

    #[test]
    fn measured_selector_rejects_invalid_private_identity_without_minting_a_handle() {
        let owner = tempfile::tempdir().unwrap();
        let project = project();
        let evidence = artifact_for(&project, b"synthetic");
        let registry = NativeMasterDeliveryRegistry::default();
        for request in ["".to_owned(), " ".to_owned(), "x\ny".to_owned(), "x".repeat(97)] {
            assert!(registry.register_measured(owner.path(), &evidence, &request, &"a".repeat(64)).is_none());
        }
        for identity in ["".to_owned(), "a".repeat(63), "A".repeat(64), "g".repeat(64)] {
            assert!(registry.register_measured(owner.path(), &evidence, "original-caller", &identity).is_none());
        }
        assert!(registry.entries.lock().unwrap().is_empty());
        let exact = " original-caller ";
        let token = registry.register_measured(owner.path(), &evidence, exact, &"b".repeat(64)).unwrap();
        assert_eq!(registry.resolve(&project, token).unwrap().audio_analysis.unwrap().request_id, exact,
            "do not trim or replace the original caller with an ma namespace");
    }

    #[test]
    fn qc_read_preflight_preserves_not_requested_and_refuses_missing_or_stale_measurements() {
        let owner = tempfile::tempdir().unwrap();
        let mut project = project();
        let bytes = b"\0\0\0\x18ftypisom\0\0\0\0read-contract";
        let path = owner.path().join("signed-master.mp4");
        fs::write(&path, bytes).unwrap();
        let registry = NativeMasterDeliveryRegistry::default();
        let evidence = artifact_for(&project, bytes);
        let legacy = registry.register(owner.path(), &evidence).unwrap();
        let source = registry.resolve(&project, legacy).unwrap();
        assert!(source.audio_qc_source(&project).unwrap().is_none());
        project.production_design.delivery_recipes.entry(project.deliverables[0].id).or_default().audio_targets =
            Some(serde_json::from_value(json!({"target_lufs": -23.0, "target_true_peak_dbtp": null})).unwrap());
        assert_eq!(source.audio_qc_source(&project), Err(AudioQcUnavailableReason::MeasurementUnavailable));
        let measured = registry.register_measured(owner.path(), &evidence, "original-caller", &"c".repeat(64)).unwrap();
        let source = registry.resolve(&project, measured).unwrap();
        assert_eq!(source.audio_qc_source(&project).unwrap().unwrap().request_id, "original-caller");
        let saved_project = project.clone();
        project.revision += 1;
        assert_eq!(source.audio_qc_source(&project), Err(AudioQcUnavailableReason::SourceChanged));
        project = saved_project.clone();
        project.generation = Uuid::new_v4();
        assert_eq!(source.audio_qc_source(&project), Err(AudioQcUnavailableReason::SourceChanged));
        project = saved_project.clone();
        project.deliverables.clear();
        assert_eq!(source.audio_qc_source(&project), Err(AudioQcUnavailableReason::SourceChanged));
        project = saved_project;
        project.production_design.delivery_recipes.clear();
        assert_eq!(source.audio_qc_source(&project), Err(AudioQcUnavailableReason::SourceChanged));
        assert_eq!(fs::read(&path).unwrap(), bytes);
        fs::write(&path, b"changed canonical master").unwrap();
        assert_eq!(source.audio_qc_source(&project), Err(AudioQcUnavailableReason::ArtifactChanged));
        assert_eq!(fs::read(&path).unwrap(), b"changed canonical master",
            "read failure must not restore or overwrite owner bytes");
    }

    #[test]
    fn qc_connection_selector_rejects_owner_or_connection_substitution() {
        let owner = tempfile::tempdir().unwrap();
        let project = project();
        // Identity-only policy fixture. No connection validation, executable or
        // native dispatcher is run by this contract test.
        let mut connection = ProductionConnection {
            schema: "motionwright-semwright-connection/1".into(),
            executable: owner.path().join("not-executed"),
            executable_sha256: "a".repeat(64),
            socket: owner.path().join("not-connected"),
            session_file: owner.path().join("not-opened"),
            output_root: owner.path().to_path_buf(),
            resource: project.resource_key(),
        };
        let identity = connection.identity().unwrap();
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry.register_measured(owner.path(), &artifact_for(&project, b"fixture"), "original", &identity).unwrap();
        let source = registry.resolve(&project, token).unwrap();
        let selector = source.audio_analysis.as_ref().unwrap();
        assert!(source.audio_connection_matches(&connection, selector));
        connection.session_file = owner.path().join("different-session");
        assert!(!source.audio_connection_matches(&connection, selector));
        connection.session_file = owner.path().join("not-opened");
        connection.output_root = owner.path().join("other-root");
        assert!(!source.audio_connection_matches(&connection, selector));
        connection.output_root = owner.path().to_path_buf();
        connection.resource = format!("project:{}", Uuid::new_v4());
        assert!(!source.audio_connection_matches(&connection, selector));
    }

    #[test]
    fn qc_read_request_rejects_private_authority_and_public_errors_remain_closed() {
        let project = project();
        let request = json!({"project_id":project.id,"generation":project.generation,"revision":project.revision,"export_token":Uuid::new_v4()});
        assert!(serde_json::from_value::<MasterReviewRequest>(request.clone()).is_ok());
        for key in ["request_id", "lane", "connection_identity", "owner_root", "master_sha256", "audio_measurements", "audio_qc"] {
            let mut forged = request.clone();
            forged[key] = json!("caller-injected");
            assert!(serde_json::from_value::<MasterReviewRequest>(forged).is_err(), "{key}");
        }
        let private_error = NativeError::new(ErrorCode::Conflict, "private/path request-and-receipt-detail");
        let reason = audio_qc_error_reason(&private_error);
        assert_eq!(reason, AudioQcUnavailableReason::UnresolvedReceipt);
        assert_eq!(serde_json::to_value(NativeAvAudioQcResponse::unavailable(reason)).unwrap(),
            json!({"kind":"unavailable","reason":"unresolved_receipt"}));
        assert_eq!(serde_json::to_value(NativeAvAudioQcResponse::NotRequested).unwrap(), json!({"kind":"not_requested"}));
        assert_eq!(audio_qc_error_reason(&NativeError::new(ErrorCode::StaleReference, "private")), AudioQcUnavailableReason::SourceChanged);
        assert_eq!(audio_qc_error_reason(&NativeError::new(ErrorCode::BackendFailed, "private")), AudioQcUnavailableReason::MeasurementUnavailable);
    }


    #[test]
    fn durable_audio_requests_refuse_private_context_and_keep_scope_exact() {
        let project = project();
        let inventory = json!({"project_id":project.id,"generation":project.generation,
            "revision":project.revision,"deliverable_id":project.deliverables[0].id});
        assert!(serde_json::from_value::<AudioQcInventoryRequest>(inventory.clone()).is_ok());
        let mut recovery = inventory.clone();
        recovery["decoded_measurement_receipt_id"] = json!(Uuid::new_v4());
        recovery["expected_master_sha256"] = json!("a".repeat(64));
        assert!(serde_json::from_value::<AudioQcRecoveryRequest>(recovery.clone()).is_ok());
        for field in [
            "caller_request_id",
            "lane",
            "connection_identity",
            "owner_root",
            "effect_grant",
            "audio_reopen",
            "proof",
            "export_token",
        ] {
            let mut value = inventory.clone();
            value[field] = json!("private");
            assert!(
                serde_json::from_value::<AudioQcInventoryRequest>(value).is_err(),
                "{field}"
            );
            let mut value = recovery.clone();
            value[field] = json!("private");
            assert!(
                serde_json::from_value::<AudioQcRecoveryRequest>(value).is_err(),
                "{field}"
            );
        }
        recovery["revision"] = json!(false);
        assert!(serde_json::from_value::<AudioQcRecoveryRequest>(recovery).is_err());
        let wire = NativeAvAudioQcInventoryResponse::Unavailable {
            reason: AudioQcUnavailableReason::UnresolvedReceipt,
        };
        assert_eq!(
            serde_json::to_value(wire).unwrap(),
            json!({"kind":"unavailable","reason":"unresolved_receipt"})
        );
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
            audio_analysis: None,
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
}
