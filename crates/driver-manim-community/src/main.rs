use async_trait::async_trait;
use motionwright_manim_profile::{
    ManimRenderProfile, ManimScenePlan, SCENE_CLASS, compile_manim_python,
    planar_camera::{
        ManimCameraScenePlanV1, PreparedCameraProgramV1, compile_camera_program_source_only,
    },
    validate_plan,
};
use semwright_driver_sdk::{
    Capability, Driver, DriverExecutionContext, DriverInterfaces, RuntimeToolArg, RuntimeToolCwd,
    RuntimeToolJob, RuntimeToolJobStatus, descriptor_digest, serve, workspace_mount,
};
use semwright_types::{CommandDescriptor, Error, ErrorCode, Idempotency, Result, Risk};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    time::Duration,
};
use uuid::Uuid;

const DRIVER_ID: &str = "manim-community";
const DRIVER_SCOPE: &str = "driver:manim-community";
const MANIM_VERSION: &str = "0.21.0";
const WORK_MOUNT: &str = "manim-work";
const OUTPUT_MOUNT: &str = "manim-output";
const RUNTIME_MOUNT: &str = "manim-runtime";
const FONTCONFIG_MOUNT: &str = "fontconfig";
const MANIM_TOOL: &str = "manim-runner";
const PYTHON_TOOL: &str = "python3";
const FFMPEG_TOOL: &str = "ffmpeg";
const MAX_JOBS: usize = 32;
const MAX_SOURCE_BYTES: usize = 256 * 1024;
const MAX_VIDEO_BYTES: u64 = 512 * 1024 * 1024;
const RENDER_TIMEOUT: Duration = Duration::from_secs(300);

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StartArgs {
    plan: ManimScenePlan,
    profile: ManimRenderProfile,
}

/// This is a separate typed, discoverable effect. Imported Python and ZIP
/// content are never accepted in place of this original canonical plan.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CameraStartArgs {
    plan: ManimCameraScenePlanV1,
    profile: ManimRenderProfile,
}

/// Closed source generation, not a grant or a render receipt. The caller must
/// subsequently use the original DriverExecutionContext and sealed runner.
fn prepare_camera_for_original_host(args: &CameraStartArgs) -> Result<PreparedCameraProgramV1> {
    args.plan
        .validate_source_only()
        .map_err(map_profile_error)?;
    args.profile.validate().map_err(map_profile_error)?;
    if args.plan.render_profile != args.profile {
        return Err(invalid(
            "Manim camera original plan and requested output profile differ",
        ));
    }
    let program = compile_camera_program_source_only(&args.plan).map_err(map_profile_error)?;
    if program.native_execution_performed || program.runtime_sample_pts_verified {
        return Err(invalid(
            "Manim camera source-only compiler cannot claim native execution",
        ));
    }
    if program.python_source.len() > MAX_SOURCE_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Generated Manim camera source exceeds the bounded source budget",
        ));
    }
    Ok(program)
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct JobArgs {
    job_ref: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(deny_unknown_fields)]
struct RenderArtifact {
    relative_path: String,
    sha256: String,
    bytes: u64,
    media_type: &'static str,
    width: u32,
    height: u32,
    frame_rate: u32,
    plan_sha256: String,
    /// Only camera jobs publish a private, checked native getter digest.
    /// A public MP4 alone never proves that all camera poses were applied.
    #[serde(skip_serializing_if = "Option::is_none")]
    camera_getters_sha256: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum JobPhase {
    Rendering,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
}

impl JobPhase {
    fn as_str(self) -> &'static str {
        match self {
            Self::Rendering => "rendering",
            Self::Cancelling => "cancelling",
            Self::Succeeded => "succeeded",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    fn terminal(self) -> bool {
        matches!(self, Self::Succeeded | Self::Failed | Self::Cancelled)
    }
}

#[derive(Debug, Clone)]
struct RenderJob {
    host_job: RuntimeToolJob,
    phase: JobPhase,
    source_file: String,
    output_relative: String,
    plan_sha256: String,
    profile: ManimRenderProfile,
    artifact: Option<RenderArtifact>,
    diagnostic: Option<String>,
    /// Kept only in the original Driver Host's bounded job lifetime. Imported
    /// source/ZIP cannot assert this typed source identity.
    camera_plan: Option<ManimCameraScenePlanV1>,
}

struct ManimCommunityDriver {
    work_root: PathBuf,
    output_root: PathBuf,
    jobs: BTreeMap<String, RenderJob>,
    capabilities: Vec<Capability>,
}

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn backend(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::BackendFailed, message)
}

fn map_profile_error(error: motionwright_manim_profile::ManimProfileError) -> Error {
    invalid(error.to_string())
}

fn canonical_root(name: &str) -> Result<PathBuf> {
    let root = workspace_mount(name)?;
    let metadata = fs::symlink_metadata(&root)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(invalid(format!(
            "{name} must be a real owner-granted directory"
        )));
    }
    fs::canonicalize(root).map_err(Into::into)
}

fn plan_digest(plan: &ManimScenePlan) -> Result<String> {
    let bytes = serde_json::to_vec(plan)?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

/// Validate the original trusted Manim process's private getter sidecar.
/// This is an input to independent FFmpeg frame/PTS confirmation, NEVER
/// interchangeable with final video fidelity, user authorization or rights.
fn verify_camera_getters_bytes(
    raw: &[u8],
    plan: &ManimCameraScenePlanV1,
    expected_plan_sha256: &str,
) -> Result<String> {
    if raw.is_empty() || raw.len() > 8 * 1024 * 1024 {
        return Err(invalid(
            "Manim camera getter envelope exceeds the admitted size",
        ));
    }
    let v: Value = serde_json::from_slice(raw)
        .map_err(|_| invalid("Manim camera getter envelope is malformed"))?;
    let samples = plan.expected_frame_samples().map_err(map_profile_error)?;
    let count = samples.len();
    let correct_header = v.get("schema_version").and_then(Value::as_u64) == Some(1)
        && v.get("profile_id").and_then(Value::as_str) == Some("mw-manim-planar-camera-v1")
        && v.get("scene_class").and_then(Value::as_str) == Some(SCENE_CLASS)
        && v.get("plan_sha256").and_then(Value::as_str) == Some(expected_plan_sha256)
        && v.get("sample_count").and_then(Value::as_u64) == Some(count as u64)
        && v.get("decoded_video_verified") == Some(&json!(false))
        && v.get("native_grants_verified_by_this_script") == Some(&json!(false))
        && v.get("binding") == Some(&serde_json::to_value(&plan.binding)?);
    if !correct_header {
        return Err(invalid(
            "Manim getter source/binding or profile is not exact",
        ));
    }
    let alphas = v
        .get("callback_alphas")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("Manim callback alpha list is unavailable"))?;
    let frames = v
        .get("frames")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("Manim native camera frame getter list is unavailable"))?;
    if alphas.len() != count || frames.len() != count {
        return Err(invalid("Manim native camera getter frame count differs"));
    }

    let close = |actual: Option<f64>, expected: f64| -> bool {
        actual.is_some_and(|x| x.is_finite() && (x - expected).abs() <= 1e-5)
    };
    let point = |value: &Value, target: [f64; 3]| -> bool {
        value.as_array().is_some_and(|items| {
            items.len() == 3 && (0..3).all(|index| close(items[index].as_f64(), target[index]))
        })
    };

    for (i, frame) in frames.iter().enumerate() {
        if frame.get("index").and_then(Value::as_u64) != Some(i as u64)
            || !close(alphas[i].as_f64(), i as f64 / (count as f64 - 0.5))
        {
            return Err(invalid(
                "Manim callback order or original sample PTS changed",
            ));
        }
        let cam = frame
            .get("camera")
            .ok_or_else(|| invalid("Manim physical camera getter row is missing"))?;
        if !point(&cam["frame_center"], samples[i].world_frame_center)
            || !close(cam["frame_width"].as_f64(), 128.0 / 9.0)
            || !close(cam["frame_height"].as_f64(), 8.0)
            || cam["pixel_width"].as_u64() != Some(640)
            || cam["pixel_height"].as_u64() != Some(360)
            || !close(cam["frame_rate"].as_f64(), 30.0)
        {
            return Err(invalid(
                "Manim native camera frame pose or dimensions differ",
            ));
        }
        let objects = frame
            .get("objects")
            .and_then(Value::as_array)
            .ok_or_else(|| invalid("Manim physical shape getters are missing"))?;
        if objects.len() != plan.nodes.len() {
            return Err(invalid("Manim getter lost an authored source object"));
        }
        for source in &plan.nodes {
            let id = source.node_id.to_string();
            let matches: Vec<_> = objects
                .iter()
                .filter(|obj| obj.get("node_id").and_then(Value::as_str) == Some(id.as_str()))
                .collect();
            if matches.len() != 1 {
                return Err(invalid("Manim original shape getter identity changed"));
            }
            let obj = matches[0];
            let center = [
                (source.x_px + source.width_px / 2.0 - 960.0) / 135.0,
                (540.0 - source.y_px - source.height_px / 2.0) / 135.0,
                0.0,
            ];
            let source_kind = match source.source_kind {
                motionwright_manim_profile::ManimSourceShapeKindV2::Rectangle => "rectangle",
                motionwright_manim_profile::ManimSourceShapeKindV2::Circle => "circle",
                motionwright_manim_profile::ManimSourceShapeKindV2::Shape => {
                    return Err(invalid("Manim source getter has an unsupported shape"));
                }
            };
            let expected_name = format!("mw_{}", source.node_id.simple());
            if obj["kind"].as_str() != Some(source_kind)
                || obj["name"].as_str() != Some(expected_name.as_str())
                || !point(&obj["world_center"], center)
                || !close(obj["width"].as_f64(), source.width_px / 135.0)
                || !close(obj["height"].as_f64(), source.height_px / 135.0)
                || obj["z_index"].as_i64() != Some(i64::from(source.z_index))
                || obj["fill_hex"]
                    .as_str()
                    .is_none_or(|c| !c.eq_ignore_ascii_case(source.fill_hex.as_str()))
                || !close(obj["fill_opacity"].as_f64(), 1.0)
                || !close(obj["stroke_width"].as_f64(), 0.0)
                || !close(obj["stroke_opacity"].as_f64(), 0.0)
            {
                return Err(invalid(
                    "Manim native shape content differs from original source",
                ));
            }
        }
    }
    Ok(hex::encode(Sha256::digest(raw)))
}

/// Private work-root getter evidence is generated by the trusted Manim
/// process, not provided by the request. Public publisher cannot turn a
/// forged .camera.json or merely valid MP4 into a successful native job.
fn verified_camera_getters_from_work_root(
    work_root: &Path,
    source_file: &str,
    plan: &ManimCameraScenePlanV1,
    expected_plan_sha256: &str,
) -> Result<String> {
    let source = Path::new(source_file);
    if source.components().count() != 1 || source.extension().and_then(|e| e.to_str()) != Some("py")
    {
        return Err(invalid("Manim source filename is not a sealed component"));
    }
    let path = work_root.join(source.with_extension("camera.json"));
    let metadata = fs::symlink_metadata(&path)
        .map_err(|_| invalid("Native Manim camera getter artifact is absent"))?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() == 0
        || metadata.len() > 8 * 1024 * 1024
    {
        return Err(invalid(
            "Manim camera getter file is not bounded and regular",
        ));
    }
    let canonical = fs::canonicalize(&path)
        .map_err(|_| invalid("Manim camera getter source could not be canonicalized"))?;
    if !canonical.starts_with(work_root) {
        return Err(invalid(
            "Manim camera getter file escaped original work root",
        ));
    }
    let bytes = fs::read(&canonical)?;
    if bytes.len() as u64 != metadata.len() {
        return Err(invalid("Manim camera getter file changed while reading"));
    }
    verify_camera_getters_bytes(&bytes, plan, expected_plan_sha256)
}

fn strict_job_ref(value: &str) -> Result<()> {
    if value.len() != 38
        || !value.starts_with("manim-")
        || !value[6..].bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err(invalid("Manim job reference is malformed"));
    }
    Ok(())
}

fn job_view(job_ref: &str, job: &RenderJob) -> Value {
    json!({
        "job_ref": job_ref,
        "state": job.phase.as_str(),
        "source_file": job.source_file,
        "plan_sha256": job.plan_sha256,
        "profile": job.profile,
        "artifact": job.artifact,
        "diagnostic": job.diagnostic,
    })
}

fn safe_output_file(root: &Path, relative: &str) -> Result<(PathBuf, u64)> {
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(invalid("Manim artifact path is not normalized"));
    }
    let mut cursor = root.to_path_buf();
    for component in path.components() {
        let Component::Normal(component) = component else {
            return Err(invalid("Manim artifact path is not normalized"));
        };
        cursor.push(component);
        let metadata = fs::symlink_metadata(&cursor)?;
        if metadata.file_type().is_symlink() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Manim artifact path traverses a symlink",
            ));
        }
    }
    let canonical = fs::canonicalize(&cursor)?;
    if !canonical.starts_with(root) {
        return Err(Error::new(
            ErrorCode::PermissionDenied,
            "Manim artifact escaped the owner output root",
        ));
    }
    let metadata = fs::metadata(&canonical)?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_VIDEO_BYTES {
        return Err(invalid(
            "Manim artifact is not a bounded regular video file",
        ));
    }
    Ok((canonical, metadata.len()))
}

fn sha256_file(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut bytes = [0_u8; 128 * 1024];
    loop {
        let count = file.read(&mut bytes)?;
        if count == 0 {
            break;
        }
        hasher.update(&bytes[..count]);
    }
    Ok(hex::encode(hasher.finalize()))
}

impl ManimCommunityDriver {
    fn production() -> Result<Self> {
        Ok(Self {
            work_root: canonical_root(WORK_MOUNT)?,
            output_root: canonical_root(OUTPUT_MOUNT)?,
            jobs: BTreeMap::new(),
            capabilities: capability_catalog()?,
        })
    }

    fn verify_descriptor(&self, command: &str, digest: &str) -> Result<()> {
        let capability = self
            .capabilities
            .iter()
            .find(|capability| capability.descriptor.name == command)
            .ok_or_else(|| Error::new(ErrorCode::NotFound, "Manim capability is absent"))?;
        if descriptor_digest(&capability.descriptor)? != digest {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Manim capability descriptor changed",
            ));
        }
        Ok(())
    }

    fn doctor(&self) -> Value {
        json!({
            "driver": DRIVER_ID,
            "driver_version": env!("CARGO_PKG_VERSION"),
            "runtime": "Manim Community",
            "runtime_version_contract": MANIM_VERSION,
            "host_tools": std::env::var_os("SEMWRIGHT_DRIVER_HOST_TOOLS").is_some(),
            "arbitrary_python": false,
            "network": false,
            "max_jobs": MAX_JOBS,
        })
    }

    async fn start(&mut self, args: Value, context: &DriverExecutionContext) -> Result<Value> {
        context.check_cancelled()?;
        let args: StartArgs = serde_json::from_value(args)?;
        validate_plan(&args.plan).map_err(map_profile_error)?;
        args.profile.validate().map_err(map_profile_error)?;
        let source = compile_manim_python(&args.plan).map_err(map_profile_error)?;
        let plan_sha256 = plan_digest(&args.plan)?;
        self.start_prepared_source(source, plan_sha256, args.profile, None, context)
            .await
    }

    /// The separate camera command shares the exact same Semwright Host
    /// admission, cancellation, job lifetime, sealed Manim runner and MP4
    /// custody as the original static command. It does not execute a saved
    /// scene.py from imported or user-controlled material.
    async fn start_camera(
        &mut self,
        args: Value,
        context: &DriverExecutionContext,
    ) -> Result<Value> {
        context.check_cancelled()?;
        let args: CameraStartArgs = serde_json::from_value(args)?;
        let program = prepare_camera_for_original_host(&args)?;
        self.start_prepared_source(
            program.python_source,
            program.plan_sha256,
            args.profile,
            Some(args.plan),
            context,
        )
        .await
    }

    async fn start_prepared_source(
        &mut self,
        source: String,
        plan_sha256: String,
        profile: ManimRenderProfile,
        camera_plan: Option<ManimCameraScenePlanV1>,
        context: &DriverExecutionContext,
    ) -> Result<Value> {
        context.check_cancelled()?;
        if self.jobs.len() >= MAX_JOBS {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Manim retained-job budget is full",
            ));
        }
        if source.len() > MAX_SOURCE_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Generated Manim source exceeds the bounded source budget",
            ));
        }
        let token = Uuid::now_v7().simple().to_string();
        let job_ref = format!("manim-{token}");
        let source_file = format!("mw_{token}.py");
        let output_name = format!("mw_{token}");
        let output_relative = format!(
            "videos/mw_{token}/{}/{}.mp4",
            profile.quality_directory(),
            output_name
        );

        let source_path = self.work_root.join(&source_file);
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&source_path)?;
        file.write_all(source.as_bytes())?;
        file.sync_all()?;

        let literal = |value: String| RuntimeToolArg::Literal { value };
        let host_job = context
            .start_runtime_tool_job_args(
                MANIM_TOOL,
                vec![
                    literal("render".into()),
                    literal("--runtime-root".into()),
                    RuntimeToolArg::MountPath {
                        mount: RUNTIME_MOUNT.into(),
                        relative: String::new(),
                    },
                    literal("--python-sealed".into()),
                    RuntimeToolArg::ToolPath {
                        tool: PYTHON_TOOL.into(),
                    },
                    literal("--ffmpeg-sealed".into()),
                    RuntimeToolArg::ToolPath {
                        tool: FFMPEG_TOOL.into(),
                    },
                    literal("--fontconfig-root".into()),
                    RuntimeToolArg::MountPath {
                        mount: FONTCONFIG_MOUNT.into(),
                        relative: String::new(),
                    },
                    literal("--work-root".into()),
                    RuntimeToolArg::MountPath {
                        mount: WORK_MOUNT.into(),
                        relative: String::new(),
                    },
                    literal("--output-root".into()),
                    RuntimeToolArg::MountPath {
                        mount: OUTPUT_MOUNT.into(),
                        relative: String::new(),
                    },
                    literal("--source".into()),
                    literal(source_file.clone()),
                    literal("--scene".into()),
                    literal(SCENE_CLASS.into()),
                    literal("--output-name".into()),
                    literal(output_name),
                    literal("--width".into()),
                    literal(profile.width.to_string()),
                    literal("--height".into()),
                    literal(profile.height.to_string()),
                    literal("--fps".into()),
                    literal(profile.frame_rate.to_string()),
                ],
                Vec::new(),
                RENDER_TIMEOUT,
                Some(RuntimeToolCwd {
                    mount: WORK_MOUNT.into(),
                    relative: String::new(),
                }),
            )
            .await?;

        self.jobs.insert(
            job_ref.clone(),
            RenderJob {
                host_job,
                phase: JobPhase::Rendering,
                source_file,
                output_relative,
                plan_sha256,
                profile,
                artifact: None,
                diagnostic: None,
                camera_plan,
            },
        );
        Ok(job_view(
            &job_ref,
            self.jobs.get(&job_ref).expect("inserted job"),
        ))
    }

    async fn refresh(&mut self, job_ref: &str, context: &DriverExecutionContext) -> Result<Value> {
        strict_job_ref(job_ref)?;
        let snapshot = self
            .jobs
            .get(job_ref)
            .cloned()
            .ok_or_else(|| Error::new(ErrorCode::NotFound, "Manim job is absent"))?;
        if snapshot.phase.terminal() {
            return Ok(job_view(job_ref, &snapshot));
        }

        let status = context.runtime_tool_job_status(&snapshot.host_job).await?;
        let job = self
            .jobs
            .get_mut(job_ref)
            .ok_or_else(|| Error::new(ErrorCode::StaleReference, "Manim job disappeared"))?;
        match status {
            RuntimeToolJobStatus::Running => job.phase = JobPhase::Rendering,
            RuntimeToolJobStatus::Cancelling => job.phase = JobPhase::Cancelling,
            RuntimeToolJobStatus::Cancelled => job.phase = JobPhase::Cancelled,
            RuntimeToolJobStatus::Failed { error } => {
                job.phase = JobPhase::Failed;
                job.diagnostic = Some(format!("{:?}: {}", error.code, error.message));
            }
            RuntimeToolJobStatus::Succeeded { output } => {
                if output.exit_code != 0 {
                    job.phase = JobPhase::Failed;
                    job.diagnostic = Some(format!(
                        "Manim {} exited with code {} ({} stderr bytes)",
                        MANIM_VERSION,
                        output.exit_code,
                        output.stderr.len()
                    ));
                } else {
                    let camera_getters_sha256 = if let Some(plan) = &job.camera_plan {
                        match verified_camera_getters_from_work_root(
                            &self.work_root,
                            &job.source_file,
                            plan,
                            &job.plan_sha256,
                        ) {
                            Ok(digest) => Some(digest),
                            Err(error) => {
                                job.phase = JobPhase::Failed;
                                job.diagnostic = Some(format!(
                                    "Original Manim camera native getter proof missing or invalid: {error}"
                                ));
                                return Ok(job_view(job_ref, job));
                            }
                        }
                    } else {
                        None
                    };
                    let (path, bytes) = safe_output_file(&self.output_root, &job.output_relative)?;
                    let sha256 = sha256_file(&path)?;
                    job.artifact = Some(RenderArtifact {
                        relative_path: job.output_relative.clone(),
                        sha256,
                        bytes,
                        media_type: "video/mp4",
                        width: job.profile.width,
                        height: job.profile.height,
                        frame_rate: job.profile.frame_rate,
                        plan_sha256: job.plan_sha256.clone(),
                        camera_getters_sha256,
                    });
                    job.phase = JobPhase::Succeeded;
                }
            }
        }
        Ok(job_view(job_ref, job))
    }

    async fn cancel(&mut self, job_ref: &str, context: &DriverExecutionContext) -> Result<Value> {
        strict_job_ref(job_ref)?;
        let snapshot = self
            .jobs
            .get(job_ref)
            .cloned()
            .ok_or_else(|| Error::new(ErrorCode::NotFound, "Manim job is absent"))?;
        if snapshot.phase.terminal() {
            return Ok(job_view(job_ref, &snapshot));
        }
        let state = context.cancel_runtime_tool_job(&snapshot.host_job).await?;
        let job = self
            .jobs
            .get_mut(job_ref)
            .ok_or_else(|| Error::new(ErrorCode::StaleReference, "Manim job disappeared"))?;
        job.phase = match state {
            RuntimeToolJobStatus::Running => JobPhase::Rendering,
            RuntimeToolJobStatus::Cancelling => JobPhase::Cancelling,
            RuntimeToolJobStatus::Cancelled => JobPhase::Cancelled,
            RuntimeToolJobStatus::Succeeded { .. } => JobPhase::Rendering,
            RuntimeToolJobStatus::Failed { error } => {
                job.diagnostic = Some(format!("{:?}: {}", error.code, error.message));
                JobPhase::Failed
            }
        };
        Ok(job_view(job_ref, job))
    }

    async fn result(&mut self, job_ref: &str, context: &DriverExecutionContext) -> Result<Value> {
        let view = self.refresh(job_ref, context).await?;
        let job = self
            .jobs
            .get(job_ref)
            .ok_or_else(|| Error::new(ErrorCode::NotFound, "Manim job is absent"))?;
        match job.phase {
            JobPhase::Succeeded => Ok(view),
            JobPhase::Failed => Err(backend("Manim render failed; inspect status diagnostics")),
            JobPhase::Cancelled => Err(Error::new(
                ErrorCode::Cancelled,
                "Manim render was cancelled",
            )),
            JobPhase::Rendering | JobPhase::Cancelling => Err(Error::new(
                ErrorCode::Conflict,
                "Manim render is not terminal",
            )),
        }
    }

    async fn execute_with_runtime(
        &mut self,
        command: &str,
        args: Value,
        context: &DriverExecutionContext,
    ) -> Result<Value> {
        match command {
            "driver.manim-community.render.start" => self.start(args, context).await,
            "driver.manim-community.camera.render.start" => self.start_camera(args, context).await,
            "driver.manim-community.render.status" => {
                let args: JobArgs = serde_json::from_value(args)?;
                self.refresh(&args.job_ref, context).await
            }
            "driver.manim-community.render.cancel" => {
                let args: JobArgs = serde_json::from_value(args)?;
                self.cancel(&args.job_ref, context).await
            }
            "driver.manim-community.render.result" => {
                let args: JobArgs = serde_json::from_value(args)?;
                self.result(&args.job_ref, context).await
            }
            _ => Err(Error::new(
                ErrorCode::NotFound,
                "Manim capability is absent",
            )),
        }
    }
}

#[async_trait]
impl Driver for ManimCommunityDriver {
    fn id(&self) -> &str {
        DRIVER_ID
    }

    fn version(&self) -> &str {
        env!("CARGO_PKG_VERSION")
    }

    fn interfaces(&self) -> DriverInterfaces {
        DriverInterfaces {
            cooperative_cancellation: true,
            host_tools: std::env::var_os("SEMWRIGHT_DRIVER_HOST_TOOLS").is_some(),
            health: true,
            ..Default::default()
        }
    }

    async fn capabilities(&mut self) -> Result<Vec<Capability>> {
        Ok(self.capabilities.clone())
    }

    async fn execute(
        &mut self,
        command: &str,
        descriptor_sha256: &str,
        _args: Value,
    ) -> Result<Value> {
        self.verify_descriptor(command, descriptor_sha256)?;
        if command == "driver.manim-community.doctor" {
            return Ok(self.doctor());
        }
        Err(Error::new(
            ErrorCode::Unsupported,
            "Manim runtime operations require Driver Host execution context",
        ))
    }

    async fn execute_with_context(
        &mut self,
        command: &str,
        descriptor_sha256: &str,
        args: Value,
        context: DriverExecutionContext,
    ) -> Result<Value> {
        self.verify_descriptor(command, descriptor_sha256)?;
        context.check_cancelled()?;
        if command == "driver.manim-community.doctor" {
            return Ok(self.doctor());
        }
        self.execute_with_runtime(command, args, &context).await
    }

    async fn health(&mut self) -> Result<Value> {
        Ok(json!({
            "healthy": self.work_root.is_dir() && self.output_root.is_dir(),
            "runtime_version_contract": MANIM_VERSION,
            "host_tools": std::env::var_os("SEMWRIGHT_DRIVER_HOST_TOOLS").is_some(),
        }))
    }
}

struct CapabilitySpec<'a> {
    name: &'a str,
    description: &'a str,
    schemas: (Value, Value),
    risk: Risk,
    idempotency: Idempotency,
    dry_run: bool,
    tags: &'a [&'a str],
}

fn capability(spec: CapabilitySpec<'_>) -> Capability {
    let (input_schema, output_schema) = spec.schemas;
    Capability {
        descriptor: CommandDescriptor {
            name: spec.name.into(),
            version: "1".into(),
            description: spec.description.into(),
            input_schema,
            output_schema,
            requires: vec![DRIVER_SCOPE.into()],
            risk: spec.risk,
            idempotency: spec.idempotency,
            timeout_ms: 300_000,
            dry_run: spec.dry_run,
            interactive_consent: false,
            backends: vec![DRIVER_SCOPE.into()],
        },
        aliases: vec![],
        tags: spec.tags.iter().map(|value| (*value).into()).collect(),
        object_types: vec!["manim-scene".into()],
    }
}

fn job_schema() -> Value {
    json!({
        "type":"object",
        "properties":{"job_ref":{"type":"string","pattern":"^manim-[0-9a-f]{32}$"}},
        "required":["job_ref"],
        "additionalProperties":false
    })
}

fn job_output_schema() -> Value {
    json!({
        "type":"object",
        "properties":{
            "job_ref":{"type":"string"},
            "state":{"enum":["rendering","cancelling","succeeded","failed","cancelled"]},
            "source_file":{"type":"string"},
            "plan_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
            "profile":{"type":"object"},
            "artifact":{"type":["object","null"]},
            "diagnostic":{"type":["string","null"]}
        },
        "required":["job_ref","state","source_file","plan_sha256","profile","artifact","diagnostic"],
        "additionalProperties":false
    })
}

fn start_schema() -> Value {
    let primitive_common = json!({
        "node_id":{"type":"string","format":"uuid"},
        "x":{"type":"number"},
        "y":{"type":"number"},
        "color":{"type":"string","pattern":"^#[0-9A-Fa-f]{6}$"}
    });
    json!({
        "type":"object",
        "properties":{
            "plan":{
                "type":"object",
                "properties":{
                    "project_id":{"type":"string","format":"uuid"},
                    "generation":{"type":"string","format":"uuid"},
                    "revision":{"type":"integer","minimum":0},
                    "scene_id":{"type":"string","format":"uuid"},
                    "primitives":{
                        "type":"array",
                        "minItems":1,
                        "maxItems":512,
                        "items":{
                            "oneOf":[
                                {
                                    "type":"object",
                                    "properties":{
                                        "kind":{"const":"text"},
                                        "node_id":primitive_common["node_id"].clone(),
                                        "text":{"type":"string","minLength":1,"maxLength":16384},
                                        "x":primitive_common["x"].clone(),
                                        "y":primitive_common["y"].clone(),
                                        "font_size":{"type":"number","minimum":4,"maximum":512},
                                        "color":primitive_common["color"].clone()
                                    },
                                    "required":["kind","node_id","text","x","y","font_size","color"],
                                    "additionalProperties":false
                                },
                                {
                                    "type":"object",
                                    "properties":{
                                        "kind":{"const":"rectangle"},
                                        "node_id":primitive_common["node_id"].clone(),
                                        "x":primitive_common["x"].clone(),
                                        "y":primitive_common["y"].clone(),
                                        "width":{"type":"number","exclusiveMinimum":0,"maximum":128},
                                        "height":{"type":"number","exclusiveMinimum":0,"maximum":128},
                                        "color":primitive_common["color"].clone()
                                    },
                                    "required":["kind","node_id","x","y","width","height","color"],
                                    "additionalProperties":false
                                },
                                {
                                    "type":"object",
                                    "properties":{
                                        "kind":{"const":"circle"},
                                        "node_id":primitive_common["node_id"].clone(),
                                        "x":primitive_common["x"].clone(),
                                        "y":primitive_common["y"].clone(),
                                        "radius":{"type":"number","exclusiveMinimum":0,"maximum":128},
                                        "color":primitive_common["color"].clone()
                                    },
                                    "required":["kind","node_id","x","y","radius","color"],
                                    "additionalProperties":false
                                }
                            ]
                        }
                    }
                },
                "required":["project_id","generation","revision","scene_id","primitives"],
                "additionalProperties":false
            },
            "profile":{
                "type":"object",
                "properties":{
                    "width":{"type":"integer","minimum":320,"maximum":7680},
                    "height":{"type":"integer","minimum":240,"maximum":4320},
                    "frame_rate":{"type":"integer","minimum":1,"maximum":120}
                },
                "required":["width","height","frame_rate"],
                "additionalProperties":false
            }
        },
        "required":["plan","profile"],
        "additionalProperties":false
    })
}

/// Discoverable, narrow typed camera rendering input. Closed top-level and
/// source document fields; exact nested rules are also enforced by the
/// #[serde(deny_unknown_fields)] wire types and validate_source_only before
/// the original Driver Host is asked to execute anything.
fn camera_start_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "properties": {
                    "schema_version": {"const": 4},
                    "profile_id": {"const": "mw-manim-planar-camera-v1"},
                    "output_role": {"const": "scene_video_contribution_without_audio_or_captions"},
                    "binding": {"type": "object"},
                    "clock": {"type": "object"},
                    "render_profile": {"type": "object"},
                    "canvas": {"type": "object"},
                    "background": {"type": "object"},
                    "camera": {"type": "object"},
                    "reframe": {"type": "object"},
                    "nodes": {
                        "type": "array", "minItems": 1, "maxItems": 32,
                        "items": {"type": "object"}
                    }
                },
                "required": [
                    "schema_version", "profile_id", "output_role", "binding",
                    "clock", "render_profile", "canvas", "background",
                    "camera", "reframe", "nodes"
                ],
                "additionalProperties": false
            },
            "profile": {
                "type": "object",
                "properties": {
                    "width": {"const": 640},
                    "height": {"const": 360},
                    "frame_rate": {"const": 30}
                },
                "required": ["width", "height", "frame_rate"],
                "additionalProperties": false
            }
        },
        "required": ["plan", "profile"],
        "additionalProperties": false
    })
}

fn capability_catalog() -> Result<Vec<Capability>> {
    Ok(vec![
        capability(CapabilitySpec {
            name: "driver.manim-community.doctor",
            description: "Inspect the bounded Manim Community runtime contract without executing user code",
            schemas: (
                json!({"type":"object","properties":{},"additionalProperties":false}),
                json!({"type":"object"}),
            ),
            risk: Risk::ReadOnly,
            idempotency: Idempotency::ReadOnly,
            dry_run: true,
            tags: &["manim", "renderer", "diagnostic"],
        }),
        capability(CapabilitySpec {
            name: "driver.manim-community.render.start",
            description: "Render a typed Motionwright Manim plan through the owner-pinned Manim Community runtime",
            schemas: (start_schema(), job_output_schema()),
            risk: Risk::MutatingReversible,
            idempotency: Idempotency::NonIdempotent,
            dry_run: false,
            tags: &[
                "manim",
                "renderer",
                "render",
                "job",
                "artifact-out:video/clip",
            ],
        }),
        capability(CapabilitySpec {
            name: "driver.manim-community.camera.render.start",
            description: "Render a closed typed planar-camera scene using the original owner-authorized Manim Community Driver Host",
            schemas: (camera_start_schema(), job_output_schema()),
            risk: Risk::MutatingReversible,
            idempotency: Idempotency::NonIdempotent,
            dry_run: false,
            tags: &[
                "manim",
                "camera",
                "renderer",
                "render",
                "job",
                "artifact-out:video/clip",
            ],
        }),
        capability(CapabilitySpec {
            name: "driver.manim-community.render.status",
            description: "Observe a bounded Manim render job",
            schemas: (job_schema(), job_output_schema()),
            risk: Risk::ReadOnly,
            idempotency: Idempotency::ReadOnly,
            dry_run: true,
            tags: &["manim", "renderer", "render", "job"],
        }),
        capability(CapabilitySpec {
            name: "driver.manim-community.render.cancel",
            description: "Request cancellation of a Manim render job owned by Driver Host",
            schemas: (job_schema(), job_output_schema()),
            risk: Risk::MutatingReversible,
            idempotency: Idempotency::Idempotent,
            dry_run: false,
            tags: &["manim", "renderer", "render", "job"],
        }),
        capability(CapabilitySpec {
            name: "driver.manim-community.render.result",
            description: "Return the digest-bound MP4 artifact for a successful Manim render job",
            schemas: (job_schema(), job_output_schema()),
            risk: Risk::ReadOnly,
            idempotency: Idempotency::ReadOnly,
            dry_run: true,
            tags: &["manim", "renderer", "render", "artifact-out:video/clip"],
        }),
    ])
}

#[tokio::main]
async fn main() {
    let driver = match ManimCommunityDriver::production() {
        Ok(driver) => driver,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(error.exit_code());
        }
    };
    if let Err(error) = serve(driver).await {
        eprintln!("{error}");
        std::process::exit(error.exit_code());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_manim_profile::ManimPrimitive;

    #[test]
    fn catalog_never_exposes_python_or_shell_execution() {
        let caps = capability_catalog().unwrap();
        assert_eq!(caps.len(), 6);
        for capability in caps {
            assert!(
                capability
                    .descriptor
                    .name
                    .starts_with("driver.manim-community.")
            );
            let encoded = serde_json::to_string(&capability.descriptor).unwrap();
            assert!(!encoded.contains("python"));
            assert!(!encoded.contains("shell"));
            assert!(!encoded.contains("command_line"));
        }
    }

    #[test]
    fn static_capability_remains_closed_and_camera_has_distinct_nonidempotent_grant() {
        let caps = capability_catalog().unwrap();
        let static_cap = caps
            .iter()
            .find(|x| x.descriptor.name == "driver.manim-community.render.start")
            .unwrap();
        let camera = caps
            .iter()
            .find(|x| x.descriptor.name == "driver.manim-community.camera.render.start")
            .unwrap();
        let old = serde_json::to_string(&static_cap.descriptor).unwrap();
        let new = serde_json::to_string(&camera.descriptor).unwrap();
        assert!(old.contains("primitives"));
        assert!(!old.contains("mw-manim-planar-camera-v1"));
        assert!(!new.contains("primitives"));
        assert!(new.contains("mw-manim-planar-camera-v1"));
        assert!(!new.contains("python"));
        assert!(!new.contains("shell"));
        assert!(new.contains("render"));
        assert_eq!(camera.descriptor.requires, vec![DRIVER_SCOPE]);
        assert_eq!(camera.descriptor.idempotency, Idempotency::NonIdempotent);
        assert_eq!(camera.descriptor.risk, Risk::MutatingReversible);
        assert!(!camera.descriptor.dry_run);
        let schema = camera_start_schema();
        assert_eq!(schema["properties"]["plan"]["additionalProperties"], false);
        assert_eq!(
            schema["properties"]["profile"]["properties"]["frame_rate"]["const"],
            30
        );
    }

    fn original_planar_camera_request() -> CameraStartArgs {
        // Actual canonical typed source, not imported scene.py. Mirrors the
        // bounded 30fps two-object native source contract on original Manim.
        let project = "10000000-0000-4000-8000-000000000001";
        let generation = "10000000-0000-4000-8000-000000000002";
        let deliverable = "10000000-0000-4000-8000-000000000003";
        let scene = "20000000-0000-4000-8000-000000000001";
        let data = json!({
            "plan": {
                "schema_version": 4,
                "profile_id": "mw-manim-planar-camera-v1",
                "output_role": "scene_video_contribution_without_audio_or_captions",
                "binding": {
                    "project_id": project, "generation": generation,
                    "revision": 17, "scene_id": scene, "deliverable_id": deliverable
                },
                "clock": {
                    "timeline_start": {"num": "0", "den": "1"},
                    "duration": {"num": "1", "den": "1"},
                    "frame_rate": {"num": "30", "den": "1"},
                    "project_start_frame": 0, "project_end_frame_exclusive": 30,
                    "local_start_frame": 0, "frame_count": 30
                },
                "render_profile": {"width": 640, "height": 360, "frame_rate": 30},
                "canvas": {"width": 1920, "height": 1080,
                           "coordinate_space": "project_pixels"},
                "background": {"kind": "solid", "color": "#000000", "opacity": 255},
                "camera": {"center_x": 1000.0, "center_y": 520.0,
                           "zoom": 1.0, "rotation_deg": 0.0,
                           "safe_margin": 0.05},
                "reframe": {
                    "id": "30000000-0000-4000-8000-000000000001",
                    "scene_id": scene, "component_version": 2,
                    "start_center_x": 960.0, "start_center_y": 540.0,
                    "end_center_x": 1000.0, "end_center_y": 520.0,
                    "duration_frames": 24
                },
                "nodes": [
                    {
                        "node_id":"40000000-0000-4000-8000-000000000001",
                        "display_name":"Rectangle", "source_kind":"rectangle",
                        "canonical_ordinal":0,"z_index":0,
                        "x_px":420.0,"y_px":270.0,"width_px":270.0,
                        "height_px":180.0,"fill_hex":"#E53935"
                    },
                    {
                        "node_id":"40000000-0000-4000-8000-000000000002",
                        "display_name":"Circle", "source_kind":"circle",
                        "canonical_ordinal":1,"z_index":1,
                        "x_px":1125.0,"y_px":540.0,"width_px":180.0,
                        "height_px":180.0,"fill_hex":"#2BBBAD"
                    }
                ]
            },
            "profile": {"width": 640, "height": 360, "frame_rate": 30}
        });
        serde_json::from_value(data).unwrap()
    }

    #[test]
    fn typed_camera_source_reaches_only_original_inert_trusted_compiler() {
        let args = original_planar_camera_request();
        let original_static = start_schema();
        let camera_descriptor = camera_start_schema();
        assert!(original_static["properties"]["plan"]["properties"]["primitives"].is_object());
        assert!(camera_descriptor["properties"]["plan"]["properties"]["primitives"].is_null());

        let prepared = prepare_camera_for_original_host(&args).unwrap();
        assert!(!prepared.native_execution_performed);
        assert!(!prepared.runtime_sample_pts_verified);
        assert!(
            prepared
                .python_source
                .contains("class MotionwrightScene(MovingCameraScene):")
        );
        assert!(prepared.python_source.contains("UpdateFromAlphaFunc"));
        assert!(
            prepared
                .python_source
                .contains("manim_camera_version_mismatch")
        );
        assert_eq!(
            prepared.source_sha256,
            hex::encode(Sha256::digest(prepared.python_source.as_bytes()))
        );
        let reopened: ManimCameraScenePlanV1 =
            serde_json::from_slice(&prepared.plan_document).unwrap();
        assert_eq!(reopened, args.plan);
        assert_eq!(
            prepared,
            prepare_camera_for_original_host(&args).unwrap(),
            "canonical source must deterministically compile before Host grant"
        );
    }

    #[test]
    fn typed_camera_source_rejects_wrong_version_profile_and_foreign_script() {
        let fixture = original_planar_camera_request();
        let mut out = fixture.clone();
        out.profile.frame_rate = 24;
        assert!(prepare_camera_for_original_host(&out).is_err());
        let mut out = fixture.clone();
        out.plan.schema_version = 3;
        assert!(prepare_camera_for_original_host(&out).is_err());
        let mut out = fixture.clone();
        out.plan.reframe.duration_frames = 0;
        assert!(prepare_camera_for_original_host(&out).is_err());
        let mut out = fixture.clone();
        out.plan.nodes[0].display_name = "__import__('os').system('bad')".into();
        let safe = prepare_camera_for_original_host(&out).unwrap();
        assert!(!safe.native_execution_performed);
        assert!(!safe.python_source.contains("eval("));
        let mut value = serde_json::to_value(&fixture).unwrap();
        value["source"] = json!("from os import system");
        assert!(serde_json::from_value::<CameraStartArgs>(value).is_err());
        let mut value = serde_json::to_value(&fixture).unwrap();
        value["plan"]["script"] = json!("user-controlled");
        assert!(serde_json::from_value::<CameraStartArgs>(value).is_err());
        let mut value = serde_json::to_value(&fixture).unwrap();
        value["plan"]["nodes"][0]["source_kind"] = json!("text");
        assert!(serde_json::from_value::<CameraStartArgs>(value).is_err());
    }

    fn verified_camera_getters_fixture(plan: &ManimCameraScenePlanV1, plan_sha256: &str) -> Value {
        let samples = plan.expected_frame_samples().unwrap();
        let count = samples.len();
        let objects: Vec<_> = plan
            .nodes
            .iter()
            .map(|node| {
                let kind = match node.source_kind {
                    motionwright_manim_profile::ManimSourceShapeKindV2::Rectangle => "rectangle",
                    motionwright_manim_profile::ManimSourceShapeKindV2::Circle => "circle",
                    _ => panic!("fixture has only closed rectangle/circle"),
                };
                json!({
                    "node_id": node.node_id,
                    "kind": kind,
                    "name": format!("mw_{}", node.node_id.simple()),
                    "world_center": [
                        (node.x_px + node.width_px / 2.0 - 960.0) / 135.0,
                        (540.0 - node.y_px - node.height_px / 2.0) / 135.0,
                        0.0
                    ],
                    "width": node.width_px / 135.0,
                    "height": node.height_px / 135.0,
                    "z_index": node.z_index,
                    "fill_hex": node.fill_hex,
                    "fill_opacity": 1.0,
                    "stroke_width": 0.0,
                    "stroke_opacity": 0.0
                })
            })
            .collect();
        let frames: Vec<_> = samples
            .into_iter()
            .enumerate()
            .map(|(i, sample)| {
                json!({
                    "index": i,
                    "camera": {
                        "frame_center": sample.world_frame_center,
                        "frame_width": 128.0 / 9.0,
                        "frame_height": 8.0,
                        "pixel_width": 640,
                        "pixel_height": 360,
                        "frame_rate": 30.0
                    },
                    "objects": objects
                })
            })
            .collect();
        json!({
            "schema_version": 1,
            "profile_id": "mw-manim-planar-camera-v1",
            "scene_class": SCENE_CLASS,
            "plan_sha256": plan_sha256,
            "binding": plan.binding,
            "sample_count": count,
            "callback_alphas": (0..count)
                .map(|i| i as f64 / (count as f64 - 0.5))
                .collect::<Vec<_>>(),
            "frames": frames,
            "decoded_video_verified": false,
            "native_grants_verified_by_this_script": false
        })
    }

    #[test]
    fn native_camera_getter_proof_requires_every_pose_and_original_source() {
        let args = original_planar_camera_request();
        let source = prepare_camera_for_original_host(&args).unwrap();
        let record = verified_camera_getters_fixture(&args.plan, &source.plan_sha256);
        let bytes = serde_json::to_vec(&record).unwrap();
        assert_eq!(
            verify_camera_getters_bytes(&bytes, &args.plan, &source.plan_sha256).unwrap(),
            hex::encode(Sha256::digest(&bytes))
        );
        assert!(verify_camera_getters_bytes(&bytes, &args.plan, &"0".repeat(64)).is_err());

        let mut corrupt = record.clone();
        corrupt["binding"]["revision"] = json!(999);
        assert!(
            verify_camera_getters_bytes(
                &serde_json::to_vec(&corrupt).unwrap(),
                &args.plan,
                &source.plan_sha256
            )
            .is_err()
        );

        let mut corrupt = record.clone();
        corrupt["frames"][18]["camera"]["frame_center"][0] = json!(0.0);
        assert!(
            verify_camera_getters_bytes(
                &serde_json::to_vec(&corrupt).unwrap(),
                &args.plan,
                &source.plan_sha256
            )
            .is_err()
        );

        let mut corrupt = record.clone();
        corrupt["frames"][8]["objects"][0]["fill_hex"] = json!("#FFFFFF");
        assert!(
            verify_camera_getters_bytes(
                &serde_json::to_vec(&corrupt).unwrap(),
                &args.plan,
                &source.plan_sha256
            )
            .is_err()
        );

        let mut corrupt = record.clone();
        corrupt["frames"].as_array_mut().unwrap().remove(15);
        assert!(
            verify_camera_getters_bytes(
                &serde_json::to_vec(&corrupt).unwrap(),
                &args.plan,
                &source.plan_sha256
            )
            .is_err()
        );

        let mut corrupt = record.clone();
        corrupt["callback_alphas"][12] = json!(1.0);
        assert!(
            verify_camera_getters_bytes(
                &serde_json::to_vec(&corrupt).unwrap(),
                &args.plan,
                &source.plan_sha256
            )
            .is_err()
        );

        let mut corrupt = record;
        corrupt["native_grants_verified_by_this_script"] = json!(true);
        assert!(
            verify_camera_getters_bytes(
                &serde_json::to_vec(&corrupt).unwrap(),
                &args.plan,
                &source.plan_sha256
            )
            .is_err()
        );
    }

    #[test]
    fn typed_plan_serialization_contains_no_source_field() {
        let plan = ManimScenePlan {
            project_id: Uuid::nil(),
            generation: Uuid::from_u128(1),
            revision: 3,
            scene_id: Uuid::from_u128(2),
            primitives: vec![ManimPrimitive::Circle {
                node_id: Uuid::from_u128(4),
                x: 0.0,
                y: 0.0,
                radius: 1.0,
                color: "#FFFFFF".into(),
            }],
        };
        let value = serde_json::to_value(StartArgs {
            plan,
            profile: ManimRenderProfile::default(),
        })
        .unwrap();
        assert!(value.pointer("/plan/primitives/0/kind").is_some());
        assert!(value.get("source").is_none());
        assert!(value.pointer("/plan/source").is_none());
    }

    #[test]
    fn output_path_is_driver_owned_and_normalized() {
        let token = "0123456789abcdef0123456789abcdef";
        let relative = format!("videos/mw_{token}/1080p30/mw_{token}.mp4");
        let path = Path::new(&relative);
        assert!(!path.is_absolute());
        assert!(
            path.components()
                .all(|part| matches!(part, Component::Normal(_)))
        );
    }
}
