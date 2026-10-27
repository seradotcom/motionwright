// Compile the actual desktop boundaries against the real service and SQLite.
// These fixture grants do not claim operator consent or installed Broker authority.
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
pub mod effect_grants;
#[path = "../../../apps/desktop/src-tauri/src/local_error.rs"]
pub mod local_error;
#[path = "../../../apps/desktop/src-tauri/src/project_export.rs"]
pub mod project_export;
#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
pub mod project_library;

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
