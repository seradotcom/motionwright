//! Actual StudioService/SQLite coverage of the private canonical operation.
//! The two-connection test below does not claim to be a two-process test.
use motionwright_domain::*;
use motionwright_service::{BranchCombinationPreviewRequest, StudioService};
use motionwright_storage::StorageError;
use std::{collections::BTreeSet, path::Path};
use uuid::Uuid;

fn commit(service: &StudioService, project: &mut Project, change: Change) {
    *project = service
        .apply(
            project.id,
            &project.stamp(),
            &Uuid::now_v7().to_string(),
            &change,
        )
        .unwrap()
        .project;
}
fn receipt_count(path: &Path, project: Uuid) -> i64 {
    let db =
        rusqlite::Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    db.query_row(
        "SELECT count(*) FROM receipts WHERE project_id=?1",
        [project.to_string()],
        |row| row.get(0),
    )
    .unwrap()
}
fn preview_request(p: &Project, input: &BranchCombination) -> BranchCombinationPreviewRequest {
    BranchCombinationPreviewRequest {
        project_id: p.id,
        generation: p.generation,
        revision: p.revision,
        input: input.clone(),
    }
}
fn setup(service: &StudioService, scene_timing_lock: bool) -> (Project, BranchCombination) {
    let mut p = service.create_project("Two authored alternatives").unwrap();
    commit(
        service,
        &mut p,
        Change::AddScene {
            name: "Shared scene".into(),
            objective: "Show causality".into(),
            duration_seconds: 5,
        },
    );
    let scene_id = p.scenes[0].id;
    let node_id = Uuid::now_v7();
    commit(
        service,
        &mut p,
        Change::AddCanvasNode {
            scene_id,
            node: CanvasNode {
                id: node_id,
                name: "Persistent subject".into(),
                kind: "shape".into(),
                parent_id: None,
                x: 10.0,
                y: 20.0,
                width: 80.0,
                height: 60.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: None,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 0,
                style: NodeStyle::default(),
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            },
        },
    );
    if scene_timing_lock {
        commit(
            service,
            &mut p,
            Change::SetLock {
                resource: format!("scene:{scene_id}"),
                kind: LockKind::Timing,
                note: "Keep the authored scene timing protected".into(),
            },
        );
    }
    let main = p.active_branch;
    commit(
        service,
        &mut p,
        Change::CreateBranch {
            name: "Motion alternative".into(),
        },
    );
    let donor = p.branches.last().unwrap().id;
    commit(service, &mut p, Change::CheckoutBranch { branch_id: donor });
    commit(
        service,
        &mut p,
        Change::TransformCanvasNode {
            scene_id,
            node_id,
            transform: CanvasTransform {
                x: 100.0,
                y: 20.0,
                width: 80.0,
                height: 60.0,
                rotation_deg: 0.0,
                opacity: 1.0,
            },
        },
    );
    commit(
        service,
        &mut p,
        Change::SetCanvasKeyframe {
            scene_id,
            node_id,
            keyframe: CanvasKeyframe {
                at: RationalTime::new(2, 1).unwrap(),
                property: MotionProperty::X,
                value: 250.0,
                interpolation: MotionInterpolation::Linear,
            },
        },
    );
    commit(service, &mut p, Change::CheckoutBranch { branch_id: main });
    let input = BranchCombination {
        source: p.stamp(),
        base_branch_id: main,
        base_head_revision: p
            .branches
            .iter()
            .find(|b| b.id == main)
            .unwrap()
            .head_revision,
        donor_branch_id: donor,
        donor_head_revision: p
            .branches
            .iter()
            .find(|b| b.id == donor)
            .unwrap()
            .head_revision,
        candidate_branch_id: Uuid::now_v7(),
        candidate_name: "Composition plus movement".into(),
        scene_id,
        picks: vec![BranchCombinationPick {
            node_id,
            field: BranchCombinationField::Motion,
        }],
        rationale: "Keep the current composition and use the other authored motion.".into(),
    };
    (p, input)
}

#[test]
fn canonical_preview_is_read_only_closed_and_bound_to_both_source_heads() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("preview.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (p, input) = setup(&service, false);
    let before = service.export_project(p.id).unwrap();
    let receipts = receipt_count(&path, p.id);
    let request = preview_request(&p, &input);
    assert_eq!(
        service.preview_branch_combination(&request).unwrap(),
        p.preview_branch_combination(&input).unwrap()
    );
    let mut wire = serde_json::to_value(&request).unwrap();
    for revision in [0, u64::MAX] {
        let mut historical = request.clone();
        historical.input.source.revision = revision;
        let historical_wire = serde_json::to_string(&historical).unwrap();
        let decoded: BranchCombinationPreviewRequest =
            serde_json::from_str(&historical_wire).unwrap();
        assert_eq!(serde_json::to_string(&decoded).unwrap(), historical_wire);
    }
    let wire_text = serde_json::to_string(&request).unwrap();
    let duplicate_source_revision =
        wire_text.replacen("\"source\":{", "\"source\":{\"revision\":0,", 1);
    assert_ne!(duplicate_source_revision, wire_text);
    let error = serde_json::from_str::<BranchCombinationPreviewRequest>(&duplicate_source_revision)
        .unwrap_err();
    assert!(error.to_string().contains("duplicate field `revision`"));
    let mut extra_source_field = wire.clone();
    extra_source_field["input"]["source"]["effect_grant"] = serde_json::json!(Uuid::now_v7());
    assert!(serde_json::from_value::<BranchCombinationPreviewRequest>(extra_source_field).is_err());
    wire["effect_grant"] = serde_json::json!(Uuid::now_v7());
    assert!(serde_json::from_value::<BranchCombinationPreviewRequest>(wire).is_err());
    let mut bad = request.clone();
    bad.generation = Uuid::now_v7();
    assert!(matches!(
        service.preview_branch_combination(&bad),
        Err(StorageError::GenerationConflict)
    ));
    let mut bad = request.clone();
    bad.revision += 1;
    assert!(matches!(
        service.preview_branch_combination(&bad),
        Err(StorageError::Conflict { .. })
    ));
    let mut bad = request.clone();
    bad.input.source.resource = format!("project:{}", Uuid::now_v7());
    assert!(matches!(
        service.preview_branch_combination(&bad),
        Err(StorageError::Domain(DomainError::Invalid(_)))
    ));
    for base in [true, false] {
        let mut bad = request.clone();
        if base {
            bad.input.base_head_revision += 1;
        } else {
            bad.input.donor_head_revision += 1;
        }
        assert!(matches!(
            service.preview_branch_combination(&bad),
            Err(StorageError::Domain(DomainError::Invalid(_)))
        ));
    }
    let after = service.export_project(p.id).unwrap();
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(receipt_count(&path, p.id), receipts);
}

#[test]
fn one_combination_event_undo_and_original_receipt_survive_sqlite_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("combination.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (before, input) = setup(&service, false);
    let initial_history = service.export_project(before.id).unwrap().events;
    let change = Change::CombineCreativeBranches {
        input: Box::new(input.clone()),
    };
    let created = service
        .apply(before.id, &before.stamp(), "retained-combination", &change)
        .unwrap();
    assert!(!created.replayed);
    assert_eq!(created.project.revision, before.revision + 1);
    assert_eq!(created.project.active_branch, before.active_branch);
    assert_eq!(created.project.scenes, before.scenes);
    for old in &before.branch_workspaces {
        assert_eq!(
            created
                .project
                .branch_workspaces
                .iter()
                .find(|w| w.branch_id == old.branch_id),
            Some(old)
        );
    }
    let journal = service.export_project(before.id).unwrap().events;
    assert_eq!(&journal[..initial_history.len()], &initial_history);
    assert_eq!(journal.len(), initial_history.len() + 1);
    assert_eq!(journal.last().unwrap().change, change);
    assert_eq!(journal.last().unwrap().revision, created.project.revision);
    let mut opened = created.project.clone();
    commit(
        &service,
        &mut opened,
        Change::CheckoutBranch {
            branch_id: input.candidate_branch_id,
        },
    );
    let patch_id = opened.production_design.patches.last().unwrap().id;
    assert_eq!(opened.scenes[0].nodes[0].keyframes.len(), 1);
    assert_eq!(opened.scenes[0].nodes[0].x, before.scenes[0].nodes[0].x);
    commit(
        &service,
        &mut opened,
        Change::UndoCreativePatch { patch_id },
    );
    assert_eq!(opened.scenes[0].nodes, before.scenes[0].nodes);
    assert_eq!(
        opened.production_design.patches.last().unwrap().reverts,
        Some(patch_id)
    );
    let final_journal = service.export_project(before.id).unwrap().events;
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(before.id).unwrap(), opened);
    assert_eq!(
        reopened.export_project(before.id).unwrap().events,
        final_journal
    );
    // Direct storage receipt recovery proves persistence; the UI must first
    // observe the current project and must never replay an uncertain write.
    let replay = reopened
        .apply(before.id, &before.stamp(), "retained-combination", &change)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, created.project);
    assert_eq!(reopened.project(before.id).unwrap(), opened);
    assert_eq!(
        reopened.export_project(before.id).unwrap().events,
        final_journal
    );
}

#[test]
fn independent_sqlite_connection_cannot_promote_a_stale_combination_or_mint_a_receipt() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("stale.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (before, input) = setup(&service, false);
    let peer = StudioService::open(&path).unwrap();
    let mut current = peer.project(before.id).unwrap();
    commit(
        &peer,
        &mut current,
        Change::RenameProject {
            title: "A later author revision".into(),
        },
    );
    let journal = peer.export_project(before.id).unwrap().events;
    let receipts = receipt_count(&path, before.id);
    let change = Change::CombineCreativeBranches {
        input: Box::new(input.clone()),
    };
    assert!(matches!(
        service.apply(before.id, &before.stamp(), "stale-outer", &change),
        Err(StorageError::Conflict { .. })
    ));
    assert!(matches!(
        service.apply(before.id, &current.stamp(), "stale-inner", &change),
        Err(StorageError::Domain(DomainError::Invalid(_)))
    ));
    assert_eq!(service.project(before.id).unwrap(), current);
    assert_eq!(peer.project(before.id).unwrap(), current);
    assert_eq!(service.export_project(before.id).unwrap().events, journal);
    assert_eq!(receipt_count(&path, before.id), receipts);
    assert!(
        current
            .branches
            .iter()
            .all(|b| b.id != input.candidate_branch_id)
    );
}

#[test]
fn preserved_scene_timing_lock_rejects_preview_and_write_without_journal_or_receipt() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("locked.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (before, input) = setup(&service, true);
    let journal = service.export_project(before.id).unwrap().events;
    let receipts = receipt_count(&path, before.id);
    assert!(matches!(
        service.preview_branch_combination(&preview_request(&before, &input)),
        Err(StorageError::Domain(DomainError::Locked(_)))
    ));
    assert!(matches!(
        service.apply(
            before.id,
            &before.stamp(),
            "locked-combination",
            &Change::CombineCreativeBranches {
                input: Box::new(input)
            }
        ),
        Err(StorageError::Domain(DomainError::Locked(_)))
    ));
    assert_eq!(service.project(before.id).unwrap(), before);
    assert_eq!(service.export_project(before.id).unwrap().events, journal);
    assert_eq!(receipt_count(&path, before.id), receipts);
}
