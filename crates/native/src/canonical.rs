use motionwright_domain::Project;
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult, Value,
    cooperation::ResourceVersion,
    effects_readback::{SpecificationInput, prepare_spec},
    graph_adapter::{native_locator, revision_candidate},
    json,
};
use semwright_project_graph as graph;

const PROVIDER: &str = "driver:motionwright";

fn graph_error(error: graph::GraphError) -> Error {
    Error::new(
        ErrorCode::InvalidArgument,
        format!("Canonical Project Graph adapter rejected Motionwright projection: {error}"),
    )
}

fn value_error(context: &'static str, error: impl std::fmt::Display) -> Error {
    Error::new(ErrorCode::InvalidArgument, format!("{context}: {error}"))
}

/// Exact application-owned project state used only as an untrusted Graph projection.
/// Canonical freshness is established later by a trusted Project Graph owner.
pub fn project_projection(project: &Project) -> NativeResult<Value> {
    serde_json::to_value(project).map_err(|error| {
        value_error(
            "Motionwright project projection could not be encoded",
            error,
        )
    })
}

/// Construct the Native SDK resource version for the current application-owned revision.
pub fn project_version(project: &Project) -> NativeResult<ResourceVersion> {
    let version = ResourceVersion {
        resource: project.resource_key(),
        generation: project.generation.to_string(),
        revision: semwright_native_sdk::cooperation::RevisionToken::new(
            project.revision.to_string(),
        )?,
    };
    version.validate()?;
    Ok(version)
}

/// Prepare an untrusted candidate for canonical Project Graph admission.
///
/// This function deliberately does not create Project Graph authority. The canonical
/// project/asset identifiers and binding generation must already come from the trusted
/// Graph owner, while provider_session must come from the authenticated Driver Host
/// channel. Returning this value never means CURRENT, STALE or admitted.
pub fn prepare_project_graph_candidate(
    project: &Project,
    canonical_project: &graph::ProjectId,
    canonical_asset: graph::LogicalAssetId,
    binding_generation: u64,
    provider_session: &str,
    observed_unix_ms: u64,
) -> NativeResult<Value> {
    let version = project_version(project)?;
    let projection = project_projection(project)?;
    let locator = native_locator(PROVIDER, &project.id.to_string(), &project.resource_key())
        .map_err(graph_error)?;
    let candidate = revision_candidate(
        canonical_project,
        canonical_asset,
        binding_generation,
        PROVIDER,
        provider_session,
        &version,
        &projection,
        observed_unix_ms,
        graph::Coverage::complete(),
        true,
    )
    .map_err(graph_error)?;

    Ok(json!({
        "schema_version": "motionwright-canonical-graph-candidate/1",
        "authority": "candidate_only",
        "provider": PROVIDER,
        "application_resource": project.resource_key(),
        "application_generation": project.generation.to_string(),
        "application_revision": project.revision.to_string(),
        "locator": locator,
        "candidate": candidate
    }))
}

/// Prepare Semwright's protected Effects specification from canonical JSON input.
///
/// The returned specification contains no verdict and confers no execution authority.
/// Protected readback/admission must still run under the Semwright-owned verification
/// path against immutable admitted artifacts.
pub fn prepare_effect_spec(input: Value) -> NativeResult<Value> {
    let definition: SpecificationInput = serde_json::from_value(input)
        .map_err(|error| value_error("Canonical Effects input is malformed", error))?;
    let spec = prepare_spec(definition)
        .map_err(|error| value_error("Canonical Effects specification was rejected", error))?;
    serde_json::to_value(spec).map_err(|error| {
        value_error(
            "Canonical Effects specification could not be encoded",
            error,
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use semwright_effect_conformance::{
        ObservedValue, Predicate,
        composition::{Digest, ExecutionStatus, Owner, PrincipalBinding},
    };
    use semwright_native_sdk::effects_readback::{
        ArtifactBinding, PropertyCheck, SPEC_SCHEMA, ScalarKind, Selector, SpecificationInput,
    };

    #[test]
    fn graph_candidate_is_untrusted_exact_revision_projection() {
        let project_id = graph::ProjectId::new();
        let asset_id = graph::LogicalAssetId::new();
        let mut first_project = Project::new("Graph fixture").unwrap();
        let first = prepare_project_graph_candidate(
            &first_project,
            &project_id,
            asset_id.clone(),
            1,
            "authenticated-motionwright-session",
            1,
        )
        .unwrap();

        assert_eq!(first["authority"], "candidate_only");
        assert_eq!(first["provider"], PROVIDER);
        assert_eq!(first["application_revision"], "0");

        first_project.title = "Graph fixture revised".into();
        first_project.revision = 1;
        let second = prepare_project_graph_candidate(
            &first_project,
            &project_id,
            asset_id,
            1,
            "authenticated-motionwright-session",
            2,
        )
        .unwrap();

        assert_eq!(second["application_revision"], "1");
        assert_ne!(
            first["candidate"]["fingerprint"],
            second["candidate"]["fingerprint"]
        );
        assert_ne!(
            first["candidate"]["observation"]["id"],
            second["candidate"]["observation"]["id"]
        );
    }

    #[test]
    fn effect_preparation_uses_canonical_spec_without_minting_a_verdict() {
        let bytes = br#"{"duration_frames":120}"#;
        let input = SpecificationInput {
            owner: Owner {
                session: "motionwright-effects-test".into(),
                principal: PrincipalBinding::Named("motionwright-test-owner".into()),
            },
            request_id: "motionwright_effect_one".into(),
            source_digest: Digest::of_bytes(b"motionwright-source"),
            runtime_digest: Digest::of_bytes(b"motionwright-runtime"),
            declared_producer_execution_status: ExecutionStatus::Unknown,
            application_roots: vec!["/tmp/motionwright-application".into()],
            artifacts: vec![ArtifactBinding {
                slot: "manifest".into(),
                path: "artifact.json".into(),
                sha256: Digest::of_bytes(bytes),
                bytes: bytes.len() as u64,
                mime_type: "application/json".into(),
            }],
            checks: vec![PropertyCheck {
                id: "duration_frames".into(),
                artifact_slot: "manifest".into(),
                selector: Selector::Json {
                    pointer: "/duration_frames".into(),
                    scalar: ScalarKind::Number {
                        units: "frame".into(),
                    },
                },
                predicate: Predicate::Equals {
                    expected: ObservedValue::Number {
                        value: 120.0,
                        units: "frame".into(),
                    },
                },
            }],
        };

        let prepared = prepare_effect_spec(serde_json::to_value(input).unwrap()).unwrap();
        assert_eq!(prepared["schema_version"], SPEC_SCHEMA);
        assert!(prepared.get("verdict").is_none());
        assert_eq!(
            prepared["definition"]["request_id"],
            "motionwright_effect_one"
        );
    }
}
