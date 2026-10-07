use async_trait::async_trait;
use motionwright_manim_profile::{
    ManimRenderProfile, ManimScenePlan, SCENE_CLASS, compile_manim_python, validate_plan,
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
        if self.jobs.len() >= MAX_JOBS {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Manim retained-job budget is full",
            ));
        }
        let args: StartArgs = serde_json::from_value(args)?;
        validate_plan(&args.plan).map_err(map_profile_error)?;
        args.profile.validate().map_err(map_profile_error)?;

        let source = compile_manim_python(&args.plan).map_err(map_profile_error)?;
        if source.len() > MAX_SOURCE_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Generated Manim source exceeds the bounded source budget",
            ));
        }
        let plan_sha256 = plan_digest(&args.plan)?;
        let token = Uuid::now_v7().simple().to_string();
        let job_ref = format!("manim-{token}");
        let source_file = format!("mw_{token}.py");
        let output_name = format!("mw_{token}");
        let output_relative = format!(
            "videos/mw_{token}/{}/{}.mp4",
            args.profile.quality_directory(),
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
                    literal(args.profile.width.to_string()),
                    literal("--height".into()),
                    literal(args.profile.height.to_string()),
                    literal("--fps".into()),
                    literal(args.profile.frame_rate.to_string()),
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
                profile: args.profile,
                artifact: None,
                diagnostic: None,
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

fn capability(
    name: &str,
    description: &str,
    input_schema: Value,
    output_schema: Value,
    risk: Risk,
    idempotency: Idempotency,
    dry_run: bool,
    tags: &[&str],
) -> Capability {
    Capability {
        descriptor: CommandDescriptor {
            name: name.into(),
            version: "1".into(),
            description: description.into(),
            input_schema,
            output_schema,
            requires: vec![DRIVER_SCOPE.into()],
            risk,
            idempotency,
            timeout_ms: 300_000,
            dry_run,
            interactive_consent: false,
            backends: vec![DRIVER_SCOPE.into()],
        },
        aliases: vec![],
        tags: tags.iter().map(|value| (*value).into()).collect(),
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

fn capability_catalog() -> Result<Vec<Capability>> {
    Ok(vec![
        capability(
            "driver.manim-community.doctor",
            "Inspect the bounded Manim Community runtime contract without executing user code",
            json!({"type":"object","properties":{},"additionalProperties":false}),
            json!({"type":"object"}),
            Risk::ReadOnly,
            Idempotency::ReadOnly,
            true,
            &["manim", "renderer", "diagnostic"],
        ),
        capability(
            "driver.manim-community.render.start",
            "Render a typed Motionwright Manim plan through the owner-pinned Manim Community runtime",
            start_schema(),
            job_output_schema(),
            Risk::MutatingReversible,
            Idempotency::NonIdempotent,
            false,
            &[
                "manim",
                "renderer",
                "render",
                "job",
                "artifact-out:video/clip",
            ],
        ),
        capability(
            "driver.manim-community.render.status",
            "Observe a bounded Manim render job",
            job_schema(),
            job_output_schema(),
            Risk::ReadOnly,
            Idempotency::ReadOnly,
            true,
            &["manim", "renderer", "render", "job"],
        ),
        capability(
            "driver.manim-community.render.cancel",
            "Request cancellation of a Manim render job owned by Driver Host",
            job_schema(),
            job_output_schema(),
            Risk::MutatingReversible,
            Idempotency::Idempotent,
            false,
            &["manim", "renderer", "render", "job"],
        ),
        capability(
            "driver.manim-community.render.result",
            "Return the digest-bound MP4 artifact for a successful Manim render job",
            job_schema(),
            job_output_schema(),
            Risk::ReadOnly,
            Idempotency::ReadOnly,
            true,
            &["manim", "renderer", "render", "artifact-out:video/clip"],
        ),
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
        assert_eq!(caps.len(), 5);
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
