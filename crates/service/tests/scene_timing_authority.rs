use motionwright_domain::*;
use motionwright_service::StudioService;

fn time(n: i64, d: i64) -> RationalTime {
    RationalTime::new(n, d).unwrap()
}
fn apply(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}
fn project(service: &StudioService) -> Project {
    let mut project = service
        .create_project("General timing lock SQLite authority")
        .unwrap();
    for (name, duration) in [("A", 7), ("B", 9), ("C", 12)] {
        project = apply(
            service,
            &project,
            &format!("create-{name}"),
            Change::AddScene {
                name: name.into(),
                objective: format!("Explain {name}; preserve the other scenes"),
                duration_seconds: duration,
            },
        );
    }
    project
}

fn preserve_closed_store(
    case: &str,
    source: &std::path::Path,
    project: &Project,
    history: &impl serde::Serialize,
) {
    let Some(root) = std::env::var_os("MOTIONWRIGHT_SCENE_TIMING_EVIDENCE_ROOT") else {
        return;
    };
    let target = std::path::PathBuf::from(root).join(case);
    std::fs::create_dir(&target).expect("each closed-store case has a fresh evidence directory");
    let wal = source.with_file_name(format!(
        "{}-wal",
        source.file_name().unwrap().to_string_lossy()
    ));
    assert!(
        !wal.exists() || wal.metadata().unwrap().len() == 0,
        "all service handles must be closed before preserving the standalone SQLite store"
    );
    std::fs::copy(source, target.join("project.sqlite3")).unwrap();
    std::fs::write(
        target.join("expected-project.json"),
        serde_json::to_vec_pretty(project).unwrap(),
    )
    .unwrap();
    std::fs::write(
        target.join("expected-history.json"),
        serde_json::to_vec_pretty(history).unwrap(),
    )
    .unwrap();
    std::fs::write(
        target.join("capture.json"),
        serde_json::to_vec_pretty(&serde_json::json!({
            "schema": "motionwright.scene-timing-closed-store/1",
            "case": case,
            "project_id": project.id,
            "generation": project.generation,
            "revision": project.revision,
            "captured_after_service_close_and_reopen_assertions": true
        }))
        .unwrap(),
    )
    .unwrap();
}

fn reject_and_reopen(reorder: bool, project_lock: bool) {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("scene-timing.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = project(&service);
    let resource = if project_lock {
        format!("project:{}", project.id)
    } else {
        format!("scene:{}", project.scenes[1].id)
    };
    project = apply(
        &service,
        &project,
        "timing-lock",
        Change::SetLock {
            resource,
            kind: LockKind::Timing,
            note: "Approved timing".into(),
        },
    );
    let before = project.clone();
    let history = service.history(project.id, 0, 256).unwrap();
    let mutation = if reorder {
        Change::MoveScene {
            scene_id: project.scenes[0].id,
            to_index: 1,
        }
    } else {
        Change::SetSceneDuration {
            scene_id: project.scenes[0].id,
            duration: time(8, 1),
        }
    };
    let result = service.apply(
        project.id,
        &project.stamp(),
        "refused-timing-change",
        &mutation,
    );
    assert!(
        result.is_err(),
        "General timing must refuse a change to locked times"
    );
    let error = result.unwrap_err().to_string();
    assert!(
        error.contains("locked"),
        "The refusal must be the intended lock, not a fixture/setup failure: {error}"
    );
    assert_eq!(service.project(project.id).unwrap(), before);
    assert_eq!(service.history(project.id, 0, 256).unwrap(), history);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(before.id).unwrap(), before);
    assert_eq!(reopened.history(before.id, 0, 256).unwrap(), history);
    drop(reopened);
    preserve_closed_store(
        &format!(
            "{}-{}",
            if reorder { "reorder" } else { "duration" },
            if project_lock {
                "project-lock"
            } else {
                "downstream-lock"
            }
        ),
        &path,
        &before,
        &history,
    );
}

#[test]
fn duration_downstream_lock_refuses_without_revision_or_journal_then_reopens() {
    reject_and_reopen(false, false);
}
#[test]
fn reorder_downstream_lock_refuses_without_revision_or_journal_then_reopens() {
    reject_and_reopen(true, false);
}
#[test]
fn duration_project_lock_refuses_without_revision_or_journal_then_reopens() {
    reject_and_reopen(false, true);
}
#[test]
fn reorder_project_lock_refuses_without_revision_or_journal_then_reopens() {
    reject_and_reopen(true, true);
}

#[test]
fn unrelated_locked_timing_and_exact_rational_changes_survive_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("scene-timing-controls.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = project(&service);
    let suffix = project.scenes[2].clone();
    project = apply(
        &service,
        &project,
        "suffix-lock",
        Change::SetLock {
            resource: format!("scene:{}", suffix.id),
            kind: LockKind::Timing,
            note: "Unchanged suffix".into(),
        },
    );
    project = apply(
        &service,
        &project,
        "earlier-reorder",
        Change::MoveScene {
            scene_id: project.scenes[0].id,
            to_index: 1,
        },
    );
    assert_eq!(project.scenes[2], suffix);
    let lock_id = project.locks[0].id;
    project = apply(
        &service,
        &project,
        "explicit-unlock",
        Change::RemoveLock { lock_id },
    );
    let prefix = project.scenes[0].clone();
    project = apply(
        &service,
        &project,
        "prefix-lock",
        Change::SetLock {
            resource: format!("scene:{}", prefix.id),
            kind: LockKind::Timing,
            note: "Unchanged prefix".into(),
        },
    );
    project = apply(
        &service,
        &project,
        "fractional-duration",
        Change::SetSceneDuration {
            scene_id: project.scenes[1].id,
            duration: time(22, 3),
        },
    );
    assert_eq!(project.scenes[0], prefix);
    assert_eq!(project.scenes[2].start, time(49, 3));
    let history = service.history(project.id, 0, 256).unwrap();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(reopened.history(project.id, 0, 256).unwrap(), history);
    drop(reopened);
    preserve_closed_store(
        "unaffected-lock-and-exact-rational",
        &path,
        &project,
        &history,
    );
}

#[test]
fn marker_edits_preserve_identity_locks_and_original_receipts_after_reopen() {
    use motionwright_storage::{APPLY_REQUEST_ENCODING_V1, ApplyReceiptObservation};

    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("markers.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let initial = service.create_project("Editorial markers").unwrap();
    let marked = apply(
        &service,
        &initial,
        "add-editorial-marker",
        Change::AddMarker {
            at: time(1001, 30_000),
            label: "First frame".into(),
        },
    );
    let original = marked.markers[0].clone();
    let locked = apply(
        &service,
        &marked,
        "lock-marker-time",
        Change::SetLock {
            resource: format!("marker:{}", original.id),
            kind: LockKind::Timing,
            note: String::new(),
        },
    );
    let rejected = Change::EditMarker {
        expected: original.clone(),
        at: time(3, 2),
        label: "Timing conflict".into(),
    };
    assert!(
        service
            .apply(locked.id, &locked.stamp(), "blocked-marker-edit", &rejected)
            .is_err()
    );
    assert_eq!(service.project(locked.id).unwrap(), locked);

    let rename = Change::EditMarker {
        expected: original.clone(),
        at: original.at,
        label: "Picture lock".into(),
    };
    let renamed = apply(&service, &locked, "rename-editorial-marker", rename.clone());
    assert_eq!(renamed.markers[0].id, original.id);
    assert_eq!(renamed.markers[0].at, original.at);
    assert!(
        service
            .apply(
                renamed.id,
                &renamed.stamp(),
                "stale-marker-preimage",
                &rename
            )
            .is_err()
    );
    assert_eq!(service.project(renamed.id).unwrap(), renamed);
    let lock_id = renamed.locks[0].id;
    let unlocked = apply(
        &service,
        &renamed,
        "unlock-marker-time",
        Change::RemoveLock { lock_id },
    );
    let moved = apply(
        &service,
        &unlocked,
        "move-editorial-marker",
        Change::EditMarker {
            expected: unlocked.markers[0].clone(),
            at: time(3, 2),
            label: "Picture lock".into(),
        },
    );
    assert_eq!(moved.markers[0].id, original.id);
    let remove = Change::RemoveMarker {
        expected: moved.markers[0].clone(),
    };
    let removed = apply(&service, &moved, "remove-editorial-marker", remove.clone());
    assert!(removed.markers.is_empty());
    assert_eq!(removed.revision, moved.revision + 1);
    drop(service);

    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(removed.id).unwrap(), removed);
    let store = reopened.store();
    let receipt = store
        .lock()
        .observe_apply_receipt_v1(
            moved.id,
            &moved.stamp(),
            "remove-editorial-marker",
            &remove,
            APPLY_REQUEST_ENCODING_V1,
        )
        .unwrap();
    let ApplyReceiptObservation::KnownVerifiedV1 { binding } = receipt else {
        panic!("expected the original marker removal receipt");
    };
    assert_eq!(binding.result_revision, removed.revision.to_string());
    assert_eq!(binding.base_branch, moved.active_branch.to_string());
}
