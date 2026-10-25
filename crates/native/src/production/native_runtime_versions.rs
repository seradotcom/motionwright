//! Native runtime versions observed through the existing authenticated operations.
//! These observations neither authorize local writes nor certify render readiness.
use super::{
    BrokerResult, Error, ErrorCode, NativeResult, ProductionClient, ProductionCoordinator,
    SourceKind, backend, is_sha256, storage_error,
};
use motionwright_domain::RevisionStamp;
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use uuid::Uuid;

const MC_VERSION: &str = "3.17.2";
const NODE_VERSION: &str = "22.22.0";
const BLENDER_VERSION: &str = "4.5.14";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeRuntimeVersionState {
    ObservedCompatible,
    ObservedIncompatible,
    /// A native version was observed, but this operation declares no compatibility policy.
    Observed,
    PackageNotFound,
    NotObserved,
}

/// Evidence for one project revision and one authenticated provider response.
/// The transport executable hash identifies the Semwright CLI, not the renderer.
/// MLT reports its provider-lifetime catalog; it is not a fresh process probe.
#[derive(Debug, Clone, Serialize)]
pub struct NativeRuntimeVersionObservation {
    schema: &'static str,
    stamp: RevisionStamp,
    connection_identity: String,
    transport_executable_sha256: String,
    broker_request_id: String,
    renderer: &'static str,
    provider: &'static str,
    provider_generation: u64,
    descriptor_sha256: String,
    expected_version: Option<&'static str>,
    observed_version: Option<String>,
    observed_components: BTreeMap<&'static str, String>,
    version_compatible: Option<bool>,
    state: NativeRuntimeVersionState,
    observation_scope: &'static str,
    observation_source: &'static str,
    observation_freshness: &'static str,
    reason_code: &'static str,
    render_performed: bool,
    scene_mutation_performed: bool,
}

impl NativeRuntimeVersionObservation {
    pub fn state(&self) -> NativeRuntimeVersionState {
        self.state
    }
    pub fn renderer(&self) -> &str {
        self.renderer
    }
    pub fn observed_version(&self) -> Option<&str> {
        self.observed_version.as_deref()
    }
    pub fn expected_version(&self) -> Option<&str> {
        self.expected_version
    }
    pub fn observed_components(&self) -> &BTreeMap<&'static str, String> {
        &self.observed_components
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
    pub fn observation_freshness(&self) -> &str {
        self.observation_freshness
    }
    pub fn matches_scope(&self, expected: &RevisionStamp) -> bool {
        self.stamp.resource == expected.resource
            && self.stamp.generation == expected.generation
            && self.stamp.revision == expected.revision
    }
}

#[derive(Debug, Clone, Copy)]
enum Probe {
    MotionCanvas,
    Mlt,
    Blender,
}

impl Probe {
    fn command(self) -> &'static str {
        match self {
            Self::MotionCanvas => "driver.motion-canvas.doctor",
            Self::Mlt => "driver.mlt-video.doctor",
            Self::Blender => "driver.blender.introspect.summary",
        }
    }
    fn provider(self) -> &'static str {
        match self {
            Self::MotionCanvas => "driver:motion-canvas",
            Self::Mlt => "driver:mlt-video",
            Self::Blender => "driver:blender",
        }
    }
    fn renderer(self) -> &'static str {
        match self {
            Self::MotionCanvas => "motion-canvas",
            Self::Mlt => "mlt",
            Self::Blender => "blender",
        }
    }
    fn args(self) -> Value {
        match self {
            Self::MotionCanvas => json!({"probe_runtime":true}),
            _ => json!({}),
        }
    }
    fn parse(self, data: &Value) -> NativeResult<ParsedProbe> {
        match self {
            Self::MotionCanvas => parse_motion_canvas(data),
            Self::Mlt => parse_mlt(data),
            Self::Blender => parse_blender(data),
        }
    }
}

struct ParsedProbe {
    expected: Option<&'static str>,
    observed: Option<String>,
    components: BTreeMap<&'static str, String>,
    compatible: Option<bool>,
    state: NativeRuntimeVersionState,
    scope: &'static str,
    source: &'static str,
    freshness: &'static str,
    reason: &'static str,
}

fn closed_object(value: &Value, keys: &[&str]) -> NativeResult<()> {
    if !value.as_object().is_some_and(|object| {
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
    }) {
        return Err(backend("Native runtime response shape is unsupported"));
    }
    Ok(())
}

fn text(value: Option<&Value>, max: usize) -> NativeResult<&str> {
    value
        .and_then(Value::as_str)
        .filter(|s| s.len() <= max && !s.chars().any(char::is_control))
        .ok_or_else(|| backend("Native runtime observation text is out of bounds"))
}

fn token(value: Option<&Value>, max: usize) -> NativeResult<&str> {
    let value = text(value, max)?;
    if value.is_empty() || value.chars().any(char::is_whitespace) {
        return Err(backend("Native runtime observation token is invalid"));
    }
    Ok(value)
}

fn version(value: Option<&Value>) -> NativeResult<&str> {
    let value = token(value, 80)?;
    if !value.bytes().enumerate().all(|(i, byte)| {
        byte.is_ascii_alphanumeric() || (i > 0 && matches!(byte, b'.' | b'+' | b'_' | b'-'))
    }) {
        return Err(backend("Native runtime observation version is invalid"));
    }
    Ok(value)
}

fn parse_blender(data: &Value) -> NativeResult<ParsedProbe> {
    closed_object(
        data,
        &[
            "version",
            "version_string",
            "rna_types",
            "operator_categories",
            "operators",
            "available_addons",
            "enabled_addons",
            "arbitrary_python",
            "generic_operator_invoke",
        ],
    )?;
    let tuple = data
        .get("version")
        .and_then(Value::as_array)
        .filter(|items| items.len() == 3)
        .ok_or_else(|| backend("Blender native version tuple is invalid"))?;
    let values: Vec<u64> = tuple
        .iter()
        .map(|v| {
            v.as_u64()
                .filter(|v| *v <= 99)
                .ok_or_else(|| backend("Blender native version tuple is invalid"))
        })
        .collect::<NativeResult<_>>()?;
    let display = text(data.get("version_string"), 128)?;
    for (key, bound) in [
        ("rna_types", 100_000),
        ("operator_categories", 4096),
        ("operators", 100_000),
        ("available_addons", 10_000),
        ("enabled_addons", 10_000),
    ] {
        if !data
            .get(key)
            .and_then(Value::as_u64)
            .is_some_and(|v| v <= bound)
        {
            return Err(backend("Blender native summary count is out of bounds"));
        }
    }
    if data.get("arbitrary_python").and_then(Value::as_bool) != Some(false)
        || data.get("generic_operator_invoke").and_then(Value::as_bool) != Some(false)
    {
        return Err(backend("Blender native summary scope is unsupported"));
    }
    let observed = format!("{}.{}.{}", values[0], values[1], values[2]);
    let compatible = observed == BLENDER_VERSION;
    Ok(ParsedProbe {
        expected: Some(BLENDER_VERSION),
        observed: Some(observed),
        components: BTreeMap::from([("version_string", display.to_owned())]),
        compatible: Some(compatible),
        state: if compatible {
            NativeRuntimeVersionState::ObservedCompatible
        } else {
            NativeRuntimeVersionState::ObservedIncompatible
        },
        scope: "selected_host_runtime_session",
        source: "host_native_session_summary",
        freshness: "current_request",
        reason: if compatible {
            "version_observed"
        } else {
            "version_mismatch"
        },
    })
}

fn parse_mlt(data: &Value) -> NativeResult<ParsedProbe> {
    closed_object(
        data,
        &[
            "driver_version",
            "driver_protocol",
            "adapters",
            "render_available",
            "mlt_version",
            "capabilities",
            "filesystem_mounts",
            "reason",
            "network",
            "native_gui_verified",
        ],
    )?;
    token(data.get("driver_version"), 80)?;
    text(data.get("reason"), 1024)?;
    if data.get("driver_protocol").and_then(Value::as_u64) != Some(7)
        || data.get("adapters")
            != Some(&json!([
                "generic-mlt/1",
                "kdenlive-generation5-inspection/1",
                "shotcut-annotations-inspection/1"
            ]))
        || data.get("capabilities").and_then(Value::as_u64).is_none()
        || data.get("network").and_then(Value::as_bool) != Some(false)
        || data.get("native_gui_verified").and_then(Value::as_bool) != Some(false)
    {
        return Err(backend("MLT doctor response contract is unsupported"));
    }
    let mounts = data
        .get("filesystem_mounts")
        .and_then(Value::as_array)
        .filter(|v| v.len() <= 3)
        .ok_or_else(|| backend("MLT doctor mount inventory is invalid"))?;
    let mut unique = std::collections::BTreeSet::new();
    for mount in mounts {
        let mount = token(Some(mount), 16)?;
        if !["project", "media", "output"].contains(&mount) || !unique.insert(mount) {
            return Err(backend("MLT doctor mount inventory is invalid"));
        }
    }
    let available = data
        .get("render_available")
        .and_then(Value::as_bool)
        .ok_or_else(|| backend("MLT doctor availability is invalid"))?;
    let mut parsed = ParsedProbe {
        expected: None,
        observed: None,
        components: BTreeMap::new(),
        compatible: None,
        state: NativeRuntimeVersionState::NotObserved,
        scope: "declared_runtime",
        source: "host_native_catalog",
        freshness: "provider_lifetime_catalog",
        reason: "native_catalog_unavailable",
    };
    if data.get("mlt_version") == Some(&Value::Null) {
        if available {
            return Err(backend("MLT catalog version and availability disagree"));
        }
        return Ok(parsed);
    }
    if !available {
        return Err(backend("MLT catalog version and availability disagree"));
    }
    let output = text(data.get("mlt_version"), 512)?;
    parsed
        .components
        .insert("mlt_version_output", output.to_owned());
    // The runtime selects a melt identification line. Its fallback is not an
    // observed version, and a lane-specific repair pin is not a global policy.
    if let Some(value) = output.trim().strip_prefix("melt ") {
        let value = Value::String(value.trim().to_owned());
        if let Ok(observed) = version(Some(&value)) {
            parsed.observed = Some(observed.to_owned());
            parsed.state = NativeRuntimeVersionState::Observed;
            parsed.reason = "native_catalog_version_observed";
            return Ok(parsed);
        }
    }
    parsed.reason = "native_catalog_version_not_identified";
    Ok(parsed)
}

fn parse_motion_canvas(data: &Value) -> NativeResult<ParsedProbe> {
    closed_object(
        data,
        &[
            "driver_version",
            "motion_canvas_version",
            "node_version",
            "managed_schema_version",
            "renderer_mode",
            "network",
            "project_mounted",
            "media_mounted",
            "output_mounted",
            "runtime_mounted",
            "render_available",
            "renderer_reason",
            "active_jobs",
            "capability_count",
            "runtime_probe",
        ],
    )?;
    token(data.get("driver_version"), 80)?;
    text(data.get("renderer_reason"), 1024)?;
    if data.get("motion_canvas_version").and_then(Value::as_str) != Some(MC_VERSION)
        || data.get("node_version").and_then(Value::as_str) != Some(NODE_VERSION)
        || data
            .get("managed_schema_version")
            .and_then(Value::as_u64)
            .is_none()
        || data.get("renderer_mode").and_then(Value::as_str)
            != Some("pinned_vite_playwright_harness")
        || data.get("network").and_then(Value::as_bool) != Some(false)
        || data.get("runtime_mounted").and_then(Value::as_bool) != Some(true)
        || data.get("render_available").and_then(Value::as_bool) != Some(true)
        || data.get("active_jobs").and_then(Value::as_u64).is_none()
        || data
            .get("capability_count")
            .and_then(Value::as_u64)
            .is_none()
        || ["project_mounted", "media_mounted", "output_mounted"]
            .iter()
            .any(|key| data.get(*key).and_then(Value::as_bool).is_none())
    {
        return Err(backend(
            "Motion Canvas doctor response contract is unsupported",
        ));
    }
    let probe = data
        .get("runtime_probe")
        .ok_or_else(|| backend("Motion Canvas native package observation is missing"))?;
    closed_object(
        probe,
        &[
            "schema_version",
            "operation",
            "requested",
            "source",
            "state",
            "expected_motion_canvas_version",
            "expected_node_version",
            "observed_node_version",
            "observed_core_version",
            "observed_two_d_version",
            "observed_vite_plugin_version",
            "compatible",
            "reason_code",
            "render_performed",
            "scene_mutation_performed",
            "browser_probe_performed",
        ],
    )?;
    if probe.get("schema_version").and_then(Value::as_u64) != Some(1)
        || probe.get("operation").and_then(Value::as_str) != Some("runtime_version_probe")
        || probe.get("requested").and_then(Value::as_bool) != Some(true)
        || probe.get("source").and_then(Value::as_str) != Some("host_native_package_probe")
        || probe
            .get("expected_motion_canvas_version")
            .and_then(Value::as_str)
            != Some(MC_VERSION)
        || probe.get("expected_node_version").and_then(Value::as_str) != Some(NODE_VERSION)
        || [
            "render_performed",
            "scene_mutation_performed",
            "browser_probe_performed",
        ]
        .iter()
        .any(|key| probe.get(*key).and_then(Value::as_bool) != Some(false))
    {
        return Err(backend(
            "Motion Canvas package probe contract is unsupported",
        ));
    }
    let node = version(probe.get("observed_node_version"))?;
    let mut components = BTreeMap::from([("node", node.to_owned())]);
    let packages = [
        ("core", "observed_core_version"),
        ("two_d", "observed_two_d_version"),
        ("vite_plugin", "observed_vite_plugin_version"),
    ];
    let (observed, compatible, state, reason) = match probe.get("state").and_then(Value::as_str) {
        Some("observed") => {
            let mut compatible = node == NODE_VERSION;
            for (name, key) in packages {
                let value = version(probe.get(key))?;
                compatible &= value == MC_VERSION;
                components.insert(name, value.to_owned());
            }
            let reason = if compatible {
                "pinned_versions_observed"
            } else {
                "observed_version_mismatch"
            };
            (
                Some(components["core"].clone()),
                Some(compatible),
                if compatible {
                    NativeRuntimeVersionState::ObservedCompatible
                } else {
                    NativeRuntimeVersionState::ObservedIncompatible
                },
                reason,
            )
        }
        Some("package_not_found") | Some("invalid_package") | Some("scope_violation") => {
            if packages
                .iter()
                .any(|(_, key)| probe.get(*key) != Some(&Value::Null))
            {
                return Err(backend(
                    "Failed Motion Canvas probe claims package versions",
                ));
            }
            let (state, reason) = match probe["state"].as_str() {
                Some("package_not_found") => (
                    NativeRuntimeVersionState::PackageNotFound,
                    "runtime_package_not_found",
                ),
                Some("invalid_package") => (
                    NativeRuntimeVersionState::NotObserved,
                    "runtime_package_invalid",
                ),
                _ => (
                    NativeRuntimeVersionState::NotObserved,
                    "runtime_package_outside_mount",
                ),
            };
            (None, None, state, reason)
        }
        _ => {
            return Err(backend(
                "Motion Canvas package observation state is unsupported",
            ));
        }
    };
    if probe.get("compatible") != Some(&json!(compatible))
        || probe.get("reason_code").and_then(Value::as_str) != Some(reason)
    {
        return Err(backend(
            "Motion Canvas package compatibility is inconsistent",
        ));
    }
    Ok(ParsedProbe {
        expected: Some(MC_VERSION),
        observed,
        components,
        compatible,
        state,
        scope: "declared_runtime",
        source: "host_native_package_probe",
        freshness: "current_request",
        reason,
    })
}

fn validate_provenance(probe: Probe, original: &BrokerResult) -> NativeResult<(u64, &str)> {
    let value = &original.value;
    if value.get("provenance_checked").and_then(Value::as_bool) != Some(true)
        || value.pointer("/execution/dry_run").and_then(Value::as_bool) != Some(false)
        || value
            .pointer("/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some(probe.provider())
        || value.pointer("/execution/provenance/source") != Some(&json!(SourceKind::Driver))
    {
        return Err(backend(
            "Native runtime observation lacks authenticated driver provenance",
        ));
    }
    let generation = value
        .pointer("/execution/provenance/provider_generation")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("Native runtime observation has no provider generation"))?;
    let descriptor = token(value.pointer("/execution/provenance/descriptor_sha256"), 64)?;
    if !is_sha256(descriptor) {
        return Err(backend(
            "Native runtime observation descriptor digest is invalid",
        ));
    }
    if original.request_id.is_empty()
        || original.request_id.len() > 128
        || original.request_id.chars().any(char::is_control)
    {
        return Err(backend(
            "Native runtime observation request identity is invalid",
        ));
    }
    Ok((generation, descriptor))
}

fn authenticate(
    probe: Probe,
    original: &BrokerResult,
    expected: &RevisionStamp,
    client: &ProductionClient,
) -> NativeResult<NativeRuntimeVersionObservation> {
    let (generation, descriptor) = validate_provenance(probe, original)?;
    let value = &original.value;
    let parsed = probe.parse(
        value
            .get("data")
            .ok_or_else(|| backend("Native runtime observation data is missing"))?,
    )?;
    Ok(NativeRuntimeVersionObservation {
        schema: "motionwright-native-runtime-observation/1",
        stamp: RevisionStamp {
            resource: expected.resource.clone(),
            generation: expected.generation,
            revision: expected.revision,
        },
        connection_identity: client.connection().identity()?,
        transport_executable_sha256: client.connection().executable_sha256.clone(),
        broker_request_id: original.request_id.clone(),
        renderer: probe.renderer(),
        provider: probe.provider(),
        provider_generation: generation,
        descriptor_sha256: descriptor.to_owned(),
        expected_version: parsed.expected,
        observed_version: parsed.observed,
        observed_components: parsed.components,
        version_compatible: parsed.compatible,
        state: parsed.state,
        observation_scope: parsed.scope,
        observation_source: parsed.source,
        observation_freshness: parsed.freshness,
        reason_code: parsed.reason,
        render_performed: false,
        scene_mutation_performed: false,
    })
}

impl ProductionClient {
    async fn observe_selected_native_runtime(
        &self,
        expected: &RevisionStamp,
        probe: Probe,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        if self.connection().resource != expected.resource {
            return Err(Error::new(
                ErrorCode::PolicyDenied,
                "Native runtime observation connection is bound to another project resource",
            ));
        }
        let original = self.execute(probe.command(), probe.args(), false).await?;
        authenticate(probe, &original, expected, self)
    }
    pub async fn observe_motion_canvas_runtime_version(
        &self,
        expected: &RevisionStamp,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.observe_selected_native_runtime(expected, Probe::MotionCanvas)
            .await
    }
    pub async fn observe_mlt_runtime_version(
        &self,
        expected: &RevisionStamp,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.observe_selected_native_runtime(expected, Probe::Mlt)
            .await
    }
    pub async fn observe_blender_runtime_version(
        &self,
        expected: &RevisionStamp,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.observe_selected_native_runtime(expected, Probe::Blender)
            .await
    }
}

impl ProductionCoordinator {
    fn require_native_version_scope(
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
                "Motionwright project changed during the native runtime observation",
            ));
        }
        Ok(())
    }
    async fn observe_selected_native_runtime(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        probe: Probe,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.require_native_version_scope(project_id, expected)?;
        // Preserve any original typed failure, including UNKNOWN, before a later scope check.
        let observation = self
            .client
            .observe_selected_native_runtime(expected, probe)
            .await?;
        self.require_native_version_scope(project_id, expected)?;
        Ok(observation)
    }
    pub async fn observe_motion_canvas_runtime_version(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.observe_selected_native_runtime(project_id, expected, Probe::MotionCanvas)
            .await
    }
    pub async fn observe_mlt_runtime_version(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.observe_selected_native_runtime(project_id, expected, Probe::Mlt)
            .await
    }
    pub async fn observe_blender_runtime_version(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
    ) -> NativeResult<NativeRuntimeVersionObservation> {
        self.observe_selected_native_runtime(project_id, expected, Probe::Blender)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blender_data() -> Value {
        json!({
            "version":[4,5,14], "version_string":"4.5.14 LTS",
            "rna_types":1200, "operator_categories":50, "operators":1300,
            "available_addons":12, "enabled_addons":0,
            "arbitrary_python":false, "generic_operator_invoke":false
        })
    }
    fn mlt_data() -> Value {
        json!({
            "driver_version":"0.1.0", "driver_protocol":7,
            "adapters":["generic-mlt/1","kdenlive-generation5-inspection/1","shotcut-annotations-inspection/1"],
            "render_available":true, "mlt_version":"melt 7.22.0", "capabilities":40,
            "filesystem_mounts":["media","output","project"],
            "reason":"Awaiting Host-mediated MLT catalog verification", "network":false,
            "native_gui_verified":false
        })
    }
    fn mc_data() -> Value {
        json!({
            "driver_version":"0.1.0", "motion_canvas_version":MC_VERSION, "node_version":NODE_VERSION,
            "managed_schema_version":1, "renderer_mode":"pinned_vite_playwright_harness",
            "network":false, "project_mounted":true, "media_mounted":true, "output_mounted":true,
            "runtime_mounted":true, "render_available":true, "renderer_reason":"",
            "active_jobs":0, "capability_count":40,
            "runtime_probe":{
                "schema_version":1, "operation":"runtime_version_probe", "requested":true,
                "source":"host_native_package_probe", "state":"observed",
                "expected_motion_canvas_version":MC_VERSION, "expected_node_version":NODE_VERSION,
                "observed_node_version":NODE_VERSION, "observed_core_version":MC_VERSION,
                "observed_two_d_version":MC_VERSION, "observed_vite_plugin_version":MC_VERSION,
                "compatible":true, "reason_code":"pinned_versions_observed",
                "render_performed":false, "scene_mutation_performed":false, "browser_probe_performed":false
            }
        })
    }
    fn data(probe: Probe) -> Value {
        match probe {
            Probe::MotionCanvas => mc_data(),
            Probe::Mlt => mlt_data(),
            Probe::Blender => blender_data(),
        }
    }

    #[test]
    fn native_blender_uses_current_summary_tuple_and_preserves_display() {
        let original = parse_blender(&blender_data()).unwrap();
        assert_eq!(original.observed.as_deref(), Some("4.5.14"));
        assert_eq!(original.components["version_string"], "4.5.14 LTS");
        assert_eq!(
            original.state,
            NativeRuntimeVersionState::ObservedCompatible
        );
        assert_eq!(original.freshness, "current_request");
        let mut mismatch = blender_data();
        mismatch["version"] = json!([4, 4, 3]);
        // A declared or cached-looking display cannot override the observed tuple.
        let parsed = parse_blender(&mismatch).unwrap();
        assert_eq!(parsed.observed.as_deref(), Some("4.4.3"));
        assert_eq!(parsed.compatible, Some(false));
        assert_eq!(
            parsed.state,
            NativeRuntimeVersionState::ObservedIncompatible
        );
    }

    #[test]
    fn native_blender_rejects_expanded_or_malformed_native_summaries() {
        for (pointer, value) in [
            ("/version", json!([4, 5])),
            ("/version", json!([4, 5, 100])),
            ("/version", json!(["4", 5, 14])),
            ("/version", json!([4, 5, -1])),
            ("/version_string", json!("4.5.14\n")),
            ("/rna_types", json!(100001)),
            ("/operator_categories", json!(4097)),
            ("/arbitrary_python", json!(true)),
            ("/generic_operator_invoke", json!(true)),
        ] {
            let mut value_copy = blender_data();
            *value_copy.pointer_mut(pointer).unwrap() = value;
            assert!(parse_blender(&value_copy).is_err(), "{pointer}");
        }
        let mut expanded = blender_data();
        expanded["health_cache"] = json!(true);
        assert!(parse_blender(&expanded).is_err());
    }

    #[test]
    fn native_mlt_preserves_catalog_scope_without_inventing_global_compatibility() {
        let original = parse_mlt(&mlt_data()).unwrap();
        assert_eq!(original.observed.as_deref(), Some("7.22.0"));
        assert_eq!(original.components["mlt_version_output"], "melt 7.22.0");
        assert_eq!(original.expected, None);
        assert_eq!(original.compatible, None);
        assert_eq!(original.state, NativeRuntimeVersionState::Observed);
        assert_eq!(original.freshness, "provider_lifetime_catalog");
        let mut different = mlt_data();
        different["mlt_version"] = json!("melt 7.34.1");
        let parsed = parse_mlt(&different).unwrap();
        assert_eq!(parsed.observed.as_deref(), Some("7.34.1"));
        assert_eq!(parsed.compatible, None);
        let mut stderr_only = mlt_data();
        stderr_only["mlt_version"] = json!(" melt 7.22.0");
        let parsed = parse_mlt(&stderr_only).unwrap();
        assert_eq!(parsed.observed.as_deref(), Some("7.22.0"));
        assert_eq!(parsed.components["mlt_version_output"], " melt 7.22.0");
        assert_eq!(parsed.state, NativeRuntimeVersionState::Observed);
        let mut absent = mlt_data();
        absent["render_available"] = json!(false);
        absent["mlt_version"] = Value::Null;
        let parsed = parse_mlt(&absent).unwrap();
        assert_eq!(parsed.state, NativeRuntimeVersionState::NotObserved);
        assert_eq!(parsed.observed, None);
        let mut fallback = mlt_data();
        fallback["mlt_version"] = json!("Version output did not identify melt");
        let parsed = parse_mlt(&fallback).unwrap();
        assert_eq!(parsed.state, NativeRuntimeVersionState::NotObserved);
        assert_eq!(parsed.reason, "native_catalog_version_not_identified");
        assert_eq!(parsed.observed, None);
    }

    #[test]
    fn native_mlt_rejects_inconsistent_catalog_and_foreign_fields() {
        for (pointer, replacement) in [
            ("/driver_protocol", json!(8)),
            ("/adapters", json!([])),
            ("/render_available", json!(false)),
            ("/mlt_version", Value::Null),
            ("/mlt_version", json!("melt 7.22.0\n")),
            ("/mlt_version", json!("x".repeat(513))),
            ("/filesystem_mounts", json!(["media", "media"])),
            ("/filesystem_mounts", json!(["runtime"])),
            ("/network", json!(true)),
            ("/native_gui_verified", json!(true)),
        ] {
            let mut changed = mlt_data();
            *changed.pointer_mut(pointer).unwrap() = replacement;
            assert!(parse_mlt(&changed).is_err(), "{pointer}");
        }
        let mut metadata = mlt_data();
        metadata["expected_version"] = json!("7.22.0");
        assert!(parse_mlt(&metadata).is_err());
    }

    #[test]
    fn native_motion_canvas_checks_node_and_each_observed_package() {
        let pinned = parse_motion_canvas(&mc_data()).unwrap();
        assert_eq!(pinned.state, NativeRuntimeVersionState::ObservedCompatible);
        assert_eq!(pinned.components.len(), 4);
        for (field, component) in [
            ("observed_node_version", "node"),
            ("observed_core_version", "core"),
            ("observed_two_d_version", "two_d"),
            ("observed_vite_plugin_version", "vite_plugin"),
        ] {
            let mut changed = mc_data();
            changed["runtime_probe"][field] = json!("99.1.2");
            changed["runtime_probe"]["compatible"] = json!(false);
            changed["runtime_probe"]["reason_code"] = json!("observed_version_mismatch");
            let parsed = parse_motion_canvas(&changed).unwrap();
            assert_eq!(
                parsed.state,
                NativeRuntimeVersionState::ObservedIncompatible
            );
            assert_eq!(parsed.components[component], "99.1.2");
            assert_eq!(parsed.compatible, Some(false));
        }
    }

    #[test]
    fn native_motion_canvas_distinguishes_absence_from_invalid_and_scope_failures() {
        for (state, reason, expected) in [
            (
                "package_not_found",
                "runtime_package_not_found",
                NativeRuntimeVersionState::PackageNotFound,
            ),
            (
                "invalid_package",
                "runtime_package_invalid",
                NativeRuntimeVersionState::NotObserved,
            ),
            (
                "scope_violation",
                "runtime_package_outside_mount",
                NativeRuntimeVersionState::NotObserved,
            ),
        ] {
            let mut changed = mc_data();
            let probe = &mut changed["runtime_probe"];
            probe["state"] = json!(state);
            probe["reason_code"] = json!(reason);
            probe["compatible"] = Value::Null;
            for key in [
                "observed_core_version",
                "observed_two_d_version",
                "observed_vite_plugin_version",
            ] {
                probe[key] = Value::Null;
            }
            let parsed = parse_motion_canvas(&changed).unwrap();
            assert_eq!(parsed.state, expected);
            assert_eq!(parsed.observed, None);
            assert_eq!(parsed.compatible, None);
            assert_eq!(
                parsed.components,
                BTreeMap::from([("node", NODE_VERSION.to_owned())])
            );
            changed["runtime_probe"]["observed_core_version"] = json!(MC_VERSION);
            assert!(parse_motion_canvas(&changed).is_err());
        }
    }

    #[test]
    fn native_motion_canvas_never_promotes_declared_pins_or_forged_probe_flags() {
        for (pointer, replacement) in [
            ("/runtime_mounted", json!(false)),
            ("/render_available", json!(false)),
            ("/runtime_probe/source", json!("runtime_contract")),
            ("/runtime_probe/requested", json!(false)),
            ("/runtime_probe/schema_version", json!(2)),
            (
                "/runtime_probe/expected_motion_canvas_version",
                json!("3.18.0"),
            ),
            ("/runtime_probe/expected_node_version", json!("24.0.0")),
            ("/runtime_probe/observed_node_version", Value::Null),
            ("/runtime_probe/observed_core_version", Value::Null),
            ("/runtime_probe/observed_two_d_version", json!("3.17.2\n")),
            (
                "/runtime_probe/observed_vite_plugin_version",
                json!("x".repeat(81)),
            ),
            ("/runtime_probe/compatible", json!(false)),
            ("/runtime_probe/reason_code", json!("version_observed")),
            ("/runtime_probe/render_performed", json!(true)),
            ("/runtime_probe/scene_mutation_performed", json!(true)),
            ("/runtime_probe/browser_probe_performed", json!(true)),
        ] {
            let mut changed = mc_data();
            *changed.pointer_mut(pointer).unwrap() = replacement;
            assert!(parse_motion_canvas(&changed).is_err(), "{pointer}");
        }
        let mut metadata_only = mc_data();
        metadata_only
            .as_object_mut()
            .unwrap()
            .remove("runtime_probe");
        assert!(parse_motion_canvas(&metadata_only).is_err());
        let mut expanded = mc_data();
        expanded["runtime_probe"]["injected"] = json!(true);
        assert!(parse_motion_canvas(&expanded).is_err());
    }

    fn broker(probe: Probe) -> BrokerResult {
        BrokerResult {
            request_id: "native-probe-original-request".into(),
            value: json!({
                "data":data(probe), "provenance_checked":true,
                "execution":{"dry_run":false,"provenance":{
                    "provider":probe.provider(), "source":SourceKind::Driver,
                    "provider_generation":17, "descriptor_sha256":"ab".repeat(32)
                }}
            }),
        }
    }

    #[test]
    fn native_runtime_requires_exact_provider_generation_descriptor_and_request_identity() {
        for probe in [Probe::MotionCanvas, Probe::Mlt, Probe::Blender] {
            let original = broker(probe);
            assert_eq!(
                validate_provenance(probe, &original).unwrap(),
                (17, "ab".repeat(32).as_str())
            );
            for (pointer, replacement) in [
                ("/provenance_checked", json!(false)),
                ("/execution/dry_run", json!(true)),
                ("/execution/provenance/provider", json!("driver:unrelated")),
                ("/execution/provenance/source", json!(SourceKind::Builtin)),
                ("/execution/provenance/provider_generation", Value::Null),
                (
                    "/execution/provenance/descriptor_sha256",
                    json!("AB".repeat(32)),
                ),
                ("/execution/provenance/descriptor_sha256", json!("x")),
            ] {
                let mut changed = broker(probe);
                *changed.value.pointer_mut(pointer).unwrap() = replacement;
                assert!(validate_provenance(probe, &changed).is_err(), "{pointer}");
            }
            for request in ["".to_owned(), "x".repeat(129), "request\n".to_owned()] {
                let mut changed = broker(probe);
                changed.request_id = request;
                assert!(validate_provenance(probe, &changed).is_err());
            }
        }
    }

    #[cfg(unix)]
    async fn probe_after_peer_commit(probe: Probe, original_failure: bool) -> (Error, Error) {
        use crate::production::tests::{fake_transport_connection, transport_reply};
        use motionwright_domain::Change;
        use motionwright_service::StudioService;
        use std::{fs, time::Duration};

        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("motionwright.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service
            .create_project("Native version original scope")
            .unwrap();
        let expected = project.stamp();
        let original_error =
            Error::new(ErrorCode::Timeout, "original native probe transport error")
                .uncertain()
                .recipe_progress(1, probe.command());
        let mut envelope = transport_reply(probe.command());
        if original_failure {
            envelope.ok = false;
            envelope.data = None;
            envelope.error = Some(original_error.clone());
        } else {
            envelope.data = Some(data(probe));
        }
        let output = serde_json::to_string(&envelope).unwrap();
        let (mut connection, counter) = fake_transport_connection(
            &temporary,
            project.resource_key(),
            &output,
            "",
            if original_failure { 19 } else { 0 },
        );
        let release = temporary.path().join("release-native-probe");
        let script = fs::read_to_string(&connection.executable).unwrap();
        let waiting = format!(
            "\nwhile [ ! -f '{}' ]; do sleep 0.01; done\nprintf '%s'",
            release.to_string_lossy().replace('\'', "'\\''"),
        );
        let script = script.replacen("\nprintf '%s'", &waiting, 1);
        assert!(script.contains("release-native-probe"));
        fs::write(&connection.executable, script).unwrap();
        connection.executable_sha256 =
            crate::production::sha256_file(&connection.executable).unwrap();
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let commit = async {
            let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
            while !counter.exists() {
                assert!(
                    tokio::time::Instant::now() < deadline,
                    "native probe did not start"
                );
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            let peer = StudioService::open(&database).unwrap();
            let edited = peer
                .apply(
                    project.id,
                    &expected,
                    "native-probe-peer-commit",
                    &Change::RenameProject {
                        title: "Peer committed during native probe".into(),
                    },
                )
                .unwrap()
                .project;
            assert_eq!(edited.revision, project.revision + 1);
            fs::write(&release, b"release original reply").unwrap();
            edited
        };
        let (result, edited) = tokio::join!(
            coordinator.observe_selected_native_runtime(project.id, &expected, probe),
            commit,
        );
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        assert_eq!(service.project(project.id).unwrap(), edited);
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        (result.unwrap_err(), original_error)
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_runtime_success_is_rejected_after_real_peer_revision() {
        for probe in [Probe::MotionCanvas, Probe::Mlt, Probe::Blender] {
            let (returned, _) = probe_after_peer_commit(probe, false).await;
            assert_eq!(returned.code, ErrorCode::StaleReference);
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_runtime_original_unknown_error_is_preserved_without_retry_after_peer_revision()
    {
        for probe in [Probe::MotionCanvas, Probe::Mlt, Probe::Blender] {
            let (returned, original) = probe_after_peer_commit(probe, true).await;
            assert_eq!(
                serde_json::to_value(returned).unwrap(),
                serde_json::to_value(original).unwrap()
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_runtime_wrong_project_scope_fails_before_transport() {
        use crate::production::tests::{fake_transport_connection, transport_reply};
        use motionwright_service::StudioService;
        for probe in [Probe::MotionCanvas, Probe::Mlt, Probe::Blender] {
            let temporary = tempfile::tempdir().unwrap();
            let service =
                StudioService::open(temporary.path().join("motionwright.sqlite3")).unwrap();
            let project = service
                .create_project("Native version stale scope")
                .unwrap();
            let mut envelope = transport_reply(probe.command());
            envelope.data = Some(data(probe));
            let (connection, counter) = fake_transport_connection(
                &temporary,
                project.resource_key(),
                &serde_json::to_string(&envelope).unwrap(),
                "",
                0,
            );
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            for changed in [
                RevisionStamp {
                    resource: "another-project".into(),
                    ..project.stamp()
                },
                RevisionStamp {
                    generation: Uuid::new_v4(),
                    ..project.stamp()
                },
                RevisionStamp {
                    revision: project.revision + 1,
                    ..project.stamp()
                },
            ] {
                let error = coordinator
                    .observe_selected_native_runtime(project.id, &changed, probe)
                    .await
                    .unwrap_err();
                assert_eq!(error.code, ErrorCode::StaleReference);
                assert!(!counter.exists());
            }
            let mut other = project.stamp();
            other.resource = "another-project".into();
            let error = coordinator
                .client
                .observe_selected_native_runtime(&other, probe)
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::PolicyDenied);
            assert!(!counter.exists());
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn native_runtime_observations_join_the_existing_renderer_registry() {
        use crate::production::renderer_registry::{registry_test_data, registry_test_provider};
        use crate::production::tests::{fake_transport_connection, transport_reply};
        use motionwright_service::StudioService;
        let temporary = tempfile::tempdir().unwrap();
        let service = StudioService::open(temporary.path().join("motionwright.sqlite3")).unwrap();
        let project = service
            .create_project("Native version registry join")
            .unwrap();
        let expected = project.stamp();
        let providers = [Probe::MotionCanvas, Probe::Mlt, Probe::Blender]
            .into_iter()
            .map(|probe| {
                let mut provider = registry_test_provider(probe.provider(), true);
                provider["generation"] = json!(17);
                provider
            })
            .collect();
        let mut envelope = transport_reply("doctor");
        envelope.data = Some(registry_test_data(providers));
        let (connection, counter) = fake_transport_connection(
            &temporary,
            project.resource_key(),
            &serde_json::to_string(&envelope).unwrap(),
            "",
            0,
        );
        let client = ProductionClient::new(connection).unwrap();
        let registry = client.discover_renderer_registry(&expected).await.unwrap();
        for probe in [Probe::MotionCanvas, Probe::Mlt, Probe::Blender] {
            let observation = authenticate(probe, &broker(probe), &expected, &client).unwrap();
            let state = registry
                .state_for(observation.renderer())
                .expect("canonical renderer registry key");
            assert_eq!(state.generation(), Some(observation.provider_generation()));
            assert!(observation.matches_scope(&expected));
        }
        assert_eq!(std::fs::read(&counter).unwrap(), b"invoked\n");
    }
}
