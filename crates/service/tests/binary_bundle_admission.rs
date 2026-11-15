//! Content admission of a binary prefix that is ambiguous with one UTF-32 unit.
//! Real SQLite/CAS and bundle restore; no decoder or Native acceptance claim.
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use std::fs;

// Authenticated prefix of bf7f1b13a2ce8401b47a4f71a26094280313e86c264a1bd9f4e339222f8db031
// (original 274124 bytes). These 64 bytes are a policy fixture, not a full video.
const ORIGINAL_MP4_PREFIX: &[u8] = &[
    0x00, 0x00, 0x00, 0x20, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d, 0x00, 0x00, 0x02, 0x00,
    0x69, 0x73, 0x6f, 0x6d, 0x69, 0x73, 0x6f, 0x32, 0x61, 0x76, 0x63, 0x31, 0x6d, 0x70, 0x34, 0x31,
    0x00, 0x00, 0x04, 0x8b, 0x6d, 0x6f, 0x6f, 0x76, 0x00, 0x00, 0x00, 0x6c, 0x6d, 0x76, 0x68, 0x64,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0xe8,
];

#[test]
fn binary_box_size_survives_fingerprinted_bundle_restore() {
    let temporary = tempfile::tempdir().unwrap();
    let source_root = temporary.path().join("source");
    let restored_root = temporary.path().join("restored");
    fs::create_dir(&source_root).unwrap();
    fs::create_dir(&restored_root).unwrap();
    let source_database = source_root.join("state.sqlite3");
    let restored_database = restored_root.join("state.sqlite3");
    let input = temporary.path().join("binary-prefix.bin");
    fs::write(&input, ORIGINAL_MP4_PREFIX).unwrap();

    let source = StudioService::open(&source_database).unwrap();
    let initial = source.create_project("Original binary prefix").unwrap();
    let project = source
        .import_asset_file(
            initial.id,
            &initial.stamp(),
            "original-binary-prefix",
            &input,
            "media-prefix.bin".into(),
            "video/mp4".into(),
        )
        .unwrap()
        .project;
    let digest = hex::encode(Sha256::digest(ORIGINAL_MP4_PREFIX));
    assert_eq!(
        project.assets[0].content_sha256.as_deref(),
        Some(digest.as_str())
    );

    let bundle = temporary.path().join("portable");
    let exported = source
        .export_project_bundle_at(project.id, &project.stamp(), &bundle)
        .unwrap();
    let manifest_before = fs::read(bundle.join("manifest.json")).unwrap();
    let blob = &exported.blobs[0];
    assert_eq!(blob.sha256, digest);
    assert_eq!(blob.size_bytes, ORIGINAL_MP4_PREFIX.len() as u64);
    assert_eq!(
        fs::read(bundle.join(&blob.relative_path)).unwrap(),
        ORIGINAL_MP4_PREFIX
    );

    let destination = StudioService::open(&restored_database).unwrap();
    let inspected = destination
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    let imported = destination
        .import_project_bundle_inspected(&bundle, &inspected.fingerprint)
        .unwrap();
    assert_eq!(imported.id, project.id);
    assert_eq!(imported.revision, project.revision);
    assert_eq!(imported.assets, project.assets);
    assert_ne!(imported.generation, project.generation);
    drop(destination);

    let reopened = StudioService::open(&restored_database).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), imported);
    assert_eq!(
        reopened.store().lock().read_blob(&digest, 64).unwrap(),
        ORIGINAL_MP4_PREFIX
    );
    assert_eq!(
        fs::read(bundle.join("manifest.json")).unwrap(),
        manifest_before
    );
    assert_eq!(
        fs::read(bundle.join(&blob.relative_path)).unwrap(),
        ORIGINAL_MP4_PREFIX
    );
    assert_eq!(fs::read(&input).unwrap(), ORIGINAL_MP4_PREFIX);
    assert_eq!(source.project(project.id).unwrap(), project);
    drop(source);

    let source_reopened = StudioService::open(&source_database).unwrap();
    assert_eq!(source_reopened.project(project.id).unwrap(), project);
    assert_eq!(
        source_reopened
            .store()
            .lock()
            .read_blob(&digest, 64)
            .unwrap(),
        ORIGINAL_MP4_PREFIX
    );
}
