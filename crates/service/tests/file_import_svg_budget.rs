//! The existing SVG admission budget must apply before capture, not after copying.
use motionwright_service::StudioService;
use motionwright_storage::StorageError;
use rusqlite::Connection;
use std::{fs, path::Path};

const SVG_LIMIT: u64 = 8 * 1024 * 1024;

fn assert_rejected_before_capture(name: &str, media_type: &str) {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("state.sqlite3");
    let source = directory.path().join(name);
    fs::File::create(&source)
        .unwrap()
        .set_len(SVG_LIMIT + 1)
        .unwrap();
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("SVG budget before capture").unwrap();
    let before = service.export_project(project.id).unwrap();
    let blob_root = service.store().lock().blob_root().to_path_buf();
    let staging = blob_root.join(".staging");
    assert!(
        !staging.exists(),
        "fixture must begin without a staging directory"
    );
    let names_before = directory_entries(&blob_root);
    let connection =
        Connection::open_with_flags(&database, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let receipt_count = || {
        connection
            .query_row(
                "SELECT COUNT(*) FROM receipts WHERE project_id=?1",
                [project.id.to_string()],
                |row| row.get::<_, i64>(0),
            )
            .unwrap()
    };
    let receipts_before = receipt_count();

    let error = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "oversized-svg-new-request",
            &source,
            "Oversized SVG".into(),
            media_type.into(),
        )
        .unwrap_err();
    assert!(matches!(error, StorageError::BlobTooLarge { size, limit }
        if size == SVG_LIMIT + 1 && limit == SVG_LIMIT));
    let after = service.export_project(project.id).unwrap();
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
    assert_eq!(receipt_count(), receipts_before);
    assert_eq!(fs::metadata(&source).unwrap().len(), SVG_LIMIT + 1);
    // The original path returned the same size error only after full capture;
    // its .part guard cleaned the file but left this directory behind.
    assert!(
        !staging.exists(),
        "oversized declared SVG created staging before rejection"
    );
    assert_eq!(directory_entries(&blob_root), names_before);
}

fn directory_entries(path: &Path) -> Vec<std::ffi::OsString> {
    let mut entries = fs::read_dir(path)
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect::<Vec<_>>();
    entries.sort();
    entries
}

#[test]
fn declared_svg_mime_budget_rejects_before_staging() {
    assert_rejected_before_capture("declared.bin", "IMAGE/SVG+XML");
}

#[test]
fn declared_svg_extension_budget_rejects_before_staging() {
    assert_rejected_before_capture("drawing.SvG", "image/png");
}

#[test]
fn small_valid_svg_still_captures_and_imports_the_exact_original() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("state.sqlite3");
    let source = directory.path().join("small.svg");
    let bytes = br#"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>"#;
    fs::write(&source, bytes).unwrap();
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Admitted small SVG").unwrap();
    let imported = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "small-svg",
            &source,
            "Small SVG".into(),
            "image/svg+xml".into(),
        )
        .unwrap();
    assert!(!imported.replayed);
    assert_eq!(imported.project.revision, project.revision + 1);
    let digest = imported.project.assets[0].content_sha256.as_ref().unwrap();
    assert_eq!(
        service.store().lock().read_blob(digest, SVG_LIMIT).unwrap(),
        bytes
    );
    assert_eq!(fs::read(source).unwrap(), bytes);
}

#[test]
fn svg_budget_does_not_become_an_opaque_asset_size_limit() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("state.sqlite3");
    let source = directory.path().join("opaque.bin");
    fs::File::create(&source)
        .unwrap()
        .set_len(SVG_LIMIT + 1)
        .unwrap();
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Opaque compatibility").unwrap();
    let imported = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "opaque-control",
            &source,
            "Opaque".into(),
            "application/octet-stream".into(),
        )
        .unwrap();
    assert!(!imported.replayed);
    assert_eq!(imported.project.revision, project.revision + 1);
    let digest = imported.project.assets[0].content_sha256.as_ref().unwrap();
    let bytes = service
        .store()
        .lock()
        .read_blob(digest, SVG_LIMIT + 1)
        .unwrap();
    assert_eq!(bytes.len() as u64, SVG_LIMIT + 1);
    assert!(bytes.iter().all(|byte| *byte == 0));
}
