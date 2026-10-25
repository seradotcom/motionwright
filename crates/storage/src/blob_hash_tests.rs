//! Causal checks of the actual CAS verifier and Store mutation boundary.
//! Hooks alter only owned synthetic files, never a provider or user source.
use super::*;
use motionwright_domain::{Asset, Change, Project};
use std::{cell::Cell, io::Write, rc::Rc};

struct Hooks;
impl Drop for Hooks {
    fn drop(&mut self) {
        file_import::BEFORE_IMPORT_SOURCE_OPEN.with(|slot| {
            slot.borrow_mut().take();
        });
        file_import::AFTER_FIRST_IMPORT_SOURCE_READ.with(|slot| {
            slot.borrow_mut().take();
        });
    }
}

fn on_first_read(action: impl FnOnce(&Path) + 'static) -> Hooks {
    file_import::AFTER_FIRST_IMPORT_SOURCE_READ.with(|slot| {
        assert!(slot.borrow().is_none());
        *slot.borrow_mut() = Some(Box::new(action));
    });
    Hooks
}

#[cfg(any(unix, windows))]
fn before_open(action: impl FnOnce(&Path) + 'static) -> Hooks {
    file_import::BEFORE_IMPORT_SOURCE_OPEN.with(|slot| {
        assert!(slot.borrow().is_none());
        *slot.borrow_mut() = Some(Box::new(action));
    });
    Hooks
}

struct Fixture {
    store: Store,
    project: Project,
    descriptor: BlobDescriptor,
    blob: PathBuf,
    directory: tempfile::TempDir,
}

fn fixture(bytes: &[u8]) -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("owned-source.bin");
    fs::write(&source, bytes).unwrap();
    let mut store = Store::open(directory.path().join("owned.sqlite3")).unwrap();
    let project = store.create_named_project("CAS capture fixture").unwrap();
    let descriptor = store.ingest_blob_file(&source).unwrap();
    let blob = store.verified_blob_path(&descriptor.sha256).unwrap();
    Fixture {
        store,
        project,
        descriptor,
        blob,
        directory,
    }
}

fn asset_change(f: &Fixture) -> Change {
    Change::AddAsset {
        asset: Asset {
            id: Uuid::new_v4(),
            name: "Owned synthetic bytes".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some(f.descriptor.sha256.clone()),
            source_revision: Some("local-import".into()),
        },
    }
}

fn assert_unchanged(f: &mut Fixture, before: &ProjectBackup, key: &str) {
    let after = f.store.export_project(f.project.id).unwrap();
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
    let count: i64 = f
        .store
        .conn
        .query_row(
            "SELECT count(*) FROM receipts WHERE project_id=?1 AND request_id=?2",
            rusqlite::params![f.project.id.to_string(), key],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}

#[test]
fn verify_blob_refuses_growth_even_when_final_digest_matches() {
    let f = fixture(b"abcde");
    fs::write(&f.blob, b"abcd").unwrap();
    let count = Rc::new(Cell::new(0));
    let observed = count.clone();
    let expected = f.blob.clone();
    let _hooks = on_first_read(move |path| {
        assert_eq!(path, expected);
        observed.set(observed.get() + 1);
        fs::OpenOptions::new()
            .append(true)
            .open(path)
            .unwrap()
            .write_all(b"e")
            .unwrap();
    });
    let result = f.store.verify_blob(&f.descriptor.sha256);
    assert_eq!(
        count.get(),
        1,
        "the growth interposition must actually execute"
    );
    assert!(
        matches!(
            result,
            Err(StorageError::BlobTooLarge { size: 5, limit: 4 })
        ),
        "the original EOF loop accepted bytes added after the captured size"
    );
}

#[test]
fn apply_refuses_growth_without_mutating_the_canonical_project() {
    let mut f = fixture(b"abcde");
    fs::write(&f.blob, b"abcd").unwrap();
    let before = f.store.export_project(f.project.id).unwrap();
    let change = asset_change(&f);
    let count = Rc::new(Cell::new(0));
    let observed = count.clone();
    let expected = f.blob.clone();
    let _hooks = on_first_read(move |path| {
        assert_eq!(path, expected);
        observed.set(observed.get() + 1);
        fs::OpenOptions::new()
            .append(true)
            .open(path)
            .unwrap()
            .write_all(b"e")
            .unwrap();
    });
    let result = f
        .store
        .apply(f.project.id, &f.project.stamp(), "growing-cas", &change);
    assert_eq!(
        count.get(),
        1,
        "the growth interposition must actually execute"
    );
    assert!(
        matches!(
            result,
            Err(StorageError::BlobTooLarge { size: 5, limit: 4 })
        ),
        "a moving CAS source must not authorize a new project revision"
    );
    assert_unchanged(&mut f, &before, "growing-cas");
}

#[test]
fn verify_blob_refuses_truncation_even_when_final_digest_matches() {
    const PREFIX: usize = 128 * 1024;
    let f = fixture(&vec![b'x'; PREFIX]);
    fs::OpenOptions::new()
        .append(true)
        .open(&f.blob)
        .unwrap()
        .write_all(b"tail-observed-before-verification")
        .unwrap();
    let count = Rc::new(Cell::new(0));
    let observed = count.clone();
    let expected = f.blob.clone();
    let _hooks = on_first_read(move |path| {
        assert_eq!(path, expected);
        observed.set(observed.get() + 1);
        fs::OpenOptions::new()
            .write(true)
            .open(path)
            .unwrap()
            .set_len(PREFIX as u64)
            .unwrap();
    });
    let result = f.store.verify_blob(&f.descriptor.sha256);
    assert_eq!(
        count.get(),
        1,
        "the truncation interposition must actually execute"
    );
    assert!(
        matches!(result, Err(StorageError::InvalidBackup(_))),
        "the original EOF loop accepted a source shorter than its captured size"
    );
}

#[test]
fn verify_blob_preserves_empty_and_multichunk_valid_sources() {
    for bytes in [
        vec![],
        b"small owned fixture".to_vec(),
        vec![b'x'; 3 * 128 * 1024 + 17],
    ] {
        let f = fixture(&bytes);
        assert_eq!(
            f.store.verify_blob(&f.descriptor.sha256).unwrap(),
            f.descriptor
        );
        assert_eq!(f.descriptor.size_bytes, bytes.len() as u64);
    }
}

#[cfg(any(unix, windows))]
fn swap_with_equal_bytes_symlink(f: &Fixture) -> (Hooks, Rc<Cell<usize>>) {
    let sentinel = f.directory.path().join("owned-sentinel.bin");
    fs::write(&sentinel, fs::read(&f.blob).unwrap()).unwrap();
    let count = Rc::new(Cell::new(0));
    let observed = count.clone();
    let expected = f.blob.clone();
    let hooks = before_open(move |path| {
        assert_eq!(path, expected);
        observed.set(observed.get() + 1);
        fs::remove_file(path).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&sentinel, path).unwrap();
        #[cfg(windows)]
        std::os::windows::fs::symlink_file(&sentinel, path).unwrap();
    });
    (hooks, count)
}

#[test]
#[cfg(any(unix, windows))]
#[cfg_attr(
    windows,
    ignore = "requires Windows symlink privilege; execute explicitly on an admitted runner"
)]
fn verify_blob_refuses_leaf_swapped_for_equal_bytes_symlink() {
    let f = fixture(b"equal synthetic bytes");
    let (_hooks, count) = swap_with_equal_bytes_symlink(&f);
    let result = f.store.verify_blob(&f.descriptor.sha256);
    assert_eq!(count.get(), 1, "the pathname swap must actually execute");
    assert!(
        result.is_err(),
        "matching digest must not authorize following a substituted symlink"
    );
}

#[test]
#[cfg(any(unix, windows))]
#[cfg_attr(
    windows,
    ignore = "requires Windows symlink privilege; execute explicitly on an admitted runner"
)]
fn apply_refuses_leaf_swapped_for_equal_bytes_symlink() {
    let mut f = fixture(b"equal synthetic bytes");
    let before = f.store.export_project(f.project.id).unwrap();
    let change = asset_change(&f);
    let (_hooks, count) = swap_with_equal_bytes_symlink(&f);
    let result = f
        .store
        .apply(f.project.id, &f.project.stamp(), "swapped-cas", &change);
    assert_eq!(count.get(), 1, "the pathname swap must actually execute");
    assert!(
        result.is_err(),
        "matching digest must not admit a project mutation through a symlink"
    );
    assert_unchanged(&mut f, &before, "swapped-cas");
}
