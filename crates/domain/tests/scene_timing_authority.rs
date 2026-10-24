use motionwright_domain::*;

fn time(n: i64, d: i64) -> RationalTime {
    RationalTime::new(n, d).unwrap()
}

fn project() -> Project {
    let mut project = Project::new("General scene timing authority").unwrap();
    for (name, duration) in [("A", 7), ("B", 9), ("C", 12)] {
        project
            .apply_change(&Change::AddScene {
                name: name.into(),
                objective: format!("Explain {name} without changing other scenes"),
                duration_seconds: duration,
            })
            .unwrap();
    }
    project
}

fn change(project: &Project, reorder: bool) -> Change {
    if reorder {
        Change::MoveScene {
            scene_id: project.scenes[0].id,
            to_index: 1,
        }
    } else {
        Change::SetSceneDuration {
            scene_id: project.scenes[0].id,
            duration: time(8, 1),
        }
    }
}

fn rejected_change_preserves_every_field(reorder: bool, project_lock: bool) {
    let mut project = project();
    let resource = if project_lock {
        format!("project:{}", project.id)
    } else {
        format!("scene:{}", project.scenes[1].id)
    };
    project
        .apply_change(&Change::SetLock {
            resource,
            kind: LockKind::Timing,
            note: "Approved timing".into(),
        })
        .unwrap();
    let before = project.clone();
    let result = project.apply_change(&change(&before, reorder));
    assert!(
        matches!(result, Err(DomainError::Locked(_))),
        "Expected a timing-lock refusal, got {result:?}"
    );
    assert_eq!(
        project, before,
        "A refused change must preserve the complete caller document"
    );
}

#[test]
fn duration_cannot_shift_a_downstream_timing_lock() {
    rejected_change_preserves_every_field(false, false);
}
#[test]
fn reorder_cannot_shift_a_downstream_timing_lock() {
    rejected_change_preserves_every_field(true, false);
}
#[test]
fn duration_cannot_bypass_a_project_timing_lock() {
    rejected_change_preserves_every_field(false, true);
}
#[test]
fn reorder_cannot_bypass_a_project_timing_lock() {
    rejected_change_preserves_every_field(true, true);
}

#[test]
fn unchanged_locked_prefix_allows_an_unlocked_later_duration() {
    let mut project = project();
    project
        .apply_change(&Change::SetLock {
            resource: format!("scene:{}", project.scenes[0].id),
            kind: LockKind::Timing,
            note: "Prefix".into(),
        })
        .unwrap();
    let prefix = project.scenes[0].clone();
    project
        .apply_change(&Change::SetSceneDuration {
            scene_id: project.scenes[1].id,
            duration: time(10, 1),
        })
        .unwrap();
    assert_eq!(project.scenes[0], prefix);
    assert_eq!(project.scenes[2].start, time(17, 1));
}

#[test]
fn unchanged_locked_suffix_allows_an_earlier_reorder() {
    let mut project = project();
    project
        .apply_change(&Change::SetLock {
            resource: format!("scene:{}", project.scenes[2].id),
            kind: LockKind::Timing,
            note: "Suffix".into(),
        })
        .unwrap();
    let before = project.clone();
    project.apply_change(&change(&before, true)).unwrap();
    assert_eq!(
        project
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>(),
        vec![
            before.scenes[1].id,
            before.scenes[0].id,
            before.scenes[2].id
        ]
    );
    assert_eq!(project.scenes[2], before.scenes[2]);
}

#[test]
fn fractional_duration_and_reorder_keep_exact_starts() {
    let mut project = project();
    project
        .apply_change(&Change::SetSceneDuration {
            scene_id: project.scenes[0].id,
            duration: time(22, 3),
        })
        .unwrap();
    assert_eq!(project.scenes[1].start, time(22, 3));
    assert_eq!(project.scenes[2].start, time(49, 3));
    let mutation = change(&project, true);
    project.apply_change(&mutation).unwrap();
    assert_eq!(project.scenes[1].start, time(9, 1));
    assert_eq!(project.scenes[2].start, time(49, 3));
}
