//! Direct reference through the pinned public DriverProvider and Driver Host.
//! A separate empty owner project receives the exact prepared composition.
//! No Broker, renderer implementation, arbitrary command, head-file writer or retry.
#[cfg(target_os = "linux")]
mod linux {
    use motionwright_driver_hyperframes::{model, store};
    use semwright_backend_api::{Context, ProvidedCapability, Provider};
    use semwright_driver_host::DriverProvider;
    use semwright_driver_sdk::Manifest;
    use semwright_policy::FilesystemGrant;
    use semwright_types::Error as NativeError;
    use serde::{Deserialize, Serialize};
    use serde_json::{Value, json};
    use std::{
        fs,
        os::unix::fs::PermissionsExt,
        path::{Path, PathBuf},
        time::{Duration, Instant},
    };
    use tokio_util::sync::CancellationToken;

    type BoxedError = Box<dyn std::error::Error + Send + Sync>;
    type Result<T> = std::result::Result<T, BoxedError>;

    fn sdk_pin_from_source_lock(raw: &str) -> Result<String> {
        let lock: Value = serde_json::from_str(raw)?;
        let source = &lock["dependencies"]["semwright"];
        let revision = source["revision"]
            .as_str()
            .ok_or("compiled SDK revision is absent")?;
        require(
            lock["schema_version"] == 1
                && source["repository"] == "https://github.com/seradotcom/semwright"
                && revision.len() == 40
                && revision
                    .bytes()
                    .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
                && source["verification"]["status"] == "verified"
                && source["verification"]["revision"] == revision,
            "compiled SDK source lock differs",
        )?;
        Ok(revision.to_owned())
    }

    fn compiled_sdk_pin() -> Result<String> {
        // The existing source-policy/controller admit the evidence and Cargo pin.
        // Bind this binary to its compile-time lock instead of a second manual pin.
        sdk_pin_from_source_lock(include_str!("../../../SOURCE_LOCK.json"))
    }

    fn require_compiled_sdk_pin(candidate: &str) -> Result<()> {
        require(
            candidate == compiled_sdk_pin()?,
            "direct SDK differs from the compiled source lock",
        )
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct BoundFile {
        path: PathBuf,
        sha256: String,
        bytes: u64,
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Config {
        schema: String,
        phase: String,
        source_sha: String,
        semwright_sha: String,
        manifest: BoundFile,
        driver: BoundFile,
        node: BoundFile,
        sandbox_helper: BoundFile,
        request: BoundFile,
        runtime: PathBuf,
        exported_source: PathBuf,
        project: PathBuf,
        output: PathBuf,
        state: PathBuf,
        evidence: PathBuf,
    }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Request {
        schema: String,
        fingerprint: String,
        runtime_lock_sha256: String,
        attempt_id: String,
        source: model::SourceInventory,
    }
    fn require(ok: bool, code: &'static str) -> Result<()> {
        if ok { Ok(()) } else { Err(code.into()) }
    }
    fn bytes(file: &BoundFile, limit: u64) -> Result<Vec<u8>> {
        require(
            file.bytes > 0 && file.bytes <= limit && model::sha_valid(&file.sha256),
            "direct binding shape",
        )?;
        let raw = store::regular(&file.path, limit)?;
        require(
            raw.len() as u64 == file.bytes && model::sha(&raw) == file.sha256,
            "direct input bytes changed",
        )?;
        Ok(raw)
    }
    fn directory(path: &Path, empty: bool) -> Result<()> {
        require(
            path.is_absolute() && path.is_dir() && path.canonicalize()? == path,
            "direct canonical root required",
        )?;
        let m = fs::symlink_metadata(path)?;
        require(
            m.permissions().mode() & 0o077 == 0,
            "direct private root required",
        )?;
        if empty {
            require(
                fs::read_dir(path)?.next().is_none(),
                "direct root already used",
            )?;
        }
        Ok(())
    }
    fn checked_request(raw: &[u8], phase: &str) -> Result<Request> {
        let request: Request = serde_json::from_slice(raw)?;
        require(
            matches!(phase, "original" | "edited")
                && request.schema == "semwright.hyperframes.render-request/1"
                && model::sha_valid(&request.fingerprint)
                && model::sha_valid(&request.runtime_lock_sha256),
            "direct request shape",
        )?;
        require(
            request.attempt_id == format!("direct-{phase}-{}", &request.fingerprint[..16])
                && request.source.fingerprint == request.fingerprint
                && model::compile(&request.source.composition)?.inventory() == request.source,
            "direct request source identity",
        )?;
        let c = &request.source.composition;
        require(
            (c.width, c.height, c.fps, c.frame_count, c.clips.len()) == (640, 360, 30, 60, 4),
            "direct fixture profile",
        )?;
        Ok(request)
    }
    fn expected_manifest(config: &Config) -> Value {
        json!({
            "manifest_version":1,"protocol":7,"id":"hyperframes","version":"0.1.0",
            "publisher":"motionwright","executable":config.driver.path,
            "sha256":config.driver.sha256,
            "application":{"desktop_id":null,"process_names":["node"],
                "supported_versions":["HyperFrames 0.8.181 (owner installed)"]},
            "transport":"stdio_v1",
            "mounts":[
                {"root":"runtime","read_only":true,"execute":true},
                {"root":"project","read_only":false,"execute":false},
                {"root":"output","read_only":false,"execute":false}],
            "system_config":[],"secrets":[],
            "tools":[{"root":"hyperframes-node-tool","name":"hyperframes-node",
                "sha256":config.node.sha256,"mounts":["runtime","project","output"],
                "system_config":[],"dependencies":[]}],
            "network":false,"nvidia_gpu":false,"loopback_port":null,
            "resources":{"open_files":512,"processes":256,"cpu_seconds":300,
                "operation_cpu_seconds":0,"address_space_bytes":4294967296u64,
                "file_size_bytes":1073741824u64},
            "request_timeout_ms":300000,
            "interfaces":{"dynamic_capabilities":false,"cooperative_cancellation":false,
                "events":false,"health":true,"progress":false,"artifacts":false,
                "native_refs":false,"host_tools":true}})
    }
    fn check_export(config: &Config, request: &Request, applied: bool) -> Result<()> {
        let compiled = model::compile(&request.source.composition)?;
        let root = config
            .exported_source
            .join(format!("source-{}", request.fingerprint));
        require(
            store::regular(&root.join("index.html"), model::MAX_SOURCE_BYTES as u64)?
                == compiled.html
                && store::regular(
                    &root.join("semwright-hyperframes.json"),
                    model::MAX_SOURCE_BYTES as u64,
                )? == compiled.source,
            "direct exported source changed",
        )?;
        let inv: model::SourceInventory = serde_json::from_slice(&store::regular(
            &root.join("source-inventory.json"),
            512 * 1024,
        )?)?;
        require(inv == request.source, "direct exported inventory changed")?;
        if applied {
            let actual = config
                .project
                .join(format!("source-{}", request.fingerprint));
            for name in [
                "index.html",
                "semwright-hyperframes.json",
                "source-inventory.json",
            ] {
                require(
                    store::regular(&root.join(name), 512 * 1024)?
                        == store::regular(&actual.join(name), 512 * 1024)?,
                    "direct Host input differs from prepared export",
                )?;
            }
        }
        Ok(())
    }

    #[derive(Debug)]
    enum DirectCallPrimary {
        ConfirmedResponse(Value),
        Provider(Box<NativeError>),
        ObserverTimeout(tokio::time::error::Elapsed),
    }

    #[derive(Debug)]
    struct DirectCallFailure {
        primary: DirectCallPrimary,
        call_custody_error: Option<NativeError>,
        host_custody_error: Option<NativeError>,
    }

    impl std::fmt::Display for DirectCallFailure {
        fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            match &self.primary {
                DirectCallPrimary::ConfirmedResponse(_) => {
                    formatter.write_str("native response observed; private result custody failed")
                }
                DirectCallPrimary::Provider(error) => std::fmt::Display::fmt(error, formatter),
                DirectCallPrimary::ObserverTimeout(_) => {
                    formatter.write_str("direct call timed out; original outcome unknown")
                }
            }
        }
    }

    impl std::error::Error for DirectCallFailure {
        fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
            match &self.primary {
                DirectCallPrimary::ConfirmedResponse(_) => self
                    .call_custody_error
                    .as_ref()
                    .map(|error| error as &(dyn std::error::Error + 'static)),
                DirectCallPrimary::Provider(error) => Some(error.as_ref()),
                DirectCallPrimary::ObserverTimeout(error) => Some(error),
            }
        }
    }

    #[derive(Serialize)]
    struct ConfirmedResponse<'a> {
        // The returned provider value is data, not a grant or a job-completion claim.
        payload: &'a Value,
    }

    #[derive(Serialize)]
    struct DirectCallFailureDetails<'a> {
        origin: &'static str,
        outcome_known: bool,
        confirmed_response: Option<ConfirmedResponse<'a>>,
        native_error: Option<&'a NativeError>,
        observer_timeout: bool,
        call_custody_error: Option<&'a NativeError>,
        host_custody_error: Option<&'a NativeError>,
        full_error_in_selected_capsule: bool,
    }

    impl DirectCallFailure {
        fn code(&self) -> String {
            match &self.primary {
                DirectCallPrimary::ConfirmedResponse(_) => "NativeResultCustodyFailed".to_owned(),
                DirectCallPrimary::Provider(error) => format!("{:?}", error.code),
                DirectCallPrimary::ObserverTimeout(_) => "Timeout".to_owned(),
            }
        }

        fn outcome_known(&self) -> bool {
            match &self.primary {
                DirectCallPrimary::ConfirmedResponse(_) => true,
                DirectCallPrimary::Provider(error) => error.outcome_known,
                DirectCallPrimary::ObserverTimeout(_) => false,
            }
        }

        fn details(&self) -> DirectCallFailureDetails<'_> {
            let (origin, native_error, observer_timeout, confirmed_response) = match &self.primary {
                DirectCallPrimary::ConfirmedResponse(value) => (
                    "confirmed-response",
                    None,
                    false,
                    Some(ConfirmedResponse { payload: value }),
                ),
                DirectCallPrimary::Provider(error) => {
                    ("provider-error", Some(error.as_ref()), false, None)
                }
                DirectCallPrimary::ObserverTimeout(_) => ("observer-timeout", None, true, None),
            };
            DirectCallFailureDetails {
                origin,
                outcome_known: self.outcome_known(),
                confirmed_response,
                native_error,
                observer_timeout,
                call_custody_error: self.call_custody_error.as_ref(),
                host_custody_error: self.host_custody_error.as_ref(),
                full_error_in_selected_capsule: false,
            }
        }
    }

    #[derive(Serialize)]
    struct DirectCallFailureSnapshot<'a> {
        schema: &'static str,
        code: String,
        outcome_known: bool,
        automatic_retry: bool,
        originals_retained: bool,
        // The existing json-projection selects the five fields above. These
        // complete typed details remain in the private original, not its TAR
        // projection; selecting them requires an explicit collector contract.
        private_original: DirectCallFailureDetails<'a>,
    }

    fn retain_call_failure(stem: &Path, primary: DirectCallPrimary) -> DirectCallFailure {
        let mut failure = DirectCallFailure {
            primary,
            call_custody_error: None,
            host_custody_error: None,
        };
        let snapshot = DirectCallFailureSnapshot {
            schema: "motionwright.hyperframes.direct-call-failure/1",
            code: failure.code(),
            outcome_known: failure.outcome_known(),
            automatic_retry: false,
            originals_retained: true,
            private_original: failure.details(),
        };
        let custody = store::fresh_json(&stem.with_extension("failure.json"), &snapshot);
        failure.call_custody_error = custody.err();
        // Keep the same owned native Error or Elapsed even when its snapshot
        // could not be reserved, written, synced or read back.
        failure
    }

    fn finish_call_observation(
        stem: &Path,
        observed: std::result::Result<
            std::result::Result<Value, NativeError>,
            tokio::time::error::Elapsed,
        >,
    ) -> Result<Value> {
        match observed {
            Ok(Ok(value)) => {
                match store::fresh_json(&stem.with_extension("result.json"), &value) {
                    Ok(()) => Ok(value),
                    Err(custody_error) => Err(Box::new(DirectCallFailure {
                        // A confirmed response is distinct from native failure or
                        // UNKNOWN. Stop the caller while retaining its whole Value.
                        primary: DirectCallPrimary::ConfirmedResponse(value),
                        call_custody_error: Some(custody_error),
                        host_custody_error: None,
                    })),
                }
            }
            Ok(Err(error)) => Err(Box::new(retain_call_failure(
                stem,
                DirectCallPrimary::Provider(Box::new(error)),
            ))),
            Err(error) => Err(Box::new(retain_call_failure(
                stem,
                DirectCallPrimary::ObserverTimeout(error),
            ))),
        }
    }

    #[derive(Serialize)]
    struct DirectHostFailureSnapshot<'a> {
        schema: &'static str,
        state: &'static str,
        original_outcome_may_be_unknown: bool,
        automatic_retry: bool,
        originals_retained: bool,
        private_original: Option<DirectCallFailureDetails<'a>>,
    }

    fn retain_host_failure(path: &Path, error: &mut BoxedError) {
        let custody = {
            let snapshot = DirectHostFailureSnapshot {
                schema: "motionwright.hyperframes.direct-host-failure/1",
                state: "NOT_VERIFIED",
                original_outcome_may_be_unknown: true,
                automatic_retry: false,
                originals_retained: true,
                private_original: error
                    .downcast_ref::<DirectCallFailure>()
                    .map(DirectCallFailure::details),
            };
            store::fresh_json(path, &snapshot)
        };
        if let Err(custody_error) = custody
            && let Some(failure) = error.downcast_mut::<DirectCallFailure>()
        {
            failure.host_custody_error = Some(custody_error);
        }
        // The caller returns the same Box. No new native call or cleanup is
        // started after an error/UNKNOWN, including a failure of both snapshots.
    }

    struct Calls<'a> {
        provider: &'a DriverProvider,
        capabilities: Vec<ProvidedCapability>,
        session: String,
        evidence: &'a Path,
        ordinal: u32,
    }
    impl Calls<'_> {
        async fn invoke(&mut self, command: &str, args: Value, label: &str) -> Result<Value> {
            self.ordinal += 1;
            require(self.ordinal <= 400, "direct call count budget")?;
            let cap = self
                .capabilities
                .iter()
                .find(|c| c.descriptor.name == command)
                .ok_or("direct required capability absent")?;
            let stem = self.evidence.join(format!("{:03}-{label}", self.ordinal));
            let request_id = semwright_types::unique_id();
            store::fresh_json(
                &stem.with_extension("intent.json"),
                &json!({
                "schema":"motionwright.hyperframes.direct-call-intent/1",
                "command":command,"request_id":request_id,
                "descriptor_sha256":semwright_driver_sdk::descriptor_digest(&cap.descriptor)?,
                "input_sha256":model::sha(&serde_json::to_vec(&args)?),
                "automatic_retry":false}),
            )?;
            let context = Context {
                session: self.session.clone(),
                request_id,
                cancellation: CancellationToken::new(),
            };
            let observed = tokio::time::timeout(
                Duration::from_secs(65),
                Provider::execute(self.provider, &context, &cap.descriptor, &args),
            )
            .await;
            finish_call_observation(&stem, observed)
        }
    }
    async fn execute(config: &Config, request: &Request, manifest: Manifest) -> Result<Value> {
        let grants = [
            ("runtime", &config.runtime, false),
            ("project", &config.project, true),
            ("output", &config.output, true),
            ("hyperframes-node-tool", &config.node.path, false),
        ]
        .into_iter()
        .map(|(name, path, write)| FilesystemGrant {
            name: name.into(),
            path: path.clone(),
            read: true,
            write,
        })
        .collect::<Vec<_>>();
        let provider = tokio::time::timeout(
            Duration::from_secs(65),
            DriverProvider::connect(
                manifest,
                &config.state,
                &config.sandbox_helper.path,
                &grants,
                false,
            ),
        )
        .await??;
        let caps = tokio::time::timeout(
            Duration::from_secs(65),
            Provider::capabilities(provider.as_ref()),
        )
        .await??;
        let session = format!(
            "hf-direct-{}-{}",
            std::env::var("GITHUB_RUN_ID")?,
            config.phase
        );
        let session_sha = model::sha(session.as_bytes());
        let mut calls = Calls {
            provider: provider.as_ref(),
            capabilities: caps,
            session,
            evidence: &config.evidence,
            ordinal: 0,
        };
        let doctor = calls
            .invoke(
                "driver.hyperframes.doctor",
                json!({"runtime_lock_sha256":request.runtime_lock_sha256}),
                "doctor",
            )
            .await?;
        require(
            doctor["native_execution_performed"] == false
                && doctor["runtime_lock_sha256"] == request.runtime_lock_sha256,
            "direct doctor binding",
        )?;
        let inspected = calls
            .invoke(
                "driver.hyperframes.project.inspect",
                json!({}),
                "inspect-empty",
            )
            .await?;
        require(
            inspected["source"].is_null(),
            "direct project was not empty",
        )?;
        let applied = calls.invoke("driver.hyperframes.project.apply",
            json!({"composition":request.source.composition,"expected_fingerprint":null,"dry_run":false}),
            "apply").await?;
        require(
            applied["source"] == serde_json::to_value(&request.source)?
                && applied["applied"] == true
                && applied["dry_run"] == false,
            "direct applied source binding",
        )?;
        check_export(config, request, true)?;
        // Exactly one non-idempotent start. Subsequent calls only observe its job.
        let started = calls
            .invoke(
                "driver.hyperframes.render.start",
                json!({"fingerprint":request.fingerprint,
                "runtime_lock_sha256":request.runtime_lock_sha256,
                "attempt_id":request.attempt_id}),
                "start",
            )
            .await?;
        let job = started["job_ref"]
            .as_str()
            .filter(|s| model::slug(s))
            .ok_or("direct original job absent")?
            .to_owned();
        store::fresh_json(&config.evidence.join("started.json"), &started)?;
        let deadline = Instant::now() + Duration::from_secs(360);
        let terminal = loop {
            require(
                Instant::now() < deadline,
                "direct original job deadline; outcome unknown",
            )?;
            let status = calls
                .invoke(
                    "driver.hyperframes.render.status",
                    json!({"job_ref":job}),
                    "status",
                )
                .await?;
            require(
                status["job_ref"] == job
                    && status["fingerprint"] == request.fingerprint
                    && status["runtime_lock_sha256"] == request.runtime_lock_sha256,
                "direct original job identity changed",
            )?;
            match status["state"].as_str() {
                Some("succeeded") => break status,
                Some("running" | "cancelling") => tokio::time::sleep(Duration::from_secs(1)).await,
                _ => {
                    store::fresh_json(
                        &config.evidence.join("terminal-not-verified.json"),
                        &status,
                    )?;
                    return Err("direct original job not verified; no replay".into());
                }
            }
        };
        store::fresh_json(&config.evidence.join("terminal.json"), &terminal)?;
        let result = calls
            .invoke(
                "driver.hyperframes.render.result",
                json!({"job_ref":job}),
                "result",
            )
            .await?;
        require(result == terminal, "direct terminal result changed")?;
        check_export(config, request, true)?;
        require(
            store::Store::new(config.project.clone())?.inspect()? == request.source,
            "direct actual source readback changed",
        )?;
        // This observed shutdown is only after the original job reached success.
        // Errors/UNKNOWN return without any explicit cleanup or retry operation.
        tokio::time::timeout(
            Duration::from_secs(65),
            Provider::shutdown(provider.as_ref()),
        )
        .await??;
        let summary = json!({
            "schema":"motionwright.hyperframes.direct-host-reference/1",
            "context":{"source_sha":config.source_sha,"semwright_sha":config.semwright_sha,
                "run_id":std::env::var("GITHUB_RUN_ID")?,"run_attempt":"1",
                "scenario":"hyperframes-first-party-direct-mediated-edit"},
            "phase":config.phase,"source":request.source,
            "request_sha256":config.request.sha256,
            "runtime_lock_sha256":request.runtime_lock_sha256,
            "manifest_sha256":config.manifest.sha256,"driver_sha256":config.driver.sha256,
            "node_sha256":config.node.sha256,"sandbox_helper_sha256":config.sandbox_helper.sha256,
            "session_sha256":session_sha,"result":result,
            "separate_empty_project":true,"prepared_export_equal_before":true,
            "prepared_export_equal_after":true,"direct_provider_without_broker":true,
            "original_job_terminal_observed":true,"host_shutdown_observed":true,
            "automatic_retry":false,"formal_acceptance":false});
        store::fresh_json(
            &config.evidence.join("direct-host-reference.json"),
            &summary,
        )?;
        Ok(summary)
    }

    pub async fn run() -> Result<()> {
        let args = std::env::args_os().skip(1).collect::<Vec<_>>();
        require(args.len() == 2, "direct closed arguments required")?;
        let config_path = PathBuf::from(&args[0]);
        let expected = args[1].to_str().ok_or("direct config digest invalid")?;
        let raw = store::regular(&config_path, 512 * 1024)?;
        require(
            model::sha_valid(expected) && model::sha(&raw) == expected,
            "direct config changed",
        )?;
        let config: Config = serde_json::from_slice(&raw)?;
        require(
            config.schema == "motionwright.hyperframes.direct-owner/1"
                && std::env::var("GITHUB_ACTIONS").as_deref() == Ok("true")
                && std::env::var("GITHUB_RUN_ATTEMPT").as_deref() == Ok("1")
                && std::env::var("GITHUB_SHA").as_deref() == Ok(config.source_sha.as_str())
                && std::env::var("GITHUB_RUN_ID").is_ok_and(|v| {
                    !v.is_empty()
                        && v.len() <= 20
                        && v.bytes().all(|b| b.is_ascii_digit())
                        && !v.starts_with('0')
                })
                && config.source_sha.len() == 40
                && config
                    .source_sha
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "direct original Actions identity required",
        )?;
        require_compiled_sdk_pin(&config.semwright_sha)?;
        let request = checked_request(&bytes(&config.request, 512 * 1024)?, &config.phase)?;
        for file in [&config.driver, &config.node, &config.sandbox_helper] {
            let _ = bytes(file, 512 * 1024 * 1024)?;
            require(
                fs::metadata(&file.path)?.permissions().mode() & 0o111 != 0,
                "direct executable mode required",
            )?;
        }
        let roots = [
            &config.runtime,
            &config.exported_source,
            &config.project,
            &config.output,
            &config.state,
            &config.evidence,
        ];
        for (i, left) in roots.iter().enumerate() {
            directory(left, i == 2 || i == 4 || i == 5)?;
            for right in &roots[i + 1..] {
                require(
                    !left.starts_with(right) && !right.starts_with(left),
                    "direct root overlap",
                )?;
            }
            for file in [&config.driver, &config.node, &config.sandbox_helper] {
                require(
                    !file.path.starts_with(left),
                    "direct executable overlaps native roots",
                )?;
            }
        }
        require(
            !config
                .output
                .join(format!("render-{}", request.attempt_id))
                .try_exists()?,
            "direct native attempt already exists",
        )?;
        let runtime = store::runtime(&config.runtime, &request.runtime_lock_sha256)?;
        require(
            runtime.node_sha256 == config.node.sha256,
            "direct Node differs from closure",
        )?;
        let raw_manifest = bytes(&config.manifest, 512 * 1024)?;
        require(
            serde_json::from_slice::<Value>(&raw_manifest)? == expected_manifest(&config),
            "direct manifest authority differs",
        )?;
        let manifest: Manifest = serde_json::from_slice(&raw_manifest)?;
        check_export(&config, &request, false)?;
        store::fresh_json(
            &config.evidence.join("direct-host-intent.json"),
            &json!({
            "schema":"motionwright.hyperframes.direct-host-intent/1",
            "config_sha256":expected,"request_sha256":config.request.sha256,
            "source_fingerprint":request.fingerprint,"automatic_retry":false}),
        )?;
        match execute(&config, &request, manifest).await {
            Ok(_) => {
                println!(
                    "{{\"status\":\"DIRECT_HOST_REFERENCE_VERIFIED\",\"automatic_retry\":false}}"
                );
                Ok(())
            }
            Err(mut error) => {
                retain_host_failure(
                    &config.evidence.join("direct-host-failure.json"),
                    &mut error,
                );
                Err(error)
            }
        }
    }
    #[cfg(test)]
    mod tests {
        use super::*;

        fn original_request() -> Value {
            let clips = [
                (0, 30, 40, 40, 240, 120, "#E53935", 1000, 0),
                (0, 30, 100, 70, 120, 60, "#FFFFFF", 500, 1),
                (30, 60, 320, 180, 240, 120, "#1E88E5", 1000, 0),
                (30, 60, 380, 210, 120, 60, "#FFFFFF", 500, 1),
            ]
            .into_iter()
            .enumerate()
            .map(|(i, (start, end, x, y, width, height, fill, opacity, z))| {
                json!({"id":format!("node-{:032x}",i+1),"start_frame":start,
                    "end_frame_exclusive":end,"x":x,"y":y,"width":width,"height":height,
                    "fill":fill,"opacity_milli":opacity,"z_index":z})
            })
            .collect::<Vec<_>>();
            let composition = serde_json::from_value(json!({
                "schema":"semwright.hyperframes.composition/1",
                "binding":{"project_resource":"project:00000000-0000-4000-8000-000000000001",
                    "generation":"00000000-0000-4000-8000-000000000002",
                    "revision":4,"deliverable_id":"00000000-0000-4000-8000-000000000003"},
                "width":640,"height":360,"fps":30,"frame_count":60,
                "background":"#101820","clips":clips}))
            .unwrap();
            let source = model::compile(&composition).unwrap().inventory();
            json!({"schema":"semwright.hyperframes.render-request/1",
                "fingerprint":source.fingerprint,"runtime_lock_sha256":"9".repeat(64),
                "attempt_id":format!("direct-original-{}",&source.fingerprint[..16]),
                "source":source})
        }

        fn native_error(outcome_known: bool) -> NativeError {
            serde_json::from_value(json!({
                "code": "BackendFailed",
                "message": "original native detail\nretained with escaped controls",
                "candidates": ["original request", "original receipt"],
                "outcome_known": outcome_known,
                "progress": {
                    "completed_steps": 2,
                    "failed_step": "driver.hyperframes.render.start",
                    "state": "original state"
                },
                "native_diagnostic": {
                    "phase": "NativeExchange", "reason": "UnexpectedEof",
                    "exit_code": null, "signal": null,
                    "stderr_tail_sha256": "a".repeat(64),
                    "stderr_tail_bytes": 8192, "stderr_tail_truncated": true
                }
            }))
            .unwrap()
        }

        fn allocations(error: &NativeError) -> (*const u8, *const String, *const ()) {
            (
                error.message.as_ptr(),
                error.candidates.as_ptr(),
                error.native_diagnostic.as_deref().unwrap() as *const _ as *const (),
            )
        }

        fn call_failure(
            error: &(dyn std::error::Error + Send + Sync + 'static),
        ) -> &DirectCallFailure {
            error.downcast_ref::<DirectCallFailure>().unwrap()
        }

        fn assert_native_primary(
            error: &(dyn std::error::Error + Send + Sync + 'static),
            expected: &Value,
            original_allocations: (*const u8, *const String, *const ()),
        ) {
            let DirectCallPrimary::Provider(original) = &call_failure(error).primary else {
                panic!("original native error was replaced by an observer timeout");
            };
            assert_eq!(serde_json::to_value(original).unwrap(), *expected);
            assert_eq!(allocations(original), original_allocations);
            assert!(
                std::error::Error::source(call_failure(error))
                    .unwrap()
                    .is::<NativeError>()
            );
        }

        fn read_json(path: &Path) -> Value {
            serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
        }

        #[test]
        fn direct_call_failure_retains_complete_known_and_unknown_errors() {
            for outcome_known in [true, false] {
                let directory = tempfile::tempdir().unwrap();
                let root = directory.path().canonicalize().unwrap();
                let stem = root.join("004-start");
                let original = native_error(outcome_known);
                let expected = serde_json::to_value(&original).unwrap();
                let original_allocations = allocations(&original);
                let returned = finish_call_observation(&stem, Ok(Err(original))).unwrap_err();

                assert_native_primary(returned.as_ref(), &expected, original_allocations);
                assert!(call_failure(returned.as_ref()).call_custody_error.is_none());
                let snapshot = read_json(&stem.with_extension("failure.json"));
                assert_eq!(snapshot["code"], expected["code"]);
                assert_eq!(snapshot["outcome_known"], outcome_known);
                assert_eq!(snapshot["private_original"]["native_error"], expected);
                assert_eq!(snapshot["private_original"]["origin"], "provider-error");
                assert_eq!(snapshot["private_original"]["observer_timeout"], false);
                assert_eq!(
                    snapshot["private_original"]["full_error_in_selected_capsule"],
                    false
                );
                assert_eq!(snapshot["automatic_retry"], false);
                assert!(!stem.with_extension("result.json").exists());
                assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
            }
        }

        #[test]
        fn direct_call_failure_existing_snapshot_keeps_primary_in_host_fallback() {
            let directory = tempfile::tempdir().unwrap();
            let root = directory.path().canonicalize().unwrap();
            let stem = root.join("003-apply");
            let call_path = stem.with_extension("failure.json");
            let host_path = root.join("direct-host-failure.json");
            let prior = b"prior call original must not be overwritten\n";
            fs::write(&call_path, prior).unwrap();
            let original = native_error(false);
            let expected = serde_json::to_value(&original).unwrap();
            let original_allocations = allocations(&original);
            let mut returned = finish_call_observation(&stem, Ok(Err(original))).unwrap_err();

            assert_native_primary(returned.as_ref(), &expected, original_allocations);
            let secondary = serde_json::to_value(
                call_failure(returned.as_ref())
                    .call_custody_error
                    .as_ref()
                    .unwrap(),
            )
            .unwrap();
            retain_host_failure(&host_path, &mut returned);

            assert_native_primary(returned.as_ref(), &expected, original_allocations);
            assert_eq!(fs::read(&call_path).unwrap(), prior);
            let fallback = read_json(&host_path);
            assert_eq!(fallback["private_original"]["native_error"], expected);
            assert_eq!(
                fallback["private_original"]["call_custody_error"],
                secondary
            );
            assert!(call_failure(returned.as_ref()).host_custody_error.is_none());
            assert_eq!(fallback["state"], "NOT_VERIFIED");
            assert_eq!(fallback["automatic_retry"], false);
            assert_eq!(fs::read_dir(&root).unwrap().count(), 2);
        }

        #[test]
        fn direct_call_failure_both_snapshots_blocked_keeps_primary_and_secondaries() {
            let directory = tempfile::tempdir().unwrap();
            let root = directory.path().canonicalize().unwrap();
            let stem = root.join("004-start");
            let call_path = stem.with_extension("failure.json");
            let host_path = root.join("direct-host-failure.json");
            let prior_call = b"original call reservation\n";
            let prior_host = b"original host reservation\n";
            fs::write(&call_path, prior_call).unwrap();
            fs::write(&host_path, prior_host).unwrap();
            let original = native_error(true);
            let expected = serde_json::to_value(&original).unwrap();
            let original_allocations = allocations(&original);
            let mut returned = finish_call_observation(&stem, Ok(Err(original))).unwrap_err();

            retain_host_failure(&host_path, &mut returned);

            assert_native_primary(returned.as_ref(), &expected, original_allocations);
            assert!(call_failure(returned.as_ref()).call_custody_error.is_some());
            assert!(call_failure(returned.as_ref()).host_custody_error.is_some());
            assert_eq!(fs::read(&call_path).unwrap(), prior_call);
            assert_eq!(fs::read(&host_path).unwrap(), prior_host);
            assert!(!stem.with_extension("result.json").exists());
            assert_eq!(fs::read_dir(&root).unwrap().count(), 2);
        }

        #[tokio::test]
        async fn direct_observer_timeout_keeps_elapsed_and_unknown_after_custody_failure() {
            for block_custody in [false, true] {
                let directory = tempfile::tempdir().unwrap();
                let root = directory.path().canonicalize().unwrap();
                let stem = root.join("004-start");
                let call_path = stem.with_extension("failure.json");
                let host_path = root.join("direct-host-failure.json");
                if block_custody {
                    fs::write(&call_path, b"prior call").unwrap();
                    fs::write(&host_path, b"prior host").unwrap();
                }
                let observed = tokio::time::timeout(
                    Duration::from_millis(1),
                    std::future::pending::<std::result::Result<Value, NativeError>>(),
                )
                .await;
                assert!(observed.is_err());
                let mut returned = finish_call_observation(&stem, observed).unwrap_err();
                retain_host_failure(&host_path, &mut returned);

                let failure = call_failure(returned.as_ref());
                assert!(matches!(
                    &failure.primary,
                    DirectCallPrimary::ObserverTimeout(_)
                ));
                assert!(!failure.outcome_known());
                assert_eq!(failure.code(), "Timeout");
                assert!(
                    std::error::Error::source(failure)
                        .unwrap()
                        .is::<tokio::time::error::Elapsed>()
                );
                assert_eq!(failure.call_custody_error.is_some(), block_custody);
                assert_eq!(failure.host_custody_error.is_some(), block_custody);
                if block_custody {
                    assert_eq!(fs::read(&call_path).unwrap(), b"prior call");
                    assert_eq!(fs::read(&host_path).unwrap(), b"prior host");
                } else {
                    let snapshot = read_json(&call_path);
                    assert_eq!(snapshot["code"], "Timeout");
                    assert_eq!(snapshot["outcome_known"], false);
                    assert!(snapshot["private_original"]["native_error"].is_null());
                    assert_eq!(snapshot["private_original"]["origin"], "observer-timeout");
                    assert_eq!(snapshot["automatic_retry"], false);
                }
                assert!(!stem.with_extension("result.json").exists());
            }
        }

        #[test]
        fn direct_success_retains_result_without_inventing_failure() {
            let directory = tempfile::tempdir().unwrap();
            let root = directory.path().canonicalize().unwrap();
            let stem = root.join("004-start");
            let observed = json!({"job_ref": "synthetic-source-contract", "state": "running"});
            let returned = finish_call_observation(&stem, Ok(Ok(observed.clone()))).unwrap();
            assert_eq!(returned, observed);
            assert_eq!(read_json(&stem.with_extension("result.json")), observed);
            assert!(!stem.with_extension("failure.json").exists());
            assert_eq!(fs::read_dir(root).unwrap().count(), 1);
        }

        #[test]
        fn confirmed_response_survives_result_and_host_custody_failures_without_replay() {
            for block_host in [false, true] {
                let directory = tempfile::tempdir().unwrap();
                let root = directory.path().canonicalize().unwrap();
                let stem = root.join("004-start");
                let result_path = stem.with_extension("result.json");
                let host_path = root.join("direct-host-failure.json");
                let prior_result = b"prior result reservation\n";
                fs::write(&result_path, prior_result).unwrap();
                if block_host {
                    fs::write(&host_path, b"prior host reservation").unwrap();
                }
                let original = json!({
                    "job_ref": "original-confirmed-response",
                    "state": "running",
                    "opaque": {"text": "original\nresponse", "values": [null, true, 18446744073709551615_u64]}
                });
                let expected = original.clone();
                let pointer = original["job_ref"].as_str().unwrap().as_ptr();
                let mut returned = finish_call_observation(&stem, Ok(Ok(original))).unwrap_err();
                retain_host_failure(&host_path, &mut returned);

                let failure = call_failure(returned.as_ref());
                let DirectCallPrimary::ConfirmedResponse(value) = &failure.primary else {
                    panic!("confirmed native response was changed into failure or UNKNOWN");
                };
                assert_eq!(*value, expected);
                assert_eq!(value["job_ref"].as_str().unwrap().as_ptr(), pointer);
                assert!(failure.outcome_known());
                assert_eq!(failure.code(), "NativeResultCustodyFailed");
                assert!(failure.call_custody_error.is_some());
                assert_eq!(failure.host_custody_error.is_some(), block_host);
                assert_eq!(fs::read(&result_path).unwrap(), prior_result);
                assert!(!stem.with_extension("failure.json").exists());
                if block_host {
                    assert_eq!(fs::read(&host_path).unwrap(), b"prior host reservation");
                } else {
                    let snapshot = read_json(&host_path);
                    assert_eq!(snapshot["state"], "NOT_VERIFIED");
                    assert_eq!(
                        snapshot["private_original"]["confirmed_response"]["payload"],
                        expected
                    );
                    assert_eq!(snapshot["private_original"]["origin"], "confirmed-response");
                    assert_eq!(snapshot["private_original"]["outcome_known"], true);
                    assert_eq!(snapshot["private_original"]["observer_timeout"], false);
                    assert!(snapshot["private_original"]["native_error"].is_null());
                    assert_eq!(
                        snapshot["private_original"]["full_error_in_selected_capsule"],
                        false
                    );
                    assert_eq!(snapshot["automatic_retry"], false);
                }
                assert_eq!(fs::read_dir(&root).unwrap().count(), 2);
            }
        }

        #[test]
        fn compiled_sdk_pin_rejects_runtime_substitution() {
            let original = compiled_sdk_pin().unwrap();
            require_compiled_sdk_pin(&original).unwrap();
            let replacement = if original == "0".repeat(40) {
                "1".repeat(40)
            } else {
                "0".repeat(40)
            };
            assert!(require_compiled_sdk_pin(&replacement).is_err());
            assert!(require_compiled_sdk_pin("unknown").is_err());
        }

        #[test]
        fn compiled_sdk_pin_rejects_unverified_or_mismatched_lock() {
            let original: Value =
                serde_json::from_str(include_str!("../../../SOURCE_LOCK.json")).unwrap();
            for field in ["status", "revision"] {
                let mut changed = original.clone();
                changed["dependencies"]["semwright"]["verification"][field] = json!("different");
                assert!(
                    sdk_pin_from_source_lock(&serde_json::to_string(&changed).unwrap()).is_err()
                );
            }
            let mut changed = original;
            changed["dependencies"]["semwright"]["repository"] = json!("https://other.invalid/sdk");
            assert!(sdk_pin_from_source_lock(&serde_json::to_string(&changed).unwrap()).is_err());
        }

        #[test]
        fn original_compiled_request_is_admitted_without_a_host() {
            let raw = serde_json::to_vec(&original_request()).unwrap();
            assert!(checked_request(&raw, "original").is_ok());
        }

        #[test]
        fn changed_source_inventory_is_rejected_before_a_host() {
            let mut value = original_request();
            value["source"]["source_sha256"] = json!("0".repeat(64));
            assert!(checked_request(&serde_json::to_vec(&value).unwrap(), "original").is_err());
        }

        #[test]
        fn wrong_phase_attempt_is_rejected_before_a_host() {
            let raw = serde_json::to_vec(&original_request()).unwrap();
            assert!(checked_request(&raw, "edited").is_err());
        }

        #[test]
        fn request_cannot_inject_an_unreviewed_execution_option() {
            let mut value = original_request();
            value["command"] = json!("/unreviewed/renderer");
            assert!(checked_request(&serde_json::to_vec(&value).unwrap(), "original").is_err());
        }
    }
}
#[cfg(target_os = "linux")]
#[tokio::main]
async fn main() {
    if linux::run().await.is_err() {
        eprintln!("hyperframes-direct=NOT_VERIFIED; automatic_retry=false");
        std::process::exit(1);
    }
}
#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("hyperframes-direct requires the reviewed Linux Driver Host");
    std::process::exit(2);
}
