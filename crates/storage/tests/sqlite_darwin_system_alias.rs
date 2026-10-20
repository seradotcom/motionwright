//! macOS has /var -> /private/var, including per-user temporary stores.
//! NOFOLLOW must reject foreign links without rejecting that built-in alias.
#![cfg(target_os = "macos")]

use motionwright_storage::{StorageError, Store};
use std::{fs, os::unix::fs::symlink, path::Path};

#[test]
fn built_in_var_alias_admits_real_sqlite_creation_and_reopen_with_nofollow() {
    let fixture = tempfile::Builder::new()
        .prefix("motionwright-macos-sqlite-system-alias-")
        .tempdir_in("/var/tmp")
        .unwrap();
    let dir = fixture.path();
    assert!(
        dir.starts_with("/var/tmp"),
        "fixture must exercise /var alias"
    );
    assert_eq!(fs::canonicalize("/var").unwrap(), Path::new("/private/var"));
    assert!(
        fs::canonicalize(dir)
            .unwrap()
            .starts_with("/private/var/tmp"),
        "fixture must map through only the verified macOS system alias"
    );

    let db = dir.join("state.sqlite3");
    let mut store = Store::open(&db).expect("valid system /var alias must be admitted");
    let project = store
        .create_named_project("Real saved project under macOS TEMP")
        .unwrap();
    drop(store);
    let reopened = Store::open(&db).expect("valid system alias must reopen without source loss");
    assert_eq!(reopened.load_project(project.id).unwrap(), project);
    assert!(fs::symlink_metadata(&db).unwrap().is_file());
}

#[test]
fn built_in_var_alias_does_not_exempt_foreign_nested_symlink() {
    let fixture = tempfile::Builder::new()
        .prefix("motionwright-macos-sqlite-foreign-alias-")
        .tempdir_in("/var/tmp")
        .unwrap();
    let owner = fixture.path().join("owner");
    fs::create_dir(&owner).unwrap();
    let redirected = fixture.path().join("user-controlled-link");
    symlink(&owner, &redirected).unwrap();

    let target = owner.join("state.sqlite3");
    assert!(
        matches!(
            Store::open(redirected.join("state.sqlite3")),
            Err(StorageError::UnsafeSourcePath(_))
        ),
        "the built-in /var exception must never admit a user-owned nested link"
    );
    assert!(!target.exists(), "the redirected store was created");
    assert!(!owner.join("state.sqlite3-wal").exists());
}
