//! Real SQLite/Store source persistence and real StudioService read/refusal.
//! Positive Store writes deliberately test canonical persistence in isolation.
//! They are NOT a workaround for StudioService's pending native admission and
//! do not count as the three end-to-end App/native acceptance cases.
use motionwright_domain::*;
use motionwright_service::{SemanticGraphContextRequest, StudioService};
use uuid::Uuid;
#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod source;
use source::graph_for;

fn apply(service: &StudioService, project: &Project, change: Change) -> Project {
    service
        .apply(
            project.id,
            &project.stamp(),
            &Uuid::new_v4().to_string(),
            &change,
        )
        .unwrap()
        .project
}
fn store_apply(
    service: &StudioService,
    project: &Project,
    request: &str,
    change: &Change,
) -> Project {
    let store = service.store();
    let outcome = store
        .lock()
        .apply(project.id, &project.stamp(), request, change)
        .unwrap();
    outcome.project
}
fn seed(service: &StudioService) -> Project {
    let mut project = service
        .create_project("Canonical Graph persistence oracle")
        .unwrap();
    project = apply(
        service,
        &project,
        Change::AddScene {
            name: "Explanatory relationships".into(),
            objective: "Preserve editable authored source".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = project.scenes[0].id;
    apply(
        service,
        &project,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    )
}
fn review(project: &Project, graph: &SemanticGraph, resource: String) -> Change {
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == graph.scene_id)
        .unwrap();
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == graph.variant_profile_ref)
        .unwrap();
    let start = scene.start.checked_add(graph.time_range.start).unwrap();
    Change::AddReview {
        kind: ReviewKind::Editorial,
        resource,
        body: "Inspect saved explanatory source".into(),
        start: Some(start),
        end: Some(start.checked_add(graph.time_range.duration).unwrap()),
        locale: Some(profile.language.clone()),
        profile_id: Some(profile.id),
    }
}
fn reject_store_unchanged(service: &StudioService, project: &Project, change: Change) {
    let before = service.history(project.id, 0, 1024).unwrap();
    let store = service.store();
    assert!(
        store
            .lock()
            .apply(
                project.id,
                &project.stamp(),
                &Uuid::new_v4().to_string(),
                &change
            )
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), *project);
    assert_eq!(service.history(project.id, 0, 1024).unwrap(), before);
}
fn context_request(project: &Project) -> SemanticGraphContextRequest {
    SemanticGraphContextRequest {
        source_snapshot: GraphSourceSnapshot::from_project(project),
        scene_id: project.scenes[0].id,
        variant_profile_ref: project.deliverables[0].id,
    }
}

#[test]
fn canonical_graph_replays_once_and_reopens_with_review_invalidation_and_original_authorship() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-causal.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let graph = graph_for(&before, false);
    let change = Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    };
    let mut saved = store_apply(&service, &before, "graph-first-explicit-source", &change);
    assert_eq!(saved.revision, before.revision + 1);
    assert_eq!(saved.production_design.semantic_graphs, vec![graph.clone()]);
    assert_eq!(
        store_apply(&service, &before, "graph-first-explicit-source", &change),
        saved
    );
    let first_events = service.history(saved.id, before.revision, 16).unwrap();
    assert_eq!(first_events.len(), 1);
    assert_eq!(first_events[0].change, change);
    let review_change = review(
        &saved,
        &graph,
        format!("graph-edge:{}:{}", graph.id, graph.content.edge_ids()[0]),
    );
    saved = apply(&service, &saved, review_change);
    let review_id = saved.reviews.last().unwrap().id;
    saved = apply(
        &service,
        &saved,
        Change::ResolveReview {
            review_id,
            resolution: "Editorial meaning reviewed, no native verdict".into(),
        },
    );
    let mut edited = graph.clone();
    edited.authorship.source_snapshot = GraphSourceSnapshot::from_project(&saved);
    if let GraphContent::CausalDiagram { vertices, .. } = &mut edited.content {
        vertices[0].label = "Revised authored concept".into();
    }
    let edited_change = Change::UpsertSemanticGraph {
        graph: Box::new(edited.clone()),
    };
    saved = store_apply(
        &service,
        &saved,
        "graph-second-explicit-source",
        &edited_change,
    );
    assert_eq!(
        saved.reviews.last().unwrap().status,
        ReviewStatus::NeedsRecheck
    );
    assert_eq!(saved.production_design.semantic_graphs[0], edited);
    assert_eq!(first_events[0].change, change);
    let journal = service.history(saved.id, 0, 1024).unwrap();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(saved.id).unwrap(), saved);
    assert_eq!(reopened.history(saved.id, 0, 1024).unwrap(), journal);
    assert_eq!(
        reopened
            .project(saved.id)
            .unwrap()
            .production_design
            .semantic_graphs[0]
            .authorship
            .source_snapshot,
        edited.authorship.source_snapshot
    );
}

#[test]
fn synthetic_flow_exact_times_and_cas_rejections_survive_closed_sqlite() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-flow.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = seed(&service);
    let graph = graph_for(&before, true);
    let mut saved = store_apply(
        &service,
        &before,
        "flow-source",
        &Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        },
    );
    for dimension in 0..4 {
        let mut invalid = graph.clone();
        invalid.authorship.source_snapshot = GraphSourceSnapshot::from_project(&saved);
        let stamp = &mut invalid.authorship.source_snapshot;
        match dimension {
            0 => stamp.project_id = Uuid::new_v4(),
            1 => stamp.generation = Uuid::new_v4(),
            2 => stamp.branch_id = Uuid::new_v4(),
            _ => stamp.revision += 1,
        }
        reject_store_unchanged(
            &service,
            &saved,
            Change::UpsertSemanticGraph {
                graph: Box::new(invalid),
            },
        );
    }
    let stale = saved.clone();
    saved = apply(
        &service,
        &saved,
        Change::SetNarrativePremise {
            premise: "Concurrent explicit edit".into(),
        },
    );
    let mut fresh_source = graph.clone();
    fresh_source.authorship.source_snapshot = GraphSourceSnapshot::from_project(&saved);
    let events = service.history(saved.id, 0, 1024).unwrap();
    {
        let store = service.store();
        assert!(
            store
                .lock()
                .apply(
                    saved.id,
                    &stale.stamp(),
                    "stale-outer-revision",
                    &Change::UpsertSemanticGraph {
                        graph: Box::new(fresh_source)
                    }
                )
                .is_err()
        );
    }
    assert_eq!(service.project(saved.id).unwrap(), saved);
    assert_eq!(service.history(saved.id, 0, 1024).unwrap(), events);
    let GraphContent::SystemFlow { source, events, .. } =
        &saved.production_design.semantic_graphs[0].content
    else {
        panic!("flow source lost");
    };
    assert!(matches!(source, GraphSyntheticFlowSource::Synthetic { .. }));
    assert_eq!(events[0].at, RationalTime { num: 1, den: 15 });
    assert_eq!(events[0].duration, RationalTime { num: 1, den: 30 });
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(saved.id).unwrap(), saved);
    assert_eq!(
        reopened
            .project(saved.id)
            .unwrap()
            .production_design
            .semantic_graphs[0],
        graph
    );
}

#[test]
fn graph_branch_versions_and_foreign_child_review_rejection_reopen_without_rebasing() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-branches.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = seed(&service);
    let graph = graph_for(&project, false);
    let main = project.active_branch;
    project = store_apply(
        &service,
        &project,
        "main-graph",
        &Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        },
    );
    project = apply(
        &service,
        &project,
        Change::CreateBranch {
            name: "Alternate causal detail".into(),
        },
    );
    let branch = project.branches.last().unwrap().id;
    project = apply(
        &service,
        &project,
        Change::CheckoutBranch { branch_id: branch },
    );
    assert_eq!(project.production_design.semantic_graphs[0], graph);
    let mut alternate = graph.clone();
    alternate.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
    let new_child = Uuid::new_v4();
    if let GraphContent::CausalDiagram { vertices, .. } = &mut alternate.content {
        let mut vertex = vertices[0].clone();
        vertex.id = new_child;
        vertex.r#box.x = 900.0;
        vertices.push(vertex);
    }
    project = store_apply(
        &service,
        &project,
        "alternate-graph",
        &Change::UpsertSemanticGraph {
            graph: Box::new(alternate.clone()),
        },
    );
    project = apply(
        &service,
        &project,
        Change::CheckoutBranch { branch_id: main },
    );
    assert_eq!(project.production_design.semantic_graphs[0], graph);
    let before = service.history(project.id, 0, 1024).unwrap();
    let foreign_review = review(
        &project,
        &graph,
        format!("graph-vertex:{}:{new_child}", graph.id),
    );
    assert!(
        service
            .apply(
                project.id,
                &project.stamp(),
                "foreign-branch-child",
                &foreign_review
            )
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(service.history(project.id, 0, 1024).unwrap(), before);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), project);
    project = apply(
        &reopened,
        &project,
        Change::CheckoutBranch { branch_id: branch },
    );
    assert_eq!(project.production_design.semantic_graphs[0], alternate);
    assert_ne!(
        alternate.authorship.source_snapshot.revision,
        project.revision
    );
    drop(reopened);
    let final_store = StudioService::open(&path).unwrap();
    assert_eq!(final_store.project(project.id).unwrap(), project);
}

#[test]
fn service_inspection_returns_real_descriptors_without_revision_or_native_ready() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("graph-read.sqlite3")).unwrap();
    let mut project = seed(&service);
    project = apply(
        &service,
        &project,
        Change::AddClaim {
            text: "Authored dependency".into(),
            source: None,
            context: "Descriptor oracle".into(),
            source_revision: Some("source-1".into()),
        },
    );
    let request = context_request(&project);
    let journal = service.history(project.id, 0, 1024).unwrap();
    let context = service.semantic_graph_authoring_context(&request).unwrap();
    assert_eq!(
        context.source_snapshot,
        GraphSourceSnapshot::from_project(&project)
    );
    assert_eq!(context.scene_id, request.scene_id);
    assert_eq!(context.variant_profile_ref, request.variant_profile_ref);
    assert_eq!(context.availability, GraphAvailability::Blocked);
    assert!(
        context
            .blocking_reasons
            .contains(&GraphBlockReason::NativeGraphProfileUnavailable)
    );
    assert!(
        context
            .blocking_reasons
            .contains(&GraphBlockReason::UnsupportedRendererGuardMissing)
    );
    assert!(
        context
            .dependencies
            .contains(&GraphDependencyDescriptor::Claim {
                claim_id: project.brief.claims[0].id,
                content_sha256: semantic_graph_claim_digest(&project.brief.claims[0]).unwrap()
            })
    );
    assert_eq!(
        context.dependencies,
        project.semantic_graph_dependencies().unwrap()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);
}

#[test]
fn read_context_never_falls_back_for_foreign_project_branch_scene_profile_or_stamp() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("graph-read-scope.sqlite3")).unwrap();
    let project = seed(&service);
    let original = context_request(&project);
    let journal = service.history(project.id, 0, 1024).unwrap();
    for dimension in 0..6 {
        let mut request = original.clone();
        match dimension {
            0 => request.source_snapshot.project_id = Uuid::new_v4(),
            1 => request.source_snapshot.generation = Uuid::new_v4(),
            2 => request.source_snapshot.branch_id = Uuid::new_v4(),
            3 => request.source_snapshot.revision += 1,
            4 => request.scene_id = Uuid::new_v4(),
            _ => request.variant_profile_ref = Uuid::new_v4(),
        }
        assert!(service.semantic_graph_authoring_context(&request).is_err());
    }
    let mut forged = serde_json::to_value(original).unwrap();
    forged["availability"] = serde_json::json!("ready");
    assert!(serde_json::from_value::<SemanticGraphContextRequest>(forged).is_err());
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);
}

#[test]
fn service_rejects_graph_writes_without_real_native_admission_and_appends_no_event() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-write-blocked.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = seed(&service);
    let graph = graph_for(&project, false);
    let journal = service.history(project.id, 0, 1024).unwrap();
    for change in [
        Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        },
        Change::RemoveSemanticGraph {
            graph_id: graph.id,
            scene_id: graph.scene_id,
            source_snapshot: GraphSourceSnapshot::from_project(&project),
        },
    ] {
        assert!(
            service
                .apply(
                    project.id,
                    &project.stamp(),
                    &Uuid::new_v4().to_string(),
                    &change
                )
                .is_err()
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);
    }
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(reopened.history(project.id, 0, 1024).unwrap(), journal);
}

#[test]
fn sqlite_rejects_all_graph_resource_aliases_and_reopened_canonical_locks_block_upsert_and_remove()
{
    let directory = tempfile::tempdir().unwrap();
    for index in 0..3 {
        let path = directory
            .path()
            .join(format!("graph-resource-{index}.sqlite3"));
        let service = StudioService::open(&path).unwrap();
        let mut project = seed(&service);
        let graph = source::graph_with_alpha_identity(&project);
        project = store_apply(
            &service,
            &project,
            "graph-resource-source",
            &Change::UpsertSemanticGraph {
                graph: Box::new(graph.clone()),
            },
        );
        let (canonical, aliases) = source::resource_spelling_cases(&graph).remove(index);
        let journal = service.history(project.id, 0, 1024).unwrap();
        for alias in &aliases {
            assert!(!semantic_graph_resource_exists(
                &project.production_design.semantic_graphs,
                alias
            ));
            let lock = Change::SetLock {
                resource: alias.clone(),
                kind: LockKind::Content,
                note: "Noncanonical Graph resource must not become an ineffective lock".into(),
            };
            let error = {
                let store = service.store();
                let outcome = store.lock().apply(
                    project.id,
                    &project.stamp(),
                    &Uuid::new_v4().to_string(),
                    &lock,
                );
                match outcome {
                    Ok(_) => panic!("Graph resource alias was persisted"),
                    Err(error) => error,
                }
            };
            assert!(error.to_string().contains("canonical lowercase UUIDs"));
            let alias_review = review(&project, &graph, alias.clone());
            assert!(
                service
                    .apply(
                        project.id,
                        &project.stamp(),
                        &Uuid::new_v4().to_string(),
                        &alias_review
                    )
                    .is_err()
            );
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);
        }
        project = apply(
            &service,
            &project,
            Change::SetLock {
                resource: canonical.clone(),
                kind: LockKind::Content,
                note: "Effective canonical Graph protection".into(),
            },
        );
        let canonical_review = review(&project, &graph, canonical.clone());
        project = apply(&service, &project, canonical_review);
        let journal = service.history(project.id, 0, 1024).unwrap();
        drop(service);
        let reopened = StudioService::open(&path).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert_eq!(reopened.history(project.id, 0, 1024).unwrap(), journal);
        assert_eq!(project.locks.last().unwrap().resource, canonical);
        assert_eq!(project.reviews.last().unwrap().anchor.resource, canonical);
        assert!(semantic_graph_resource_exists(
            &project.production_design.semantic_graphs,
            &canonical
        ));
        for alias in &aliases {
            assert!(!semantic_graph_resource_exists(
                &project.production_design.semantic_graphs,
                alias
            ));
        }
        let mut edited = graph.clone();
        edited.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
        if let GraphContent::CausalDiagram { vertices, .. } = &mut edited.content {
            vertices[0].label = "Must stay locked after reopen".into();
        }
        for change in [
            Change::UpsertSemanticGraph {
                graph: Box::new(edited),
            },
            Change::RemoveSemanticGraph {
                graph_id: graph.id,
                scene_id: graph.scene_id,
                source_snapshot: GraphSourceSnapshot::from_project(&project),
            },
        ] {
            let error = {
                let store = reopened.store();
                let outcome = store.lock().apply(
                    project.id,
                    &project.stamp(),
                    &Uuid::new_v4().to_string(),
                    &change,
                );
                match outcome {
                    Ok(_) => panic!("Canonical Graph lock did not survive reopen"),
                    Err(error) => error,
                }
            };
            assert!(error.to_string().contains("locked"));
            assert!(error.to_string().contains(&canonical));
            assert_eq!(reopened.project(project.id).unwrap(), project);
            assert_eq!(reopened.history(project.id, 0, 1024).unwrap(), journal);
        }
        drop(reopened);
        let final_store = StudioService::open(&path).unwrap();
        assert_eq!(final_store.project(project.id).unwrap(), project);
        assert_eq!(final_store.history(project.id, 0, 1024).unwrap(), journal);
    }
}

#[test]
fn graph_review_points_and_subranges_replay_once_and_survive_closed_sqlite() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-review-ranges.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = seed(&service);
    project = apply(
        &service,
        &project,
        Change::AddScene {
            name: "Later Graph scene".into(),
            objective: "Preserve absolute review time in SQLite".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = project.scenes[1].id;
    project = apply(
        &service,
        &project,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    );
    let mut graph = graph_for(&project, false);
    graph.scene_id = scene_id;
    graph.subject_ref = GraphSubjectRef::Scene { scene_id };
    graph.time_range.start = RationalTime { num: 1, den: 1 };
    graph.time_range.duration = RationalTime { num: 4, den: 1 };
    project = store_apply(
        &service,
        &project,
        "saved-source-for-review-ranges",
        &Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        },
    );
    let time = |num, den| RationalTime { num, den };
    for resource in [
        format!("graph:{}", graph.id),
        format!(
            "graph-vertex:{}:{}",
            graph.id,
            graph.content.vertices()[0].id
        ),
        format!("graph-edge:{}:{}", graph.id, graph.content.edge_ids()[0]),
    ] {
        for (saved_start, saved_end) in [
            (time(7, 1), None),
            (time(11, 1), None),
            (time(15, 2), Some(time(21, 2))),
            (time(7, 1), Some(time(7, 1))),
        ] {
            let before = project.clone();
            let mut change = review(&before, &graph, resource.clone());
            if let Change::AddReview { start, end, .. } = &mut change {
                *start = Some(saved_start);
                *end = saved_end;
            }
            let request = Uuid::new_v4().to_string();
            project = service
                .apply(before.id, &before.stamp(), &request, &change)
                .unwrap()
                .project;
            assert_eq!(
                service
                    .apply(before.id, &before.stamp(), &request, &change)
                    .unwrap()
                    .project,
                project
            );
            let events = service.history(project.id, before.revision, 16).unwrap();
            assert_eq!(events.len(), 1);
            assert_eq!(events[0].change, change);
            let anchor = &project.reviews.last().unwrap().anchor;
            assert_eq!(anchor.resource, resource);
            assert_eq!(anchor.start, Some(saved_start));
            assert_eq!(anchor.end, saved_end);
            assert_eq!(anchor.profile_id, Some(graph.variant_profile_ref));
            assert_eq!(
                anchor.locale.as_deref(),
                Some(before.deliverables[0].language.as_str())
            );
        }
        for dimension in 0..6 {
            let mut invalid = review(&project, &graph, resource.clone());
            if let Change::AddReview {
                start,
                end,
                locale,
                profile_id,
                ..
            } = &mut invalid
            {
                match dimension {
                    0 => {
                        *start = Some(time(6, 1));
                        *end = None;
                    }
                    1 => {
                        *start = Some(time(12, 1));
                        *end = None;
                    }
                    2 => *end = Some(time(12, 1)),
                    3 => {
                        *start = Some(time(10, 1));
                        *end = Some(time(9, 1));
                    }
                    4 => *locale = Some("different-from-saved-profile".into()),
                    _ => *profile_id = Some(project.deliverables[1].id),
                }
            }
            let journal = service.history(project.id, 0, 1024).unwrap();
            assert!(
                service
                    .apply(
                        project.id,
                        &project.stamp(),
                        &Uuid::new_v4().to_string(),
                        &invalid
                    )
                    .is_err()
            );
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);
        }
    }
    assert_eq!(project.reviews.len(), 12);
    assert_eq!(project.production_design.semantic_graphs, vec![graph]);
    let journal = service.history(project.id, 0, 1024).unwrap();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(reopened.history(project.id, 0, 1024).unwrap(), journal);
}
