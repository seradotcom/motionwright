use motionwright_domain::Project;
use motionwright_service::StudioService;
use motionwright_storage::StorageError;
use std::sync::{Arc, Barrier};
use std::thread;
use uuid::Uuid;

// Both revisions use the same fixtures and assertions. Only this adapter
// changes from the old title-only API to the durable-intent API.
mod adapter {
    use motionwright_service::StudioService;
    pub use motionwright_service::{
        ProjectCreationIntent as Intent, ProjectCreationOutcome as Outcome,
    };
    use motionwright_storage::StorageError;

    pub fn create(service: &StudioService, intent: &Intent) -> Result<Outcome, StorageError> {
        service.create_project_with_intent(intent)
    }
}

fn intent(request_id: Uuid, title: &str) -> adapter::Intent {
    adapter::Intent {
        request_id,
        title: title.into(),
    }
}

fn assert_single_project(service: &StudioService, expected: &Project) {
    let projects = service.projects(100).unwrap();
    assert_eq!(
        projects.len(),
        1,
        "duplicate delivery must not persist another project row"
    );
    assert_eq!(projects[0], *expected);
    assert_eq!(service.project(expected.id).unwrap(), *expected);
}

fn assert_new_project(outcome: &adapter::Outcome, request_id: Uuid, title: &str) {
    assert_eq!(outcome.request_id, request_id);
    assert_eq!(outcome.project.title, title);
    assert_eq!(outcome.project.revision, 0);
    assert!(!outcome.replayed);
    outcome.project.validate().unwrap();
}

#[test]
fn duplicate_delivery_preserves_one_project_and_its_original_result() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("state.sqlite3")).unwrap();
    let request = intent(Uuid::now_v7(), "One durable creation");
    let first = adapter::create(&service, &request).unwrap();
    assert_new_project(&first, request.request_id, &request.title);
    let repeated = adapter::create(&service, &request).unwrap();
    // Check actual persisted state before any replay metadata.
    assert_single_project(&service, &first.project);
    assert_eq!(repeated.request_id, request.request_id);
    assert_eq!(repeated.project, first.project);
    assert!(repeated.replayed);
}

#[test]
fn duplicate_delivery_after_reopen_preserves_the_original_identity() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("state.sqlite3");
    let request_id = Uuid::now_v7();
    let title = "Creation across service reopen";
    let service = StudioService::open(&database).unwrap();
    let first = adapter::create(&service, &intent(request_id, title)).unwrap();
    assert_new_project(&first, request_id, title);
    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    let repeated = adapter::create(&reopened, &intent(request_id, title)).unwrap();
    assert_single_project(&reopened, &first.project);
    assert_eq!(repeated.request_id, request_id);
    assert_eq!(repeated.project, first.project);
    assert!(repeated.replayed);
    drop(reopened);
    let reopened_again = StudioService::open(&database).unwrap();
    assert_single_project(&reopened_again, &first.project);
}

#[test]
fn changing_title_for_the_same_operation_rejects_request_reuse() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("state.sqlite3")).unwrap();
    let request_id = Uuid::now_v7();
    let first = adapter::create(&service, &intent(request_id, "Original creation title")).unwrap();
    let changed = adapter::create(
        &service,
        &intent(request_id, "Different input for the same operation"),
    );
    assert_single_project(&service, &first.project);
    assert!(matches!(changed, Err(StorageError::RequestReuse)));
    let repeated =
        adapter::create(&service, &intent(request_id, "Original creation title")).unwrap();
    assert_eq!(repeated.project, first.project);
    assert_eq!(repeated.request_id, request_id);
    assert!(repeated.replayed);
    assert_single_project(&service, &first.project);
}

#[test]
fn distinct_operations_with_the_same_title_create_distinct_valid_projects() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("state.sqlite3")).unwrap();
    let first_id = Uuid::now_v7();
    let second_id = Uuid::now_v7();
    let title = "A title is not an operation identity";
    assert_ne!(first_id, second_id);
    let first = adapter::create(&service, &intent(first_id, title)).unwrap();
    let second = adapter::create(&service, &intent(second_id, title)).unwrap();
    assert_new_project(&first, first_id, title);
    assert_new_project(&second, second_id, title);
    assert_ne!(first.project.id, second.project.id);
    let projects = service.projects(100).unwrap();
    assert_eq!(projects.len(), 2);
    assert!(
        projects
            .iter()
            .any(|project| project.id == first.project.id)
    );
    assert!(
        projects
            .iter()
            .any(|project| project.id == second.project.id)
    );
    assert_eq!(service.project(first.project.id).unwrap(), first.project);
    assert_eq!(service.project(second.project.id).unwrap(), second.project);
}

#[test]
fn concurrent_delivery_through_two_connections_has_one_committed_creation() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("state.sqlite3");
    // Initialize sequentially to isolate the creation race from migrations.
    let left_service = StudioService::open(&database).unwrap();
    let right_service = StudioService::open(&database).unwrap();
    let request_id = Uuid::now_v7();
    let title = "One operation through two clients";
    let left_intent = intent(request_id, title);
    let right_intent = intent(request_id, title);
    let barrier = Arc::new(Barrier::new(3));
    let left_barrier = Arc::clone(&barrier);
    let left = thread::spawn(move || {
        left_barrier.wait();
        adapter::create(&left_service, &left_intent)
    });
    let right_barrier = Arc::clone(&barrier);
    let right = thread::spawn(move || {
        right_barrier.wait();
        adapter::create(&right_service, &right_intent)
    });
    barrier.wait();
    let left = left
        .join()
        .expect("left client must finish")
        .expect("identical concurrent intent must resolve successfully");
    let right = right
        .join()
        .expect("right client must finish")
        .expect("identical concurrent intent must resolve successfully");
    let reopened = StudioService::open(&database).unwrap();
    assert_single_project(&reopened, &left.project);
    assert_eq!(right.project, left.project);
    assert_eq!(left.request_id, request_id);
    assert_eq!(right.request_id, request_id);
    assert_eq!(left.project.title, title);
    assert_eq!(left.project.revision, 0);
    left.project.validate().unwrap();
    assert_eq!(
        [left.replayed, right.replayed]
            .into_iter()
            .filter(|replayed| !replayed)
            .count(),
        1,
        "exactly one client must perform the creation"
    );
    assert_eq!(
        [left.replayed, right.replayed]
            .into_iter()
            .filter(|replayed| *replayed)
            .count(),
        1,
        "the other client must receive the original committed result"
    );
}

#[cfg(test)]
mod readonly_recovery {
    use motionwright_domain::Change;
    use motionwright_service::{ProjectCreationIntent, StudioService};
    use std::{fs, fs::OpenOptions, io::Write, path::Path};
    use uuid::Uuid;

    fn persist_intent(path: &Path, intent: &ProjectCreationIntent) -> Vec<u8> {
        let bytes = serde_json::to_vec(intent).unwrap();
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path).unwrap();
        file.write_all(&bytes).unwrap();
        file.sync_all().unwrap();
        drop(file);
        assert_eq!(fs::read(path).unwrap(), bytes);
        bytes
    }

    fn reload_intent(path: &Path) -> ProjectCreationIntent {
        serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
    }

    #[test]
    fn recovery_uses_persisted_intent_and_retains_the_original_creation_result() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let pending_path = directory.path().join("pending-create.json");
        let request_id = Uuid::now_v7();
        let intent = ProjectCreationIntent {
            request_id,
            title: "Original creation title".into(),
        };
        let pending_bytes = persist_intent(&pending_path, &intent);
        drop(intent);
        let dispatched = reload_intent(&pending_path);
        let service = StudioService::open(&database).unwrap();
        // Simulate withholding a committed response from the client. The
        // harness reads the DB to establish its oracle independently.
        let _ = service.create_project_with_intent(&dispatched).unwrap();
        let projects = service.projects(100).unwrap();
        assert_eq!(projects.len(), 1);
        let original = projects[0].clone();
        assert_eq!(original.title, "Original creation title");
        assert_eq!(original.revision, 0);
        drop(dispatched);
        drop(service);

        let editor = StudioService::open(&database).unwrap();
        let edited = editor
            .apply(
                original.id,
                &original.stamp(),
                "edit-after-project-creation",
                &Change::RenameProject {
                    title: "Title from a later edit".into(),
                },
            )
            .unwrap()
            .project;
        drop(editor);
        let checkpoint = reload_intent(&pending_path);
        assert_eq!(checkpoint.request_id, request_id);
        let recovery = StudioService::open(&database).unwrap();
        let before = recovery.projects(100).unwrap();
        assert_eq!(before, vec![edited.clone()]);
        for _ in 0..2 {
            let outcome = recovery
                .recover_project_creation(&checkpoint)
                .unwrap()
                .unwrap();
            assert_eq!(outcome.request_id, request_id);
            assert_eq!(outcome.project, original);
            assert!(outcome.replayed);
            assert_eq!(recovery.projects(100).unwrap(), before);
            assert_eq!(recovery.project(original.id).unwrap(), edited);
            assert_eq!(fs::read(&pending_path).unwrap(), pending_bytes);
        }
        drop(recovery);
        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(reopened.projects(100).unwrap(), before);
    }

    #[test]
    fn absence_neither_creates_nor_adopts_a_same_title_project() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let pending_path = directory.path().join("pending-create.json");
        let title = "A shared title is not a creation receipt";
        let service = StudioService::open(&database).unwrap();
        let unrelated = service.create_project(title).unwrap();
        let intent = ProjectCreationIntent {
            request_id: Uuid::now_v7(),
            title: title.into(),
        };
        let pending_bytes = persist_intent(&pending_path, &intent);
        let before = service.projects(100).unwrap();
        assert_eq!(before, vec![unrelated.clone()]);
        drop(intent);
        for _ in 0..2 {
            let checkpoint = reload_intent(&pending_path);
            assert!(
                service
                    .recover_project_creation(&checkpoint)
                    .unwrap()
                    .is_none()
            );
            assert_eq!(service.projects(100).unwrap(), before);
            assert_eq!(fs::read(&pending_path).unwrap(), pending_bytes);
        }
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        assert!(
            reopened
                .recover_project_creation(&reload_intent(&pending_path))
                .unwrap()
                .is_none()
        );
        assert_eq!(reopened.projects(100).unwrap(), before);
        assert_eq!(reopened.project(unrelated.id).unwrap(), unrelated);
    }
}
