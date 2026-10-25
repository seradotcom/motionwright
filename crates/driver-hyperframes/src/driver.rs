//! Protocol-v7 provider. Driver Host exclusively owns native process/job lifecycle.
use crate::{
    Error, ErrorCode, Result,
    model::{self, Composition, SourceInventory},
    store::{self, Store},
};
use async_trait::async_trait;
use schemars::{JsonSchema, schema_for};
use semwright_driver_sdk::{
    Capability, Driver, DriverExecutionContext, DriverInterfaces, RuntimeToolArg, RuntimeToolCwd,
    RuntimeToolJob, RuntimeToolJobStatus, artifact_output_tag, descriptor_digest, workspace_mount,
};
use semwright_types::{CommandDescriptor, Idempotency, Risk};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::{path::PathBuf, time::Duration};
const ID: &str = "hyperframes";
const SCOPE: &str = "driver:hyperframes";
pub const LAUNCH_SOURCE: &str = "import{pathToFileURL}from'node:url';import{join}from'node:path';await(await import(pathToFileURL(join(process.argv[1],'semwright-hyperframes-runner.mjs')).href)).main()";

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Empty {}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct PinArgs {
    runtime_lock_sha256: String,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct PlanArgs {
    composition: Composition,
    runtime_lock_sha256: String,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ApplyArgs {
    composition: Composition,
    expected_fingerprint: Option<String>,
    #[serde(default)]
    dry_run: bool,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct StartArgs {
    fingerprint: String,
    runtime_lock_sha256: String,
    attempt_id: String,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct JobArgs {
    job_ref: String,
}
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct NativeCommand {
    argv_sha256: String,
    exit_code: Option<i32>,
    signal: Option<String>,
    stdout_sha256: String,
    stdout_bytes: u64,
    stderr_sha256: String,
    stderr_bytes: u64,
    capture_complete: bool,
    automatic_retry: bool,
}
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Versions {
    node: String,
    hyperframes: String,
    browser: String,
    ffmpeg: String,
    ffprobe: String,
}
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NativeFrame {
    pub index: u32,
    pub file: String,
    pub bytes: u64,
    pub sha256: String,
    pub width: u32,
    pub height: u32,
}
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NativeReceipt {
    pub schema: String,
    pub status: String,
    pub attempt_id: String,
    pub fingerprint: String,
    pub source_sha256: String,
    pub html_sha256: String,
    pub runtime_lock_sha256: String,
    pub hyperframes_version: String,
    pub hyperframes_commit: String,
    pub frame_count: u32,
    pub fps: u32,
    pub frames: Vec<NativeFrame>,
    native_command: NativeCommand,
    versions: Versions,
    pub source_unchanged: bool,
    pub pixel_decoding_performed: bool,
    pub automatic_retry: bool,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct JobBinding {
    schema: String,
    attempt_id: String,
    session_sha256: String,
    job: RuntimeToolJob,
    source: SourceInventory,
    runtime_lock_sha256: String,
}
#[derive(Serialize, JsonSchema)]
struct Plan {
    source: SourceInventory,
    runtime_lock_sha256: String,
    runnable: bool,
    dependencies_verified: bool,
    implicit_installation: bool,
}
#[derive(Serialize, JsonSchema)]
struct Doctor {
    provider: String,
    hyperframes_version: String,
    hyperframes_commit: String,
    runtime_lock_sha256: String,
    runtime_files_verified: usize,
    native_execution_performed: bool,
    implicit_installation: bool,
}
#[derive(Serialize, JsonSchema)]
struct Inspection {
    source: Option<SourceInventory>,
    native_execution_performed: bool,
}
#[derive(Serialize, JsonSchema)]
struct Applied {
    source: SourceInventory,
    applied: bool,
    dry_run: bool,
}
#[derive(Serialize, JsonSchema)]
struct JobView {
    job_ref: String,
    state: String,
    fingerprint: String,
    runtime_lock_sha256: String,
    directory: String,
    receipt: Option<NativeReceipt>,
    automatic_retry: bool,
}

pub struct HyperframesDriver {
    project: Option<Store>,
    runtime: Option<PathBuf>,
    output: Option<PathBuf>,
}
impl HyperframesDriver {
    pub fn production() -> Result<Self> {
        let project = workspace_mount("project")
            .ok()
            .map(Store::new)
            .transpose()?;
        let runtime = workspace_mount("runtime")
            .ok()
            .map(store::canonical_root)
            .transpose()?;
        let output = workspace_mount("output")
            .ok()
            .map(store::canonical_root)
            .transpose()?;
        let roots = [
            project.as_ref().map(|s| s.root.as_path()),
            runtime.as_deref(),
            output.as_deref(),
        ];
        for (i, left) in roots.iter().enumerate() {
            for right in &roots[i + 1..] {
                if let (Some(left), Some(right)) = (left, right) {
                    if left.starts_with(right) || right.starts_with(left) {
                        return Err(Error::new(
                            ErrorCode::PermissionDenied,
                            "HyperFrames grants must be distinct, nonoverlapping roots",
                        ));
                    }
                }
            }
        }
        Ok(Self {
            project,
            runtime,
            output,
        })
    }
    fn project(&self) -> Result<&Store> {
        self.project.as_ref().ok_or_else(|| {
            Error::new(
                ErrorCode::PermissionDenied,
                "HyperFrames project grant is absent",
            )
        })
    }
    fn runtime(&self) -> Result<&std::path::Path> {
        self.runtime.as_deref().ok_or_else(|| {
            Error::new(
                ErrorCode::PermissionDenied,
                "HyperFrames verified runtime grant is absent",
            )
        })
    }
    fn output(&self) -> Result<&std::path::Path> {
        self.output.as_deref().ok_or_else(|| {
            Error::new(
                ErrorCode::PermissionDenied,
                "HyperFrames output grant is absent",
            )
        })
    }
    fn parse<T: DeserializeOwned>(value: Value) -> Result<T> {
        serde_json::from_value(value).map_err(Into::into)
    }
    fn cap<I: JsonSchema, O: JsonSchema>(
        name: &str,
        description: &str,
        risk: Risk,
        idempotency: Idempotency,
        dry_run: bool,
    ) -> Result<Capability> {
        Ok(Capability {
            descriptor: CommandDescriptor {
                name: name.into(),
                version: "1".into(),
                description: description.into(),
                input_schema: serde_json::to_value(schema_for!(I))?,
                output_schema: serde_json::to_value(schema_for!(O))?,
                requires: vec![SCOPE.into()],
                risk,
                idempotency,
                timeout_ms: 60_000,
                dry_run,
                interactive_consent: false,
                backends: vec![SCOPE.into()],
            },
            aliases: vec![],
            tags: vec!["hyperframes".into(), "native-source".into()],
            object_types: vec!["hyperframes-composition".into()],
        })
    }
    fn catalog() -> Result<Vec<Capability>> {
        let mut start = Self::cap::<StartArgs, JobView>(
            "driver.hyperframes.render.start",
            "Start one original native CLI job through Driver Host",
            Risk::MutatingReversible,
            Idempotency::NonIdempotent,
            false,
        )?;
        start.tags.push(artifact_output_tag("image/png-sequence")?);
        Ok(vec![
            Self::cap::<PinArgs, Doctor>(
                "driver.hyperframes.doctor",
                "Read and verify the admitted dependency closure without native execution or installation",
                Risk::ReadOnly,
                Idempotency::ReadOnly,
                false,
            )?,
            Self::cap::<Empty, Inspection>(
                "driver.hyperframes.project.inspect",
                "Read the preserved typed source and native HTML and reject external drift",
                Risk::ReadOnly,
                Idempotency::ReadOnly,
                false,
            )?,
            Self::cap::<PlanArgs, Plan>(
                "driver.hyperframes.project.plan",
                "Validate a closed native composition and exact dependency pin without execution",
                Risk::ReadOnly,
                Idempotency::ReadOnly,
                false,
            )?,
            Self::cap::<ApplyArgs, Applied>(
                "driver.hyperframes.project.apply",
                "Preserve a versioned editable native source bundle under an expected fingerprint",
                Risk::MutatingReversible,
                Idempotency::NonIdempotent,
                true,
            )?,
            start,
            Self::cap::<JobArgs, JobView>(
                "driver.hyperframes.render.status",
                "Observe the original Host-owned job without restarting it",
                Risk::ReadOnly,
                Idempotency::ReadOnly,
                false,
            )?,
            Self::cap::<JobArgs, JobView>(
                "driver.hyperframes.render.result",
                "Read exact original native CLI output bytes after terminal success",
                Risk::ReadOnly,
                Idempotency::ReadOnly,
                false,
            )?,
        ])
    }
    fn authorize_descriptor(command: &str, digest: &str) -> Result<()> {
        let capability = Self::catalog()?
            .into_iter()
            .find(|c| c.descriptor.name == command)
            .ok_or_else(|| Error::new(ErrorCode::NotFound, "Unknown closed HyperFrames command"))?;
        if descriptor_digest(&capability.descriptor)? != digest {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "HyperFrames descriptor changed",
            ));
        }
        Ok(())
    }
    fn source_matches(&self, fingerprint: &str) -> Result<SourceInventory> {
        let source = self.project()?.inspect()?;
        if source.fingerprint != fingerprint {
            return Err(Error::new(
                ErrorCode::Conflict,
                "HyperFrames native source fingerprint changed",
            ));
        }
        Ok(source)
    }
    fn validate_attempt(value: &str) -> Result<()> {
        if !model::slug(value) {
            return Err(Error::invalid(
                "Native attempt identity is not a bounded slug",
            ));
        }
        Ok(())
    }
    async fn start(&self, args: StartArgs, context: &DriverExecutionContext) -> Result<Value> {
        Self::validate_attempt(&args.attempt_id)?;
        let source = self.source_matches(&args.fingerprint)?;
        let _runtime = store::runtime(self.runtime()?, &args.runtime_lock_sha256)?;
        let _output = self.output()?;
        context.check_cancelled()?;
        // Reserve before Host IPC. Unknown/failed start never reuses this id.
        let store = self.project()?;
        let intent = store
            .root
            .join(format!("native-job-{}.intent.json", args.attempt_id));
        store::fresh_json(
            &intent,
            &json!({"schema":"semwright.hyperframes.job-intent/1","source":source,
            "runtime_lock_sha256":args.runtime_lock_sha256,"attempt_id":args.attempt_id,
            "session_sha256":model::sha(context.session().as_bytes()),"automatic_retry":false}),
        )?;
        let input = serde_json::to_vec(
            &json!({"schema":"semwright.hyperframes.render-request/1", "fingerprint":source.fingerprint,
            "runtime_lock_sha256":args.runtime_lock_sha256,"attempt_id":args.attempt_id,"source":source}),
        )?;
        let literal = |value: &str| RuntimeToolArg::Literal {
            value: value.into(),
        };
        let mount = |value: &str| RuntimeToolArg::MountPath {
            mount: value.into(),
            relative: String::new(),
        };
        let job = context
            .start_runtime_tool_job_args(
                "hyperframes-node",
                vec![
                    literal("--input-type=module"),
                    literal("--eval"),
                    literal(LAUNCH_SOURCE),
                    mount("runtime"),
                    mount("project"),
                    mount("output"),
                ],
                input,
                Duration::from_secs(300),
                Some(RuntimeToolCwd {
                    mount: "project".into(),
                    relative: String::new(),
                }),
            )
            .await
            .map_err(Error::uncertain)?;
        let binding = JobBinding {
            schema: "semwright.hyperframes.job-binding/1".into(),
            attempt_id: args.attempt_id.clone(),
            session_sha256: model::sha(context.session().as_bytes()),
            job,
            source: source.clone(),
            runtime_lock_sha256: args.runtime_lock_sha256.clone(),
        };
        store::fresh_json(
            &store
                .root
                .join(format!("native-job-{}.binding.json", args.attempt_id)),
            &binding,
        )
        .map_err(Error::uncertain)?;
        Ok(serde_json::to_value(JobView {
            job_ref: args.attempt_id.clone(),
            state: "running".into(),
            fingerprint: source.fingerprint,
            runtime_lock_sha256: args.runtime_lock_sha256,
            directory: format!("render-{}", args.attempt_id),
            receipt: None,
            automatic_retry: false,
        })?)
    }
    fn binding(&self, id: &str, context: &DriverExecutionContext) -> Result<JobBinding> {
        Self::validate_attempt(id)?;
        let binding: JobBinding = serde_json::from_slice(&store::regular(
            &self
                .project()?
                .root
                .join(format!("native-job-{id}.binding.json")),
            1024 * 1024,
        )?)?;
        if binding.schema != "semwright.hyperframes.job-binding/1"
            || binding.attempt_id != id
            || binding.session_sha256 != model::sha(context.session().as_bytes())
        {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Native job is not bound to this original owner session",
            ));
        }
        binding.job.validate()?;
        Ok(binding)
    }
    fn receipt(&self, binding: &JobBinding) -> Result<NativeReceipt> {
        let root = self
            .output()?
            .join(format!("render-{}", binding.attempt_id));
        let receipt: NativeReceipt = serde_json::from_slice(&store::regular(
            &root.join("native-render-receipt.json"),
            512 * 1024,
        )?)?;
        let source = &binding.source;
        let lock_bytes =
            store::regular(&self.runtime()?.join("runtime-lock.json"), 8 * 1024 * 1024)?;
        if model::sha(&lock_bytes) != binding.runtime_lock_sha256 {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Original HyperFrames runtime lock changed",
            ));
        }
        let lock: store::RuntimeLock = serde_json::from_slice(&lock_bytes)?;
        lock.validate()?;
        if receipt.versions.node != lock.node_version
            || receipt.versions.hyperframes != model::HYPERFRAMES_VERSION
            || receipt.versions.browser != lock.browser_version
            || receipt.versions.ffmpeg != lock.ffmpeg_version
            || receipt.versions.ffprobe != lock.ffprobe_version
        {
            return Err(Error::new(
                ErrorCode::ProtocolMismatch,
                "Native executable versions differ from the pinned runtime",
            ));
        }
        if receipt.schema != "semwright.hyperframes.native-render/1"
            || receipt.status != "completed"
            || receipt.attempt_id != binding.attempt_id
            || receipt.fingerprint != source.fingerprint
            || receipt.source_sha256 != source.source_sha256
            || receipt.html_sha256 != source.html_sha256
            || receipt.runtime_lock_sha256 != binding.runtime_lock_sha256
            || receipt.hyperframes_version != model::HYPERFRAMES_VERSION
            || receipt.hyperframes_commit != model::HYPERFRAMES_COMMIT
            || receipt.frame_count != source.composition.frame_count
            || receipt.fps != 30
            || receipt.frames.len() != receipt.frame_count as usize
            || !receipt.source_unchanged
            || receipt.pixel_decoding_performed
            || receipt.automatic_retry
            || receipt.native_command.exit_code != Some(0)
            || receipt.native_command.signal.is_some()
            || !receipt.native_command.capture_complete
            || receipt.native_command.automatic_retry
        {
            return Err(Error::new(
                ErrorCode::ProtocolMismatch,
                "Native HyperFrames receipt does not bind the original completed command",
            ));
        }
        let stdout = store::regular(&root.join("native-command.stdout"), 4 * 1024 * 1024)?;
        let stderr = store::regular(&root.join("native-command.stderr"), 4 * 1024 * 1024)?;
        if stdout.len() as u64 != receipt.native_command.stdout_bytes
            || model::sha(&stdout) != receipt.native_command.stdout_sha256
            || stderr.len() as u64 != receipt.native_command.stderr_bytes
            || model::sha(&stderr) != receipt.native_command.stderr_sha256
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native HyperFrames command originals changed",
            ));
        }
        let mut total = 0u64;
        for (index, frame) in receipt.frames.iter().enumerate() {
            if frame.index != index as u32
                || frame.file != format!("frames/frame_{:06}.png", index + 1)
                || !store::relative(&frame.file)
                || !model::sha_valid(&frame.sha256)
                || frame.bytes == 0
                || frame.bytes > 16 * 1024 * 1024
                || frame.width != source.composition.width
                || frame.height != source.composition.height
            {
                return Err(Error::new(
                    ErrorCode::ProtocolMismatch,
                    "Native HyperFrames frame inventory invalid",
                ));
            }
            total = total
                .checked_add(frame.bytes)
                .ok_or_else(|| Error::invalid("Native artifact byte count overflow"))?;
            if total > 256 * 1024 * 1024 {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "Native HyperFrames output exceeds byte budget",
                ));
            }
            let bytes = store::regular(&root.join(&frame.file), frame.bytes)?;
            if bytes.len() as u64 != frame.bytes || model::sha(&bytes) != frame.sha256 {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Native HyperFrames PNG original changed",
                ));
            }
        }
        // Re-read original source; a successful process is insufficient if the
        // input drifted while it executed. Existing versioned bundles are kept.
        let original = self.project()?.bundle(&source.fingerprint)?;
        if model::sha(&store::regular(
            &original.join("index.html"),
            model::MAX_SOURCE_BYTES as u64,
        )?) != source.html_sha256
            || model::sha(&store::regular(
                &original.join("semwright-hyperframes.json"),
                model::MAX_SOURCE_BYTES as u64,
            )?) != source.source_sha256
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Original native HyperFrames source changed",
            ));
        }
        Ok(receipt)
    }
    async fn status(&self, args: JobArgs, context: &DriverExecutionContext) -> Result<Value> {
        let binding = self.binding(&args.job_ref, context)?;
        let status = context.runtime_tool_job_status(&binding.job).await?;
        let (state, receipt) = match status {
            RuntimeToolJobStatus::Running => ("running", None),
            RuntimeToolJobStatus::Cancelling => ("cancelling", None),
            RuntimeToolJobStatus::Cancelled => ("cancelled", None),
            RuntimeToolJobStatus::Failed { .. } => ("unknown", None),
            RuntimeToolJobStatus::Succeeded { output } if output.exit_code == 0 => {
                ("succeeded", Some(self.receipt(&binding)?))
            }
            RuntimeToolJobStatus::Succeeded { .. } => ("failed", None),
        };
        Ok(serde_json::to_value(JobView {
            job_ref: args.job_ref,
            state: state.into(),
            fingerprint: binding.source.fingerprint,
            runtime_lock_sha256: binding.runtime_lock_sha256,
            directory: format!("render-{}", binding.attempt_id),
            receipt,
            automatic_retry: false,
        })?)
    }
}
#[async_trait]
impl Driver for HyperframesDriver {
    fn id(&self) -> &str {
        ID
    }
    fn version(&self) -> &str {
        env!("CARGO_PKG_VERSION")
    }
    fn interfaces(&self) -> DriverInterfaces {
        DriverInterfaces {
            health: true,
            host_tools: true,
            ..Default::default()
        }
    }
    async fn capabilities(&mut self) -> Result<Vec<Capability>> {
        Self::catalog()
    }
    async fn execute(&mut self, command: &str, descriptor: &str, args: Value) -> Result<Value> {
        Self::authorize_descriptor(command, descriptor)?;
        match command {
            "driver.hyperframes.doctor" => {
                let args: PinArgs = Self::parse(args)?;
                let lock = store::runtime(self.runtime()?, &args.runtime_lock_sha256)?;
                let _ = self.project()?;
                let _ = self.output()?;
                Ok(serde_json::to_value(Doctor {
                    provider: SCOPE.into(),
                    hyperframes_version: model::HYPERFRAMES_VERSION.into(),
                    hyperframes_commit: model::HYPERFRAMES_COMMIT.into(),
                    runtime_lock_sha256: args.runtime_lock_sha256,
                    runtime_files_verified: lock.files.len(),
                    native_execution_performed: false,
                    implicit_installation: false,
                })?)
            }
            "driver.hyperframes.project.inspect" => {
                let _: Empty = Self::parse(args)?;
                Ok(serde_json::to_value(Inspection {
                    source: self.project()?.current()?,
                    native_execution_performed: false,
                })?)
            }
            "driver.hyperframes.project.plan" => {
                let args: PlanArgs = Self::parse(args)?;
                let compiled = model::compile(&args.composition)?;
                let _ = store::runtime(self.runtime()?, &args.runtime_lock_sha256)?;
                let _ = self.project()?;
                let _ = self.output()?;
                Ok(serde_json::to_value(Plan {
                    source: compiled.inventory(),
                    runtime_lock_sha256: args.runtime_lock_sha256,
                    runnable: true,
                    dependencies_verified: true,
                    implicit_installation: false,
                })?)
            }
            "driver.hyperframes.project.apply" => {
                let args: ApplyArgs = Self::parse(args)?;
                let compiled = model::compile(&args.composition)?;
                let source = if args.dry_run {
                    let current = self.project()?.current()?;
                    if current.as_ref().map(|s| s.fingerprint.as_str())
                        != args.expected_fingerprint.as_deref()
                    {
                        return Err(Error::new(ErrorCode::Conflict, "Native source changed"));
                    }
                    compiled.inventory()
                } else {
                    self.project()?
                        .apply(&compiled, args.expected_fingerprint.as_deref())?
                };
                Ok(serde_json::to_value(Applied {
                    source,
                    applied: !args.dry_run,
                    dry_run: args.dry_run,
                })?)
            }
            _ => Err(Error::new(
                ErrorCode::Unsupported,
                "HyperFrames native execution requires the original Driver Host context",
            )),
        }
    }
    async fn execute_with_context(
        &mut self,
        command: &str,
        descriptor: &str,
        args: Value,
        context: DriverExecutionContext,
    ) -> Result<Value> {
        Self::authorize_descriptor(command, descriptor)?;
        context.check_cancelled()?;
        match command {
            "driver.hyperframes.render.start" => self.start(Self::parse(args)?, &context).await,
            "driver.hyperframes.render.status" | "driver.hyperframes.render.result" => {
                self.status(Self::parse(args)?, &context).await
            }
            _ => self.execute(command, descriptor, args).await,
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn no_host_context_cannot_start_or_replay_native_mutation() {
        let mut driver = HyperframesDriver {
            project: None,
            runtime: None,
            output: None,
        };
        let caps = driver.capabilities().await.unwrap();
        let start = caps
            .iter()
            .find(|c| c.descriptor.name == "driver.hyperframes.render.start")
            .unwrap();
        let result = driver
            .execute(
                &start.descriptor.name,
                &descriptor_digest(&start.descriptor).unwrap(),
                json!({}),
            )
            .await;
        assert!(result.is_err());
    }
    #[tokio::test]
    async fn missing_grants_and_malformed_descriptor_refuse_before_execution() {
        let mut driver = HyperframesDriver {
            project: None,
            runtime: None,
            output: None,
        };
        let caps = driver.capabilities().await.unwrap();
        let inspect = caps
            .iter()
            .find(|c| c.descriptor.name == "driver.hyperframes.project.inspect")
            .unwrap();
        let error = driver
            .execute(
                &inspect.descriptor.name,
                &descriptor_digest(&inspect.descriptor).unwrap(),
                json!({}),
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::PermissionDenied);
        assert!(
            driver
                .execute(&inspect.descriptor.name, &"0".repeat(64), json!({}))
                .await
                .is_err()
        );
    }
}
