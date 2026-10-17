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
        SceneFilmIntent, build_motion_canvas_segments,
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
use motionwright_storage::{ProductionReceipt, ProductionReceiptWatermark};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult, Value};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Write,
    path::Path,
};
use uuid::Uuid;

const SAMPLE_RATE: u64 = 48_000;
const MAX_SAMPLE_FRAMES: u64 = SAMPLE_RATE * 15;
const MAX_MIX_WAV_BYTES: u64 = 44 + MAX_SAMPLE_FRAMES * 4;
const MAX_SFX_SOURCE_ASSETS: usize = 64;
const MAX_SFX_SOURCE_BYTES: u64 = 16 * 1024 * 1024;
const SOURCE_SCOPE: &str =
    "verified-owner-original-mix-source-only-not-canonical-delivery-or-native-master";
const MOTION_SOURCE_MAX_SEGMENTS: usize = 64;
const MOTION_JOURNAL_MAX_RECEIPTS: u64 = 20_000;
const MOTION_JOURNAL_PAGE_SIZE: usize = 64;
// Application journal fields wrap the already bounded native output envelope.
const MOTION_JOURNAL_MAX_ROW_BYTES: usize = super::MAX_OUTPUT_BYTES + 4096;
const MOTION_JOURNAL_MAX_RETAINED_BYTES: usize = 64 * 1024 * 1024;

fn unsupported(reason: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, reason)
}

fn stale(reason: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, reason)
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

/// Complete canonical Film identity bound to the application's original
/// Motion Canvas receipts. This is source custody, not decoded-pixel approval.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CanonicalMotionCanvasFilmSource {
    pub segment_id: String,
    /// The existing SDK canonical_digest of the complete typed Film.
    pub film_sha256: String,
    pub plan_ref: String,
    pub native_fingerprint: String,
    pub native_job_ref: String,
    pub connection_identity: String,
    pub provider_generation: u64,
    /// Original completed rows: plan, apply, render-start, render-result, verify.
    pub completed_receipt_ids: [Uuid; 5],
}

struct CanonicalMotionSourceSnapshot {
    bindings: Vec<CanonicalMotionCanvasFilmSource>,
    watermark: ProductionReceiptWatermark,
}

fn motion_source_mismatch() -> Error {
    stale("Canonical Motion Canvas Film is not bound to its original source receipts")
}

fn motion_receipt_snapshot_changed() -> Error {
    stale(
        "Canonical Motion Canvas receipt snapshot changed or is incomplete; reconcile the original render without redispatch",
    )
}

fn unresolved_motion_source() -> Error {
    Error::new(
        ErrorCode::Conflict,
        "Canonical Motion Canvas source has an UNKNOWN original receipt; reconcile without redispatch",
    ).uncertain()
}

fn motion_journal_error(error: motionwright_storage::StorageError) -> Error {
    match error {
        motionwright_storage::StorageError::ReceiptStreamChanged => {
            motion_receipt_snapshot_changed()
        }
        other => storage_error(other),
    }
}

fn recheck_motion_watermark(
    service: &StudioService,
    project_id: Uuid,
    watermark: &ProductionReceiptWatermark,
) -> NativeResult<()> {
    if service
        .production_receipt_watermark(project_id)
        .map_err(motion_journal_error)?
        != *watermark
    {
        return Err(motion_receipt_snapshot_changed());
    }
    Ok(())
}

/// Traverse the complete bounded snapshot, not the latest-receipts UI window.
/// The service checks the same watermark around every keyset page.
fn visit_motion_receipts(
    service: &StudioService,
    project_id: Uuid,
    watermark: &ProductionReceiptWatermark,
    mut visit: impl FnMut(ProductionReceipt) -> NativeResult<()>,
) -> NativeResult<()> {
    if watermark.count > MOTION_JOURNAL_MAX_RECEIPTS {
        return Err(unsupported(
            "Canonical Motion Canvas source exceeds the bounded 20,000-receipt verification budget",
        ));
    }
    if (watermark.count == 0) != watermark.latest_id.is_none() {
        return Err(motion_receipt_snapshot_changed());
    }
    let mut before = None;
    let mut scanned = 0_u64;
    while scanned < watermark.count {
        let page = service
            .production_receipts_snapshot_page(
                project_id,
                watermark,
                before,
                MOTION_JOURNAL_PAGE_SIZE,
            )
            .map_err(motion_journal_error)?;
        if page.is_empty() || page.len() > MOTION_JOURNAL_PAGE_SIZE {
            return Err(motion_receipt_snapshot_changed());
        }
        for row in page {
            if row.project_id != project_id
                || watermark.latest_id.is_none_or(|head| row.id > head)
                || before.is_some_and(|cursor| row.id >= cursor)
            {
                return Err(motion_receipt_snapshot_changed());
            }
            before = Some(row.id);
            scanned += 1;
            if scanned > watermark.count {
                return Err(motion_receipt_snapshot_changed());
            }
            visit(row)?;
        }
    }
    recheck_motion_watermark(service, project_id, watermark)
}

#[derive(Default)]
struct MotionStageAuthority {
    connection_identity: Option<String>,
    provider_generation: Option<u64>,
    last_completed: Option<Uuid>,
}

struct ExpectedMotionStage<'a> {
    request_id: String,
    command: &'static str,
    args: Value,
    mutation: bool,
    job_ref: Option<&'a str>,
}

fn original_motion_stage<'a>(
    project: &Project,
    rows: &'a BTreeMap<String, Vec<ProductionReceipt>>,
    expected: ExpectedMotionStage<'_>,
    authority: &mut MotionStageAuthority,
    descriptors: &mut BTreeMap<&'static str, String>,
) -> NativeResult<(&'a Value, Uuid)> {
    let pair = rows
        .get(&expected.request_id)
        .ok_or_else(motion_source_mismatch)?;
    if pair.iter().any(|row| row.stage == "outcome_unknown") {
        return Err(unresolved_motion_source());
    }
    if pair.len() != 2 {
        return Err(motion_source_mismatch());
    }
    let dispatch = pair
        .iter()
        .find(|row| row.stage == "dispatching")
        .ok_or_else(motion_source_mismatch)?;
    let completed = pair
        .iter()
        .find(|row| row.stage == "completed")
        .ok_or_else(motion_source_mismatch)?;
    if dispatch.id >= completed.id
        || authority
            .last_completed
            .is_some_and(|prior| prior >= dispatch.id)
    {
        return Err(motion_source_mismatch());
    }
    let digest = super::request_digest(expected.command, &expected.args)?;
    for row in pair {
        if row.project_id != project.id
            || row.generation != project.generation
            || row.revision != project.revision
            || row.request_id != expected.request_id
            || row.command != expected.command
            || row.request_sha256 != digest
        {
            return Err(motion_source_mismatch());
        }
    }
    let connection = dispatch
        .payload
        .get("connection")
        .and_then(Value::as_str)
        .filter(|value| super::is_sha256(value))
        .ok_or_else(motion_source_mismatch)?;
    let job = expected
        .job_ref
        .map_or(Value::Null, |value| Value::String(value.into()));
    if dispatch.payload.get("mutation").and_then(Value::as_bool) != Some(expected.mutation)
        || dispatch.payload.get("job_ref") != Some(&job)
        || completed.payload.get("job_ref") != Some(&job)
        || completed
            .payload
            .get("broker_request_id")
            .and_then(Value::as_str)
            .is_none_or(|id| id.is_empty() || id.len() > 256 || id.chars().any(char::is_control))
    {
        return Err(motion_source_mismatch());
    }
    let result = completed
        .payload
        .get("result")
        .ok_or_else(motion_source_mismatch)?;
    if result.get("provenance_checked").and_then(Value::as_bool) != Some(true) {
        return Err(motion_source_mismatch());
    }
    let execution: semwright_native_sdk::types::Execution = serde_json::from_value(
        result
            .get("execution")
            .cloned()
            .ok_or_else(motion_source_mismatch)?,
    )
    .map_err(|_| motion_source_mismatch())?;
    let provenance = execution
        .provenance
        .as_ref()
        .ok_or_else(motion_source_mismatch)?;
    let generation = provenance
        .provider_generation
        .ok_or_else(motion_source_mismatch)?;
    // SDK54 classifies Driver descriptions as untrusted metadata. Their
    // invocation must still come from the exact expected provider worker.
    if execution.dry_run
        || !provenance.untrusted_metadata
        || provenance.provider != "driver:motion-canvas"
        || provenance.source != semwright_native_sdk::types::SourceKind::Driver
        || provenance.execution_provider.as_deref() != Some("driver:motion-canvas")
        || !super::is_sha256(&provenance.descriptor_sha256)
        || authority
            .connection_identity
            .as_deref()
            .is_some_and(|prior| prior != connection)
        || authority
            .provider_generation
            .is_some_and(|prior| prior != generation)
    {
        return Err(motion_source_mismatch());
    }
    authority.connection_identity = Some(connection.into());
    authority.provider_generation = Some(generation);
    authority.last_completed = Some(completed.id);
    if let Some(prior) = descriptors.insert(expected.command, provenance.descriptor_sha256.clone())
        && prior != provenance.descriptor_sha256
    {
        return Err(motion_source_mismatch());
    }
    Ok((
        result.get("data").ok_or_else(motion_source_mismatch)?,
        completed.id,
    ))
}

fn canonical_motion_source_snapshot(
    service: &StudioService,
    project: &Project,
    deliverable_id: Uuid,
    options: &FilmBuildOptions,
    rendered: &MotionCanvasRenderEvidence,
) -> NativeResult<CanonicalMotionSourceSnapshot> {
    if rendered.project_resource != project.resource_key()
        || rendered.generation != project.generation
        || rendered.revision != project.revision
        || rendered.deliverable_id != deliverable_id
        || rendered.frame_rate != options.frame_rate
    {
        return Err(motion_source_mismatch());
    }
    let films = build_motion_canvas_segments(project, deliverable_id, options)?;
    if films.is_empty()
        || films.len() > MOTION_SOURCE_MAX_SEGMENTS
        || films.len() != rendered.segments.len()
    {
        return Err(motion_source_mismatch());
    }
    let mut jobs = BTreeMap::new();
    for (index, (film, observed)) in films.iter().zip(&rendered.segments).enumerate() {
        if film.id != observed.segment_id
            || film.scene_ids != observed.scene_ids
            || film.frame_count != observed.frame_count
            || !super::is_sha256(&observed.plan_ref)
            || !super::is_sha256(&observed.fingerprint)
            || observed.job_ref.is_empty()
            || observed.job_ref.len() > 256
            || observed.job_ref.chars().any(char::is_control)
            || jobs.insert(observed.job_ref.as_str(), index).is_some()
        {
            return Err(motion_source_mismatch());
        }
    }
    let watermark = service
        .production_receipt_watermark(project.id)
        .map_err(motion_journal_error)?;
    let mut attempts = vec![None; films.len()];
    let mut ambiguous = false;
    let mut unknown = false;
    visit_motion_receipts(service, project.id, &watermark, |row| {
        if row.command != "driver.motion-canvas.render.result" {
            return Ok(());
        }
        let job = row
            .payload
            .pointer("/result/data/job_ref")
            .or_else(|| row.payload.get("job_ref"))
            .and_then(Value::as_str);
        let Some(&index) = job.and_then(|job| jobs.get(job)) else {
            return Ok(());
        };
        let suffix = format!(":motion:{index}:render-result");
        let Some(attempt) = row.request_id.strip_suffix(&suffix) else {
            return Ok(());
        };
        if attempt.trim().is_empty() || attempt.len() > 96 || attempt.chars().any(char::is_control)
        {
            return Err(motion_source_mismatch());
        }
        if row.stage == "outcome_unknown" {
            unknown = true;
        }
        if row.stage == "completed" && attempts[index].replace(attempt.to_owned()).is_some() {
            ambiguous = true;
        }
        Ok(())
    })?;
    if unknown {
        return Err(unresolved_motion_source());
    }
    if ambiguous || attempts.iter().any(Option::is_none) {
        return Err(motion_source_mismatch());
    }
    let attempt = attempts[0].as_deref().ok_or_else(motion_source_mismatch)?;
    if attempts
        .iter()
        .any(|value| value.as_deref() != Some(attempt))
    {
        return Err(motion_source_mismatch());
    }
    let stages = ["plan", "apply", "render-start", "render-result", "verify"];
    let mut requests = BTreeSet::new();
    for index in 0..films.len() {
        for stage in stages {
            requests.insert(format!("{attempt}:motion:{index}:{stage}"));
        }
    }
    let mut rows: BTreeMap<String, Vec<ProductionReceipt>> = BTreeMap::new();
    let mut retained_bytes = 0_usize;
    let mut extra_rows = false;
    visit_motion_receipts(service, project.id, &watermark, |row| {
        if !requests.contains(&row.request_id) {
            return Ok(());
        }
        // Preserve every matching request's disposition. In particular, no
        // generation/stage/command filter may hide older UNKNOWN/extra rows.
        unknown |= row.stage == "outcome_unknown";
        let pair = rows.entry(row.request_id.clone()).or_default();
        if pair.len() == 2 {
            extra_rows = true;
            return Ok(());
        }
        let bytes = serde_json::to_vec(&row.payload)
            .map_err(|_| motion_source_mismatch())?
            .len();
        retained_bytes = retained_bytes
            .checked_add(bytes)
            .ok_or_else(motion_source_mismatch)?;
        if bytes > MOTION_JOURNAL_MAX_ROW_BYTES
            || retained_bytes > MOTION_JOURNAL_MAX_RETAINED_BYTES
        {
            return Err(unsupported(
                "Canonical Motion Canvas source receipt payloads exceed the bounded retention budget",
            ));
        }
        pair.push(row);
        Ok(())
    })?;
    if unknown {
        return Err(unresolved_motion_source());
    }
    if extra_rows {
        return Err(motion_source_mismatch());
    }
    let mut bindings = Vec::with_capacity(films.len());
    let mut descriptors = BTreeMap::new();
    for (index, (film, observed)) in films.iter().zip(&rendered.segments).enumerate() {
        let prefix = format!("{attempt}:motion:{index}");
        let intent = serde_json::to_value(&film.film).map_err(|_| motion_source_mismatch())?;
        let film_digest = semwright_semantic_composition::canonical_digest(&film.film)
            .map_err(|_| motion_source_mismatch())?;
        let mut authority = MotionStageAuthority::default();
        let (plan, plan_id) = original_motion_stage(
            project,
            &rows,
            ExpectedMotionStage {
                request_id: format!("{prefix}:plan"),
                command: "driver.motion-canvas.composition.plan",
                args: semwright_native_sdk::json!({
                    "film": &intent,
                    "budget": {"max_iterations": 8, "max_operations": super::MOTION_PLAN_MAX_OPERATIONS,
                        "max_findings": 1024, "max_observations": 128, "max_elapsed_ms": 120000},
                }),
                mutation: false,
                job_ref: None,
            },
            &mut authority,
            &mut descriptors,
        )?;
        if plan.get("plan_ref").and_then(Value::as_str) != Some(observed.plan_ref.as_str())
            || plan.pointer("/plan/digest").and_then(Value::as_str)
                != Some(observed.plan_ref.as_str())
            || plan.pointer("/plan/body/intent") != Some(&intent)
            || plan
                .pointer("/plan/body/intent_digest")
                .and_then(Value::as_str)
                != Some(film_digest.as_str())
            || plan.get("resulting_fingerprint").and_then(Value::as_str)
                != Some(observed.fingerprint.as_str())
            || plan.get("repair").and_then(Value::as_bool) != Some(false)
        {
            return Err(motion_source_mismatch());
        }
        let (applied, apply_id) = original_motion_stage(
            project,
            &rows,
            ExpectedMotionStage {
                request_id: format!("{prefix}:apply"),
                command: "driver.motion-canvas.composition.apply",
                args: semwright_native_sdk::json!({"plan_ref": &observed.plan_ref, "dry_run": false}),
                mutation: true,
                job_ref: None,
            },
            &mut authority,
            &mut descriptors,
        )?;
        if applied.get("plan_ref").and_then(Value::as_str) != Some(observed.plan_ref.as_str())
            || applied.get("intent_digest").and_then(Value::as_str) != Some(film_digest.as_str())
            || applied.get("fingerprint").and_then(Value::as_str)
                != Some(observed.fingerprint.as_str())
            || applied.get("applied").and_then(Value::as_bool) != Some(true)
            || applied.get("execution_status").and_then(Value::as_str) != Some("completed")
        {
            return Err(motion_source_mismatch());
        }
        let (started, start_id) = original_motion_stage(
            project,
            &rows,
            ExpectedMotionStage {
                request_id: format!("{prefix}:render-start"),
                command: "driver.motion-canvas.render.start",
                args: semwright_native_sdk::json!({
                    "expected_fingerprint": &observed.fingerprint,
                    "profile": {"first_frame": 0, "end_frame_exclusive": film.frame_count,
                        "scale": "full", "transparent": false, "timeout_ms": super::MOTION_RENDER_TIMEOUT_MS},
                }),
                mutation: true,
                job_ref: None,
            },
            &mut authority,
            &mut descriptors,
        )?;
        if started.get("job_ref").and_then(Value::as_str) != Some(observed.job_ref.as_str())
            || !matches!(
                started.get("state").and_then(Value::as_str),
                Some("queued" | "starting" | "rendering" | "succeeded")
            )
        {
            return Err(motion_source_mismatch());
        }
        let (result, result_id) = original_motion_stage(
            project,
            &rows,
            ExpectedMotionStage {
                request_id: format!("{prefix}:render-result"),
                command: "driver.motion-canvas.render.result",
                args: semwright_native_sdk::json!({"job_ref": &observed.job_ref}),
                mutation: false,
                job_ref: Some(&observed.job_ref),
            },
            &mut authority,
            &mut descriptors,
        )?;
        if result.get("job_ref").and_then(Value::as_str) != Some(observed.job_ref.as_str())
            || result.get("state").and_then(Value::as_str) != Some("succeeded")
            || result.get("artifact") != Some(&observed.artifact)
            || observed.artifact.get("frame_count").and_then(Value::as_u64)
                != Some(film.frame_count)
        {
            return Err(motion_source_mismatch());
        }
        let (verified, verify_id) = original_motion_stage(
            project,
            &rows,
            ExpectedMotionStage {
                request_id: format!("{prefix}:verify"),
                command: "driver.motion-canvas.composition.verify",
                args: semwright_native_sdk::json!({"plan_ref": &observed.plan_ref, "job_ref": &observed.job_ref}),
                mutation: false,
                job_ref: Some(&observed.job_ref),
            },
            &mut authority,
            &mut descriptors,
        )?;
        if verified != &observed.verification {
            return Err(motion_source_mismatch());
        }
        super::ensure_native_motion_verification(verified)?;
        bindings.push(CanonicalMotionCanvasFilmSource {
            segment_id: film.id.clone(),
            film_sha256: film_digest.as_str().into(),
            plan_ref: observed.plan_ref.clone(),
            native_fingerprint: observed.fingerprint.clone(),
            native_job_ref: observed.job_ref.clone(),
            connection_identity: authority
                .connection_identity
                .ok_or_else(motion_source_mismatch)?,
            provider_generation: authority
                .provider_generation
                .ok_or_else(motion_source_mismatch)?,
            completed_receipt_ids: [plan_id, apply_id, start_id, result_id, verify_id],
        });
    }
    recheck_motion_watermark(service, project.id, &watermark)?;
    Ok(CanonicalMotionSourceSnapshot {
        bindings,
        watermark,
    })
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
    pub canonical_motion_source: Vec<CanonicalMotionCanvasFilmSource>,
    pub evidence_scope: String,
}

impl ProductionCoordinator {
    /// Read-only source custody from the current saved Film and the original
    /// application journal. Caller JSON cannot supply or replace those rows.
    /// This grants no render/mux effect and does not verify pixels or audio.
    pub fn verify_native_canonical_original_mix_motion_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<Vec<CanonicalMotionCanvasFilmSource>> {
        let project = current_project(&self.service, project_id, expected)?;
        if self.client.connection().resource != project.resource_key() {
            return Err(stale("Canonical original mix Broker owner root is foreign"));
        }
        if project
            .production_design
            .delivery_recipes
            .get(&deliverable_id)
            .and_then(|recipe| recipe.original_mix.as_ref())
            .is_none()
        {
            return Err(unsupported(
                "Save an original mix recipe for this delivery profile",
            ));
        }
        let dependency = canonical_dependency(&project, deliverable_id)?;
        let options = canonical_original_mix_film_options(&project, deliverable_id)?;
        let source = canonical_motion_source_snapshot(
            &self.service,
            &project,
            deliverable_id,
            &options,
            rendered,
        )?;
        recheck_canonical_dependency(
            &self.service,
            project_id,
            expected,
            deliverable_id,
            &dependency,
        )?;
        recheck_motion_watermark(&self.service, project_id, &source.watermark)?;
        Ok(source.bindings)
    }

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
        let canonical_motion_source = canonical_motion_source_snapshot(
            &self.service,
            &project,
            deliverable_id,
            &options,
            rendered,
        )?;
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
        recheck_motion_watermark(
            &self.service,
            project_id,
            &canonical_motion_source.watermark,
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
        let after_motion = canonical_motion_source_snapshot(
            &self.service,
            &project,
            deliverable_id,
            &options,
            rendered,
        )?;
        if after_motion.bindings != canonical_motion_source.bindings {
            return Err(motion_source_mismatch());
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
            canonical_motion_source: canonical_motion_source.bindings,
            evidence_scope: if captioned {
                "actual-native-canonical-original-mix-h264-aac-caption-source-not-decoded-pixel-or-human-approved"
            } else {
                "actual-native-canonical-original-mix-h264-aac-source-bound-not-mastered-or-human-approved"
            }.into(),
        })
    }
}

#[cfg(test)]
mod tests_canonical_film_receipt_snapshot {
    use super::*;
    use motionwright_storage::ProductionReceiptInput;
    use semwright_native_sdk::json;

    #[test]
    fn canonical_film_source_snapshot_rejects_append_between_keyset_pages() {
        let root = tempfile::Builder::new()
            .prefix("canonical-film-snapshot-")
            .tempdir_in(env!("CARGO_MANIFEST_DIR"))
            .unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let project = service
            .create_project("Original Film receipt snapshot")
            .unwrap();
        let row = |index| ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: format!("snapshot-observation-{index}"),
            request_sha256: sha256(b"synthetic snapshot observation"),
            command: "driver.motion-canvas.render.status".into(),
            stage: "completed".into(),
            payload: json!({"fixture": "synthetic observation, no provider execution"}),
        };
        for index in 0..65 {
            service.append_production_receipt(row(index)).unwrap();
        }
        let watermark = service.production_receipt_watermark(project.id).unwrap();
        let mut visited = 0;
        let error = visit_motion_receipts(&service, project.id, &watermark, |_| {
            visited += 1;
            if visited == 1 {
                // Deterministic drift after the first page is read. The next
                // keyset page must not silently extend or truncate the source.
                service.append_production_receipt(row(65)).unwrap();
            }
            Ok(())
        })
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert_eq!(error.message, motion_receipt_snapshot_changed().message);
        assert!(error.outcome_known);
        assert_eq!(visited, MOTION_JOURNAL_PAGE_SIZE);
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            66
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }
}
