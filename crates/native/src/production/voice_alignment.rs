//! Backend-only voice alignment qualification and one-shot atomic application.
#[cfg(all(test, unix))]
mod caller_tests;
mod evidence;
#[cfg(test)]
mod tests;

use super::{AuthenticatedSdkResult, ProductionCoordinator, request_digest, storage_error};
use motionwright_domain::{
    AlignmentEvidence, Change, Project, RationalTime, RevisionStamp, TranscriptSegment,
    VoiceAlignmentApplication, VoiceAlignmentReceiptRef,
};
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyReceiptObservation, voice_alignment_receipt_sha256_v1,
};
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult, Value, json, types::SourceKind,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};
use uuid::Uuid;

const COMMAND: &str = "driver.audio-analysis.voice.align";
const PROVIDER: &str = "driver:audio-analysis";
const TOOL: &str = "audio-alignment-launcher";
const ENGINE: &str = "WAV2VEC2_ASR_BASE_960H/torchaudio-2.8.0+cpu/acc007-en-ctc-1";

/// User intent, never execution evidence or permission to install Measured data.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiceAlignmentIntent {
    pub request_id: Uuid,
    pub voice_track_id: Uuid,
    pub transcript_version: u64,
    pub transcript_text: String,
}

/// A fresh qualified result retained in native memory. Stored JSON cannot mint it.
pub struct QualifiedVoiceAlignment {
    project_id: Uuid,
    expected: RevisionStamp,
    apply_request_id: String,
    change: Change,
    apply_attempted: bool,
    cancelled: bool,
    runtime_job_id: String,
    candidate_sha256: String,
    execution_sha256: String,
    runtime_stdout_sha256: String,
    runtime_stdout_bytes: usize,
    admission_binding_sha256: String,
}

// This type has no Deserialize, Clone or public constructor. Its eventual
// factory must bind the actual current authenticated provider registration to
// the admitted driver/manifest/tool/runtime/model bytes and Host input mount.
// A descriptor hash alone, config JSON, CI success or execution.json cannot
// construct it. Provider generation/catalog revision are session observations.
struct ObservedAlignmentAdmission {
    connection_identity: String,
    provider_generation: u64,
    catalog_revision: u64,
    descriptor_sha256: String,
    provider_version: String,
    capability_version: String,
    source_commit: String,
    admission_binding_sha256: String,
    native_input_root: PathBuf,
}

struct PreparedAlignment {
    project_id: Uuid,
    expected: RevisionStamp,
    intent: VoiceAlignmentIntent,
    asset_id: Uuid,
    request: Value,
    args: Value,
    source: evidence::SourceEvidence,
}

fn rejected(message: &str) -> Error {
    Error::new(ErrorCode::Conflict, message)
}

fn checked(condition: bool, message: &str) -> NativeResult<()> {
    if condition {
        Ok(())
    } else {
        Err(rejected(message))
    }
}

fn closed(value: &Value, keys: &[&str]) -> NativeResult<()> {
    let object = value
        .as_object()
        .ok_or_else(|| rejected("Expected closed alignment object"))?;
    checked(
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key)),
        "Unexpected or missing alignment field",
    )
}

fn token(value: &Value) -> NativeResult<&str> {
    let value = value
        .as_str()
        .ok_or_else(|| rejected("Expected native identifier"))?;
    checked(
        !value.is_empty() && value.len() <= 256 && !value.chars().any(char::is_control),
        "Native identifier is invalid",
    )?;
    Ok(value)
}

fn rational(numerator: u64, denominator: u64) -> NativeResult<RationalTime> {
    let numerator = i64::try_from(numerator)
        .map_err(|_| rejected("Reconstructed rational numerator exceeds the domain range"))?;
    let denominator = i64::try_from(denominator)
        .map_err(|_| rejected("Reconstructed rational denominator exceeds the domain range"))?;
    // Reconstructed integer frame/sample counts use the domain constructor.
    // The public string wire format is not an evidence ingress here.
    RationalTime::new(numerator, denominator)
        .map_err(|_| rejected("Reconstructed rational time is invalid"))
}

fn current_project(project: &Project, expected: &RevisionStamp) -> NativeResult<()> {
    checked(
        expected.resource == project.resource_key()
            && expected.generation == project.generation
            && expected.revision == project.revision,
        "Voice alignment source revision changed",
    )
}

impl ProductionCoordinator {
    fn observe_alignment_admission(&self) -> NativeResult<ObservedAlignmentAdmission> {
        // Deliberate integration boundary, not a fallback or a synthetic grant.
        // The runtime owner must bind the authenticated current registration.
        // No dispatch, staging write or project mutation occurs while absent.
        Err(Error::new(
            ErrorCode::Unsupported,
            "The authenticated native alignment runtime profile has not been admitted",
        ))
    }

    /// Prepare a backend-only result; this does not install transcript segments.
    pub async fn prepare_voice_alignment(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        intent: VoiceAlignmentIntent,
        cancelled: &AtomicBool,
    ) -> NativeResult<QualifiedVoiceAlignment> {
        let admission = self.observe_alignment_admission()?;
        self.prepare_voice_alignment_with_admission(
            project_id, expected, intent, admission, cancelled,
        )
        .await
    }

    async fn prepare_voice_alignment_with_admission(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        intent: VoiceAlignmentIntent,
        admission: ObservedAlignmentAdmission,
        cancelled: &AtomicBool,
    ) -> NativeResult<QualifiedVoiceAlignment> {
        checked(
            !cancelled.load(Ordering::SeqCst),
            "Voice alignment was cancelled before dispatch",
        )?;
        checked(
            !intent.request_id.is_nil()
                && !intent.voice_track_id.is_nil()
                && (1..=9_007_199_254_740_991).contains(&intent.transcript_version)
                && evidence::transcript_is_supported(&intent.transcript_text),
            "Voice alignment intent is outside the admitted bounded profile",
        )?;
        admission.check_connection(self)?;
        let (project, wav) = {
            let handle = self.service.store();
            let store = handle.lock();
            let project = store.load_project(project_id).map_err(storage_error)?;
            current_project(&project, expected)?;
            let track = project
                .audio
                .voice_tracks
                .iter()
                .find(|track| track.id == intent.voice_track_id)
                .ok_or_else(|| rejected("Selected voice is absent"))?;
            checked(
                project.audio.active_voice_track_id == Some(track.id)
                    && track.sample_rate_hz == 16_000
                    && track.channels == 1,
                "Select an original 16 kHz mono voice for this profile",
            )?;
            checked(
                !project
                    .audio
                    .transcript
                    .iter()
                    .any(|segment| segment.voice_track_id == track.id),
                "This add-only alignment profile cannot replace an existing transcript",
            )?;
            checked(
                project.assets.iter().any(|asset| {
                    asset.id == track.asset_id
                        && asset.content_sha256.as_deref() == Some(track.source_sha256.as_str())
                        && matches!(
                            asset.media_type.as_str(),
                            "audio/wav" | "audio/wave" | "audio/x-wav"
                        )
                }),
                "Selected voice does not match its original WAV asset",
            )?;
            let wav = store
                .read_blob(&track.source_sha256, evidence::MAX_SOURCE_BYTES as u64)
                .map_err(storage_error)?;
            (project, wav)
        };
        // The Store mutex is released before filesystem staging or any await.
        let source = evidence::inspect_source(&wav).map_err(rejected)?;
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == intent.voice_track_id)
            .ok_or_else(|| rejected("Selected voice is absent"))?;
        checked(
            track.source_sha256 == source.sha256
                && track.measured_duration == rational(source.sample_frames as u64, 16_000)?,
            "Original voice bytes and measured duration differ",
        )?;
        let request = json!({
            "schema":"motionwright.alignment-request/1","request_id":intent.request_id,
            "project_id":project.id,"project_generation":project.generation,
            "project_revision":project.revision,"voice_track_id":track.id,"asset_id":track.asset_id,
            "source_commit":admission.source_commit,"language":"en",
            "transcript":{"version":intent.transcript_version,"text":intent.transcript_text,
                "sha256":evidence::digest(intent.transcript_text.as_bytes())},
            "source":{"sha256":source.sha256,"size_bytes":source.size_bytes,
                "sample_rate_hz":16000,"channels":1,"sample_frames":source.sample_frames},
            "runtime":evidence::runtime_request(),
        });
        evidence::validate_request(&request, &source).map_err(rejected)?;
        let file_name = format!("{}.wav", intent.request_id);
        let args = json!({"file_name":file_name,"request":request});
        let prepared = PreparedAlignment {
            project_id,
            expected: expected.clone(),
            intent,
            asset_id: track.asset_id,
            request: args["request"].clone(),
            args,
            source,
        };
        let production_request = prepared.intent.request_id.to_string();
        checked(
            self.service
                .latest_production_receipt(project_id, &production_request)
                .map_err(storage_error)?
                .is_none(),
            "Existing production attempt requires observation; it cannot be dispatched again",
        )?;
        checked(
            !cancelled.load(Ordering::SeqCst),
            "Voice alignment was cancelled before staging",
        )?;
        stage_source(&admission.native_input_root, &file_name, &wav)?;
        checked(
            !cancelled.load(Ordering::SeqCst),
            "Voice alignment was cancelled before dispatch",
        )?;
        // Creating a retained runtime job is an external mutation. Preserve
        // uncertainty and receipt custody even though the Project is unchanged.
        let outcome = self
            .execute_with_receipt_origin(
                project_id,
                expected,
                &production_request,
                COMMAND,
                prepared.args.clone(),
                true,
            )
            .await?;
        let observed = outcome.into_authenticated_result().ok_or_else(|| {
            rejected("A completed receipt replay cannot qualify a fresh alignment")
        })?;
        checked(
            !cancelled.load(Ordering::SeqCst),
            "Voice alignment completed after cancellation; evidence is retained but not applied",
        )?;
        self.qualify_voice_alignment(*observed, prepared, &admission)
    }
}

impl ObservedAlignmentAdmission {
    fn check_connection(&self, coordinator: &ProductionCoordinator) -> NativeResult<()> {
        checked(
            self.connection_identity == coordinator.client.connection().identity()?
                && self.provider_generation > 0
                && self.catalog_revision > 0
                && evidence::is_hex(&self.descriptor_sha256, 64)
                && evidence::is_hex(&self.admission_binding_sha256, 64)
                && evidence::is_hex(&self.source_commit, 40)
                && !self.provider_version.is_empty()
                && self.provider_version.len() <= 256
                && !self.capability_version.is_empty()
                && self.capability_version.len() <= 256,
            "Authenticated alignment admission is not bound to this connection",
        )
    }

    fn check_result(&self, result: &Value) -> NativeResult<()> {
        closed(
            result,
            &["data", "execution", "warnings", "provenance_checked"],
        )?;
        checked(
            result["provenance_checked"] == json!(true) && result["warnings"] == json!([]),
            "Native result has unchecked provenance or warnings",
        )?;
        let execution = &result["execution"];
        checked(
            execution["backend"] == json!(PROVIDER)
                && execution["dry_run"] == json!(false)
                && execution["policy_decision"] == json!("allow")
                && execution["fallbacks_attempted"] == json!([]),
            "Alignment did not execute through the admitted provider policy",
        )?;
        let provenance = &execution["provenance"];
        closed(
            provenance,
            &[
                "provider",
                "source",
                "provider_version",
                "capability_version",
                "descriptor_sha256",
                "untrusted_metadata",
                "catalog_revision",
                "execution_provider",
                "provider_generation",
            ],
        )?;
        checked(
            provenance["provider"] == json!(PROVIDER)
                && provenance["source"] == json!(SourceKind::Driver)
                && provenance["provider_version"] == json!(self.provider_version)
                && provenance["capability_version"] == json!(self.capability_version)
                && provenance["descriptor_sha256"] == json!(self.descriptor_sha256)
                && provenance["untrusted_metadata"] == json!(false)
                && provenance["catalog_revision"] == json!(self.catalog_revision)
                && provenance["execution_provider"] == json!(PROVIDER)
                && provenance["provider_generation"] == json!(self.provider_generation),
            "Result is not from the authenticated current alignment registration",
        )
    }
}

impl ProductionCoordinator {
    fn qualify_voice_alignment(
        &self,
        observed: AuthenticatedSdkResult,
        prepared: PreparedAlignment,
        admission: &ObservedAlignmentAdmission,
    ) -> NativeResult<QualifiedVoiceAlignment> {
        admission.check_connection(self)?;
        let receipt = &observed.completed_receipt;
        checked(
            observed.mutation
                && receipt.stage == "completed"
                && !receipt.id.is_nil()
                && receipt.project_id == prepared.project_id
                && receipt.generation == prepared.expected.generation
                && receipt.revision == prepared.expected.revision
                && receipt.request_id == prepared.intent.request_id.to_string()
                && receipt.command == COMMAND
                && receipt.request_sha256 == request_digest(COMMAND, &prepared.args)?,
            "Fresh native receipt is bound to different alignment input",
        )?;
        token(&receipt.payload["broker_request_id"])?;
        let result = &receipt.payload["result"];
        admission.check_result(result)?;
        let data = &result["data"];
        closed(data, &["runtime_output", "runtime_job"])?;
        let output = evidence::decode_runtime_output(&data["runtime_output"]).map_err(rejected)?;
        let checked_evidence =
            evidence::validate(&prepared.request, &prepared.source, &output.envelope)
                .map_err(rejected)?;
        let job = &data["runtime_job"];
        closed(
            job,
            &[
                "id",
                "status",
                "tool_name",
                "request_id",
                "request_sha256",
                "candidate_sha256",
                "execution_sha256",
            ],
        )?;
        let job_id = token(&job["id"])?;
        checked(
            job_id.len() <= 128
                && job_id
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-'),
            "Runtime job identifier is outside the admitted SDK contract",
        )?;
        checked(
            job["status"] == json!("succeeded")
                && job["tool_name"] == json!(TOOL)
                && job["request_id"] == json!(prepared.intent.request_id)
                && job["request_sha256"] == json!(checked_evidence.request_sha256)
                && job["candidate_sha256"] == json!(checked_evidence.candidate_sha256)
                && job["execution_sha256"] == json!(checked_evidence.execution_sha256),
            "Terminal runtime job is bound to different source or output",
        )?;
        // The producer may report succeeded only after wait Ok and exit_code 0.
        // Its ability to make that claim is established by admission + the fresh
        // native invocation, never by execution.json or a stored receipt.
        checked(
            checked_evidence.unresolved_word_count == 0
                && checked_evidence
                    .words
                    .iter()
                    .all(|word| word.frames.is_some()),
            "Alignment contains Unknown words; no transcript will be installed",
        )?;
        current_project(
            &self
                .service
                .project(prepared.project_id)
                .map_err(storage_error)?,
            &prepared.expected,
        )?;
        let receipt_ref = VoiceAlignmentReceiptRef {
            id: receipt.id,
            request_id: receipt.request_id.clone(),
            request_sha256: receipt.request_sha256.clone(),
            command: receipt.command.clone(),
            receipt_sha256: voice_alignment_receipt_sha256_v1(receipt).map_err(storage_error)?,
        };
        let mut segments = Vec::with_capacity(checked_evidence.words.len());
        for word in checked_evidence.words {
            let (start, end) = word
                .frames
                .ok_or_else(|| rejected("Unknown word has no interval"))?;
            segments.push(TranscriptSegment {
                id: Uuid::now_v7(),
                voice_track_id: prepared.intent.voice_track_id,
                start: rational(start as u64, 50)?,
                end: rational(end as u64, 50)?,
                text: word.text,
                speaker: None,
                alignment: AlignmentEvidence::Measured {
                    engine: ENGINE.into(),
                    source_sha256: prepared.source.sha256.clone(),
                    confidence_millis: None,
                },
            });
        }
        let application = VoiceAlignmentApplication {
            profile: evidence::CONTRACT.into(),
            voice_track_id: prepared.intent.voice_track_id,
            asset_id: prepared.asset_id,
            source_sha256: prepared.source.sha256,
            transcript_version: prepared.intent.transcript_version,
            transcript_sha256: evidence::digest(prepared.intent.transcript_text.as_bytes()),
            transcript_text: prepared.intent.transcript_text,
            receipt: receipt_ref,
            segments,
            // No cue ownership has been granted by this initial profile.
            cues: Vec::new(),
        };
        Ok(QualifiedVoiceAlignment {
            project_id: prepared.project_id,
            expected: prepared.expected,
            apply_request_id: format!("voice-alignment-apply:{}", prepared.intent.request_id),
            change: Change::ApplyVoiceAlignment { application },
            apply_attempted: false,
            cancelled: false,
            runtime_job_id: job_id.to_owned(),
            candidate_sha256: checked_evidence.candidate_sha256,
            execution_sha256: checked_evidence.execution_sha256,
            runtime_stdout_sha256: output.sha256,
            runtime_stdout_bytes: output.size_bytes,
            admission_binding_sha256: admission.admission_binding_sha256.clone(),
        })
    }

    /// Apply one already-qualified native object. A second uncertain attempt is
    /// never issued. Cancellation is checked until the atomic Store handoff;
    /// a commit already observed is returned even if cancellation arrived later.
    pub fn apply_qualified_voice_alignment(
        &self,
        alignment: &mut QualifiedVoiceAlignment,
        cancelled: &AtomicBool,
    ) -> NativeResult<Value> {
        let observation = self
            .service
            .store()
            .lock()
            .observe_apply_receipt_v1(
                alignment.project_id,
                &alignment.expected,
                &alignment.apply_request_id,
                &alignment.change,
                APPLY_REQUEST_ENCODING_V1,
            )
            .map_err(storage_error)?;
        match observation {
            ApplyReceiptObservation::KnownVerifiedV1 { binding } => {
                return Ok(json!({
                    "status":"ALREADY_APPLIED","replayed":true,"binding":binding,
                    "runtime_job_id":alignment.runtime_job_id,
                    "candidate_sha256":alignment.candidate_sha256,
                    "execution_sha256":alignment.execution_sha256,
                    "runtime_stdout_sha256":alignment.runtime_stdout_sha256,
                    "runtime_stdout_bytes":alignment.runtime_stdout_bytes,
                    "admission_binding_sha256":alignment.admission_binding_sha256,
                    "human_acceptance":false,
                }));
            }
            ApplyReceiptObservation::NotFound => {}
            _ => {
                return Err(rejected(
                    "Alignment apply receipt is not a verified commit; reconcile without retrying",
                ));
            }
        }
        alignment.cancelled |= cancelled.load(Ordering::SeqCst);
        checked(
            !alignment.cancelled,
            "Qualified voice alignment was cancelled before application",
        )?;
        if alignment.apply_attempted {
            return Err(rejected("An alignment apply was already attempted; missing evidence does not authorize retry")
                .uncertain());
        }
        // Mark before the fallible mutation. Even storage errors cannot reset it.
        alignment.apply_attempted = true;
        alignment.cancelled |= cancelled.load(Ordering::SeqCst);
        checked(
            !alignment.cancelled,
            "Qualified voice alignment was cancelled before application",
        )?;
        let outcome = self
            .service
            .apply(
                alignment.project_id,
                &alignment.expected,
                &alignment.apply_request_id,
                &alignment.change,
            )
            .map_err(storage_error)?;
        Ok(json!({
            "status":"APPLIED","replayed":outcome.replayed,"outcome":outcome,
            "runtime_job_id":alignment.runtime_job_id,
            "candidate_sha256":alignment.candidate_sha256,
            "execution_sha256":alignment.execution_sha256,
            "runtime_stdout_sha256":alignment.runtime_stdout_sha256,
            "runtime_stdout_bytes":alignment.runtime_stdout_bytes,
            "admission_binding_sha256":alignment.admission_binding_sha256,
            "human_acceptance":false,
        }))
    }
}

fn checked_directory(path: &Path) -> NativeResult<()> {
    checked(path.is_absolute(), "Alignment input root must be absolute")?;
    let mut cursor = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir | Component::CurDir => {
                return Err(rejected(
                    "Alignment input root contains a traversal component",
                ));
            }
            Component::Prefix(_) | Component::RootDir => {
                cursor.push(component.as_os_str());
                continue;
            }
            Component::Normal(_) => cursor.push(component.as_os_str()),
        }
        let metadata = fs::symlink_metadata(&cursor)
            .map_err(|_| rejected("Alignment input root is unavailable"))?;
        checked(
            !metadata.file_type().is_symlink() && metadata.is_dir(),
            "Alignment input root contains a link or non-directory",
        )?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            checked(
                metadata.file_attributes() & 0x400 == 0,
                "Alignment input root contains a reparse point",
            )?;
        }
    }
    Ok(())
}

fn stage_source(root: &Path, file_name: &str, bytes: &[u8]) -> NativeResult<()> {
    checked_directory(root)?;
    checked(
        !file_name.is_empty()
            && file_name.len() <= 200
            && file_name.is_ascii()
            && file_name.ends_with(".wav")
            && !file_name.starts_with('.')
            && !file_name.contains("..")
            && !file_name.contains('/')
            && !file_name.contains('\\')
            && bytes.len() <= evidence::MAX_SOURCE_BYTES,
        "Invalid bounded alignment source staging input",
    )?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(root.join(file_name)).map_err(|_| {
        rejected("Alignment source staging exists or failed; preserve and reconcile the attempt")
    })?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| {
            rejected("Alignment source staging may be partial; preserve and reconcile the attempt")
        })
}
