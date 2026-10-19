//! Read-only DriverHost observations over two pinned SDK fixtures.
//!
//! Compile this source as a separate libtest against the unchanged SDK libraries.
//! The caller binds the complete SDK checkout, compiler, libraries and executable.
//! This file records API Values; it does not claim original JSON-RPC wire bytes,
//! rendering, Node execution, seek fidelity, four-phase production or acceptance.

#[cfg(not(target_os = "linux"))]
compile_error!("This fixture requires the original Linux DriverHost sandbox.");

use semwright_backend_api::{Context, ProvidedCapability, Provider};
use semwright_driver_host::DriverProvider;
use semwright_driver_motion_canvas::{
    model::Project,
    refs::{Kind, Reference},
};
use semwright_driver_sdk::{
    ApplicationMatch, Capability, DriverInterfaces, DriverMount, DriverResources, Manifest,
    Transport, capabilities_digest, descriptor_digest,
};
use semwright_policy::FilesystemGrant;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fmt::Display,
    fs::{self, DirBuilder, File, Metadata, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Component, Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio_util::sync::CancellationToken;

const SDK_SHA: &str = "54d3459a051adbce4cae9f60c0dea8f60c824be6";
const SDK_TREE: &str = "984066e477dd71e0f40dde6809bf431a2705aa4e";
const SDK_LOCK: &str = "aff7a06615d18d9ae155c4afa9182c22210340435afba9bea72f4c4b493b895e";
const CATALOG_DIGEST: &str = "d7a1bc53c1cc25bfb839edbb99b81c03bf720441f050e86144fd6c9aa672a5a5";
const MAX_FIXTURE: u64 = 16 * 1024;
const MAX_EXECUTABLE: u64 = 512 * 1024 * 1024;
const MAX_SOURCE: u64 = 256 * 1024;
const MAX_VALUE: usize = 4 * 1024 * 1024;
const MAX_REPORT: usize = 64 * 1024 * 1024;
const CALL_TIMEOUT: Duration = Duration::from_secs(15);
const SEMANTIC: Input = Input {
    label: "semantic",
    path: "fixtures/motion-canvas/semantic-complete/semwright-motion.json",
    bytes: 9643,
    sha256: "3e98c414762f8187463b3dd0bc4ebed13fdea8f2879e63d0db015ec6efad2059",
    project_id: "semantic-complete",
    generation: "11111111111111111111111111111111",
};
const CUES: Input = Input {
    label: "cues",
    path: "fixtures/motion-canvas/audio-cues/semwright-motion.json",
    bytes: 1760,
    sha256: "3815bf836331dd8f5aae01d4f463d699329b20568310cd5005fa16138e944cc5",
    project_id: "audio-cues",
    generation: "617564696f2d63756573000000000000",
};
const CALLS: [(&str, &str); 11] = [
    ("semantic", "driver.motion-canvas.doctor"),
    ("semantic", "driver.motion-canvas.semantic.types"),
    ("semantic", "driver.motion-canvas.semantic.describe"),
    ("semantic", "driver.motion-canvas.node.list"),
    ("semantic", "driver.motion-canvas.node.property.get"),
    ("semantic", "driver.motion-canvas.render.plan"),
    ("semantic", "driver.motion-canvas.doctor"),
    ("cues", "driver.motion-canvas.doctor"),
    ("cues", "driver.motion-canvas.scene.list"),
    ("cues", "driver.motion-canvas.cue.list"),
    ("cues", "driver.motion-canvas.doctor"),
];

#[derive(Clone, Copy)]
struct Input {
    label: &'static str,
    path: &'static str,
    bytes: u64,
    sha256: &'static str,
    project_id: &'static str,
    generation: &'static str,
}

#[derive(Debug)]
struct Failure {
    state: &'static str,
    message: String,
}

type Checked<T> = Result<T, Failure>;

fn failed(error: impl Display) -> Failure {
    Failure {
        state: "FAILED",
        message: error.to_string(),
    }
}

fn unknown(error: impl Display) -> Failure {
    Failure {
        state: "UNKNOWN",
        message: error.to_string(),
    }
}

fn sdk_error(error: semwright_types::Error) -> Failure {
    if error.code == semwright_types::ErrorCode::Timeout {
        unknown(format!("{error:?}"))
    } else {
        failed(format!("{error:?}"))
    }
}

fn ensure(condition: bool, reason: &str) -> Checked<()> {
    if condition {
        Ok(())
    } else {
        Err(failed(reason))
    }
}

fn epoch_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

struct BoundedJson {
    bytes: Vec<u8>,
    cap: usize,
}

impl Write for BoundedJson {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        if data.len() > self.cap.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other(
                "serialized evidence exceeds its budget",
            ));
        }
        self.bytes.extend_from_slice(data);
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn json_bytes(value: &Value, cap: usize) -> Checked<Vec<u8>> {
    let mut output = BoundedJson {
        bytes: Vec::new(),
        cap,
    };
    serde_json::to_writer(&mut output, value).map_err(failed)?;
    Ok(output.bytes)
}

fn token(metadata: &Metadata) -> Value {
    json!({
        "device": metadata.dev(), "inode": metadata.ino(), "size": metadata.len(),
        "mode": metadata.mode(), "uid": metadata.uid(), "gid": metadata.gid(),
        "nlink": metadata.nlink(), "mtime_seconds": metadata.mtime(),
        "mtime_nanoseconds": metadata.mtime_nsec(), "ctime_seconds": metadata.ctime(),
        "ctime_nanoseconds": metadata.ctime_nsec()
    })
}

fn checked_path(path: &Path, directory: bool) -> Checked<PathBuf> {
    ensure(path.is_absolute(), "an input path is not absolute")?;
    ensure(
        !path
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir)),
        "an input path contains relative components",
    )?;
    for ancestor in path.ancestors() {
        let meta = fs::symlink_metadata(ancestor).map_err(failed)?;
        ensure(!meta.file_type().is_symlink(), "symlink in an input path")?;
    }
    let meta = fs::symlink_metadata(path).map_err(failed)?;
    ensure(
        if directory {
            meta.is_dir()
        } else {
            meta.is_file()
        },
        "input is not the required regular file or directory",
    )?;
    let canonical = fs::canonicalize(path).map_err(failed)?;
    ensure(canonical == path, "input path is not canonical")?;
    Ok(canonical)
}

fn env_path(name: &str, directory: bool) -> Checked<PathBuf> {
    let path =
        PathBuf::from(std::env::var_os(name).ok_or_else(|| failed(format!("{name} is required")))?);
    checked_path(&path, directory)
}

fn env_new_directory(name: &str) -> Checked<PathBuf> {
    let path =
        PathBuf::from(std::env::var_os(name).ok_or_else(|| failed(format!("{name} is required")))?);
    ensure(path.is_absolute(), "new work path is not absolute")?;
    ensure(
        !path
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir)),
        "new work path contains relative components",
    )?;
    let parent = checked_path(
        path.parent()
            .ok_or_else(|| failed("new work path has no parent"))?,
        true,
    )?;
    let leaf = path
        .file_name()
        .ok_or_else(|| failed("new work path has no leaf"))?;
    ensure(
        parent.join(leaf) == path,
        "new work path has a noncanonical parent",
    )?;
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(path),
        Err(error) => Err(failed(error)),
        Ok(_) => Err(failed(
            "private work leaf must be absent before the fixture",
        )),
    }
}

fn inspect(path: &Path, cap: u64, retain: bool) -> Checked<(Value, Vec<u8>)> {
    checked_path(path, false)?;
    let before = fs::symlink_metadata(path).map_err(failed)?;
    ensure(
        before.len() <= cap,
        "file exceeds the declared input budget",
    )?;
    ensure(
        !retain || cap <= MAX_SOURCE,
        "in-memory file budget is not bounded",
    )?;
    let before_token = token(&before);
    let mut file = File::open(path).map_err(failed)?;
    ensure(
        token(&file.metadata().map_err(failed)?) == before_token,
        "file changed before open",
    )?;
    let mut digest = Sha256::new();
    let mut bytes = Vec::new();
    let mut total = 0_u64;
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        let remaining = before.len().saturating_sub(total);
        let wanted = (remaining + 1).min(chunk.len() as u64) as usize;
        let count = file.read(&mut chunk[..wanted]).map_err(failed)?;
        if count == 0 {
            break;
        }
        total += count as u64;
        ensure(
            total <= before.len() && total <= cap,
            "file grew during bounded read",
        )?;
        digest.update(&chunk[..count]);
        if retain {
            bytes.extend_from_slice(&chunk[..count]);
        }
    }
    ensure(total == before.len(), "file was truncated during read")?;
    ensure(
        token(&file.metadata().map_err(failed)?) == before_token,
        "open file changed during read",
    )?;
    ensure(
        token(&fs::symlink_metadata(path).map_err(failed)?) == before_token,
        "file path changed during read",
    )?;
    Ok((
        json!({
            "path": path, "bytes": total, "sha256": hex::encode(digest.finalize()),
            "identity": before_token
        }),
        bytes,
    ))
}

fn mkdir_new(path: &Path) -> Checked<()> {
    DirBuilder::new().mode(0o700).create(path).map_err(failed)?;
    checked_path(path, true)?;
    ensure(
        fs::metadata(path).map_err(failed)?.mode() & 0o077 == 0,
        "private directory permissions are not 0700",
    )?;
    Ok(())
}

fn write_new(path: &Path, bytes: &[u8], executable: bool) -> Checked<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(failed)?;
    file.write_all(bytes).map_err(failed)?;
    file.sync_all().map_err(failed)?;
    drop(file);
    if executable {
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(failed)?;
    }
    Ok(())
}

fn copy_driver(source: &Path, destination: &Path, expected: &Value) -> Checked<Value> {
    let mut input = File::open(source).map_err(failed)?;
    ensure(
        token(&input.metadata().map_err(failed)?) == expected["identity"],
        "driver changed before private copy",
    )?;
    let length = expected["bytes"]
        .as_u64()
        .ok_or_else(|| failed("driver size is missing"))?;
    ensure(
        length <= MAX_EXECUTABLE,
        "driver copy exceeds executable budget",
    )?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o700)
        .open(destination)
        .map_err(failed)?;
    let mut digest = Sha256::new();
    let mut total = 0_u64;
    let mut chunk = [0_u8; 64 * 1024];
    loop {
        let wanted = (length.saturating_sub(total) + 1).min(chunk.len() as u64) as usize;
        let count = input.read(&mut chunk[..wanted]).map_err(failed)?;
        if count == 0 {
            break;
        }
        total += count as u64;
        ensure(total <= length, "driver grew during private copy")?;
        digest.update(&chunk[..count]);
        output.write_all(&chunk[..count]).map_err(failed)?;
    }
    output.sync_all().map_err(failed)?;
    drop(output);
    ensure(total == length, "driver copy was truncated")?;
    ensure(
        hex::encode(digest.finalize()) == expected["sha256"].as_str().unwrap_or(""),
        "driver bytes changed during copy",
    )?;
    ensure(
        token(&input.metadata().map_err(failed)?) == expected["identity"],
        "opened driver changed during copy",
    )?;
    ensure(
        token(&fs::symlink_metadata(source).map_err(failed)?) == expected["identity"],
        "driver path changed during copy",
    )?;
    let (record, _) = inspect(destination, MAX_EXECUTABLE, false)?;
    ensure(
        record["identity"]["nlink"].as_u64() == Some(1),
        "private driver is multiply linked",
    )?;
    ensure(
        record["sha256"] == expected["sha256"] && record["bytes"] == expected["bytes"],
        "private driver copy differs",
    )?;
    Ok(record)
}

fn project_snapshot(root: &Path) -> Checked<Value> {
    checked_path(root, true)?;
    let entries = fs::read_dir(root)
        .map_err(failed)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(failed)?;
    ensure(
        entries.len() == 1 && entries[0].file_name() == "semwright-motion.json",
        "project directory has unexpected entries",
    )?;
    let (record, _) = inspect(&root.join("semwright-motion.json"), MAX_FIXTURE, false)?;
    ensure(
        record["identity"]["nlink"].as_u64() == Some(1),
        "private fixture is multiply linked",
    )?;
    Ok(
        json!({"directory": root, "directory_identity": token(&fs::metadata(root).map_err(failed)?), "file": record}),
    )
}

struct Watched {
    path: PathBuf,
    cap: u64,
    before: Value,
}

struct Observation {
    document: Value,
    started: Instant,
    event_bytes: usize,
    connections: usize,
    completed_connections: usize,
    shutdowns: usize,
    attempted_calls: usize,
    completed_calls: usize,
    next_call: usize,
    uncertain: bool,
}

impl Observation {
    fn new(output: &Path) -> Self {
        Self {
            document: json!({
                "schema": "motionwright.motion-canvas-readonly-host.v1",
                "started_unix_ms": epoch_millis(), "terminal": "UNKNOWN",
                "sdk_source_expected": {"commit": SDK_SHA, "tree": SDK_TREE, "complete_checkout_binding": "REQUIRED_FROM_CALLER"},
                "fixture_source_role": "NEW_SEPARATE_LIBTEST",
                "output_directory": output, "inputs": [], "hosts": [], "calls": [], "events": [],
                "input_postcheck": [], "scope": {
                    "api_values_are_original_responses": true, "raw_json_rpc_wire_bytes": false,
                    "render_calls": 0, "node_tool_calls": 0, "mutation_capability_calls": 0,
                    "pixel_or_audio_verification": false, "seek_runtime_verification": false,
                    "four_phase_production": false, "installed_product": false,
                    "human_evaluation": false, "formal_acceptance": false, "release_acceptance": false,
                    "full_runtime_dependency_attestation": false,
                    "admission_requires_harness_exit_zero_and_caller_source_binding": true
                }
            }),
            started: Instant::now(),
            event_bytes: 0,
            connections: 0,
            completed_connections: 0,
            shutdowns: 0,
            attempted_calls: 0,
            completed_calls: 0,
            next_call: 0,
            uncertain: false,
        }
    }

    fn event(&mut self, kind: &str, data: Value) -> Checked<()> {
        let value = json!({
            "kind": kind, "unix_ms": epoch_millis(),
            "elapsed_millis": self.started.elapsed().as_millis(), "data": data
        });
        let bytes = json_bytes(&value, MAX_VALUE)?;
        ensure(
            bytes.len() <= MAX_REPORT.saturating_sub(self.event_bytes),
            "event evidence budget exhausted",
        )?;
        self.event_bytes += bytes.len();
        self.document["events"].as_array_mut().unwrap().push(value);
        let mut stdout = std::io::stdout().lock();
        stdout
            .write_all(b"MW_SDK_READONLY_EVENT ")
            .map_err(failed)?;
        stdout.write_all(&bytes).map_err(failed)?;
        stdout.write_all(b"\n").map_err(failed)?;
        stdout.flush().map_err(failed)?;
        Ok(())
    }

    fn host_mut(&mut self, index: usize) -> &mut Value {
        &mut self.document["hosts"].as_array_mut().unwrap()[index]
    }

    fn record_error(&mut self, error: &Failure) {
        if error.state == "UNKNOWN" {
            self.uncertain = true;
        }
        if self.document.get("first_error").is_none() {
            self.document["first_error"] = json!({"state": error.state, "message": error.message});
        }
    }
}

fn pinned_input(
    source: &Path,
    input: Input,
    report: &mut Observation,
    watched: &mut Vec<Watched>,
) -> Checked<(Vec<u8>, Project)> {
    let path = source.join(input.path);
    let (record, bytes) = inspect(&path, MAX_FIXTURE, true)?;
    ensure(
        record["bytes"] == input.bytes && record["sha256"] == input.sha256,
        "original fixture bytes differ from the SDK pin",
    )?;
    let project: Project = serde_json::from_slice(&bytes).map_err(failed)?;
    ensure(
        project.id == input.project_id
            && project.generation == input.generation
            && project.revision == 1,
        "fixture identity differs from its original",
    )?;
    ensure(
        project.assets.is_empty() && project.audio.is_empty(),
        "readonly fixture unexpectedly includes media",
    )?;
    let raw = String::from_utf8(bytes.clone()).map_err(failed)?;
    report.document["inputs"]
        .as_array_mut()
        .unwrap()
        .push(json!({
            "label": input.label, "relative_source_path": input.path, "record": record,
            "original_utf8": raw, "project_identity_before": {
                "id": project.id, "generation": project.generation, "revision": project.revision
            }
        }));
    watched.push(Watched {
        path,
        cap: MAX_FIXTURE,
        before: record,
    });
    Ok((bytes, project))
}

fn manifest(executable: PathBuf, digest: String) -> Manifest {
    Manifest {
        manifest_version: 1,
        protocol: 3,
        id: "motion-canvas".into(),
        version: "1.0.0".into(),
        publisher: "motionwright-ci-fixture".into(),
        executable,
        sha256: digest,
        application: ApplicationMatch {
            desktop_id: None,
            process_names: vec!["node".into()],
            supported_versions: vec![],
        },
        transport: Transport::StdioV1,
        mounts: vec![DriverMount {
            root: "project".into(),
            read_only: true,
            execute: false,
        }],
        system_config: vec![],
        secrets: vec![],
        tools: vec![],
        network: false,
        nvidia_gpu: false,
        loopback_port: None,
        resources: DriverResources {
            open_files: 128,
            processes: 32,
            cpu_seconds: 60,
            operation_cpu_seconds: 0,
            address_space_bytes: 1_073_741_824,
            file_size_bytes: 268_435_456,
        },
        request_timeout_ms: 10_000,
        interfaces: DriverInterfaces {
            cooperative_cancellation: true,
            progress: true,
            artifacts: true,
            health: true,
            ..DriverInterfaces::default()
        },
    }
}

async fn catalog(
    provider: &DriverProvider,
    host: &str,
    report: &mut Observation,
) -> Checked<(Vec<ProvidedCapability>, Value)> {
    report.event(
        "catalog_intent",
        json!({"host": host, "state": "UNKNOWN_UNTIL_OUTCOME"}),
    )?;
    let capabilities = tokio::time::timeout(CALL_TIMEOUT, Provider::capabilities(provider))
        .await
        .map_err(|_| unknown("capabilities timed out; no replay"))?
        .map_err(sdk_error)?;
    ensure(
        capabilities.len() == 33,
        "SDK capability count differs from the original golden",
    )?;
    let names = capabilities
        .iter()
        .map(|c| c.descriptor.name.as_str())
        .collect::<BTreeSet<_>>();
    ensure(names.len() == 33, "SDK capability names are not unique")?;
    let original = capabilities
        .iter()
        .map(|c| Capability {
            descriptor: c.descriptor.clone(),
            aliases: c.aliases.clone(),
            tags: c.tags.clone(),
            object_types: c.object_types.clone(),
        })
        .collect::<Vec<_>>();
    let digest = capabilities_digest(&original).map_err(sdk_error)?;
    ensure(
        digest == CATALOG_DIGEST,
        "SDK capability catalog differs from the original golden",
    )?;
    let value = serde_json::to_value(&original).map_err(failed)?;
    let serialized = json_bytes(&value, MAX_VALUE)?;
    let observation = json!({
        "original_sdk_digest": digest, "serialized_value_sha256": sha256(&serialized),
        "serialized_value_bytes": serialized.len(),
        "serialized_value_utf8": std::str::from_utf8(&serialized).map_err(failed)?,
        "capabilities": value
    });
    report.event(
        "catalog_outcome",
        json!({"host": host, "state": "RETURNED", "count": 33, "original_sdk_digest": digest}),
    )?;
    Ok((capabilities, observation))
}

async fn query(
    provider: &DriverProvider,
    capabilities: &[ProvidedCapability],
    host: &str,
    name: &str,
    args: Value,
    report: &mut Observation,
) -> Checked<Value> {
    ensure(
        report.next_call < CALLS.len() && CALLS[report.next_call] == (host, name),
        "query is outside the closed ordered fixture",
    )?;
    let matches = capabilities
        .iter()
        .filter(|c| c.descriptor.name == name)
        .collect::<Vec<_>>();
    ensure(
        matches.len() == 1,
        "query capability is missing or ambiguous",
    )?;
    let capability = matches[0];
    ensure(
        capability.descriptor.risk == semwright_types::Risk::ReadOnly
            && capability.descriptor.idempotency == semwright_types::Idempotency::ReadOnly,
        "selected descriptor is not read-only",
    )?;
    ensure(
        !Provider::closed(provider)
            .ok_or_else(|| failed("Host has no lifecycle token"))?
            .is_cancelled(),
        "Host disconnected before query",
    )?;
    let descriptor_hash = descriptor_digest(&capability.descriptor).map_err(sdk_error)?;
    let context = Context {
        session: format!("motionwright-sdk-readonly-{host}"),
        request_id: semwright_types::unique_id(),
        cancellation: CancellationToken::new(),
    };
    let call_index = report.document["calls"].as_array().unwrap().len();
    let request = json!({
        "ordinal": report.next_call + 1, "host": host, "command": name, "args": args,
        "session": context.session, "request_id": context.request_id,
        "descriptor": capability.descriptor, "descriptor_digest": descriptor_hash,
        "state": "UNKNOWN_UNTIL_OUTCOME"
    });
    report.document["calls"]
        .as_array_mut()
        .unwrap()
        .push(request.clone());
    report.event("execute_intent", request)?;
    report.next_call += 1;
    report.attempted_calls += 1;
    let result = tokio::time::timeout(
        CALL_TIMEOUT,
        Provider::execute(provider, &context, &capability.descriptor, &args),
    )
    .await;
    let value = match result {
        Ok(Ok(value)) => value,
        Ok(Err(error)) => {
            let error = sdk_error(error);
            report.record_error(&error);
            report.document["calls"][call_index]["state"] = json!(error.state);
            report.document["calls"][call_index]["error"] = json!(error.message);
            report.event(
                "execute_outcome",
                report.document["calls"][call_index].clone(),
            )?;
            return Err(error);
        }
        Err(_) => {
            let error = unknown("query timed out; cancellation requested once and no replay");
            report.record_error(&error);
            context.cancellation.cancel();
            report.uncertain = true;
            report.document["calls"][call_index]["state"] = json!("UNKNOWN");
            report.document["calls"][call_index]["error"] = json!(error.message);
            report.event(
                "execute_outcome",
                report.document["calls"][call_index].clone(),
            )?;
            return Err(error);
        }
    };
    let bytes = json_bytes(&value, MAX_VALUE)?;
    report.completed_calls += 1;
    report.document["calls"][call_index]["state"] = json!("RETURNED");
    report.document["calls"][call_index]["response"] = value.clone();
    report.document["calls"][call_index]["response_value_sha256"] = json!(sha256(&bytes));
    report.document["calls"][call_index]["response_value_bytes"] = json!(bytes.len());
    report.document["calls"][call_index]["response_value_utf8"] =
        json!(std::str::from_utf8(&bytes).map_err(failed)?);
    report.event(
        "execute_outcome",
        report.document["calls"][call_index].clone(),
    )?;
    Ok(value)
}

fn field<'a>(value: &'a Value, name: &str) -> Checked<&'a Value> {
    value
        .get(name)
        .ok_or_else(|| failed(format!("response has no {name}")))
}

fn items(value: &Value) -> Checked<&[Value]> {
    field(value, "items")?
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| failed("items is not an array"))
}

fn unique<'a>(values: &'a [Value], name: &str, expected: &str) -> Checked<&'a Value> {
    let matches = values
        .iter()
        .filter(|item| item.get(name).and_then(Value::as_str) == Some(expected))
        .collect::<Vec<_>>();
    ensure(
        matches.len() == 1,
        "selected response item is missing or ambiguous",
    )?;
    Ok(matches[0])
}

fn reference(
    value: &Value,
    project: &Project,
    fingerprint: &str,
    kind: Kind,
    id: &str,
) -> Checked<String> {
    let raw = value
        .as_str()
        .ok_or_else(|| failed("returned reference is not a string"))?;
    let reference = Reference::decode(raw).map_err(sdk_error)?;
    reference
        .check(project, fingerprint, kind)
        .map_err(sdk_error)?;
    ensure(
        reference.id == id && reference.encode() == raw,
        "returned reference identity is not canonical",
    )?;
    Ok(raw.to_owned())
}

fn list_binding(value: &Value, input: Input) -> Checked<()> {
    ensure(
        value["fingerprint"] == input.sha256 && value["revision"].as_u64() == Some(1),
        "list source identity or revision differs",
    )
}

fn doctor(value: &Value) -> Checked<()> {
    ensure(
        value["driver_version"] == "1.0.0"
            && value["motion_canvas_version"] == "3.17.2"
            && value["network"] == false
            && value["render_available"] == false
            && value["project_mounted"] == true
            && value["media_mounted"] == false
            && value["output_mounted"] == false
            && value["runtime_mounted"] == false
            && value["active_jobs"].as_u64() == Some(0)
            && value["capability_count"].as_u64() == Some(33),
        "doctor does not report the original closed read-only environment",
    )
}

async fn semantic_queries(
    provider: &DriverProvider,
    caps: &[ProvidedCapability],
    project: &Project,
    report: &mut Observation,
) -> Checked<()> {
    let host = SEMANTIC.label;
    let before = query(provider, caps, host, CALLS[0].1, json!({}), report).await?;
    doctor(&before)?;
    let types = query(provider, caps, host, CALLS[1].1, json!({}), report).await?;
    let type_items = items(&types)?;
    ensure(
        types["motion_canvas_version"] == "3.17.2"
            && types["managed_type_count"].as_u64() == Some(20)
            && type_items.len() == 20,
        "semantic type inventory differs",
    )?;
    for key in ["kind", "upstream_class"] {
        let names = type_items
            .iter()
            .map(|item| {
                field(item, key)?
                    .as_str()
                    .ok_or_else(|| failed("semantic identity is not a string"))
            })
            .collect::<Checked<BTreeSet<_>>>()?;
        ensure(
            names.len() == 20,
            "semantic kind or upstream class is duplicated",
        )?;
    }
    let polygon = unique(type_items, "kind", "polygon")?;
    ensure(
        polygon["upstream_class"] == "Polygon",
        "polygon class mapping differs",
    )?;
    let described = query(
        provider,
        caps,
        host,
        CALLS[2].1,
        json!({"kind": "polygon"}),
        report,
    )
    .await?;
    ensure(
        described == *polygon,
        "describe differs from the original types entry",
    )?;
    let properties = field(&described, "properties")?
        .as_array()
        .ok_or_else(|| failed("properties is not an array"))?;
    let sides = unique(properties, "semantic_name", "sides")?;
    ensure(
        sides["upstream_name"] == "sides"
            && sides["value_kind"] == "integer"
            && sides["storage"] == "semantic"
            && sides["animatable"] == true
            && sides["source_interface"] == "PolygonProps",
        "sides descriptor differs",
    )?;
    let listed = query(provider, caps, host, CALLS[3].1, json!({}), report).await?;
    list_binding(&listed, SEMANTIC)?;
    let nodes = items(&listed)?;
    let expected_nodes = project
        .scenes
        .iter()
        .flat_map(|scene| scene.nodes.iter())
        .collect::<Vec<_>>();
    ensure(
        nodes.len() == 14 && expected_nodes.len() == 14,
        "node inventory differs",
    )?;
    let mut seen = BTreeSet::new();
    for node in nodes {
        let id = field(node, "id")?
            .as_str()
            .ok_or_else(|| failed("node id is not a string"))?;
        ensure(seen.insert(id), "duplicate node identity")?;
        let original = expected_nodes
            .iter()
            .find(|item| item.id == id)
            .ok_or_else(|| failed("unknown node identity"))?;
        ensure(
            node["name"] == original.name
                && node["kind"] == json!(original.kind)
                && node["parent"] == json!(original.parent),
            "node metadata differs from source",
        )?;
        reference(
            field(node, "reference")?,
            project,
            SEMANTIC.sha256,
            Kind::Node,
            id,
        )?;
    }
    let node = unique(nodes, "id", "polygon")?;
    let node_ref = reference(
        field(node, "reference")?,
        project,
        SEMANTIC.sha256,
        Kind::Node,
        "polygon",
    )?;
    let property = query(
        provider,
        caps,
        host,
        CALLS[4].1,
        json!({"node_ref": node_ref, "property": "sides"}),
        report,
    )
    .await?;
    list_binding(&property, SEMANTIC)?;
    ensure(
        property["node_ref"] == node_ref
            && property["descriptor"] == *sides
            && property["is_set"] == true
            && property["value"].as_f64() == Some(6.0),
        "revision-bound sides readback differs",
    )?;
    reference(
        field(&property, "node_ref")?,
        project,
        SEMANTIC.sha256,
        Kind::Node,
        "polygon",
    )?;
    let plan = query(
        provider,
        caps,
        host,
        CALLS[5].1,
        json!({"profile": {
            "first_frame": 0, "end_frame_exclusive": 2, "scale": "full",
            "transparent": false, "timeout_ms": 10000
        }}),
        report,
    )
    .await?;
    ensure(
        plan == json!({
            "renderer": "bundled_browser_v1", "width": 1280, "height": 720, "fps": 30,
            "first_frame": 0, "end_frame_exclusive": 2, "frame_count": 2,
            "project_duration_ms": 1500, "alpha": false, "color_space": "srgb", "timeout_ms": 10000
        }),
        "bounded two-frame plan differs from the original serialized contract",
    )?;
    let after = query(provider, caps, host, CALLS[6].1, json!({}), report).await?;
    doctor(&after)?;
    ensure(before == after, "doctor changed across read-only queries")?;
    Ok(())
}

async fn cue_queries(
    provider: &DriverProvider,
    caps: &[ProvidedCapability],
    project: &Project,
    report: &mut Observation,
) -> Checked<()> {
    let host = CUES.label;
    let before = query(provider, caps, host, CALLS[7].1, json!({}), report).await?;
    doctor(&before)?;
    let scenes = query(provider, caps, host, CALLS[8].1, json!({}), report).await?;
    list_binding(&scenes, CUES)?;
    let scene_items = items(&scenes)?;
    ensure(
        scene_items.len() == 1,
        "cue fixture scene inventory differs",
    )?;
    let scene = unique(scene_items, "id", "cue-scene")?;
    ensure(
        scene["name"] == "Cue timing" && scene["duration_ms"].as_u64() == Some(2200),
        "cue scene metadata differs",
    )?;
    let scene_ref = reference(
        field(scene, "reference")?,
        project,
        CUES.sha256,
        Kind::Scene,
        "cue-scene",
    )?;
    let cues = query(
        provider,
        caps,
        host,
        CALLS[9].1,
        json!({"scene_ref": scene_ref}),
        report,
    )
    .await?;
    list_binding(&cues, CUES)?;
    let cue_items = items(&cues)?;
    ensure(cue_items.len() == 1, "cue inventory differs")?;
    let cue = unique(cue_items, "id", "voice-in")?;
    ensure(
        cue["name"] == "voice-in" && cue["at_ms"].as_u64() == Some(600),
        "cue identity or timestamp differs",
    )?;
    reference(
        field(cue, "reference")?,
        project,
        CUES.sha256,
        Kind::Cue,
        "voice-in",
    )?;
    let after = query(provider, caps, host, CALLS[10].1, json!({}), report).await?;
    doctor(&after)?;
    ensure(before == after, "doctor changed across cue queries")?;
    Ok(())
}

async fn run_host(
    input: Input,
    original: &[u8],
    project: &Project,
    work: &Path,
    driver: &Path,
    driver_record: &Value,
    sandbox: &Path,
    report: &mut Observation,
) -> Checked<()> {
    let root = work.join(input.label);
    mkdir_new(&root)?;
    let project_root = root.join("project");
    let state = root.join("host-state");
    let bin = root.join("bin");
    for directory in [&project_root, &state, &bin] {
        mkdir_new(directory)?;
    }
    write_new(&project_root.join("semwright-motion.json"), original, false)?;
    let project_before = project_snapshot(&project_root)?;
    ensure(
        project_before["file"]["sha256"] == input.sha256
            && project_before["file"]["bytes"] == input.bytes,
        "prepared project is not the original fixture",
    )?;
    let copied_driver = bin.join("semwright-motion-canvas-driver");
    let copied_before = copy_driver(driver, &copied_driver, driver_record)?;
    let manifest = manifest(
        copied_driver.clone(),
        copied_before["sha256"].as_str().unwrap().to_owned(),
    );
    let grants = vec![FilesystemGrant {
        name: "project".into(),
        path: project_root.clone(),
        read: true,
        write: false,
    }];
    let index = report.document["hosts"].as_array().unwrap().len();
    report.document["hosts"]
        .as_array_mut()
        .unwrap()
        .push(json!({
            "label": input.label, "state": "PREPARED", "manifest": manifest,
            "grants": [{"name": "project", "path": project_root, "read": true, "write": false}],
            "network_opt_in": false, "project_before": project_before,
            "driver_before": copied_before, "host_state_directory": state,
            "shutdown": {"state": "NOT_ATTEMPTED"}, "identity_before": null, "identity_after": null
        }));
    let recorded_grants = report.host_mut(index)["grants"].clone();
    report.event(
        "connect_intent",
        json!({
            "host": input.label, "state": "UNKNOWN_UNTIL_OUTCOME",
            "manifest": manifest, "grants": recorded_grants
        }),
    )?;
    report.connections += 1;
    let connected = tokio::time::timeout(
        CALL_TIMEOUT,
        DriverProvider::connect(manifest, &state, sandbox, &grants, false),
    )
    .await;
    let provider = match connected {
        Ok(Ok(provider)) => provider,
        Ok(Err(error)) => {
            let error = sdk_error(error);
            report.record_error(&error);
            report.host_mut(index)["state"] = json!(error.state);
            report.host_mut(index)["error"] = json!(error.message);
            report.event(
                "connect_outcome",
                json!({"host": input.label, "state": error.state, "error": error.message}),
            )?;
            return Err(error);
        }
        Err(_) => {
            report.uncertain = true;
            report.host_mut(index)["state"] = json!("UNKNOWN");
            report.event("connect_outcome", json!({"host": input.label, "state": "UNKNOWN", "error": "connect timed out; no replay"}))?;
            return Err(unknown("connect timed out; no replay"));
        }
    };
    report.completed_connections += 1;
    let identity_before =
        serde_json::to_value(Provider::identity(provider.as_ref())).map_err(failed)?;
    report.host_mut(index)["identity_before"] = identity_before.clone();
    report.host_mut(index)["state"] = json!("CONNECTED");
    report.event(
        "connect_outcome",
        json!({"host": input.label, "state": "CONNECTED", "identity": identity_before}),
    )?;
    let mut primary: Checked<()> = async {
        let (caps, before_catalog) = catalog(provider.as_ref(), input.label, report).await?;
        report.host_mut(index)["catalog_before"] = before_catalog.clone();
        if input.label == SEMANTIC.label {
            semantic_queries(provider.as_ref(), &caps, project, report).await?;
        } else {
            cue_queries(provider.as_ref(), &caps, project, report).await?;
        }
        let (_, after_catalog) = catalog(provider.as_ref(), input.label, report).await?;
        report.host_mut(index)["catalog_after"] = after_catalog.clone();
        ensure(
            before_catalog == after_catalog,
            "capability catalog changed during the Host lifecycle",
        )?;
        let identity_after =
            serde_json::to_value(Provider::identity(provider.as_ref())).map_err(failed)?;
        report.host_mut(index)["identity_after"] = identity_after.clone();
        ensure(
            identity_before == identity_after,
            "provider identity changed during read-only calls",
        )?;
        Ok(())
    }
    .await;
    if let Err(error) = &primary {
        report.record_error(error);
    }
    let cleanup: Checked<()> = async {
        let closed = Provider::closed(provider.as_ref()).ok_or_else(|| failed("Host has no closure token"))?;
        ensure(!closed.is_cancelled(), "Host closed before the requested shutdown")?;
        report.event("shutdown_intent", json!({"host": input.label, "state": "UNKNOWN_UNTIL_OUTCOME", "cleanup_after_error": primary.is_err()}))?;
        report.host_mut(index)["shutdown"]["state"] = json!("UNKNOWN_UNTIL_OUTCOME");
        tokio::time::timeout(CALL_TIMEOUT, Provider::shutdown(provider.as_ref())).await
            .map_err(|_| unknown("Host shutdown timed out; no replay"))?.map_err(sdk_error)?;
        ensure(closed.is_cancelled(), "Host shutdown returned without closed lifecycle")?;
        report.shutdowns += 1;
        report.host_mut(index)["shutdown"] = json!({"state": "COMPLETED", "closed_token_cancelled": true});
        report.event("shutdown_outcome", json!({"host": input.label, "state": "COMPLETED"}))?;
        Ok(())
    }.await;
    if let Err(error) = cleanup {
        report.uncertain = true;
        report.host_mut(index)["shutdown"] = json!({"state": "UNKNOWN", "error": error.message});
        let _ = report.event(
            "shutdown_outcome",
            json!({"host": input.label, "state": "UNKNOWN", "error": error.message}),
        );
        if primary.is_ok() {
            primary = Err(unknown(error.message));
        }
    }
    drop(provider);
    let postcheck: Checked<()> = (|| {
        let after = project_snapshot(&project_root)?;
        report.host_mut(index)["project_after"] = after.clone();
        ensure(
            after == project_before,
            "project content or identity changed across Host lifecycle",
        )?;
        let (copied_after, _) = inspect(&copied_driver, MAX_EXECUTABLE, false)?;
        report.host_mut(index)["driver_after"] = copied_after.clone();
        ensure(
            copied_after == copied_before,
            "private driver changed across Host lifecycle",
        )?;
        let (raw_after, _) = inspect(
            &project_root.join("semwright-motion.json"),
            MAX_FIXTURE,
            true,
        )?;
        ensure(
            raw_after["sha256"] == input.sha256,
            "project after-state differs from original bytes",
        )?;
        report.host_mut(index)["project_identity_after"] = json!({
            "id": project.id, "generation": project.generation, "revision": project.revision,
            "derived_from_unchanged_original_bytes": true, "fingerprint": input.sha256
        });
        Ok(())
    })();
    if let Err(error) = postcheck {
        report.host_mut(index)["postcheck_error"] = json!(error.message);
        if primary.is_ok() {
            primary = Err(error);
        }
    }
    report.host_mut(index)["state"] = json!(if primary.is_ok() {
        "COMPLETED"
    } else {
        "INCOMPLETE"
    });
    primary
}

fn execute(output: &Path, report: &mut Observation, watched: &mut Vec<Watched>) -> Checked<()> {
    let source = env_path("MW_SDK_READONLY_SOURCE", true)?;
    let driver = env_path("MW_SDK_READONLY_DRIVER", false)?;
    let sandbox = env_path("MW_SDK_READONLY_SANDBOX", false)?;
    ensure(
        !output.starts_with(&source) && !source.starts_with(output),
        "output and SDK source overlap",
    )?;
    let work = env_new_directory("MW_SDK_READONLY_WORK")?;
    ensure(
        !work.starts_with(&source) && !source.starts_with(&work),
        "private work and SDK source overlap",
    )?;
    ensure(
        !work.starts_with(output) && !output.starts_with(&work),
        "private work and observation output overlap",
    )?;
    report.document["private_work_root"] = json!(work);
    report.document["private_work_upload_allowed"] = json!(false);
    let self_source = checked_path(Path::new(file!()), false)?;
    let harness = checked_path(&std::env::current_exe().map_err(failed)?, false)?;
    for (label, path, cap) in [
        ("sdk_lock", source.join("Cargo.lock"), MAX_SOURCE),
        ("driver", driver.clone(), MAX_EXECUTABLE),
        ("sandbox", sandbox.clone(), MAX_EXECUTABLE),
        ("new_fixture_source", self_source, MAX_SOURCE),
        ("new_fixture_harness", harness, MAX_EXECUTABLE),
    ] {
        let (record, _) = inspect(&path, cap, false)?;
        if label == "sdk_lock" {
            ensure(
                record["sha256"] == SDK_LOCK,
                "SDK lock differs from source pin",
            )?;
        }
        report.document["observed_files"][label] = record.clone();
        watched.push(Watched {
            path,
            cap,
            before: record,
        });
    }
    let driver_record = report.document["observed_files"]["driver"].clone();
    let (semantic_bytes, semantic_project) = pinned_input(&source, SEMANTIC, report, watched)?;
    let (cue_bytes, cue_project) = pinned_input(&source, CUES, report, watched)?;
    report.document["source_root"] = json!(source);
    report.document["counts_expected"] = json!({
        "provider_connections": 2, "provider_shutdowns": 2, "execute_calls": 11,
        "libtest_methods": 1, "original_fixture_files": 2
    });
    report.event(
        "fixture_preflight_complete",
        json!({
            "state": "SOURCE_INPUTS_PINNED", "sdk_source_expected": SDK_SHA,
            "complete_checkout_binding": "REQUIRED_FROM_CALLER",
            "original_inputs": report.document["inputs"],
            "observed_files": report.document["observed_files"]
        }),
    )?;
    mkdir_new(&work)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(failed)?;
    let host_result: Checked<()> = runtime.block_on(async {
        run_host(
            SEMANTIC,
            &semantic_bytes,
            &semantic_project,
            &work,
            &driver,
            &driver_record,
            &sandbox,
            report,
        )
        .await?;
        run_host(
            CUES,
            &cue_bytes,
            &cue_project,
            &work,
            &driver,
            &driver_record,
            &sandbox,
            report,
        )
        .await?;
        Ok(())
    });
    host_result?;
    ensure(
        report.connections == 2
            && report.completed_connections == 2
            && report.shutdowns == 2
            && report.attempted_calls == 11
            && report.completed_calls == 11
            && report.next_call == 11,
        "actual completed calls or Host lifecycles differ from the closed fixture",
    )?;
    Ok(())
}

fn run_checked() -> Checked<()> {
    let output = env_path("MW_SDK_READONLY_OUTPUT", true)?;
    ensure(
        fs::read_dir(&output).map_err(failed)?.next().is_none(),
        "output directory must be new and empty",
    )?;
    fs::set_permissions(&output, fs::Permissions::from_mode(0o700)).map_err(failed)?;
    let mut report = Observation::new(&output);
    let mut watched = Vec::new();
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        execute(&output, &mut report, &mut watched)
    }));
    let mut primary = match result {
        Ok(result) => result,
        Err(_) => Err(unknown(
            "fixture panicked; child state is uncertain and no replay is allowed",
        )),
    };
    if let Err(error) = &primary {
        report.record_error(error);
    }
    for input in watched {
        let checked = inspect(&input.path, input.cap, false);
        match checked {
            Ok((after, _)) => {
                let equal = after == input.before;
                report.document["input_postcheck"].as_array_mut().unwrap().push(json!({
                    "path": input.path, "before": input.before, "after": after, "unchanged": equal
                }));
                if !equal && primary.is_ok() {
                    primary = Err(failed("watched input changed after execution"));
                }
            }
            Err(error) => {
                report.document["input_postcheck"].as_array_mut().unwrap().push(json!({
                    "path": input.path, "before": input.before, "unchanged": false, "error": error.message
                }));
                if primary.is_ok() {
                    primary = Err(error);
                }
            }
        }
    }
    if let Err(error) = &primary {
        report.record_error(error);
    }
    if report.uncertain && primary.is_ok() {
        primary = Err(unknown("an attempted phase has no complete outcome"));
    }
    let state = if report.uncertain {
        "UNKNOWN"
    } else if primary.is_ok() {
        "PASSED"
    } else {
        "FAILED"
    };
    report.document["terminal"] = json!(state);
    report.document["ended_unix_ms"] = json!(epoch_millis());
    report.document["elapsed_millis"] = json!(report.started.elapsed().as_millis());
    report.document["counts"] = json!({
        "provider_connections_attempted": report.connections,
        "provider_connections_completed": report.completed_connections,
        "provider_shutdowns_completed": report.shutdowns,
        "execute_calls_attempted": report.attempted_calls,
        "execute_calls_completed": report.completed_calls
    });
    report.document["scope"]["readonly_fixture_complete"] =
        json!(primary.is_ok() && !report.uncertain);
    let bytes = json_bytes(&report.document, MAX_REPORT)?;
    let destination = output.join("readonly-host-observation.json");
    if let Err(error) = write_new(&destination, &bytes, false) {
        eprintln!(
            "readonly evidence write failed: {}; original outcome: {:?}",
            error.message, primary
        );
        return Err(primary.err().unwrap_or(error));
    }
    let (receipt, _) = inspect(&destination, MAX_REPORT as u64, false)?;
    ensure(
        receipt["sha256"] == sha256(&bytes),
        "final observation bytes differ after write",
    )?;
    println!(
        "MW_SDK_READONLY_TERMINAL {}",
        json!({
            "state": state, "observation": receipt, "readonly_fixture_complete": primary.is_ok() && !report.uncertain,
            "native_render_calls": 0, "node_tool_calls": 0, "formal_acceptance": false
        })
    );
    std::io::stdout().flush().map_err(failed)?;
    primary
}

#[test]
fn readonly_host_queries_preserve_original_fixtures() {
    if let Err(error) = run_checked() {
        panic!("{}: {}", error.state, error.message);
    }
}
