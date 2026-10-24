use motionwright_domain::{Asset, Change, Project};
use motionwright_service::{BundleFingerprint, StudioService};
use motionwright_storage::StorageError;
use rusqlite::{Connection, OpenFlags, types::Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
use uuid::Uuid;

#[derive(Clone, Copy, Debug)]
enum Kind {
    Static,
    Active,
    HiddenActive,
    External,
}

impl Kind {
    fn fixture(self) -> (&'static [u8], &'static str, &'static str) {
        match self {
            Self::Static => (
                br#"<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="blue"/></svg>"#,
                "drawing.svg",
                "image/svg+xml",
            ),
            Self::Active => (
                br#"<svg xmlns="http://www.w3.org/2000/svg"><script>/* inert admission test marker; never rendered */</script></svg>"#,
                "drawing.svg",
                "image/svg+xml",
            ),
            Self::HiddenActive => (
                br#"<svg xmlns="http://www.w3.org/2000/svg"><script>/* inert admission test marker; never rendered */</script></svg>"#,
                "drawing.bin",
                "application/octet-stream",
            ),
            Self::External => (
                br#"<svg xmlns="http://www.w3.org/2000/svg"><image href="https://svg-admission.invalid/never-fetched.png"/></svg>"#,
                "drawing.svg",
                "image/svg+xml",
            ),
        }
    }
}

#[derive(Clone, Copy, Debug)]
enum Route {
    Inspect,
    InspectFingerprint,
    Import,
    ImportFingerprint,
}

impl Route {
    fn imports(self) -> bool {
        matches!(self, Self::Import | Self::ImportFingerprint)
    }
}

fn service(root: &Path, name: &str) -> StudioService {
    StudioService::open(root.join(name).join("state.sqlite3")).unwrap()
}

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn files(root: &Path) -> BTreeMap<String, String> {
    fn visit(root: &Path, path: &Path, result: &mut BTreeMap<String, String>) {
        if !path.exists() {
            return;
        }
        for entry in fs::read_dir(path).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            let info = fs::symlink_metadata(&path).unwrap();
            assert!(!info.file_type().is_symlink());
            if info.is_dir() {
                visit(root, &path, result);
            } else {
                assert!(info.is_file());
                result.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                    digest(&fs::read(&path).unwrap()),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(root, root, &mut result);
    result
}

fn logical_sql(database: &Path) -> BTreeMap<String, Vec<String>> {
    let conn = Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let names: Vec<String> = conn
        .prepare("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    let mut result = BTreeMap::new();
    for name in names {
        let mut statement = conn
            .prepare(&format!("SELECT * FROM \"{}\"", name.replace('"', "\"\"")))
            .unwrap();
        let columns = statement.column_count();
        let mut rows: Vec<String> = statement
            .query_map([], |row| {
                let values = (0..columns)
                    .map(|column| row.get::<_, Value>(column))
                    .collect::<Result<Vec<_>, _>>()?;
                Ok(format!("{values:?}"))
            })
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        rows.sort();
        result.insert(name, rows);
    }
    result
}

#[derive(Debug, PartialEq)]
struct State {
    sql: BTreeMap<String, Vec<String>>,
    cas: BTreeMap<String, String>,
}

fn state(service: &StudioService) -> State {
    let shared = service.store();
    let store = shared.lock();
    State {
        sql: logical_sql(store.path()),
        cas: files(store.blob_root()),
    }
}

fn reopen(service: StudioService) -> StudioService {
    let database = service.store().lock().path().to_path_buf();
    drop(service);
    StudioService::open(database).unwrap()
}

struct Fixture {
    source: StudioService,
    project: Project,
    bundle: PathBuf,
    input: PathBuf,
    payload: Vec<u8>,
    digest: String,
    fingerprint: BundleFingerprint,
}

// This is a coherent legacy producer, not an import-policy bypass in the test.
// The real Store creates CAS, the journal, receipts and the exported manifest.
// No hand-edited project, event, digest or SQL row is used.
fn fixture(root: &Path, name: &str, kind: Kind) -> Fixture {
    let source = service(root, &format!("{name}-source"));
    let project = source
        .create_project("Legacy portable SVG fixture")
        .unwrap();
    let (payload, asset_name, media_type) = kind.fixture();
    let input = root.join(format!("{name}-{asset_name}"));
    fs::write(&input, payload).unwrap();
    let shared = source.store();
    let mut store = shared.lock();
    let blob = store.ingest_blob_file(&input).unwrap();
    assert_eq!(blob.sha256, digest(payload));
    let project = store
        .apply(
            project.id,
            &project.stamp(),
            "legacy-fixture-asset",
            &Change::AddAsset {
                asset: Asset {
                    id: Uuid::now_v7(),
                    name: asset_name.into(),
                    media_type: media_type.into(),
                    content_sha256: Some(blob.sha256.clone()),
                    source_revision: Some("local-import".into()),
                },
            },
        )
        .unwrap()
        .project;
    let bundle = root.join(format!("{name}-bundle"));
    let manifest = store.export_project_bundle(project.id, &bundle).unwrap();
    assert_eq!(manifest.backup.project, project);
    assert_eq!(manifest.blobs.len(), 1);
    assert_eq!(manifest.blobs[0].sha256, blob.sha256);
    assert_eq!(
        fs::read(bundle.join(&manifest.blobs[0].relative_path)).unwrap(),
        payload
    );
    drop(store);
    drop(shared);
    // A fingerprint is an input identity, never a permission token. Compute it
    // from the real export so import is exercised even when inspection rejects.
    let manifest_bytes = fs::read(bundle.join("manifest.json")).unwrap();
    let fingerprint = BundleFingerprint::from_sha256(hex::encode(
        Sha256::new()
            .chain_update(b"motionwright.bundle-inspection.v1\0")
            .chain_update(&manifest_bytes)
            .finalize(),
    ))
    .unwrap();
    Fixture {
        source,
        project,
        bundle,
        input,
        payload: payload.to_vec(),
        digest: blob.sha256,
        fingerprint,
    }
}

fn destination(root: &Path, name: &str) -> (StudioService, Project, PathBuf) {
    let service = service(root, name);
    let project = service
        .create_project("Existing destination sentinel")
        .unwrap();
    let input = root.join(format!("{name}-sentinel.bin"));
    fs::write(
        &input,
        b"existing sentinel bytes must survive bundle rejection",
    )
    .unwrap();
    let project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "existing-sentinel-import",
            &input,
            "sentinel.bin".into(),
            "application/octet-stream".into(),
        )
        .unwrap()
        .project;
    (service, project, input)
}

fn call(
    service: &StudioService,
    bundle: &Path,
    fingerprint: &BundleFingerprint,
    route: Route,
) -> Result<(), StorageError> {
    match route {
        Route::Inspect => service.inspect_project_bundle(bundle).map(|_| ()),
        Route::InspectFingerprint => service
            .inspect_project_bundle_with_fingerprint(bundle)
            .map(|_| ()),
        Route::Import => service.import_project_bundle(bundle).map(|_| ()),
        Route::ImportFingerprint => service
            .import_project_bundle_inspected(bundle, fingerprint)
            .map(|_| ()),
    }
}

fn assert_svg_rejected(result: Result<(), StorageError>) {
    let error = result.expect_err("coherent active/external SVG bundle was admitted");
    assert!(
        error.to_string().contains("SVG"),
        "rejection must be the SVG admission policy, not a malformed fixture: {error}"
    );
}

fn rejected_bundle(kind: Kind, route: Route) {
    let temp = tempfile::tempdir().unwrap();
    let fixture = fixture(temp.path(), "producer", kind);
    let original_source = state(&fixture.source);
    let original_bundle = files(&fixture.bundle);
    let (target, sentinel, sentinel_file) = destination(temp.path(), "target");
    let sentinel_bytes = fs::read(&sentinel_file).unwrap();
    let before = state(&target);

    let result = call(&target, &fixture.bundle, &fixture.fingerprint, route);
    let after = state(&target);
    let payload_present = target.store().lock().verify_blob(&fixture.digest).is_ok();
    eprintln!(
        "SVG_BUNDLE_ADMISSION kind={kind:?} route={route:?} accepted={} sql_changed={} cas_changed={} payload_present={payload_present}",
        result.is_ok(),
        before.sql != after.sql,
        before.cas != after.cas,
    );
    assert_svg_rejected(result);
    assert_eq!(after, before);
    assert!(!payload_present);
    assert_eq!(target.project(sentinel.id).unwrap(), sentinel);
    assert_eq!(fs::read(&sentinel_file).unwrap(), sentinel_bytes);
    let target = reopen(target);
    assert_eq!(state(&target), before);
    assert_eq!(target.project(sentinel.id).unwrap(), sentinel);
    assert_eq!(files(&fixture.bundle), original_bundle);
    assert_eq!(state(&fixture.source), original_source);
    assert_eq!(fs::read(&fixture.input).unwrap(), fixture.payload);
}

fn static_bundle(route: Route) {
    let temp = tempfile::tempdir().unwrap();
    let fixture = fixture(temp.path(), "producer", Kind::Static);
    let source_before = state(&fixture.source);
    let bundle_before = files(&fixture.bundle);
    let (target, sentinel, sentinel_file) = destination(temp.path(), "target");
    let before = state(&target);
    let sentinel_bytes = fs::read(&sentinel_file).unwrap();
    call(&target, &fixture.bundle, &fixture.fingerprint, route).unwrap();
    assert_eq!(target.project(sentinel.id).unwrap(), sentinel);
    assert_eq!(fs::read(&sentinel_file).unwrap(), sentinel_bytes);
    let target = reopen(target);
    if route.imports() {
        let imported = target.project(fixture.project.id).unwrap();
        assert_eq!(imported.assets, fixture.project.assets);
        assert_ne!(imported.generation, fixture.project.generation);
        assert_eq!(
            target
                .store()
                .lock()
                .read_blob(&fixture.digest, fixture.payload.len() as u64)
                .unwrap(),
            fixture.payload
        );
    } else {
        assert_eq!(state(&target), before);
    }
    assert_eq!(state(&fixture.source), source_before);
    assert_eq!(files(&fixture.bundle), bundle_before);
    assert_eq!(fs::read(&fixture.input).unwrap(), fixture.payload);
}

macro_rules! rejected_case {
    ($name:ident, $kind:ident, $route:ident) => {
        #[test]
        fn $name() {
            rejected_bundle(Kind::$kind, Route::$route);
        }
    };
}
macro_rules! static_case {
    ($name:ident, $route:ident) => {
        #[test]
        fn $name() {
            static_bundle(Route::$route);
        }
    };
}

static_case!(static_svg_legacy_inspection_is_readonly, Inspect);
static_case!(
    static_svg_fingerprinted_inspection_is_readonly,
    InspectFingerprint
);
static_case!(static_svg_legacy_import_preserves_exact_bytes, Import);
static_case!(
    static_svg_fingerprinted_import_preserves_exact_bytes,
    ImportFingerprint
);
rejected_case!(active_svg_legacy_inspection_rejects, Active, Inspect);
rejected_case!(
    active_svg_fingerprinted_inspection_rejects,
    Active,
    InspectFingerprint
);
rejected_case!(
    active_svg_legacy_import_rejects_before_publication,
    Active,
    Import
);
rejected_case!(
    active_svg_fingerprinted_import_rejects_before_publication,
    Active,
    ImportFingerprint
);
rejected_case!(
    mislabeled_svg_legacy_inspection_rejects,
    HiddenActive,
    Inspect
);
rejected_case!(
    mislabeled_svg_fingerprinted_inspection_rejects,
    HiddenActive,
    InspectFingerprint
);
rejected_case!(
    mislabeled_svg_legacy_import_rejects_before_publication,
    HiddenActive,
    Import
);
rejected_case!(
    mislabeled_svg_fingerprinted_import_rejects_before_publication,
    HiddenActive,
    ImportFingerprint
);
rejected_case!(external_svg_legacy_inspection_rejects, External, Inspect);
rejected_case!(
    external_svg_fingerprinted_inspection_rejects,
    External,
    InspectFingerprint
);
rejected_case!(
    external_svg_legacy_import_rejects_before_publication,
    External,
    Import
);
rejected_case!(
    external_svg_fingerprinted_import_rejects_before_publication,
    External,
    ImportFingerprint
);

#[test]
fn direct_file_controls_use_exactly_the_same_svg_bytes() {
    for kind in [
        Kind::Static,
        Kind::Active,
        Kind::HiddenActive,
        Kind::External,
    ] {
        let temp = tempfile::tempdir().unwrap();
        let (target, project, _) = destination(temp.path(), "target");
        let (payload, name, media_type) = kind.fixture();
        let input = temp.path().join(name);
        fs::write(&input, payload).unwrap();
        let before = state(&target);
        let result = target.import_asset_file(
            project.id,
            &project.stamp(),
            "direct-svg-control",
            &input,
            name.into(),
            media_type.into(),
        );
        if matches!(kind, Kind::Static) {
            let imported = result.unwrap().project;
            assert_eq!(
                imported.assets.last().unwrap().content_sha256.as_deref(),
                Some(digest(payload).as_str())
            );
        } else {
            assert_svg_rejected(result.map(|_| ()));
            assert_eq!(state(&target), before);
            assert_eq!(state(&reopen(target)), before);
        }
        assert_eq!(fs::read(&input).unwrap(), payload);
    }
}

fn replace_with_coherent_active_bundle(root: &Path, safe: &Fixture, active: &Fixture) {
    fs::rename(&safe.bundle, root.join("retained-static-bundle")).unwrap();
    fs::rename(&active.bundle, &safe.bundle).unwrap();
}

#[test]
fn legacy_import_revalidates_svg_after_an_earlier_safe_inspection() {
    let temp = tempfile::tempdir().unwrap();
    let safe = fixture(temp.path(), "safe", Kind::Static);
    let active = fixture(temp.path(), "active", Kind::Active);
    let (target, _, _) = destination(temp.path(), "target");
    target.inspect_project_bundle(&safe.bundle).unwrap();
    replace_with_coherent_active_bundle(temp.path(), &safe, &active);
    let before = state(&target);
    let presented = files(&safe.bundle);
    assert_svg_rejected(target.import_project_bundle(&safe.bundle).map(|_| ()));
    assert_eq!(state(&target), before);
    assert_eq!(state(&reopen(target)), before);
    assert_eq!(files(&safe.bundle), presented);
}

#[test]
fn inspected_import_checks_both_manifest_identity_and_current_svg_policy() {
    let temp = tempfile::tempdir().unwrap();
    let safe = fixture(temp.path(), "safe", Kind::Static);
    let active = fixture(temp.path(), "active", Kind::Active);
    let (target, _, _) = destination(temp.path(), "target");
    let inspected = target
        .inspect_project_bundle_with_fingerprint(&safe.bundle)
        .unwrap();
    replace_with_coherent_active_bundle(temp.path(), &safe, &active);
    let before = state(&target);
    let stale = target
        .import_project_bundle_inspected(&safe.bundle, &inspected.fingerprint)
        .unwrap_err();
    assert!(
        stale
            .to_string()
            .contains("manifest changed after inspection")
    );
    assert_eq!(state(&target), before);
    // The previous rejection is determinate. A fresh known input identity
    // still cannot authorize the active bytes that now inhabit this path.
    assert_svg_rejected(
        target
            .import_project_bundle_inspected(&safe.bundle, &active.fingerprint)
            .map(|_| ()),
    );
    assert_eq!(state(&target), before);
    assert_eq!(state(&reopen(target)), before);
}
