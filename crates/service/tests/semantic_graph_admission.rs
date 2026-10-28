//! Real service/SQLite seam tests with a Rust-only admission test double.
//! No test here claims a genuine Host issuer, native fonts, or runtime Ready.
use motionwright_domain::*;
use motionwright_service::{
    SemanticGraphAdmission, SemanticGraphContextRequest, SemanticGraphWriteLease,
    SemanticGraphWriteScope, StudioService,
};
use motionwright_storage::{Result as StorageResult, StorageError, Store};
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
use uuid::Uuid;
#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod source;

#[derive(Clone, Copy)]
enum Mode {
    Admit,
    Reject,
    RevokeAfterAcquire,
}
#[derive(Debug, Clone)]
struct SeenScope {
    current: Project,
    request_id: String,
    change: Change,
    source: GraphSourceSnapshot,
    scene_id: Uuid,
    profile_id: Uuid,
}
#[derive(Debug, PartialEq)]
struct DroppedLease {
    request_id: String,
    revision: u64,
    events: usize,
}
struct TestAdmission {
    mode: Mode,
    path: PathBuf,
    project_id: Uuid,
    store: Arc<parking_lot::Mutex<Store>>,
    revoked: AtomicBool,
    active: AtomicUsize,
    checks: AtomicUsize,
    seen: Mutex<Vec<SeenScope>>,
    dropped: Mutex<Vec<DroppedLease>>,
}
fn denied() -> StorageError {
    DomainError::Invalid("test Host admission rejected or revoked".into()).into()
}
impl TestAdmission {
    fn new(service: &StudioService, path: &Path, project: &Project, mode: Mode) -> Self {
        Self {
            mode,
            path: path.to_owned(),
            project_id: project.id,
            store: service.store(),
            revoked: AtomicBool::new(false),
            active: AtomicUsize::new(0),
            checks: AtomicUsize::new(0),
            seen: Mutex::new(Vec::new()),
            dropped: Mutex::new(Vec::new()),
        }
    }
}
struct TestLease<'a> {
    owner: &'a TestAdmission,
    scope: &'a SemanticGraphWriteScope<'a>,
}
impl SemanticGraphAdmission for TestAdmission {
    fn acquire<'a>(
        &'a self,
        scope: &'a SemanticGraphWriteScope<'a>,
    ) -> StorageResult<Box<dyn SemanticGraphWriteLease + 'a>> {
        assert!(
            self.store.try_lock().is_none(),
            "same Store must stay locked"
        );
        if scope.current_project().id != self.project_id {
            return Err(denied());
        }
        assert_eq!(
            scope.current_project().id,
            scope.source_snapshot().project_id
        );
        assert!(matches!(scope.change(), Change::UpsertSemanticGraph { .. }));
        assert_eq!(scope.graph().scene_id, scope.scene_id());
        assert_eq!(
            scope.graph().variant_profile_ref,
            scope.variant_profile_ref()
        );
        self.seen.lock().unwrap().push(SeenScope {
            current: scope.current_project().clone(),
            request_id: scope.request_id().to_owned(),
            change: scope.change().clone(),
            source: scope.source_snapshot().clone(),
            scene_id: scope.scene_id(),
            profile_id: scope.variant_profile_ref(),
        });
        if matches!(self.mode, Mode::Reject) {
            return Err(denied());
        }
        assert_eq!(self.active.fetch_add(1, Ordering::SeqCst), 0);
        if matches!(self.mode, Mode::RevokeAfterAcquire) {
            self.revoked.store(true, Ordering::SeqCst);
        }
        Ok(Box::new(TestLease { owner: self, scope }))
    }
}
impl SemanticGraphWriteLease for TestLease<'_> {
    fn check_current(&self) -> StorageResult<()> {
        assert!(self.owner.store.try_lock().is_none());
        assert_eq!(self.owner.active.load(Ordering::SeqCst), 1);
        self.owner.checks.fetch_add(1, Ordering::SeqCst);
        if self.owner.revoked.load(Ordering::SeqCst) {
            return Err(denied());
        }
        Ok(())
    }
}
impl Drop for TestLease<'_> {
    fn drop(&mut self) {
        assert!(
            self.owner.store.try_lock().is_none(),
            "Store outlives lease"
        );
        assert_eq!(self.owner.active.fetch_sub(1, Ordering::SeqCst), 1);
        let connection = rusqlite::Connection::open_with_flags(
            &self.owner.path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        let id = self.owner.project_id.to_string();
        let revision: u64 = connection
            .query_row("SELECT revision FROM projects WHERE id=?1", [&id], |row| {
                row.get(0)
            })
            .unwrap();
        let events: usize = connection
            .query_row(
                "SELECT COUNT(*) FROM events WHERE project_id=?1",
                [&id],
                |row| row.get(0),
            )
            .unwrap();
        self.owner.dropped.lock().unwrap().push(DroppedLease {
            request_id: self.scope.request_id().to_owned(),
            revision,
            events,
        });
    }
}
fn seed(service: &StudioService) -> Project {
    let project = service.create_project("Graph admission seam").unwrap();
    let project = service
        .apply(
            project.id,
            &project.stamp(),
            "seed-scene",
            &Change::AddScene {
                name: "Explanatory relationships".into(),
                objective: "Admitted authored source".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    service
        .apply(
            project.id,
            &project.stamp(),
            "seed-renderer",
            &Change::SetSceneRenderer {
                scene_id: project.scenes[0].id,
                renderer: RendererKind::ManimCommunity,
            },
        )
        .unwrap()
        .project
}
fn upsert(project: &Project) -> Change {
    Change::UpsertSemanticGraph {
        graph: Box::new(source::graph_for(project, false)),
    }
}
fn unchanged(
    service: &StudioService,
    before: &Project,
    events: &[motionwright_storage::ProjectEvent],
) {
    assert_eq!(service.project(before.id).unwrap(), *before);
    assert_eq!(service.history(before.id, 0, 1024).unwrap(), events);
}

#[test]
fn exact_scope_and_lease_survive_one_durable_commit_without_enabling_ready() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-admission.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let prior_events = service.history(before.id, 0, 1024).unwrap();
    let change = upsert(&before);
    let Change::UpsertSemanticGraph { graph } = &change else {
        unreachable!()
    };
    let admission = TestAdmission::new(&service, &path, &before, Mode::Admit);
    // This fixture-only switch invokes the unchanged former service route.
    // It is not compiled into a product permission or readiness mechanism.
    let legacy = std::env::var_os("MOTIONWRIGHT_A2_GRAPH_ADMISSION_LEGACY_ROUTE").is_some();
    let result = if legacy {
        service.apply(before.id, &before.stamp(), "admitted-source-save", &change)
    } else {
        service.apply_semantic_graph_with_admission("admitted-source-save", &change, &admission)
    };
    if legacy {
        unchanged(&service, &before, &prior_events);
        assert!(admission.seen.lock().unwrap().is_empty());
        eprintln!(
            "A2_LEGACY_ROUTE_REFUSED_WITH_ZERO_WRITES={}",
            result.is_err()
        );
    }
    let outcome = result.unwrap();
    assert!(!outcome.replayed);
    assert_eq!(outcome.previous_revision, before.revision);
    assert_eq!(outcome.project.revision, before.revision + 1);
    assert_eq!(
        outcome.project.production_design.semantic_graphs,
        vec![(**graph).clone()]
    );
    assert_eq!(service.project(before.id).unwrap(), outcome.project);
    let events = service.history(before.id, before.revision, 16).unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].change, change);
    assert_eq!(admission.active.load(Ordering::SeqCst), 0);
    assert_eq!(admission.checks.load(Ordering::SeqCst), 1);
    let seen = admission.seen.lock().unwrap();
    assert_eq!(seen.len(), 1);
    assert_eq!(seen[0].current, before);
    assert_eq!(seen[0].request_id, "admitted-source-save");
    assert_eq!(seen[0].change, change);
    assert_eq!(seen[0].source, GraphSourceSnapshot::from_project(&before));
    assert_eq!(seen[0].scene_id, graph.scene_id);
    assert_eq!(seen[0].profile_id, graph.variant_profile_ref);
    drop(seen);
    assert_eq!(
        *admission.dropped.lock().unwrap(),
        vec![DroppedLease {
            request_id: "admitted-source-save".into(),
            revision: outcome.project.revision,
            events: prior_events.len() + 1,
        }]
    );
    let context = service
        .semantic_graph_authoring_context(&SemanticGraphContextRequest {
            source_snapshot: GraphSourceSnapshot::from_project(&outcome.project),
            scene_id: graph.scene_id,
            variant_profile_ref: graph.variant_profile_ref,
        })
        .unwrap();
    assert_eq!(context.availability, GraphAvailability::Blocked);
    assert!(
        context
            .blocking_reasons
            .contains(&GraphBlockReason::GraphWriteUnavailable)
    );
}

#[test]
fn known_success_replays_after_head_advance_and_reopen_but_changed_payload_does_not() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-known-replay.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let change = upsert(&before);
    let admission = TestAdmission::new(&service, &path, &before, Mode::Admit);
    let first = service
        .apply_semantic_graph_with_admission("known-graph-source", &change, &admission)
        .unwrap();
    let latest = service
        .apply(
            before.id,
            &first.project.stamp(),
            "later-title",
            &Change::RenameProject {
                title: "Later current project".into(),
            },
        )
        .unwrap()
        .project;
    let events = service.history(before.id, 0, 1024).unwrap();
    drop(admission);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    let current_access = TestAdmission::new(&reopened, &path, &latest, Mode::Admit);
    let replay = reopened
        .apply_semantic_graph_with_admission("known-graph-source", &change, &current_access)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(replay.previous_revision, first.previous_revision);
    assert_eq!(replay.request_id, first.request_id);
    unchanged(&reopened, &latest, &events);
    let seen = current_access.seen.lock().unwrap();
    assert_eq!(seen[0].current, latest);
    assert_eq!(seen[0].source, GraphSourceSnapshot::from_project(&before));
    drop(seen);
    let mut altered = change.clone();
    let Change::UpsertSemanticGraph { graph } = &mut altered else {
        unreachable!()
    };
    graph.authorship.rationale = "Different authored input for the same request ID".into();
    let conflict = reopened.apply_semantic_graph_with_admission(
        "known-graph-source",
        &altered,
        &current_access,
    );
    assert!(matches!(conflict, Err(StorageError::RequestReuse)));
    unchanged(&reopened, &latest, &events);
    assert_eq!(current_access.active.load(Ordering::SeqCst), 0);
    assert_eq!(current_access.checks.load(Ordering::SeqCst), 2);
    assert!(
        current_access
            .dropped
            .lock()
            .unwrap()
            .iter()
            .all(|drop| { drop.revision == latest.revision && drop.events == events.len() })
    );
    let revoked_access = TestAdmission::new(&reopened, &path, &latest, Mode::Reject);
    assert!(
        reopened
            .apply_semantic_graph_with_admission("known-graph-source", &change, &revoked_access,)
            .is_err()
    );
    unchanged(&reopened, &latest, &events);
}

#[test]
fn uncommitted_mismatches_in_all_four_source_fields_leave_no_writes() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-stale-source.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    for field in ["project", "generation", "branch", "revision"] {
        let mut graph = source::graph_for(&before, false);
        let snapshot = &mut graph.authorship.source_snapshot;
        match field {
            "project" => snapshot.project_id = Uuid::new_v4(),
            "generation" => snapshot.generation = Uuid::new_v4(),
            "branch" => snapshot.branch_id = Uuid::new_v4(),
            "revision" => snapshot.revision -= 1,
            _ => unreachable!(),
        }
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        };
        let admission = TestAdmission::new(&service, &path, &before, Mode::Admit);
        let result = service.apply_semantic_graph_with_admission(
            &format!("uncommitted-{field}"),
            &change,
            &admission,
        );
        match field {
            "project" => assert!(matches!(result, Err(StorageError::NotFound))),
            "generation" => assert!(matches!(result, Err(StorageError::GenerationConflict))),
            "revision" => assert!(matches!(result, Err(StorageError::Conflict { .. }))),
            "branch" => assert!(matches!(result, Err(StorageError::Domain(_)))),
            _ => unreachable!(),
        }
        unchanged(&service, &before, &events);
        assert_eq!(admission.active.load(Ordering::SeqCst), 0);
        assert!(
            admission
                .dropped
                .lock()
                .unwrap()
                .iter()
                .all(|drop| { drop.revision == before.revision && drop.events == events.len() })
        );
    }
}

#[test]
fn rejected_acquisition_and_revocation_before_commit_leave_no_writes() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-revoked.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let change = upsert(&before);
    for mode in [Mode::Reject, Mode::RevokeAfterAcquire] {
        let admission = TestAdmission::new(&service, &path, &before, mode);
        assert!(
            service
                .apply_semantic_graph_with_admission(
                    "known-uncommitted-rejection",
                    &change,
                    &admission,
                )
                .is_err()
        );
        unchanged(&service, &before, &events);
        assert_eq!(admission.active.load(Ordering::SeqCst), 0);
        let acquired = usize::from(matches!(mode, Mode::RevokeAfterAcquire));
        assert_eq!(admission.checks.load(Ordering::SeqCst), acquired);
        assert_eq!(admission.dropped.lock().unwrap().len(), acquired);
    }
}

#[test]
fn invalid_graph_still_uses_domain_validation_under_the_held_lease() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-invalid.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let mut graph = source::graph_for(&before, false);
    graph.component_version = u8::MAX;
    let change = Change::UpsertSemanticGraph {
        graph: Box::new(graph),
    };
    let admission = TestAdmission::new(&service, &path, &before, Mode::Admit);
    assert!(matches!(
        service.apply_semantic_graph_with_admission("invalid-graph-shape", &change, &admission,),
        Err(StorageError::Domain(_))
    ));
    unchanged(&service, &before, &events);
    assert_eq!(admission.checks.load(Ordering::SeqCst), 1);
    assert_eq!(
        *admission.dropped.lock().unwrap(),
        vec![DroppedLease {
            request_id: "invalid-graph-shape".into(),
            revision: before.revision,
            events: events.len(),
        }]
    );
}

#[test]
fn ordinary_apply_removal_and_other_changes_cannot_enter_this_seam() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-closed-routes.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let change = upsert(&before);
    assert!(
        service
            .apply(before.id, &before.stamp(), "ordinary-graph", &change)
            .is_err()
    );
    let Change::UpsertSemanticGraph { graph } = &change else {
        unreachable!()
    };
    let removal = Change::RemoveSemanticGraph {
        graph_id: graph.id,
        scene_id: graph.scene_id,
        source_snapshot: GraphSourceSnapshot::from_project(&before),
    };
    let admission = TestAdmission::new(&service, &path, &before, Mode::Admit);
    for other in [
        removal.clone(),
        Change::RenameProject {
            title: "Not a Graph source save".into(),
        },
    ] {
        assert!(
            service
                .apply_semantic_graph_with_admission("wrong-change-variant", &other, &admission,)
                .is_err()
        );
    }
    assert!(
        service
            .apply(before.id, &before.stamp(), "ordinary-removal", &removal)
            .is_err()
    );
    assert!(admission.seen.lock().unwrap().is_empty());
    assert_eq!(admission.checks.load(Ordering::SeqCst), 0);
    unchanged(&service, &before, &events);
}

// These inspection adapters exercise the Service contract with real SQLite.
// They are unit-test doubles, not deployed Host identity or native/font proof.
// The production adapter and its installed route require separate evidence.
#[derive(Clone, Copy)]
enum InspectionMode {
    Admit,
    Reject,
    EditThroughIndependentStore,
}

struct TestAuthoringAdmission {
    writer: TestAdmission,
    project: Project,
    request: SemanticGraphContextRequest,
    mode: InspectionMode,
    inspections: AtomicUsize,
}

fn authoring_request(project: &Project) -> SemanticGraphContextRequest {
    SemanticGraphContextRequest {
        source_snapshot: GraphSourceSnapshot::from_project(project),
        scene_id: project.scenes[0].id,
        variant_profile_ref: project.deliverables[0].id,
    }
}

impl TestAuthoringAdmission {
    fn new(
        service: &StudioService,
        path: &Path,
        project: &Project,
        writer_mode: Mode,
        mode: InspectionMode,
    ) -> Self {
        Self {
            writer: TestAdmission::new(service, path, project, writer_mode),
            project: project.clone(),
            request: authoring_request(project),
            mode,
            inspections: AtomicUsize::new(0),
        }
    }
}

impl SemanticGraphAdmission for TestAuthoringAdmission {
    fn inspect_authoring(
        &self,
        project: &Project,
        request: &SemanticGraphContextRequest,
    ) -> StorageResult<()> {
        assert!(self.writer.store.try_lock().is_none());
        assert_eq!(project, &self.project);
        assert_eq!(request, &self.request);
        self.inspections.fetch_add(1, Ordering::SeqCst);
        if matches!(self.mode, InspectionMode::Reject) || self.writer.revoked.load(Ordering::SeqCst)
        {
            return Err(denied());
        }
        if matches!(self.mode, InspectionMode::EditThroughIndependentStore) {
            // Model an independent writer during the observation. This uses
            // another SQLite connection, never reenters the locked Service,
            // and preserves the independent writer's real event and receipt.
            let mut independent = Store::open(&self.writer.path)?;
            let outcome = independent.apply(
                project.id,
                &project.stamp(),
                "independent-edit-during-graph-inspection",
                &Change::RenameProject {
                    title: "Independent edit during Graph inspection".into(),
                },
            )?;
            assert_eq!(outcome.project.revision, project.revision + 1);
        }
        Ok(())
    }

    fn acquire<'a>(
        &'a self,
        scope: &'a SemanticGraphWriteScope<'a>,
    ) -> StorageResult<Box<dyn SemanticGraphWriteLease + 'a>> {
        self.writer.acquire(scope)
    }
}

#[test]
fn existing_write_adapter_denies_authoring_inspection_by_default_without_writes() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-default-inspection-denial.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let request = authoring_request(&before);
    let admission = TestAdmission::new(&service, &path, &before, Mode::Admit);
    let error = service
        .semantic_graph_authoring_context_with_admission(&request, &admission)
        .unwrap_err();
    assert!(error.to_string().contains("real native admission adapter"));
    assert!(admission.seen.lock().unwrap().is_empty());
    assert_eq!(admission.active.load(Ordering::SeqCst), 0);
    assert_eq!(admission.checks.load(Ordering::SeqCst), 0);
    assert_eq!(
        service
            .semantic_graph_authoring_context(&request)
            .unwrap()
            .availability,
        GraphAvailability::Blocked
    );
    unchanged(&service, &before, &events);
}

#[test]
fn admitted_authoring_context_preserves_exact_eleven_fields_without_acquiring_write_lease() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-admitted-context-shape.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let request = authoring_request(&before);
    let admission =
        TestAuthoringAdmission::new(&service, &path, &before, Mode::Admit, InspectionMode::Admit);
    let ordinary = service.semantic_graph_authoring_context(&request).unwrap();
    let admitted = service
        .semantic_graph_authoring_context_with_admission(&request, &admission)
        .unwrap();
    assert_eq!(admitted.availability, GraphAvailability::Ready);
    assert!(admitted.blocking_reasons.is_empty());
    let wire = serde_json::to_value(&admitted).unwrap();
    let expected_fields: std::collections::BTreeSet<&str> = [
        "schema",
        "source_snapshot",
        "scene_id",
        "variant_profile_ref",
        "component_version",
        "native_profile",
        "font_weight",
        "font_selection",
        "availability",
        "blocking_reasons",
        "dependencies",
    ]
    .into_iter()
    .collect();
    assert_eq!(
        wire.as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect::<std::collections::BTreeSet<_>>(),
        expected_fields
    );
    let mut expected = serde_json::to_value(&ordinary).unwrap();
    expected["availability"] = serde_json::json!("ready");
    expected["blocking_reasons"] = serde_json::json!([]);
    assert_eq!(wire, expected);
    assert_eq!(
        admitted.schema,
        "motionwright.semantic-graph-authoring-context/1"
    );
    assert_eq!(admitted.component_version, SEMANTIC_GRAPH_COMPONENT_VERSION);
    assert_eq!(admitted.native_profile, SEMANTIC_GRAPH_PROFILE);
    assert_eq!(admitted.font_weight, 400);
    assert_eq!(
        admitted.font_selection.primary_family,
        "Instrument Sans Variable"
    );
    assert_eq!(admitted.font_selection.mono_family, "IBM Plex Mono");
    assert_eq!(admission.inspections.load(Ordering::SeqCst), 1);
    assert!(admission.writer.seen.lock().unwrap().is_empty());
    assert_eq!(admission.writer.active.load(Ordering::SeqCst), 0);
    assert_eq!(admission.writer.checks.load(Ordering::SeqCst), 0);
    unchanged(&service, &before, &events);
}

#[test]
fn admitted_context_rejects_foreign_or_stale_source_and_selection_before_inspection() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-admitted-context-stale.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let original = authoring_request(&before);
    let admission =
        TestAuthoringAdmission::new(&service, &path, &before, Mode::Admit, InspectionMode::Admit);
    for field in [
        "project",
        "generation",
        "branch",
        "revision",
        "scene",
        "profile",
    ] {
        let mut request = original.clone();
        match field {
            "project" => request.source_snapshot.project_id = Uuid::new_v4(),
            "generation" => request.source_snapshot.generation = Uuid::new_v4(),
            "branch" => request.source_snapshot.branch_id = Uuid::new_v4(),
            "revision" => request.source_snapshot.revision += 1,
            "scene" => request.scene_id = Uuid::new_v4(),
            "profile" => request.variant_profile_ref = Uuid::new_v4(),
            _ => unreachable!(),
        }
        assert!(
            service
                .semantic_graph_authoring_context_with_admission(&request, &admission)
                .is_err(),
            "mismatched {field} must not reach the native adapter"
        );
        assert_eq!(admission.inspections.load(Ordering::SeqCst), 0);
        unchanged(&service, &before, &events);
    }
}

#[test]
fn incompatible_renderer_stays_blocked_without_inspecting_native_authority() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-context-incompatible-renderer.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let created = service
        .create_project("Incompatible Graph renderer")
        .unwrap();
    let before = service
        .apply(
            created.id,
            &created.stamp(),
            "seed-default-renderer-scene",
            &Change::AddScene {
                name: "Default renderer scene".into(),
                objective: "Keep unsupported source blocked".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    assert_ne!(before.scenes[0].renderer, RendererKind::ManimCommunity);
    let events = service.history(before.id, 0, 1024).unwrap();
    let admission =
        TestAuthoringAdmission::new(&service, &path, &before, Mode::Admit, InspectionMode::Admit);
    let context = service
        .semantic_graph_authoring_context_with_admission(&authoring_request(&before), &admission)
        .unwrap();
    assert_eq!(context.availability, GraphAvailability::Blocked);
    assert_eq!(
        context.blocking_reasons,
        vec![GraphBlockReason::ProfileUnsupported]
    );
    assert_eq!(admission.inspections.load(Ordering::SeqCst), 0);
    unchanged(&service, &before, &events);
}

#[test]
fn deliverable_excluding_the_scene_stays_blocked_without_native_inspection() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-context-excluded-scene.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let seeded = seed(&service);
    let with_other_scene = service
        .apply(
            seeded.id,
            &seeded.stamp(),
            "add-other-profile-scene",
            &Change::AddScene {
                name: "Different selected scene".into(),
                objective: "Limit the delivery profile to another scene".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let mut profile = with_other_scene.deliverables[0].clone();
    profile.included_scene_ids = vec![with_other_scene.scenes[1].id];
    let before = service
        .apply(
            with_other_scene.id,
            &with_other_scene.stamp(),
            "exclude-graph-scene-from-profile",
            &Change::UpsertDeliverable { profile },
        )
        .unwrap()
        .project;
    assert_eq!(before.scenes[0].renderer, RendererKind::ManimCommunity);
    assert_eq!(
        before.deliverables[0].included_scene_ids,
        vec![before.scenes[1].id]
    );
    let events = service.history(before.id, 0, 1024).unwrap();
    let admission =
        TestAuthoringAdmission::new(&service, &path, &before, Mode::Admit, InspectionMode::Admit);
    let context = service
        .semantic_graph_authoring_context_with_admission(&authoring_request(&before), &admission)
        .unwrap();
    assert_eq!(context.availability, GraphAvailability::Blocked);
    assert_eq!(
        context.blocking_reasons,
        vec![GraphBlockReason::ProfileUnsupported]
    );
    assert_eq!(admission.inspections.load(Ordering::SeqCst), 0);
    unchanged(&service, &before, &events);
}

#[test]
fn rejected_inspection_cannot_return_ready_or_create_an_event() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-context-inspection-rejected.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let admission = TestAuthoringAdmission::new(
        &service,
        &path,
        &before,
        Mode::Admit,
        InspectionMode::Reject,
    );
    assert!(
        service
            .semantic_graph_authoring_context_with_admission(
                &authoring_request(&before),
                &admission
            )
            .is_err()
    );
    assert_eq!(admission.inspections.load(Ordering::SeqCst), 1);
    assert!(admission.writer.seen.lock().unwrap().is_empty());
    unchanged(&service, &before, &events);
}

#[test]
fn independent_store_change_during_inspection_is_preserved_and_prevents_ready() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-context-independent-writer.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let admission = TestAuthoringAdmission::new(
        &service,
        &path,
        &before,
        Mode::Admit,
        InspectionMode::EditThroughIndependentStore,
    );
    let error = service
        .semantic_graph_authoring_context_with_admission(&authoring_request(&before), &admission)
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("Graph source changed during authoring inspection")
    );
    assert_eq!(admission.inspections.load(Ordering::SeqCst), 1);
    assert!(admission.writer.seen.lock().unwrap().is_empty());
    let current = service.project(before.id).unwrap();
    assert_eq!(current.revision, before.revision + 1);
    assert_eq!(current.title, "Independent edit during Graph inspection");
    let latest_events = service.history(before.id, 0, 1024).unwrap();
    assert_eq!(latest_events.len(), events.len() + 1);
    assert_eq!(&latest_events[..events.len()], events.as_slice());
    assert_eq!(
        latest_events.last().unwrap().change,
        Change::RenameProject {
            title: "Independent edit during Graph inspection".into(),
        }
    );
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(before.id).unwrap(), current);
    assert_eq!(reopened.history(before.id, 0, 1024).unwrap(), latest_events);
}

#[test]
fn ready_context_does_not_authorize_ordinary_or_denied_source_save() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-context-is-not-permission.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let admission = TestAuthoringAdmission::new(
        &service,
        &path,
        &before,
        Mode::Reject,
        InspectionMode::Admit,
    );
    let request = authoring_request(&before);
    let context = service
        .semantic_graph_authoring_context_with_admission(&request, &admission)
        .unwrap();
    assert_eq!(context.availability, GraphAvailability::Ready);
    let change = upsert(&before);
    assert!(
        service
            .apply(before.id, &before.stamp(), "ordinary-after-ready", &change)
            .is_err()
    );
    assert!(
        service
            .apply_semantic_graph_with_admission("denied-after-ready", &change, &admission)
            .is_err()
    );
    assert_eq!(admission.inspections.load(Ordering::SeqCst), 1);
    assert_eq!(admission.writer.seen.lock().unwrap().len(), 1);
    assert_eq!(admission.writer.active.load(Ordering::SeqCst), 0);
    assert_eq!(admission.writer.checks.load(Ordering::SeqCst), 0);
    assert_eq!(
        service
            .semantic_graph_authoring_context(&request)
            .unwrap()
            .availability,
        GraphAvailability::Blocked
    );
    unchanged(&service, &before, &events);
}

#[test]
fn revocation_after_ready_is_rechecked_before_apply_and_prevents_further_inspection() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("graph-context-revoked-before-apply.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let events = service.history(before.id, 0, 1024).unwrap();
    let admission =
        TestAuthoringAdmission::new(&service, &path, &before, Mode::Admit, InspectionMode::Admit);
    let request = authoring_request(&before);
    assert_eq!(
        service
            .semantic_graph_authoring_context_with_admission(&request, &admission)
            .unwrap()
            .availability,
        GraphAvailability::Ready
    );
    admission.writer.revoked.store(true, Ordering::SeqCst);
    assert!(
        service
            .apply_semantic_graph_with_admission(
                "revoked-after-ready",
                &upsert(&before),
                &admission
            )
            .is_err()
    );
    assert_eq!(admission.writer.active.load(Ordering::SeqCst), 0);
    assert_eq!(admission.writer.checks.load(Ordering::SeqCst), 1);
    assert_eq!(
        *admission.writer.dropped.lock().unwrap(),
        vec![DroppedLease {
            request_id: "revoked-after-ready".into(),
            revision: before.revision,
            events: events.len(),
        }]
    );
    assert!(
        service
            .semantic_graph_authoring_context_with_admission(&request, &admission)
            .is_err()
    );
    unchanged(&service, &before, &events);
}
