use super::{
    BrokerResult, Error, ErrorCode, NativeResult, ProductionClient, ProductionCoordinator,
    SourceKind, backend, is_sha256, storage_error,
};
use motionwright_domain::RevisionStamp;
use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

const MANIM_DOCTOR: &str = "driver.manim-community.doctor";
const MANIM_PROVIDER: &str = "driver:manim-community";
const MANIM_VERSION: &str = "0.21.0";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ManimRuntimeVersionState {
    ObservedCompatible,
    ObservedIncompatible,
    PackageNotFound,
    NotObserved,
}

/// A bounded observation from the existing owner-pinned Driver Host.
/// Package absence is limited to its declared runtime. It is not an install,
/// render-readiness, or native GUI acceptance certificate.
#[derive(Debug, Clone, Serialize)]
pub struct ManimRuntimeVersionObservation {
    schema: &'static str,
    stamp: RevisionStamp,
    connection_identity: String,
    executable_sha256: String,
    broker_request_id: String,
    provider: &'static str,
    provider_generation: u64,
    descriptor_sha256: String,
    expected_version: &'static str,
    observed_version: Option<String>,
    version_compatible: Option<bool>,
    state: ManimRuntimeVersionState,
    observation_scope: &'static str,
    observation_source: &'static str,
    reason_code: &'static str,
    render_performed: bool,
    scene_mutation_performed: bool,
}

impl ManimRuntimeVersionObservation {
    pub fn state(&self) -> ManimRuntimeVersionState {
        self.state
    }
    pub fn observed_version(&self) -> Option<&str> {
        self.observed_version.as_deref()
    }
    pub fn version_compatible(&self) -> Option<bool> {
        self.version_compatible
    }
    pub fn provider_generation(&self) -> u64 {
        self.provider_generation
    }
    pub fn broker_request_id(&self) -> &str {
        &self.broker_request_id
    }
    pub fn reason_code(&self) -> &str {
        self.reason_code
    }
    pub fn matches_scope(&self, expected: &RevisionStamp) -> bool {
        self.stamp.resource == expected.resource
            && self.stamp.generation == expected.generation
            && self.stamp.revision == expected.revision
    }
}

struct ParsedManimProbe {
    state: ManimRuntimeVersionState,
    observed_version: Option<String>,
    compatible: Option<bool>,
    source: &'static str,
    reason: &'static str,
}

fn bounded_token(value: Option<&Value>, max: usize) -> NativeResult<&str> {
    value
        .and_then(Value::as_str)
        .filter(|value| {
            !value.is_empty()
                && value.len() <= max
                && !value
                    .chars()
                    .any(|ch| ch.is_control() || ch.is_whitespace())
        })
        .ok_or_else(|| backend("Manim runtime observation token is out of bounds"))
}

fn parse_manim_probe(data: &Value) -> NativeResult<ParsedManimProbe> {
    if !data.as_object().is_some_and(|object| object.len() == 9)
        || data.get("driver").and_then(Value::as_str) != Some("manim-community")
        || data.get("runtime").and_then(Value::as_str) != Some("Manim Community")
        || data.get("runtime_version_contract").and_then(Value::as_str) != Some(MANIM_VERSION)
        || data.get("host_tools").and_then(Value::as_bool).is_none()
        || data.get("arbitrary_python").and_then(Value::as_bool) != Some(false)
        || data.get("network").and_then(Value::as_bool) != Some(false)
        || data.get("max_jobs").and_then(Value::as_u64) != Some(32)
    {
        return Err(backend("Manim doctor response contract is unsupported"));
    }
    bounded_token(data.get("driver_version"), 80)?;
    let probe = data
        .get("runtime_probe")
        .ok_or_else(|| backend("Manim native runtime observation is missing"))?;
    if !probe.as_object().is_some_and(|object| object.len() == 11)
        || probe.get("schema").and_then(Value::as_u64) != Some(1)
        || probe.get("operation").and_then(Value::as_str) != Some("runtime_version_probe")
        || probe.get("requested").and_then(Value::as_bool) != Some(true)
        || probe.get("expected_version").and_then(Value::as_str) != Some(MANIM_VERSION)
        || probe.get("render_performed").and_then(Value::as_bool) != Some(false)
        || probe
            .get("scene_mutation_performed")
            .and_then(Value::as_bool)
            != Some(false)
    {
        return Err(backend(
            "Manim runtime probe response contract is unsupported",
        ));
    }
    match probe.get("state").and_then(Value::as_str) {
        Some("observed") => {
            if data.get("host_tools").and_then(Value::as_bool) != Some(true)
                || probe.get("source").and_then(Value::as_str) != Some("host_native_package_probe")
            {
                return Err(backend("Manim version requires a native Host observation"));
            }
            let observed = bounded_token(probe.get("observed_version"), 80)?;
            let compatible = observed == MANIM_VERSION;
            let reason = if compatible {
                "version_observed"
            } else {
                "version_mismatch"
            };
            if probe.get("compatible").and_then(Value::as_bool) != Some(compatible)
                || probe.get("reason_code").and_then(Value::as_str) != Some(reason)
            {
                return Err(backend(
                    "Manim native version compatibility is inconsistent",
                ));
            }
            Ok(ParsedManimProbe {
                state: if compatible {
                    ManimRuntimeVersionState::ObservedCompatible
                } else {
                    ManimRuntimeVersionState::ObservedIncompatible
                },
                observed_version: Some(observed.to_owned()),
                compatible: Some(compatible),
                source: "host_native_package_probe",
                reason,
            })
        }
        Some("package_not_found") => {
            if data.get("host_tools").and_then(Value::as_bool) != Some(true)
                || probe.get("source").and_then(Value::as_str) != Some("host_native_package_probe")
                || probe.get("observed_version") != Some(&Value::Null)
                || probe.get("compatible") != Some(&Value::Null)
                || probe.get("reason_code").and_then(Value::as_str)
                    != Some("package_not_found_in_declared_runtime")
            {
                return Err(backend("Manim package observation is inconsistent"));
            }
            Ok(ParsedManimProbe {
                state: ManimRuntimeVersionState::PackageNotFound,
                observed_version: None,
                compatible: None,
                source: "host_native_package_probe",
                reason: "package_not_found_in_declared_runtime",
            })
        }
        Some("not_observed") => {
            if probe.get("source").and_then(Value::as_str) != Some("none")
                || probe.get("observed_version") != Some(&Value::Null)
                || probe.get("compatible") != Some(&Value::Null)
            {
                return Err(backend("Manim unobserved runtime response is inconsistent"));
            }
            let reason = match probe.get("reason_code").and_then(Value::as_str) {
                Some("host_context_unavailable") => "host_context_unavailable",
                Some("host_tools_unavailable") => "host_tools_unavailable",
                Some("host_probe_failed") => "host_probe_failed",
                Some("runner_failed") => "runner_failed",
                Some("invalid_runner_response") => "invalid_runner_response",
                _ => return Err(backend("Manim runtime observation reason is unsupported")),
            };
            Ok(ParsedManimProbe {
                state: ManimRuntimeVersionState::NotObserved,
                observed_version: None,
                compatible: None,
                source: "none",
                reason,
            })
        }
        _ => Err(backend("Manim runtime observation state is unsupported")),
    }
}

fn authenticated_manim_observation(
    original: &BrokerResult,
    expected: &RevisionStamp,
    client: &ProductionClient,
) -> NativeResult<ManimRuntimeVersionObservation> {
    let value = &original.value;
    if value.get("provenance_checked").and_then(Value::as_bool) != Some(true)
        || value.pointer("/execution/dry_run").and_then(Value::as_bool) != Some(false)
        || value
            .pointer("/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some(MANIM_PROVIDER)
        || value.pointer("/execution/provenance/source") != Some(&json!(SourceKind::Driver))
    {
        return Err(backend(
            "Manim runtime observation lacks authenticated native provenance",
        ));
    }
    let provider_generation = value
        .pointer("/execution/provenance/provider_generation")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("Manim runtime observation has no provider generation"))?;
    let descriptor_sha256 =
        bounded_token(value.pointer("/execution/provenance/descriptor_sha256"), 64)?;
    if !is_sha256(descriptor_sha256) {
        return Err(backend(
            "Manim runtime observation descriptor digest is invalid",
        ));
    }
    if original.request_id.is_empty()
        || original.request_id.len() > 128
        || original.request_id.chars().any(char::is_control)
    {
        return Err(backend(
            "Manim runtime observation request identity is invalid",
        ));
    }
    let parsed = parse_manim_probe(
        value
            .get("data")
            .ok_or_else(|| backend("Manim runtime observation data is missing"))?,
    )?;
    Ok(ManimRuntimeVersionObservation {
        schema: "motionwright-manim-runtime-observation/1",
        stamp: RevisionStamp {
            resource: expected.resource.clone(),
            generation: expected.generation,
            revision: expected.revision,
        },
        connection_identity: client.connection().identity()?,
        executable_sha256: client.connection().executable_sha256.clone(),
        broker_request_id: original.request_id.clone(),
        provider: MANIM_PROVIDER,
        provider_generation,
        descriptor_sha256: descriptor_sha256.to_owned(),
        expected_version: MANIM_VERSION,
        observed_version: parsed.observed_version,
        version_compatible: parsed.compatible,
        state: parsed.state,
        observation_scope: "declared_runtime",
        observation_source: parsed.source,
        reason_code: parsed.reason,
        render_performed: false,
        scene_mutation_performed: false,
    })
}

impl ProductionClient {
    /// Observe the fixed native package through the existing Driver Host.
    /// Transport/provider errors retain their original type; they do not
    /// establish that a package or application is absent.
    pub async fn observe_manim_runtime_version(
        &self,
        expected: &RevisionStamp,
    ) -> NativeResult<ManimRuntimeVersionObservation> {
        if self.connection().resource != expected.resource {
            return Err(Error::new(
                ErrorCode::PolicyDenied,
                "Manim runtime observation connection is bound to another project resource",
            ));
        }
        let original = self
            .execute(MANIM_DOCTOR, json!({"probe_runtime": true}), false)
            .await?;
        authenticated_manim_observation(&original, expected, self)
    }
}

impl ProductionCoordinator {
    fn require_manim_runtime_probe_scope(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
    ) -> NativeResult<()> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed during the Manim runtime observation",
            ));
        }
        Ok(())
    }

    pub async fn observe_manim_runtime_version(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
    ) -> NativeResult<ManimRuntimeVersionObservation> {
        self.require_manim_runtime_probe_scope(project_id, expected)?;
        // A failed original probe has no observation to admit. Preserve its
        // typed error before any later project/storage revalidation can fail.
        let observation = self.client.observe_manim_runtime_version(expected).await?;
        self.require_manim_runtime_probe_scope(project_id, expected)?;
        Ok(observation)
    }
}

#[cfg(test)]
pub(super) fn manim_runtime_test_data(version: Option<&str>) -> Value {
    let compatible = version.map(|value| value == MANIM_VERSION);
    json!({
        "driver":"manim-community", "driver_version":"0.1.0",
        "runtime":"Manim Community", "runtime_version_contract":MANIM_VERSION,
        "host_tools":true, "arbitrary_python":false, "network":false, "max_jobs":32,
        "runtime_probe":{
            "schema":1, "operation":"runtime_version_probe", "requested":true,
            "source":"host_native_package_probe",
            "state":if version.is_some() { "observed" } else { "package_not_found" },
            "expected_version":MANIM_VERSION, "observed_version":version, "compatible":compatible,
            "reason_code":match compatible {
                Some(true) => "version_observed",
                Some(false) => "version_mismatch",
                None => "package_not_found_in_declared_runtime",
            },
            "render_performed":false, "scene_mutation_performed":false,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manim_runtime_version_parser_preserves_four_distinct_outcomes() {
        for (version, state, compatible) in [
            (
                Some("0.21.0"),
                ManimRuntimeVersionState::ObservedCompatible,
                Some(true),
            ),
            (
                Some("0.20.1"),
                ManimRuntimeVersionState::ObservedIncompatible,
                Some(false),
            ),
            (None, ManimRuntimeVersionState::PackageNotFound, None),
        ] {
            let parsed = parse_manim_probe(&manim_runtime_test_data(version)).unwrap();
            assert_eq!(parsed.state, state);
            assert_eq!(parsed.observed_version.as_deref(), version);
            assert_eq!(parsed.compatible, compatible);
        }
        let mut unavailable = manim_runtime_test_data(None);
        unavailable["runtime_probe"]["state"] = json!("not_observed");
        unavailable["runtime_probe"]["source"] = json!("none");
        unavailable["runtime_probe"]["reason_code"] = json!("runner_failed");
        let parsed = parse_manim_probe(&unavailable).unwrap();
        assert_eq!(parsed.state, ManimRuntimeVersionState::NotObserved);
        assert_eq!(parsed.observed_version, None);
        assert_eq!(parsed.compatible, None);
        assert_eq!(parsed.reason, "runner_failed");
    }

    #[test]
    fn manim_runtime_version_parser_rejects_metadata_and_forged_observations() {
        let base = manim_runtime_test_data(Some("0.21.0"));
        for (pointer, replacement) in [
            ("/host_tools", json!(false)),
            ("/runtime_probe/requested", json!(false)),
            ("/runtime_probe/source", json!("runtime_contract")),
            ("/runtime_probe/expected_version", json!("0.20.1")),
            ("/runtime_probe/observed_version", Value::Null),
            ("/runtime_probe/observed_version", json!("0.21.0\n")),
            ("/runtime_probe/observed_version", json!("x".repeat(81))),
            ("/runtime_probe/compatible", json!(false)),
            ("/runtime_probe/reason_code", json!("not_requested")),
            ("/runtime_probe/render_performed", json!(true)),
            ("/runtime_probe/scene_mutation_performed", json!(true)),
        ] {
            let mut changed = base.clone();
            *changed.pointer_mut(pointer).unwrap() = replacement;
            assert!(parse_manim_probe(&changed).is_err(), "{pointer}");
        }
        let mut metadata_only = base.clone();
        metadata_only
            .as_object_mut()
            .unwrap()
            .remove("runtime_probe");
        assert!(parse_manim_probe(&metadata_only).is_err());
        let mut expanded = base.clone();
        expanded["runtime_probe"]["injected"] = json!(true);
        assert!(parse_manim_probe(&expanded).is_err());
        let mut mismatch = manim_runtime_test_data(Some("0.20.1"));
        mismatch["runtime_probe"]["compatible"] = json!(true);
        assert!(parse_manim_probe(&mismatch).is_err());
        let mut false_absence = manim_runtime_test_data(None);
        false_absence["runtime_probe"]["observed_version"] = json!("0.21.0");
        assert!(parse_manim_probe(&false_absence).is_err());
    }

    #[cfg(unix)]
    async fn probe_after_real_peer_revision(fail_doctor: bool) -> (Error, Error) {
        use crate::production::tests::{fake_transport_connection, transport_reply};
        use motionwright_domain::Change;
        use motionwright_service::StudioService;
        use std::{fs, time::Duration};

        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("motionwright.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service
            .create_project("Probe scope before peer commit")
            .unwrap();
        let expected = project.stamp();
        let original_error = Error::new(ErrorCode::Timeout, "original doctor transport error")
            .uncertain()
            .recipe_progress(1, MANIM_DOCTOR);
        let mut envelope = transport_reply(MANIM_DOCTOR);
        if fail_doctor {
            envelope.ok = false;
            envelope.data = None;
            envelope.error = Some(original_error.clone());
        } else {
            envelope.data = Some(manim_runtime_test_data(Some(MANIM_VERSION)));
        }
        let output = serde_json::to_string(&envelope).unwrap();
        let (mut connection, counter) = fake_transport_connection(
            &temporary,
            project.resource_key(),
            &output,
            "",
            if fail_doctor { 19 } else { 0 },
        );
        let release = temporary.path().join("release-probe-reply");
        let script = fs::read_to_string(&connection.executable).unwrap();
        // The child has entered the original transport but cannot return its
        // reply until another SQLite connection commits a real revision.
        let waiting = format!(
            "\nwhile [ ! -f '{}' ]; do sleep 0.01; done\nprintf '%s'",
            release.to_string_lossy().replace('\'', "'\\''"),
        );
        let script = script.replacen("\nprintf '%s'", &waiting, 1);
        assert!(script.contains("release-probe-reply"));
        fs::write(&connection.executable, script).unwrap();
        connection.executable_sha256 =
            crate::production::sha256_file(&connection.executable).unwrap();
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let peer_commit = async {
            let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
            while !counter.exists() {
                assert!(
                    tokio::time::Instant::now() < deadline,
                    "original probe did not start"
                );
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            let peer = StudioService::open(&database).unwrap();
            let edited = peer
                .apply(
                    project.id,
                    &expected,
                    "peer-commit-during-original-probe",
                    &Change::RenameProject {
                        title: "Peer committed during probe".into(),
                    },
                )
                .unwrap()
                .project;
            assert_eq!(edited.revision, project.revision + 1);
            fs::write(&release, b"release once").unwrap();
            edited
        };
        let (result, edited) = tokio::join!(
            coordinator.observe_manim_runtime_version(project.id, &expected),
            peer_commit,
        );
        let returned = result.unwrap_err();
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(service.project(project.id).unwrap(), edited);
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        (returned, original_error)
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn original_probe_error_is_preserved_after_peer_revision_commits() {
        let (returned, original) = probe_after_real_peer_revision(true).await;
        assert_eq!(
            serde_json::to_value(returned).unwrap(),
            serde_json::to_value(original).unwrap()
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn successful_probe_is_not_admitted_after_peer_revision_commits() {
        let (returned, _) = probe_after_real_peer_revision(false).await;
        assert_eq!(returned.code, ErrorCode::StaleReference);
    }
}
