//! Canonical Project mutation/review/snapshot oracles, not native acceptance.
use motionwright_domain::*;
use uuid::Uuid;
#[path = "support/semantic_graph_source.rs"]
mod source;
use source::{graph_for, seed_domain};

fn save(project: &mut Project, graph: SemanticGraph) {
    project
        .apply_change(&Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        })
        .unwrap();
}
fn reject_unchanged(project: &mut Project, change: Change) {
    let before = project.clone();
    assert!(project.apply_change(&change).is_err());
    assert_eq!(*project, before);
}
fn review(graph: &SemanticGraph, project: &Project, resource: String) -> Change {
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
    let end = start.checked_add(graph.time_range.duration).unwrap();
    Change::AddReview {
        kind: ReviewKind::Editorial,
        resource,
        body: "Review authored meaning; not native acceptance".into(),
        start: Some(start),
        end: Some(end),
        locale: Some(profile.language.clone()),
        profile_id: Some(profile.id),
    }
}

#[test]
fn graph_change_wire_preserves_original_tags_fields_and_rejects_unknown_authority() {
    let project = seed_domain();
    let graph = graph_for(&project, false);
    let change = Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    };
    let wire = serde_json::to_value(&change).unwrap();
    assert_eq!(wire["type"], "upsert_semantic_graph");
    assert_eq!(wire["graph"]["id"], graph.id.to_string());
    assert_eq!(
        serde_json::from_value::<Change>(wire.clone()).unwrap(),
        change
    );
    let mut forged = wire;
    forged["ready"] = serde_json::json!(true);
    assert!(serde_json::from_value::<Change>(forged).is_err());
    let remove = Change::RemoveSemanticGraph {
        graph_id: graph.id,
        scene_id: graph.scene_id,
        source_snapshot: GraphSourceSnapshot::from_project(&project),
    };
    let wire = serde_json::to_value(&remove).unwrap();
    assert_eq!(wire["type"], "remove_semantic_graph");
    assert_eq!(wire.as_object().unwrap().len(), 4);
    assert_eq!(serde_json::from_value::<Change>(wire).unwrap(), remove);
}

#[test]
fn an_absent_graph_collection_preserves_legacy_reads_and_uses_the_existing_first_write_upgrade() {
    let mut project = seed_domain();
    project.schema_version = LEGACY_PROJECT_SCHEMA_VERSION;
    project.validate().unwrap();
    let wire = serde_json::to_value(&project).unwrap();
    assert!(wire["production_design"].get("semantic_graphs").is_none());
    let reopened: Project = serde_json::from_value(wire).unwrap();
    assert_eq!(reopened, project);
    assert_eq!(reopened.schema_version, LEGACY_PROJECT_SCHEMA_VERSION);
    let graph = graph_for(&project, false);
    let source_snapshot = graph.authorship.source_snapshot.clone();
    save(&mut project, graph);
    assert_eq!(project.schema_version, PROJECT_SCHEMA_VERSION);
    assert_eq!(
        project.production_design.semantic_graphs[0]
            .authorship
            .source_snapshot,
        source_snapshot
    );
}

#[test]
fn all_four_authorship_dimensions_are_current_on_write_and_rejection_is_atomic() {
    let mut project = seed_domain();
    let graph = graph_for(&project, false);
    for dimension in 0..4 {
        let mut wrong = graph.clone();
        let stamp = &mut wrong.authorship.source_snapshot;
        match dimension {
            0 => stamp.project_id = Uuid::new_v4(),
            1 => stamp.generation = Uuid::new_v4(),
            2 => stamp.branch_id = Uuid::new_v4(),
            _ => stamp.revision += 1,
        }
        reject_unchanged(
            &mut project,
            Change::UpsertSemanticGraph {
                graph: Box::new(wrong),
            },
        );
    }
    save(&mut project, graph.clone());
    for dimension in 0..4 {
        let mut wrong = GraphSourceSnapshot::from_project(&project);
        match dimension {
            0 => wrong.project_id = Uuid::new_v4(),
            1 => wrong.generation = Uuid::new_v4(),
            2 => wrong.branch_id = Uuid::new_v4(),
            _ => wrong.revision += 1,
        }
        reject_unchanged(
            &mut project,
            Change::RemoveSemanticGraph {
                graph_id: graph.id,
                scene_id: graph.scene_id,
                source_snapshot: wrong,
            },
        );
    }
}

#[test]
fn deleted_children_and_new_children_cannot_bypass_existing_graph_locks() {
    let mut original = seed_domain();
    let graph = graph_for(&original, false);
    save(&mut original, graph.clone());
    let old_vertex = graph.content.vertices()[1].id;
    let old_edge = graph.content.edge_ids()[0];
    let new_vertex = Uuid::new_v4();
    for resource in [
        original.resource_key(),
        format!("scene:{}", graph.scene_id),
        format!("graph:{}", graph.id),
        format!("graph-vertex:{}:{old_vertex}", graph.id),
        format!("graph-edge:{}:{old_edge}", graph.id),
        format!("graph-vertex:{}:{new_vertex}", graph.id),
    ] {
        for kind in [
            LockKind::Content,
            LockKind::Timing,
            LockKind::Position,
            LockKind::Style,
            LockKind::Renderer,
        ] {
            let mut project = original.clone();
            project
                .apply_change(&Change::SetLock {
                    resource: resource.clone(),
                    kind,
                    note: "Authored protection".into(),
                })
                .unwrap();
            let mut changed = graph.clone();
            if let GraphContent::CausalDiagram { vertices, edges } = &mut changed.content {
                edges.clear();
                vertices.truncate(1);
                let mut vertex = vertices[0].clone();
                vertex.id = new_vertex;
                vertex.r#box.x = 900.0;
                vertices.push(vertex);
            }
            changed.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
            reject_unchanged(
                &mut project,
                Change::UpsertSemanticGraph {
                    graph: Box::new(changed),
                },
            );
        }
    }
    let mut project = original;
    project
        .apply_change(&Change::SetLock {
            resource: format!("graph-edge:{}:{old_edge}", graph.id),
            kind: LockKind::Content,
            note: "Keep relation".into(),
        })
        .unwrap();
    let source_snapshot = GraphSourceSnapshot::from_project(&project);
    reject_unchanged(
        &mut project,
        Change::RemoveSemanticGraph {
            graph_id: graph.id,
            scene_id: graph.scene_id,
            source_snapshot,
        },
    );
}

#[test]
fn graph_identity_kind_and_scene_profile_uniqueness_are_preserved() {
    let mut project = seed_domain();
    let graph = graph_for(&project, false);
    save(&mut project, graph.clone());
    let duplicate = graph_for(&project, false);
    reject_unchanged(
        &mut project,
        Change::UpsertSemanticGraph {
            graph: Box::new(duplicate),
        },
    );
    let mut kind_change = graph_for(&project, true);
    kind_change.id = graph.id;
    reject_unchanged(
        &mut project,
        Change::UpsertSemanticGraph {
            graph: Box::new(kind_change),
        },
    );
    project
        .apply_change(&Change::AddScene {
            name: "Separate scene".into(),
            objective: "Different source".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let scene_id = project.scenes[1].id;
    project
        .apply_change(&Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        })
        .unwrap();
    let mut moved = graph;
    moved.scene_id = scene_id;
    moved.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
    reject_unchanged(
        &mut project,
        Change::UpsertSemanticGraph {
            graph: Box::new(moved),
        },
    );
}

#[test]
fn claim_bound_metaphor_edits_preserve_other_occurrences_and_reject_invalid_targets_atomically() {
    let mut project = seed_domain();
    project
        .apply_change(&Change::AddScene {
            name: "Another representation of the same claim".into(),
            objective: "Keep independently authored occurrences distinct".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let other_scene_id = project.scenes[1].id;
    project
        .apply_change(&Change::SetSceneRenderer {
            scene_id: other_scene_id,
            renderer: RendererKind::ManimCommunity,
        })
        .unwrap();
    let mut profile = project.deliverables[0].clone();
    profile.included_scene_ids = vec![project.scenes[0].id, other_scene_id];
    project
        .apply_change(&Change::UpsertDeliverable { profile })
        .unwrap();
    project
        .apply_change(&Change::AddClaim {
            text: "An authored source supplies a dependent stage.".into(),
            source: Some(SourceReference::ManualNote {
                label: "Original explanatory source".into(),
            }),
            context: "A conceptual relationship, not measured causation.".into(),
            source_revision: Some("source-v1".into()),
        })
        .unwrap();
    let original_claim = project.brief.claims[0].clone();
    let stale_digest = semantic_graph_claim_digest(&original_claim).unwrap();
    let expected = ClaimEditableFields::from(&original_claim);
    let mut replacement = expected.clone();
    replacement.source_revision = Some("source-v2".into());
    project
        .apply_change(&Change::UpdateClaim {
            claim_id: original_claim.id,
            expected,
            replacement,
        })
        .unwrap();
    let claim = project.brief.claims[0].clone();
    let digest = semantic_graph_claim_digest(&claim).unwrap();
    assert_eq!(claim.id, original_claim.id);
    assert_ne!(digest, stale_digest);
    let binding = GraphTargetBinding::Claim {
        claim_id: claim.id,
        content_sha256: digest.clone(),
    };

    let mut selected = graph_for(&project, false);
    let GraphContent::CausalDiagram { vertices, .. } = &mut selected.content else {
        panic!("The existing causal fixture must supply the selected occurrence");
    };
    vertices[0].target = Some(binding.clone());
    vertices[0].label = "Source as a reservoir".into();
    save(&mut project, selected.clone());

    let mut other = graph_for(&project, true);
    other.scene_id = other_scene_id;
    other.subject_ref = GraphSubjectRef::Scene {
        scene_id: other_scene_id,
    };
    let GraphContent::SystemFlow {
        vertices,
        edges,
        events,
        ..
    } = &mut other.content
    else {
        panic!("The existing flow fixture must supply the other occurrence");
    };
    for vertex in vertices.iter_mut() {
        vertex.id = Uuid::new_v4();
    }
    vertices[0].target = Some(binding.clone());
    vertices[0].label = "Source as an input port".into();
    edges[0].id = Uuid::new_v4();
    edges[0].from_vertex_id = vertices[0].id;
    edges[0].to_vertex_id = vertices[1].id;
    events[0].id = Uuid::new_v4();
    events[0].edge_id = edges[0].id;
    assert_ne!(selected.id, other.id);
    assert_ne!(selected.scene_id, other.scene_id);
    assert!(selected.content.vertices().iter().all(|selected_vertex| {
        other
            .content
            .vertices()
            .iter()
            .all(|other_vertex| selected_vertex.id != other_vertex.id)
    }));
    save(&mut project, other.clone());
    let brief_before = project.brief.clone();

    let mut edited = selected.clone();
    edited.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
    let GraphContent::CausalDiagram { vertices, .. } = &mut edited.content else {
        panic!("Editing the selected occurrence must retain its semantic kind");
    };
    vertices[0].label = "Source as a pulse".into();
    vertices[0].shape = GraphVertexShape::Circle;
    vertices[0].r#box.x = 80.0;
    vertices[0].r#box.width = 80.0;
    assert_ne!(edited.content, selected.content);
    save(&mut project, edited.clone());

    assert_eq!(project.brief, brief_before);
    assert_eq!(project.brief.claims, vec![claim.clone()]);
    assert_eq!(
        semantic_graph_claim_digest(&project.brief.claims[0]).unwrap(),
        digest
    );
    assert!(project.semantic_graph_dependencies().unwrap().contains(
        &GraphDependencyDescriptor::Claim {
            claim_id: claim.id,
            content_sha256: digest.clone(),
        }
    ));
    assert_eq!(project.production_design.semantic_graphs.len(), 2);
    for expected in [&edited, &other] {
        let saved = project
            .production_design
            .semantic_graphs
            .iter()
            .find(|graph| graph.id == expected.id)
            .unwrap();
        assert_eq!(saved, expected);
        assert_eq!(saved.content.vertices()[0].target, Some(binding.clone()));
        project.validate_semantic_graph_dependencies(saved).unwrap();
    }
    assert_eq!(edited.id, selected.id);
    assert_eq!(edited.scene_id, selected.scene_id);
    assert_eq!(
        edited.content.vertices()[0].id,
        selected.content.vertices()[0].id
    );
    assert_eq!(edited.content.edge_ids(), selected.content.edge_ids());
    project.validate().unwrap();

    let reject_with_reason = |project: &mut Project, mut graph: SemanticGraph, reason: &str| {
        graph.authorship.source_snapshot = GraphSourceSnapshot::from_project(project);
        let before = project.clone();
        assert_eq!(
            project.apply_change(&Change::UpsertSemanticGraph {
                graph: Box::new(graph),
            }),
            Err(DomainError::Invalid(reason.into()))
        );
        assert_eq!(*project, before);
    };
    let missing_claim_id = Uuid::new_v4();
    assert!(
        project
            .brief
            .claims
            .iter()
            .all(|claim| claim.id != missing_claim_id)
    );
    for invalid_target in [
        GraphTargetBinding::Claim {
            claim_id: claim.id,
            content_sha256: stale_digest,
        },
        GraphTargetBinding::Claim {
            claim_id: missing_claim_id,
            content_sha256: digest,
        },
    ] {
        let mut invalid = edited.clone();
        let GraphContent::CausalDiagram { vertices, .. } = &mut invalid.content else {
            panic!("The target refusal must use the saved causal occurrence");
        };
        vertices[0].target = Some(invalid_target);
        reject_with_reason(
            &mut project,
            invalid,
            "Graph dependency is stale or has no current domain fingerprint",
        );
    }
    let mut looped = edited;
    let GraphContent::CausalDiagram { edges, .. } = &mut looped.content else {
        panic!("The topology refusal must use the saved causal occurrence");
    };
    edges[0].to_vertex_id = edges[0].from_vertex_id;
    reject_with_reason(
        &mut project,
        looped,
        "Graph edge needs distinct endpoints and bounded waypoints",
    );
}

#[test]
fn historical_claim_binding_is_preserved_but_stale_dependency_cannot_be_reused() {
    let mut project = seed_domain();
    project
        .apply_change(&Change::AddClaim {
            text: "Source claim".into(),
            source: None,
            context: "Synthetic scenario".into(),
            source_revision: Some("v1".into()),
        })
        .unwrap();
    let claim = project.brief.claims[0].clone();
    let mut graph = graph_for(&project, false);
    graph.subject_ref = GraphSubjectRef::Claim {
        claim_id: claim.id,
        content_sha256: semantic_graph_claim_digest(&claim).unwrap(),
    };
    save(&mut project, graph.clone());
    let expected = ClaimEditableFields::from(&claim);
    let mut replacement = expected.clone();
    replacement.source_revision = Some("v2".into());
    project
        .apply_change(&Change::UpdateClaim {
            claim_id: claim.id,
            expected,
            replacement,
        })
        .unwrap();
    assert_eq!(project.production_design.semantic_graphs[0], graph);
    project.validate().unwrap();
    assert!(
        project
            .validate_semantic_graph_dependencies(&graph)
            .is_err()
    );
    let mut stale = graph.clone();
    stale.authorship.source_snapshot = GraphSourceSnapshot::from_project(&project);
    reject_unchanged(
        &mut project,
        Change::UpsertSemanticGraph {
            graph: Box::new(stale),
        },
    );
    let before = project.production_design.semantic_graphs.clone();
    project.generation = Uuid::new_v4();
    project.validate().unwrap();
    assert_eq!(project.production_design.semantic_graphs, before);
    assert_ne!(
        project.generation,
        graph.authorship.source_snapshot.generation
    );
}

#[test]
fn a_profile_resize_or_scene_trim_cannot_silently_rewrite_saved_geometry_or_time() {
    let mut project = seed_domain();
    let graph = graph_for(&project, false);
    save(&mut project, graph.clone());
    let mut profile = project.deliverables[0].clone();
    profile.width += 2;
    reject_unchanged(&mut project, Change::UpsertDeliverable { profile });
    reject_unchanged(
        &mut project,
        Change::SetSceneDuration {
            scene_id: graph.scene_id,
            duration: RationalTime { num: 1, den: 1 },
        },
    );
    assert_eq!(project.production_design.semantic_graphs[0], graph);
}

#[test]
fn graph_review_requires_saved_child_bounded_absolute_time_and_exact_profile_locale() {
    let mut project = seed_domain();
    let mut graph = graph_for(&project, false);
    graph.time_range.start = RationalTime { num: 1, den: 1 };
    graph.time_range.duration = RationalTime { num: 4, den: 1 };
    save(&mut project, graph.clone());
    let valid = review(
        &graph,
        &project,
        format!(
            "graph-vertex:{}:{}",
            graph.id,
            graph.content.vertices()[0].id
        ),
    );
    for dimension in 0..7 {
        let mut wrong = valid.clone();
        if let Change::AddReview {
            resource,
            start,
            end,
            locale,
            profile_id,
            ..
        } = &mut wrong
        {
            match dimension {
                0 => *resource = format!("graph-vertex:{}:{}", graph.id, Uuid::new_v4()),
                1 => *start = Some(RationalTime { num: 0, den: 1 }),
                2 => *end = Some(RationalTime { num: 6, den: 1 }),
                3 => *locale = Some("es".into()),
                4 => *profile_id = Some(project.deliverables[1].id),
                5 => *locale = None,
                _ => *profile_id = None,
            }
        }
        reject_unchanged(&mut project, wrong);
    }
    project.apply_change(&valid).unwrap();
    let saved = project.reviews[0].clone();
    let source_snapshot = GraphSourceSnapshot::from_project(&project);
    project
        .apply_change(&Change::RemoveSemanticGraph {
            graph_id: graph.id,
            scene_id: graph.scene_id,
            source_snapshot,
        })
        .unwrap();
    assert_eq!(project.reviews[0], saved);
    project.validate().unwrap();
    reject_unchanged(&mut project, valid);
}

#[test]
fn every_saved_branch_snapshot_is_validated_without_rebasing_authorship() {
    let mut project = seed_domain();
    let graph = graph_for(&project, false);
    save(&mut project, graph.clone());
    project
        .apply_change(&Change::CreateBranch {
            name: "Alternate Graph realization".into(),
        })
        .unwrap();
    let branch = project.branches.last().unwrap().id;
    project
        .apply_change(&Change::CheckoutBranch { branch_id: branch })
        .unwrap();
    assert_eq!(project.production_design.semantic_graphs[0], graph);
    assert_ne!(
        graph.authorship.source_snapshot.branch_id,
        project.active_branch
    );
    project.validate().unwrap();
    for base in [true, false] {
        let mut corrupt = project.clone();
        let workspace = corrupt
            .branch_workspaces
            .iter_mut()
            .find(|workspace| workspace.branch_id == branch)
            .unwrap();
        let state = if base {
            &mut workspace.base_state
        } else {
            &mut workspace.current_state
        };
        state.production_design.semantic_graphs[0]
            .reference_frame
            .width += 1;
        assert!(corrupt.validate().is_err());
    }
}

#[test]
fn active_brand_rules_apply_to_unbranded_graphs_and_cannot_be_added_around_saved_text() {
    let mut project = seed_domain();
    let graph = graph_for(&project, false);
    save(&mut project, graph.clone());
    let profile = BrandProfile {
        id: Uuid::new_v4(),
        label: "Scoped authored policy".into(),
        version: 1,
        rules: vec![BrandRule::ForbiddenPhrase {
            id: Uuid::new_v4(),
            phrase: "Authored concept".into(),
        }],
    };
    reject_unchanged(
        &mut project,
        Change::SetBrandGovernance {
            profile: Some(profile),
            exceptions: vec![],
        },
    );
    let profile = BrandProfile {
        id: Uuid::new_v4(),
        label: "Visible wordmark policy".into(),
        version: 1,
        rules: vec![BrandRule::RequiredWordmark {
            id: Uuid::new_v4(),
            text: "Required wordmark".into(),
        }],
    };
    reject_unchanged(
        &mut project,
        Change::SetBrandGovernance {
            profile: Some(profile),
            exceptions: vec![],
        },
    );
    assert!(graph.brand_profile_ref.is_none());
}

#[test]
fn uppercase_graph_parent_and_child_aliases_are_rejected_before_lock_mutation() {
    let mut original = seed_domain();
    let graph = source::graph_with_alpha_identity(&original);
    save(&mut original, graph.clone());
    for (canonical, aliases) in source::resource_spelling_cases(&graph) {
        assert!(semantic_graph_resource_exists(
            &original.production_design.semantic_graphs,
            &canonical
        ));
        for alias in aliases {
            assert_ne!(alias, canonical);
            assert!(!semantic_graph_resource_exists(
                &original.production_design.semantic_graphs,
                &alias
            ));
            for mut project in [original.clone(), Project::new("No Graph yet").unwrap()] {
                let before = project.clone();
                let error = project
                    .apply_change(&Change::SetLock {
                        resource: alias.clone(),
                        kind: LockKind::Content,
                        note: "This spelling must be rejected".into(),
                    })
                    .unwrap_err();
                assert!(error.to_string().contains("canonical lowercase UUIDs"));
                assert_eq!(project, before);
            }
        }
        let mut locked = original.clone();
        locked
            .apply_change(&Change::SetLock {
                resource: canonical.clone(),
                kind: LockKind::Content,
                note: "Canonical protection must be effective".into(),
            })
            .unwrap();
        let mut edited = graph.clone();
        edited.authorship.source_snapshot = GraphSourceSnapshot::from_project(&locked);
        if let GraphContent::CausalDiagram { vertices, .. } = &mut edited.content {
            vertices[0].label = "Locked edit".into();
        }
        let before = locked.clone();
        for change in [
            Change::UpsertSemanticGraph {
                graph: Box::new(edited),
            },
            Change::RemoveSemanticGraph {
                graph_id: graph.id,
                scene_id: graph.scene_id,
                source_snapshot: GraphSourceSnapshot::from_project(&locked),
            },
        ] {
            let error = locked.apply_change(&change).unwrap_err();
            assert!(matches!(&error, DomainError::Locked(_)));
            assert!(error.to_string().contains(&canonical));
            assert_eq!(locked, before);
        }
    }
}

#[test]
fn serialized_active_and_branch_locks_cannot_reopen_with_graph_uuid_aliases() {
    let mut original = seed_domain();
    let graph = source::graph_with_alpha_identity(&original);
    save(&mut original, graph.clone());
    original
        .apply_change(&Change::CreateBranch {
            name: "Graph lock snapshot".into(),
        })
        .unwrap();
    for (_, aliases) in source::resource_spelling_cases(&graph) {
        for alias in aliases {
            for snapshot in 0..3 {
                let mut corrupted = original.clone();
                let lock = ProjectLock {
                    id: Uuid::new_v4(),
                    resource: alias.clone(),
                    kind: LockKind::Content,
                    note: "Alias retained verbatim by JSON, rejected by Project validation".into(),
                };
                match snapshot {
                    0 => corrupted.locks.push(lock),
                    1 => corrupted
                        .branch_workspaces
                        .last_mut()
                        .unwrap()
                        .base_state
                        .locks
                        .push(lock),
                    _ => corrupted
                        .branch_workspaces
                        .last_mut()
                        .unwrap()
                        .current_state
                        .locks
                        .push(lock),
                }
                let bytes = serde_json::to_vec(&corrupted).unwrap();
                assert!(String::from_utf8(bytes.clone()).unwrap().contains(&alias));
                assert!(
                    corrupted
                        .validate()
                        .unwrap_err()
                        .to_string()
                        .contains("canonical lowercase UUIDs")
                );
                let error = serde_json::from_slice::<Project>(&bytes).unwrap_err();
                assert!(error.to_string().contains("canonical lowercase UUIDs"));
            }
        }
    }
}

#[test]
fn graph_reviews_reject_aliases_on_creation_and_when_historical_json_is_reopened() {
    let mut original = seed_domain();
    let graph = source::graph_with_alpha_identity(&original);
    save(&mut original, graph.clone());
    for (canonical, aliases) in source::resource_spelling_cases(&graph) {
        let mut reviewed = original.clone();
        reviewed
            .apply_change(&review(&graph, &original, canonical))
            .unwrap();
        for alias in aliases {
            let mut project = original.clone();
            reject_unchanged(&mut project, review(&graph, &original, alias.clone()));
            let mut corrupted = reviewed.clone();
            corrupted.reviews.last_mut().unwrap().anchor.resource = alias;
            let bytes = serde_json::to_vec(&corrupted).unwrap();
            assert!(
                corrupted
                    .validate()
                    .unwrap_err()
                    .to_string()
                    .contains("canonical lowercase UUIDs")
            );
            let error = serde_json::from_slice::<Project>(&bytes).unwrap_err();
            assert!(error.to_string().contains("canonical lowercase UUIDs"));
        }
    }
}

#[test]
fn graph_review_preserves_points_and_contained_ranges_for_each_saved_identity() {
    let mut original = seed_domain();
    original
        .apply_change(&Change::AddScene {
            name: "Later Graph scene".into(),
            objective: "Review project time without rebasing to local time".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let scene_id = original.scenes[1].id;
    original
        .apply_change(&Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        })
        .unwrap();
    let mut graph = graph_for(&original, false);
    graph.scene_id = scene_id;
    graph.subject_ref = GraphSubjectRef::Scene { scene_id };
    graph.time_range.start = RationalTime { num: 1, den: 1 };
    graph.time_range.duration = RationalTime { num: 4, den: 1 };
    save(&mut original, graph.clone());
    let time = |num, den| RationalTime { num, den };
    // Scene starts at 6: Graph's exact absolute interval is [7, 11].
    let ranges = [
        (time(7, 1), None),
        (time(50, 7), None),
        (time(11, 1), None),
        (time(7, 1), Some(time(7, 1))),
        (time(15, 2), Some(time(21, 2))),
        (time(7, 1), Some(time(11, 1))),
    ];
    for resource in [
        format!("graph:{}", graph.id),
        format!(
            "graph-vertex:{}:{}",
            graph.id,
            graph.content.vertices()[0].id
        ),
        format!("graph-edge:{}:{}", graph.id, graph.content.edge_ids()[0]),
    ] {
        for (expected_start, expected_end) in ranges {
            let mut project = original.clone();
            let mut change = review(&graph, &project, resource.clone());
            if let Change::AddReview { start, end, .. } = &mut change {
                *start = Some(expected_start);
                *end = expected_end;
            }
            project.apply_change(&change).unwrap();
            let anchor = &project.reviews.last().unwrap().anchor;
            assert_eq!(anchor.resource, resource);
            assert_eq!(anchor.start, Some(expected_start));
            assert_eq!(anchor.end, expected_end);
            assert_eq!(anchor.profile_id, Some(graph.variant_profile_ref));
            assert_eq!(
                anchor.locale.as_deref(),
                Some(original.deliverables[0].language.as_str())
            );
            assert_eq!(anchor.branch_id, original.active_branch);
            assert_eq!(anchor.revision, original.revision);
            assert_eq!(
                project.production_design.semantic_graphs,
                original.production_design.semantic_graphs
            );
            let reopened: Project =
                serde_json::from_slice(&serde_json::to_vec(&project).unwrap()).unwrap();
            assert_eq!(reopened, project);
            reopened.validate().unwrap();
        }
    }
}

#[test]
fn graph_review_rejects_outside_missing_or_reversed_times_without_mutation() {
    let mut original = seed_domain();
    let mut graph = graph_for(&original, false);
    graph.time_range.start = RationalTime { num: 1, den: 1 };
    graph.time_range.duration = RationalTime { num: 4, den: 1 };
    save(&mut original, graph.clone());
    let time = |num| RationalTime { num, den: 1 };
    let invalid = [
        (None, None),
        (None, Some(time(5))),
        (Some(time(0)), None),
        (Some(time(6)), None),
        (Some(time(0)), Some(time(4))),
        (Some(time(1)), Some(time(6))),
        (Some(time(4)), Some(time(3))),
    ];
    for resource in [
        format!("graph:{}", graph.id),
        format!(
            "graph-vertex:{}:{}",
            graph.id,
            graph.content.vertices()[0].id
        ),
        format!("graph-edge:{}:{}", graph.id, graph.content.edge_ids()[0]),
    ] {
        for (invalid_start, invalid_end) in invalid {
            let mut project = original.clone();
            let mut change = review(&graph, &project, resource.clone());
            if let Change::AddReview { start, end, .. } = &mut change {
                *start = invalid_start;
                *end = invalid_end;
            }
            reject_unchanged(&mut project, change);
        }
    }
}
