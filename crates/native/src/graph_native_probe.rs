//! Native Graph font observations from this provider's real Host invocation.
//! Private wrappers retain original tool output; serialized probe data cannot
//! construct an admission, grant, Store binding or operation permit.
use crate::{Error, ErrorCode, NativeResult};
use motionwright_domain::{Change, GraphSourceSnapshot, Project};
use motionwright_manim_profile::semantic_graph_text_probe::{
    GraphTextProbePurposeV1, GraphTextProbeRequestV1, ManimGraphTextProbeObservationV1,
    graph_authoring_context_probe_requests_v1, graph_text_probe_request_v1,
    parse_graph_text_probe_observation_v1,
};
use motionwright_service::SemanticGraphContextRequest;
use semwright_native_sdk::{
    cooperation::CallContext,
    driver_sdk::{RuntimeToolArg, ToolExecutionOutput, workspace_mount},
};
use sha2::{Digest, Sha256};

const PROBE_TOOL: &str = "manim-runner";
const PROBE_MOUNTS: [&str; 3] = ["manim-runtime", "manim-work", "fontconfig"];

/// Presence only determines which interface the mounted provider negotiates.
/// Actual access, executable pins and delegated tools are checked by Host for
/// every invocation. Missing mounts never produce a positive observation.
pub(crate) fn owner_probe_mounts_present() -> bool {
    PROBE_MOUNTS
        .iter()
        .all(|name| workspace_mount(name).is_ok())
}

fn invalid(message: &'static str) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn unsupported(message: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

fn closed_arguments() -> Vec<RuntimeToolArg> {
    let literal = |value: &str| RuntimeToolArg::Literal {
        value: value.into(),
    };
    let mount = |value: &str| RuntimeToolArg::MountPath {
        mount: value.into(),
        relative: String::new(),
    };
    vec![
        literal("probe-graph-text"),
        literal("--runtime-root"),
        mount("manim-runtime"),
        literal("--python-sealed"),
        RuntimeToolArg::ToolPath {
            tool: "python3".into(),
        },
        literal("--ffmpeg-sealed"),
        RuntimeToolArg::ToolPath {
            tool: "ffmpeg".into(),
        },
        literal("--fontconfig-root"),
        mount("fontconfig"),
        literal("--work-root"),
        mount("manim-work"),
    ]
}

fn binding_digest<T: serde::Serialize>(value: &T) -> NativeResult<String> {
    let bytes = serde_json::to_vec(value)
        .map_err(|_| invalid("Graph native source binding could not be encoded"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

struct ObservedProbe {
    request: GraphTextProbeRequestV1,
    observation: ManimGraphTextProbeObservationV1,
    // The same original Host output is retained across the synchronous Store
    // boundary. The runner separately preserves stdin and original streams.
    original: ToolExecutionOutput,
}

impl ObservedProbe {
    fn check(&self, context: &CallContext) -> NativeResult<()> {
        context.driver_context()?;
        context.check_cancelled()?;
        if self.observation.host_request_id != context.request_id() || self.original.exit_code != 0
        {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Graph font observation belongs to another native invocation",
            ));
        }
        parse_graph_text_probe_observation_v1(&self.original.stdout, &self.request)
            .map(|_| ())
            .map_err(|_| unsupported("Retained original Graph font observation is invalid"))
    }
}

async fn observe_probe(
    context: &CallContext,
    request: GraphTextProbeRequestV1,
) -> NativeResult<ObservedProbe> {
    let host = context.driver_context()?;
    context.check_cancelled()?;
    if host.native_target().is_none() || context.expected().is_none() {
        return Err(Error::new(
            ErrorCode::PermissionDenied,
            "Graph font inspection requires an actual observed native resource",
        ));
    }
    if !owner_probe_mounts_present() {
        return Err(unsupported(
            "Graph native runtime and fonts are not mounted",
        ));
    }
    let stdin = request
        .encode()
        .map_err(|_| unsupported("Graph text exceeds the closed native probe profile"))?;
    let timeout = request.host_timeout();
    // Only this real invocation can ask Host to resolve the sealed tool and
    // delegated mounts. Text remains bounded stdin, never executable source.
    let original = host
        .execute_runtime_tool_args(PROBE_TOOL, closed_arguments(), stdin, timeout, None)
        .await
        .map_err(Error::uncertain)?;
    original.validate()?;
    if original.exit_code != 0 {
        return Err(Error::new(
            ErrorCode::BackendFailed,
            "The original Graph native font probe did not complete successfully",
        ));
    }
    let observation = parse_graph_text_probe_observation_v1(&original.stdout, &request)
        .map_err(|_| unsupported("The original Graph native font observation was rejected"))?;
    let observed = ObservedProbe {
        request,
        observation,
        original,
    };
    observed.check(context)?;
    Ok(observed)
}

pub(crate) struct AuthoringFonts {
    original_project: Project,
    original_request: SemanticGraphContextRequest,
    binding: String,
    probes: [ObservedProbe; 2],
}

impl AuthoringFonts {
    pub(crate) fn check(
        &self,
        context: &CallContext,
        project: &Project,
        request: &SemanticGraphContextRequest,
    ) -> NativeResult<()> {
        if project != &self.original_project
            || request != &self.original_request
            || binding_digest(&(
                "motionwright.graph-authoring-font-source/1",
                project,
                request,
            ))? != self.binding
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Graph authoring source changed during native inspection",
            ));
        }
        for probe in &self.probes {
            probe.check(context)?;
        }
        Ok(())
    }
}

pub(crate) async fn observe_authoring_fonts(
    context: &CallContext,
    project: &Project,
    request: &SemanticGraphContextRequest,
) -> NativeResult<AuthoringFonts> {
    request
        .source_snapshot
        .require_current(project)
        .map_err(|_| Error::new(ErrorCode::StaleReference, "Graph authoring source is stale"))?;
    crate::manim_graph_projection::validate_manim_semantic_graph_scene_output(
        project,
        &request.source_snapshot,
        request.scene_id,
        request.variant_profile_ref,
    )?;
    let binding = binding_digest(&(
        "motionwright.graph-authoring-font-source/1",
        project,
        request,
    ))?;
    let [primary, mono] = graph_authoring_context_probe_requests_v1(&binding, context.request_id())
        .map_err(|_| unsupported("Graph authoring font controls could not be prepared"))?;
    let primary = observe_probe(context, primary).await?;
    let mono = observe_probe(context, mono).await?;
    Ok(AuthoringFonts {
        original_project: project.clone(),
        original_request: request.clone(),
        binding,
        probes: [primary, mono],
    })
}

pub(crate) struct ProposedFonts {
    original_project: Project,
    change_binding: String,
    probe: ObservedProbe,
}

impl ProposedFonts {
    pub(crate) fn check(
        &self,
        context: &CallContext,
        project: &Project,
        change: &Change,
    ) -> NativeResult<()> {
        if project != &self.original_project || binding_digest(change)? != self.change_binding {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Graph proposal changed after its actual native font observation",
            ));
        }
        self.probe.check(context)
    }
}

pub(crate) async fn observe_proposed_fonts(
    context: &CallContext,
    project: &Project,
    change: &Change,
) -> NativeResult<ProposedFonts> {
    crate::manim_graph_projection::validate_proposed_manim_semantic_graph_source(project, change)?;
    let Change::UpsertSemanticGraph { graph } = change else {
        return Err(invalid(
            "Native Graph text inspection requires an exact source proposal",
        ));
    };
    let mut candidate = project.clone();
    candidate
        .apply_change(change)
        .map_err(|_| unsupported("Proposed Graph violates its canonical domain"))?;
    let stamp = GraphSourceSnapshot::from_project(&candidate);
    let plan = crate::manim_graph_projection::build_manim_semantic_graph_source_plan(
        &candidate,
        &stamp,
        graph.id,
        graph.variant_profile_ref,
    )?;
    let request =
        graph_text_probe_request_v1(&plan, context.request_id(), GraphTextProbePurposeV1::Upsert)
            .map_err(|_| unsupported("Proposed Graph exceeds the bounded native text profile"))?;
    let probe = observe_probe(context, request).await?;
    Ok(ProposedFonts {
        original_project: project.clone(),
        change_binding: binding_digest(change)?,
        probe,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn probe_arguments_delegate_only_closed_runtime_roots_and_sealed_tools() {
        let args = closed_arguments();
        semwright_native_sdk::driver_sdk::validate_runtime_tool_args(&args).unwrap();
        assert_eq!(args.len(), 11);
        let mounts = args
            .iter()
            .filter_map(|arg| match arg {
                RuntimeToolArg::MountPath { mount, relative } => {
                    assert!(relative.is_empty());
                    Some(mount.as_str())
                }
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(mounts, ["manim-runtime", "fontconfig", "manim-work"]);
        assert!(!mounts.contains(&"motionwright-data"));
    }

    #[test]
    fn authoring_font_binding_detects_body_change_under_equal_source_stamp() {
        let project = crate::semantic_graph_source_fixture::seed_domain();
        let request = SemanticGraphContextRequest {
            source_snapshot: GraphSourceSnapshot::from_project(&project),
            scene_id: project.scenes[0].id,
            variant_profile_ref: project.deliverables[0].id,
        };
        let original = binding_digest(&(
            "motionwright.graph-authoring-font-source/1",
            &project,
            &request,
        ))
        .unwrap();
        let mut changed = project.clone();
        changed.title = "Another persisted project body".into();
        assert_eq!(
            GraphSourceSnapshot::from_project(&changed),
            request.source_snapshot
        );
        assert_ne!(
            original,
            binding_digest(&(
                "motionwright.graph-authoring-font-source/1",
                &changed,
                &request
            ))
            .unwrap()
        );
    }
}
