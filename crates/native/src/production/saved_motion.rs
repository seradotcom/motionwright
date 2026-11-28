//! Explicit reopening of a completed full capture from the original SQLite
//! history. This restores private accounting custody, never an effect grant.
//! No receipt, root, debit, UNKNOWN state or provider result is rewritten.
use super::*;
use motionwright_domain::Project;
use semwright_native_sdk::types::Execution;
use std::collections::BTreeMap;

const KEY: &str = "original_motion_reopen";
const SCHEMA: &str = "motionwright.original-motion-reopen/1";
const VERIFY: &str = "driver.motion-canvas.composition.verify";
const MAX_SEGMENTS: usize = 64;
const MAX_FRAMES: u64 = 36_000;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SavedMotionBase {
    schema: String,
    project_sha256: String,
    connection_identity: String,
    root_id: Uuid,
    deliverable_id: Uuid,
    request_id: String,
    options: FilmBuildOptions,
    segment_count: usize,
    total_frames: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SavedMotionReceiptContext {
    source: SavedMotionBase,
    index: usize,
}

fn project_digest(project: &Project) -> NativeResult<String> {
    request_digest("motionwright.original-motion.project", &json!(project))
}

fn operation_key(project: &Project, deliverable_id: Uuid) -> String {
    format!(
        "motion-canvas/{}/{}/{}/{}",
        project.id, project.generation, project.revision, deliverable_id
    )
}

fn require_current(
    coordinator: &ProductionCoordinator,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = coordinator
        .service
        .project(project_id)
        .map_err(storage_error)?;
    if project.resource_key() != expected.resource
        || project.generation != expected.generation
        || project.revision != expected.revision
        || coordinator.client.connection().resource != project.resource_key()
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Saved capture source or owner changed",
        ));
    }
    Ok(project)
}

impl SavedMotionBase {
    pub(super) fn for_full(
        coordinator: &ProductionCoordinator,
        project: &Project,
        request_id: &str,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        segments: &[crate::film::MotionCanvasSegment],
    ) -> NativeResult<Option<std::sync::Arc<Self>>> {
        // The experimental hybrid and larger legacy captures keep their own
        // admission. This bounded reopen route cannot change their behavior.
        if coordinator.active_hybrid_budget.is_some()
            || segments.is_empty()
            || segments.len() > MAX_SEGMENTS
            || options.scene_intents.len() > 1024
            || options.font_family.len() > 256
            || options.mono_font_family.len() > 256
        {
            return Ok(None);
        }
        let total_frames = segments.iter().try_fold(0_u64, |total, segment| {
            total
                .checked_add(segment.frame_count)
                .ok_or_else(|| invalid("Saved capture frame overflow"))
        })?;
        if total_frames == 0 || total_frames > MAX_FRAMES {
            return Ok(None);
        }
        let root = coordinator
            .active_motion_budget
            .as_ref()
            .ok_or_else(|| invalid("Full capture has no original accounting root"))?;
        Ok(Some(std::sync::Arc::new(Self {
            schema: SCHEMA.into(),
            project_sha256: project_digest(project)?,
            connection_identity: coordinator.client.connection().identity()?,
            root_id: root.root_id,
            deliverable_id,
            request_id: request_id.into(),
            options: options.clone(),
            segment_count: segments.len(),
            total_frames,
        })))
    }

    fn check(&self, coordinator: &ProductionCoordinator, project: &Project) -> NativeResult<()> {
        if self.schema != SCHEMA
            || self.project_sha256 != project_digest(project)?
            || self.connection_identity != coordinator.client.connection().identity()?
            || self.root_id.is_nil()
            || self.deliverable_id.is_nil()
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == self.deliverable_id)
            || self.request_id.trim().is_empty()
            || self.request_id.len() > 96
            || self.request_id.chars().any(char::is_control)
            || !(1..=MAX_SEGMENTS).contains(&self.segment_count)
            || !(1..=MAX_FRAMES).contains(&self.total_frames)
            || self.options.scene_intents.len() > 1024
            || self.options.font_family.len() > 256
            || self.options.mono_font_family.len() > 256
        {
            return Err(invalid(
                "Saved capture no longer matches its complete source and owner",
            ));
        }
        Ok(())
    }
}

impl SavedMotionReceiptContext {
    pub(super) fn new(source: &SavedMotionBase, index: usize) -> Self {
        Self {
            source: source.clone(),
            index,
        }
    }
    pub(super) fn payload(&self) -> Value {
        json!(self)
    }
    fn from_receipt(receipt: &ProductionReceipt) -> NativeResult<Option<Self>> {
        let Some(value) = receipt.payload.get(KEY) else {
            return Ok(None);
        };
        // Bound the closed context before deserializing another copy of it.
        let object = value
            .as_object()
            .ok_or_else(|| invalid("Saved capture context is malformed"))?;
        let source = object
            .get("source")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("Saved capture source is absent"))?;
        let options = source
            .get("options")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("Saved capture Film options are absent"))?;
        if object.len() != 2
            || source.len() != 9
            || options.len() != 4
            || options
                .get("scene_intents")
                .and_then(Value::as_array)
                .is_none_or(|a| a.len() > 1024)
            || serde_json::to_vec(value)
                .map_err(|_| invalid("Saved capture context cannot be read"))?
                .len()
                > 196_608
        {
            return Err(invalid(
                "Saved capture context exceeds its closed read bounds",
            ));
        }
        let context: Self = serde_json::from_value(value.clone())
            .map_err(|_| invalid("Saved capture context fields are invalid"))?;
        if context.index >= context.source.segment_count
            || context.source.segment_count > MAX_SEGMENTS
        {
            return Err(invalid("Saved capture segment selector is invalid"));
        }
        Ok(Some(context))
    }
    pub(super) fn check_dispatch(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        request_id: &str,
    ) -> NativeResult<()> {
        self.source.check(coordinator, project)?;
        if request_id != format!("{}:motion:{}:verify", self.source.request_id, self.index)
            || self.index >= self.source.segment_count
            || coordinator.active_hybrid_budget.is_some()
            || coordinator
                .active_motion_budget
                .as_ref()
                .map(|root| root.root_id)
                != Some(self.source.root_id)
        {
            return Err(invalid(
                "Saved capture metadata is outside its original full invocation",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct SavedMotionCaptureHint {
    pub receipt_id: Uuid,
    pub capture_sha256: String,
    pub segment_count: usize,
    pub frame_count: u64,
    pub fps_num: u32,
    pub fps_den: u32,
}

#[derive(Clone, Debug, Serialize)]
pub struct SavedMotionCaptureInventory {
    pub schema: &'static str,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub capture: Option<SavedMotionCaptureHint>,
}

/// Backend-only proof. Caller JSON cannot construct it or supply root/limits.
pub struct RecoveredMotionCapture {
    receipt_id: Uuid,
    capture_sha256: String,
    evidence: MotionCanvasRenderEvidence,
    options: FilmBuildOptions,
    owner_root: PathBuf,
}

impl RecoveredMotionCapture {
    pub fn receipt_id(&self) -> Uuid {
        self.receipt_id
    }
    pub fn capture_sha256(&self) -> &str {
        &self.capture_sha256
    }
    pub fn evidence(&self) -> &MotionCanvasRenderEvidence {
        &self.evidence
    }
    pub fn options(&self) -> &FilmBuildOptions {
        &self.options
    }
    pub fn owner_root(&self) -> &Path {
        &self.owner_root
    }
}

struct CaptureSource {
    base: SavedMotionBase,
    evidence: MotionCanvasRenderEvidence,
    receipt_id: Uuid,
}

impl CaptureSource {
    fn hint(&self) -> NativeResult<SavedMotionCaptureHint> {
        Ok(SavedMotionCaptureHint {
            receipt_id: self.receipt_id,
            capture_sha256: request_digest("motion-canvas.original-render", &json!(self.evidence))?,
            segment_count: self.base.segment_count,
            frame_count: self.base.total_frames,
            fps_num: self.base.options.frame_rate.num,
            fps_den: self.base.options.frame_rate.den,
        })
    }
}

// Each admitted atomic call has exactly its original intent and terminal.
// Neither a later completed row nor a new request label reconciles UNKNOWN.
fn original<'a>(
    rows: &'a BTreeMap<String, Vec<ProductionReceipt>>,
    base: &SavedMotionBase,
    project: &Project,
    request: &str,
    command: &str,
    args: Value,
    mutation: bool,
) -> NativeResult<(&'a ProductionReceipt, &'a Value)> {
    let pair = rows
        .get(request)
        .ok_or_else(|| invalid("Saved capture atomic history is incomplete"))?;
    if pair.iter().any(|row| row.stage == "outcome_unknown") {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Saved capture retains an UNKNOWN atomic result",
        )
        .uncertain());
    }
    if pair.len() != 2 || pair[0].stage != "completed" || pair[1].stage != "dispatching" {
        return Err(invalid(
            "Saved capture needs exactly its original intent and completion",
        ));
    }
    let digest = request_digest(command, &args)?;
    for row in pair {
        if row.project_id != project.id
            || row.generation != project.generation
            || row.revision != project.revision
            || row.command != command
            || row.request_sha256 != digest
        {
            return Err(invalid("Saved capture atomic source identity changed"));
        }
    }
    if pair[1].payload.get("connection").and_then(Value::as_str)
        != Some(base.connection_identity.as_str())
        || pair[1].payload.get("mutation").and_then(Value::as_bool) != Some(mutation)
    {
        return Err(invalid("Saved capture dispatch owner or operation changed"));
    }
    let result = pair[0]
        .payload
        .get("result")
        .ok_or_else(|| invalid("Saved capture native result is absent"))?;
    if result.get("provenance_checked").and_then(Value::as_bool) != Some(true) {
        return Err(invalid("Saved capture has no original checked provenance"));
    }
    let execution: Execution = serde_json::from_value(
        result
            .get("execution")
            .cloned()
            .ok_or_else(|| invalid("Saved capture native execution is absent"))?,
    )
    .map_err(|_| invalid("Saved capture native execution is malformed"))?;
    let provenance = execution
        .provenance
        .as_ref()
        .ok_or_else(|| invalid("Saved capture provider is absent"))?;
    if execution.dry_run
        || provenance.untrusted_metadata
        || provenance.provider != "driver:motion-canvas"
        || provenance.source != SourceKind::Driver
        || provenance.execution_provider.as_deref() != Some("driver:motion-canvas")
        || provenance.provider_generation.is_none()
        || !is_sha256(&provenance.descriptor_sha256)
        || pair[0]
            .payload
            .get("broker_request_id")
            .and_then(Value::as_str)
            .is_none_or(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))
    {
        return Err(invalid(
            "Saved capture is not the original native driver execution",
        ));
    }
    Ok((
        &pair[0],
        result
            .get("data")
            .ok_or_else(|| invalid("Saved capture data is absent"))?,
    ))
}

fn reference(value: &Value, pointer: &str) -> NativeResult<String> {
    let text = required_string(value, pointer, "Saved capture native reference is absent")?;
    if text.len() > 256 || text.chars().any(char::is_control) {
        return Err(invalid("Saved capture native reference exceeds its bound"));
    }
    Ok(text)
}

impl ProductionCoordinator {
    fn read_saved_motion_source(
        &self,
        project: &Project,
        deliverable_id: Uuid,
        selected_id: Option<Uuid>,
    ) -> NativeResult<Option<CaptureSource>> {
        let mut selected: Option<(Uuid, SavedMotionReceiptContext)> = None;
        audio_analysis::receipt_history::visit_history(&self.service, project.id, true, |row| {
            if row.generation != project.generation
                || row.revision != project.revision
                || row.command != VERIFY
                || row.stage != "completed"
            {
                return Ok(());
            }
            let Some(context) = SavedMotionReceiptContext::from_receipt(row)? else {
                return Ok(());
            };
            if context.source.deliverable_id != deliverable_id || context.index != 0 {
                return Ok(());
            }
            context.source.check(self, project)?;
            if let Some((_, prior)) = &selected {
                // A second full capture in the same accounting root is not a
                // license to choose a different original after process restart.
                if prior != &context {
                    return Err(invalid(
                        "Multiple full captures need their original retained custody",
                    ));
                }
                return Err(invalid("Saved capture has duplicate original completions"));
            }
            selected = Some((row.id, context));
            Ok(())
        })?;
        let Some((receipt_id, context)) = selected else {
            if selected_id.is_some() {
                return Err(invalid("Selected saved capture is unavailable"));
            }
            return Ok(None);
        };
        if selected_id.is_some_and(|id| id != receipt_id) {
            return Err(invalid(
                "Saved capture selector belongs to another original",
            ));
        }
        let base = context.source;
        let segments = plan_motion_canvas_segments(project, deliverable_id, &base.options)?;
        let total = segments.iter().try_fold(0_u64, |total, segment| {
            total
                .checked_add(segment.frame_count)
                .ok_or_else(|| invalid("Saved capture frame overflow"))
        })?;
        if segments.len() != base.segment_count || total != base.total_frames {
            return Err(invalid("Saved capture no longer matches the complete Film"));
        }
        self.require_no_unresolved_scene_requests(
            project.id,
            &base.request_id,
            SceneRequestFamily::Motion,
        )?;
        let mut rows: BTreeMap<String, Vec<ProductionReceipt>> = BTreeMap::new();
        for index in 0..segments.len() {
            for suffix in ["plan", "apply", "render-start", "render-result", "verify"] {
                rows.insert(
                    format!("{}:motion:{index}:{suffix}", base.request_id),
                    Vec::new(),
                );
            }
        }
        audio_analysis::receipt_history::visit_history(&self.service, project.id, false, |row| {
            if let Some(pair) = rows.get_mut(&row.request_id) {
                if pair.len() >= 3 {
                    return Err(invalid("Saved capture atomic history is ambiguous"));
                }
                pair.push(row.clone());
            }
            Ok(())
        })?;
        let mut evidence = Vec::with_capacity(segments.len());
        for (index, segment) in segments.into_iter().enumerate() {
            let prefix = format!("{}:motion:{index}", base.request_id);
            let (_, planned) = original(
                &rows,
                &base,
                project,
                &format!("{prefix}:plan"),
                "driver.motion-canvas.composition.plan",
                json!({
                    "film": segment.film, "budget": {
                        "max_iterations":8, "max_operations":MOTION_PLAN_MAX_OPERATIONS,
                        "max_findings":1024, "max_observations":128, "max_elapsed_ms":120000
                    }
                }),
                false,
            )?;
            let plan_ref = reference(planned, "/plan_ref")?;
            let (_, applied) = original(
                &rows,
                &base,
                project,
                &format!("{prefix}:apply"),
                "driver.motion-canvas.composition.apply",
                json!({"plan_ref":plan_ref,"dry_run":false}),
                true,
            )?;
            if applied.get("applied").and_then(Value::as_bool) != Some(true)
                || applied.get("execution_status").and_then(Value::as_str) != Some("completed")
            {
                return Err(invalid(
                    "Saved capture has no completed original Film application",
                ));
            }
            let fingerprint = reference(applied, "/fingerprint")?;
            let (start_row, started) = original(
                &rows,
                &base,
                project,
                &format!("{prefix}:render-start"),
                "driver.motion-canvas.render.start",
                json!({
                    "expected_fingerprint":fingerprint,
                    "profile":{"first_frame":0,"end_frame_exclusive":segment.frame_count,
                        "scale":"full","transparent":false,"timeout_ms":MOTION_RENDER_TIMEOUT_MS}
                }),
                true,
            )?;
            let job_ref = reference(started, "/job_ref")?;
            let original_generation = motion_render_generation(start_row.payload.get("result"))?;
            let (result_row, rendered) = original(
                &rows,
                &base,
                project,
                &format!("{prefix}:render-result"),
                "driver.motion-canvas.render.result",
                json!({"job_ref":job_ref}),
                false,
            )?;
            require_motion_render_generation(
                result_row.payload.get("result"),
                original_generation,
            )?;
            require_acknowledged_motion_job(rendered, &job_ref, "saved result")?;
            if rendered.get("state").and_then(Value::as_str) != Some("succeeded") {
                return Err(invalid("Saved capture original render did not succeed"));
            }
            let artifact = rendered
                .get("artifact")
                .ok_or_else(|| invalid("Saved capture artifact is absent"))?;
            if artifact.get("frame_count").and_then(Value::as_u64) != Some(segment.frame_count)
                || artifact
                    .get("manifest_sha256")
                    .and_then(Value::as_str)
                    .is_none_or(|s| !is_sha256(s))
            {
                return Err(invalid(
                    "Saved capture needs its original native manifest digest and full frame range",
                ));
            }
            let (verified_row, verification) = original(
                &rows,
                &base,
                project,
                &format!("{prefix}:verify"),
                VERIFY,
                json!({"plan_ref":plan_ref,"job_ref":job_ref}),
                false,
            )?;
            require_motion_render_generation(
                verified_row.payload.get("result"),
                original_generation,
            )?;
            let expected_context = SavedMotionReceiptContext::new(&base, index);
            for row in &rows[&format!("{prefix}:verify")] {
                if SavedMotionReceiptContext::from_receipt(row)?.as_ref() != Some(&expected_context)
                {
                    return Err(invalid(
                        "Saved capture context differs from its original intent",
                    ));
                }
            }
            if index == 0 && verified_row.id != receipt_id {
                return Err(invalid("Saved capture selected completion changed"));
            }
            ensure_native_motion_verification(verification)?;
            if segment.film.version == semwright_motion_authoring::AUTHORING_BLEND_VERSION {
                let digest = semwright_semantic_composition::canonical_digest(&segment.film)
                    .map_err(|_| invalid("Saved Film intent cannot be hashed"))?;
                if verification
                    .pointer("/measurement/intent_digest")
                    .and_then(Value::as_str)
                    != Some(digest.as_str())
                    || verification
                        .pointer("/measurement/validation/plan_digest")
                        .and_then(Value::as_str)
                        != Some(plan_ref.as_str())
                {
                    return Err(invalid(
                        "Saved Film v2 verification is detached from its original plan",
                    ));
                }
            }
            let audio_cue_frame_coverage = motion_canvas_window::verified_audio_cue_frames(
                &segment,
                &plan_ref,
                &fingerprint,
                artifact,
                verification,
            )?;
            evidence.push(MotionCanvasSegmentEvidence {
                segment_id: segment.id,
                scene_ids: segment.scene_ids,
                frame_count: segment.frame_count,
                plan_ref,
                fingerprint,
                job_ref,
                artifact: artifact.clone(),
                verification: verification.clone(),
                audio_cue_frame_coverage,
            });
        }
        Ok(Some(CaptureSource {
            evidence: MotionCanvasRenderEvidence {
                project_resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
                deliverable_id,
                frame_rate: base.options.frame_rate,
                segments: evidence,
            },
            base,
            receipt_id,
        }))
    }

    pub fn saved_motion_capture_inventory(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
    ) -> NativeResult<SavedMotionCaptureInventory> {
        let project = require_current(self, project_id, expected)?;
        if !project.deliverables.iter().any(|p| p.id == deliverable_id) {
            return Err(invalid("Saved capture profile is unavailable"));
        }
        let watermark = self
            .service
            .production_receipt_watermark(project_id)
            .map_err(storage_error)?;
        let capture = self
            .read_saved_motion_source(&project, deliverable_id, None)?
            .map(|source| source.hint())
            .transpose()?;
        self.check_saved_motion_snapshot(&project, expected, &watermark)?;
        Ok(SavedMotionCaptureInventory {
            schema: "motionwright.saved-motion-inventory/1",
            project_id,
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            capture,
        })
    }

    fn check_saved_motion_snapshot(
        &self,
        project: &Project,
        expected: &RevisionStamp,
        watermark: &motionwright_storage::ProductionReceiptWatermark,
    ) -> NativeResult<()> {
        let current = require_current(self, project.id, expected)?;
        if project_digest(&current)? != project_digest(project)?
            || self
                .service
                .production_receipt_watermark(project.id)
                .map_err(storage_error)?
                != *watermark
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Saved capture source or receipt snapshot changed",
            ));
        }
        Ok(())
    }

    pub fn recover_saved_motion_capture(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        receipt_id: Uuid,
    ) -> NativeResult<RecoveredMotionCapture> {
        if self.active_hybrid_budget.is_some() {
            return Err(invalid(
                "Saved full capture cannot attach to an experimental hybrid operation",
            ));
        }
        let project = require_current(self, project_id, expected)?;
        let watermark = self
            .service
            .production_receipt_watermark(project_id)
            .map_err(storage_error)?;
        let source = self
            .read_saved_motion_source(&project, deliverable_id, Some(receipt_id))?
            .ok_or_else(|| invalid("Selected saved capture is unavailable"))?;
        let root = self
            .service
            .root_operation_budget(project_id, source.base.root_id)
            .map_err(storage_error)?;
        let key = operation_key(&project, deliverable_id);
        if root.operation_key != key
            || root.generation != project.generation
            || root.revision != project.revision
        {
            return Err(invalid(
                "Saved capture no longer owns its original durable budget",
            ));
        }
        // Reuse the existing complete planner in an ISOLATED cache. A
        // provisional record is never visible to another Desktop invocation.
        // The hit path loads SQLite and checks exact limits; it cannot create a
        // root. The placeholder scope cannot authorize any provider effect.
        let digest = source.hint()?.capture_sha256;
        let source_digest = request_digest(
            "motion-canvas.original-film-options",
            &json!(source.base.options),
        )?;
        let mut isolated = self.clone();
        isolated.motion_budget_roots = Default::default();
        isolated
            .motion_budget_roots
            .lock()
            .map_err(|_| backend("Saved capture validation custody is unavailable"))?
            .insert(
                key.clone(),
                RetainedMotionRoot {
                    root: root.clone(),
                    store_session: store_session_identity(&self.service),
                    connection_identity: source.base.connection_identity.clone(),
                    source_digest: source_digest.clone(),
                    scope: MotionRootBudgetScope {
                        root_id: root.root_id,
                        max_render_frames: 0,
                        max_frame_bytes: 0,
                        mlt_route: None,
                        mlt: None,
                        audio_analysis: false,
                    },
                    render_digest: Some(digest.clone()),
                    direct_mlt_scene_scope: None,
                },
            );
        let scene_ids = source
            .evidence
            .segments
            .iter()
            .flat_map(|s| s.scene_ids.iter().copied())
            .collect::<Vec<_>>();
        let scoped = isolated.with_motion_scene_boundary_scope(
            &project,
            deliverable_id,
            &source.base.options,
            &scene_ids,
        )?;
        let scope = scoped
            .active_motion_budget
            .clone()
            .ok_or_else(|| invalid("Saved capture has no derived budget"))?;
        let direct_mlt_scene_scope = (scope.mlt_route == Some(MltBudgetRoute::Direct)).then(|| {
            std::sync::Arc::new(crate::native_scene_boundary::ExecutionScope::mlt_motion(
                &project,
                deliverable_id,
                &source.base.options,
                &scene_ids,
            ))
        });
        recovery::validate_saved_motion_proof(
            &self.client.connection().output_root,
            &source.evidence,
        )?;
        self.check_saved_motion_snapshot(&project, expected, &watermark)?;
        source.base.check(self, &project)?;
        if self
            .service
            .root_operation_budget(project_id, root.root_id)
            .map_err(storage_error)?
            != root
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Original accounting changed during capture recovery",
            ));
        }
        let mut cache = self
            .motion_budget_roots
            .lock()
            .map_err(|_| backend("Original accounting custody is unavailable"))?;
        if let Some(prior) = cache.get(&key) {
            prior.require_original_owner(&self.service, self.client.connection())?;
            if prior.root.root_id != root.root_id
                || prior.source_digest != source_digest
                || prior.render_digest.as_ref() != Some(&digest)
            {
                return Err(invalid(
                    "Saved capture cannot replace another retained original",
                ));
            }
        } else {
            if cache.len() >= MAX_RETAINED_MOTION_ROOTS {
                return Err(invalid(
                    "Original accounting custody has reached its retained operation limit",
                ));
            }
            cache.insert(
                key,
                RetainedMotionRoot {
                    root,
                    store_session: store_session_identity(&self.service),
                    connection_identity: source.base.connection_identity,
                    source_digest,
                    scope,
                    render_digest: Some(digest.clone()),
                    direct_mlt_scene_scope,
                },
            );
        }
        Ok(RecoveredMotionCapture {
            receipt_id: source.receipt_id,
            capture_sha256: digest,
            evidence: source.evidence,
            options: source.base.options,
            owner_root: self.client.connection().output_root.clone(),
        })
    }
}
