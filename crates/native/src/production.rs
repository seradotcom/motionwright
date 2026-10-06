use motionwright_domain::RevisionStamp;
use motionwright_service::{ProductionReceipt, StudioService};
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
use tokio::{io::AsyncReadExt, process::Command, time::timeout};
use uuid::Uuid;

const CONNECTION_SCHEMA: &str = "motionwright-semwright-connection/1";
const MAX_CONFIG_BYTES: u64 = 16 * 1024;
const MAX_ARGS_BYTES: usize = 220_000;
const MAX_OUTPUT_BYTES: usize = 1_048_576;
const DEFAULT_DEADLINE_SECS: u64 = 45;
const MLT_DEADLINE_SECS: u64 = 330;

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

fn expected_provider(command: &str) -> Option<&'static str> {
    if MOTION_CANVAS_COMMANDS.contains(&command) {
        Some("driver:motion-canvas")
    } else if MLT_COMMANDS.contains(&command) {
        Some("driver:mlt-video")
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
        let expected = expected_provider(command).ok_or_else(|| {
            Error::new(ErrorCode::Unsupported, "Production command is not enabled")
        })?;
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
        let deadline = if command.starts_with("driver.mlt-video.") {
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
        if provenance.provider != expected
            || provenance.source != SourceKind::Driver
            || provenance.provider_generation.is_none()
            || !is_sha256(&provenance.descriptor_sha256)
        {
            return Err(backend(
                "Canonical production response provenance does not match the expected driver",
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
            .append_production_receipt(
                project_id,
                project.generation,
                project.revision,
                request_id,
                &digest,
                command,
                "dispatching",
                json!({
                    "mutation": mutation,
                    "connection": self.client.connection().identity()?
                }),
            )
            .map_err(storage_error)?;

        match self.client.execute(command, args, mutation).await {
            Ok(result) => {
                let receipt = self
                    .service
                    .append_production_receipt(
                        project_id,
                        project.generation,
                        project.revision,
                        request_id,
                        &digest,
                        command,
                        "completed",
                        json!({
                            "result": result.value,
                            "broker_request_id": result.request_id
                        }),
                    )
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
                let _ = self.service.append_production_receipt(
                    project_id,
                    project.generation,
                    project.revision,
                    request_id,
                    &digest,
                    command,
                    stage,
                    json!({
                        "error": &error,
                        "mutation": mutation
                    }),
                );
                Err(error)
            }
        }
    }
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
    use motionwright_storage::Store;
    use semwright_native_sdk::types::{Execution, InvocationProvenance};
    use std::fs::File;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

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
    #[tokio::test]
    async fn coordinator_binds_revision_provenance_and_request_replay() {
        let (service, project, temp) = fixture_service();
        let connection = fake_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let expected = project.stamp();

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
    async fn command_allowlist_and_resource_binding_fail_closed() {
        let (service, project, temp) = fixture_service();
        let mut connection = fake_connection(&temp, "project:other".into());
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
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
