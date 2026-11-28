// Compile the actual desktop boundaries against the real service and SQLite.
// These fixture grants do not claim operator consent or installed Broker authority.
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
pub mod effect_grants;
#[path = "../../../apps/desktop/src-tauri/src/file_import.rs"]
pub mod file_import;
#[path = "../../../apps/desktop/src-tauri/src/local_error.rs"]
pub mod local_error;
#[path = "../../../apps/desktop/src-tauri/src/project_export.rs"]
pub mod project_export;
#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
pub mod project_library;

#[path = "support/legacy_v5_fixture.rs"]
mod legacy_v5_fixture;

#[test]
fn damaged_project_bootstrap_reports_a_safe_error_and_preserves_both_records() {
    use motionwright_service::StudioService;
    use project_library::{
        BootstrapProjectSelection, BootstrapRequest, ListProjectsRequest, OpenProjectRequest,
        bootstrap_project, list_projects, open_project,
    };

    let directory = tempfile::tempdir().unwrap();
    let seed = directory.path().join("seed/library.sqlite3");
    std::fs::create_dir(seed.parent().unwrap()).unwrap();
    let service = StudioService::open(&seed).unwrap();
    let damaged = service.create_project("Damaged fixture").unwrap();
    let healthy = service.create_project("Healthy fixture").unwrap();
    let healthy_before = service.export_project(healthy.id).unwrap();
    drop(service);

    let path = directory.path().join("historical/library.sqlite3");
    let corrupt = serde_json::to_string("MW_PRIVATE_LIBRARY_SENTINEL not found").unwrap();
    legacy_v5_fixture::from_closed_seed(&seed, &path, |connection| {
        connection
            .execute(
                "UPDATE projects SET document_json=?1 WHERE id=?2",
                rusqlite::params![&corrupt, damaged.id.to_string()],
            )
            .unwrap();
    });
    let service = StudioService::open(&path).unwrap();
    let error = open_project(
        &service,
        OpenProjectRequest {
            project_id: damaged.id,
        },
    )
    .unwrap_err();
    assert!(!error.contains("MW_PRIVATE_LIBRARY_SENTINEL"));
    assert_eq!(
        error,
        "The stored project could not be opened: invalid project data"
    );
    let (opened, selection) = bootstrap_project(
        &service,
        BootstrapRequest {
            project_id: Some(damaged.id),
        },
    );
    assert!(opened.is_none());
    assert!(
        matches!(selection, BootstrapProjectSelection::Unavailable { project_id, message }
        if project_id == damaged.id && message == error)
    );
    let page = list_projects(
        &service,
        serde_json::from_str::<ListProjectsRequest>("{}").unwrap(),
    )
    .unwrap();
    assert_eq!(page.items.len(), 2);
    let database =
        rusqlite::Connection::open_with_flags(&path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    assert_eq!(
        database
            .query_row(
                "SELECT document_json FROM projects WHERE id=?1",
                [damaged.id.to_string()],
                |row| row.get::<_, String>(0)
            )
            .unwrap(),
        corrupt
    );
    assert_eq!(
        service.export_project(healthy.id).unwrap().payload_sha256,
        healthy_before.payload_sha256
    );
}

#[test]
fn malformed_bundle_metadata_is_not_reflected_in_desktop_errors() {
    let directory = tempfile::tempdir().unwrap();
    let service =
        motionwright_service::StudioService::open(directory.path().join("project.sqlite3"))
            .unwrap();
    let project = service
        .create_project("Private bundle error boundary")
        .unwrap();
    let before = service.project(project.id).unwrap();
    let bundle = directory.path().join("malformed-bundle");
    std::fs::create_dir(&bundle).unwrap();

    let sentinel = "MW_PRIVATE_METADATA_SENTINEL";
    let manifest = serde_json::to_vec(&format!("{sentinel} not found")).unwrap();
    let manifest_path = bundle.join("manifest.json");
    std::fs::write(&manifest_path, &manifest).unwrap();

    let error = match service.inspect_project_bundle_with_fingerprint(&bundle) {
        Err(error) => error,
        Ok(_) => panic!("a string cannot be a project bundle manifest"),
    };
    // Require the actual parser-reflection path, not an unrelated rejection.
    assert!(matches!(
        &error,
        motionwright_storage::StorageError::Serde(_)
    ));
    let original = error.to_string();
    assert!(original.contains(sentinel));
    assert!(original.contains("not found"));

    let presented = local_error::sanitized(&error);
    assert_eq!(presented, "invalid project data");
    assert!(!presented.contains(sentinel));
    assert_ne!(presented, original);

    // Presentation must not repair, rewrite, or import the rejected source.
    assert_eq!(std::fs::read(manifest_path).unwrap(), manifest);
    assert_eq!(service.project(project.id).unwrap(), before);
}
