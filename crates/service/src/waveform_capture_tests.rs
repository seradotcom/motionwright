//! Deterministic interleavings through the real waveform service and persistent Store.
use super::*;
use motionwright_domain::VoiceTrack;
use rusqlite::{Connection, OpenFlags, types::Value};
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    rc::Rc,
};

#[derive(Clone, Copy)]
pub(super) enum Phase {
    Source,
    Publish,
    Cache,
    Return,
}

type Hook = Box<dyn FnOnce()>;
thread_local! {
    static HOOKS: RefCell<[Option<Hook>; 4]> =
        const { RefCell::new([None, None, None, None]) };
}

pub(super) fn run_hook(phase: Phase) {
    let hook = HOOKS.with(|slots| slots.borrow_mut()[phase as usize].take());
    if let Some(hook) = hook {
        hook();
    }
}

fn schedule(phase: Phase, action: impl FnOnce() + 'static) -> Rc<Cell<usize>> {
    let calls = Rc::new(Cell::new(0));
    let observed = Rc::clone(&calls);
    HOOKS.with(|slots| {
        let mut slots = slots.borrow_mut();
        assert!(slots[phase as usize].is_none());
        slots[phase as usize] = Some(Box::new(move || {
            observed.set(observed.get() + 1);
            action();
        }));
    });
    calls
}

struct ClearHooks;
impl Drop for ClearHooks {
    fn drop(&mut self) {
        HOOKS.with(|slots| *slots.borrow_mut() = [None, None, None, None]);
    }
}

fn wav_bytes(sample: i16) -> Vec<u8> {
    const FRAMES: u32 = 6_000;
    const RATE: u32 = 8_000;
    let data_bytes = FRAMES * 2;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&RATE.to_le_bytes());
    bytes.extend_from_slice(&(RATE * 2).to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for _ in 0..FRAMES {
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    bytes
}

fn rows(connection: &Connection, sql: &str) -> Vec<Vec<Value>> {
    let mut statement = connection.prepare(sql).unwrap();
    let columns = statement.column_count();
    statement
        .query_map([], |row| (0..columns).map(|index| row.get(index)).collect())
        .unwrap()
        .collect::<rusqlite::Result<_>>()
        .unwrap()
}

fn sql_snapshot(database: &Path) -> Vec<(String, Vec<Vec<Value>>)> {
    let connection =
        Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let mut tables = connection
        .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        .unwrap();
    let names = tables
        .query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    let mut snapshot = vec![
        (
            "schema".into(),
            rows(
                &connection,
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name",
            ),
        ),
        (
            "user_version".into(),
            rows(&connection, "PRAGMA user_version"),
        ),
    ];
    for name in names {
        let quoted = name.replace('"', "\"\"");
        let mut values = rows(&connection, &format!("SELECT * FROM \"{quoted}\""));
        values.sort_by_key(|row| format!("{row:?}"));
        snapshot.push((name, values));
    }
    snapshot
}

fn tree(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn visit(root: &Path, path: &Path, result: &mut BTreeMap<String, Vec<u8>>) {
        if !path.exists() {
            return;
        }
        for entry in fs::read_dir(path).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(
                !metadata.is_symlink(),
                "fixture tree must not introduce links"
            );
            if metadata.is_dir() {
                visit(root, &path, result);
            } else {
                assert!(metadata.is_file());
                result.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                    fs::read(path).unwrap(),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(root, root, &mut result);
    result
}

fn cache_root(blob_root: &Path) -> PathBuf {
    blob_root.parent().unwrap().join("derived-cache")
}

struct Fixture {
    _clear: ClearHooks,
    database: PathBuf,
    source: PathBuf,
    source_bytes: Vec<u8>,
    source_cas: PathBuf,
    blob_root: PathBuf,
    service: StudioService,
    project: Project,
    track: VoiceTrack,
    temporary: tempfile::TempDir,
}

impl Fixture {
    fn new() -> Self {
        HOOKS.with(|slots| assert!(slots.borrow().iter().all(Option::is_none)));
        let temporary = tempfile::tempdir().unwrap();
        let original = temporary.path().join("original");
        fs::create_dir(&original).unwrap();
        let database = original.join("studio.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let initial = service.create_project("Captured waveform").unwrap();
        let source = original.join("voice.wav");
        let source_bytes = wav_bytes(8192);
        fs::write(&source, &source_bytes).unwrap();
        let project = service
            .import_voice_file(
                initial.id,
                &initial.stamp(),
                "captured-waveform-voice",
                &source,
                VoiceImportMetadata {
                    name: "voice.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Measured source".into(),
                },
            )
            .unwrap()
            .project;
        let track = project.audio.voice_tracks[0].clone();
        let (source_cas, blob_root) = {
            let shared = service.store();
            let store = shared.lock();
            (
                store.verified_blob_path(&track.source_sha256).unwrap(),
                store.blob_root().to_path_buf(),
            )
        };
        Self {
            _clear: ClearHooks,
            temporary,
            database,
            source,
            source_bytes,
            source_cas,
            blob_root,
            service,
            project,
            track,
        }
    }

    fn page(&self) -> StorageResult<WaveformPage> {
        self.service
            .waveform_page(self.project.id, self.track.id, 0, 2)
    }

    fn cache_hit(&self) -> DerivedCacheHit {
        let scope = format!(
            "project:{}:voice:{}:source:{}",
            self.project.id, self.track.id, self.track.source_sha256
        );
        let fingerprint =
            audio::waveform_fingerprint(&self.track.source_sha256, audio::WAVEFORM_FRAMES_PER_PEAK)
                .unwrap();
        self.service
            .lookup_derived_cache(&scope, "waveform-proxy", &fingerprint)
            .unwrap()
            .unwrap()
    }

    fn replacement(&self) -> (Store, Project, PathBuf, PathBuf) {
        let root = self.temporary.path().join("replacement");
        fs::create_dir(&root).unwrap();
        let database = root.join("studio.sqlite3");
        let mut store = Store::open(&database).unwrap();
        assert_eq!(
            store.ingest_blob_file(&self.source).unwrap().sha256,
            self.track.source_sha256
        );
        let restored = store
            .import_project(&self.service.export_project(self.project.id).unwrap())
            .unwrap();
        assert_eq!(restored.id, self.project.id);
        assert_eq!(restored.revision, self.project.revision);
        assert_ne!(restored.generation, self.project.generation);
        let blob_root = store.blob_root().to_path_buf();
        (store, restored, database, blob_root)
    }
}

fn original_page(page: &WaveformPage) -> bool {
    page.total_frames == 6_000
        && page.peak_count == 3
        && page.peaks.len() == 2
        && page.peaks.iter().all(|peak| (0.249..0.251).contains(peak))
}

#[test]
fn source_capture_survives_same_length_a_b_a_replacement() {
    let fixture = Fixture::new();
    let before = sql_snapshot(&fixture.database);
    let replacement = wav_bytes(24576);
    assert_eq!(fixture.source_bytes.len(), replacement.len());
    let path = fixture.source_cas.clone();
    let shared = fixture.service.store();
    let swapped = schedule(Phase::Source, move || {
        let _guard = shared
            .try_lock()
            .expect("source seam must release the Store mutex");
        fs::write(path, replacement).unwrap();
    });
    let path = fixture.source_cas.clone();
    let original = fixture.source_bytes.clone();
    let restored = schedule(Phase::Return, move || fs::write(path, original).unwrap());
    let result = fixture.page();
    assert_eq!(swapped.get(), 1);
    assert_eq!(restored.get(), 1);
    assert_eq!(sql_snapshot(&fixture.database), before);
    assert_eq!(fs::read(&fixture.source_cas).unwrap(), fixture.source_bytes);
    assert_eq!(fs::read(&fixture.source).unwrap(), fixture.source_bytes);
    let page = result.expect("unchanged restored source must remain readable");
    assert_eq!(page.source_sha256, fixture.track.source_sha256);
    if !original_page(&page) {
        assert_eq!(page.total_frames, 6_000);
        assert_eq!(page.peak_count, 3);
        assert_eq!(page.peaks.len(), 2);
        assert!(page.peaks.iter().all(|peak| (0.749..0.751).contains(peak)));
        panic!("WAVEFORM_SOURCE_CAPTURE_MISMATCH: source A, decoded B, final CAS A");
    }
    assert!(
        !tree(&fixture.blob_root)
            .keys()
            .any(|name| name.ends_with(".part"))
    );
}

#[test]
fn cache_capture_survives_valid_header_a_b_a_replacement() {
    let fixture = Fixture::new();
    assert!(original_page(&fixture.page().unwrap()));
    let hit = fixture.cache_hit();
    let original = fs::read(&hit.blob_path).unwrap();
    let mut replacement = original.clone();
    assert_eq!(replacement.len(), 78);
    replacement[72..74].copy_from_slice(&49_151_u16.to_le_bytes());
    assert_eq!(&replacement[..72], &original[..72]);
    assert_ne!(replacement, original);
    let before_rows = sql_snapshot(&fixture.database);
    let before_cas = tree(&fixture.blob_root);
    let before_cache = tree(&cache_root(&fixture.blob_root));
    let path = hit.blob_path.clone();
    let shared = fixture.service.store();
    let swapped = schedule(Phase::Cache, move || {
        let _guard = shared
            .try_lock()
            .expect("cache parse must release the Store mutex");
        fs::write(path, replacement).unwrap();
    });
    let restored = schedule(Phase::Return, move || {
        fs::write(hit.blob_path, original).unwrap()
    });
    let result = fixture.page();
    assert_eq!(swapped.get(), 1);
    assert_eq!(restored.get(), 1);
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(tree(&cache_root(&fixture.blob_root)), before_cache);
    let page = result.expect("valid captured proxy must remain readable");
    if !original_page(&page) {
        assert_eq!(page.peaks.len(), 2);
        assert!((0.749..0.751).contains(&page.peaks[0]));
        assert!((0.249..0.251).contains(&page.peaks[1]));
        panic!("WAVEFORM_CACHE_CAPTURE_MISMATCH: verified proxy A, returned B, final CAS A");
    }
}

#[test]
fn cached_page_rejects_replacement_generation_at_same_revision() {
    let fixture = Fixture::new();
    assert!(original_page(&fixture.page().unwrap()));
    let before_rows = sql_snapshot(&fixture.database);
    let before_cas = tree(&fixture.blob_root);
    let before_cache = tree(&cache_root(&fixture.blob_root));
    let (replacement, restored, database, blob_root) = fixture.replacement();
    let replacement_rows = sql_snapshot(&database);
    let replacement_cas = tree(&blob_root);
    let replacement_cache = tree(&cache_root(&blob_root));
    let shared = fixture.service.store();
    let calls = schedule(Phase::Return, move || {
        *shared
            .try_lock()
            .expect("late read seam must release Store") = replacement;
    });
    let result = fixture.page();
    assert_eq!(calls.get(), 1);
    assert_eq!(
        fixture.service.project(restored.id).unwrap().generation,
        restored.generation
    );
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(tree(&cache_root(&fixture.blob_root)), before_cache);
    assert_eq!(sql_snapshot(&database), replacement_rows);
    assert_eq!(tree(&blob_root), replacement_cas);
    assert_eq!(tree(&cache_root(&blob_root)), replacement_cache);
    match result {
        Err(StorageError::GenerationConflict) => {}
        Ok(page) => {
            assert!(original_page(&page));
            panic!("WAVEFORM_STALE_GENERATION_RESULT: old generation page escaped replacement");
        }
        Err(error) => panic!("unexpected error before generation oracle: {error:?}"),
    }
}

#[test]
fn generated_proxy_rejects_replacement_generation_before_admission() {
    let fixture = Fixture::new();
    let before_rows = sql_snapshot(&fixture.database);
    let before_cas = tree(&fixture.blob_root);
    let (replacement, restored, database, blob_root) = fixture.replacement();
    let replacement_rows = sql_snapshot(&database);
    let replacement_cas = tree(&blob_root);
    let replacement_cache = tree(&cache_root(&blob_root));
    assert!(replacement_cache.is_empty());
    let shared = fixture.service.store();
    let calls = schedule(Phase::Publish, move || {
        *shared
            .try_lock()
            .expect("publication seam must release Store") = replacement;
    });
    let result = fixture.page();
    assert_eq!(calls.get(), 1);
    assert_eq!(
        fixture.service.project(restored.id).unwrap().generation,
        restored.generation
    );
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(sql_snapshot(&database), replacement_rows);
    match result {
        Err(StorageError::GenerationConflict) => {
            assert_eq!(tree(&blob_root), replacement_cas);
            assert_eq!(tree(&cache_root(&blob_root)), replacement_cache);
        }
        Ok(page) => {
            assert!(original_page(&page));
            assert!(tree(&blob_root).len() > replacement_cas.len());
            assert!(!tree(&cache_root(&blob_root)).is_empty());
            panic!(
                "WAVEFORM_STALE_GENERATION_ADMISSION: proxy was admitted into replacement Store"
            );
        }
        Err(error) => panic!("unexpected error before admission oracle: {error:?}"),
    }
}

#[test]
fn unchanged_capture_preserves_page_contract_and_readonly_cache_hit() {
    let fixture = Fixture::new();
    let before_rows = sql_snapshot(&fixture.database);
    let first = fixture.page().unwrap();
    assert!(original_page(&first));
    assert!(!first.has_previous);
    assert!(first.has_next);
    let before_cas = tree(&fixture.blob_root);
    let before_cache = tree(&cache_root(&fixture.blob_root));
    let second = fixture
        .service
        .waveform_page(fixture.project.id, fixture.track.id, 1, 2)
        .unwrap();
    assert_eq!(second.peaks.len(), 1);
    assert!(second.has_previous);
    assert!(!second.has_next);
    assert!((0.249..0.251).contains(&second.peaks[0]));
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(tree(&cache_root(&fixture.blob_root)), before_cache);
}

#[test]
fn cache_hit_does_not_decode_again_under_a_reduced_read_work_budget() {
    let fixture = Fixture::new();
    assert!(original_page(&fixture.page().unwrap()));
    let limits = ImportLimits::default().with_audio_read_bytes(1).unwrap();
    let service = StudioService::open_with_import_limits(&fixture.database, limits).unwrap();
    let before_rows = sql_snapshot(&fixture.database);
    let before_cas = tree(&fixture.blob_root);
    let before_cache = tree(&cache_root(&fixture.blob_root));
    assert!(original_page(
        &service
            .waveform_page(fixture.project.id, fixture.track.id, 0, 2)
            .unwrap()
    ));
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(tree(&cache_root(&fixture.blob_root)), before_cache);
}

#[test]
fn corrupt_cached_bytes_are_not_a_miss_and_are_not_regenerated() {
    let fixture = Fixture::new();
    fixture.page().unwrap();
    let hit = fixture.cache_hit();
    let mut bytes = fs::read(&hit.blob_path).unwrap();
    bytes[72] ^= 1;
    fs::write(&hit.blob_path, bytes).unwrap();
    let before_rows = sql_snapshot(&fixture.database);
    let before_cas = tree(&fixture.blob_root);
    let before_cache = tree(&cache_root(&fixture.blob_root));
    assert!(matches!(
        fixture.page(),
        Err(StorageError::InvalidBackup(_))
    ));
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(tree(&cache_root(&fixture.blob_root)), before_cache);
}

#[test]
fn warm_proxy_does_not_hide_missing_original_or_foreign_track() {
    let fixture = Fixture::new();
    fixture.page().unwrap();
    let other = fixture.service.create_project("No voice attached").unwrap();
    assert!(matches!(
        fixture
            .service
            .waveform_page(other.id, fixture.track.id, 0, 2),
        Err(StorageError::NotFound)
    ));
    fs::remove_file(&fixture.source_cas).unwrap();
    let before_rows = sql_snapshot(&fixture.database);
    let before_cas = tree(&fixture.blob_root);
    let before_cache = tree(&cache_root(&fixture.blob_root));
    assert!(matches!(
        fixture.page(),
        Err(StorageError::BlobMissing { .. })
    ));
    assert_eq!(sql_snapshot(&fixture.database), before_rows);
    assert_eq!(tree(&fixture.blob_root), before_cas);
    assert_eq!(tree(&cache_root(&fixture.blob_root)), before_cache);
}
