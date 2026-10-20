//! Real Windows-2025 NTFS reparse boundary tests, not simulated Rust links.
//! A6 supplies synthetic fixtures wholly inside RUNNER_TEMP, then invokes
//! these ignored-by-default tests explicitly. Missing capability is a failure
//! when selected; an unexecuted test must never become a product PASS.
#![cfg(windows)]

use motionwright_storage::{StorageError, Store};
use std::{env, fs, os::windows::fs::MetadataExt, path::PathBuf};

const REPARSE_POINT: u32 = 0x0400;

fn synthetic_runner_root() -> PathBuf {
    let root = PathBuf::from(
        env::var_os("MW_SQLITE_NTFS_FIXTURE_ROOT")
            .expect("A6 must provide MW_SQLITE_NTFS_FIXTURE_ROOT"),
    );
    let runner_temp =
        PathBuf::from(env::var_os("RUNNER_TEMP").expect("requires a disposable runner"));
    assert!(root.is_absolute());
    let resolved = fs::canonicalize(&root).expect("A6 root must already exist");
    let allowed = fs::canonicalize(&runner_temp).expect("RUNNER_TEMP must exist");
    assert!(
        resolved.starts_with(allowed),
        "test fixtures must remain inside the disposable runner temp directory"
    );
    resolved
}

fn verified_directory_junction() -> (PathBuf, PathBuf) {
    let root = synthetic_runner_root();
    let owner = root.join("owner");
    let junction = root.join("junction");
    let owner_meta = fs::symlink_metadata(&owner).expect("A6 must create ordinary owner directory");
    assert!(owner_meta.is_dir());
    assert_eq!(owner_meta.file_attributes() & REPARSE_POINT, 0);
    let junction_meta = fs::symlink_metadata(&junction)
        .expect("A6 must create the NTFS directory junction before the selected test");
    assert_ne!(
        junction_meta.file_attributes() & REPARSE_POINT,
        0,
        "the fixture did not actually create an NTFS reparse point"
    );
    assert_eq!(
        fs::canonicalize(&junction).unwrap(),
        fs::canonicalize(&owner).unwrap(),
        "junction must point only to the synthetic owner directory"
    );
    (owner, junction)
}

#[test]
#[ignore = "A6 must provide verified synthetic NTFS junction fixture on Windows-2025"]
fn windows_junction_does_not_admit_another_owners_existing_sqlite() {
    let (owner, junction) = verified_directory_junction();
    let real_path = owner.join("a2-existing-owner-project.sqlite3");
    let mut valid = Store::open(&real_path).expect("the ordinary owner path must work");
    let project = valid
        .create_named_project("Synthetic owner project")
        .unwrap();
    drop(valid);
    let original = fs::read(&real_path).unwrap();

    assert!(
        matches!(
            Store::open(junction.join("a2-existing-owner-project.sqlite3")),
            Err(StorageError::UnsafeSourcePath(_))
        ),
        "redirected SQLite path unexpectedly admitted an existing valid database"
    );
    assert_eq!(fs::read(&real_path).unwrap(), original);
    assert_eq!(
        Store::open(&real_path)
            .unwrap()
            .load_project(project.id)
            .unwrap(),
        project
    );
}

#[test]
#[ignore = "A6 must provide verified synthetic NTFS junction fixture on Windows-2025"]
fn windows_junction_cannot_create_sqlite_or_sidecars_in_unowned_directory() {
    let (owner, junction) = verified_directory_junction();
    let attempted = owner.join("a2-create-attempt.sqlite3");
    assert!(!attempted.exists(), "fresh fixture must not preexist");

    assert!(
        matches!(
            Store::open(junction.join("a2-create-attempt.sqlite3")),
            Err(StorageError::UnsafeSourcePath(_))
        ),
        "redirected SQLite parent unexpectedly admitted database creation"
    );
    assert!(!attempted.exists());
    assert!(!owner.join("a2-create-attempt.sqlite3-wal").exists());
    assert!(!owner.join("a2-create-attempt.sqlite3-shm").exists());
}

#[test]
#[ignore = "A6 must provide an actual NTFS file symlink/reparse fixture; never report skipped case PASS"]
fn windows_reparse_file_leaf_is_rejected_without_touching_target_bytes() {
    let root = synthetic_runner_root();
    let real = root.join("file-target.bin");
    let alias = root.join("file-reparse.bin");
    let original = fs::read(&real).expect("A6 must provide an ordinary synthetic target file");
    let meta = fs::symlink_metadata(&alias)
        .expect("A6 must create a file symlink or reparse fixture before the selected test");
    assert_ne!(
        meta.file_attributes() & REPARSE_POINT,
        0,
        "the file fixture is not an NTFS reparse point"
    );
    assert_eq!(
        fs::canonicalize(&alias).unwrap(),
        fs::canonicalize(&real).unwrap(),
        "file alias must resolve only to the synthetic fixture target"
    );
    assert!(
        matches!(Store::open(&alias), Err(StorageError::UnsafeSourcePath(_))),
        "reparse SQLite leaf unexpectedly admitted a foreign file"
    );
    assert_eq!(fs::read(&real).unwrap(), original);
    assert!(!root.join("file-reparse.bin-wal").exists());
}
