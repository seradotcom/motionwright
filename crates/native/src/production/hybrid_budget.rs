//! A producer-owned root for the admitted MC / static Manim / MC route.
//!
//! This is accounting, not a Host grant. All calls still pass the original
//! permission, revision, receipt custody and UNKNOWN fences. A caller receives
//! original evidence, never a coordinator with arbitrary budgeted execution.
use super::*;
use motionwright_domain::{Project, RendererKind};
use semwright_video_domain::native_observation;
use std::{collections::BTreeMap, io::Write, sync::Arc};

const MEDIA_MAX_BYTES: u64 = 256_000_000;
const MASTER_MAX_BYTES: u64 = 32 * 1024 * 1024;
const MANIM_OBSERVATION_FRAME_MAX_BYTES: u64 = 32 * 1024 * 1024;
const MANIM_OBSERVATION_REPORT_MAX_BYTES: u64 = 16 * 1024 * 1024;
// Closed native + Host FFV1 custody on selected SDK e64a2d28, runtime.rs
// preflight_ffv1_custody: six bounded native stages and two Host envelopes.
const FFV1_CUSTODY_BYTES: u64 =
    6 * (2 * 16_384 + 65_536) + 65_536 + 2 * (65_536 + 2 * 262_144 + 65_536);
// Selected SDK e64 xml.rs MAX_XML; the native render/mux each retain one
// original .native-input.mlt. The Host's two-copy scratch check stays separate.
const NATIVE_INPUT_XML_MAX_BYTES: u64 = 8 * 1024 * 1024;

/// Retained outputs of the actual two MC jobs and actual static Manim job.
/// It cannot be reconstructed from JSON or rebound to another coordinator.
pub struct HybridNativeSources {
    pub(super) project: Project,
    pub(super) expected: RevisionStamp,
    pub(super) deliverable_id: Uuid,
    pub(super) options: FilmBuildOptions,
    pub(super) project_sha256: String,
    pub(super) namespace: String,
    pub(super) request_prefix: String,
    pub(super) plan: Value,
    pub(super) motion_canvas: MotionCanvasRenderEvidence,
    pub(super) manim: Value,
    evidence: Value,
    owner: ProductionCoordinator,
    scope: HybridRootBudgetScope,
}

impl HybridNativeSources {
    pub fn evidence(&self) -> &Value {
        &self.evidence
    }
}

#[derive(Debug, Clone)]
struct MotionContribution {
    segment_id: String,
    scene_ids: Vec<Uuid>,
    frames: u64,
    output_ordinal: usize,
}

#[derive(Debug)]
struct HybridBudgetPlan {
    project: Project,
    expected: RevisionStamp,
    deliverable_id: Uuid,
    options: FilmBuildOptions,
    connection_identity: String,
    project_sha256: String,
    namespace: String,
    request_prefix: String,
    source_plan: Value,
    motion: Vec<MotionContribution>,
    motion_frames: u64,
    frame_reservation: u64,
    manim_scene_id: Uuid,
    manim_plan: Value,
    manim_plan_sha256: String,
    manim_profile: Value,
    manim_frames: u64,
    total_frames: u64,
    width: u32,
    height: u32,
    source_audio_sha256: String,
    source_audio_bytes: u64,
    source_audio_path: PathBuf,
    limits: RootBudgetUnits,
}

#[derive(Debug, Clone)]
struct HybridMltBinding {
    encodes: BTreeMap<String, MltEncodeBudget>,
    source_paths: Vec<String>,
}

#[derive(Debug, Clone)]
enum HybridPhase {
    Sources,
    Mlt(HybridMltBinding),
}

#[derive(Debug, Clone)]
pub(super) struct HybridRootBudgetScope {
    root: RootOperationBudget,
    plan: Arc<HybridBudgetPlan>,
    phase: HybridPhase,
}

fn bounded_add(left: u64, right: u64) -> NativeResult<u64> {
    left.checked_add(right)
        .ok_or_else(|| invalid("Hybrid native resource reservation overflow"))
}
fn bounded_mul(left: u64, right: u64) -> NativeResult<u64> {
    left.checked_mul(right)
        .ok_or_else(|| invalid("Hybrid native resource reservation overflow"))
}
fn require(condition: bool, message: &'static str) -> NativeResult<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(message))
    }
}
fn supported(condition: bool, message: &'static str) -> NativeResult<()> {
    if condition {
        Ok(())
    } else {
        Err(Error::new(ErrorCode::Unsupported, message))
    }
}
fn one_attempt() -> RootBudgetUnits {
    RootBudgetUnits {
        attempts: 1,
        model_calls: 0,
        render_frames: 0,
        output_bytes: 0,
    }
}
fn project_digest(project: &Project) -> NativeResult<String> {
    // The original/reopened project files and consumer use these exact bytes.
    let mut bytes = serde_json::to_vec_pretty(project)
        .map_err(|_| invalid("Hybrid original Project cannot be serialized"))?;
    bytes.push(b'\n');
    Ok(hex::encode(Sha256::digest(bytes)))
}
fn exact_frames(num: i64, den: i64, rate: Rate) -> NativeResult<u64> {
    require(
        num >= 0 && den > 0 && rate.num > 0 && rate.den > 0,
        "Hybrid native clock is negative or invalid",
    )?;
    let numerator = i128::from(num) * i128::from(rate.num);
    let denominator = i128::from(den) * i128::from(rate.den);
    require(
        numerator % denominator == 0,
        "Hybrid native clock is not frame-exact",
    )?;
    u64::try_from(numerator / denominator)
        .map_err(|_| invalid("Hybrid native clock overflows its frame bound"))
}
fn operation_key(project: &Project, deliverable_id: Uuid) -> String {
    // Deliberately the SAME key as the pre-existing MC root. A new label,
    // renderer route, coordinator or process cannot buy replacement credit.
    format!(
        "motion-canvas/{}/{}/{}/{}",
        project.id, project.generation, project.revision, deliverable_id
    )
}
fn observation_bytes(width: u32, height: u32, frames: u64) -> NativeResult<u64> {
    let budget =
        native_observation::observation_budget(width, height, frames).map_err(|error| {
            invalid(format!(
                "Hybrid native observation exceeds the pinned SDK profile: {error}"
            ))
        })?;
    // Charge the actual retained bound. The SDK separately checks its measured
    // peak_free_bytes for scratch/publication; that is not retained disk usage.
    bounded_add(
        budget.retained_png_bytes_upper_bound,
        native_observation::REPORT_LIMIT as u64,
    )
}
fn native_host_custody_bytes() -> NativeResult<u64> {
    // Selected Host main.rs preflight_native_host_custody retains an original
    // report copy, bounded input request, terminal result and custody receipt.
    bounded_add(
        native_observation::REPORT_LIMIT as u64,
        bounded_add(native_observation::MAX_JSON as u64, 4_096 + 16_384)?,
    )
}
fn manim_output_bytes(frames: u64) -> NativeResult<u64> {
    let source = u64::try_from(motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2)
        .map_err(|_| invalid("Static Manim source bound is invalid"))?;
    // Original MP4, decoded original frames/report, and bounded source,
    // readback and recovery package. The reservation does not assert outputs.
    let observations = bounded_add(
        bounded_mul(frames, MANIM_OBSERVATION_FRAME_MAX_BYTES)?,
        MANIM_OBSERVATION_REPORT_MAX_BYTES,
    )?;
    bounded_add(
        bounded_add(MANIM_VIDEO_MAX_BYTES, observations)?,
        bounded_mul(source, 4)?,
    )
}

impl HybridBudgetPlan {
    fn build(
        coordinator: &ProductionCoordinator,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<Self> {
        let project = coordinator
            .service
            .project(project_id)
            .map_err(storage_error)?;
        require(
            project.id == project_id && RevisionStamp::from(&project) == *expected,
            "Hybrid production requires the current full Project stamp",
        )?;
        project
            .validate()
            .map_err(|_| invalid("Hybrid canonical Project is invalid"))?;
        require(
            coordinator.client.connection().resource == project.resource_key(),
            "Hybrid provider connection belongs to another project",
        )?;
        let connection_identity = coordinator.client.connection().identity()?;
        supported(
            project.production_design.scene_boundaries.is_empty(),
            "The mixed native route has no typed lowering for declared SceneBoundaries",
        )?;
        supported(
            project.scenes.len() == 3
                && project.scenes[0].renderer == RendererKind::MotionCanvas
                && project.scenes[1].renderer == RendererKind::ManimCommunity
                && project.scenes[2].renderer == RendererKind::MotionCanvas,
            "The admitted hybrid route requires exactly MC / static Manim / MC",
        )?;
        let profile = project
            .deliverables
            .iter()
            .find(|row| row.id == deliverable_id)
            .ok_or_else(|| invalid("Hybrid saved master profile is absent"))?;
        supported(
            profile.width == 1920
                && profile.height == 1080
                && profile.frame_rate.num == 30
                && profile.frame_rate.den == 1
                && options.frame_rate.num == 30
                && options.frame_rate.den == 1
                && profile.video_codec == VideoCodec::H264
                && profile.audio_codec == AudioCodec::Aac
                && profile.audio_sample_rate_hz == 48_000
                && profile.container == OutputContainer::Mp4
                && profile.color_space == OutputColorSpace::Rec709
                && !profile.captions
                && !profile.burn_in_captions
                && profile.included_scene_ids.is_empty(),
            "The admitted hybrid master is full-cut 1080p30 Rec.709 H264/AAC without captions",
        )?;
        supported(
            project.audio.sfx_cues.is_empty()
                && project.audio.cues.is_empty()
                && project.audio.transcript.is_empty()
                && project
                    .production_design
                    .delivery_recipes
                    .get(&deliverable_id)
                    .is_none_or(|recipe| recipe.original_mix.is_none()),
            "The hybrid native route cannot discard authored mixing or captions",
        )?;

        let mut counts = Vec::with_capacity(project.scenes.len());
        let mut cursor = 0;
        for scene in &project.scenes {
            let start = exact_frames(scene.start.num, scene.start.den, options.frame_rate)?;
            let frames = exact_frames(scene.duration.num, scene.duration.den, options.frame_rate)?;
            supported(
                start == cursor && frames == 60,
                "The admitted hybrid cut requires contiguous sixty-frame contributions",
            )?;
            cursor = bounded_add(cursor, frames)?;
            counts.push(frames);
        }
        // This guard describes the currently implemented finite route. Costs
        // below use the actual canonical clocks, never a caller's limits or a
        // global replacement for the general MC or Manim producers.
        supported(
            cursor == 180,
            "The admitted hybrid cut contains 180 canonical frames",
        )?;

        let segments = plan_motion_canvas_segments(&project, deliverable_id, options)?;
        supported(
            segments.len() == 2,
            "Hybrid MC source partition is not exactly two runs",
        )?;
        let mut motion = Vec::with_capacity(segments.len());
        let mut motion_frames = 0;
        let mut frame_reservation = MOTION_ROOT_FRAME_RESERVE_MIN_BYTES;
        for (index, segment) in segments.iter().enumerate() {
            let ordinal = index * 2;
            let scene = &project.scenes[ordinal];
            require(
                segment.scene_ids == [scene.id]
                    && segment.frame_count == counts[ordinal]
                    && exact_frames(
                        segment.global_start.num,
                        segment.global_start.den,
                        options.frame_rate,
                    )? == exact_frames(scene.start.num, scene.start.den, options.frame_rate)?
                    && segment.film.output.width == profile.width
                    && segment.film.output.height == profile.height,
                "Hybrid MC plan differs from the canonical contribution",
            )?;
            let pixels = bounded_mul(
                u64::from(segment.film.output.width),
                u64::from(segment.film.output.height),
            )?;
            frame_reservation =
                frame_reservation.max(bounded_add(bounded_mul(pixels, 4)?, 1_048_576)?);
            motion_frames = bounded_add(motion_frames, segment.frame_count)?;
            motion.push(MotionContribution {
                segment_id: segment.id.clone(),
                scene_ids: segment.scene_ids.clone(),
                frames: segment.frame_count,
                output_ordinal: ordinal,
            });
        }
        let manim_scene_id = project.scenes[1].id;
        manim_graph::require_non_graph_saved_selection(
            &coordinator.service,
            project_id,
            expected,
            manim_scene_id,
            deliverable_id,
        )?;
        // Compile static source BEFORE the first MC effect; unsupported Manim
        // features cannot be discovered only after paying for native renders.
        let source = crate::manim_static_projection::prepare_saved_manim_static_source(
            &coordinator.service,
            project_id,
            expected,
            manim_scene_id,
            deliverable_id,
        )?;
        let manim_frames = u64::from(source.plan.clock.frame_count);
        require(
            manim_frames == counts[1]
                && source.plan.clock.project_start_frame == counts[0]
                && source.plan.clock.project_end_frame_exclusive
                    == bounded_add(counts[0], counts[1])?
                && source.plan.clock.local_start_frame == 0
                && source.plan.render_profile.width == profile.width
                && source.plan.render_profile.height == profile.height
                && source.plan.render_profile.frame_rate == options.frame_rate.num,
            "Static Manim plan differs from its canonical clock or saved master",
        )?;
        let manim_plan = serde_json::to_value(&source.plan)
            .map_err(|_| invalid("Hybrid Manim source cannot be encoded"))?;
        let manim_profile = serde_json::to_value(&source.plan.render_profile)
            .map_err(|_| invalid("Hybrid Manim profile cannot be encoded"))?;

        let voice_id = profile
            .voice_track_id
            .ok_or_else(|| invalid("Hybrid original voice is absent"))?;
        let voice = project
            .audio
            .voice_tracks
            .iter()
            .find(|row| row.id == voice_id)
            .ok_or_else(|| invalid("Hybrid original voice is not saved in the Project"))?;
        supported(
            voice.sample_rate_hz == 48_000
                && voice.channels == 2
                && exact_frames(
                    voice.measured_duration.num,
                    voice.measured_duration.den,
                    options.frame_rate,
                )? == cursor,
            "The hybrid original voice must be stereo 48kHz on the canonical timeline",
        )?;
        let verified = coordinator
            .service
            .verified_master_voice(project_id, expected, voice_id)
            .map_err(storage_error)?;
        let audio_frames = bounded_mul(cursor, 48_000)? / u64::from(options.frame_rate.num);
        let source_audio_bytes = bounded_add(44, bounded_mul(audio_frames, 4)?)?;
        require(
            verified.sha256 == voice.source_sha256 && verified.size_bytes == source_audio_bytes,
            "Hybrid saved voice identity or PCM byte count differs from its canonical clock",
        )?;
        let parent = verified
            .path
            .parent()
            .ok_or_else(|| invalid("Hybrid original voice has no CAS parent"))?;
        let name = verified
            .path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| invalid("Hybrid original voice has no valid CAS member"))?;
        let wave =
            read_verified_production_artifact(parent, name, &verified.sha256, source_audio_bytes)?;
        verify_original_pcm(&wave, audio_frames)?;

        let project_sha256 = project_digest(&project)?;
        let namespace = format!("mw-hybrid-{}", &project_sha256[..20]);
        // The full saved Project digest includes generation/revision. Distinct
        // saved profiles also receive distinct IDs. Same source reuses exactly
        // these IDs; neither caller labels nor process identity participate.
        let request_prefix = format!(
            "hybrid180-{}",
            request_digest(
                "native.hybrid.original",
                &json!({"project_sha256":project_sha256,"deliverable_id":deliverable_id})
            )?
        );
        let source_plan = json!({"schema":"motionwright.native.hybrid-source-plan/1",
        "project_sha256":project_sha256,"contributions":[
            {"renderer":"motion_canvas","scene_ids":motion[0].scene_ids,
                "segment_id":motion[0].segment_id,"output_start_frame":0,"frame_count":counts[0]},
            {"renderer":"manim_static","scene_ids":[manim_scene_id],
                "plan_sha256":source.closure.plan_sha256,"output_start_frame":counts[0],"frame_count":counts[1]},
            {"renderer":"motion_canvas","scene_ids":motion[1].scene_ids,
                "segment_id":motion[1].segment_id,"output_start_frame":bounded_add(counts[0],counts[1])?,
                "frame_count":counts[2]}
        ]});
        let segment_count =
            u64::try_from(motion.len()).map_err(|_| invalid("Hybrid MC count overflows"))?;
        let mc_polls = bounded_add(
            MOTION_RENDER_POLL_DEADLINE_SECS * 1_000 / MOTION_RENDER_POLL_INTERVAL_MS,
            MOTION_ROOT_NON_POLL_ATTEMPTS_PER_SEGMENT,
        )?;
        let manim_polls = bounded_add(
            MANIM_RENDER_POLL_DEADLINE_SECS * 1_000 / MANIM_RENDER_POLL_INTERVAL_MS,
            2,
        )?;
        // Encodes + project/sequence/track + four calls per clip + final
        // lookup/duration + plan/start/result + bounded status + one mux.
        let mlt_calls = bounded_add(
            segment_count,
            bounded_add(bounded_mul(counts.len() as u64, 4)?, 3 + 2 + 3 + 330 + 1)?,
        )?;
        let attempts = bounded_add(
            bounded_add(bounded_mul(segment_count, mc_polls)?, manim_polls)?,
            mlt_calls,
        )?;
        // Actual native frame passes: MC, Manim, two MC encodes, one semantic
        // timeline, one mux. Manim is never charged as a fictitious MC encode.
        let render_frames = bounded_add(
            bounded_add(motion_frames, manim_frames)?,
            bounded_add(motion_frames, bounded_mul(cursor, 2)?)?,
        )?;
        let mc_output = bounded_add(
            bounded_mul(motion_frames, frame_reservation)?,
            bounded_add(
                bounded_mul(bounded_mul(segment_count, 2)?, MAX_BRAND_FONT_BYTES)?,
                bounded_mul(
                    bounded_mul(segment_count, 3)?,
                    MOTION_ROOT_FRAME_RESERVE_MIN_BYTES,
                )?,
            )?,
        )?;
        let mut encode_output = 0;
        for contribution in &motion {
            encode_output = bounded_add(
                encode_output,
                bounded_add(
                    MEDIA_MAX_BYTES,
                    bounded_add(
                        observation_bytes(profile.width, profile.height, contribution.frames)?,
                        FFV1_CUSTODY_BYTES,
                    )?,
                )?,
            )?;
        }
        let complete_observations = bounded_add(
            bounded_mul(
                bounded_add(
                    observation_bytes(profile.width, profile.height, cursor)?,
                    NATIVE_INPUT_XML_MAX_BYTES,
                )?,
                2,
            )?,
            native_host_custody_bytes()?,
        )?;
        let mlt_output = bounded_add(
            encode_output,
            bounded_add(
                bounded_add(MLT_MASTER_MAX_BYTES, bounded_mul(MASTER_MAX_BYTES, 2)?)?,
                complete_observations,
            )?,
        )?;
        // All four native MLT effects retain complete observations: two real
        // MC encodes, the semantic timeline, and the AAC master.
        let output_bytes = bounded_add(
            bounded_add(mc_output, manim_output_bytes(manim_frames)?)?,
            bounded_add(mlt_output, source_audio_bytes)?,
        )?;
        let limits = RootBudgetUnits {
            attempts,
            model_calls: 0,
            render_frames,
            output_bytes,
        };
        let width = profile.width;
        let height = profile.height;
        require(
            coordinator
                .service
                .project(project_id)
                .map_err(storage_error)?
                == project,
            "Hybrid canonical Project changed during source and resource preflight",
        )?;
        Ok(Self {
            project,
            expected: expected.clone(),
            deliverable_id,
            options: options.clone(),
            connection_identity,
            project_sha256,
            namespace,
            request_prefix,
            source_plan,
            motion,
            motion_frames,
            frame_reservation,
            manim_scene_id,
            manim_plan,
            manim_plan_sha256: source.closure.plan_sha256,
            manim_profile,
            manim_frames,
            total_frames: cursor,
            width,
            height,
            source_audio_sha256: verified.sha256,
            source_audio_bytes,
            source_audio_path: verified.path,
            limits,
        })
    }
}

fn verify_original_pcm(bytes: &[u8], sample_frames: u64) -> NativeResult<()> {
    let pcm_bytes = bounded_mul(sample_frames, 4)?;
    require(
        bytes.len() as u64 == bounded_add(44, pcm_bytes)?,
        "Hybrid original PCM byte count differs from the canonical timeline",
    )?;
    // The finite Film180 input is the original 44-byte PCM16 WAV format. No
    // conversion, padding, metadata rewriting or substituted waveform occurs.
    require(
        &bytes[0..4] == b"RIFF"
            && &bytes[8..16] == b"WAVEfmt "
            && &bytes[36..40] == b"data"
            && u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as u64 == bytes.len() as u64 - 8
            && u32::from_le_bytes(bytes[16..20].try_into().unwrap()) == 16
            && u16::from_le_bytes(bytes[20..22].try_into().unwrap()) == 1
            && u16::from_le_bytes(bytes[22..24].try_into().unwrap()) == 2
            && u32::from_le_bytes(bytes[24..28].try_into().unwrap()) == 48_000
            && u32::from_le_bytes(bytes[28..32].try_into().unwrap()) == 192_000
            && u16::from_le_bytes(bytes[32..34].try_into().unwrap()) == 4
            && u16::from_le_bytes(bytes[34..36].try_into().unwrap()) == 16
            && u32::from_le_bytes(bytes[40..44].try_into().unwrap()) as u64 == pcm_bytes,
        "Hybrid saved input is not the unchanged canonical PCM16 stereo WAV",
    )
}

impl HybridRootBudgetScope {
    fn recheck(&self, coordinator: &ProductionCoordinator, project: &Project) -> NativeResult<()> {
        require(
            coordinator.active_motion_budget.is_none(),
            "Hybrid root cannot coexist with a second native root",
        )?;
        require(
            *project == self.plan.project
                && coordinator
                    .service
                    .project(project.id)
                    .map_err(storage_error)?
                    == self.plan.project
                && coordinator.client.connection().resource == project.resource_key()
                && coordinator.client.connection().identity()? == self.plan.connection_identity,
            "Hybrid native source or original provider connection changed",
        )?;
        let current = coordinator
            .service
            .root_operation_budget(project.id, self.root.root_id)
            .map_err(storage_error)?;
        require(
            current.project_id == self.root.project_id
                && current.root_id == self.root.root_id
                && current.generation == self.root.generation
                && current.revision == self.root.revision
                && current.operation_key == self.root.operation_key
                && current.limits == self.plan.limits,
            "Hybrid continuation does not retain its original immutable accounting root",
        )
    }

    pub(super) fn require_motion_selection(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        scene_ids: &[Uuid],
    ) -> NativeResult<()> {
        self.recheck(coordinator, project)?;
        let selected = self
            .plan
            .motion
            .iter()
            .flat_map(|row| row.scene_ids.iter().copied())
            .collect::<Vec<_>>();
        require(
            matches!(&self.phase, HybridPhase::Sources)
                && deliverable_id == self.plan.deliverable_id
                && *options == self.plan.options
                && scene_ids == selected.as_slice(),
            "Hybrid MC scope differs from its original Film selection",
        )
    }

    pub(super) fn verify_motion_output(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        deliverable_id: Uuid,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<()> {
        self.recheck(coordinator, project)?;
        require(
            deliverable_id == self.plan.deliverable_id
                && rendered.project_resource == project.resource_key()
                && rendered.generation == project.generation
                && rendered.revision == project.revision
                && rendered.deliverable_id == deliverable_id
                && rendered.frame_rate == self.plan.options.frame_rate
                && rendered.segments.len() == self.plan.motion.len(),
            "Hybrid Motion Canvas outputs belong to another original source",
        )?;
        let mut jobs = std::collections::BTreeSet::new();
        for (actual, planned) in rendered.segments.iter().zip(&self.plan.motion) {
            require(
                actual.segment_id == planned.segment_id
                    && actual.scene_ids == planned.scene_ids
                    && actual.frame_count == planned.frames
                    && !actual.job_ref.is_empty()
                    && !actual.plan_ref.is_empty()
                    && !actual.fingerprint.is_empty()
                    && jobs.insert(actual.job_ref.as_str()),
                "Hybrid Motion Canvas contribution differs from its original native plan",
            )?;
            ensure_native_motion_verification(&actual.verification)?;
            let manifest = required_string(
                &actual.artifact,
                "/manifest",
                "Hybrid MC manifest is absent",
            )?;
            let sha = required_string(
                &actual.artifact,
                "/manifest_sha256",
                "Hybrid MC manifest digest is absent",
            )?;
            let _ = read_verified_production_artifact(
                &coordinator.client.connection().output_root,
                &manifest,
                &sha,
                8 * 1024 * 1024,
            )?;
        }
        Ok(())
    }

    fn verify_manim_output(
        &self,
        coordinator: &ProductionCoordinator,
        original: &Value,
    ) -> NativeResult<()> {
        require(
            original.get("renderer").and_then(Value::as_str) == Some("manim-community")
                && original.get("project_revision").and_then(Value::as_u64)
                    == Some(self.plan.expected.revision)
                && original.get("scene_id") == Some(&json!(self.plan.manim_scene_id))
                && original.get("plan_sha256").and_then(Value::as_str)
                    == Some(self.plan.manim_plan_sha256.as_str())
                && original.get("profile") == Some(&self.plan.manim_profile)
                && original
                    .pointer("/artifact/static_source_custody_error")
                    .is_none(),
            "Hybrid static Manim output differs from its canonical source",
        )?;
        let native_artifact = original
            .pointer("/result/result/data/artifact")
            .ok_or_else(|| backend("Hybrid static Manim original native artifact is absent"))?;
        let path = required_string(
            native_artifact,
            "/relative_path",
            "Hybrid Manim artifact path is absent",
        )?;
        let sha = required_string(
            native_artifact,
            "/sha256",
            "Hybrid Manim artifact digest is absent",
        )?;
        let raw = read_verified_production_artifact(
            &coordinator.client.connection().output_root,
            &path,
            &sha,
            MEDIA_MAX_BYTES,
        )?;
        require(
            native_artifact.get("bytes").and_then(Value::as_u64) == Some(raw.len() as u64),
            "Hybrid original Manim video byte count changed",
        )?;
        let plan: motionwright_manim_profile::ManimScenePlanV2 =
            serde_json::from_value(self.plan.manim_plan.clone())
                .map_err(|_| invalid("Hybrid original Manim source cannot be reopened"))?;
        let retained_source = crate::manim_static_projection::verify_rendered_manim_static_source(
            &coordinator.client.connection().output_root,
            &path,
            &plan,
            native_artifact,
        )?;
        require(
            original.pointer("/artifact/static_source") == Some(&retained_source),
            "Hybrid original static Manim source was not retained unchanged",
        )
    }

    fn stages_original_audio(&self, request_id: &str, command: &str) -> bool {
        matches!(&self.phase, HybridPhase::Sources)
            && command == "driver.motion-canvas.render.start"
            && request_id == format!("{}-mc:motion:0:render-start", self.plan.request_prefix)
    }

    pub(super) fn dispatch_budget(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        request_id: &str,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<(Uuid, RootBudgetUnits)> {
        self.recheck(coordinator, project)?;
        let mut units = self.units_for(command, args, mutation)?;
        if self.stages_original_audio(request_id, command) {
            units.output_bytes = bounded_add(units.output_bytes, self.plan.source_audio_bytes)?;
        }
        Ok((self.root.root_id, units))
    }

    pub(super) fn stage_after_admission(
        &self,
        coordinator: &ProductionCoordinator,
        request_id: &str,
        command: &str,
    ) -> NativeResult<()> {
        if !self.stages_original_audio(request_id, command) {
            return Ok(());
        }
        self.recheck(coordinator, &self.plan.project)?;
        let intent = coordinator
            .service
            .latest_production_receipt(self.plan.project.id, request_id)
            .map_err(storage_error)?
            .ok_or_else(|| backend("Hybrid input has no admitted dispatch intent"))?;
        require(
            intent.stage == "dispatching"
                && intent.command == command
                && intent.generation == self.plan.expected.generation
                && intent.revision == self.plan.expected.revision
                && intent.payload.get("mutation").and_then(Value::as_bool) == Some(true),
            "Hybrid original input cannot be staged outside its charged native start",
        )?;
        let source_parent = self
            .plan
            .source_audio_path
            .parent()
            .ok_or_else(|| backend("Hybrid original CAS parent is unavailable"))?;
        let source_name = self
            .plan
            .source_audio_path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| backend("Hybrid original CAS member is unavailable"))?;
        let bytes = read_verified_production_artifact(
            source_parent,
            source_name,
            &self.plan.source_audio_sha256,
            self.plan.source_audio_bytes,
        )?;
        require(
            bytes.len() as u64 == self.plan.source_audio_bytes,
            "Hybrid original source WAV changed before its charged staging",
        )?;
        let output = &coordinator.client.connection().output_root;
        let metadata = safe_metadata(output)?;
        require(
            metadata.is_dir(),
            "Hybrid native output root is not an admitted directory",
        )?;
        let path = output.join(format!("{}-source.wav", self.plan.namespace));
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
            options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
        }
        #[cfg(not(any(unix, windows)))]
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Hybrid original audio staging is unavailable on this platform",
        ));
        let mut file = options.open(&path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                Error::new(
                    ErrorCode::Conflict,
                    "Hybrid original WAV already exists; preserve the original attempt",
                )
            } else {
                backend("Hybrid original WAV cannot be exclusively staged")
            }
        })?;
        file.write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| {
                backend(
                    "Hybrid original WAV staging did not complete; retain its charged partial file",
                )
            })?;
        drop(file);
        self.verify_staged_audio(coordinator)?;
        // Reopen the original CAS too; this never claims continuous isolation
        // of all ancestors between finite file observations.
        require(
            read_verified_production_artifact(
                source_parent,
                source_name,
                &self.plan.source_audio_sha256,
                self.plan.source_audio_bytes,
            )? == bytes,
            "Hybrid original CAS bytes changed during native input staging",
        )
    }

    fn verify_staged_audio(&self, coordinator: &ProductionCoordinator) -> NativeResult<()> {
        let bytes = read_verified_production_artifact(
            &coordinator.client.connection().output_root,
            &format!("{}-source.wav", self.plan.namespace),
            &self.plan.source_audio_sha256,
            self.plan.source_audio_bytes,
        )?;
        require(
            bytes.len() as u64 == self.plan.source_audio_bytes,
            "Hybrid staged original WAV has a different byte count",
        )
    }

    fn units_for(
        &self,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<RootBudgetUnits> {
        match &self.phase {
            HybridPhase::Sources if MOTION_CANVAS_COMMANDS.contains(&command) => {
                // Reuse the existing MC mutation classes and output bound.
                let motion = MotionRootBudgetScope {
                    root_id: self.root.root_id,
                    max_render_frames: self.plan.motion_frames,
                    max_frame_bytes: self.plan.frame_reservation,
                    mlt_route: None,
                    mlt: None,
                    audio_analysis: false,
                };
                motion.units_for(command, args, mutation)
            }
            HybridPhase::Sources if command == "driver.manim-community.static.render.start" => {
                require(
                    mutation
                        && args
                            == &json!({"plan":self.plan.manim_plan,"profile":self.plan.manim_profile}),
                    "Hybrid Manim effect is not its preflighted original static source",
                )?;
                let mut units = one_attempt();
                units.render_frames = self.plan.manim_frames;
                units.output_bytes = manim_output_bytes(self.plan.manim_frames)?;
                Ok(units)
            }
            HybridPhase::Sources
                if matches!(
                    command,
                    "driver.manim-community.render.status" | "driver.manim-community.render.result"
                ) =>
            {
                require(
                    !mutation,
                    "Hybrid Manim observation cannot be reclassified as a mutation",
                )?;
                Ok(one_attempt())
            }
            HybridPhase::Mlt(binding) => self.mlt_units(binding, command, args, mutation),
            _ => Err(Error::new(
                ErrorCode::PermissionDenied,
                "Native command has no producer-owned hybrid continuation",
            )),
        }
    }

    fn mlt_units(
        &self,
        binding: &HybridMltBinding,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<RootBudgetUnits> {
        let mut units = one_attempt();
        let visual = format!("{}-visual.mkv", self.plan.namespace);
        match command {
            "driver.mlt-video.frames.encode" => {
                let encoded = args
                    .get("manifest_path")
                    .and_then(Value::as_str)
                    .and_then(|path| binding.encodes.get(path))
                    .ok_or_else(|| invalid("Hybrid encoder has no retained MC manifest"))?;
                require(
                    mutation
                        && args.get("root").and_then(Value::as_str) == Some("output")
                        && args.get("expected_manifest_sha256").and_then(Value::as_str)
                            == Some(encoded.manifest_sha256.as_str())
                        && args.get("output_path").and_then(Value::as_str)
                            == Some(encoded.output_path.as_str())
                        && args.get("max_bytes").and_then(Value::as_u64) == Some(encoded.max_bytes),
                    "Hybrid encoder differs from its retained real MC contribution",
                )?;
                units.render_frames = encoded.frames;
                units.output_bytes = bounded_add(
                    encoded.max_bytes,
                    bounded_add(
                        observation_bytes(self.plan.width, self.plan.height, encoded.frames)?,
                        FFV1_CUSTODY_BYTES,
                    )?,
                )?;
            }
            "driver.mlt-video.render.plan" | "driver.mlt-video.render.start" => {
                require(
                    mutation == (command == "driver.mlt-video.render.start")
                        && args.get("profile").and_then(Value::as_str)
                            == Some("lossless-video-only")
                        && args.get("output").and_then(Value::as_str) == Some(visual.as_str()),
                    "Hybrid timeline differs from its closed native output",
                )?;
                if mutation {
                    units.render_frames = self.plan.total_frames;
                    units.output_bytes = bounded_add(
                        MLT_MASTER_MAX_BYTES,
                        bounded_add(
                            observation_bytes(
                                self.plan.width,
                                self.plan.height,
                                self.plan.total_frames,
                            )?,
                            bounded_add(NATIVE_INPUT_XML_MAX_BYTES, native_host_custody_bytes()?)?,
                        )?,
                    )?;
                }
            }
            "driver.mlt-video.av.mux" => {
                require(
                    mutation
                        && args.get("video_root").and_then(Value::as_str) == Some("output")
                        && args.get("video_path").and_then(Value::as_str) == Some(visual.as_str())
                        && args.get("audio_root").and_then(Value::as_str) == Some("output")
                        && args.get("audio_path").and_then(Value::as_str)
                            == Some(format!("{}-source.wav", self.plan.namespace).as_str())
                        && args.get("audio_sha256").and_then(Value::as_str)
                            == Some(self.plan.source_audio_sha256.as_str())
                        && args.get("output_path").and_then(Value::as_str)
                            == Some(format!("{}-master.mp4", self.plan.namespace).as_str())
                        && args.get("frame_count").and_then(Value::as_u64)
                            == Some(self.plan.total_frames)
                        && args.get("width").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.width))
                        && args.get("height").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.height))
                        && args.get("fps_num").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.options.frame_rate.num))
                        && args.get("fps_den").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.options.frame_rate.den))
                        && args.get("sample_rate").and_then(Value::as_u64) == Some(48_000)
                        && args.get("channels").and_then(Value::as_u64) == Some(2)
                        && args.get("profile").and_then(Value::as_str) == Some("h264-aac-mp4")
                        && args.get("max_bytes").and_then(Value::as_u64) == Some(MASTER_MAX_BYTES),
                    "Hybrid mux differs from its original video/audio plan",
                )?;
                units.render_frames = self.plan.total_frames;
                // The native mux writes BOTH the MP4 and decoded PCM. Neither
                // product is omitted from the durable output reservation.
                units.output_bytes = bounded_add(
                    bounded_mul(MASTER_MAX_BYTES, 2)?,
                    bounded_add(
                        observation_bytes(
                            self.plan.width,
                            self.plan.height,
                            self.plan.total_frames,
                        )?,
                        NATIVE_INPUT_XML_MAX_BYTES,
                    )?,
                )?;
            }
            "driver.mlt-video.asset.import" => {
                require(
                    mutation
                        && args.get("kind").and_then(Value::as_str) == Some("file")
                        && args.get("root").and_then(Value::as_str) == Some("output")
                        && args
                            .get("path")
                            .and_then(Value::as_str)
                            .is_some_and(|path| {
                                binding.source_paths.iter().any(|retained| retained == path)
                            }),
                    "Hybrid timeline cannot import an unretained contribution",
                )?;
            }
            "driver.mlt-video.project.create"
            | "driver.mlt-video.sequence.create"
            | "driver.mlt-video.track.create"
            | "driver.mlt-video.clip.insert" => {
                require(
                    mutation,
                    "Hybrid semantic edit requires its original mutation class",
                )?;
            }
            "driver.mlt-video.sequence.list"
            | "driver.mlt-video.track.list"
            | "driver.mlt-video.sequence.duration"
            | "driver.mlt-video.render.status"
            | "driver.mlt-video.render.result" => {
                require(
                    !mutation,
                    "Hybrid observation has a different mutation class",
                )?;
            }
            _ => {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Native command is outside the closed hybrid timeline continuation",
                ));
            }
        }
        Ok(units)
    }
}

impl ProductionCoordinator {
    pub(super) fn with_native_hybrid_scope(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<Self> {
        require(
            self.active_motion_budget.is_none() && self.active_hybrid_budget.is_none(),
            "Hybrid production cannot replace a retained native root",
        )?;
        let plan = HybridBudgetPlan::build(self, project_id, expected, deliverable_id, options)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            &format!("{}-mc", plan.request_prefix),
            SceneRequestFamily::Motion,
        )?;
        self.require_no_unresolved_scene_requests(
            project_id,
            &format!("{}-manim", plan.request_prefix),
            SceneRequestFamily::Manim,
        )?;
        let root = self
            .service
            .create_root_operation_budget(RootOperationBudgetInput {
                project_id,
                generation: expected.generation,
                revision: expected.revision,
                operation_key: operation_key(&plan.project, deliverable_id),
                limits: plan.limits,
            })
            .map_err(storage_error)?;
        let mut scoped = self.clone();
        scoped.active_hybrid_budget = Some(HybridRootBudgetScope {
            root,
            plan: Arc::new(plan),
            phase: HybridPhase::Sources,
        });
        Ok(scoped)
    }

    /// Produce the admitted mixed native originals under one immutable root.
    /// Unsupported selections fail before any renderer is dispatched. A failed
    /// or UNKNOWN original is not retried under a new label or fresh budget.
    pub async fn realize_native_hybrid_sources(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<HybridNativeSources> {
        let scoped =
            self.with_native_hybrid_scope(project_id, expected, deliverable_id, options)?;
        let scope = scoped
            .active_hybrid_budget
            .as_ref()
            .ok_or_else(|| backend("Hybrid producer lost its original root"))?
            .clone();
        let motion_request = format!("{}-mc", scope.plan.request_prefix);
        let manim_request = format!("{}-manim", scope.plan.request_prefix);
        let motion_canvas = scoped
            .render_motion_canvas_segments(
                project_id,
                expected,
                &motion_request,
                deliverable_id,
                options,
            )
            .await?;
        let manim = scoped
            .realize_manim_static_scene(
                project_id,
                expected,
                &manim_request,
                scope.plan.manim_scene_id,
                deliverable_id,
            )
            .await?;
        scope.verify_motion_output(&scoped, &scope.plan.project, deliverable_id, &motion_canvas)?;
        scope.verify_manim_output(&scoped, &manim)?;
        scope.verify_staged_audio(&scoped)?;
        scope.recheck(&scoped, &scope.plan.project)?;
        let evidence = json!({"plan":scope.plan.source_plan,"motion_canvas":motion_canvas,
            "manim":[{"original_native_result":manim}]});
        Ok(HybridNativeSources {
            project: scope.plan.project.clone(),
            expected: expected.clone(),
            deliverable_id,
            options: options.clone(),
            project_sha256: scope.plan.project_sha256.clone(),
            namespace: scope.plan.namespace.clone(),
            request_prefix: scope.plan.request_prefix.clone(),
            plan: scope.plan.source_plan.clone(),
            motion_canvas,
            manim,
            evidence,
            owner: self.clone(),
            scope,
        })
    }

    // Only sibling production modules can borrow this private continuation.
    // A public source-evidence type is not an arbitrary execute capability.
    pub(super) fn hybrid_mlt_scope(&self, sources: &HybridNativeSources) -> NativeResult<Self> {
        require(
            Arc::ptr_eq(
                &self.motion_budget_roots,
                &sources.owner.motion_budget_roots,
            ),
            "Hybrid continuation belongs to another coordinator or Store owner",
        )?;
        sources.scope.recheck(self, &sources.project)?;
        require(
            sources.expected == sources.scope.plan.expected
                && sources.deliverable_id == sources.scope.plan.deliverable_id
                && sources.options == sources.scope.plan.options
                && sources.project_sha256 == sources.scope.plan.project_sha256
                && sources.namespace == sources.scope.plan.namespace
                && sources.request_prefix == sources.scope.plan.request_prefix
                && sources.plan == sources.scope.plan.source_plan,
            "Hybrid source selection differs from its original retained plan",
        )?;
        sources.scope.verify_motion_output(
            self,
            &sources.project,
            sources.deliverable_id,
            &sources.motion_canvas,
        )?;
        sources.scope.verify_manim_output(self, &sources.manim)?;
        sources.scope.verify_staged_audio(self)?;
        let mut encodes = BTreeMap::new();
        let mut source_paths = Vec::new();
        for (actual, planned) in sources
            .motion_canvas
            .segments
            .iter()
            .zip(&sources.scope.plan.motion)
        {
            let manifest = required_string(
                &actual.artifact,
                "/manifest",
                "Hybrid MC original manifest is absent",
            )?;
            let manifest_sha256 = required_string(
                &actual.artifact,
                "/manifest_sha256",
                "Hybrid MC original manifest digest is absent",
            )?;
            let output_path = format!("{}-mc-{}.mkv", sources.namespace, planned.output_ordinal);
            source_paths.push(output_path.clone());
            require(
                encodes
                    .insert(
                        manifest,
                        MltEncodeBudget {
                            manifest_sha256,
                            frames: planned.frames,
                            output_path,
                            max_bytes: MEDIA_MAX_BYTES,
                        },
                    )
                    .is_none(),
                "Hybrid MC contributions cannot share a substituted original manifest",
            )?;
        }
        source_paths.push(required_string(
            &sources.manim,
            "/artifact/relative_path",
            "Hybrid original Manim video is absent",
        )?);
        let mut scope = sources.scope.clone();
        scope.phase = HybridPhase::Mlt(HybridMltBinding {
            encodes,
            source_paths,
        });
        let mut scoped = self.clone();
        scoped.active_hybrid_budget = Some(scope);
        // Declared boundaries were refused before first effect. Do not pass an
        // MC-only typed selection as a purported proof for the Manim clip.
        scoped.scene_boundary_scope = None;
        Ok(scoped)
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    include!("hybrid_budget_tests.rs");
}
