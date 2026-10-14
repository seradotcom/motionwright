//! v0.1 installed-owner path authority regression tests.
//! These are read-only filesystem fixtures and never invoke Semwright.
use super::*;
use sha2::{Digest, Sha256};

#[test]
fn accepts_an_unlinked_owner_root_and_digest_bound_native_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let owner = temp.path().join("owner");
    fs::create_dir(&owner).unwrap();
    let content = b"verified-original-media-fixture";
    fs::write(owner.join("verified.bin"), content).unwrap();
    let hash = hex::encode(Sha256::digest(content));
    assert!(safe_metadata(&owner).unwrap().is_dir());
    assert!(
        safe_metadata(&owner.join("verified.bin"))
            .unwrap()
            .is_file()
    );
    assert_eq!(
        read_verified_production_artifact(&owner, "verified.bin", &hash, 1024).unwrap(),
        content,
    );
}

#[test]
fn rejects_non_normalized_parent_components_without_following_them() {
    let temp = tempfile::tempdir().unwrap();
    let owner = temp.path().join("owner");
    fs::create_dir(&owner).unwrap();
    fs::create_dir(owner.join("sub")).unwrap();
    fs::write(owner.join("media.bin"), b"verified fixture").unwrap();

    // The parent traversal exists and resolves to the trusted file, but the
    // selected owner connection path itself is not an exact normalized path.
    assert!(safe_metadata(&owner.join("sub").join("..").join("media.bin")).is_err());
    assert!(
        read_verified_production_artifact(
            &owner,
            "../owner/media.bin",
            &hex::encode(Sha256::digest(b"verified fixture")),
            1024,
        )
        .is_err()
    );
}

#[cfg(unix)]
#[test]
fn refuses_symlinked_connection_parent_and_owner_root() {
    use std::os::unix::fs::symlink;

    let temp = tempfile::tempdir().unwrap();
    let real = temp.path().join("real");
    fs::create_dir(&real).unwrap();
    fs::write(real.join("connection.json"), b"private local connection").unwrap();
    fs::write(real.join("frame.png"), b"native-content").unwrap();
    let alias = temp.path().join("linked-parent");
    symlink(&real, &alias).unwrap();

    // The final file itself is a normal file. The old final-only check would
    // accept it, and canonicalizing root before inspecting it could hide it.
    assert!(
        fs::metadata(alias.join("connection.json"))
            .unwrap()
            .is_file()
    );
    assert!(safe_metadata(&alias.join("connection.json")).is_err());
    assert!(ProductionConnection::load(alias.join("connection.json")).is_err());
    assert!(safe_metadata(&alias).is_err());
    assert!(
        read_verified_production_artifact(
            &alias,
            "frame.png",
            &hex::encode(Sha256::digest(b"native-content")),
            1024,
        )
        .is_err()
    );
}

#[cfg(unix)]
#[test]
fn refuses_symlinked_intermediate_media_directory() {
    use std::os::unix::fs::symlink;

    let temp = tempfile::tempdir().unwrap();
    let owner = temp.path().join("owner");
    let actual = owner.join("verified");
    fs::create_dir_all(&actual).unwrap();
    fs::write(actual.join("frame.png"), b"native-frame").unwrap();
    symlink(&actual, owner.join("alias")).unwrap();
    assert!(
        read_verified_production_artifact(
            &owner,
            "alias/frame.png",
            &hex::encode(Sha256::digest(b"native-frame")),
            1024,
        )
        .is_err()
    );
}

#[cfg(target_os = "macos")]
#[test]
fn accepts_only_system_owned_legacy_macos_temp_aliases() {
    for (legacy, expected) in [
        ("/tmp", "/private/tmp"),
        ("/var", "/private/var"),
        ("/etc", "/private/etc"),
    ] {
        assert_eq!(
            trusted_macos_system_alias(Path::new(legacy)).as_deref(),
            Some(Path::new(expected)),
        );
    }
    let fixture = tempfile::Builder::new()
        .prefix("mw-v01-macos-owner-path-")
        .tempdir_in("/tmp")
        .unwrap();
    let path = fixture.path().join("verified.bin");
    fs::write(&path, b"trusted-temporary-native-file").unwrap();
    assert!(safe_metadata(&path).unwrap().is_file());
}

#[cfg(windows)]
#[test]
#[ignore = "Real NTFS junction fixture is created by the trusted Windows Actions runner"]
fn refuses_ntfs_junctions_from_ci_fixture() {
    // No first-party process execution primitive is introduced into the Rust
    // SDK. The Windows CI workflow creates a real NTFS junction outside the
    // checkout, sets these two fixture paths and invokes this ignored test.
    let real = PathBuf::from(
        std::env::var_os("MOTIONWRIGHT_V01_JUNCTION_REAL")
            .expect("Windows CI must supply the original fixture directory"),
    );
    let junction = PathBuf::from(
        std::env::var_os("MOTIONWRIGHT_V01_JUNCTION")
            .expect("Windows CI must supply the fixture NTFS junction"),
    );
    assert!(real.join("connection.json").is_file());
    assert_eq!(fs::read(real.join("frame.png")).unwrap(), b"native-frame");
    let attributes = fs::symlink_metadata(&junction).unwrap();
    assert!(is_link_or_reparse(&attributes));

    // The final file and hash are genuine; only the owner path is redirected.
    assert!(safe_metadata(&junction.join("connection.json")).is_err());
    assert!(ProductionConnection::load(junction.join("connection.json")).is_err());
    assert!(safe_metadata(&junction).is_err());
    assert!(
        read_verified_production_artifact(
            &junction,
            "frame.png",
            &hex::encode(Sha256::digest(b"native-frame")),
            1024,
        )
        .is_err()
    );
    // CI owns disposal of the junction; this read-only Rust fixture never
    // deletes it, follows it to mutate data or accesses a real owner session.
}
