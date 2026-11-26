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

fn original_image_sources() -> [(&'static [u8], &'static str); 2] {
    const JPEG: &[u8] = &[
        255, 216, 255, 224, 0, 16, 74, 70, 73, 70, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 255, 219, 0, 67,
        0, 3, 2, 2, 3, 2, 2, 3, 3, 3, 3, 4, 3, 3, 4, 5, 8, 5, 5, 4, 4, 5, 10, 7, 7, 6, 8, 12, 10,
        12, 12, 11, 10, 11, 11, 13, 14, 18, 16, 13, 14, 17, 14, 11, 11, 16, 22, 16, 17, 19, 20, 21,
        21, 21, 12, 15, 23, 24, 22, 20, 24, 18, 20, 21, 20, 255, 219, 0, 67, 1, 3, 4, 4, 5, 4, 5,
        9, 5, 5, 9, 20, 13, 11, 13, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
        20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20,
        20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 255, 192, 0, 17, 8, 0, 16, 0, 32, 3, 1, 34, 0,
        2, 17, 1, 3, 17, 1, 255, 196, 0, 31, 0, 0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0,
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 255, 196, 0, 181, 16, 0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4,
        4, 0, 0, 1, 125, 1, 2, 3, 0, 4, 17, 5, 18, 33, 49, 65, 6, 19, 81, 97, 7, 34, 113, 20, 50,
        129, 145, 161, 8, 35, 66, 177, 193, 21, 82, 209, 240, 36, 51, 98, 114, 130, 9, 10, 22, 23,
        24, 25, 26, 37, 38, 39, 40, 41, 42, 52, 53, 54, 55, 56, 57, 58, 67, 68, 69, 70, 71, 72, 73,
        74, 83, 84, 85, 86, 87, 88, 89, 90, 99, 100, 101, 102, 103, 104, 105, 106, 115, 116, 117,
        118, 119, 120, 121, 122, 131, 132, 133, 134, 135, 136, 137, 138, 146, 147, 148, 149, 150,
        151, 152, 153, 154, 162, 163, 164, 165, 166, 167, 168, 169, 170, 178, 179, 180, 181, 182,
        183, 184, 185, 186, 194, 195, 196, 197, 198, 199, 200, 201, 202, 210, 211, 212, 213, 214,
        215, 216, 217, 218, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 241, 242, 243, 244,
        245, 246, 247, 248, 249, 250, 255, 196, 0, 31, 1, 0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0,
        0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 255, 196, 0, 181, 17, 0, 2, 1, 2, 4, 4, 3, 4,
        7, 5, 4, 4, 0, 1, 2, 119, 0, 1, 2, 3, 17, 4, 5, 33, 49, 6, 18, 65, 81, 7, 97, 113, 19, 34,
        50, 129, 8, 20, 66, 145, 161, 177, 193, 9, 35, 51, 82, 240, 21, 98, 114, 209, 10, 22, 36,
        52, 225, 37, 241, 23, 24, 25, 26, 38, 39, 40, 41, 42, 53, 54, 55, 56, 57, 58, 67, 68, 69,
        70, 71, 72, 73, 74, 83, 84, 85, 86, 87, 88, 89, 90, 99, 100, 101, 102, 103, 104, 105, 106,
        115, 116, 117, 118, 119, 120, 121, 122, 130, 131, 132, 133, 134, 135, 136, 137, 138, 146,
        147, 148, 149, 150, 151, 152, 153, 154, 162, 163, 164, 165, 166, 167, 168, 169, 170, 178,
        179, 180, 181, 182, 183, 184, 185, 186, 194, 195, 196, 197, 198, 199, 200, 201, 202, 210,
        211, 212, 213, 214, 215, 216, 217, 218, 226, 227, 228, 229, 230, 231, 232, 233, 234, 242,
        243, 244, 245, 246, 247, 248, 249, 250, 255, 218, 0, 12, 3, 1, 0, 2, 17, 3, 17, 0, 63, 0,
        125, 20, 81, 95, 206, 135, 246, 88, 81, 69, 20, 1, 255, 217,
    ];
    [
        (
            include_bytes!("../../../apps/desktop/src-tauri/icons/icon.png"),
            "image/png",
        ),
        (JPEG, "image/jpeg"),
    ]
}

#[test]
fn original_static_images_keep_exact_cas_bytes_after_input_deletion_and_store_reopen() {
    for (bytes, mime) in original_image_sources() {
        let root = tempfile::tempdir().unwrap();
        let db = root.path().join("images.sqlite3");
        let service = StudioService::open(&db).unwrap();
        let project = import(&service, root.path(), bytes, mime);
        let before = service.history(project.id, 0, 100).unwrap();
        drop(service);
        let service = StudioService::open(&db).unwrap();
        let asset = project.assets.last().unwrap();
        let mut image = service
            .verified_asset_media(
                project.id,
                &project.stamp(),
                asset.id,
                asset.content_sha256.as_ref().unwrap(),
            )
            .unwrap();
        assert!(image.media_kind().is_image());
        assert_eq!(image.media_kind().maximum_bytes(), 16 * 1024 * 1024);
        assert_eq!(image.media_kind().media_type(), mime);
        assert_eq!(image.read_range(0, bytes.len() as u64 - 1).unwrap(), bytes);
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(service.history(project.id, 0, 100).unwrap(), before);
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
    }
}

#[test]
fn original_image_bounds_refuse_animation_dimensions_headers_and_oversize_without_mutation() {
    let [(png, _), (jpeg, _)] = original_image_sources();
    let mut too_wide = png.to_vec();
    too_wide[16..20].copy_from_slice(&8193_u32.to_be_bytes());
    let mut too_many_pixels = png.to_vec();
    too_many_pixels[16..20].copy_from_slice(&8192_u32.to_be_bytes());
    too_many_pixels[20..24].copy_from_slice(&8192_u32.to_be_bytes());
    let mut animated = png[..33].to_vec();
    animated.extend_from_slice(&8_u32.to_be_bytes());
    animated.extend_from_slice(b"acTL");
    animated.extend_from_slice(&[0; 12]);
    animated.extend_from_slice(&png[33..]);
    let mut oversized = png.to_vec();
    oversized.resize(16 * 1024 * 1024 + 1, 0);
    let sof = jpeg.windows(2).position(|v| v == [0xff, 0xc0]).unwrap();
    let mut dynamic_height = jpeg.to_vec();
    dynamic_height[sof + 5..sof + 7].copy_from_slice(&0_u16.to_be_bytes());
    let mut large_jpeg = jpeg.to_vec();
    large_jpeg[sof + 7..sof + 9].copy_from_slice(&8193_u16.to_be_bytes());
    let mut header_bomb = vec![0xff, 0xd8];
    for _ in 0..17 {
        header_bomb.extend_from_slice(&[0xff, 0xe1, 0xff, 0xff]);
        header_bomb.extend(std::iter::repeat_n(0, 65_533));
    }
    header_bomb.extend_from_slice(&jpeg[2..]);
    for (bytes, mime) in [
        (too_wide, "image/png"),
        (too_many_pixels, "image/png"),
        (animated, "image/png"),
        (oversized, "image/png"),
        (png[..png.len() - 2].to_vec(), "image/png"),
        (dynamic_height, "image/jpeg"),
        (large_jpeg, "image/jpeg"),
        (header_bomb, "image/jpeg"),
        (png.to_vec(), "image/jpeg"),
        (jpeg.to_vec(), "image/png"),
        (png.to_vec(), "image/svg+xml"),
    ] {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("invalid-image.sqlite3")).unwrap();
        let project = import(&service, root.path(), &bytes, mime);
        let before = service.history(project.id, 0, 100).unwrap();
        let asset = project.assets.last().unwrap();
        assert!(
            service
                .verified_asset_media(
                    project.id,
                    &project.stamp(),
                    asset.id,
                    asset.content_sha256.as_ref().unwrap(),
                )
                .is_err(),
            "{mime}: {} bytes",
            bytes.len()
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(service.history(project.id, 0, 100).unwrap(), before);
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
    }
}
