use motionwright_domain::*;
use motionwright_service::StudioService;
use std::path::Path;
use uuid::Uuid;

fn id() -> Uuid {
    Uuid::parse_str("00000000-0000-4000-8000-000000000095").unwrap()
}
fn config() -> ProceduralConfig {
    ProceduralConfig {
        distribution: FieldDistribution::Grid,
        count: 3,
        columns: 3,
        origin_x: 300,
        origin_y: 300,
        area_width: 600,
        area_height: 200,
        ..Default::default()
    }
}
fn influenced(size: u16) -> ProceduralConfig {
    ProceduralConfig {
        influence: Some(ProceduralInfluence {
            center_x: 600,
            center_y: 400,
            radius: 150,
            target_size: size,
        }),
        ..config()
    }
}
fn apply(service: &StudioService, p: &Project, request: &str, change: Change) -> Project {
    service
        .apply(p.id, &p.stamp(), request, &change)
        .unwrap()
        .project
}
fn create(service: &StudioService) -> Project {
    let mut p = service
        .create_project("Persistent editable influence")
        .unwrap();
    for (index, name) in ["Field", "Unrelated context"].into_iter().enumerate() {
        p = apply(
            service,
            &p,
            &format!("scene-{index}"),
            Change::AddScene {
                name: name.into(),
                objective: "Preserve authored context".into(),
                duration_seconds: 6,
            },
        );
    }
    p
}
fn change(p: &Project, config: ProceduralConfig) -> Change {
    Change::UpsertProceduralField {
        instance_id: id(),
        scene_id: p.scenes[0].id,
        config,
    }
}
fn upsert(
    service: &StudioService,
    p: &Project,
    request: &str,
    config: ProceduralConfig,
) -> Project {
    apply(service, p, request, change(p, config))
}
fn refuse_unchanged(
    service: &StudioService,
    p: &Project,
    request: &str,
    change: Change,
    reason: &str,
) {
    let history = service.history(p.id, 0, 256).unwrap();
    let result = service.apply(p.id, &p.stamp(), request, &change);
    let error = result.unwrap_err().to_string();
    assert!(
        error.to_lowercase().contains(reason),
        "wrong refusal: {error}"
    );
    assert_eq!(service.project(p.id).unwrap(), *p);
    assert_eq!(service.history(p.id, 0, 256).unwrap(), history);
}
fn close_and_preserve(case: &str, service: StudioService, source: &Path, p: &Project) {
    assert_eq!(service.project(p.id).unwrap(), *p);
    let history = service.history(p.id, 0, 256).unwrap();
    drop(service);
    let reopened = StudioService::open(source).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), *p);
    assert_eq!(reopened.history(p.id, 0, 256).unwrap(), history);
    drop(reopened);
    let Some(root) = std::env::var_os("MOTIONWRIGHT_PROCEDURAL_INFLUENCE_EVIDENCE_ROOT") else {
        return;
    };
    let target = std::path::PathBuf::from(root).join(case);
    std::fs::create_dir(&target).expect("each closed-store capture uses a fresh directory");
    let wal = source.with_file_name(format!(
        "{}-wal",
        source.file_name().unwrap().to_string_lossy()
    ));
    assert!(
        !wal.exists() || wal.metadata().unwrap().len() == 0,
        "standalone closed SQLite capture"
    );
    std::fs::copy(source, target.join("project.sqlite3")).unwrap();
    std::fs::write(
        target.join("expected-project.json"),
        serde_json::to_vec_pretty(p).unwrap(),
    )
    .unwrap();
    std::fs::write(
        target.join("expected-history.json"),
        serde_json::to_vec_pretty(&history).unwrap(),
    )
    .unwrap();
    std::fs::write(
        target.join("capture.json"),
        serde_json::to_vec_pretty(&serde_json::json!({
            "schema":"motionwright.procedural-influence-closed-store/1","case":case,
            "project_id":p.id,"generation":p.generation,"revision":p.revision,
            "captured_after_service_close_and_reopen_assertions":true
        }))
        .unwrap(),
    )
    .unwrap();
}

#[test]
fn ten_revisions_keep_ids_and_human_style_and_reject_a_stale_request() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("influence.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = create(&service);
    p = upsert(&service, &p, "legacy-field", config());
    let ids = p.scenes[0].nodes.iter().map(|n| n.id).collect::<Vec<_>>();
    let unrelated = p.scenes[1].clone();
    let node = p.scenes[0].nodes[0].clone();
    p = apply(
        &service,
        &p,
        "human-fill",
        Change::UpdateCanvasStyle {
            scene_id: p.scenes[0].id,
            node_id: node.id,
            style: NodeStyle {
                fill: Some("#EECCAA".into()),
                ..node.style
            },
        },
    );
    let stale = p.stamp();
    for revision in 1..=10 {
        p = upsert(
            &service,
            &p,
            &format!("influence-{revision}"),
            influenced(40 + revision * 4),
        );
        assert_eq!(
            p.production_design.procedural_fields[0].generator_version,
            2
        );
        assert_eq!(p.scenes[0].nodes[1].width, f64::from(40 + revision * 4));
        assert_eq!(
            p.scenes[0].nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
            ids
        );
        assert_eq!(p.scenes[0].nodes[0].style.fill.as_deref(), Some("#EECCAA"));
        assert_eq!(p.scenes[1], unrelated);
        assert_eq!(
            p.production_design.procedural_fields[0].baseline[0]
                .style
                .fill
                .as_deref(),
            Some("#A5C8DF")
        );
    }
    let before_history = service.history(p.id, 0, 256).unwrap();
    assert!(
        service
            .apply(p.id, &stale, "stale-influence", &change(&p, influenced(96)))
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert_eq!(service.history(p.id, 0, 256).unwrap(), before_history);
    close_and_preserve("ten-revisions", service, &path, &p);
}

#[test]
fn invalid_parameters_and_geometric_overflow_preserve_full_sqlite_state() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("influence.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let p = create(&service);
    let invalid = [
        (
            ProceduralConfig {
                influence: Some(ProceduralInfluence {
                    center_x: 299,
                    center_y: 400,
                    radius: 150,
                    target_size: 64,
                }),
                ..config()
            },
            "center",
        ),
        (
            ProceduralConfig {
                influence: Some(ProceduralInfluence {
                    center_x: 600,
                    center_y: 400,
                    radius: 0,
                    target_size: 64,
                }),
                ..config()
            },
            "radius",
        ),
        (
            ProceduralConfig {
                influence: Some(ProceduralInfluence {
                    center_x: 600,
                    center_y: 400,
                    radius: 150,
                    target_size: 129,
                }),
                ..config()
            },
            "target size",
        ),
        (
            ProceduralConfig {
                count: 1,
                columns: 1,
                origin_x: 0,
                origin_y: 0,
                area_width: 32,
                area_height: 32,
                influence: Some(ProceduralInfluence {
                    center_x: 16,
                    center_y: 16,
                    radius: 1,
                    target_size: 128,
                }),
                ..config()
            },
            "influenced object",
        ),
    ];
    for (index, (config, reason)) in invalid.into_iter().enumerate() {
        refuse_unchanged(
            &service,
            &p,
            &format!("invalid-{index}"),
            change(&p, config),
            reason,
        );
    }
    close_and_preserve("invalid-geometry", service, &path, &p);
}

#[test]
fn node_and_project_locks_block_size_influence_without_appending_history() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("influence.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = create(&service);
    p = upsert(&service, &p, "legacy-field", config());
    let node_id = p.scenes[0].nodes[1].id;
    for (index, property) in [NodeProperty::Size, NodeProperty::Position]
        .into_iter()
        .enumerate()
    {
        p = apply(
            &service,
            &p,
            &format!("node-lock-{index}"),
            Change::SetNodePropertyLock {
                scene_id: p.scenes[0].id,
                node_id,
                property,
                locked: true,
            },
        );
        refuse_unchanged(
            &service,
            &p,
            &format!("refused-node-lock-{index}"),
            change(&p, influenced(64)),
            "locked",
        );
        p = apply(
            &service,
            &p,
            &format!("node-unlock-{index}"),
            Change::SetNodePropertyLock {
                scene_id: p.scenes[0].id,
                node_id,
                property,
                locked: false,
            },
        );
    }
    p = apply(
        &service,
        &p,
        "project-position-lock",
        Change::SetLock {
            resource: format!("project:{}", p.id),
            kind: LockKind::Position,
            note: "Approved project geometry".into(),
        },
    );
    refuse_unchanged(
        &service,
        &p,
        "refused-project-lock",
        change(&p, influenced(64)),
        "locked",
    );
    close_and_preserve("locks", service, &path, &p);
}

#[test]
fn branch_checkout_and_disable_restore_the_correct_versioned_field() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("influence.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = create(&service);
    p = upsert(&service, &p, "legacy-field", config());
    let main = p.active_branch;
    let original = p.scenes[0].nodes.clone();
    p = apply(
        &service,
        &p,
        "branch",
        Change::CreateBranch {
            name: "Emphasis alternative".into(),
        },
    );
    let branch = p
        .branches
        .iter()
        .find(|b| b.name == "Emphasis alternative")
        .unwrap()
        .id;
    p = apply(
        &service,
        &p,
        "checkout-alternative",
        Change::CheckoutBranch { branch_id: branch },
    );
    p = upsert(&service, &p, "alternative-influence", influenced(64));
    let alternative = p.scenes[0].nodes.clone();
    assert_ne!(alternative, original);
    p = apply(
        &service,
        &p,
        "checkout-main",
        Change::CheckoutBranch { branch_id: main },
    );
    assert_eq!(p.scenes[0].nodes, original);
    assert_eq!(
        p.production_design.procedural_fields[0].generator_version,
        1
    );
    assert_eq!(
        p.production_design.procedural_fields[0].config.influence,
        None
    );
    p = apply(
        &service,
        &p,
        "checkout-alternative-again",
        Change::CheckoutBranch { branch_id: branch },
    );
    assert_eq!(p.scenes[0].nodes, alternative);
    assert_eq!(
        p.production_design.procedural_fields[0].generator_version,
        2
    );
    p = upsert(&service, &p, "disable-influence", config());
    assert_eq!(p.scenes[0].nodes, original);
    assert_eq!(
        p.production_design.procedural_fields[0].generator_version,
        1
    );
    close_and_preserve("branches-and-disable", service, &path, &p);
}

#[test]
fn conflicting_human_size_is_retained_and_detach_keeps_every_editable_object() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("influence.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = create(&service);
    p = upsert(&service, &p, "influenced-field", influenced(64));
    let node = p.scenes[0].nodes[1].clone();
    p = apply(
        &service,
        &p,
        "human-size",
        Change::TransformCanvasNode {
            scene_id: p.scenes[0].id,
            node_id: node.id,
            transform: CanvasTransform {
                x: node.x,
                y: node.y,
                width: 70.0,
                height: node.height,
                rotation_deg: node.rotation_deg,
                opacity: node.opacity,
            },
        },
    );
    refuse_unchanged(
        &service,
        &p,
        "conflicting-size",
        change(&p, influenced(80)),
        "override conflict",
    );
    let original = p.scenes[0].nodes.clone();
    p = apply(
        &service,
        &p,
        "detach-retaining-work",
        Change::DetachProceduralField { instance_id: id() },
    );
    assert_eq!(p.scenes[0].nodes, original);
    assert!(p.production_design.procedural_fields.is_empty());
    let keep = p.scenes[0].nodes[1].clone();
    p = apply(
        &service,
        &p,
        "independent-fill-after-detach",
        Change::UpdateCanvasStyle {
            scene_id: p.scenes[0].id,
            node_id: keep.id,
            style: NodeStyle {
                fill: Some("#B9E8DC".into()),
                ..keep.style
            },
        },
    );
    assert_eq!(p.scenes[0].nodes[1].width, 70.0);
    assert_eq!(p.scenes[0].nodes[1].style.fill.as_deref(), Some("#B9E8DC"));
    close_and_preserve("conflict-and-detach", service, &path, &p);
}
