//! Backend-only seam for admitted Graph source inspection, save and removal.
//!
//! This module supplies no admission issuer and makes no native runtime claim.
//! The ordinary StudioService::apply and Graph context remain fail-closed.
use crate::semantic_graph::SemanticGraphContextRequest;
use crate::{StorageResult, StudioService};
use motionwright_domain::{
    Change, DomainError, GraphSourceSnapshot, Project, RevisionStamp, SemanticGraph,
};
use motionwright_storage::{ApplyOutcome, Store};
use uuid::Uuid;

/// Immutable operation input assembled by the service from the persisted
/// Project and exact Graph change. This is not an authorization DTO.
/// There is no public constructor, Clone, Serialize or Deserialize implementation.
pub struct SemanticGraphWriteScope<'a> {
    current_project: &'a Project,
    request_id: &'a str,
    change: &'a Change,
    source: &'a GraphSourceSnapshot,
    graph_id: Uuid,
    scene_id: Uuid,
    graph: Option<&'a SemanticGraph>,
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
    /// Exact authored source for Upsert; removal carries no replacement Graph.
    pub fn graph(&self) -> Option<&SemanticGraph> {
        self.graph
    }
    pub fn source_snapshot(&self) -> &GraphSourceSnapshot {
        self.source
    }
    pub fn graph_id(&self) -> Uuid {
        self.graph_id
    }
    pub fn scene_id(&self) -> Uuid {
        self.scene_id
    }
    /// Removal has no authored profile, including on replay after deletion.
    pub fn variant_profile_ref(&self) -> Option<Uuid> {
        self.graph.map(|graph| graph.variant_profile_ref)
    }
}

/// Trusted Rust backend adapter, never a WebView/JSON capability factory.
///
/// A production implementation must derive current access from a genuine
/// NativeSDK Host invocation and bind the exact scope, including the original
/// request/change, all four snapshot fields, Graph identity, scene and any
/// authored profile. It must retain the actual issuer's operation permit and
/// lifecycle protections through lease Drop. A cached observation, caller
/// flag, path or serialized readiness value does not satisfy this contract.
///
/// Current Host/actor/project access is required even for a historical reply;
/// source-revision freshness and exact receipt replay belong to Store.apply.
/// Acquisition/checks are synchronous under this service's Store mutex: do not
/// await, call back into StudioService, or acquire another Store mutex here.
/// The native adapter is intentionally not implemented by this module.
pub trait SemanticGraphAdmission {
    /// Inspect the actual Store while Service holds its existing mutex.
    ///
    /// This optional callback adds no authority. Production Native adapters
    /// must use it to compare the retained connection identity with their
    /// original mounted Store and the caller's independently bound Store.
    /// Do not await, reenter Service, relock its Arc, mutate, or retain this
    /// borrow. The callback ends before acquire() and mutable Store.apply;
    /// the same mutex guard remains held through the complete operation.
    fn inspect_store(&self, _store: &Store) -> StorageResult<()> {
        Ok(())
    }

    /// Inspect source-authoring availability for the exact persisted selection.
    /// Service has checked all snapshot fields, scene/profile membership and
    /// renderer before calling this synchronous method under its Store mutex.
    ///
    /// The real native adapter must check current authenticated owner access,
    /// the canonical Graph consumer and its source/font constraints. Do not
    /// reenter Service or acquire a Store mutex. This observation issues no
    /// write permit: acquire() must independently validate the actual change,
    /// including future labels/fonts, and retain real authority through apply.
    /// Existing adapters deny inspection until they explicitly implement it.
    fn inspect_authoring(
        &self,
        _project: &Project,
        _request: &SemanticGraphContextRequest,
    ) -> StorageResult<()> {
        Err(DomainError::Invalid(
            "Semantic Graph authoring inspection requires the real native admission adapter".into(),
        )
        .into())
    }

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
    /// Save or remove one Graph through an injected, trusted backend admission.
    /// This does not enable ordinary apply or authoring-context Ready.
    /// A matching known-success retry returns Store's original durable result;
    /// this method neither recovers nor automatically retries UNKNOWN effects.
    pub fn apply_semantic_graph_with_admission(
        &self,
        request_id: &str,
        change: &Change,
        admission: &dyn SemanticGraphAdmission,
    ) -> StorageResult<ApplyOutcome> {
        let (source, graph_id, scene_id, graph) = match change {
            Change::UpsertSemanticGraph { graph } => (
                &graph.authorship.source_snapshot,
                graph.id,
                graph.scene_id,
                Some(graph.as_ref()),
            ),
            Change::RemoveSemanticGraph {
                graph_id,
                scene_id,
                source_snapshot,
            } => (source_snapshot, *graph_id, *scene_id, None),
            _ => {
                return Err(DomainError::Invalid(
                    "The admitted Graph boundary accepts only UpsertSemanticGraph or RemoveSemanticGraph"
                        .into(),
                )
                .into());
            }
        };
        let mut store = self.store.lock();
        admission.inspect_store(&store)?;
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
            source,
            graph_id,
            scene_id,
            graph,
        };
        let lease = admission.acquire(&scope)?;
        lease.check_current()?;
        // Do not require source == current here: Store validates an existing
        // exact receipt before freshness. For a new mutation, domain Graph
        // validation checks all four fields inside Store's SQL transaction.
        // A removal replay must reach that receipt check even when its Graph
        // is already absent; target existence and locks also belong to Store.
        let outcome = store.apply(source.project_id, &expected, request_id, change);
        // The operation permit outlives success, replay, conflict and failure.
        drop(lease);
        outcome
    }
}
