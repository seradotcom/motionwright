//! SQLite/CAS original-media reads. MP4-shaped cases test boundaries, not decoding.
use motionwright_domain::{Change, Project};
use motionwright_service::StudioService;
use motionwright_storage::{ASSET_MEDIA_RANGE_BYTES, Store};
use std::{
    fs,
    io::{Seek, SeekFrom, Write},
    path::Path,
};
use uuid::Uuid;

fn import(service: &StudioService, root: &Path, bytes: &[u8], mime: &str) -> Project {
    let project = service.create_project("Original media").unwrap();
    let path = root.join(format!("input-{}", Uuid::new_v4()));
    fs::write(&path, bytes).unwrap();
    let project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "import-original",
            &path,
            "Original imported source".into(),
            mime.into(),
        )
        .unwrap()
        .project;
    fs::remove_file(path).unwrap();
    project
}
fn mp4(size: usize) -> Vec<u8> {
    let mut bytes = (0..size)
        .map(|index| (index % 251) as u8)
        .collect::<Vec<_>>();
    bytes[4..8].copy_from_slice(b"ftyp");
    bytes
}
fn wav() -> Vec<u8> {
    let mut bytes = Vec::new();
    let samples = 8_000u32;
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + samples * 2).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16u32.to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&8_000u32.to_le_bytes());
    bytes.extend_from_slice(&16_000u32.to_le_bytes());
    bytes.extend_from_slice(&2u16.to_le_bytes());
    bytes.extend_from_slice(&16u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&(samples * 2).to_le_bytes());
    for i in 0..samples {
        bytes.extend_from_slice(&((i % 200) as i16 * 40).to_le_bytes());
    }
    bytes
}
#[test]
fn original_media_above_old_ipc_cap_reads_exact_ranges_after_store_reopen_without_input_file() {
    let root = tempfile::tempdir().unwrap();
    let db = root.path().join("media.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let bytes = mp4(16 * 1024 * 1024 + 259);
    let project = import(&service, root.path(), &bytes, "video/mp4");
    let asset = project.assets.last().unwrap();
    let digest = asset.content_sha256.as_ref().unwrap();
    let events = service.history(project.id, 0, 100).unwrap();
    let mut media = service
        .verified_asset_media(project.id, &project.stamp(), asset.id, digest)
        .unwrap();
    assert_eq!(media.size_bytes(), bytes.len() as u64);
    assert_eq!(media.media_kind().media_type(), "video/mp4");
    assert_eq!(
        media.read_range(1_048_568, 1_048_590).unwrap(),
        bytes[1_048_568..=1_048_590]
    );
    assert_eq!(
        media
            .read_range(bytes.len() as u64 - 73, bytes.len() as u64 - 1)
            .unwrap(),
        bytes[bytes.len() - 73..]
    );
    assert!(media.read_range(0, ASSET_MEDIA_RANGE_BYTES).is_err());
    assert!(media.read_range(5, 4).is_err());
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(service.history(project.id, 0, 100).unwrap(), events);
    drop(media);
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let mut media = reopened
        .verified_asset_media(project.id, &project.stamp(), asset.id, digest)
        .unwrap();
    assert_eq!(media.read_range(0, 31).unwrap(), bytes[..32]);
    assert_eq!(reopened.history(project.id, 0, 100).unwrap(), events);
}
#[test]
fn source_scope_header_digest_and_post_capture_corruption_fail_closed() {
    let root = tempfile::tempdir().unwrap();
    let db = root.path().join("media.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let project = import(
        &service,
        root.path(),
        &mp4(2 * 1024 * 1024 + 19),
        "video/mp4",
    );
    let asset = project.assets.last().unwrap();
    let digest = asset.content_sha256.as_ref().unwrap();
    for stamp in [
        motionwright_domain::RevisionStamp {
            revision: project.revision + 1,
            ..project.stamp()
        },
        motionwright_domain::RevisionStamp {
            generation: Uuid::new_v4(),
            ..project.stamp()
        },
        motionwright_domain::RevisionStamp {
            resource: format!("project:{}", Uuid::new_v4()),
            ..project.stamp()
        },
    ] {
        assert!(
            service
                .verified_asset_media(project.id, &stamp, asset.id, digest)
                .is_err()
        );
    }
    assert!(
        service
            .verified_asset_media(project.id, &project.stamp(), Uuid::new_v4(), digest)
            .is_err()
    );
    assert!(
        service
            .verified_asset_media(project.id, &project.stamp(), asset.id, &"00".repeat(32))
            .is_err()
    );
    let mut media = service
        .verified_asset_media(project.id, &project.stamp(), asset.id, digest)
        .unwrap();
    let store = Store::open(&db).unwrap();
    let path = store.verified_blob_path(digest).unwrap();
    drop(store);
    let mut writer = fs::OpenOptions::new().write(true).open(path).unwrap();
    writer.seek(SeekFrom::Start(1_048_600)).unwrap();
    writer.write_all(b"changed original").unwrap();
    writer.sync_all().unwrap();
    assert!(media.read_range(1_048_590, 1_048_640).is_err());
    assert!(
        service
            .verified_asset_media(project.id, &project.stamp(), asset.id, digest)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    let wrong_type = import(&service, root.path(), &wav(), "video/mp4");
    let bad = wrong_type.assets.last().unwrap();
    assert!(
        service
            .verified_asset_media(
                wrong_type.id,
                &wrong_type.stamp(),
                bad.id,
                bad.content_sha256.as_ref().unwrap()
            )
            .is_err()
    );
}
#[test]
fn original_wav_is_retained_and_a_later_project_revision_requires_reselection() {
    let root = tempfile::tempdir().unwrap();
    let service = StudioService::open(root.path().join("wav.sqlite3")).unwrap();
    let bytes = wav();
    let project = import(&service, root.path(), &bytes, "audio/x-wav");
    let asset = project.assets.last().unwrap();
    let digest = asset.content_sha256.as_ref().unwrap();
    let mut media = service
        .verified_asset_media(project.id, &project.stamp(), asset.id, digest)
        .unwrap();
    assert_eq!(media.media_kind().media_type(), "audio/wav");
    assert_eq!(media.read_range(0, bytes.len() as u64 - 1).unwrap(), bytes);
    let renamed = service
        .apply(
            project.id,
            &project.stamp(),
            "later-project-edit",
            &Change::RenameProject {
                title: "Later saved edit".into(),
            },
        )
        .unwrap()
        .project;
    assert!(
        service
            .verified_asset_media(project.id, &project.stamp(), asset.id, digest)
            .is_err()
    );
    assert!(
        service
            .verified_asset_media(project.id, &renamed.stamp(), asset.id, digest)
            .is_ok()
    );
}
