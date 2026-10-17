use motionwright_domain::Project;
use motionwright_service::StudioService;
use motionwright_storage::StorageError;
use std::sync::{Arc, Barrier};
use std::thread;
use uuid::Uuid;

// Baseline adapter: the existing service API cannot accept the retained
// operation UUID. This does not emulate a receipt or deduplicate by title.
mod adapter {
    use super::{Project, StorageError, StudioService};
    use uuid::Uuid;

    pub struct Intent {
        pub request_id: Uuid,
        pub title: String,
    }

    pub struct Outcome {
        pub request_id: Uuid,
        pub project: Project,
        pub replayed: bool,
    }

    pub fn create(service: &StudioService, intent: &Intent) -> Result<Outcome, StorageError> {
        let project = service.create_project(&intent.title)?;
        Ok(Outcome {
            request_id: intent.request_id,
            project,
            replayed: false,
        })
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
