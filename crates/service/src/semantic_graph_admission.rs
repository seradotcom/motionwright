//! Backend-only seam for one admitted Graph source save.
//!
//! This module supplies no admission issuer and makes no runtime/Ready claim.
//! The ordinary StudioService::apply and Graph context remain fail-closed.
use crate::{StorageResult, StudioService};
use motionwright_domain::{
    Change, DomainError, GraphSourceSnapshot, Project, RevisionStamp, SemanticGraph,
};
use motionwright_storage::ApplyOutcome;
use uuid::Uuid;

/// Immutable operation input assembled by the service from the persisted
/// Project and exact source-save change. This is not an authorization DTO.
/// There is no public constructor, Clone, Serialize or Deserialize implementation.
pub struct SemanticGraphWriteScope<'a> {
    current_project: &'a Project,
    request_id: &'a str,
    change: &'a Change,
    graph: &'a SemanticGraph,
}
impl SemanticGraphWriteScope<'_> {
    /// Current application state for authentic Host/actor/project access checks.
    /// Its revision may exceed source_snapshot() on a known durable replay.
    pub fn current_project(&self) -> &Project {
        self.current_project
    }
    pub fn request_id(&self) -> &str {
        self.request_id
    }
    /// The original immutable payload used by Store's own request digest.
    pub fn change(&self) -> &Change {
        self.change
    }
    pub fn graph(&self) -> &SemanticGraph {
        self.graph
    }
    pub fn source_snapshot(&self) -> &GraphSourceSnapshot {
        &self.graph.authorship.source_snapshot
    }
    pub fn scene_id(&self) -> Uuid {
        self.graph.scene_id
    }
    pub fn variant_profile_ref(&self) -> Uuid {
        self.graph.variant_profile_ref
    }
}

/// Trusted Rust backend adapter, never a WebView/JSON capability factory.
///
/// A production implementation must derive current access from a genuine
/// NativeSDK Host invocation and bind the exact scope, including the original
/// request/change, all four snapshot fields, scene and profile. It must retain
/// the actual issuer's operation permit and lifecycle protections in the
/// returned lease through Drop. A cached observation, caller flag, path or
/// serialized readiness value does not satisfy this contract.
///
/// Current Host/actor/project access is required even for a historical reply;
/// source-revision freshness and exact receipt replay belong to Store.apply.
/// Acquisition/checks are synchronous under this service's Store mutex: do not
/// await, call back into StudioService, or acquire another Store mutex here.
/// The native adapter is intentionally not implemented by this module.
pub trait SemanticGraphAdmission {
    fn acquire<'a>(
        &'a self,
        scope: &'a SemanticGraphWriteScope<'a>,
    ) -> StorageResult<Box<dyn SemanticGraphWriteLease + 'a>>;
}

/// Non-serializable operation lease held across the complete Store.apply call.
///
/// The trusted implementation must retain real backend authority for its
/// lifetime. This final check rejects pre-commit revocation; a marker object
/// backed only by an earlier successful check is not an implementation.
pub trait SemanticGraphWriteLease {
    fn check_current(&self) -> StorageResult<()>;
}

impl StudioService {
    /// Save one Graph source through an injected, trusted backend admission.
    /// This does not enable ordinary apply, removal, or authoring-context Ready.
    /// A matching known-success retry returns Store's original durable result;
    /// this method neither recovers nor automatically retries UNKNOWN effects.
    pub fn apply_semantic_graph_with_admission(
        &self,
        request_id: &str,
        change: &Change,
        admission: &dyn SemanticGraphAdmission,
    ) -> StorageResult<ApplyOutcome> {
        let Change::UpsertSemanticGraph { graph } = change else {
            return Err(DomainError::Invalid(
                "The admitted Graph source-save boundary accepts only UpsertSemanticGraph".into(),
            )
            .into());
        };
        let mut store = self.store.lock();
        let source = &graph.authorship.source_snapshot;
        let current_project = store.load_project(source.project_id)?;
        let expected = RevisionStamp {
            resource: current_project.resource_key(),
            generation: source.generation,
            revision: source.revision,
        };
        let scope = SemanticGraphWriteScope {
            current_project: &current_project,
            request_id,
            change,
            graph,
        };
        let lease = admission.acquire(&scope)?;
        lease.check_current()?;
        // Do not require source == current here: Store validates an existing
        // exact receipt before freshness. For a new mutation, domain Graph
        // validation checks all four fields inside Store's SQL transaction.
        let outcome = store.apply(source.project_id, &expected, request_id, change);
        // The operation permit outlives success, replay, conflict and failure.
        drop(lease);
        outcome
    }
}
