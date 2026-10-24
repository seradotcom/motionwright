use super::{Error, ErrorCode, NativeResult, ProductionClient, backend};
use motionwright_domain::RevisionStamp;
use serde_json::{Value, json};

const RENDERERS: [(&str, &str); 4] = [
    ("motion-canvas", "driver:motion-canvas"),
    ("mlt", "driver:mlt-video"),
    ("blender", "driver:blender"),
    ("manim-community", "driver:manim-community"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RendererRegistryState {
    PresentConnected { generation: u64 },
    PresentDisconnected { generation: u64 },
    NotRegistered,
}
impl RendererRegistryState {
    pub fn label(self) -> &'static str {
        match self {
            Self::PresentConnected { .. } => "present_connected",
            Self::PresentDisconnected { .. } => "present_disconnected",
            Self::NotRegistered => "not_registered_current_broker",
        }
    }
    pub fn generation(self) -> Option<u64> {
        match self {
            Self::PresentConnected { generation } | Self::PresentDisconnected { generation } => {
                Some(generation)
            }
            Self::NotRegistered => None,
        }
    }
}

/// An observation from the authenticated, fixed doctor command.
/// Registration and lease state do not establish native runtime readiness.
#[derive(Debug)]
pub struct RendererRegistryObservation {
    stamp: RevisionStamp,
    broker_request_id: String,
    catalog_revision: u64,
    states: [RendererRegistryState; 4],
    provider_versions: [Option<String>; 4],
}
impl RendererRegistryObservation {
    pub fn matches_scope(&self, stamp: &RevisionStamp) -> bool {
        self.stamp.resource == stamp.resource
            && self.stamp.generation == stamp.generation
            && self.stamp.revision == stamp.revision
    }
    pub fn broker_request_id(&self) -> &str {
        &self.broker_request_id
    }
    pub fn catalog_revision(&self) -> u64 {
        self.catalog_revision
    }
    pub fn state_for(&self, renderer: &str) -> Option<RendererRegistryState> {
        RENDERERS
            .iter()
            .position(|(name, _)| *name == renderer)
            .map(|i| self.states[i])
    }
    pub fn provider_version_for(&self, renderer: &str) -> Option<&str> {
        RENDERERS
            .iter()
            .position(|(name, _)| *name == renderer)
            .and_then(|i| self.provider_versions[i].as_deref())
    }
}

fn token(value: &Value, max: usize) -> Result<&str, &'static str> {
    let value = value.as_str().ok_or("Registry token must be a string")?;
    if value.is_empty() || value.len() > max || value.chars().any(char::is_control) {
        return Err("Registry token is out of bounds");
    }
    Ok(value)
}

fn parse_doctor_registry(
    data: &Value,
    stamp: &RevisionStamp,
    broker_request_id: &str,
) -> Result<RendererRegistryObservation, &'static str> {
    if data.get("project").and_then(Value::as_str) != Some("Semwright")
        || data.get("fake").and_then(Value::as_bool) != Some(false)
    {
        return Err("Renderer registry requires a real Semwright doctor response");
    }
    if broker_request_id.is_empty()
        || broker_request_id.len() > 128
        || broker_request_id.chars().any(char::is_control)
    {
        return Err("Renderer registry requires a bounded original Broker request id");
    }
    let registry = data
        .get("providers")
        .and_then(Value::as_object)
        .ok_or("Doctor provider registry is missing")?;
    if registry.len() != 3
        || registry.get("registration").and_then(Value::as_str) != Some("owner configuration only")
    {
        return Err("Doctor provider registry shape is unsupported");
    }
    let catalog_revision = registry
        .get("revision")
        .and_then(Value::as_u64)
        .ok_or("Doctor registry revision is invalid")?;
    let providers = registry
        .get("providers")
        .and_then(Value::as_array)
        .ok_or("Doctor provider list is invalid")?;
    let mut observed: [Option<RendererRegistryState>; 4] = [None; 4];
    let mut provider_versions: [Option<String>; 4] = std::array::from_fn(|_| None);
    for provider in providers {
        let row = provider
            .as_object()
            .ok_or("Doctor provider entry is invalid")?;
        if row.len() != 5 {
            return Err("Doctor provider entry shape is unsupported");
        }
        let identity = row
            .get("identity")
            .and_then(Value::as_object)
            .ok_or("Doctor provider identity is invalid")?;
        if identity.len() != 6 {
            return Err("Doctor provider identity shape is unsupported");
        }
        token(
            identity
                .get("kind")
                .ok_or("Doctor provider kind is missing")?,
            32,
        )?;
        let version = token(
            identity
                .get("version")
                .ok_or("Doctor provider version is missing")?,
            80,
        )?;
        let namespace = token(
            identity
                .get("namespace")
                .ok_or("Doctor provider namespace is missing")?,
            256,
        )?;
        token(
            identity
                .get("origin")
                .ok_or("Doctor provider origin is missing")?,
            256,
        )?;
        let application = identity
            .get("application")
            .ok_or("Doctor provider application field is missing")?;
        if !application.is_null() {
            token(application, 256)?;
        }
        let id = token(
            identity.get("id").ok_or("Doctor provider id is missing")?,
            256,
        )?;
        token(
            row.get("route").ok_or("Doctor provider route is missing")?,
            256,
        )?;
        let generation = row
            .get("generation")
            .and_then(Value::as_u64)
            .ok_or("Doctor provider generation is invalid")?;
        let connected = row
            .get("connected")
            .and_then(Value::as_bool)
            .ok_or("Doctor provider connection state is invalid")?;
        let interfaces = row
            .get("interfaces")
            .and_then(Value::as_object)
            .ok_or("Doctor provider interface summary is invalid")?;
        const INTERFACES: [&str; 6] = [
            "dynamic_capabilities",
            "cancellation",
            "events",
            "progress",
            "artifacts",
            "health",
        ];
        if interfaces.len() != INTERFACES.len()
            || INTERFACES
                .iter()
                .any(|name| interfaces.get(*name).and_then(Value::as_bool).is_none())
        {
            return Err("Doctor provider interface summary shape is unsupported");
        }
        if let Some(index) = RENDERERS.iter().position(|(_, expected)| *expected == id) {
            let expected_namespace = format!(
                "driver.{}.",
                RENDERERS[index]
                    .1
                    .strip_prefix("driver:")
                    .expect("closed renderer identity")
            );
            if identity.get("kind") != Some(&json!(super::SourceKind::Driver))
                || namespace != expected_namespace
            {
                return Err("Doctor renderer identity kind or namespace is inconsistent");
            }
            let state = if connected {
                RendererRegistryState::PresentConnected { generation }
            } else {
                RendererRegistryState::PresentDisconnected { generation }
            };
            if observed[index].replace(state).is_some() {
                return Err("Doctor registry contains an ambiguous renderer identity");
            }
            provider_versions[index] = Some(version.to_owned());
        }
    }
    Ok(RendererRegistryObservation {
        stamp: RevisionStamp {
            resource: stamp.resource.clone(),
            generation: stamp.generation,
            revision: stamp.revision,
        },
        broker_request_id: broker_request_id.to_owned(),
        catalog_revision,
        states: observed.map(|state| state.unwrap_or(RendererRegistryState::NotRegistered)),
        provider_versions,
    })
}

impl ProductionClient {
    pub async fn discover_renderer_registry(
        &self,
        expected: &RevisionStamp,
    ) -> NativeResult<RendererRegistryObservation> {
        if self.connection().resource != expected.resource {
            return Err(Error::new(
                ErrorCode::PolicyDenied,
                "Renderer discovery connection is bound to another project resource",
            ));
        }
        let original = self.execute("doctor", json!({}), false).await?;
        let data = original
            .value
            .get("data")
            .ok_or_else(|| backend("Authenticated doctor response contains no data"))?;
        parse_doctor_registry(data, expected, &original.request_id).map_err(backend)
    }
}

#[cfg(test)]
pub(super) fn registry_test_data(providers: Vec<Value>) -> Value {
    json!({"project":"Semwright","fake":false,"providers":{
        "providers":providers,"revision":9,"registration":"owner configuration only"
    }})
}
#[cfg(test)]
pub(super) fn registry_test_provider(id: &str, connected: bool) -> Value {
    let slug = id.strip_prefix("driver:").expect("fixture driver identity");
    json!({
        "identity":{"id":id,"kind":super::SourceKind::Driver,"version":"0.1.0",
            "namespace":format!("driver.{slug}."),"application":null,
            "origin":"synthetic renderer registry fixture"},
        "route":id,"generation":7,"connected":connected,
        "interfaces":{"dynamic_capabilities":false,"cancellation":false,"events":false,
            "progress":false,"artifacts":false,"health":false}
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn stamp() -> RevisionStamp {
        RevisionStamp {
            resource: "motionwright:renderer-registry-fixture".into(),
            generation: uuid::Uuid::from_u128(1),
            revision: 4,
        }
    }
    #[test]
    fn registry_preserves_connected_disconnected_and_unregistered_states() {
        let data = registry_test_data(vec![
            registry_test_provider("driver:blender", true),
            registry_test_provider("driver:manim-community", false),
        ]);
        let registry = parse_doctor_registry(&data, &stamp(), "fixture-request").unwrap();
        assert_eq!(registry.catalog_revision(), 9);
        assert_eq!(registry.broker_request_id(), "fixture-request");
        assert_eq!(
            registry.state_for("blender"),
            Some(RendererRegistryState::PresentConnected { generation: 7 })
        );
        assert_eq!(
            registry.state_for("manim-community"),
            Some(RendererRegistryState::PresentDisconnected { generation: 7 })
        );
        assert_eq!(
            registry.state_for("motion-canvas"),
            Some(RendererRegistryState::NotRegistered)
        );
        assert_eq!(
            registry.state_for("mlt"),
            Some(RendererRegistryState::NotRegistered)
        );
        assert_eq!(registry.provider_version_for("blender"), Some("0.1.0"));
        assert_eq!(registry.provider_version_for("mlt"), None);
        assert_eq!(registry.state_for("remotion"), None);
    }
    #[test]
    fn complete_empty_registry_means_not_registered_in_this_broker() {
        let registry =
            parse_doctor_registry(&registry_test_data(vec![]), &stamp(), "fixture-request")
                .unwrap();
        for (renderer, _) in RENDERERS {
            assert_eq!(
                registry.state_for(renderer),
                Some(RendererRegistryState::NotRegistered)
            );
            assert_eq!(registry.provider_version_for(renderer), None);
        }
    }
    #[test]
    fn fake_or_incomplete_registry_never_becomes_an_absence_observation() {
        let valid = registry_test_data(vec![registry_test_provider("driver:blender", true)]);
        let mut fake = valid.clone();
        fake["fake"] = json!(true);
        let mut missing_list = valid.clone();
        missing_list["providers"]
            .as_object_mut()
            .unwrap()
            .remove("providers");
        let mut malformed = valid.clone();
        malformed["providers"]["providers"][0]["connected"] = json!("true");
        let mut missing_identity = valid;
        missing_identity["providers"]["providers"][0]["identity"]
            .as_object_mut()
            .unwrap()
            .remove("version");
        for data in [fake, missing_list, malformed, missing_identity] {
            assert!(parse_doctor_registry(&data, &stamp(), "fixture-request").is_err());
        }
    }
    #[test]
    fn duplicate_or_inconsistent_renderer_identity_is_rejected() {
        let provider = registry_test_provider("driver:blender", true);
        assert!(
            parse_doctor_registry(
                &registry_test_data(vec![provider.clone(), provider.clone()]),
                &stamp(),
                "fixture-request"
            )
            .is_err()
        );
        let mut wrong_kind = provider.clone();
        wrong_kind["identity"]["kind"] = json!(super::super::SourceKind::Builtin);
        let mut wrong_namespace = provider;
        wrong_namespace["identity"]["namespace"] = json!("driver.manim-community.");
        for provider in [wrong_kind, wrong_namespace] {
            assert!(
                parse_doctor_registry(
                    &registry_test_data(vec![provider]),
                    &stamp(),
                    "fixture-request"
                )
                .is_err()
            );
        }
    }
    #[test]
    fn registry_version_metadata_and_request_id_are_bounded() {
        for version in [String::new(), "x".repeat(81), "0.1.0\n".to_owned()] {
            let mut provider = registry_test_provider("driver:blender", true);
            provider["identity"]["version"] = json!(version);
            assert!(
                parse_doctor_registry(
                    &registry_test_data(vec![provider]),
                    &stamp(),
                    "fixture-request"
                )
                .is_err()
            );
        }
        for request_id in [String::new(), "x".repeat(129), "request\n".to_owned()] {
            assert!(
                parse_doctor_registry(&registry_test_data(vec![]), &stamp(), &request_id).is_err()
            );
        }
    }
    #[test]
    fn registry_observation_is_bound_to_resource_generation_and_revision() {
        let original = stamp();
        let registry =
            parse_doctor_registry(&registry_test_data(vec![]), &original, "fixture-request")
                .unwrap();
        assert!(registry.matches_scope(&original));
        let mut changed = stamp();
        changed.resource.push_str("-other");
        assert!(!registry.matches_scope(&changed));
        let mut changed = stamp();
        changed.generation = uuid::Uuid::from_u128(2);
        assert!(!registry.matches_scope(&changed));
        let mut changed = stamp();
        changed.revision += 1;
        assert!(!registry.matches_scope(&changed));
    }
}
