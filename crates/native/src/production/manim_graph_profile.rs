//! Initial Graph profile observation through the existing production transport.
//! This is a snapshot of one Host probe, not a provider lease or write admission.
use super::{
    BrokerResult, Error, ErrorCode, NativeResult, ProductionClient, ProductionCoordinator,
    RendererRegistryObservation, RendererRegistryState, SourceKind, backend, is_sha256,
    storage_error,
};
use crate::manim_graph_projection::{
    ManimSemanticGraphSceneOutputEligibility, validate_manim_semantic_graph_scene_output,
};
use motionwright_domain::{GraphSourceSnapshot, RevisionStamp};
use motionwright_manim_profile::semantic_graph_profile_probe::{
    ManimFixedGraphProfileObservationV1, parse_fixed_graph_profile_observation_v1,
};
use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

const COMMAND: &str = "driver.manim-community.doctor";
const PROVIDER: &str = "driver:manim-community";
const RENDERER: &str = "manim-community";
const MANIM_VERSION: &str = "0.21.0";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ManimGraphProfileState {
    Observed,
    NotObserved,
}

/// Constructed only after the original production dispatch. Serialized copies cannot
/// reconstruct this observation and never replace the live operation permit.
#[derive(Debug, Clone, Serialize)]
pub struct ManimGraphProfileObservation {
    schema: &'static str,
    selection: ManimSemanticGraphSceneOutputEligibility,
    connection_identity: String,
    transport_executable_sha256: String,
    registry_broker_request_id: String,
    registry_catalog_revision: u64,
    broker_request_id: String,
    provider: &'static str,
    provider_version: String,
    provider_generation: u64,
    execution_catalog_revision: u64,
    descriptor_sha256: String,
    state: ManimGraphProfileState,
    observation: Option<ManimFixedGraphProfileObservationV1>,
    observation_scope: &'static str,
    observation_source: &'static str,
    observation_freshness: &'static str,
    reason_code: &'static str,
    profile_admitted: bool,
    render_performed: bool,
    scene_mutation_performed: bool,
    future_label_coverage_verified: bool,
}
impl ManimGraphProfileObservation {
    pub fn state(&self) -> ManimGraphProfileState {
        self.state
    }
    pub fn selection(&self) -> &ManimSemanticGraphSceneOutputEligibility {
        &self.selection
    }
    pub fn fixed_profile(&self) -> Option<&ManimFixedGraphProfileObservationV1> {
        self.observation.as_ref()
    }
    pub fn connection_identity(&self) -> &str {
        &self.connection_identity
    }
    pub fn provider_generation(&self) -> u64 {
        self.provider_generation
    }
    pub fn descriptor_sha256(&self) -> &str {
        &self.descriptor_sha256
    }
    pub fn broker_request_id(&self) -> &str {
        &self.broker_request_id
    }
    pub fn reason_code(&self) -> &str {
        self.reason_code
    }
    pub fn observation_freshness(&self) -> &str {
        self.observation_freshness
    }
    pub fn matches_scope(
        &self,
        expected: &GraphSourceSnapshot,
        scene_id: Uuid,
        deliverable_id: Uuid,
    ) -> bool {
        self.selection.source_snapshot == *expected
            && self.selection.scene_id == scene_id
            && self.selection.deliverable_id == deliverable_id
    }
}

fn closed(value: &Value, keys: &[&str]) -> NativeResult<()> {
    let object = value
        .as_object()
        .ok_or_else(|| backend("Graph profile observation requires a closed object"))?;
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err(backend(
            "Graph profile observation object shape is unsupported",
        ));
    }
    Ok(())
}
fn token(value: Option<&Value>, max: usize) -> NativeResult<&str> {
    value
        .and_then(Value::as_str)
        .filter(|text| {
            !text.is_empty()
                && text.len() <= max
                && !text.chars().any(|c| c.is_control() || c.is_whitespace())
        })
        .ok_or_else(|| backend("Graph profile observation token is invalid"))
}
struct ParsedProfile {
    state: ManimGraphProfileState,
    observation: Option<ManimFixedGraphProfileObservationV1>,
    source: &'static str,
    reason: &'static str,
}
fn parse_profile(data: &Value) -> NativeResult<ParsedProfile> {
    closed(
        data,
        &[
            "driver",
            "driver_version",
            "runtime",
            "runtime_version_contract",
            "host_tools",
            "arbitrary_python",
            "network",
            "max_jobs",
            "runtime_probe",
            "graph_profile_probe",
        ],
    )?;
    if data["driver"] != RENDERER
        || data["runtime"] != "Manim Community"
        || data["runtime_version_contract"] != MANIM_VERSION
        || data["arbitrary_python"] != false
        || data["network"] != false
        || data["max_jobs"] != 32
        || !data["host_tools"].is_boolean()
    {
        return Err(backend("Manim Graph doctor contract is unsupported"));
    }
    token(data.get("driver_version"), 80)?;
    // Only the Graph probe is requested. Preserve the legacy eleven-field not_requested
    // report; do not imply or launch a second native version probe.
    let legacy = json!({
        "schema":1,"operation":"runtime_version_probe","requested":false,
        "source":"none","state":"not_observed","expected_version":MANIM_VERSION,
        "observed_version":null,"compatible":null,"reason_code":"not_requested",
        "render_performed":false,"scene_mutation_performed":false,
    });
    if data["runtime_probe"] != legacy {
        return Err(backend(
            "Graph-only doctor changed the legacy runtime probe contract",
        ));
    }
    let probe = &data["graph_profile_probe"];
    closed(
        probe,
        &[
            "schema",
            "operation",
            "requested",
            "source",
            "state",
            "observation",
            "reason_code",
            "render_performed",
            "scene_mutation_performed",
            "profile_admitted",
        ],
    )?;
    if probe["schema"] != 1
        || probe["operation"] != "fixed_graph_profile_probe"
        || probe["requested"] != true
        || probe["render_performed"] != false
        || probe["scene_mutation_performed"] != false
        || probe["profile_admitted"] != false
    {
        return Err(backend("Graph profile probe contract is unsupported"));
    }
    match probe["state"].as_str() {
        Some("observed") => {
            if data["host_tools"] != true
                || probe["source"] != "host_native_fixed_graph_profile_probe"
                || probe["reason_code"] != "fixed_profile_sample_observed"
            {
                return Err(backend(
                    "Graph profile requires an original native Host observation",
                ));
            }
            let bytes = serde_json::to_vec(&probe["observation"])
                .map_err(|_| backend("Graph fixed profile observation could not be encoded"))?;
            let observation = parse_fixed_graph_profile_observation_v1(&bytes).map_err(|_| {
                backend("Graph fixed profile observation violates the current compiler contract")
            })?;
            Ok(ParsedProfile {
                state: ManimGraphProfileState::Observed,
                observation: Some(observation),
                source: "host_native_fixed_graph_profile_probe",
                reason: "fixed_profile_sample_observed",
            })
        }
        Some("not_observed") => {
            if probe["source"] != "none" || !probe["observation"].is_null() {
                return Err(backend("Unobserved Graph profile contains native evidence"));
            }
            let reason = match probe["reason_code"].as_str() {
                Some("host_context_unavailable") => "host_context_unavailable",
                Some("host_tools_unavailable") if data["host_tools"] == false => {
                    "host_tools_unavailable"
                }
                Some("runner_failed") if data["host_tools"] == true => "runner_failed",
                Some("invalid_runner_response") if data["host_tools"] == true => {
                    "invalid_runner_response"
                }
                _ => return Err(backend("Graph profile observation reason is inconsistent")),
            };
            Ok(ParsedProfile {
                state: ManimGraphProfileState::NotObserved,
                observation: None,
                source: "none",
                reason,
            })
        }
        _ => Err(backend("Graph profile observation state is unsupported")),
    }
}
fn registry_generation(
    registry: &RendererRegistryObservation,
    expected: &RevisionStamp,
) -> NativeResult<u64> {
    if !registry.matches_scope(expected) {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Graph profile registry scope changed",
        ));
    }
    match registry.state_for(RENDERER) {
        Some(RendererRegistryState::PresentConnected { generation }) => Ok(generation),
        _ => Err(Error::new(
            ErrorCode::Unavailable,
            "Manim provider is not connected in the selected current registry",
        )),
    }
}
fn validate_provenance(
    original: &BrokerResult,
    expected_generation: u64,
    registry_catalog_revision: u64,
    expected_version: &str,
) -> NativeResult<(u64, String)> {
    let value = &original.value;
    if value.get("provenance_checked") != Some(&json!(true))
        || value.pointer("/execution/dry_run") != Some(&json!(false))
        || value.pointer("/execution/backend").and_then(Value::as_str) != Some(PROVIDER)
        || value
            .pointer("/execution/policy_decision")
            .and_then(Value::as_str)
            != Some("allow")
        || value
            .pointer("/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some(PROVIDER)
        || value
            .pointer("/execution/provenance/execution_provider")
            .and_then(Value::as_str)
            != Some(PROVIDER)
        || value.pointer("/execution/provenance/source") != Some(&json!(SourceKind::Driver))
    {
        return Err(backend(
            "Graph profile lacks authenticated direct Driver provenance",
        ));
    }
    let generation = value
        .pointer("/execution/provenance/provider_generation")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("Graph profile provider generation is absent"))?;
    let catalog_revision = value
        .pointer("/execution/provenance/catalog_revision")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("Graph profile catalog revision is absent"))?;
    if generation != expected_generation || catalog_revision < registry_catalog_revision {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Manim provider changed between registry discovery and Graph profile observation",
        ));
    }
    let descriptor = token(value.pointer("/execution/provenance/descriptor_sha256"), 64)?;
    if !is_sha256(descriptor)
        || token(value.pointer("/execution/provenance/provider_version"), 80)? != expected_version
    {
        return Err(backend(
            "Graph profile descriptor or provider version is inconsistent",
        ));
    }
    if original.request_id.is_empty()
        || original.request_id.len() > 128
        || original
            .request_id
            .chars()
            .any(|c| c.is_control() || c.is_whitespace())
    {
        return Err(backend("Graph profile Broker request identity is invalid"));
    }
    Ok((catalog_revision, descriptor.to_owned()))
}
fn authenticate(
    original: &BrokerResult,
    selection: ManimSemanticGraphSceneOutputEligibility,
    registry: &RendererRegistryObservation,
    expected: &RevisionStamp,
    client: &ProductionClient,
) -> NativeResult<ManimGraphProfileObservation> {
    let expected_generation = registry_generation(registry, expected)?;
    let expected_version = registry
        .provider_version_for(RENDERER)
        .ok_or_else(|| backend("Graph profile registry provider version is absent"))?;
    let (catalog_revision, descriptor) = validate_provenance(
        original,
        expected_generation,
        registry.catalog_revision(),
        expected_version,
    )?;
    let data = original
        .value
        .get("data")
        .ok_or_else(|| backend("Graph profile observation data is absent"))?;
    if token(data.get("driver_version"), 80)? != expected_version {
        return Err(backend(
            "Graph doctor version differs from the selected provider",
        ));
    }
    let parsed = parse_profile(data)?;
    Ok(ManimGraphProfileObservation {
        schema: "motionwright-manim-graph-profile-observation/1",
        selection,
        connection_identity: client.connection().identity()?,
        transport_executable_sha256: client.connection().executable_sha256.clone(),
        registry_broker_request_id: registry.broker_request_id().into(),
        registry_catalog_revision: registry.catalog_revision(),
        broker_request_id: original.request_id.clone(),
        provider: PROVIDER,
        provider_version: expected_version.into(),
        provider_generation: expected_generation,
        execution_catalog_revision: catalog_revision,
        descriptor_sha256: descriptor,
        state: parsed.state,
        observation: parsed.observation,
        observation_scope: "declared_manim_runtime_fixed_ascii_sample_only",
        observation_source: parsed.source,
        observation_freshness: "original_dispatch_snapshot_only",
        reason_code: parsed.reason,
        profile_admitted: false,
        render_performed: false,
        scene_mutation_performed: false,
        future_label_coverage_verified: false,
    })
}
impl ProductionClient {
    async fn observe_selected_manim_graph_profile(
        &self,
        selection: ManimSemanticGraphSceneOutputEligibility,
        registry: &RendererRegistryObservation,
        expected: &RevisionStamp,
    ) -> NativeResult<ManimGraphProfileObservation> {
        if self.connection().resource != expected.resource {
            return Err(Error::new(
                ErrorCode::PolicyDenied,
                "Graph profile connection is bound to another project resource",
            ));
        }
        registry_generation(registry, expected)?;
        let original = self
            .execute(COMMAND, json!({"probe_graph_profile":true}), false)
            .await?;
        // Preserve the original failure, including UNKNOWN, before later revalidation.
        let observed = authenticate(&original, selection, registry, expected, self)?;
        self.connection().validate()?;
        Ok(observed)
    }
}
impl ProductionCoordinator {
    fn require_manim_graph_profile_scope(
        &self,
        expected: &GraphSourceSnapshot,
        scene_id: Uuid,
        deliverable_id: Uuid,
    ) -> NativeResult<(ManimSemanticGraphSceneOutputEligibility, RevisionStamp)> {
        let project = self
            .service
            .project(expected.project_id)
            .map_err(storage_error)?;
        let selection = validate_manim_semantic_graph_scene_output(
            &project,
            expected,
            scene_id,
            deliverable_id,
        )?;
        Ok((selection, RevisionStamp::from(&project)))
    }
    /// Observe before a first Graph is saved. No source/receipt mutation or admission.
    /// The live operation permit remains owned by the existing authority boundary.
    pub async fn observe_manim_graph_profile(
        &self,
        expected: &GraphSourceSnapshot,
        scene_id: Uuid,
        deliverable_id: Uuid,
    ) -> NativeResult<ManimGraphProfileObservation> {
        let (selection, stamp) =
            self.require_manim_graph_profile_scope(expected, scene_id, deliverable_id)?;
        let registry = self.client.discover_renderer_registry(&stamp).await?;
        self.require_manim_graph_profile_scope(expected, scene_id, deliverable_id)?;
        let observed = self
            .client
            .observe_selected_manim_graph_profile(selection, &registry, &stamp)
            .await?;
        self.require_manim_graph_profile_scope(expected, scene_id, deliverable_id)?;
        Ok(observed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_manim_profile::{
        semantic_graph_profile_probe::{
            GRAPH_PROFILE_PROBE_FONT_SIZE, GRAPH_PROFILE_PROBE_SAMPLE,
            fixed_graph_profile_probe_program_v1,
        },
        semantic_graph_source::graph_builtin_asset_pins,
        semantic_graph_text::GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
    };
    // Parser/transport fixture only: these bytes are not native runtime or font evidence.
    // Parser/transport fixture only. The compiler payload is decoded as JSON;
    // no Python is executed and no mock bytes become native runtime evidence.
    fn mock_fixed_profile() -> Value {
        let program = fixed_graph_profile_probe_program_v1().unwrap();
        let payloads: Vec<&str> = program
            .lines()
            .filter_map(|line| line.strip_prefix("_DATA = "))
            .collect();
        assert_eq!(payloads.len(), 1, "one compiler-owned fixed probe payload");
        let encoded: String = serde_json::from_str(payloads[0]).unwrap();
        let compiler: Value = serde_json::from_str(&encoded).unwrap();
        assert_eq!(compiler["sample_text"], GRAPH_PROFILE_PROBE_SAMPLE);
        assert_eq!(
            compiler["sample_font_size_px"],
            GRAPH_PROFILE_PROBE_FONT_SIZE
        );
        let lock = &compiler["font_lock"];
        let families = lock["packages"].as_array().unwrap().iter().map(|package| {
            let font = package["font_files"].as_array().unwrap().last().unwrap();
            json!({
                "editorial_family":package["editorial_family"],
                "native_family":package["expected_native_family"],
                "unknown_glyph_count":0,"pango_version":"mock-1","fontconfig_version":1,
                "runs":[{
                    "offset":0,"length":GRAPH_PROFILE_PROBE_SAMPLE.len(),
                    "num_chars":GRAPH_PROFILE_PROBE_SAMPLE.len(),
                    "family":package["expected_native_family"],"weight":400,"style":0,"face_index":0,
                    "font_relative_path":font["relative_path"],"font_sha256":font["sha256"],
                }],
            })
        }).collect::<Vec<_>>();
        json!({
            "schema":"motionwright.manim.fixed-graph-profile-observation/1",
            "profile_id":motionwright_domain::SEMANTIC_GRAPH_PROFILE,
            "contract_sha256":GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
            "manim_version":MANIM_VERSION,"sample_text":GRAPH_PROFILE_PROBE_SAMPLE,
            "sample_font_size_px":GRAPH_PROFILE_PROBE_FONT_SIZE,
            "coverage_scope":"fixed_ascii_sample_only","helper_pins":compiler["helper_pins"],
            "asset_pins":graph_builtin_asset_pins(),"families":families,
            "future_label_coverage_verified":false,"render_performed":false,
            "scene_mutation_performed":false,"profile_admitted":false,
        })
    }

    fn doctor_data() -> Value {
        json!({
            "driver":RENDERER,"driver_version":"0.1.0","runtime":"Manim Community",
            "runtime_version_contract":MANIM_VERSION,"host_tools":true,"arbitrary_python":false,"network":false,"max_jobs":32,
            "runtime_probe":{"schema":1,"operation":"runtime_version_probe","requested":false,
                "source":"none","state":"not_observed","expected_version":MANIM_VERSION,
                "observed_version":null,"compatible":null,"reason_code":"not_requested",
                "render_performed":false,"scene_mutation_performed":false},
            "graph_profile_probe":{"schema":1,"operation":"fixed_graph_profile_probe","requested":true,
                "source":"host_native_fixed_graph_profile_probe","state":"observed","observation":mock_fixed_profile(),
                "reason_code":"fixed_profile_sample_observed","render_performed":false,
                "scene_mutation_performed":false,"profile_admitted":false},
        })
    }
    fn original_result() -> BrokerResult {
        BrokerResult {
            request_id: "graph-probe-original".into(),
            value: json!({
                "data":doctor_data(),"provenance_checked":true,"warnings":[],
                "execution":{"backend":PROVIDER,"policy_decision":"allow","dry_run":false,
                    "provenance":{"provider":PROVIDER,"execution_provider":PROVIDER,"source":SourceKind::Driver,
                        "provider_version":"0.1.0","provider_generation":7,"catalog_revision":9,
                        "descriptor_sha256":"ab".repeat(32)}}
            }),
        }
    }
    #[test]
    fn parser_accepts_fixed_sample_without_promoting_legacy_or_admission() {
        let data = doctor_data();
        assert_eq!(data.as_object().unwrap().len(), 10);
        assert_eq!(data["runtime_probe"].as_object().unwrap().len(), 11);
        assert_eq!(data["graph_profile_probe"].as_object().unwrap().len(), 10);
        let parsed = parse_profile(&data).unwrap();
        assert_eq!(parsed.state, ManimGraphProfileState::Observed);
        let profile = parsed.observation.unwrap();
        assert_eq!(profile.asset_pins.len(), 9);
        assert_eq!(profile.helper_pins.len(), 6);
        assert_eq!(profile.families.len(), 2);
        assert!(!profile.profile_admitted && !profile.future_label_coverage_verified);
    }
    #[test]
    fn parser_rejects_missing_expanded_or_forged_doctor_contracts() {
        for (pointer, value) in [
            ("/driver", json!("motion-canvas")),
            ("/host_tools", json!(false)),
            ("/network", json!(true)),
            ("/runtime_probe/requested", json!(true)),
            ("/runtime_probe/reason_code", json!("version_observed")),
            ("/graph_profile_probe/requested", json!(false)),
            ("/graph_profile_probe/source", json!("runtime_contract")),
            ("/graph_profile_probe/reason_code", json!("ready")),
            ("/graph_profile_probe/profile_admitted", json!(true)),
            ("/graph_profile_probe/render_performed", json!(true)),
        ] {
            let mut data = doctor_data();
            *data.pointer_mut(pointer).unwrap() = value;
            assert!(parse_profile(&data).is_err(), "{pointer}");
        }
        for scope in ["", "/runtime_probe", "/graph_profile_probe"] {
            let mut expanded = doctor_data();
            expanded
                .pointer_mut(scope)
                .unwrap()
                .as_object_mut()
                .unwrap()
                .insert("injected".into(), json!(true));
            assert!(parse_profile(&expanded).is_err(), "{scope}");
        }
        let mut missing = doctor_data();
        missing
            .as_object_mut()
            .unwrap()
            .remove("graph_profile_probe");
        assert!(parse_profile(&missing).is_err());
    }
    #[test]
    fn consumer_runs_the_current_fixed_profile_parser() {
        for (pointer, value) in [
            ("/contract_sha256", json!("0".repeat(64))),
            ("/asset_pins/0/sha256", json!("0".repeat(64))),
            ("/helper_pins/0/sha256", json!("0".repeat(64))),
            ("/families/0/unknown_glyph_count", json!(1)),
            ("/families/0/runs/0/weight", json!(700)),
            ("/future_label_coverage_verified", json!(true)),
        ] {
            let mut data = doctor_data();
            *data["graph_profile_probe"]["observation"]
                .pointer_mut(pointer)
                .unwrap() = value;
            assert!(parse_profile(&data).is_err(), "{pointer}");
        }
    }
    #[test]
    fn unobserved_probe_is_distinct_and_cannot_carry_native_evidence() {
        for reason in [
            "host_context_unavailable",
            "host_tools_unavailable",
            "runner_failed",
            "invalid_runner_response",
        ] {
            let mut data = doctor_data();
            data["host_tools"] = json!(reason != "host_tools_unavailable");
            let probe = &mut data["graph_profile_probe"];
            probe["source"] = json!("none");
            probe["state"] = json!("not_observed");
            probe["reason_code"] = json!(reason);
            probe["observation"] = Value::Null;
            let parsed = parse_profile(&data).unwrap();
            assert_eq!(parsed.state, ManimGraphProfileState::NotObserved);
            assert!(parsed.observation.is_none());
            data["graph_profile_probe"]["observation"] = mock_fixed_profile();
            assert!(parse_profile(&data).is_err());
        }
    }
    #[test]
    fn provenance_rejects_forged_route_generation_catalog_and_request() {
        assert!(validate_provenance(&original_result(), 7, 9, "0.1.0").is_ok());
        for (pointer, value) in [
            ("/provenance_checked", json!(false)),
            ("/execution/dry_run", json!(true)),
            ("/execution/backend", json!("driver:blender")),
            ("/execution/policy_decision", json!("deny")),
            ("/execution/provenance/provider", json!("driver:blender")),
            (
                "/execution/provenance/execution_provider",
                json!("driver:blender"),
            ),
            ("/execution/provenance/source", json!(SourceKind::Builtin)),
            ("/execution/provenance/provider_generation", json!(8)),
            ("/execution/provenance/catalog_revision", json!(8)),
            ("/execution/provenance/provider_version", json!("another")),
            ("/execution/provenance/descriptor_sha256", json!("invalid")),
        ] {
            let mut original = original_result();
            *original.value.pointer_mut(pointer).unwrap() = value;
            assert!(
                validate_provenance(&original, 7, 9, "0.1.0").is_err(),
                "{pointer}"
            );
        }
        let mut original = original_result();
        original.value["execution"]
            .as_object_mut()
            .unwrap()
            .remove("provenance");
        assert!(validate_provenance(&original, 7, 9, "0.1.0").is_err());
        for id in ["".to_owned(), "x".repeat(129), "request\n".into()] {
            let mut original = original_result();
            original.request_id = id;
            assert!(validate_provenance(&original, 7, 9, "0.1.0").is_err());
        }
    }

    #[cfg(unix)]
    mod transport {
        use super::*;
        use crate::production::{
            ProductionConnection,
            renderer_registry::{registry_test_data, registry_test_provider},
            sha256_file,
            tests::{fake_transport_connection, transport_reply},
        };
        use motionwright_domain::{
            Change, OutputColorSpace, OutputContainer, Project, RendererKind, VideoCodec,
        };
        use motionwright_service::StudioService;
        use semwright_native_sdk::types::Envelope;
        use std::{
            fs,
            path::{Path, PathBuf},
            time::Duration,
        };

        fn apply(service: &StudioService, project: &mut Project, id: &str, change: Change) {
            *project = service
                .apply(project.id, &project.stamp(), id, &change)
                .unwrap()
                .project;
        }
        fn fixture(temp: &tempfile::TempDir) -> (StudioService, Project) {
            let service = StudioService::open(temp.path().join("graph-profile.sqlite3")).unwrap();
            let mut project = service
                .create_project("Initial Graph profile consumer")
                .unwrap();
            apply(
                &service,
                &mut project,
                "scene",
                Change::AddScene {
                    name: "Graph scene".into(),
                    objective: "Editable explanation".into(),
                    duration_seconds: 6,
                },
            );
            let scene_id = project.scenes[0].id;
            apply(
                &service,
                &mut project,
                "renderer",
                Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::ManimCommunity,
                },
            );
            let mut profile = project.deliverables[0].clone();
            profile.width = 640;
            profile.height = 360;
            profile.frame_rate = motionwright_domain::RationalTime::new(30, 1).unwrap();
            profile.container = OutputContainer::Mp4;
            profile.video_codec = VideoCodec::H264;
            profile.color_space = OutputColorSpace::Rec709;
            profile.burn_in_captions = false;
            apply(
                &service,
                &mut project,
                "output",
                Change::UpsertDeliverable { profile },
            );
            assert!(project.production_design.semantic_graphs.is_empty());
            (service, project)
        }
        fn registry_reply(connected: bool) -> Envelope {
            let mut reply = transport_reply("doctor");
            reply.request_id = "registry-original".into();
            reply.data = Some(registry_test_data(vec![registry_test_provider(
                PROVIDER, connected,
            )]));
            reply
        }
        fn graph_reply() -> Envelope {
            let mut reply = transport_reply(COMMAND);
            reply.request_id = "graph-probe-original".into();
            reply.data = Some(doctor_data());
            let provenance = reply.execution.provenance.as_mut().unwrap();
            provenance.provider_version = "0.1.0".into();
            provenance.provider_generation = Some(7);
            provenance.catalog_revision = 9;
            reply
        }
        fn quote(value: &str) -> String {
            format!("'{}'", value.replace('\'', "'\\''"))
        }
        fn connection(
            temp: &tempfile::TempDir,
            resource: String,
            registry: &Envelope,
            graph: &Envelope,
            release: Option<&Path>,
        ) -> (ProductionConnection, PathBuf) {
            let (mut connection, counter) = fake_transport_connection(temp, resource, "", "", 0);
            let wait = release
                .map(|path| {
                    format!(
                        "while [ ! -f {} ]; do sleep 0.01; done\n",
                        quote(&path.to_string_lossy())
                    )
                })
                .unwrap_or_default();
            let script = format!(
                "#!/bin/sh\nprintf '%s\\n' \"$7\" >> {}\ncase \"$7\" in\n\
                 doctor) printf '%s' {}; exit {};;\n\
                 driver.manim-community.doctor)\n\
                 [ \"$9\" = {} ] || exit 73\n\
                 {}printf '%s' {}; exit {};;\n\
                 *) exit 74;;\nesac\n",
                quote(&counter.to_string_lossy()),
                quote(&serde_json::to_string(registry).unwrap()),
                if registry.ok { 0 } else { 19 },
                quote(r#"{"probe_graph_profile":true}"#),
                wait,
                quote(&serde_json::to_string(graph).unwrap()),
                if graph.ok { 0 } else { 19 },
            );
            fs::write(&connection.executable, script).unwrap();
            connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
            (connection, counter)
        }
        fn expected_log() -> &'static [u8] {
            b"doctor\ndriver.manim-community.doctor\n"
        }

        #[tokio::test]
        async fn first_graph_observation_uses_original_transport_without_creating_a_graph() {
            let temp = tempfile::tempdir().unwrap();
            let (service, project) = fixture(&temp);
            let expected = GraphSourceSnapshot::from_project(&project);
            let scene = project.scenes[0].id;
            let output = project.deliverables[0].id;
            let (connection, counter) = connection(
                &temp,
                project.resource_key(),
                &registry_reply(true),
                &graph_reply(),
                None,
            );
            let identity = connection.identity().unwrap();
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let observed = coordinator
                .observe_manim_graph_profile(&expected, scene, output)
                .await
                .unwrap();
            assert_eq!(observed.state(), ManimGraphProfileState::Observed);
            assert_eq!(observed.connection_identity(), identity);
            assert_eq!(observed.provider_generation(), 7);
            assert_eq!(observed.descriptor_sha256(), "ab".repeat(32));
            assert!(observed.matches_scope(&expected, scene, output));
            assert_eq!(
                observed.observation_freshness(),
                "original_dispatch_snapshot_only"
            );
            for field in 0..4 {
                let mut changed = expected.clone();
                match field {
                    0 => changed.project_id = Uuid::new_v4(),
                    1 => changed.generation = Uuid::new_v4(),
                    2 => changed.branch_id = Uuid::new_v4(),
                    _ => changed.revision += 1,
                }
                assert!(!observed.matches_scope(&changed, scene, output));
            }
            assert!(!observed.matches_scope(&expected, Uuid::new_v4(), output));
            assert!(!observed.matches_scope(&expected, scene, Uuid::new_v4()));
            let json = serde_json::to_value(&observed).unwrap();
            for flag in [
                "profile_admitted",
                "render_performed",
                "scene_mutation_performed",
                "future_label_coverage_verified",
            ] {
                assert_eq!(json[flag], false);
            }
            assert_eq!(fs::read(counter).unwrap(), expected_log());
            assert_eq!(service.project(project.id).unwrap(), project);
            assert!(
                service
                    .production_receipts(project.id, 10)
                    .unwrap()
                    .is_empty()
            );
        }
        #[tokio::test]
        async fn scope_and_selection_fail_before_any_dispatch() {
            for case in 0..8 {
                let temp = tempfile::tempdir().unwrap();
                let (service, mut project) = fixture(&temp);
                let mut expected = GraphSourceSnapshot::from_project(&project);
                let mut scene = project.scenes[0].id;
                let mut output = project.deliverables[0].id;
                let mut resource = project.resource_key();
                match case {
                    0 => expected.project_id = Uuid::new_v4(),
                    1 => expected.generation = Uuid::new_v4(),
                    2 => expected.branch_id = Uuid::new_v4(),
                    3 => expected.revision += 1,
                    4 => scene = Uuid::new_v4(),
                    5 => output = Uuid::new_v4(),
                    6 => resource.push_str("-foreign"),
                    _ => {
                        let mut profile = project.deliverables[0].clone();
                        profile.burn_in_captions = true;
                        apply(
                            &service,
                            &mut project,
                            "unsupported-output",
                            Change::UpsertDeliverable { profile },
                        );
                        expected = GraphSourceSnapshot::from_project(&project);
                    }
                }
                let (connection, counter) =
                    connection(&temp, resource, &registry_reply(true), &graph_reply(), None);
                let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
                assert!(
                    coordinator
                        .observe_manim_graph_profile(&expected, scene, output)
                        .await
                        .is_err(),
                    "{case}"
                );
                assert!(!counter.exists(), "{case}");
                assert_eq!(service.project(project.id).unwrap(), project);
            }
        }
        #[tokio::test]
        async fn disconnected_registry_and_registry_unknown_never_dispatch_graph_probe() {
            for fail_registry in [false, true] {
                let temp = tempfile::tempdir().unwrap();
                let (service, project) = fixture(&temp);
                let mut registry = registry_reply(false);
                let original = Error::new(ErrorCode::Timeout, "registry original UNKNOWN")
                    .uncertain()
                    .recipe_progress(0, "doctor");
                if fail_registry {
                    registry.ok = false;
                    registry.data = None;
                    registry.error = Some(original.clone());
                }
                let (connection, counter) = connection(
                    &temp,
                    project.resource_key(),
                    &registry,
                    &graph_reply(),
                    None,
                );
                let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
                let returned = coordinator
                    .observe_manim_graph_profile(
                        &GraphSourceSnapshot::from_project(&project),
                        project.scenes[0].id,
                        project.deliverables[0].id,
                    )
                    .await
                    .unwrap_err();
                if fail_registry {
                    assert_eq!(
                        serde_json::to_value(returned).unwrap(),
                        serde_json::to_value(original).unwrap()
                    );
                } else {
                    assert_eq!(returned.code, ErrorCode::Unavailable);
                }
                assert_eq!(fs::read(counter).unwrap(), b"doctor\n");
                assert_eq!(service.project(project.id).unwrap(), project);
            }
        }
        #[tokio::test]
        async fn changed_provider_or_doctor_identity_is_not_admitted_or_retried() {
            for case in 0..3 {
                let temp = tempfile::tempdir().unwrap();
                let (service, project) = fixture(&temp);
                let mut reply = graph_reply();
                match case {
                    0 => {
                        reply
                            .execution
                            .provenance
                            .as_mut()
                            .unwrap()
                            .provider_generation = Some(8)
                    }
                    1 => {
                        reply.execution.provenance.as_mut().unwrap().provider =
                            "driver:blender".into()
                    }
                    _ => reply.data.as_mut().unwrap()["driver_version"] = json!("different"),
                }
                let (connection, counter) = connection(
                    &temp,
                    project.resource_key(),
                    &registry_reply(true),
                    &reply,
                    None,
                );
                let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
                assert!(
                    coordinator
                        .observe_manim_graph_profile(
                            &GraphSourceSnapshot::from_project(&project),
                            project.scenes[0].id,
                            project.deliverables[0].id
                        )
                        .await
                        .is_err()
                );
                assert_eq!(fs::read(counter).unwrap(), expected_log());
                assert_eq!(service.project(project.id).unwrap(), project);
            }
        }
        async fn peer_commit_during_probe(fail_probe: bool) -> (Error, Error) {
            let temp = tempfile::tempdir().unwrap();
            let (service, project) = fixture(&temp);
            let expected = GraphSourceSnapshot::from_project(&project);
            let original = Error::new(ErrorCode::Timeout, "original Graph Host outcome UNKNOWN")
                .uncertain()
                .recipe_progress(1, COMMAND);
            let mut reply = graph_reply();
            if fail_probe {
                reply.ok = false;
                reply.data = None;
                reply.error = Some(original.clone());
            }
            let release = temp.path().join("release-original-graph-probe");
            let (connection, counter) = connection(
                &temp,
                project.resource_key(),
                &registry_reply(true),
                &reply,
                Some(&release),
            );
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let peer = async {
                let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
                loop {
                    if fs::read(&counter).is_ok_and(|bytes| bytes.as_slice() == expected_log()) {
                        break;
                    }
                    assert!(
                        tokio::time::Instant::now() < deadline,
                        "Graph probe did not enter original transport"
                    );
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
                let peer = StudioService::open(temp.path().join("graph-profile.sqlite3")).unwrap();
                let edited = peer
                    .apply(
                        project.id,
                        &project.stamp(),
                        "peer-edit-during-graph-probe",
                        &Change::RenameProject {
                            title: "Peer committed during original Graph probe".into(),
                        },
                    )
                    .unwrap()
                    .project;
                fs::write(&release, b"release once").unwrap();
                edited
            };
            let (result, edited) = tokio::join!(
                coordinator.observe_manim_graph_profile(
                    &expected,
                    project.scenes[0].id,
                    project.deliverables[0].id
                ),
                peer
            );
            assert_eq!(fs::read(counter).unwrap(), expected_log());
            assert_eq!(service.project(project.id).unwrap(), edited);
            assert!(
                service
                    .production_receipts(project.id, 10)
                    .unwrap()
                    .is_empty()
            );
            assert!(edited.production_design.semantic_graphs.is_empty());
            (result.unwrap_err(), original)
        }
        #[tokio::test]
        async fn original_graph_unknown_survives_peer_commit_without_another_dispatch() {
            let (returned, original) = peer_commit_during_probe(true).await;
            assert_eq!(
                serde_json::to_value(returned).unwrap(),
                serde_json::to_value(original).unwrap()
            );
        }
        #[tokio::test]
        async fn successful_graph_probe_is_rejected_after_peer_commit() {
            let (returned, _) = peer_commit_during_probe(false).await;
            assert_eq!(returned.code, ErrorCode::StaleReference);
        }
    }
}
