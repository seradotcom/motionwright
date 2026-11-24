use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;
fn time(n: i64, d: i64) -> RationalTime {
    RationalTime::new(n, d).unwrap()
}
fn apply(service: &StudioService, project: &Project, key: &str, change: &Change) -> Project {
    service
        .apply(project.id, &project.stamp(), key, change)
        .unwrap()
        .project
}
fn command(project: &Project, before: Vec<Beat>, after: Vec<Beat>) -> Change {
    Change::EditSceneBeatTimeline {
        expected: NarrativeEditSource {
            project_id: project.id,
            generation: project.generation,
            branch_id: project.active_branch,
            base_revision: project.revision,
        },
        scene_id: project.scenes[0].id,
        expected_duration: project.scenes[0].duration,
        expected_beats: before,
        beats: after,
        rationale: "One reviewed split".into(),
    }
}
#[test]
fn split_preview_single_journal_entry_reopen_conditional_inverse_and_refusal_preserve_store() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("beat-timeline.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = service.create_project("Saved editable beat cuts").unwrap();
    project = apply(
        &service,
        &project,
        "scene",
        &Change::AddScene {
            name: "Original".into(),
            objective: "Retain source".into(),
            duration_seconds: 6,
        },
    );
    let original = Beat {
        id: Uuid::now_v7(),
        label: "Whole source".into(),
        objective: "Exact source text".into(),
        start: time(0, 1),
        duration: time(6, 1),
    };
    project = apply(
        &service,
        &project,
        "beat",
        &Change::UpsertSceneBeat {
            scene_id: project.scenes[0].id,
            beat: original.clone(),
        },
    );
    let before = project.clone();
    let mut left = original.clone();
    left.duration = time(1, 3);
    let right = Beat {
        id: Uuid::now_v7(),
        start: time(1, 3),
        duration: time(17, 3),
        ..original.clone()
    };
    let change = command(&project, vec![original.clone()], vec![left, right]);
    let preview = service
        .preview_change(project.id, &project.stamp(), &change)
        .unwrap();
    assert!(!preview.committed);
    assert_eq!(service.project(project.id).unwrap(), before);
    assert!(
        service
            .history(project.id, before.revision, 8)
            .unwrap()
            .is_empty()
    );
    project = apply(&service, &project, "split-once", &change);
    assert_eq!(project.revision, before.revision + 1);
    assert_eq!(
        service
            .history(project.id, before.revision, 8)
            .unwrap()
            .len(),
        1
    );
    drop(service);
    let service = StudioService::open(&path).unwrap();
    assert_eq!(service.project(project.id).unwrap(), project);
    let saved = project.clone();
    project = apply(
        &service,
        &project,
        "later-title",
        &Change::RenameProject {
            title: "Keep this title".into(),
        },
    );
    let inverse = command(&project, saved.scenes[0].beats.clone(), vec![original]);
    project = apply(&service, &project, "conditional-undo", &inverse);
    assert_eq!(project.scenes[0].beats, before.scenes[0].beats);
    assert_eq!(project.title, "Keep this title");
    let history = service.history(project.id, before.revision, 8).unwrap();
    assert_eq!(history.len(), 3);
    let invalid = command(
        &project,
        saved.scenes[0].beats.clone(),
        before.scenes[0].beats.clone(),
    );
    assert!(
        service
            .apply(project.id, &project.stamp(), "stale-source", &invalid)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(
        service.history(project.id, before.revision, 8).unwrap(),
        history
    );
    drop(service);
    let service = StudioService::open(&path).unwrap();
    assert_eq!(service.project(project.id).unwrap(), project);
}
