use motionwright_domain::*;
use uuid::Uuid;
fn time(n: i64, d: i64) -> RationalTime {
    RationalTime::new(n, d).unwrap()
}
fn source() -> Project {
    let mut project = Project::new("Exact reversible beat cuts").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Editorial scene".into(),
            objective: "Keep the scene clock".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    for index in 0..3 {
        project
            .apply_change(&Change::UpsertSceneBeat {
                scene_id,
                beat: Beat {
                    id: Uuid::now_v7(),
                    label: format!("Part {index}"),
                    objective: "Original author text".into(),
                    start: time(index * 2, 1),
                    duration: time(2, 1),
                },
            })
            .unwrap();
    }
    project
}
fn change(project: &Project, beats: Vec<Beat>) -> Change {
    let scene = &project.scenes[0];
    Change::EditSceneBeatTimeline {
        expected: NarrativeEditSource {
            project_id: project.id,
            generation: project.generation,
            branch_id: project.active_branch,
            base_revision: project.revision,
        },
        scene_id: scene.id,
        expected_duration: scene.duration,
        expected_beats: scene.beats.clone(),
        beats,
        rationale: "Split and retain exact editable timing".into(),
    }
}
fn split(project: &Project) -> Change {
    let mut beats = project.scenes[0].beats.clone();
    let mut right = beats[0].clone();
    right.id = Uuid::now_v7();
    right.start = time(1, 3);
    right.duration = time(5, 3);
    beats[0].duration = time(1, 3);
    beats.insert(1, right);
    change(project, beats)
}
#[test]
fn exact_split_serializes_and_conditional_inverse_preserves_later_content_and_draft_status() {
    let mut project = source();
    project.scenes[0].status = SceneStatus::Approved;
    let original = project.clone();
    let operation = split(&project);
    let encoded = serde_json::to_vec(&operation).unwrap();
    let decoded: Change = serde_json::from_slice(&encoded).unwrap();
    project.apply_change(&decoded).unwrap();
    assert_eq!(project.revision, original.revision + 1);
    assert_eq!(
        project.scenes[0].beats[0].id,
        original.scenes[0].beats[0].id
    );
    assert_eq!(project.scenes[0].beats[0].duration, time(1, 3));
    assert_eq!(project.scenes[0].beats[1].start, time(1, 3));
    assert_eq!(project.scenes[0].beats[1].duration, time(5, 3));
    assert_eq!(project.scenes[0].nodes, original.scenes[0].nodes);
    assert_eq!(project.scenes[0].duration, original.scenes[0].duration);
    assert_eq!(project.scenes[0].status, SceneStatus::Draft);
    project
        .apply_change(&Change::RenameProject {
            title: "Later unrelated title".into(),
        })
        .unwrap();
    let inverse = change(&project, original.scenes[0].beats.clone());
    project.apply_change(&inverse).unwrap();
    assert_eq!(project.title, "Later unrelated title");
    assert_eq!(project.scenes[0].beats, original.scenes[0].beats);
    assert_eq!(project.scenes[0].status, SceneStatus::Draft);
}
#[test]
fn all_content_timing_locks_stale_identity_and_changed_source_are_atomic() {
    for scope in ["project", "scene"] {
        for kind in [LockKind::Content, LockKind::Timing] {
            let mut project = source();
            let resource = if scope == "project" {
                project.resource_key()
            } else {
                format!("scene:{}", project.scenes[0].id)
            };
            project
                .apply_change(&Change::SetLock {
                    resource,
                    kind,
                    note: "Retained lock".into(),
                })
                .unwrap();
            let before = project.clone();
            let operation = split(&project);
            assert!(matches!(
                project.apply_change(&operation),
                Err(DomainError::Locked(_))
            ));
            assert_eq!(project, before);
        }
    }
    for field in 0..5 {
        let mut project = source();
        let mut operation = split(&project);
        if let Change::EditSceneBeatTimeline {
            expected,
            expected_duration,
            expected_beats,
            ..
        } = &mut operation
        {
            match field {
                0 => expected.base_revision += 1,
                1 => expected.branch_id = Uuid::now_v7(),
                2 => expected.generation = Uuid::now_v7(),
                3 => *expected_duration = time(7, 1),
                _ => expected_beats[0].objective = "Other source".into(),
            }
        }
        let before = project.clone();
        assert!(project.apply_change(&operation).is_err());
        assert_eq!(project, before);
    }
}
#[test]
fn malformed_timeline_rejects_overlap_duplicate_foreign_identity_and_noncanonical_order() {
    for case in 0..4 {
        let mut project = source();
        if case == 2 {
            project
                .apply_change(&Change::AddScene {
                    name: "Other scene".into(),
                    objective: "Preserve".into(),
                    duration_seconds: 6,
                })
                .unwrap();
            let mut other = project.scenes[0].beats[0].clone();
            other.id = Uuid::now_v7();
            project
                .apply_change(&Change::UpsertSceneBeat {
                    scene_id: project.scenes[1].id,
                    beat: other,
                })
                .unwrap();
        }
        let mut beats = project.scenes[0].beats.clone();
        match case {
            0 => beats[1].start = time(1, 1),
            1 => beats[1].id = beats[0].id,
            2 => beats[1].id = project.scenes[1].beats[0].id,
            _ => beats.swap(0, 1),
        }
        let operation = change(&project, beats);
        let before = project.clone();
        assert!(project.apply_change(&operation).is_err());
        assert_eq!(project, before);
    }
}
#[test]
fn inverse_requires_the_exact_whole_current_timeline() {
    let mut project = source();
    let before = project.clone();
    let operation = split(&project);
    project.apply_change(&operation).unwrap();
    let mut inverse = change(&project, before.scenes[0].beats.clone());
    let mut changed = project.scenes[0].beats[1].clone();
    changed.objective = "New editorial content".into();
    project
        .apply_change(&Change::UpsertSceneBeat {
            scene_id: project.scenes[0].id,
            beat: changed,
        })
        .unwrap();
    if let Change::EditSceneBeatTimeline { expected, .. } = &mut inverse {
        expected.base_revision = project.revision;
    }
    let current = project.clone();
    assert!(
        project
            .apply_change(&inverse)
            .unwrap_err()
            .to_string()
            .contains("conditional source")
    );
    assert_eq!(project, current);
}

#[test]
fn split_cannot_shorten_an_existing_editorial_minimum_hold() {
    let mut project = source();
    project
        .apply_change(&Change::AddNarrativeBeat {
            label: "Keep reading time".into(),
            objective: "Explain the original source".into(),
            audience_takeaway: "Understand the evidence".into(),
            claim_ids: vec![],
            preferred_duration: None,
        })
        .unwrap();
    let beat_id = project.narrative.beats[0].id;
    let mut editing = NarrativeEditingState::default();
    editing.bindings.push(NarrativeRealizationBinding {
        id: Uuid::now_v7(),
        beat_id,
        target: NarrativeRealizationTarget::SceneBeat {
            scene_id: project.scenes[0].id,
            scene_beat_id: project.scenes[0].beats[0].id,
        },
    });
    editing.minimum_holds.push(NarrativeMinimumHold {
        beat_id,
        duration: time(1, 1),
    });
    project.narrative.editing = Some(editing);
    project.validate().unwrap();
    let before = project.clone();
    let operation = split(&project);
    let error = project.apply_change(&operation).unwrap_err().to_string();
    assert!(
        error.contains("hold") || error.contains("minimum"),
        "{error}"
    );
    assert_eq!(project, before);
}
