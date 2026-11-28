//! Preview-only native prefixes and explicit, revision-bound original reuse.
//! This module consumes the existing provider and Store proxy cache. It neither
//! authorizes a master nor asserts seek/checkpoint or unobserved simulation work.
use super::*;
use crate::film::{MotionCanvasWindowPlan, build_motion_canvas_window};
use motionwright_domain::{Project, SourceFrameWindow};
use motionwright_service::{DerivedCacheRecord, RenderWindowCacheKey};
use semwright_motion_authoring::{Film, MotionMeasurement, SubjectContent};
use semwright_semantic_composition::{
    ExecutionStatus, SupportLevel, Verdict, VerificationReport, canonical_digest,
};
use std::collections::BTreeSet;

const MAX_SELECTED_FRAMES: u64 = 4_096;
const MAX_CAPTURE_FRAMES: u64 = 72_000;
const MAX_WINDOW_MANIFEST_BYTES: u64 = 16 * 1024 * 1024;
const MAX_WINDOW_FRAME_BYTES: u64 = 24 * 1024 * 1024;
const MAX_SELECTED_BYTES: u64 = 64 * 1024 * 1024;

/// Native raster scale, independent of the saved Film and master profile.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionCanvasWindowScale {
    #[default]
    Full,
    Half,
    Quarter,
}

impl MotionCanvasWindowScale {
    pub fn is_full(&self) -> bool {
        *self == Self::Full
    }

    pub(super) fn provider_name(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::Half => "half",
            Self::Quarter => "quarter",
        }
    }

    fn raster(self, width: u32, height: u32) -> NativeResult<Option<MotionCanvasWindowRaster>> {
        if self.is_full() {
            return Ok(None);
        }
        let divisor = match self {
            Self::Half => 2,
            Self::Quarter => 4,
            Self::Full => 1,
        };
        if !width.is_multiple_of(divisor) || !height.is_multiple_of(divisor) {
            return Err(invalid(
                "Reduced native preview requires exact integral scaled dimensions",
            ));
        }
        let (width, height) = (width / divisor, height / divisor);
        if width == 0
            || height == 0
            || width > 4096
            || height > 4096
            || u64::from(width) * u64::from(height) > 8_847_360
        {
            return Err(invalid(
                "Reduced native preview exceeds the pinned provider raster bounds",
            ));
        }
        Ok(Some(MotionCanvasWindowRaster {
            scale: self,
            width,
            height,
        }))
    }
}

#[derive(Clone)]
pub(super) struct MotionCanvasCaptureSpec {
    pub(super) end: u64,
    pub(super) scale: MotionCanvasWindowScale,
    pub(super) blend_admission: Option<MotionBlendAdmission>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct MotionCanvasWindowRaster {
    pub scale: MotionCanvasWindowScale,
    pub width: u32,
    pub height: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionCanvasWindowRequest {
    pub deliverable_id: Uuid,
    pub source_scene_id: Uuid,
    pub source_window: SourceFrameWindow,
    pub options: FilmBuildOptions,
    #[serde(default, skip_serializing_if = "MotionCanvasWindowScale::is_full")]
    pub render_scale: MotionCanvasWindowScale,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionCanvasWindowUse {
    NativePrefix,
    ExplicitOriginalReuse,
    UnchangedFilmReuse,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionCanvasStartOrigin {
    FreshAuthenticatedCall,
    StoredReceiptReplay,
}

#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasCaptureTimings {
    pub fonts_micros: u64,
    pub plan_micros: u64,
    pub apply_micros: u64,
    pub render_and_wait_micros: u64,
    pub verification_micros: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasWindowCost {
    pub operation: MotionCanvasWindowUse,
    /// The prefix observed in the retained original; unchanged on a warm read.
    pub original_prefix_pngs: u64,
    pub selected_pngs: u64,
    pub reused_original_pngs: u64,
    /// A fresh authenticated start call, not proof of new internal simulation.
    pub start_calls_observed: u64,
    /// Bytes returned by bounded Store blob reads; not total disk I/O.
    pub cached_png_bytes_returned: u64,
    /// Original PNG payload admitted by Store; not a physical write counter.
    pub cache_payload_bytes_admitted: u64,
    pub cache_micros: u64,
    pub internal_simulation_steps: Option<u64>,
    pub internal_simulation_observation: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasWindowValidation {
    pub verdict: Verdict,
    pub native_frame_coverage: Verdict,
    pub pass_checks: usize,
    pub unknown_checks: usize,
    pub unknown_findings: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasWindowCapture {
    pub segment_frame_count: u64,
    pub prefix: SourceFrameWindow,
    pub plan_ref: String,
    pub fingerprint: String,
    pub job_ref: String,
    pub start_origin: MotionCanvasStartOrigin,
    pub artifact: Value,
    /// Exact provider return, including partial UNKNOWN checks and findings.
    pub verification: Value,
    pub timings: MotionCanvasCaptureTimings,
}

#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasCachedWindowFrame {
    pub source_frame: u64,
    pub segment_frame: u64,
    pub original_relative_path: String,
    pub sha256: String,
    pub bytes: u64,
    pub key: RenderWindowCacheKey,
    pub record: DerivedCacheRecord,
}

#[derive(Debug, Clone)]
pub struct MotionCanvasWindowCarrySource {
    branch: Uuid,
    original_revision: u64,
    connection_identity: String,
}

/// Backend-produced state. It is never deserialized from an IPC request and
/// its private provenance is not the public preview response.
#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasWindowEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub request: MotionCanvasWindowRequest,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raster: Option<MotionCanvasWindowRaster>,
    pub segment_id: String,
    pub segment_index: usize,
    pub segment_window: SourceFrameWindow,
    pub film_digest: String,
    pub original_capture: MotionCanvasWindowCapture,
    #[serde(skip)]
    pub carry_source: Option<MotionCanvasWindowCarrySource>,
    pub validation: MotionCanvasWindowValidation,
    pub cached_frames: Vec<MotionCanvasCachedWindowFrame>,
    pub cost: MotionCanvasWindowCost,
}

/// Closed projection: no paths, provider identifiers, raw findings or job refs.
#[derive(Debug, Clone, Serialize)]
pub struct MotionCanvasWindowSummary {
    pub generation: Uuid,
    pub revision: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub original_revision: Option<u64>,
    pub deliverable_id: Uuid,
    pub source_scene_id: Uuid,
    pub source_window: SourceFrameWindow,
    pub segment_window: SourceFrameWindow,
    pub original_prefix: SourceFrameWindow,
    pub frame_rate: Rate,
    pub preview_only: bool,
    pub master_eligible: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raster: Option<MotionCanvasWindowRaster>,
    pub validation: MotionCanvasWindowValidation,
    pub cost: MotionCanvasWindowCost,
}

impl MotionCanvasWindowEvidence {
    pub fn summary(&self) -> MotionCanvasWindowSummary {
        MotionCanvasWindowSummary {
            generation: self.generation,
            revision: self.revision,
            original_revision: self
                .carry_source
                .as_ref()
                .map(|source| source.original_revision)
                .filter(|revision| *revision < self.revision),
            deliverable_id: self.request.deliverable_id,
            source_scene_id: self.request.source_scene_id,
            source_window: self.request.source_window,
            segment_window: self.segment_window,
            original_prefix: self.original_capture.prefix,
            frame_rate: self.request.options.frame_rate,
            preview_only: true,
            master_eligible: false,
            raster: self.raster,
            validation: self.validation.clone(),
            cost: self.cost.clone(),
        }
    }
}

pub(super) struct MotionCanvasSegmentCapture {
    pub(super) plan_ref: String,
    pub(super) fingerprint: String,
    pub(super) job_ref: String,
    pub(super) artifact: Value,
    pub(super) verification: Value,
    pub(super) start_origin: MotionCanvasStartOrigin,
    pub(super) timings: MotionCanvasCaptureTimings,
}

pub(super) fn elapsed_micros(start: std::time::Instant) -> u64 {
    u64::try_from(start.elapsed().as_micros()).unwrap_or(u64::MAX)
}

fn current_project(
    service: &StudioService,
    id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = service.project(id).map_err(storage_error)?;
    require_scope(&project, expected)?;
    Ok(project)
}
fn require_scope(project: &Project, expected: &RevisionStamp) -> NativeResult<()> {
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Native window belongs to another saved project revision",
        ));
    }
    Ok(())
}
fn request_plan(
    project: &Project,
    request: &MotionCanvasWindowRequest,
) -> NativeResult<MotionCanvasWindowPlan> {
    let count = request
        .source_window
        .end
        .checked_sub(request.source_window.start)
        .ok_or_else(|| invalid("Native window has reversed source bounds"))?;
    if count == 0 || count > MAX_SELECTED_FRAMES {
        return Err(invalid(
            "Native window selected frame count exceeds the proxy bound",
        ));
    }
    let plan = build_motion_canvas_window(
        project,
        request.deliverable_id,
        request.source_scene_id,
        request.source_window,
        &request.options,
    )?;
    if plan.segment_window.end == 0 || plan.segment_window.end > MAX_CAPTURE_FRAMES {
        return Err(invalid(
            "Native window prefix exceeds the existing PNG sequence bound",
        ));
    }
    // Check dimensions before any provider plan, apply or render.
    request.render_scale.raster(
        plan.segment.film.output.width,
        plan.segment.film.output.height,
    )?;
    Ok(plan)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct VerificationWire {
    measurement: MotionMeasurement,
    report: VerificationReport,
}

fn validate_prefix_measurement(
    value: &Value,
    film: &Film,
    plan_ref: &str,
    fingerprint: &str,
    artifact: &Value,
    end: u64,
    full_count: u64,
) -> NativeResult<MotionCanvasWindowValidation> {
    let wire: VerificationWire = serde_json::from_value(value.clone())
        .map_err(|_| backend("Native window measurement has an incompatible schema"))?;
    let measurement = &wire.measurement;
    let expected_intent = canonical_digest(film)
        .map_err(|_| invalid("Canonical window Film digest could not be calculated"))?;
    let coverage = &measurement.coverage;
    let manifest_digest = artifact
        .get("manifest_sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| backend("Native window artifact digest is absent"))?;
    if measurement.version != semwright_motion_authoring::AUTHORING_VERSION
        || wire.report.execution_status != ExecutionStatus::Completed
        || wire.report.support_level != SupportLevel::Native
        || value.pointer("/report/validation") != value.pointer("/measurement/validation")
        || measurement.intent_digest != expected_intent
        || measurement.validation.plan_digest.as_str() != plan_ref
        || value
            .pointer("/measurement/validation/base")
            .and_then(Value::as_array)
            .map(Vec::len)
            != Some(1)
        || value
            .pointer("/measurement/validation/base/0/key/provider")
            .and_then(Value::as_str)
            != Some("driver:motion-canvas")
        || value
            .pointer("/measurement/validation/base/0/key/resource")
            .and_then(Value::as_str)
            != Some("project")
        || value
            .pointer("/measurement/validation/base/0/revision/kind")
            .and_then(Value::as_str)
            != Some("fingerprint")
        || value
            .pointer("/measurement/validation/base/0/revision/value")
            .and_then(Value::as_str)
            != Some(fingerprint)
        || coverage.first_frame != 0
        || coverage.end_frame_exclusive != end
        || coverage.observed_frames != end
        || !coverage.exhaustive
        || coverage.fps != film.output.frame_rate
        || coverage.method != "motion-canvas-3.17.2-native-frame-probe-v1"
        || coverage.artifact_digest.as_str() != manifest_digest
        || coverage.font_resources_digest.is_none()
    {
        return Err(backend(
            "Native window measurement is not bound to its original prefix and Film",
        ));
    }
    let checks = &measurement.validation.checks;
    let report_verdict = wire
        .report
        .verdict()
        .map_err(|_| backend("Native window verification report is invalid"))?;
    let mut names = BTreeSet::new();
    if report_verdict == Verdict::Fail
        || checks.is_empty()
        || checks
            .iter()
            .any(|check| !names.insert(check.rule.as_str()) || check.verdict == Verdict::Fail)
        || measurement
            .findings
            .iter()
            .any(|finding| finding.verdict == Verdict::Fail)
        || names
            != measurement
                .validation
                .required_rules
                .iter()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
    {
        return Err(backend(
            "Native window measurement contains failed, missing or duplicate rules",
        ));
    }
    let coverage_check = checks
        .iter()
        .find(|check| check.rule == "native-frame-coverage")
        .ok_or_else(|| backend("Native window frame coverage rule is absent"))?;
    let expected_coverage = if end == full_count {
        Verdict::Pass
    } else {
        Verdict::Unknown
    };
    if end > full_count || coverage_check.verdict != expected_coverage {
        return Err(backend(
            "Native window coverage was promoted beyond the observed prefix",
        ));
    }
    let unknown_checks = checks
        .iter()
        .filter(|check| check.verdict == Verdict::Unknown)
        .count();
    let unknown_findings = measurement
        .findings
        .iter()
        .filter(|finding| finding.verdict == Verdict::Unknown)
        .count();
    Ok(MotionCanvasWindowValidation {
        verdict: if unknown_findings > 0 {
            Verdict::Unknown
        } else {
            report_verdict
        },
        native_frame_coverage: coverage_check.verdict,
        pass_checks: checks.len() - unknown_checks,
        unknown_checks,
        unknown_findings,
    })
}

#[derive(Deserialize)]
struct FrameManifest {
    index: u64,
    file: String,
    sha256: String,
    bytes: u64,
}
#[derive(Deserialize)]
struct ManifestPlan {
    first_frame: u64,
    end_frame_exclusive: u64,
    frame_count: u64,
    #[serde(default)]
    width: Option<u32>,
    #[serde(default)]
    height: Option<u32>,
}
#[derive(Deserialize)]
struct RenderManifest {
    plan: ManifestPlan,
    frames: Vec<FrameManifest>,
}

fn selected_originals(
    root: &Path,
    artifact: &Value,
    selected: SourceFrameWindow,
    raster: Option<MotionCanvasWindowRaster>,
) -> NativeResult<Vec<FrameManifest>> {
    let directory = artifact
        .get("directory")
        .and_then(Value::as_str)
        .filter(|name| {
            name.len() == 39
                && name.starts_with("render-")
                && name.as_bytes()[7..].iter().all(u8::is_ascii_hexdigit)
                && !name.bytes().any(|b| b.is_ascii_uppercase())
        })
        .ok_or_else(|| backend("Native window artifact directory is not canonical"))?;
    let manifest = format!("{directory}/artifact-manifest.json");
    if artifact.get("manifest").and_then(Value::as_str) != Some(manifest.as_str())
        || artifact.get("frame_count").and_then(Value::as_u64) != Some(selected.end)
        || selected.start >= selected.end
        || artifact.get("first_png").and_then(Value::as_str)
            != Some(format!("{directory}/frames/000000.png").as_str())
        || artifact.get("last_png").and_then(Value::as_str)
            != Some(format!("{directory}/frames/{:06}.png", selected.end - 1).as_str())
    {
        return Err(backend(
            "Native window artifact disagrees with its requested prefix",
        ));
    }
    let digest = artifact
        .get("manifest_sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| backend("Native window manifest digest is absent"))?;
    let bytes =
        read_verified_production_artifact(root, &manifest, digest, MAX_WINDOW_MANIFEST_BYTES)?;
    if artifact.get("manifest_bytes").and_then(Value::as_u64) != Some(bytes.len() as u64) {
        return Err(backend(
            "Native window manifest byte count differs from the terminal",
        ));
    }
    let parsed: RenderManifest = serde_json::from_slice(&bytes)
        .map_err(|_| backend("Native window manifest cannot be decoded"))?;
    if parsed.plan.first_frame != 0
        || parsed.plan.end_frame_exclusive != selected.end
        || parsed.plan.frame_count != selected.end
        || parsed.frames.len() as u64 != selected.end
    {
        return Err(backend(
            "Native window manifest is not an exhaustive zero-origin prefix",
        ));
    }
    if let Some(raster) = raster
        && (parsed.plan.width != Some(raster.width) || parsed.plan.height != Some(raster.height))
    {
        return Err(backend(
            "Native window manifest does not identify the requested reduced raster",
        ));
    }
    let mut output = Vec::new();
    let mut total = 0_u64;
    for (index, mut frame) in parsed.frames.into_iter().enumerate() {
        if frame.index != index as u64
            || frame.file != format!("frames/{index:06}.png")
            || !is_sha256(&frame.sha256)
            || frame.bytes == 0
            || frame.bytes > MAX_WINDOW_FRAME_BYTES
        {
            return Err(backend(
                "Native window manifest has invalid frame identity or bounds",
            ));
        }
        if frame.index >= selected.start {
            total = total
                .checked_add(frame.bytes)
                .ok_or_else(|| invalid("Native window selected PNG byte count overflow"))?;
            if total > MAX_SELECTED_BYTES {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "Native window selected PNGs exceed the 64 MiB cache admission bound",
                ));
            }
            frame.file = format!("{directory}/{}", frame.file);
            output.push(frame);
        }
    }
    Ok(output)
}

/// The manifest SHA and existing Store capture bind the complete bytes.
/// Check actual reduced raster dimensions, not merely the requested scale.
fn require_window_raster(bytes: &[u8], raster: MotionCanvasWindowRaster) -> NativeResult<()> {
    if bytes.len() < 33
        || !bytes.starts_with(b"\x89PNG\r\n\x1a\n")
        || bytes[8..12] != 13_u32.to_be_bytes()
        || &bytes[12..16] != b"IHDR"
        || bytes[16..20] != raster.width.to_be_bytes()
        || bytes[20..24] != raster.height.to_be_bytes()
    {
        return Err(backend(
            "Native window PNG dimensions differ from the admitted reduced raster",
        ));
    }
    Ok(())
}

fn frame_key(
    evidence: &MotionCanvasWindowEvidence,
    offset: u64,
    sha256: &str,
) -> NativeResult<RenderWindowCacheKey> {
    let source = evidence
        .request
        .source_window
        .start
        .checked_add(offset)
        .ok_or_else(|| invalid("Native window source cache index overflow"))?;
    let end = source
        .checked_add(1)
        .ok_or_else(|| invalid("Native window cache end overflow"))?;
    let digest = canonical_digest(&json!({
        "schema":"motionwright.native-window-original-frame/1",
        "project_resource":evidence.project_resource,
        "generation":evidence.generation,"revision":evidence.revision,
        "request":evidence.request,"film_digest":evidence.film_digest,
        "segment_id":evidence.segment_id,"segment_index":evidence.segment_index,
        "segment_window":evidence.segment_window,
        "original_plan_ref":evidence.original_capture.plan_ref,
        "original_fingerprint":evidence.original_capture.fingerprint,
        "original_job_ref":evidence.original_capture.job_ref,
        "original_prefix":evidence.original_capture.prefix,
        "coverage":evidence.original_capture.verification.pointer("/measurement/coverage"),
        "source_frame":source,"png_sha256":sha256
    }))
    .map_err(|_| invalid("Native window cache fingerprint cannot be encoded"))?;
    let project_id = evidence
        .project_resource
        .strip_prefix("project:")
        .and_then(|text| Uuid::parse_str(text).ok())
        .ok_or_else(|| invalid("Native window project resource is malformed"))?;
    Ok(RenderWindowCacheKey {
        project_id,
        expected: RevisionStamp {
            resource: evidence.project_resource.clone(),
            generation: evidence.generation,
            revision: evidence.revision,
        },
        deliverable_id: evidence.request.deliverable_id,
        source_window: SourceFrameWindow { start: source, end },
        fingerprint_sha256: digest.as_str().to_owned(),
    })
}

fn require_uncaptured_identity(
    service: &StudioService,
    project_id: Uuid,
    prefix: &str,
) -> NativeResult<()> {
    for suffix in ["apply", "render-start"] {
        if let Some(prior) = service
            .latest_production_receipt(project_id, &format!("{prefix}:{suffix}"))
            .map_err(storage_error)?
        {
            let error = Error::new(
                ErrorCode::Conflict,
                "Native window capture identity already exists; inspect the original or explicitly reuse its window token",
            );
            return Err(
                if prior.stage == "outcome_unknown"
                    || (prior.stage == "dispatching"
                        && prior.payload.get("mutation").and_then(Value::as_bool) == Some(true))
                {
                    error.uncertain()
                } else {
                    error
                },
            );
        }
    }
    Ok(())
}

impl ProductionCoordinator {
    /// Acceptance is synchronous, including a Future queued before first poll.
    pub fn render_motion_canvas_window<'a>(
        &'a self,
        project_id: Uuid,
        expected: &'a RevisionStamp,
        request_id: &'a str,
        request: &'a MotionCanvasWindowRequest,
    ) -> impl std::future::Future<Output = NativeResult<MotionCanvasWindowEvidence>> + Send + 'a
    {
        let accepted = self.accept_retained_font_render(
            project_id,
            expected,
            request_id,
            ProductionFontInvocationKind::Window,
        );
        async move {
            let mut work = accepted?;
            if let Some(work) = work.as_mut() {
                work.start_native()?;
            }
            let result = self
                .render_motion_canvas_window_inner(project_id, expected, request_id, request)
                .await;
            if let Some(work) = work {
                work.finish_native(&result);
            }
            result
        }
    }

    async fn render_motion_canvas_window_inner(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        request: &MotionCanvasWindowRequest,
    ) -> NativeResult<MotionCanvasWindowEvidence> {
        if request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("Motion Canvas window request id is invalid"));
        }
        let project = current_project(&self.service, project_id, expected)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            request_id,
            SceneRequestFamily::Motion,
        )?;
        let plan = request_plan(&project, request)?;
        let raster = request.render_scale.raster(
            plan.segment.film.output.width,
            plan.segment.film.output.height,
        )?;
        let prefix = format!("{request_id}:motion:{}", plan.segment_index);
        // The existing durable receipts are inspected before any native call.
        // Cold has no implicit replay/fallback; warm is an explicit token read.
        require_uncaptured_identity(&self.service, project_id, &prefix)?;
        let blend_admission =
            if plan.segment.film.version == semwright_motion_authoring::AUTHORING_BLEND_VERSION {
                if self.active_hybrid_budget.is_some() {
                    return Err(Error::new(
                        ErrorCode::Unsupported,
                        "Experimental hybrid root has no Film v2 blend admission",
                    ));
                }
                Some(self.negotiate_motion_blend(project_id, expected).await?)
            } else {
                None
            };
        let scoped = self.with_motion_scene_boundary_scope(
            &project,
            request.deliverable_id,
            &request.options,
            &plan.segment.scene_ids,
        )?;
        let capture = scoped
            .capture_motion_canvas_segment(
                project_id,
                expected,
                &prefix,
                plan.segment_index,
                &plan.segment,
                MotionCanvasCaptureSpec {
                    end: plan.segment_window.end,
                    scale: request.render_scale,
                    blend_admission,
                },
            )
            .await?;
        let validation = validate_prefix_measurement(
            &capture.verification,
            &plan.segment.film,
            &capture.plan_ref,
            &capture.fingerprint,
            &capture.artifact,
            plan.segment_window.end,
            plan.segment.frame_count,
        )?;
        let film_digest = canonical_digest(&plan.segment.film)
            .map_err(|_| invalid("Canonical window Film digest cannot be encoded"))?
            .as_str()
            .to_owned();
        let selected = selected_originals(
            &self.client.connection().output_root,
            &capture.artifact,
            plan.segment_window,
            raster,
        )?;
        let frames = request.source_window.end - request.source_window.start;
        let mut evidence = MotionCanvasWindowEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            request: request.clone(),
            raster,
            segment_id: plan.segment.id,
            segment_index: plan.segment_index,
            segment_window: plan.segment_window,
            film_digest,
            original_capture: MotionCanvasWindowCapture {
                segment_frame_count: plan.segment.frame_count,
                prefix: SourceFrameWindow {
                    start: 0,
                    end: plan.segment_window.end,
                },
                plan_ref: capture.plan_ref,
                fingerprint: capture.fingerprint,
                job_ref: capture.job_ref,
                start_origin: capture.start_origin,
                artifact: capture.artifact,
                verification: capture.verification,
                timings: capture.timings,
            },
            carry_source: Some(MotionCanvasWindowCarrySource {
                branch: project.active_branch,
                original_revision: project.revision,
                connection_identity: self.client.connection().identity()?,
            }),
            validation,
            cached_frames: Vec::with_capacity(selected.len()),
            cost: MotionCanvasWindowCost {
                operation: MotionCanvasWindowUse::NativePrefix,
                original_prefix_pngs: plan.segment_window.end,
                selected_pngs: frames,
                reused_original_pngs: 0,
                start_calls_observed: u64::from(
                    capture.start_origin == MotionCanvasStartOrigin::FreshAuthenticatedCall,
                ),
                cached_png_bytes_returned: 0,
                cache_payload_bytes_admitted: 0,
                cache_micros: 0,
                internal_simulation_steps: None,
                internal_simulation_observation: "not_observed",
            },
        };
        let cache_service = self.service.clone();
        let output_root = self.client.connection().output_root.clone();
        let cache_expected = expected.clone();
        // Register before spawn; the actual blocking worker owns this guard if
        // its async waiter is cancelled or dropped while caching continues.
        let cache_work = self.accept_retained_font_window_cache()?;
        tokio::task::spawn_blocking(move || -> NativeResult<MotionCanvasWindowEvidence> {
            let mut cache_work = cache_work;
            if let Some(work) = cache_work.as_mut() {
                work.start_native()?;
            }
            let result = (|| -> NativeResult<MotionCanvasWindowEvidence> {
                let cache_started = std::time::Instant::now();
                for (offset, frame) in selected.into_iter().enumerate() {
                    let key = frame_key(&evidence, offset as u64, &frame.sha256)?;
                    let path =
                        output_artifact_path(&output_root, &frame.file, MAX_WINDOW_FRAME_BYTES)?;
                    // Check reduced PNG dimensions before publishing a proxy.
                    // The verified Store capture checks the same complete digest.
                    if let Some(raster) = evidence.raster {
                        let bytes = read_verified_production_artifact(
                            &output_root,
                            &frame.file,
                            &frame.sha256,
                            MAX_WINDOW_FRAME_BYTES,
                        )?;
                        if bytes.len() as u64 != frame.bytes {
                            return Err(backend("Native window PNG byte count changed"));
                        }
                        require_window_raster(&bytes, raster)?;
                    }
                    // Compare the manifest descriptor during Store staging, before
                    // publishing the proxy payload or cache metadata.
                    let record = cache_service
                        .put_render_window_proxy_verified(
                            &key,
                            &path,
                            &motionwright_service::BlobDescriptor {
                                sha256: frame.sha256.clone(),
                                size_bytes: frame.bytes,
                            },
                            MAX_WINDOW_FRAME_BYTES,
                            1,
                        )
                        .map_err(storage_error)?;
                    if record.blob_sha256 != frame.sha256 || record.size_bytes != frame.bytes {
                        return Err(backend(
                            "Native PNG changed while its original cache payload was retained",
                        ));
                    }
                    evidence.cost.cache_payload_bytes_admitted = evidence
                        .cost
                        .cache_payload_bytes_admitted
                        .checked_add(record.size_bytes)
                        .ok_or_else(|| invalid("Native window cache bytes overflow"))?;
                    evidence.cached_frames.push(MotionCanvasCachedWindowFrame {
                        source_frame: key.source_window.start,
                        segment_frame: frame.index,
                        original_relative_path: frame.file,
                        sha256: frame.sha256,
                        bytes: frame.bytes,
                        key,
                        record,
                    });
                }
                evidence.cost.cache_micros = elapsed_micros(cache_started);
                current_project(&cache_service, project_id, &cache_expected)?;
                if evidence.cached_frames.len() as u64 != frames {
                    return Err(backend(
                        "Native window did not retain every selected original PNG",
                    ));
                }
                Ok(evidence)
            })();
            if let Some(work) = cache_work {
                work.finish_native(&result);
            }
            result
        })
        .await
        .map_err(|_| {
            backend(
                "Native window cache task did not complete; retain the original render receipts",
            )
        })?
    }
}

fn verify_reusable_window(
    plan: &MotionCanvasWindowPlan,
    request: &MotionCanvasWindowRequest,
    original: &MotionCanvasWindowEvidence,
) -> NativeResult<MotionCanvasWindowValidation> {
    let digest = canonical_digest(&plan.segment.film)
        .map_err(|_| invalid("Current canonical Film digest cannot be encoded"))?;
    let raster = request.render_scale.raster(
        plan.segment.film.output.width,
        plan.segment.film.output.height,
    )?;
    if original.raster != raster
        || original.film_digest != digest.as_str()
        || original.segment_id != plan.segment.id
        || original.segment_index != plan.segment_index
        || original.segment_window != plan.segment_window
        || original.original_capture.prefix
            != (SourceFrameWindow {
                start: 0,
                end: plan.segment_window.end,
            })
        || original.original_capture.segment_frame_count != plan.segment.frame_count
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Original native window does not match the saved canonical Film",
        ));
    }
    // Recheck the original typed measurement without claiming a current runtime.
    validate_prefix_measurement(
        &original.original_capture.verification,
        &plan.segment.film,
        &original.original_capture.plan_ref,
        &original.original_capture.fingerprint,
        &original.original_capture.artifact,
        plan.segment_window.end,
        plan.segment.frame_count,
    )
}

/// Read-only original reuse. A miss, stale revision, tamper or absent token at
/// the registry is an error; this function has no producer/client to fall back to.
pub fn reuse_motion_canvas_window(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request: &MotionCanvasWindowRequest,
    original: &MotionCanvasWindowEvidence,
) -> NativeResult<MotionCanvasWindowEvidence> {
    let project = current_project(service, project_id, expected)?;
    if original.project_resource != project.resource_key()
        || original.generation != project.generation
        || original.revision != project.revision
        || &original.request != request
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Original native window scope or Film options differ from this request",
        ));
    }
    let validation = verify_reusable_window(&request_plan(&project, request)?, request, original)?;
    let count = request.source_window.end - request.source_window.start;
    if original.cached_frames.len() as u64 != count {
        return Err(backend(
            "Original native window cache inventory is incomplete",
        ));
    }
    let started = std::time::Instant::now();
    let mut bytes_read = 0_u64;
    for index in 0..count {
        bytes_read = bytes_read
            .checked_add(
                read_cached_motion_canvas_window_frame(service, original, index)?.len() as u64,
            )
            .ok_or_else(|| invalid("Original native window cache read bytes overflow"))?;
        if bytes_read > MAX_SELECTED_BYTES {
            return Err(invalid(
                "Original native window cache payload exceeds its retained bound",
            ));
        }
    }
    current_project(service, project_id, expected)?;
    let mut reused = original.clone();
    reused.validation = validation;
    reused.cost = MotionCanvasWindowCost {
        operation: MotionCanvasWindowUse::ExplicitOriginalReuse,
        original_prefix_pngs: original.original_capture.prefix.end,
        selected_pngs: count,
        reused_original_pngs: count,
        start_calls_observed: 0,
        cached_png_bytes_returned: bytes_read,
        cache_payload_bytes_admitted: 0,
        cache_micros: elapsed_micros(started),
        internal_simulation_steps: None,
        internal_simulation_observation: "not_observed",
    };
    Ok(reused)
}

/// Admit a fresh preview of byte-identical, self-contained Film after a saved edit.
/// Only retained provider PNGs are read. The old revision's Store entries stay revoked.
pub fn carry_unchanged_motion_canvas_window(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request: &MotionCanvasWindowRequest,
    original: &MotionCanvasWindowEvidence,
    connection: &ProductionConnection,
) -> NativeResult<MotionCanvasWindowEvidence> {
    let project = current_project(service, project_id, expected)?;
    let resource = project.resource_key();
    let source = original
        .carry_source
        .as_ref()
        .ok_or_else(|| invalid("Native window has no retained original connection"))?;
    if original.project_resource != resource
        || original.generation != project.generation
        || source.branch != project.active_branch
        || original.revision >= project.revision
        || source.original_revision > original.revision
        || &original.request != request
        || connection.resource != resource
        || connection.identity()? != source.connection_identity
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Original native window and current saved source differ",
        ));
    }
    let plan = request_plan(&project, request)?;
    let film = &plan.segment.film;
    // Text, media, and cues can depend on content outside the Film digest.
    let self_contained = film.assets.is_empty()
        && film.cues.cues.is_empty()
        && film
            .sequences
            .iter()
            .flat_map(|sequence| &sequence.beats)
            .flat_map(|beat| &beat.shots)
            .all(|shot| {
                shot.annotations.is_empty()
                    && shot.captions.is_empty()
                    && shot.subjects.iter().all(|subject| {
                        matches!(
                            subject.content,
                            SubjectContent::Group
                                | SubjectContent::Rectangle { .. }
                                | SubjectContent::Circle { .. }
                                | SubjectContent::Path { .. }
                                | SubjectContent::Camera { .. }
                        )
                    })
            });
    if !self_contained {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Native window reuse after editing requires self-contained shape geometry",
        ));
    }
    // The Film digest does not bind the current declaration or its predecessor.
    crate::native_scene_boundary::guard_motion_scenes(
        &project,
        request.deliverable_id,
        &request.options,
        &plan.segment.scene_ids,
    )?;
    let validation = verify_reusable_window(&plan, request, original)?;
    let count = request.source_window.end - request.source_window.start;
    if original.cached_frames.len() as u64 != count {
        return Err(backend(
            "Original native window cache inventory is incomplete",
        ));
    }
    let started = std::time::Instant::now();
    let mut bytes = 0_u64;
    let mut frames = Vec::with_capacity(original.cached_frames.len());
    for index in 0..count {
        let frame = original_window_frame(original, index)?;
        bytes = bytes
            .checked_add(frame.bytes)
            .filter(|bytes| *bytes <= MAX_SELECTED_BYTES)
            .ok_or_else(|| invalid("Original native window exceeds its retained byte bound"))?;
        frames.push(frame.clone());
    }
    let mut reused = original.clone();
    reused.revision = project.revision;
    reused.validation = validation;
    for (index, frame) in frames.iter_mut().enumerate() {
        let path = output_artifact_path(
            &connection.output_root,
            &frame.original_relative_path,
            MAX_WINDOW_FRAME_BYTES,
        )?;
        let key = frame_key(&reused, index as u64, &frame.sha256)?;
        let record = service
            .put_render_window_proxy_verified(
                &key,
                path,
                &motionwright_service::BlobDescriptor {
                    sha256: frame.sha256.clone(),
                    size_bytes: frame.bytes,
                },
                MAX_WINDOW_FRAME_BYTES,
                1,
            )
            .map_err(storage_error)?;
        frame.key = key;
        frame.record = record;
    }
    current_project(service, project_id, expected)?;
    reused.cached_frames = frames;
    reused.cost = MotionCanvasWindowCost {
        operation: MotionCanvasWindowUse::UnchangedFilmReuse,
        original_prefix_pngs: original.original_capture.prefix.end,
        selected_pngs: count,
        reused_original_pngs: count,
        start_calls_observed: 0,
        cached_png_bytes_returned: 0,
        cache_payload_bytes_admitted: bytes,
        cache_micros: elapsed_micros(started),
        internal_simulation_steps: None,
        internal_simulation_observation: "not_observed",
    };
    Ok(reused)
}

fn original_window_frame(
    original: &MotionCanvasWindowEvidence,
    index: u64,
) -> NativeResult<&MotionCanvasCachedWindowFrame> {
    let frame = original
        .cached_frames
        .get(usize::try_from(index).map_err(|_| invalid("Native cached frame index overflow"))?)
        .ok_or_else(|| invalid("Native cached frame is outside its selected window"))?;
    let expected_key = frame_key(original, index, &frame.sha256)?;
    if frame.key != expected_key
        || frame.source_frame != frame.key.source_window.start
        || frame.segment_frame
            != original
                .segment_window
                .start
                .checked_add(index)
                .ok_or_else(|| invalid("Native cache local frame index overflow"))?
        || frame.bytes == 0
        || frame.bytes > MAX_WINDOW_FRAME_BYTES
        || frame.record.blob_sha256 != frame.sha256
        || frame.record.size_bytes != frame.bytes
    {
        return Err(backend(
            "Native cached frame no longer matches its original scope",
        ));
    }
    Ok(frame)
}

pub fn read_cached_motion_canvas_window_frame(
    service: &StudioService,
    original: &MotionCanvasWindowEvidence,
    index: u64,
) -> NativeResult<Vec<u8>> {
    let frame = original_window_frame(original, index)?;
    let shared = service.store();
    let store = shared.lock();
    let project = store
        .load_project(frame.key.project_id)
        .map_err(storage_error)?;
    require_scope(&project, &frame.key.expected)?;
    let hit = store
        .lookup_render_window_proxy(&frame.key)
        .map_err(storage_error)?
        .ok_or_else(|| {
            Error::new(
                ErrorCode::NotFound,
                "Original native window cache is absent; no native render was dispatched",
            )
        })?;
    if hit.record.blob_sha256 != frame.sha256
        || hit.record.size_bytes != frame.bytes
        || hit.validated_bytes != frame.bytes
        || hit.record.fingerprint_sha256 != frame.key.fingerprint_sha256
    {
        return Err(backend(
            "Native cache lookup disagrees with the retained original PNG",
        ));
    }
    let bytes = store
        .read_blob(&frame.sha256, MAX_WINDOW_FRAME_BYTES)
        .map_err(storage_error)?;
    if bytes.len() as u64 != frame.bytes || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(backend(
            "Native cache payload is not the original bounded PNG",
        ));
    }
    if let Some(raster) = original.raster {
        require_window_raster(&bytes, raster)?;
    }
    require_scope(
        &store
            .load_project(frame.key.project_id)
            .map_err(storage_error)?,
        &frame.key.expected,
    )?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::Change;
    use semwright_motion_authoring::{Archetype, NarrativeRole};

    #[test]
    fn reduced_window_raster_preserves_film_and_refuses_foreign_png_or_scale() {
        let (_directory, _service, project, original) = fixture();
        let full = request_plan(&project, &original.request).unwrap();
        let legacy = serde_json::to_value(&original.request).unwrap();
        assert!(legacy.get("render_scale").is_none());
        assert!(
            serde_json::from_value::<MotionCanvasWindowRequest>(legacy.clone())
                .unwrap()
                .render_scale
                .is_full()
        );
        let mut reduced = original.request.clone();
        reduced.render_scale = MotionCanvasWindowScale::Half;
        let planned = request_plan(&project, &reduced).unwrap();
        assert_eq!(
            canonical_digest(&full.segment.film).unwrap(),
            canonical_digest(&planned.segment.film).unwrap()
        );
        assert_eq!(full.segment_window, planned.segment_window);
        let raster = reduced
            .render_scale
            .raster(
                planned.segment.film.output.width,
                planned.segment.film.output.height,
            )
            .unwrap()
            .unwrap();
        assert_eq!((raster.width, raster.height), (960, 540));
        assert_eq!(
            serde_json::to_value(&reduced).unwrap()["render_scale"],
            "half"
        );
        let mut wrong = legacy;
        wrong["render_scale"] = json!("double");
        assert!(serde_json::from_value::<MotionCanvasWindowRequest>(wrong).is_err());
        assert!(MotionCanvasWindowScale::Quarter.raster(1918, 1080).is_err());
        assert_eq!(
            MotionCanvasWindowScale::Quarter
                .raster(1920, 1080)
                .unwrap()
                .unwrap()
                .width,
            480
        );
        let mut header = vec![0; 33];
        header[..8].copy_from_slice(b"\x89PNG\r\n\x1a\n");
        header[8..12].copy_from_slice(&13_u32.to_be_bytes());
        header[12..16].copy_from_slice(b"IHDR");
        header[16..20].copy_from_slice(&raster.width.to_be_bytes());
        header[20..24].copy_from_slice(&raster.height.to_be_bytes());
        require_window_raster(&header, raster).unwrap();
        header[16..20].copy_from_slice(&1920_u32.to_be_bytes());
        assert!(require_window_raster(&header, raster).is_err());
        let mut other = original.clone();
        other.request = reduced;
        other.raster = Some(raster);
        assert_ne!(
            frame_key(&original, 0, &"ab".repeat(32)).unwrap(),
            frame_key(&other, 0, &"ab".repeat(32)).unwrap()
        );
    }

    fn fixture() -> (
        tempfile::TempDir,
        StudioService,
        Project,
        MotionCanvasWindowEvidence,
    ) {
        let directory = tempfile::tempdir().unwrap();
        let service = StudioService::open(directory.path().join("window.sqlite3")).unwrap();
        let initial = service
            .create_project("Native window source fixture")
            .unwrap();
        let project = service
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "window-fixture-scene",
                &Change::AddScene {
                    name: "Original".into(),
                    objective: "Preserve source clock".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let scene_id = project.scenes[0].id;
        let node = motionwright_domain::CanvasNode {
            id: Uuid::new_v4(),
            name: "Original shape".into(),
            kind: "shape".into(),
            parent_id: None,
            x: 240.0,
            y: 240.0,
            width: 160.0,
            height: 160.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: motionwright_domain::CoordinateSpace::ProjectPixels,
            z_index: 1,
            style: motionwright_domain::NodeStyle {
                fill: Some("#E0C080".into()),
                stroke: None,
                stroke_width: 0.0,
                font_family: None,
                font_size: None,
                font_weight: None,
                line_height: None,
                blend_mode: motionwright_domain::BlendMode::Normal,
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        let project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "window-fixture-shape",
                &Change::AddCanvasNode { scene_id, node },
            )
            .unwrap()
            .project;
        let request = MotionCanvasWindowRequest {
            render_scale: crate::production::MotionCanvasWindowScale::Full,
            deliverable_id: project.deliverables[0].id,
            source_scene_id: scene_id,
            source_window: SourceFrameWindow { start: 10, end: 12 },
            options: FilmBuildOptions {
                frame_rate: Rate::new(30, 1).unwrap(),
                font_family: crate::film::MOTION_CANVAS_FONT_FAMILY.into(),
                mono_font_family: crate::film::MOTION_CANVAS_MONO_FONT_FAMILY.into(),
                scene_intents: vec![crate::film::SceneFilmIntent {
                    scene_id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                }],
            },
        };
        let plan = request_plan(&project, &request).unwrap();
        let plan_ref = "a1".repeat(32);
        let artifact = json!({"manifest_sha256":"b2".repeat(32)});
        let checks = json!({
            "plan_digest":plan_ref,"base":[{
                "key":{"provider":"driver:motion-canvas","resource":"project"},
                "document_id":"synthetic-native-project","provider_session":"synthetic-unit-session",
                "generation":project.generation.to_string(),
                "revision":{"kind":"fingerprint","value":"f6".repeat(32)},
                "concurrency":"best_effort_revalidate"
            }],"required_rules":["native-frame-coverage"],
            "checks":[{"rule":"native-frame-coverage","version":1,"verdict":"UNKNOWN",
                "evidence_class":"DETERMINISTIC","evidence":[],"reason":"prefix only"}]
        });
        let measurement = json!({
            "version":1,"intent_digest":canonical_digest(&plan.segment.film).unwrap(),
            "coverage":{"first_frame":0,"end_frame_exclusive":12,"observed_frames":12,
                "exhaustive":true,"fps":{"num":30,"den":1},
                "method":"motion-canvas-3.17.2-native-frame-probe-v1",
                "observation_digest":"c3".repeat(32),"render_input_digest":"d4".repeat(32),
                "font_resources_digest":"e5".repeat(32),"artifact_digest":"b2".repeat(32)},
            "metrics":[],"findings":[],"validation":checks
        });
        let verification = json!({"measurement":measurement,"report":{
            "execution_status":"completed","support_level":"native","validation":checks,
            "effects_observed":[],"effects_unobservable":[]
        }});
        let validation = validate_prefix_measurement(
            &verification,
            &plan.segment.film,
            &plan_ref,
            &"f6".repeat(32),
            &artifact,
            12,
            60,
        )
        .unwrap();
        let mut evidence = MotionCanvasWindowEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            request,
            raster: None,
            segment_id: plan.segment.id,
            segment_index: 0,
            segment_window: plan.segment_window,
            film_digest: canonical_digest(&plan.segment.film)
                .unwrap()
                .as_str()
                .to_owned(),
            original_capture: MotionCanvasWindowCapture {
                segment_frame_count: 60,
                prefix: SourceFrameWindow { start: 0, end: 12 },
                plan_ref,
                fingerprint: "f6".repeat(32),
                job_ref: "synthetic-unit-only".into(),
                start_origin: MotionCanvasStartOrigin::FreshAuthenticatedCall,
                artifact,
                verification,
                timings: MotionCanvasCaptureTimings {
                    fonts_micros: 0,
                    plan_micros: 0,
                    apply_micros: 0,
                    render_and_wait_micros: 0,
                    verification_micros: 0,
                },
            },
            carry_source: None,
            validation,
            cached_frames: vec![],
            cost: MotionCanvasWindowCost {
                operation: MotionCanvasWindowUse::NativePrefix,
                original_prefix_pngs: 12,
                selected_pngs: 2,
                reused_original_pngs: 0,
                start_calls_observed: 1,
                cached_png_bytes_returned: 0,
                cache_payload_bytes_admitted: 0,
                cache_micros: 0,
                internal_simulation_steps: None,
                internal_simulation_observation: "not_observed",
            },
        };
        // A bounded cache custody fixture only; no native renderer was invoked.
        let png = b"\x89PNG\r\n\x1a\noriginal-unit-cache-payload";
        let path = directory.path().join("original.png");
        fs::write(&path, png).unwrap();
        for offset in 0..2 {
            let sha256 = hex::encode(Sha256::digest(png));
            let key = frame_key(&evidence, offset, &sha256).unwrap();
            let record = service.put_render_window_proxy(&key, &path, 1).unwrap();
            evidence.cached_frames.push(MotionCanvasCachedWindowFrame {
                source_frame: 10 + offset,
                segment_frame: 10 + offset,
                original_relative_path: format!("synthetic-unit-only/{offset}.png"),
                sha256,
                bytes: png.len() as u64,
                key,
                record,
            });
        }
        (directory, service, project, evidence)
    }

    #[test]
    fn prefix_unknown_is_preserved_and_never_passes_the_full_validator() {
        let (_dir, _service, project, evidence) = fixture();
        assert_eq!(evidence.validation.verdict, Verdict::Unknown);
        assert_eq!(evidence.validation.native_frame_coverage, Verdict::Unknown);
        assert!(
            ensure_native_motion_verification(&evidence.original_capture.verification).is_err()
        );
        let summary = evidence.summary();
        assert!(summary.preview_only);
        assert!(!summary.master_eligible);
        assert!(summary.cost.internal_simulation_steps.is_none());
        let plan = request_plan(&project, &evidence.request).unwrap();
        for pointer in [
            "/measurement/validation/checks/0/verdict",
            "/report/validation/checks/0/verdict",
        ] {
            let mut tampered = evidence.original_capture.verification.clone();
            *tampered.pointer_mut(pointer).unwrap() = json!("PASS");
            assert!(
                validate_prefix_measurement(
                    &tampered,
                    &plan.segment.film,
                    &evidence.original_capture.plan_ref,
                    &evidence.original_capture.fingerprint,
                    &evidence.original_capture.artifact,
                    12,
                    60
                )
                .is_err()
            );
        }
        // The SDK's real report evaluator also keeps a nominally PASS rule
        // UNKNOWN when its deterministic observations are absent.
        let mut nominal = evidence.original_capture.verification.clone();
        nominal["measurement"]["coverage"]["end_frame_exclusive"] = json!(60);
        nominal["measurement"]["coverage"]["observed_frames"] = json!(60);
        nominal["measurement"]["validation"]["checks"][0]["verdict"] = json!("PASS");
        nominal["report"]["validation"] = nominal["measurement"]["validation"].clone();
        let observed = validate_prefix_measurement(
            &nominal,
            &plan.segment.film,
            &evidence.original_capture.plan_ref,
            &evidence.original_capture.fingerprint,
            &evidence.original_capture.artifact,
            60,
            60,
        )
        .unwrap();
        assert_eq!(observed.native_frame_coverage, Verdict::Pass);
        assert_eq!(observed.verdict, Verdict::Unknown);
    }

    #[test]
    fn prefix_failed_or_foreign_native_measurement_is_rejected() {
        let (_dir, _service, project, evidence) = fixture();
        let plan = request_plan(&project, &evidence.request).unwrap();
        for (pointer, value) in [
            ("/measurement/version", json!(999)),
            ("/measurement/intent_digest", json!("01".repeat(32))),
            ("/measurement/coverage/first_frame", json!(1)),
            ("/measurement/coverage/observed_frames", json!(11)),
            ("/measurement/coverage/exhaustive", json!(false)),
            (
                "/measurement/coverage/artifact_digest",
                json!("02".repeat(32)),
            ),
            ("/measurement/coverage/font_resources_digest", Value::Null),
        ] {
            let mut changed = evidence.original_capture.verification.clone();
            *changed.pointer_mut(pointer).unwrap() = value;
            assert!(
                validate_prefix_measurement(
                    &changed,
                    &plan.segment.film,
                    &evidence.original_capture.plan_ref,
                    &evidence.original_capture.fingerprint,
                    &evidence.original_capture.artifact,
                    12,
                    60
                )
                .is_err(),
                "{pointer}"
            );
        }
        let mut failed = evidence.original_capture.verification.clone();
        failed["measurement"]["validation"]["checks"][0]["verdict"] = json!("FAIL");
        failed["report"]["validation"] = failed["measurement"]["validation"].clone();
        assert!(
            validate_prefix_measurement(
                &failed,
                &plan.segment.film,
                &evidence.original_capture.plan_ref,
                &evidence.original_capture.fingerprint,
                &evidence.original_capture.artifact,
                12,
                60
            )
            .is_err()
        );
    }

    #[test]
    fn explicit_original_reuse_reads_existing_store_bytes_without_production_receipts() {
        let (_dir, service, project, evidence) = fixture();
        let before = service.production_receipts(project.id, 512).unwrap();
        let original_bytes =
            read_cached_motion_canvas_window_frame(&service, &evidence, 0).unwrap();
        let original_capture = serde_json::to_value(&evidence.original_capture).unwrap();
        let reused = reuse_motion_canvas_window(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            &evidence.request,
            &evidence,
        )
        .unwrap();
        assert_eq!(
            reused.cost.operation,
            MotionCanvasWindowUse::ExplicitOriginalReuse
        );
        assert_eq!(reused.cost.start_calls_observed, 0);
        assert_eq!(reused.cost.reused_original_pngs, 2);
        assert_eq!(
            reused.cost.cached_png_bytes_returned,
            original_bytes.len() as u64 * 2
        );
        assert_eq!(reused.cost.cache_payload_bytes_admitted, 0);
        assert_eq!(
            serde_json::to_value(&reused.original_capture).unwrap(),
            original_capture
        );
        assert_eq!(
            service.production_receipts(project.id, 512).unwrap(),
            before
        );
    }

    #[test]
    fn unchanged_window_carry_admits_same_film_without_reactivating_old_keys() {
        let (dir, service, project, mut evidence) = fixture();
        let root = dir.path().canonicalize().unwrap();
        let connection = ProductionConnection {
            schema: "motionwright-semwright-connection/1".into(),
            executable: root.join("unused-cli"),
            executable_sha256: "a".repeat(64),
            socket: root.join("unused-socket"),
            session_file: root.join("unused-session"),
            output_root: root.clone(),
            resource: project.resource_key(),
        };
        evidence.carry_source = Some(MotionCanvasWindowCarrySource {
            branch: project.active_branch,
            original_revision: project.revision,
            connection_identity: connection.identity().unwrap(),
        });
        for frame in &evidence.cached_frames {
            let bytes = read_cached_motion_canvas_window_frame(
                &service,
                &evidence,
                frame.source_frame - evidence.request.source_window.start,
            )
            .unwrap();
            let path = root.join(&frame.original_relative_path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, bytes).unwrap();
        }
        let original_keys = evidence
            .cached_frames
            .iter()
            .map(|frame| &frame.key)
            .collect::<Vec<_>>();
        let edited = service
            .apply(
                project.id,
                &project.stamp(),
                "window-unrelated-title-change",
                &Change::RenameProject {
                    title: "Same rendered scene".into(),
                },
            )
            .unwrap()
            .project;
        assert!(read_cached_motion_canvas_window_frame(&service, &evidence, 0).is_err());
        let reused = carry_unchanged_motion_canvas_window(
            &service,
            project.id,
            &edited.stamp(),
            &evidence.request,
            &evidence,
            &connection,
        )
        .unwrap();
        assert_eq!(reused.summary().original_revision, Some(project.revision));
        assert_eq!(reused.revision, edited.revision);
        assert_eq!(
            reused.cost.operation,
            MotionCanvasWindowUse::UnchangedFilmReuse
        );
        assert_eq!(reused.cost.start_calls_observed, 0);
        assert_eq!(reused.cost.reused_original_pngs, 2);
        assert!(
            reused
                .cached_frames
                .iter()
                .zip(&evidence.cached_frames)
                .all(|(new, old)| new.sha256 == old.sha256 && new.key != old.key)
        );
        for key in original_keys {
            assert!(service.lookup_render_window_proxy(key).is_err());
        }
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );

        let mut different = connection.clone();
        different.socket = root.join("foreign-socket");
        assert!(matches!(
            carry_unchanged_motion_canvas_window(
                &service,
                project.id,
                &edited.stamp(),
                &evidence.request,
                &evidence,
                &different,
            ),
            Err(error) if error.code == ErrorCode::StaleReference
        ));

        let boundary_id = Uuid::new_v4();
        let draft = service
            .apply(
                project.id,
                &edited.stamp(),
                "window-continuity-draft",
                &Change::UpsertSceneBoundary {
                    boundary: Box::new(motionwright_domain::SceneBoundary {
                        version: motionwright_domain::SCENE_BOUNDARY_VERSION,
                        id: boundary_id,
                        scene_id: evidence.request.source_scene_id,
                        input: motionwright_domain::SceneBoundaryInput::SelfContained,
                        definition: motionwright_domain::SceneBoundaryDefinition::DraftIncomplete {
                            source: None,
                            entry: None,
                            exit: None,
                        },
                    }),
                },
            )
            .unwrap()
            .project;
        let plan = request_plan(&draft, &evidence.request).unwrap();
        assert_eq!(
            canonical_digest(&plan.segment.film).unwrap().as_str(),
            evidence.film_digest.as_str(),
        );
        let error = carry_unchanged_motion_canvas_window(
            &service,
            project.id,
            &draft.stamp(),
            &evidence.request,
            &evidence,
            &connection,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert!(error.message.contains("unsourced Draft"));
        let mut draft_evidence = evidence.clone();
        draft_evidence.revision = draft.revision;
        for (index, frame) in draft_evidence.cached_frames.iter().enumerate() {
            let key = frame_key(&draft_evidence, index as u64, &frame.sha256).unwrap();
            assert!(service.lookup_render_window_proxy(&key).unwrap().is_none());
        }
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        let edited = service
            .apply(
                project.id,
                &draft.stamp(),
                "window-remove-continuity-draft",
                &Change::RemoveSceneBoundary { boundary_id },
            )
            .unwrap()
            .project;

        fs::write(
            root.join(&evidence.cached_frames[0].original_relative_path),
            b"replacement",
        )
        .unwrap();
        assert!(
            carry_unchanged_motion_canvas_window(
                &service,
                project.id,
                &edited.stamp(),
                &evidence.request,
                &evidence,
                &connection,
            )
            .is_err()
        );

        let scene = &edited.scenes[0];
        let node = &scene.nodes[0];
        let changed = service
            .apply(
                project.id,
                &edited.stamp(),
                "window-change-rendered-shape",
                &Change::TransformCanvasNode {
                    scene_id: scene.id,
                    node_id: node.id,
                    transform: motionwright_domain::CanvasTransform {
                        x: node.x + 10.0,
                        y: node.y,
                        width: node.width,
                        height: node.height,
                        rotation_deg: node.rotation_deg,
                        opacity: node.opacity,
                    },
                },
            )
            .unwrap()
            .project;
        assert!(matches!(
            carry_unchanged_motion_canvas_window(
                &service,
                project.id,
                &changed.stamp(),
                &evidence.request,
                &evidence,
                &connection,
            ),
            Err(error) if error.code == ErrorCode::StaleReference
                && error.message == "Original native window does not match the saved canonical Film"
        ));
    }

    #[test]
    fn old_revision_and_relabelled_cache_scope_are_rejected_without_rekeying() {
        let (_dir, service, project, evidence) = fixture();
        let original_keys = evidence
            .cached_frames
            .iter()
            .map(|frame| frame.key.clone())
            .collect::<Vec<_>>();
        let edited = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "window-edit-after-original",
                &Change::RenameProject {
                    title: "Edited saved source".into(),
                },
            )
            .unwrap()
            .project;
        let result = reuse_motion_canvas_window(
            &service,
            edited.id,
            &RevisionStamp::from(&edited),
            &evidence.request,
            &evidence,
        )
        .unwrap_err();
        assert_eq!(result.code, ErrorCode::StaleReference);
        assert!(read_cached_motion_canvas_window_frame(&service, &evidence, 0).is_err());
        assert!(
            service
                .lookup_render_window_proxy(&original_keys[0])
                .is_err()
        );
        let mut relabelled = evidence.clone();
        relabelled.revision = edited.revision;
        assert!(read_cached_motion_canvas_window_frame(&service, &relabelled, 0).is_err());
        assert!(
            service
                .production_receipts(project.id, 512)
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            evidence
                .cached_frames
                .iter()
                .map(|frame| frame.key.clone())
                .collect::<Vec<_>>(),
            original_keys
        );
    }

    #[test]
    fn missing_or_tampered_cached_originals_never_fall_back_to_a_renderer() {
        let (_dir, service, project, evidence) = fixture();
        let mut wrong_options = evidence.request.clone();
        wrong_options.options.font_family.push_str(" changed");
        assert!(
            reuse_motion_canvas_window(
                &service,
                project.id,
                &RevisionStamp::from(&project),
                &wrong_options,
                &evidence
            )
            .is_err()
        );
        let hit = service
            .lookup_render_window_proxy(&evidence.cached_frames[0].key)
            .unwrap()
            .unwrap();
        fs::write(&hit.blob_path, b"tampered cache").unwrap();
        assert!(read_cached_motion_canvas_window_frame(&service, &evidence, 0).is_err());
        assert!(
            reuse_motion_canvas_window(
                &service,
                project.id,
                &RevisionStamp::from(&project),
                &evidence.request,
                &evidence
            )
            .is_err()
        );
        assert!(
            service
                .production_receipts(project.id, 512)
                .unwrap()
                .is_empty()
        );
        let (_dir, service, project, evidence) = fixture();
        service
            .purge_render_window_proxies(
                project.id,
                &motionwright_domain::RenderTailInspectRequest {
                    expected_generation: project.generation,
                    expected_revision: project.revision,
                    changed_scene_ids: vec![],
                    cause: motionwright_domain::RenderImpactCause::GlobalMix,
                    tail_frames: 0,
                    tail_source_asset_id: None,
                },
            )
            .unwrap();
        assert!(read_cached_motion_canvas_window_frame(&service, &evidence, 0).is_err());
        assert!(
            service
                .production_receipts(project.id, 512)
                .unwrap()
                .is_empty()
        );
    }
}
