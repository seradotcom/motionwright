//! Controlled import boundary for an unsupported traversal ZIP source.
//! These cases exercise product APIs; they do not extract archives or admit ZIP support.
use motionwright_domain::Project;
use motionwright_service::StudioService;
use motionwright_storage::StorageError;
use rusqlite::{Connection, OpenFlags, types::Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

fn logical_rows(path: &Path) -> BTreeMap<String, Vec<Vec<Value>>> {
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let names: Vec<String> = connection
        .prepare("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    let mut snapshot = BTreeMap::new();
    for name in names {
        let sql = format!(
            "SELECT * FROM \"{}\" ORDER BY rowid",
            name.replace('"', "\"\"")
        );
        let mut statement = connection.prepare(&sql).unwrap();
        let count = statement.column_count();
        let mut rows = statement.query([]).unwrap();
        let mut values: Vec<Vec<Value>> = vec![];
        while let Some(row) = rows.next().unwrap() {
            values.push((0..count).map(|index| row.get(index).unwrap()).collect());
        }
        snapshot.insert(name, values);
    }
    snapshot
}

// Include directories (including empty staging) and exact regular file bytes.
// Inventory verifies file types before reading their bytes.
fn inventory(root: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
    fn visit(root: &Path, current: &Path, result: &mut BTreeMap<PathBuf, Option<Vec<u8>>>) {
        for entry in fs::read_dir(current).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            let key = path.strip_prefix(root).unwrap().to_path_buf();
            if metadata.is_dir() {
                result.insert(key, None);
                visit(root, &path, result);
            } else {
                assert!(metadata.is_file(), "inventory must not open a special file");
                result.insert(key, Some(fs::read(path).unwrap()));
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(root, root, &mut result);
    result
}

fn regular_control(service: &StudioService, source: &Path) -> Project {
    let bytes = b"ordinary immutable import control";
    fs::write(source, bytes).unwrap();
    let original = service.create_project("Import boundary control").unwrap();
    let outcome = service
        .import_asset_file(
            original.id,
            &original.stamp(),
            "regular-positive-control",
            source,
            "regular.txt".into(),
            "text/plain".into(),
        )
        .unwrap();
    assert_eq!(outcome.project.revision, original.revision + 1);
    let digest = hex::encode(Sha256::digest(bytes));
    assert_eq!(
        service.store().lock().read_blob(&digest, 4096).unwrap(),
        bytes
    );
    outcome.project
}

#[test]
fn project_bundle_entry_rejects_real_traversal_zip_without_side_effects() {
    const ZIP: &[u8] = include_bytes!("fixtures/traversal-project.zip");
    assert_eq!(
        hex::encode(Sha256::digest(ZIP)),
        "8427caee60c5d4b1dd36735524307824d714404380403149a238d366b78b2487"
    );
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    let source = StudioService::open(root.join("source/state.sqlite3")).unwrap();
    let project = regular_control(&source, &root.join("regular.txt"));
    let bundle = root.join("directory-bundle");
    source
        .export_project_bundle_at(project.id, &project.stamp(), &bundle)
        .unwrap();

    let database = root.join("target/state.sqlite3");
    let service = StudioService::open(&database).unwrap();
    service.create_project("Existing target project").unwrap();
    let inspected = service
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    let incoming = root.join("incoming");
    let outside = root.join("outside");
    fs::create_dir(&incoming).unwrap();
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("sentinel.txt"), b"outside bytes must survive").unwrap();
    let archive = incoming.join("traversal-project.zip");
    fs::write(&archive, ZIP).unwrap();

    let blob_root = service.store().lock().blob_root().to_path_buf();
    let rows = logical_rows(&database);
    let blobs = inventory(&blob_root);
    let exterior = inventory(&outside);
    let error = service
        .inspect_project_bundle_with_fingerprint(&archive)
        .unwrap_err();
    assert!(matches!(error, StorageError::UnsafeSourcePath(ref reason)
        if reason == "portable bundle must be a real non-symlink directory"));
    // A real directory inspection does not authorize a different source kind.
    let error = service
        .import_project_bundle_inspected(&archive, &inspected.fingerprint)
        .unwrap_err();
    assert!(matches!(error, StorageError::UnsafeSourcePath(ref reason)
        if reason == "portable bundle must be a real non-symlink directory"));
    assert_eq!(logical_rows(&database), rows);
    assert_eq!(inventory(&blob_root), blobs);
    assert_eq!(inventory(&outside), exterior);
    assert_eq!(fs::read(&archive).unwrap(), ZIP);

    let imported = service
        .import_project_bundle_inspected(&bundle, &inspected.fingerprint)
        .unwrap();
    assert_eq!(imported.title, project.title);
    assert_eq!(imported.assets, project.assets);
    assert_eq!(service.project(imported.id).unwrap(), imported);
    let digest = imported.assets[0].content_sha256.as_ref().unwrap();
    assert_eq!(
        service.store().lock().read_blob(digest, 4096).unwrap(),
        b"ordinary immutable import control"
    );
    assert_eq!(inventory(&outside), exterior);
}
