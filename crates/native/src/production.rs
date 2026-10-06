use crate::{
    film::{FilmBuildOptions, build_motion_canvas_segments},
    multi_renderer::{blender_export_path, build_blender_contribution},
};
use motionwright_domain::RevisionStamp;
use motionwright_service::StudioService;
use motionwright_storage::{ProductionReceipt, ProductionReceiptInput};
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult, Value, json,
    types::{Envelope, SourceKind},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{
    io::AsyncReadExt,
    process::Command,
    time::{Instant, sleep, timeout},
};
use uuid::Uuid;

const CONNECTION_SCHEMA: &str = "motionwright-semwright-connection/1";
const MAX_CONFIG_BYTES: u64 = 16 * 1024;
const MAX_ARGS_BYTES: usize = 220_000;
const MAX_OUTPUT_BYTES: usize = 1_048_576;
const DEFAULT_DEADLINE_SECS: u64 = 45;
const MLT_DEADLINE_SECS: u64 = 330;
const MOTION_RENDER_TIMEOUT_MS: u64 = 300_000;
const MOTION_RENDER_POLL_DEADLINE_SECS: u64 = 330;
const MOTION_RENDER_POLL_INTERVAL_MS: u64 = 1_000;
const MOTION_PLAN_MAX_OPERATIONS: u32 = 4_096;

const MOTION_CANVAS_COMMANDS: &[&str] = &[
    "driver.motion-canvas.composition.inspect",
    "driver.motion-canvas.composition.plan",
    "driver.motion-canvas.composition.apply",
    "driver.motion-canvas.composition.verify",
    "driver.motion-canvas.render.start",
    "driver.motion-canvas.render.status",
    "driver.motion-canvas.render.cancel",
    "driver.motion-canvas.render.result",
];

const MLT_COMMANDS: &[&str] = &[
    "driver.mlt-video.frames.encode",
    "driver.mlt-video.sync.probe",
    "driver.mlt-video.av.mux",
    "driver.mlt-video.render.start",
    "driver.mlt-video.render.status",
    "driver.mlt-video.render.cancel",
    "driver.mlt-video.render.result",
];

const BLENDER_COMMANDS: &[&str] = &[
    "driver.blender.semantic.datablock.create",
    "driver.blender.semantic.objects",
    "driver.blender.mesh.geometry.replace",
    "driver.blender.semantic.object.create",
    "driver.blender.material.create",
    "driver.blender.material.assign",
    "driver.blender.export.glb",
];

const WORKFLOW_READ_COMMANDS: &[&str] = &[
    "workflow.traces.list",
    "workflow.candidates.list",
    "workflow.patterns.list",
    "workflow.suggestions.list",
    "workflow.proposals.list",
    "workflow.promotions.list",
    "workflow.proposal.plan",
];

const WORKFLOW_ACTION_COMMANDS: &[&str] = &[
    "workflow.record.start",
    "workflow.record.stop",
    "workflow.compile",
    "workflow.suggestion.compile",
    "workflow.proposal.accept",
    "workflow.verify",
    "workflow.replay",
    "workflow.promote",
];

const WORKFLOW_MUTATING_COMMANDS: &[&str] = &[
    "workflow.record.start",
    "workflow.record.stop",
    "workflow.compile",
    "workflow.suggestion.compile",
    "workflow.proposal.accept",
    "workflow.verify",
    "workflow.replay",
    "workflow.promote",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductionConnection {
    pub schema: String,
    pub executable: PathBuf,
    pub executable_sha256: String,
    pub socket: PathBuf,
    pub session_file: PathBuf,
    pub output_root: PathBuf,
    pub resource: String,
}

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn backend(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::BackendFailed, message)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn safe_metadata(path: &Path) -> NativeResult<fs::Metadata> {
    if !path.is_absolute() {
        return Err(invalid("Production connection paths must be absolute"));
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| {
        Error::new(
            ErrorCode::Unavailable,
            "Production connection path is unavailable",
        )
    })?;
    if metadata.file_type().is_symlink() {
        return Err(Error::new(
            ErrorCode::PermissionDenied,
            "Production connection paths cannot be symlinks",
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o022 != 0 {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Production connection paths cannot be group/world writable",
            ));
        }
    }
    Ok(metadata)
}

fn sha256_file(path: &Path) -> NativeResult<String> {
    let mut file = fs::File::open(path).map_err(|_| {
        Error::new(
            ErrorCode::Unavailable,
            "Semwright executable is unavailable",
        )
    })?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|_| backend("Semwright executable could not be verified"))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

impl ProductionConnection {
    pub fn load(path: impl AsRef<Path>) -> NativeResult<Self> {
        let path = path.as_ref();
        let metadata = safe_metadata(path)?;
        if !metadata.is_file() || metadata.len() > MAX_CONFIG_BYTES {
            return Err(invalid(
                "Production connection file is invalid or too large",
            ));
        }
        let bytes = fs::read(path).map_err(|_| {
            Error::new(
                ErrorCode::Unavailable,
                "Production connection file is unavailable",
            )
        })?;
        let connection: Self = serde_json::from_slice(&bytes)
            .map_err(|_| invalid("Production connection file is malformed"))?;
        connection.validate()?;
        Ok(connection)
    }

    pub fn validate(&self) -> NativeResult<()> {
        if self.schema != CONNECTION_SCHEMA {
            return Err(invalid(
                "Unsupported Motionwright production connection schema",
            ));
        }
        if !is_sha256(&self.executable_sha256)
            || self.resource.is_empty()
            || self.resource.len() > 512
            || self.resource.chars().any(char::is_control)
        {
            return Err(invalid("Production connection identity is invalid"));
        }

        let executable = safe_metadata(&self.executable)?;
        if !executable.is_file() {
            return Err(invalid("Semwright executable must be a regular file"));
        }
        let session = safe_metadata(&self.session_file)?;
        if !session.is_file() || session.len() > MAX_CONFIG_BYTES {
            return Err(invalid("Semwright session file is invalid"));
        }
        let output = safe_metadata(&self.output_root)?;
        if !output.is_dir() {
            return Err(invalid("Production output root must be a directory"));
        }
        let _socket = safe_metadata(&self.socket)?;

        if sha256_file(&self.executable)? != self.executable_sha256 {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Semwright executable digest changed",
            ));
        }
        Ok(())
    }

    pub fn identity(&self) -> NativeResult<String> {
        let bytes = serde_json::to_vec(self)
            .map_err(|_| invalid("Production connection could not be encoded"))?;
        Ok(hex::encode(Sha256::digest(bytes)))
    }
}

#[derive(Debug, Clone)]
struct ExpectedAuthority {
    provider: &'static str,
    source: SourceKind,
    provider_generation_required: bool,
}

fn expected_authority(command: &str) -> Option<ExpectedAuthority> {
    if MOTION_CANVAS_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:motion-canvas",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if MLT_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:mlt-video",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if BLENDER_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:blender",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if WORKFLOW_READ_COMMANDS.contains(&command)
        || WORKFLOW_ACTION_COMMANDS.contains(&command)
    {
        Some(ExpectedAuthority {
            provider: "semwright-core",
            source: SourceKind::Builtin,
            provider_generation_required: false,
        })
    } else {
        None
    }
}

fn request_digest(command: &str, args: &Value) -> NativeResult<String> {
    let bytes = serde_json::to_vec(&(command, args))
        .map_err(|_| invalid("Production request could not be encoded"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

async fn read_bounded<R>(reader: R) -> std::io::Result<Vec<u8>>
where
    R: tokio::io::AsyncRead + Unpin,
{
    let mut bytes = Vec::new();
    let mut limited = reader.take((MAX_OUTPUT_BYTES + 1) as u64);
    limited.read_to_end(&mut bytes).await?;
    Ok(bytes)
}

#[derive(Debug, Clone)]
pub struct BrokerResult {
    pub value: Value,
    pub request_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionCanvasRenderEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub segments: Vec<MotionCanvasSegmentEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionCanvasSegmentEvidence {
    pub segment_id: String,
    pub scene_ids: Vec<Uuid>,
    pub frame_count: u64,
    pub plan_ref: String,
    pub fingerprint: String,
    pub job_ref: String,
    pub artifact: Value,
    pub verification: Value,
}

#[derive(Clone)]
pub struct ProductionClient {
    connection: ProductionConnection,
}

impl ProductionClient {
    pub fn new(connection: ProductionConnection) -> NativeResult<Self> {
        connection.validate()?;
        Ok(Self { connection })
    }

    pub fn connection(&self) -> &ProductionConnection {
        &self.connection
    }

    pub async fn execute(
        &self,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<BrokerResult> {
        let expected = expected_authority(command).ok_or_else(|| {
            Error::new(ErrorCode::Unsupported, "Canonical command is not enabled")
        })?;
        let mutation = mutation || WORKFLOW_MUTATING_COMMANDS.contains(&command);
        self.connection.validate()?;
        let encoded = serde_json::to_vec(&args)
            .map_err(|_| invalid("Production command arguments are malformed"))?;
        if encoded.len() > MAX_ARGS_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Production command arguments exceed the transport budget",
            ));
        }

        let mut child = Command::new(&self.connection.executable);
        child
            .arg("--socket")
            .arg(&self.connection.socket)
            .arg("--session-file")
            .arg(&self.connection.session_file)
            .arg("--json")
            .arg("execute")
            .arg(command)
            .arg("--args-json")
            .arg(
                std::str::from_utf8(&encoded)
                    .map_err(|_| invalid("Production arguments are not UTF-8 JSON"))?,
            )
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        let mut child = child.spawn().map_err(|_| {
            Error::new(ErrorCode::Unavailable, "Semwright CLI could not be started")
        })?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| backend("Semwright CLI stdout unavailable"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| backend("Semwright CLI stderr unavailable"))?;
        let stdout_task = tokio::spawn(read_bounded(stdout));
        let stderr_task = tokio::spawn(read_bounded(stderr));
        let deadline =
            if command.starts_with("driver.mlt-video.") || command.starts_with("driver.blender.") {
                MLT_DEADLINE_SECS
            } else {
                DEFAULT_DEADLINE_SECS
            };

        let status = match timeout(Duration::from_secs(deadline), child.wait()).await {
            Ok(result) => result.map_err(|_| backend("Semwright CLI process failed"))?,
            Err(_) => {
                let _ = child.kill().await;
                let error =
                    Error::new(ErrorCode::Timeout, "Canonical production command timed out");
                return Err(if mutation { error.uncertain() } else { error });
            }
        };
        let stdout = stdout_task
            .await
            .map_err(|_| backend("Semwright CLI output task failed"))?
            .map_err(|_| backend("Semwright CLI output could not be read"))?;
        let stderr = stderr_task
            .await
            .map_err(|_| backend("Semwright CLI diagnostic task failed"))?
            .map_err(|_| backend("Semwright CLI diagnostics could not be read"))?;
        if stdout.len() > MAX_OUTPUT_BYTES || stderr.len() > MAX_OUTPUT_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Semwright CLI response exceeded the transport budget",
            ));
        }

        let envelope: Envelope = serde_json::from_slice(&stdout)
            .map_err(|_| backend("Semwright CLI returned an invalid response envelope"))?;
        if envelope.command != command {
            return Err(backend(
                "Semwright CLI response command does not match the request",
            ));
        }
        if !status.success() || !envelope.ok {
            let error = envelope.error.unwrap_or_else(|| {
                backend("Canonical production command failed without a typed error")
            });
            return Err(error);
        }
        let provenance = envelope
            .execution
            .provenance
            .as_ref()
            .ok_or_else(|| backend("Canonical production response has no provenance"))?;
        if provenance.provider != expected.provider
            || provenance.source != expected.source
            || (expected.provider_generation_required && provenance.provider_generation.is_none())
            || !is_sha256(&provenance.descriptor_sha256)
        {
            return Err(backend(
                "Canonical response provenance does not match the expected authority",
            ));
        }
        let value = envelope
            .data
            .ok_or_else(|| backend("Canonical production response has no data"))?;
        Ok(BrokerResult {
            value: json!({
                "data": value,
                "execution": envelope.execution,
                "warnings": envelope.warnings,
                "provenance_checked": true
            }),
            request_id: envelope.request_id,
        })
    }
}

fn response_data(value: &Value) -> NativeResult<&Value> {
    value
        .pointer("/result/data")
        .ok_or_else(|| backend("Canonical production response is missing provider data"))
}

fn required_string(value: &Value, pointer: &str, context: &str) -> NativeResult<String> {
    value
        .pointer(pointer)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| backend(context))
}

fn ensure_native_motion_verification(value: &Value) -> NativeResult<()> {
    if value
        .pointer("/report/execution_status")
        .and_then(Value::as_str)
        != Some("completed")
        || value
            .pointer("/report/support_level")
            .and_then(Value::as_str)
            != Some("native")
    {
        return Err(backend(
            "Motion Canvas composition verification did not complete with native support",
        ));
    }
    let findings = value
        .pointer("/measurement/findings")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Motion Canvas verification findings are unavailable"))?;
    if !findings.is_empty() {
        return Err(backend(
            "Motion Canvas native verification returned unresolved findings",
        ));
    }
    let checks = value
        .pointer("/measurement/validation/checks")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Motion Canvas verification checks are unavailable"))?;
    if checks.is_empty()
        || checks
            .iter()
            .any(|check| check.get("verdict").and_then(Value::as_str) != Some("PASS"))
    {
        return Err(backend(
            "Motion Canvas native verification did not produce an all-PASS check set",
        ));
    }
    Ok(())
}

#[derive(Clone)]
pub struct ProductionCoordinator {
    service: StudioService,
    client: ProductionClient,
}

impl ProductionCoordinator {
    pub fn new(service: StudioService, connection: ProductionConnection) -> NativeResult<Self> {
        Ok(Self {
            service,
            client: ProductionClient::new(connection)?,
        })
    }

    pub fn receipts(&self, project_id: Uuid, limit: usize) -> NativeResult<Vec<ProductionReceipt>> {
        self.service
            .production_receipts(project_id, limit)
            .map_err(storage_error)
    }

    pub async fn render_motion_canvas_segments(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<MotionCanvasRenderEvidence> {
        if request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("Motion Canvas production request id is invalid"));
        }

        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since Motion Canvas production was prepared",
            ));
        }

        let segments = build_motion_canvas_segments(&project, deliverable_id, options)?;
        if segments.is_empty() {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "The selected revision has no Motion Canvas segments to render",
            ));
        }

        let mut evidence = Vec::with_capacity(segments.len());
        for (index, segment) in segments.into_iter().enumerate() {
            let prefix = format!("{request_id}:motion:{index}");
            let film = serde_json::to_value(&segment.film)
                .map_err(|_| invalid("Canonical Film could not be encoded"))?;

            let planned = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:plan"),
                    "driver.motion-canvas.composition.plan",
                    json!({
                        "film": film,
                        "budget": {
                            "max_iterations": 8,
                            "max_operations": MOTION_PLAN_MAX_OPERATIONS,
                            "max_findings": 1024,
                            "max_observations": 128,
                            "max_elapsed_ms": 120000
                        }
                    }),
                    false,
                )
                .await?;
            let plan_ref = required_string(
                response_data(&planned)?,
                "/plan_ref",
                "Motion Canvas composition plan returned no plan reference",
            )?;

            let applied = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:apply"),
                    "driver.motion-canvas.composition.apply",
                    json!({"plan_ref": plan_ref, "dry_run": false}),
                    true,
                )
                .await?;
            let applied_data = response_data(&applied)?;
            if applied_data.get("applied").and_then(Value::as_bool) != Some(true)
                || applied_data.get("execution_status").and_then(Value::as_str) != Some("completed")
            {
                return Err(backend(
                    "Motion Canvas composition apply did not complete deterministically",
                ));
            }
            let fingerprint = required_string(
                applied_data,
                "/fingerprint",
                "Motion Canvas composition apply returned no source fingerprint",
            )?;

            let started = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:render-start"),
                    "driver.motion-canvas.render.start",
                    json!({
                        "expected_fingerprint": fingerprint,
                        "profile": {
                            "first_frame": 0,
                            "end_frame_exclusive": segment.frame_count,
                            "scale": "full",
                            "transparent": false,
                            "timeout_ms": MOTION_RENDER_TIMEOUT_MS
                        }
                    }),
                    true,
                )
                .await?;
            let job_ref = required_string(
                response_data(&started)?,
                "/job_ref",
                "Motion Canvas render did not return a job reference",
            )?;

            let deadline = Instant::now() + Duration::from_secs(MOTION_RENDER_POLL_DEADLINE_SECS);
            let mut poll = 0_u32;
            let terminal = loop {
                if Instant::now() >= deadline {
                    return Err(Error::new(
                        ErrorCode::Timeout,
                        "Motion Canvas render did not reach a terminal state before the bounded deadline",
                    )
                    .uncertain());
                }
                let status = self
                    .execute(
                        project_id,
                        expected,
                        &format!("{prefix}:render-status:{poll}"),
                        "driver.motion-canvas.render.status",
                        json!({"job_ref": job_ref}),
                        false,
                    )
                    .await?;
                let data = response_data(&status)?.clone();
                match data.get("state").and_then(Value::as_str) {
                    Some("succeeded") => break data,
                    Some("failed") => {
                        return Err(backend("Motion Canvas render reported failure"));
                    }
                    Some("cancelled") => {
                        return Err(Error::new(
                            ErrorCode::Cancelled,
                            "Motion Canvas render was cancelled",
                        ));
                    }
                    Some("queued") | Some("starting") | Some("rendering") => {}
                    Some(_) => {
                        return Err(backend(
                            "Motion Canvas render returned an unsupported job state",
                        ));
                    }
                    None => {
                        return Err(backend(
                            "Motion Canvas render status returned no canonical state",
                        ));
                    }
                }
                poll = poll
                    .checked_add(1)
                    .ok_or_else(|| backend("Motion Canvas render poll counter overflowed"))?;
                sleep(Duration::from_millis(MOTION_RENDER_POLL_INTERVAL_MS)).await;
            };
            if terminal
                .pointer("/artifact/frame_count")
                .and_then(Value::as_u64)
                != Some(segment.frame_count)
            {
                return Err(backend(
                    "Motion Canvas terminal artifact frame count does not match the Film realization",
                ));
            }

            let rendered = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:render-result"),
                    "driver.motion-canvas.render.result",
                    json!({"job_ref": job_ref}),
                    false,
                )
                .await?;
            let rendered_data = response_data(&rendered)?;
            if rendered_data.get("state").and_then(Value::as_str) != Some("succeeded") {
                return Err(backend(
                    "Motion Canvas render result is not a successful terminal artifact",
                ));
            }
            let artifact = rendered_data
                .get("artifact")
                .cloned()
                .ok_or_else(|| backend("Motion Canvas render result has no artifact"))?;
            if artifact.get("frame_count").and_then(Value::as_u64) != Some(segment.frame_count) {
                return Err(backend(
                    "Motion Canvas result artifact frame count does not match the Film realization",
                ));
            }

            let verified = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:verify"),
                    "driver.motion-canvas.composition.verify",
                    json!({"plan_ref": plan_ref, "job_ref": job_ref}),
                    false,
                )
                .await?;
            let verification = response_data(&verified)?.clone();
            ensure_native_motion_verification(&verification)?;

            evidence.push(MotionCanvasSegmentEvidence {
                segment_id: segment.id,
                scene_ids: segment.scene_ids,
                frame_count: segment.frame_count,
                plan_ref,
                fingerprint,
                job_ref,
                artifact,
                verification,
            });
        }

        Ok(MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            segments: evidence,
        })
    }

    async fn blender_ref(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        root: &str,
        name: &str,
    ) -> NativeResult<Value> {
        let response = self
            .execute(
                project_id,
                expected,
                request_id,
                "driver.blender.semantic.objects",
                json!({"root": root, "query": name, "limit": 8}),
                false,
            )
            .await?;
        response_named_ref(&response, name)
    }

    pub async fn realize_blender_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
    ) -> NativeResult<Value> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since the Blender contribution was prepared",
            ));
        }
        let plan = build_blender_contribution(&project, scene_id)?;
        let collection = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:collection"),
                "driver.blender.semantic.datablock.create",
                json!({
                    "root": "collections",
                    "name": plan.collection_name
                }),
                true,
            )
            .await?;

        let mut objects = Vec::with_capacity(plan.meshes.len());
        for (index, mesh) in plan.meshes.iter().enumerate() {
            let mesh_data = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:data"),
                    "driver.blender.semantic.datablock.create",
                    json!({
                        "root": "meshes",
                        "name": mesh.name
                    }),
                    true,
                )
                .await?;
            let mesh_ref = self
                .blender_ref(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:lookup-before-geometry"),
                    "meshes",
                    &mesh.name,
                )
                .await?;
            let geometry = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:geometry"),
                    "driver.blender.mesh.geometry.replace",
                    json!({
                        "mesh_ref": mesh_ref,
                        "vertices": mesh.vertices,
                        "edges": mesh.edges,
                        "faces": mesh.faces
                    }),
                    true,
                )
                .await?;
            let current_mesh_ref = self
                .blender_ref(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:lookup-after-geometry"),
                    "meshes",
                    &mesh.name,
                )
                .await?;
            let collection_ref = self
                .blender_ref(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:collection-ref"),
                    "collections",
                    &plan.collection_name,
                )
                .await?;
            let object = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:object"),
                    "driver.blender.semantic.object.create",
                    json!({
                        "name": mesh.name,
                        "data_ref": current_mesh_ref,
                        "collection_ref": collection_ref
                    }),
                    true,
                )
                .await?;
            let material = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:material"),
                    "driver.blender.material.create",
                    json!({
                        "name": mesh.material_name,
                        "color": mesh.color_rgba,
                        "roughness": 0.45,
                        "metallic": 0.0
                    }),
                    true,
                )
                .await?;
            let assignment = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mesh:{index}:material-assign"),
                    "driver.blender.material.assign",
                    json!({
                        "object": mesh.name,
                        "material": mesh.material_name
                    }),
                    true,
                )
                .await?;
            objects.push(json!({
                "node_id": mesh.node_id,
                "mesh_data": mesh_data,
                "geometry": geometry,
                "object": object,
                "material": material,
                "material_assignment": assignment
            }));
        }

        let export_path = blender_export_path(&plan);
        let export = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:export"),
                "driver.blender.export.glb",
                json!({
                    "collection": plan.collection_name,
                    "path": export_path,
                    "animations": false
                }),
                true,
            )
            .await?;

        Ok(json!({
            "renderer": "blender",
            "native_driver": "driver:blender",
            "project_revision": plan.revision,
            "scene_id": plan.scene_id,
            "collection": collection,
            "objects": objects,
            "export_path": export_path,
            "export": export
        }))
    }

    pub async fn execute(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<Value> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since production was prepared",
            ));
        }
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Production connection is bound to another Motionwright resource",
            ));
        }
        let job_ref = args
            .get("job_ref")
            .or_else(|| args.get("job"))
            .and_then(Value::as_str)
            .map(str::to_owned);
        let digest = request_digest(command, &args)?;

        if let Some(prior) = self
            .service
            .latest_production_receipt(project_id, request_id)
            .map_err(storage_error)?
        {
            if prior.request_sha256 != digest || prior.command != command {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Production request id was reused with different input",
                ));
            }
            if prior.stage == "completed"
                && let Some(result) = prior.payload.get("result")
            {
                return Ok(json!({
                    "replayed": true,
                    "result": result,
                    "receipt": prior
                }));
            }
            let error = Error::new(
                ErrorCode::Conflict,
                "Production request already has a non-terminal or failed receipt; inspect before retrying",
            );
            return Err(if prior.stage == "outcome_unknown" {
                error.uncertain()
            } else {
                error
            });
        }

        self.service
            .append_production_receipt(ProductionReceiptInput {
                project_id,
                generation: project.generation,
                revision: project.revision,
                request_id: request_id.into(),
                request_sha256: digest.clone(),
                command: command.into(),
                stage: "dispatching".into(),
                payload: json!({
                    "mutation": mutation,
                    "connection": self.client.connection().identity()?,
                    "job_ref": job_ref.clone()
                }),
            })
            .map_err(storage_error)?;

        match self.client.execute(command, args, mutation).await {
            Ok(result) => {
                let receipt = self
                    .service
                    .append_production_receipt(ProductionReceiptInput {
                        project_id,
                        generation: project.generation,
                        revision: project.revision,
                        request_id: request_id.into(),
                        request_sha256: digest.clone(),
                        command: command.into(),
                        stage: "completed".into(),
                        payload: json!({
                            "result": result.value,
                            "broker_request_id": result.request_id,
                            "job_ref": job_ref.clone()
                        }),
                    })
                    .map_err(|_| {
                        let error = backend(
                            "Production completed but its local receipt could not be persisted",
                        );
                        if mutation { error.uncertain() } else { error }
                    })?;
                Ok(json!({
                    "replayed": false,
                    "result": receipt.payload["result"],
                    "receipt": receipt
                }))
            }
            Err(error) => {
                let stage = if error.outcome_known {
                    "failed_known"
                } else {
                    "outcome_unknown"
                };
                let _ = self
                    .service
                    .append_production_receipt(ProductionReceiptInput {
                        project_id,
                        generation: project.generation,
                        revision: project.revision,
                        request_id: request_id.into(),
                        request_sha256: digest,
                        command: command.into(),
                        stage: stage.into(),
                        payload: json!({
                            "error": &error,
                            "mutation": mutation,
                            "job_ref": job_ref
                        }),
                    });
                Err(error)
            }
        }
    }
}

fn response_named_ref(value: &Value, name: &str) -> NativeResult<Value> {
    value
        .pointer("/result/data/items")
        .and_then(Value::as_array)
        .and_then(|items| {
            items
                .iter()
                .find(|item| item.get("name").and_then(Value::as_str) == Some(name))
        })
        .and_then(|item| item.get("ref"))
        .cloned()
        .ok_or_else(|| backend("Blender semantic lookup returned no exact typed ref"))
}

fn storage_error(error: motionwright_storage::StorageError) -> Error {
    match error {
        motionwright_storage::StorageError::NotFound => {
            Error::new(ErrorCode::NotFound, "Motionwright project not found")
        }
        motionwright_storage::StorageError::Conflict { .. }
        | motionwright_storage::StorageError::GenerationConflict => Error::new(
            ErrorCode::StaleReference,
            "Motionwright project changed since production was prepared",
        ),
        motionwright_storage::StorageError::RequestReuse => Error::new(
            ErrorCode::Conflict,
            "Production request id was reused with different input",
        ),
        _ => backend("Motionwright production receipt storage failed"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle};
    use motionwright_storage::Store;
    use semwright_media_time::Rate;
    use semwright_motion_authoring::{Archetype, NarrativeRole};
    use semwright_native_sdk::types::{Execution, InvocationProvenance};
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    use std::{collections::BTreeSet, fs::File};

    fn fixture_service() -> (
        StudioService,
        motionwright_domain::Project,
        tempfile::TempDir,
    ) {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let service = StudioService::from_store(store);
        let project = service.create_project("Production fixture").unwrap();
        (service, project, temp)
    }

    #[cfg(unix)]
    fn fake_connection(temp: &tempfile::TempDir, resource: String) -> ProductionConnection {
        let executable = temp.path().join("semwright-fake");
        let socket = temp.path().join("broker.sock");
        let session = temp.path().join("session.json");
        let output = temp.path().join("output");
        fs::create_dir(&output).unwrap();
        File::create(&socket).unwrap();
        fs::write(&session, b"{}").unwrap();

        let execution = Execution {
            backend: "driver:motion-canvas".into(),
            duration_ms: 1,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            provenance: Some(InvocationProvenance {
                provider: "driver:motion-canvas".into(),
                source: SourceKind::Driver,
                provider_version: "test".into(),
                capability_version: "test".into(),
                descriptor_sha256: "ab".repeat(32),
                untrusted_metadata: false,
                catalog_revision: 1,
                execution_provider: Some("driver:motion-canvas".into()),
                provider_generation: Some(1),
            }),
            dry_run: false,
        };
        let envelope = Envelope {
            ok: true,
            request_id: "broker-request".into(),
            command: "driver.motion-canvas.composition.inspect".into(),
            data: Some(json!({"film":null})),
            error: None,
            execution,
            warnings: vec![],
        };
        let output_json = serde_json::to_string(&envelope).unwrap();
        fs::write(
            &executable,
            format!(
                "#!/bin/sh\nprintf '%s\\n' '{}'
",
                output_json.replace("'", "'\\''")
            ),
        )
        .unwrap();
        let mut permissions = fs::metadata(&executable).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&executable, permissions).unwrap();
        let mut permissions = fs::metadata(&session).unwrap().permissions();
        permissions.set_mode(0o600);
        fs::set_permissions(&session, permissions).unwrap();
        let mut permissions = fs::metadata(&socket).unwrap().permissions();
        permissions.set_mode(0o600);
        fs::set_permissions(&socket, permissions).unwrap();
        let mut permissions = fs::metadata(&output).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&output, permissions).unwrap();

        ProductionConnection {
            schema: CONNECTION_SCHEMA.into(),
            executable_sha256: sha256_file(&executable).unwrap(),
            executable,
            socket,
            session_file: session,
            output_root: output,
            resource,
        }
    }

    #[cfg(unix)]
    fn fake_render_connection(temp: &tempfile::TempDir, resource: String) -> ProductionConnection {
        let mut connection = fake_connection(temp, resource);
        let execution = Execution {
            backend: "driver:motion-canvas".into(),
            duration_ms: 1,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            provenance: Some(InvocationProvenance {
                provider: "driver:motion-canvas".into(),
                source: SourceKind::Driver,
                provider_version: "test".into(),
                capability_version: "test".into(),
                descriptor_sha256: "cd".repeat(32),
                untrusted_metadata: false,
                catalog_revision: 1,
                execution_provider: Some("driver:motion-canvas".into()),
                provider_generation: Some(2),
            }),
            dry_run: false,
        };
        let execution_json = serde_json::to_string(&execution).unwrap();
        let script = r#"#!/bin/sh
command="$7"
case "$command" in
  driver.motion-canvas.composition.plan)
    data='{"plan_ref":"plan-native-1","repair":false}'
    ;;
  driver.motion-canvas.composition.apply)
    data='{"applied":true,"execution_status":"completed","fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
    ;;
  driver.motion-canvas.render.start)
    data='{"job_ref":"job-native-1","state":"queued"}'
    ;;
  driver.motion-canvas.render.status)
    data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60}}'
    ;;
  driver.motion-canvas.render.result)
    data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60,"manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'
    ;;
  driver.motion-canvas.composition.verify)
    data='{"report":{"execution_status":"completed","support_level":"native"},"measurement":{"findings":[],"validation":{"checks":[{"id":"native-frame-evidence","verdict":"PASS"}]}}}'
    ;;
  *)
    exit 2
    ;;
esac
printf '{"ok":true,"request_id":"broker-render-request","command":"%s","data":%s,"error":null,"execution":__EXECUTION__,"warnings":[]}\n' "$command" "$data"
"#
        .replace("__EXECUTION__", &execution_json);
        fs::write(&connection.executable, script).unwrap();
        let mut permissions = fs::metadata(&connection.executable).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&connection.executable, permissions).unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        connection
    }

    fn motion_canvas_fixture(
        service: &StudioService,
        initial: motionwright_domain::Project,
    ) -> motionwright_domain::Project {
        let scene = service
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "render-fixture-scene",
                &Change::AddScene {
                    name: "Native render".into(),
                    objective: "Prove the canonical render path".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let scene_id = scene.scenes[0].id;
        service
            .apply(
                scene.id,
                &RevisionStamp::from(&scene),
                "render-fixture-node",
                &Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::now_v7(),
                        name: "Native title".into(),
                        kind: "text".into(),
                        parent_id: None,
                        x: 240.0,
                        y: 320.0,
                        width: 960.0,
                        height: 160.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: Some("Motionwright".into()),
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#F5F5F2".into()),
                            stroke: None,
                            stroke_width: 0.0,
                            font_family: Some("Instrument Sans Variable".into()),
                            font_size: Some(64.0),
                            font_weight: Some(700),
                            line_height: Some(1.05),
                            blend_mode: BlendMode::Normal,
                        },
                        relations: vec![],
                        property_locks: BTreeSet::new(),
                        keyframes: vec![],
                    },
                },
            )
            .unwrap()
            .project
    }

    #[test]
    fn production_allowlist_binds_blender_to_the_native_driver() {
        for command in BLENDER_COMMANDS {
            let authority = expected_authority(command).unwrap();
            assert_eq!(authority.provider, "driver:blender");
            assert_eq!(authority.source, SourceKind::Driver);
            assert!(authority.provider_generation_required);
        }
        assert!(expected_authority("driver.blender.python.exec").is_none());
        assert!(expected_authority("driver.manim.render").is_none());
    }

    #[test]
    fn workflow_surface_is_bounded_and_bound_to_builtin_core() {
        for command in WORKFLOW_READ_COMMANDS
            .iter()
            .chain(WORKFLOW_ACTION_COMMANDS.iter())
        {
            let authority = expected_authority(command).unwrap();
            assert_eq!(authority.provider, "semwright-core");
            assert_eq!(authority.source, SourceKind::Builtin);
            assert!(!authority.provider_generation_required);
        }
        assert!(expected_authority("workflow.trace.delete").is_none());
        assert!(expected_authority("workflow.demote").is_none());
        assert!(expected_authority("workflow.suggestion.dismiss").is_none());
        assert!(WORKFLOW_MUTATING_COMMANDS.contains(&"workflow.replay"));
        assert!(!WORKFLOW_MUTATING_COMMANDS.contains(&"workflow.proposal.plan"));
    }

    #[test]
    fn blender_response_refs_are_extracted_only_from_exact_lookup_results() {
        let value = json!({
            "result": {
                "data": {
                    "items": [{
                        "name": "Mesh",
                        "ref": {
                            "root": "meshes",
                            "name": "Mesh",
                            "path": [],
                            "generation": 1
                        }
                    }]
                }
            }
        });
        assert_eq!(
            response_named_ref(&value, "Mesh").unwrap()["root"],
            "meshes"
        );
        assert!(response_named_ref(&value, "Other").is_err());
        assert!(response_named_ref(&json!({"result":{"data":{}}}), "Mesh").is_err());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn coordinator_binds_revision_provenance_and_request_replay() {
        let (service, project, temp) = fixture_service();
        let connection = fake_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let expected = motionwright_domain::RevisionStamp::from(&project);

        let first = coordinator
            .execute(
                project.id,
                &expected,
                "request-one",
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await
            .unwrap();
        assert_eq!(first["replayed"], false);
        assert_eq!(first["result"]["provenance_checked"], true);

        let replay = coordinator
            .execute(
                project.id,
                &expected,
                "request-one",
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await
            .unwrap();
        assert_eq!(replay["replayed"], true);
        assert_eq!(coordinator.receipts(project.id, 10).unwrap().len(), 2);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn motion_canvas_production_is_revision_bound_rendered_and_natively_verified() {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let connection = fake_render_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let expected = RevisionStamp::from(&project);
        let scene_id = project.scenes[0].id;
        let deliverable_id = project.deliverables[0].id;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: vec![crate::film::SceneFilmIntent {
                scene_id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            }],
        };

        let rendered = coordinator
            .render_motion_canvas_segments(
                project.id,
                &expected,
                "native-render-e2e",
                deliverable_id,
                &options,
            )
            .await
            .unwrap();

        assert_eq!(rendered.project_resource, project.resource_key());
        assert_eq!(rendered.generation, project.generation);
        assert_eq!(rendered.revision, project.revision);
        assert_eq!(rendered.segments.len(), 1);
        assert_eq!(rendered.segments[0].frame_count, 60);
        assert_eq!(rendered.segments[0].artifact["frame_count"], 60);
        assert_eq!(
            rendered.segments[0].verification["report"]["support_level"],
            "native"
        );
        assert_eq!(
            rendered.segments[0].verification["measurement"]["validation"]["checks"][0]["verdict"],
            "PASS"
        );
        assert_eq!(coordinator.receipts(project.id, 100).unwrap().len(), 12);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn command_allowlist_and_resource_binding_fail_closed() {
        let (service, project, temp) = fixture_service();
        let mut connection = fake_connection(&temp, "project:other".into());
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &motionwright_domain::RevisionStamp::from(&project),
                "wrong-resource",
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::PermissionDenied);

        connection.resource = project.resource_key();
        let client = ProductionClient::new(connection).unwrap();
        let error = client
            .execute("driver.unsupported.execute", json!({}), true)
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }
}
