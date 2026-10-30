//! Physical SQLite tests for read-only, source-bound domain previews.
use motionwright_domain::{Change, DomainError, LockKind, Project, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_storage::StorageError;
use rusqlite::{Connection, OpenFlags};
use std::path::Path;
use uuid::Uuid;

fn monitor(path: &Path) -> Connection {
    Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap()
}
fn data_version(db: &Connection) -> i64 {
    db.query_row("PRAGMA data_version", [], |row| row.get(0))
        .unwrap()
}
fn receipts(db: &Connection, id: Uuid) -> i64 {
    db.query_row(
        "SELECT count(*) FROM receipts WHERE project_id=?1",
        [id.to_string()],
        |row| row.get(0),
    )
    .unwrap()
}
fn rename(title: &str) -> Change {
    Change::RenameProject {
        title: title.into(),
    }
}
fn scene() -> Change {
    Change::AddScene {
        name: "Proposed scene".into(),
        objective: "Preview does not publish this scene".into(),
        duration_seconds: 3,
    }
}
fn unchanged(service: &StudioService, project: &Project, db: &Connection, version: i64) {
    assert_eq!(service.project(project.id).unwrap(), *project);
    assert_eq!(
        data_version(db),
        version,
        "preview wrote to physical SQLite"
    );
}

#[test]
fn rename_and_scene_preview_do_not_write_sqlite_journal_receipts_or_future_ids() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("preview.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Original title").unwrap();
    let db = monitor(&path);
    let version = data_version(&db);
    let before = service.export_project(project.id).unwrap();
    let receipt_count = receipts(&db, project.id);

    let preview = service
        .preview_change(project.id, &project.stamp(), &rename("Proposed title"))
        .unwrap();
    assert_eq!(preview.base, project.stamp());
    assert_eq!(preview.candidate_title, "Proposed title");
    assert_eq!(preview.candidate_scene_count, 0);
    assert!(!preview.committed);
    assert!(!preview.exact_future_ids_proven);

    for _ in 0..2 {
        let preview = service
            .preview_change(project.id, &project.stamp(), &scene())
            .unwrap();
        assert_eq!(preview.base, project.stamp());
        assert_eq!(preview.candidate_title, project.title);
        assert_eq!(preview.candidate_scene_count, 1);
        assert!(!preview.committed);
        assert!(!preview.exact_future_ids_proven);
        let wire = serde_json::to_value(&preview).unwrap();
        assert!(wire.get("request_id").is_none());
        assert!(wire.get("recovery").is_none());
        assert!(wire.get("project").is_none());
    }
    unchanged(&service, &project, &db, version);
    let after = service.export_project(project.id).unwrap();
    assert_eq!(after.events, before.events);
    assert_eq!(receipts(&db, project.id), receipt_count);
    drop(db);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(
        reopened.export_project(project.id).unwrap().events,
        before.events
    );
}

#[test]
fn preview_rejects_stale_revision_generation_foreign_resource_and_missing_project() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("identity.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Observed").unwrap();
    let db = monitor(&path);
    let version = data_version(&db);
    let change = rename("Unapplied");

    let mut expected = project.stamp();
    expected.revision += 1;
    assert!(matches!(
        service.preview_change(project.id, &expected, &change),
        Err(StorageError::Conflict {
            expected: 1,
            actual: 0
        })
    ));
    let mut expected = project.stamp();
    expected.generation = Uuid::now_v7();
    assert!(matches!(
        service.preview_change(project.id, &expected, &change),
        Err(StorageError::GenerationConflict)
    ));
    let mut expected = project.stamp();
    expected.resource = format!("project:{}", Uuid::now_v7());
    assert!(matches!(
        service.preview_change(project.id, &expected, &change),
        Err(StorageError::GenerationConflict)
    ));
    assert!(matches!(
        service.preview_change(Uuid::now_v7(), &project.stamp(), &change),
        Err(StorageError::NotFound)
    ));
    unchanged(&service, &project, &db, version);
    assert_eq!(receipts(&db, project.id), 0);
}

#[test]
fn rejected_domain_preview_leaves_physical_store_unchanged() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("invalid.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Original").unwrap();
    let db = monitor(&path);
    let version = data_version(&db);
    let invalid = Change::AddScene {
        name: "Invalid duration".into(),
        objective: String::new(),
        duration_seconds: 0,
    };
    for change in [rename("   "), invalid] {
        assert!(matches!(
            service.preview_change(project.id, &project.stamp(), &change),
            Err(StorageError::Domain(DomainError::Invalid(_)))
        ));
    }
    unchanged(&service, &project, &db, version);
    assert_eq!(receipts(&db, project.id), 0);
}

#[test]
fn preview_preserves_existing_domain_locks_without_publishing_a_partial_candidate() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("locked.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Locked scene").unwrap();
    let project = service
        .apply(project.id, &project.stamp(), "seed-scene", &scene())
        .unwrap()
        .project;
    let scene_id = project.scenes[0].id;
    let project = service
        .apply(
            project.id,
            &project.stamp(),
            "lock-scene",
            &Change::SetLock {
                resource: format!("scene:{scene_id}"),
                kind: LockKind::Timing,
                note: "Keep authored duration".into(),
            },
        )
        .unwrap()
        .project;
    let db = monitor(&path);
    let version = data_version(&db);
    let receipt_count = receipts(&db, project.id);
    assert!(matches!(
        service.preview_change(
            project.id,
            &project.stamp(),
            &Change::SetSceneDuration {
                scene_id,
                duration: motionwright_domain::RationalTime::new(4, 1).unwrap(),
            }
        ),
        Err(StorageError::Domain(DomainError::Locked(_)))
    ));
    unchanged(&service, &project, &db, version);
    assert_eq!(receipts(&db, project.id), receipt_count);
}

#[test]
fn two_connections_preview_then_commit_keeps_cas_and_receipts_after_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("concurrent.sqlite3");
    let first = StudioService::open(&path).unwrap();
    let original = first.create_project("Original").unwrap();
    let second = StudioService::open(&path).unwrap();
    let proposed = scene();
    let preview = first
        .preview_change(original.id, &original.stamp(), &proposed)
        .unwrap();
    let current = second
        .apply(
            original.id,
            &original.stamp(),
            "other-client-rename",
            &rename("Concurrent title"),
        )
        .unwrap()
        .project;

    assert!(matches!(
        first.preview_change(original.id, &preview.base, &proposed),
        Err(StorageError::Conflict { .. })
    ));
    assert!(matches!(
        first.apply(
            original.id,
            &preview.base,
            "preview-is-not-authority",
            &proposed
        ),
        Err(StorageError::Conflict { .. })
    ));
    let refreshed = first
        .preview_change(current.id, &current.stamp(), &proposed)
        .unwrap();
    assert_eq!(refreshed.candidate_title, "Concurrent title");
    let applied = first
        .apply(current.id, &refreshed.base, "fresh-scene-commit", &proposed)
        .unwrap();
    assert_eq!(applied.project.revision, 2);
    assert_eq!(applied.project.title, "Concurrent title");
    assert_eq!(applied.project.scenes.len(), 1);
    drop(first);
    drop(second);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(original.id).unwrap(), applied.project);
    let replay = reopened
        .apply(
            original.id,
            &RevisionStamp::from(&current),
            "fresh-scene-commit",
            &proposed,
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, applied.project);
    let db = monitor(&path);
    assert_eq!(receipts(&db, original.id), 2);
    assert_eq!(reopened.history(original.id, 0, 10).unwrap().len(), 2);
}
