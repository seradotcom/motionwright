use motionwright_service::StudioService;
use motionwright_storage::DerivedCacheRecord;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
use tempfile::{TempDir, tempdir};

const SCOPE: &str = "project:publication:generation:one";
const KIND: &str = "render-window-proxy";

struct Fixture {
    root: TempDir,
    service: StudioService,
    first: PathBuf,
    second: PathBuf,
    fingerprint: String,
    manifest: PathBuf,
    blob: PathBuf,
    record: DerivedCacheRecord,
}

fn files(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn visit(root: &Path, path: &Path, result: &mut BTreeMap<PathBuf, Vec<u8>>) {
        for entry in fs::read_dir(path).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                visit(root, &path, result);
            } else {
                result.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    fs::read(path).unwrap(),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(root, root, &mut result);
    result
}

fn fixture() -> Fixture {
    let root = tempdir().unwrap();
    let service = StudioService::open(root.path().join("project.sqlite3")).unwrap();
    let first = root.path().join("first.bin");
    let second = root.path().join("second.bin");
    fs::write(&first, b"original derived bytes").unwrap();
    fs::write(&second, b"different derived bytes").unwrap();
    let fingerprint = "42".repeat(32);
    let record = service
        .put_derived_cache_file(SCOPE, KIND, &fingerprint, &first, 37)
        .unwrap();
    let blob = service
        .lookup_derived_cache(SCOPE, KIND, &fingerprint)
        .unwrap()
        .unwrap()
        .blob_path;
    let manifests = files(&root.path().join("derived-cache"))
        .keys()
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .cloned()
        .collect::<Vec<_>>();
    assert_eq!(manifests.len(), 1);
    let manifest = root.path().join("derived-cache").join(&manifests[0]);
    Fixture {
        root,
        service,
        first,
        second,
        fingerprint,
        manifest,
        blob,
        record,
    }
}

#[test]
fn missing_blob_does_not_authorize_different_output_for_the_same_key() {
    let f = fixture();
    fs::remove_file(&f.blob).unwrap();
    assert!(
        f.service
            .lookup_derived_cache(SCOPE, KIND, &f.fingerprint)
            .unwrap()
            .is_none()
    );
    let before = files(f.root.path());
    let result = f
        .service
        .put_derived_cache_file(SCOPE, KIND, &f.fingerprint, &f.second, 99);
    let after = files(f.root.path());
    assert!(
        before == after,
        "conflicting repair changed retained files or published a new CAS blob"
    );
    assert!(
        result.is_err(),
        "missing content does not erase manifest authority"
    );
}

#[test]
fn exact_blob_repair_preserves_manifest_metadata_and_reopens() {
    let f = fixture();
    let manifest = fs::read(&f.manifest).unwrap();
    fs::remove_file(&f.blob).unwrap();
    let repaired = f
        .service
        .put_derived_cache_file(SCOPE, KIND, &f.fingerprint, &f.first, 999)
        .unwrap();
    assert_eq!(repaired, f.record);
    assert_eq!(fs::read(&f.manifest).unwrap(), manifest);
    assert_eq!(fs::read(&f.blob).unwrap(), b"original derived bytes");
    drop(f.service);
    let reopened = StudioService::open(f.root.path().join("project.sqlite3")).unwrap();
    let hit = reopened
        .lookup_derived_cache(SCOPE, KIND, &f.fingerprint)
        .unwrap()
        .unwrap();
    assert_eq!(hit.record, f.record);
    assert_eq!(fs::read(hit.blob_path).unwrap(), b"original derived bytes");
    assert_eq!(fs::read(f.first).unwrap(), b"original derived bytes");
}

#[test]
fn known_conflict_does_not_publish_the_rejected_blob() {
    let f = fixture();
    let before = files(f.root.path());
    let result = f
        .service
        .put_derived_cache_file(SCOPE, KIND, &f.fingerprint, &f.second, 99);
    let after = files(f.root.path());
    assert!(result.is_err());
    assert!(
        before == after,
        "known conflict published candidate CAS bytes"
    );
}

#[test]
fn malformed_existing_manifest_does_not_publish_candidate_bytes() {
    let f = fixture();
    fs::write(&f.manifest, b"foreign malformed manifest: preserve").unwrap();
    let before = files(f.root.path());
    let result = f
        .service
        .put_derived_cache_file(SCOPE, KIND, &f.fingerprint, &f.second, 99);
    let after = files(f.root.path());
    assert!(result.is_err());
    assert!(
        before == after,
        "invalid manifest admission published candidate CAS bytes"
    );
}

#[test]
fn distinct_scopes_and_kinds_remain_independent() {
    let f = fixture();
    let original = fs::read(&f.manifest).unwrap();
    let other_scope = f
        .service
        .put_derived_cache_file(
            "project:other:generation:one",
            KIND,
            &f.fingerprint,
            &f.second,
            9,
        )
        .unwrap();
    let other_kind = f
        .service
        .put_derived_cache_file(SCOPE, "caption-sidecar", &f.fingerprint, &f.second, 11)
        .unwrap();
    assert_ne!(other_scope.scope_sha256, f.record.scope_sha256);
    assert_ne!(other_kind.kind, f.record.kind);
    assert_eq!(fs::read(&f.manifest).unwrap(), original);
    let hit = f
        .service
        .lookup_derived_cache(SCOPE, KIND, &f.fingerprint)
        .unwrap()
        .unwrap();
    assert_eq!(hit.record, f.record);
    assert_eq!(fs::read(hit.blob_path).unwrap(), b"original derived bytes");
}
