//! SQLite aliases must never grant authority over a different project store.
//! Synthetic temporary fixtures: no customer state and no host system paths.
//! Unix source-bound negatives; Windows NTFS reparse paths require separate
//! real Windows checks instead of claiming a synthetic symlink privilege.
#![cfg(unix)]

use motionwright_storage::{StorageError, Store};
use std::{fs, os::unix::fs::symlink};

#[test]
fn sqlite_file_symlink_cannot_open_another_owners_real_store() {
    let root = tempfile::tempdir().unwrap();
    let owner_dir = root.path().join("owner");
    fs::create_dir_all(&owner_dir).unwrap();
    let real_path = owner_dir.join("source.sqlite3");
    let mut owner = Store::open(&real_path).unwrap();
    let saved = owner
        .create_named_project("Another owner's durable project")
        .unwrap();
    drop(owner);

    let caller_dir = root.path().join("caller");
    fs::create_dir_all(&caller_dir).unwrap();
    let alias = caller_dir.join("project.sqlite3");
    symlink(&real_path, &alias).unwrap();
    let before = fs::read(&real_path).unwrap();

    // A recognized Motionwright schema is not a grant to follow an alias.
    let result = Store::open(&alias);
    assert!(
        matches!(result, Err(StorageError::UnsafeSourcePath(_))),
        "Store::open followed an SQLite symlink into a foreign owner store"
    );
    assert_eq!(fs::read(&real_path).unwrap(), before);
    assert_eq!(
        Store::open(&real_path)
            .unwrap()
            .load_project(saved.id)
            .unwrap(),
        saved
    );
    assert!(
        fs::symlink_metadata(&alias)
            .unwrap()
            .file_type()
            .is_symlink()
    );
}

#[test]
fn sqlite_parent_directory_symlink_cannot_create_foreign_owner_state() {
    let root = tempfile::tempdir().unwrap();
    let owner_dir = root.path().join("owner");
    fs::create_dir_all(&owner_dir).unwrap();
    let caller = root.path().join("caller");
    fs::create_dir_all(&caller).unwrap();
    let alias_dir = caller.join("redirect");
    symlink(&owner_dir, &alias_dir).unwrap();
    let unwanted = owner_dir.join("source.sqlite3");

    let result = Store::open(alias_dir.join("source.sqlite3"));
    assert!(
        matches!(result, Err(StorageError::UnsafeSourcePath(_))),
        "Store::open used a redirected parent to create a foreign SQLite file"
    );
    assert!(!unwanted.exists(), "an unowned SQLite file was created");
    assert!(!owner_dir.join("source.sqlite3-wal").exists());
}

#[test]
fn nested_sqlite_directory_alias_is_not_hidden_by_regular_final_parent() {
    let root = tempfile::tempdir().unwrap();
    let owner_dir = root.path().join("owner");
    let nested = owner_dir.join("nested");
    fs::create_dir_all(&nested).unwrap();
    let caller = root.path().join("caller");
    fs::create_dir_all(&caller).unwrap();
    let alias = caller.join("alias");
    symlink(&owner_dir, &alias).unwrap();

    let result = Store::open(alias.join("nested").join("state.sqlite3"));
    assert!(
        matches!(result, Err(StorageError::UnsafeSourcePath(_))),
        "a symlink ancestor let SQLite mutate a foreign folder"
    );
    assert!(!nested.join("state.sqlite3").exists());
}

#[test]
fn ordinary_regular_sqlite_project_creation_and_reopen_still_work() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("ordinary").join("state.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let project = store.create_named_project("Real owner data").unwrap();
    drop(store);

    let reopened = Store::open(&path).unwrap();
    assert_eq!(reopened.load_project(project.id).unwrap(), project);
    assert!(fs::symlink_metadata(&path).unwrap().is_file());
}
