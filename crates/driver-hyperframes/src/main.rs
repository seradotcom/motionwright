//! HyperFrames native profile through the existing Semwright Driver Host.
//! Logical attempts survive process restart; unknown outcome never restarts source execution.
use async_trait::async_trait;
use motionwright_hyperframes_driver::{
    atomic_json, file_sha, hex_digest, invalid, read, regular, root, sha, token, write_new,
};
use motionwright_hyperframes_profile::{
    HYPERFRAMES_VERSION, HyperframesPlan, MAX_DOCUMENT_BYTES, compile_html, validate_plan,
};
use semwright_driver_sdk::{
    Capability, Driver, DriverExecutionContext, DriverInterfaces, RuntimeToolArg, RuntimeToolCwd,
    RuntimeToolJob, RuntimeToolJobStatus, descriptor_digest, serve, workspace_mount,
};
use semwright_types::{CommandDescriptor, Error, ErrorCode, Idempotency, Result, Risk};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{fs, path::PathBuf, time::Duration};
use uuid::Uuid;
const ID: &str = "hyperframes";
const WORK: &str = "hyperframes-work";
const OUTPUT: &str = "hyperframes-output";
const RUNTIME: &str = "hyperframes-runtime";
const ASSETS: &str = "hyperframes-assets";
const SCOPE: &str = "driver:hyperframes";
const MAX_RETAINED: usize = 256;
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StartArgs {
    attempt_id: Uuid,
    plan: HyperframesPlan,
    expected_source_sha256: String,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct JobArgs {
    job_ref: String,
}
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum Phase {
    Starting,
    Rendering,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
    Unknown,
}
impl Phase {
    fn terminal(self) -> bool {
        matches!(self, Self::Succeeded | Self::Failed | Self::Cancelled)
    }
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    schema: u32,
    job_ref: String,
    plan_sha256: String,
    source_sha256: String,
    phase: Phase,
    host_job: Option<RuntimeToolJob>,
    result: Option<Value>,
    diagnostic: Option<String>,
}
struct HyperframesDriver {
    work: PathBuf,
    output: PathBuf,
    runtime: PathBuf,
    capabilities: Vec<Capability>,
}
impl HyperframesDriver {
    fn new() -> Result<Self> {
        Ok(Self {
            work: root(&workspace_mount(WORK)?)?,
            output: root(&workspace_mount(OUTPUT)?)?,
            runtime: root(&workspace_mount(RUNTIME)?)?,
            capabilities: catalog(),
        })
    }
    fn load(&self, id: &str) -> Result<Journal> {
        token(id)?;
        let bytes = read(&self.work, &format!("{id}/job.json"), 64 * 1024)?;
        let job: Journal = serde_json::from_slice(&bytes)?;
        if job.schema != 1
            || job.job_ref != id
            || !hex_digest(&job.plan_sha256)
            || !hex_digest(&job.source_sha256)
        {
            return Err(invalid("Driver journal identity differs"));
        }
        Ok(job)
    }
    fn save(&self, job: &Journal) -> Result<()> {
        token(&job.job_ref)?;
        atomic_json(&self.work.join(&job.job_ref).join("job.json"), job)
    }
    fn view(job: &Journal) -> Value {
        json!({"job_ref":job.job_ref,"state":job.phase,"plan_sha256":job.plan_sha256,"source_sha256":job.source_sha256,"result":job.result,"diagnostic":job.diagnostic})
    }
    fn doctor(&self) -> Value {
        let receipt = read(&self.runtime, "runtime.json", 1024 * 1024)
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok());
        json!({"driver":ID,"profile":"hyperframes-core-firefox-png-v1","runtime_version_contract":HYPERFRAMES_VERSION,
            "runtime_receipt_present":receipt.is_some(),"configured_hyperframes":receipt.as_ref().and_then(|v|v["hyperframes"].as_str()),
            "network":false,"arbitrary_source_execution":false,"source_preserved":true,"retained_jobs_limit":MAX_RETAINED,
            "host_tools":std::env::var_os("SEMWRIGHT_DRIVER_HOST_TOOLS").is_some(),"admission":"runtime bytes, assets and fonts are reverified during render; this diagnostic does not certify pixels"})
    }
    fn verify_descriptor(&self, command: &str, digest: &str) -> Result<()> {
        let descriptor = &self
            .capabilities
            .iter()
            .find(|c| c.descriptor.name == command)
            .ok_or_else(|| Error::new(ErrorCode::NotFound, "Unknown HyperFrames capability"))?
            .descriptor;
        if descriptor_digest(descriptor)? != digest {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "HyperFrames descriptor changed",
            ));
        }
        Ok(())
    }
    async fn start(&mut self, args: Value, context: &DriverExecutionContext) -> Result<Value> {
        context.check_cancelled()?;
        let args: StartArgs = serde_json::from_value(args)?;
        validate_plan(&args.plan).map_err(|e| invalid(e.to_string()))?;
        let source = compile_html(&args.plan.document).map_err(|e| invalid(e.to_string()))?;
        if sha(source.as_bytes()) != args.expected_source_sha256 {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Expected native source does not match the current document",
            ));
        }
        let bytes = serde_json::to_vec(&args.plan)?;
        if bytes.len() > MAX_DOCUMENT_BYTES {
            return Err(invalid("Native plan exceeds its source budget"));
        }
        let plan_sha256 = sha(&bytes);
        let id = format!("hf-{}", args.attempt_id.simple());
        if self.work.join(&id).exists() {
            let existing = self.load(&id)?;
            if existing.plan_sha256 != plan_sha256
                || existing.source_sha256 != args.expected_source_sha256
            {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Logical attempt identity was reused for a different source",
                ));
            }
            return self.refresh(&id, context).await;
        }
        if fs::read_dir(&self.work)?
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().starts_with("hf-"))
            .take(MAX_RETAINED + 1)
            .count()
            >= MAX_RETAINED
        {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Retained native attempt budget is full; archive terminal attempts explicitly",
            ));
        }
        // Write intent before dispatch. A restart in the dispatch/persist gap yields UNKNOWN, not a duplicate render.
        fs::create_dir(self.work.join(&id))?;
        fs::create_dir(self.output.join(&id))?;
        write_new(&self.work.join(&id).join("plan.json"), &bytes)?;
        write_new(&self.work.join(&id).join("index.html"), source.as_bytes())?;
        let mut job = Journal {
            schema: 1,
            job_ref: id.clone(),
            plan_sha256,
            source_sha256: args.expected_source_sha256,
            phase: Phase::Starting,
            host_job: None,
            result: None,
            diagnostic: None,
        };
        self.save(&job)?;
        let literal = |value: &str| RuntimeToolArg::Literal {
            value: value.into(),
        };
        let mount = |name: &str| RuntimeToolArg::MountPath {
            mount: name.into(),
            relative: String::new(),
        };
        let result = context
            .start_runtime_tool_job_args(
                "hyperframes-runner",
                vec![
                    literal("render"),
                    literal("--runtime-root"),
                    mount(RUNTIME),
                    literal("--node-sealed"),
                    RuntimeToolArg::ToolPath {
                        tool: "node".into(),
                    },
                    literal("--ffmpeg-sealed"),
                    RuntimeToolArg::ToolPath {
                        tool: "ffmpeg".into(),
                    },
                    literal("--work-root"),
                    mount(WORK),
                    literal("--output-root"),
                    mount(OUTPUT),
                    literal("--assets-root"),
                    mount(ASSETS),
                    literal("--job"),
                    literal(&id),
                    literal("--plan-sha256"),
                    literal(&job.plan_sha256),
                    literal("--source-sha256"),
                    literal(&job.source_sha256),
                ],
                vec![],
                Duration::from_secs(300),
                Some(RuntimeToolCwd {
                    mount: WORK.into(),
                    relative: id.clone(),
                }),
            )
            .await;
        match result {
            Ok(host) => {
                job.host_job = Some(host);
                job.phase = Phase::Rendering;
            }
            Err(error) => {
                job.phase = Phase::Unknown;
                job.diagnostic = Some(format!(
                    "Dispatch outcome requires reconciliation: {:?}",
                    error.code
                ));
                self.save(&job)?;
                return Err(error);
            }
        }
        self.save(&job)?;
        Ok(Self::view(&job))
    }
    fn verify_result(&self, job: &Journal) -> Result<Value> {
        let directory = root(&self.output.join(&job.job_ref))?;
        let bytes = read(&directory, "result.json", 64 * 1024)?;
        let mut result: Value = serde_json::from_slice(&bytes)?;
        if result["schema"] != "motionwright.hyperframes-runtime-result/1"
            || result["plan_sha256"] != job.plan_sha256
            || result["source_sha256"] != job.source_sha256
        {
            return Err(invalid("Native result is not bound to its admitted source"));
        }
        for key in ["frames", "mezzanine", "source", "document", "observations"] {
            let item = &result[key];
            let relative = item["relative_path"]
                .as_str()
                .ok_or_else(|| invalid("Missing native artifact locator"))?
                .to_owned();
            let (file, size) = regular(&directory, &relative, 512 * 1024 * 1024)?;
            if item["bytes"] != size || item["sha256"] != file_sha(&file, 512 * 1024 * 1024)? {
                return Err(invalid("Native artifact digest/size mismatch"));
            }
            result[key]["relative_path"] = json!(format!("{}/{relative}", job.job_ref));
        }
        // Result locators are confined references, never a transfer or execution grant.
        Ok(result)
    }
    async fn refresh(&self, id: &str, context: &DriverExecutionContext) -> Result<Value> {
        let mut job = self.load(id)?;
        if job.phase == Phase::Succeeded {
            job.result = Some(self.verify_result(&job)?);
            return Ok(Self::view(&job));
        }
        if job.phase.terminal() {
            return Ok(Self::view(&job));
        }
        let Some(host) = &job.host_job else {
            job.phase = Phase::Unknown;
            job.diagnostic=Some("Attempt was recorded but dispatch result is unknown; automatic resubmission is prohibited".into());
            self.save(&job)?;
            return Ok(Self::view(&job));
        };
        match context.runtime_tool_job_status(host).await {
            Ok(RuntimeToolJobStatus::Running) => job.phase = Phase::Rendering,
            Ok(RuntimeToolJobStatus::Cancelling) => job.phase = Phase::Cancelling,
            Ok(RuntimeToolJobStatus::Cancelled) => job.phase = Phase::Cancelled,
            Ok(RuntimeToolJobStatus::Failed { error }) => {
                job.phase = Phase::Failed;
                job.diagnostic = Some(format!(
                    "{:?}: {}",
                    error.code,
                    error.message.chars().take(1200).collect::<String>()
                ));
            }
            Ok(RuntimeToolJobStatus::Succeeded { output }) => {
                if output.exit_code != 0 {
                    job.phase = Phase::Failed;
                    job.diagnostic = Some(format!(
                        "Native runtime exited {}: {}",
                        output.exit_code,
                        String::from_utf8_lossy(&output.stderr)
                            .chars()
                            .take(1200)
                            .collect::<String>()
                    ));
                } else {
                    match self.verify_result(&job) {
                        Ok(result) => {
                            job.result = Some(result);
                            job.phase = Phase::Succeeded;
                        }
                        Err(error) => {
                            job.phase = Phase::Failed;
                            job.diagnostic = Some(error.to_string());
                        }
                    }
                }
            }
            Err(error) => {
                job.phase = Phase::Unknown;
                job.diagnostic = Some(format!(
                    "Host outcome unavailable ({:?}); logical attempt is not resubmitted",
                    error.code
                ));
            }
        }
        self.save(&job)?;
        Ok(Self::view(&job))
    }
    async fn cancel(&self, id: &str, context: &DriverExecutionContext) -> Result<Value> {
        let mut job = self.load(id)?;
        if job.phase.terminal() {
            return Ok(Self::view(&job));
        }
        let Some(host) = &job.host_job else {
            return self.refresh(id, context).await;
        };
        let result = context.cancel_runtime_tool_job(host).await;
        match result {
            Ok(RuntimeToolJobStatus::Cancelled) => job.phase = Phase::Cancelled,
            Ok(_) => job.phase = Phase::Cancelling,
            Err(error) => {
                job.phase = Phase::Unknown;
                job.diagnostic = Some(format!("Cancellation outcome unknown: {:?}", error.code));
            }
        }
        self.save(&job)?;
        Ok(Self::view(&job))
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
            cooperative_cancellation: true,
            health: true,
            host_tools: std::env::var_os("SEMWRIGHT_DRIVER_HOST_TOOLS").is_some(),
            ..Default::default()
        }
    }
    async fn capabilities(&mut self) -> Result<Vec<Capability>> {
        Ok(self.capabilities.clone())
    }
    async fn execute(&mut self, command: &str, digest: &str, args: Value) -> Result<Value> {
        self.verify_descriptor(command, digest)?;
        if command == "driver.hyperframes.doctor" && args.as_object().is_some_and(|v| v.is_empty())
        {
            return Ok(self.doctor());
        }
        Err(Error::new(
            ErrorCode::Unsupported,
            "Native capture requires Driver Host execution context",
        ))
    }
    async fn execute_with_context(
        &mut self,
        command: &str,
        digest: &str,
        args: Value,
        context: DriverExecutionContext,
    ) -> Result<Value> {
        self.verify_descriptor(command, digest)?;
        context.check_cancelled()?;
        if command == "driver.hyperframes.doctor" {
            return self.execute(command, digest, args).await;
        }
        if command == "driver.hyperframes.render.start" {
            return self.start(args, &context).await;
        }
        let args: JobArgs = serde_json::from_value(args)?;
        match command {
            "driver.hyperframes.render.status" => self.refresh(&args.job_ref, &context).await,
            "driver.hyperframes.render.cancel" => self.cancel(&args.job_ref, &context).await,
            "driver.hyperframes.render.result" => {
                let result = self.refresh(&args.job_ref, &context).await?;
                if result["state"] != "succeeded" {
                    return Err(Error::new(
                        ErrorCode::Conflict,
                        "Native result is not verified and terminal; inspect its status",
                    ));
                }
                Ok(result)
            }
            _ => Err(Error::new(
                ErrorCode::NotFound,
                "Unknown HyperFrames operation",
            )),
        }
    }
    async fn health(&mut self) -> Result<Value> {
        Ok(
            json!({"healthy":self.work.is_dir()&&self.output.is_dir(),"runtime_version_contract":HYPERFRAMES_VERSION}),
        )
    }
}
fn plan_schema() -> Value {
    json!({"type":"object","properties":{
    "project_id":{"type":"string","format":"uuid"},"generation":{"type":"string","format":"uuid"},"revision":{"type":"integer","minimum":0},"scene_id":{"type":"string","format":"uuid"},
    "document":{"type":"object","properties":{"version":{"const":1},"canvas":{"type":"object"},"camera":{"type":"object"},"nodes":{"type":"array","minItems":1,"maxItems":256,"items":{"type":"object"}},"assets":{"type":"array","maxItems":64,"items":{"type":"object"}}},"required":["version","canvas","nodes"],"additionalProperties":false}
},"required":["project_id","generation","revision","scene_id","document"],"additionalProperties":false})
}
fn catalog() -> Vec<Capability> {
    let start = json!({"type":"object","properties":{"attempt_id":{"type":"string","format":"uuid"},"plan":plan_schema(),"expected_source_sha256":{"type":"string","pattern":"^[a-f0-9]{64}$"}},"required":["attempt_id","plan","expected_source_sha256"],"additionalProperties":false});
    let job = json!({"type":"object","properties":{"job_ref":{"type":"string","pattern":"^hf-[0-9a-f]{32}$"}},"required":["job_ref"],"additionalProperties":false});
    let output = json!({"type":"object","properties":{"job_ref":{"type":"string"},"state":{"enum":["starting","rendering","cancelling","succeeded","failed","cancelled","unknown"]},"plan_sha256":{"type":"string"},"source_sha256":{"type":"string"},"result":{"type":["object","null"]},"diagnostic":{"type":["string","null"]}},"required":["job_ref","state","plan_sha256","source_sha256","result","diagnostic"],"additionalProperties":false});
    [
        ("doctor","Inspect the configured native HTML capture contract, not creative quality",json!({"type":"object","properties":{},"additionalProperties":false}),json!({"type":"object"}),Risk::ReadOnly,Idempotency::ReadOnly),
        ("render.start","Capture a typed renderer-native HTML composition with preserved source, alpha, frame evidence and logical-attempt deduplication",start,output.clone(),Risk::MutatingReversible,Idempotency::Idempotent),
        ("render.status","Reconcile a recorded logical attempt without automatically resubmitting unknown outcomes",job.clone(),output.clone(),Risk::ReadOnly,Idempotency::ReadOnly),
        ("render.cancel","Cancel the Host-owned native capture attempt; unknown results remain unknown",job.clone(),output.clone(),Risk::MutatingReversible,Idempotency::Idempotent),
        ("render.result","Return digest-verified renderer-native source, frames, observations and FFV1 RGBA mezzanine",job,output,Risk::ReadOnly,Idempotency::ReadOnly),
    ].into_iter().map(|(name,description,input_schema,output_schema,risk,idempotency)|Capability{
        descriptor:CommandDescriptor{name:format!("driver.hyperframes.{name}"),version:"1".into(),description:description.into(),input_schema,output_schema,requires:vec![SCOPE.into()],risk,idempotency,timeout_ms:300_000,dry_run:name!="render.start"&&name!="render.cancel",interactive_consent:false,backends:vec![SCOPE.into()]},
        aliases:vec![],tags:vec!["hyperframes".into(),"native-html".into(),"render".into()],object_types:vec!["hyperframes-composition".into()],
    }).collect()
}
#[tokio::main]
async fn main() {
    let result = match HyperframesDriver::new() {
        Ok(driver) => serve(driver).await,
        Err(e) => Err(e),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(error.exit_code());
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capability_catalog_is_distinct_and_never_grants_source_execution() {
        let caps = catalog();
        assert_eq!(caps.len(), 5);
        for cap in caps {
            assert!(cap.descriptor.name.starts_with("driver.hyperframes."));
            assert!(
                !cap.descriptor
                    .input_schema
                    .to_string()
                    .contains("executable")
            );
            assert!(
                !cap.descriptor
                    .input_schema
                    .to_string()
                    .contains("command_line")
            );
        }
    }
    #[test]
    fn duplicate_attempt_and_unknown_state_are_persisted_data() {
        let dir = tempfile::tempdir().unwrap();
        let id = "hf-0123456789abcdef0123456789abcdef";
        fs::create_dir(dir.path().join(id)).unwrap();
        let driver = HyperframesDriver {
            work: dir.path().into(),
            output: dir.path().into(),
            runtime: dir.path().into(),
            capabilities: catalog(),
        };
        let job = Journal {
            schema: 1,
            job_ref: id.into(),
            plan_sha256: "ab".repeat(32),
            source_sha256: "cd".repeat(32),
            phase: Phase::Unknown,
            host_job: None,
            result: None,
            diagnostic: Some("Reconciliation required".into()),
        };
        driver.save(&job).unwrap();
        assert_eq!(driver.load(id).unwrap().phase, Phase::Unknown);
        assert!(!driver.load(id).unwrap().phase.terminal());
    }
}
