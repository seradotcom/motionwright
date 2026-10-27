//! Read-only consumers of the selected canonical Broker and Driver Host.
//! CLI compatibility, provider registration and a native package version remain
//! separate observations. None of them creates a grant or establishes render readiness.
use crate::runtime_setup::RuntimeConnectionRegistry;
use motionwright_domain::{Project, RevisionStamp};
use motionwright_native::{
    creative_doctor::{
        CliEvidence, CreativeRuntimeDoctorReport, inspect_creative_runtime,
        inspect_creative_runtime_with_registry,
    },
    production::{
        ManimRuntimeVersionObservation, ManimRuntimeVersionState, NativeRuntimeVersionObservation,
        NativeRuntimeVersionState, ProductionClient, ProductionConnection,
        RendererRegistryObservation, RendererRegistryState,
    },
};
use motionwright_service::StudioService;
use serde::Serialize;
use uuid::Uuid;

pub(crate) struct DoctorRequest<'a> {
    pub project_id: Uuid,
    pub expected: &'a RevisionStamp,
    pub source_pin: &'a str,
    pub cli: CliEvidence,
    pub connection_identity: Option<&'a str>,
    pub executable_sha256: Option<&'a str>,
}

/// Additive Desktop evidence. These values are outputs of the canonical
/// authenticated client; they are never accepted as caller authority.
#[derive(Debug, Serialize)]
pub(crate) struct RuntimeDoctorReport {
    #[serde(flatten)]
    pub report: CreativeRuntimeDoctorReport,
    pub registry_observation: Option<RegistryObservation>,
    pub manim_runtime_observation: Option<ManimRuntimeVersionObservation>,
    pub native_runtime_observations: Vec<NativeRuntimeVersionObservation>,
    pub native_runtime_probe_failures: Vec<NativeRuntimeProbeFailure>,
}

#[derive(Debug, Serialize)]
pub(crate) struct RegistryObservation {
    pub stamp: RevisionStamp,
    pub connection_identity: String,
    pub executable_sha256: String,
    pub broker_request_id: String,
    pub catalog_revision: u64,
    pub providers: Vec<RegistryProviderObservation>,
}

#[derive(Debug, Serialize)]
pub(crate) struct RegistryProviderObservation {
    pub renderer: &'static str,
    pub provider: &'static str,
    pub state: &'static str,
    pub provider_generation: Option<u64>,
    pub provider_version: Option<String>,
}

/// A failed read is not evidence of installation, absence or compatibility.
/// The generation is the registry expectation, not a claimed probe result.
#[derive(Debug, Serialize)]
pub(crate) struct NativeRuntimeProbeFailure {
    pub renderer: &'static str,
    pub provider: &'static str,
    pub registry_provider_generation: u64,
    pub state: &'static str,
    pub version_compatible: Option<bool>,
    pub error_code: semwright_native_sdk::ErrorCode,
    pub outcome_known: bool,
    pub reason_code: &'static str,
}

#[derive(Clone, Copy)]
enum NativeProbe {
    Blender,
    Mlt,
    MotionCanvas,
}

impl NativeProbe {
    fn renderer(self) -> &'static str {
        match self {
            Self::Blender => "blender",
            Self::Mlt => "mlt",
            Self::MotionCanvas => "motion-canvas",
        }
    }
    fn provider(self) -> &'static str {
        match self {
            Self::Blender => "driver:blender",
            Self::Mlt => "driver:mlt-video",
            Self::MotionCanvas => "driver:motion-canvas",
        }
    }
    fn version_check(self) -> &'static str {
        match self {
            Self::Blender => "native_runtime_blender_version",
            Self::Mlt => "native_runtime_mlt_version",
            Self::MotionCanvas => "native_runtime_motion_canvas_version",
        }
    }
}

fn include_native_observation(
    report: &mut CreativeRuntimeDoctorReport,
    expected: &RevisionStamp,
    probe: NativeProbe,
    provider_generation: u64,
    observation: &NativeRuntimeVersionObservation,
) -> Result<(), String> {
    if observation.renderer() != probe.renderer()
        || !observation.matches_scope(expected)
        || observation.provider_generation() != provider_generation
    {
        return Err(
            "The native provider generation or project changed during diagnosis; refresh and recheck."
                .into(),
        );
    }
    let (status, renderer_state) = match observation.state() {
        NativeRuntimeVersionState::ObservedCompatible => {
            ("validated", "native_runtime_version_compatible")
        }
        NativeRuntimeVersionState::ObservedIncompatible => {
            ("blocked", "native_runtime_version_incompatible")
        }
        NativeRuntimeVersionState::Observed => ("observed", "native_runtime_version_observed"),
        NativeRuntimeVersionState::PackageNotFound => {
            ("blocked", "native_package_not_found_in_declared_runtime")
        }
        NativeRuntimeVersionState::NotObserved => ("not_tested", "native_runtime_not_observed"),
    };
    let check = report
        .checks
        .iter_mut()
        .find(|check| check.id == probe.version_check())
        .ok_or_else(|| "The runtime report is missing its native version check.".to_owned())?;
    check.status = status;
    check.detail = format!(
        "provider={}; provider_generation={}; observed_version={}; expected_version={}; compatible={}; freshness={}; reason={}; broker_request_id={}. This is a scoped native version observation; it does not establish rendering, font admission or effect authority.",
        probe.provider(),
        observation.provider_generation(),
        observation.observed_version().unwrap_or("not_observed"),
        observation
            .expected_version()
            .unwrap_or("no_global_compatibility_policy"),
        observation
            .version_compatible()
            .map_or("not_assessed", |compatible| {
                if compatible { "true" } else { "false" }
            }),
        observation.observation_freshness(),
        observation.reason_code(),
        observation.broker_request_id(),
    );
    let renderer = report
        .renderers
        .iter_mut()
        .find(|renderer| renderer.renderer == probe.renderer())
        .ok_or_else(|| "The runtime report is missing its renderer row.".to_owned())?;
    renderer.state = renderer_state;
    Ok(())
}

fn include_native_failure(
    report: &mut CreativeRuntimeDoctorReport,
    probe: NativeProbe,
    provider_generation: u64,
    error: &semwright_native_sdk::Error,
) -> Result<NativeRuntimeProbeFailure, String> {
    let check = report
        .checks
        .iter_mut()
        .find(|check| check.id == probe.version_check())
        .ok_or_else(|| "The runtime report is missing its native version check.".to_owned())?;
    check.status = "not_tested";
    let dependency = if matches!(probe, NativeProbe::MotionCanvas) {
        " Motion Canvas requires the reviewed opt-in package-probe contract; a metadata-only doctor response is not native version evidence."
    } else {
        ""
    };
    check.detail = format!(
        "provider={}; registry_provider_generation={}; probe_error={:?}; outcome_known={}. Native availability and compatibility remain unobserved. The failed request was not retried.{}",
        probe.provider(),
        provider_generation,
        error.code,
        error.outcome_known,
        dependency,
    );
    let renderer = report
        .renderers
        .iter_mut()
        .find(|renderer| renderer.renderer == probe.renderer())
        .ok_or_else(|| "The runtime report is missing its renderer row.".to_owned())?;
    renderer.state = "native_runtime_not_observed";
    Ok(NativeRuntimeProbeFailure {
        renderer: probe.renderer(),
        provider: probe.provider(),
        registry_provider_generation: provider_generation,
        state: "not_observed",
        version_compatible: None,
        error_code: error.code,
        outcome_known: error.outcome_known,
        reason_code: "native_runtime_probe_failed",
    })
}

fn registry_evidence(
    observation: &RendererRegistryObservation,
    expected: &RevisionStamp,
    identity: &RuntimeIdentity,
) -> Result<RegistryObservation, String> {
    if !observation.matches_scope(expected) {
        return Err("The Broker registry belongs to a different project scope.".into());
    }
    let providers = [
        ("motion-canvas", "driver:motion-canvas"),
        ("mlt", "driver:mlt-video"),
        ("blender", "driver:blender"),
        ("manim-community", "driver:manim-community"),
    ]
    .into_iter()
    .map(|(renderer, provider)| {
        let state = observation.state_for(renderer).ok_or_else(|| {
            "The authenticated registry is missing a supported renderer state.".to_owned()
        })?;
        Ok(RegistryProviderObservation {
            renderer,
            provider,
            state: state.label(),
            provider_generation: state.generation(),
            provider_version: observation
                .provider_version_for(renderer)
                .map(str::to_owned),
        })
    })
    .collect::<Result<Vec<_>, String>>()?;
    Ok(RegistryObservation {
        stamp: expected.clone(),
        connection_identity: identity.connection.clone(),
        executable_sha256: identity.executable.clone(),
        broker_request_id: observation.broker_request_id().to_owned(),
        catalog_revision: observation.catalog_revision(),
        providers,
    })
}

#[derive(Debug, Clone)]
struct RuntimeIdentity {
    connection: String,
    executable: String,
}

fn require_scope(project: &Project, expected: &RevisionStamp) -> Result<(), String> {
    if project.resource_key() != expected.resource
        || project.generation != expected.generation
        || project.revision != expected.revision
    {
        return Err("Project changed during runtime diagnosis; refresh and recheck.".into());
    }
    Ok(())
}

fn require_identity(
    expected: &RuntimeIdentity,
    connection_identity: &str,
    executable_sha256: &str,
) -> Result<(), String> {
    if expected.connection.is_empty()
        || expected.executable.is_empty()
        || expected.connection != connection_identity
        || expected.executable != executable_sha256
    {
        return Err(
            "The selected runtime connection changed during diagnosis; refresh and recheck.".into(),
        );
    }
    Ok(())
}

fn current_connection(
    owner: &crate::desktop_owner::DesktopOwner,
    service: &StudioService,
    connections: &RuntimeConnectionRegistry,
    request: &DoctorRequest<'_>,
    identity: &RuntimeIdentity,
) -> Result<(Project, ProductionConnection), String> {
    let _phase = owner.phase()?;
    let project = service
        .project(request.project_id)
        .map_err(|_| "The current project could not be read for runtime diagnosis.".to_owned())?;
    require_scope(&project, request.expected)?;
    let path = connections
        .resolve(project.id, project.generation)?
        .ok_or_else(|| {
            "The selected runtime connection was removed; choose a connection and recheck."
                .to_owned()
        })?;
    let connection = ProductionConnection::load(path)
        .map_err(|_| "The selected runtime connection failed local validation.".to_owned())?;
    if connection.resource != request.expected.resource {
        return Err("The selected runtime belongs to another project resource.".into());
    }
    let current_identity = connection
        .identity()
        .map_err(|_| "The selected runtime identity could not be verified.".to_owned())?;
    require_identity(identity, &current_identity, &connection.executable_sha256)?;
    Ok((project, connection))
}

fn include_manim_observation(
    report: &mut CreativeRuntimeDoctorReport,
    expected: &RevisionStamp,
    provider_generation: u64,
    observation: &ManimRuntimeVersionObservation,
) -> Result<(), String> {
    if !observation.matches_scope(expected)
        || observation.provider_generation() != provider_generation
    {
        return Err(
            "The Driver Host generation or project changed during diagnosis; refresh and recheck."
                .into(),
        );
    }
    let (status, renderer_state) = match observation.state() {
        ManimRuntimeVersionState::ObservedCompatible => {
            ("validated", "native_runtime_version_compatible")
        }
        ManimRuntimeVersionState::ObservedIncompatible => {
            ("blocked", "native_runtime_version_incompatible")
        }
        ManimRuntimeVersionState::PackageNotFound => {
            ("blocked", "native_package_not_found_in_declared_runtime")
        }
        ManimRuntimeVersionState::NotObserved => ("not_tested", "native_runtime_not_observed"),
    };
    let check = report
        .checks
        .iter_mut()
        .find(|check| check.id == "native_runtime_manim_community_version")
        .ok_or_else(|| {
            "The runtime diagnosis report is missing its native version check.".to_owned()
        })?;
    check.status = status;
    check.detail = format!(
        "provider=driver:manim-community; provider_generation={}; observed_version={}; compatible={}; reason={}; broker_request_id={}. This is a read-only observation of the declared runtime; it does not install, render, mutate a scene or grant an effect.",
        observation.provider_generation(),
        observation.observed_version().unwrap_or("not_observed"),
        observation
            .version_compatible()
            .map_or("not_observed", |compatible| if compatible {
                "true"
            } else {
                "false"
            }),
        observation.reason_code(),
        observation.broker_request_id(),
    );
    if let Some(renderer) = report
        .renderers
        .iter_mut()
        .find(|renderer| renderer.renderer == "manim-community")
    {
        renderer.state = renderer_state;
    }
    report.runtime_version_detail = "Semwright CLI compatibility and an authenticated Broker registry response were observed. The scoped Manim Driver Host probe has its own result below; other native versions, rendering, fonts and effect grants remain separate checks.";
    Ok(())
}

/// Called only after the Desktop validates its Native SDK contract. Every
/// external read still uses ProductionClient's canonical authenticated route.
pub(crate) async fn inspect(
    owner: &crate::desktop_owner::DesktopOwner,
    service: &StudioService,
    connections: &RuntimeConnectionRegistry,
    request: DoctorRequest<'_>,
) -> Result<RuntimeDoctorReport, String> {
    let current = {
        let _phase = owner.phase()?;
        service.project(request.project_id).map_err(|_| {
            "The current project could not be read for runtime diagnosis.".to_owned()
        })?
    };
    require_scope(&current, request.expected)?;
    if request.cli != CliEvidence::VersionCompatible {
        return Ok(RuntimeDoctorReport {
            report: inspect_creative_runtime(&current, request.source_pin, request.cli, true),
            registry_observation: None,
            manim_runtime_observation: None,
            native_runtime_observations: Vec::new(),
            native_runtime_probe_failures: Vec::new(),
        });
    }
    let identity = RuntimeIdentity {
        connection: request
            .connection_identity
            .ok_or_else(|| "The compatible CLI probe has no connection identity.".to_owned())?
            .to_owned(),
        executable: request
            .executable_sha256
            .ok_or_else(|| "The compatible CLI probe has no executable digest.".to_owned())?
            .to_owned(),
    };
    let (_, connection) = current_connection(owner, service, connections, &request, &identity)?;
    let (client, registry) = {
        let _phase = owner.phase()?;
        let client = ProductionClient::new(connection)
            .map_err(|_| "The selected canonical runtime connection was rejected.".to_owned())?;
        let registry = client.discover_renderer_registry(request.expected).await;
        (client, registry)
    };
    let registry = registry.map_err(|_| {
        "The authenticated Broker registry query failed; no renderer absence or readiness is inferred."
            .to_owned()
    })?;
    let (current, _) = current_connection(owner, service, connections, &request, &identity)?;
    let manim_generation = match registry.state_for("manim-community") {
        Some(RendererRegistryState::PresentConnected { generation }) => Some(generation),
        _ => None,
    };
    let registry_observation = registry_evidence(&registry, request.expected, &identity)?;
    let native_generations = [
        NativeProbe::Blender,
        NativeProbe::Mlt,
        NativeProbe::MotionCanvas,
    ]
    .into_iter()
    .filter_map(|probe| match registry.state_for(probe.renderer()) {
        Some(RendererRegistryState::PresentConnected { generation }) => Some((probe, generation)),
        _ => None,
    })
    .collect::<Vec<_>>();
    let mut manim_runtime_observation = None;
    let mut native_runtime_observations = Vec::new();
    let mut native_runtime_probe_failures = Vec::new();
    let mut report = inspect_creative_runtime_with_registry(
        &current,
        request.source_pin,
        request.cli,
        true,
        registry,
    )
    .map_err(str::to_owned)?;
    if let Some(generation) = manim_generation {
        let observation = {
            let _phase = owner.phase()?;
            client.observe_manim_runtime_version(request.expected).await
        }
        .map_err(|_| {
            "The selected Driver Host runtime probe failed; its package availability and compatibility remain unobserved."
                .to_owned()
        })?;
        // Do not attach a late result after an edit, a connection replacement,
        // or removal of the selected reference.
        current_connection(owner, service, connections, &request, &identity)?;
        include_manim_observation(&mut report, request.expected, generation, &observation)?;
        manim_runtime_observation = Some(observation);
    }
    for (probe, generation) in native_generations {
        // Each read uses the original canonical client only while the selected
        // project and transport still have the original identity.
        current_connection(owner, service, connections, &request, &identity)?;
        let result = {
            let _phase = owner.phase()?;
            match probe {
                NativeProbe::Blender => {
                    client
                        .observe_blender_runtime_version(request.expected)
                        .await
                }
                NativeProbe::Mlt => client.observe_mlt_runtime_version(request.expected).await,
                NativeProbe::MotionCanvas => {
                    client
                        .observe_motion_canvas_runtime_version(request.expected)
                        .await
                }
            }
        };
        current_connection(owner, service, connections, &request, &identity)?;
        match result {
            Ok(observation) => {
                include_native_observation(
                    &mut report,
                    request.expected,
                    probe,
                    generation,
                    &observation,
                )?;
                native_runtime_observations.push(observation);
            }
            Err(error) if error.code == semwright_native_sdk::ErrorCode::StaleReference => {
                return Err(
                    "The native runtime observation became stale during diagnosis; refresh and recheck."
                        .into(),
                );
            }
            Err(error) => {
                // Do not infer absence or retry a failed/UNKNOWN probe. A later
                // iteration is a different provider's independent read.
                native_runtime_probe_failures.push(include_native_failure(
                    &mut report,
                    probe,
                    generation,
                    &error,
                )?);
            }
        }
    }
    current_connection(owner, service, connections, &request, &identity)?;
    report.runtime_version_detail = "Semwright CLI compatibility and the authenticated Broker registry were observed. Native version results below retain their own provider generation, scope and freshness; MLT declares no global compatibility policy. Rendering, font admission, installation and effect authority remain separate checks.";
    Ok(RuntimeDoctorReport {
        report,
        registry_observation: Some(registry_observation),
        manim_runtime_observation,
        native_runtime_observations,
        native_runtime_probe_failures,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_scope_and_runtime_identity_remain_admissible() {
        let project = Project::new("Owned runtime diagnosis").unwrap();
        require_scope(&project, &project.stamp()).unwrap();
        let identity = RuntimeIdentity {
            connection: "original_connection".into(),
            executable: "original_executable".into(),
        };
        require_identity(&identity, "original_connection", "original_executable").unwrap();
    }

    #[test]
    fn changed_revision_generation_or_resource_rejects_late_results() {
        let project = Project::new("Owned runtime diagnosis").unwrap();
        let mut changed = project.stamp();
        changed.revision += 1;
        assert!(require_scope(&project, &changed).is_err());
        changed = project.stamp();
        changed.generation = Uuid::new_v4();
        assert!(require_scope(&project, &changed).is_err());
        changed = project.stamp();
        changed.resource = Project::new("Other resource").unwrap().resource_key();
        assert!(require_scope(&project, &changed).is_err());
    }

    #[test]
    fn connection_or_executable_replacement_rejects_late_results() {
        let identity = RuntimeIdentity {
            connection: "original_connection".into(),
            executable: "original_executable".into(),
        };
        assert!(
            require_identity(&identity, "replacement_connection", "original_executable").is_err()
        );
        assert!(
            require_identity(&identity, "original_connection", "replacement_executable").is_err()
        );
        assert!(require_identity(&identity, "", "original_executable").is_err());
    }

    #[test]
    fn missing_probe_identity_cannot_be_satisfied_by_empty_strings() {
        let identity = RuntimeIdentity {
            connection: String::new(),
            executable: String::new(),
        };
        assert!(require_identity(&identity, "", "").is_err());
    }
}
