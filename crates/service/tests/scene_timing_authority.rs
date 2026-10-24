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
}
