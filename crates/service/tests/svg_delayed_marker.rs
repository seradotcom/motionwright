//! Content-bound SVG admission regardless of a caller-selected image MIME.
//! Real StudioService, temporary SQLite, immutable staging/CAS and reopen.
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use std::fs;

const ONE_PIXEL_PNG: &[u8] = &[
    137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0,
    0, 0, 144, 119, 83, 222, 0, 0, 0, 12, 73, 68, 65, 84, 120, 156, 99, 248, 165, 17, 0, 0, 3, 146,
    1, 115, 179, 56, 0, 66, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130,
];

fn active_svg_with_prefix(case: &str) -> Vec<u8> {
    let mut bytes = match case {
        "long_whitespace" => vec![b' '; 16 * 1024 + 173],
        "long_comment" => {
            let mut body = b"<!--".to_vec();
            body.extend(vec![b'X'; 16 * 1024 + 211]);
            body.extend_from_slice(b"-->");
            body
        }
        "chunk_boundary" => vec![b' '; 16 * 1024 - 2],
        _ => panic!("unknown synthetic fixture"),
    };
    bytes.extend_from_slice(
        br#"<svg xmlns="http://www.w3.org/2000/svg" onload="alert('no')"><script>alert(1)</script></svg>"#,
    );
    bytes
}

#[test]
fn svg_active_markup_beyond_old_sniff_window_cannot_enter_cas() {
    for case in ["long_whitespace", "long_comment", "chunk_boundary"] {
        let root = tempfile::tempdir().unwrap();
        let database = root.path().join("original.sqlite3");
        let source = root.path().join("mislabelled.png");
        let bytes = active_svg_with_prefix(case);
        let digest = hex::encode(Sha256::digest(&bytes));
        fs::write(&source, &bytes).unwrap();
        let service = StudioService::open(&database).unwrap();
        let project = service.create_project("SVG content guard").unwrap();
        let old_backup = service.export_project(project.id).unwrap();

        let result = service.import_asset_file(
            project.id,
            &project.stamp(),
            &format!("svg-case-{case}"),
            &source,
            "mislabelled.png".into(),
            "image/png".into(),
        );
        assert!(result.is_err(), "active disguised SVG admitted: {case}");

        let after = service.export_project(project.id).unwrap();
        assert_eq!(after.project, old_backup.project, "case={case}");
        assert_eq!(after.events, old_backup.events, "case={case}");
        assert_eq!(
            after.payload_sha256, old_backup.payload_sha256,
            "case={case}"
        );
        assert!(
            service.store().lock().verify_blob(&digest).is_err(),
            "case={case}"
        );

        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(
            reopened.project(project.id).unwrap(),
            project,
            "case={case}"
        );
        assert!(
            reopened.store().lock().verify_blob(&digest).is_err(),
            "case={case}"
        );
        let staging = root.path().join("blobs/.staging");
        assert!(
            fs::read_dir(&staging).unwrap().next().is_none(),
            "private capture survived refused import: {case}"
        );
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }
}

#[test]
fn real_png_still_imports_and_survives_sqlite_reopen() {
    let root = tempfile::tempdir().unwrap();
    let database = root.path().join("original.sqlite3");
    let source = root.path().join("pixel.png");
    fs::write(&source, ONE_PIXEL_PNG).unwrap();
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Genuine image").unwrap();
    let outcome = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "genuine-image-png",
            &source,
            "pixel.png".into(),
            "image/png".into(),
        )
        .unwrap();
    assert_eq!(outcome.project.revision, project.revision + 1);
    let digest = hex::encode(Sha256::digest(ONE_PIXEL_PNG));
    assert_eq!(
        service.store().lock().read_blob(&digest, 1024).unwrap(),
        ONE_PIXEL_PNG
    );
    drop(service);

    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), outcome.project);
    assert_eq!(
        reopened.store().lock().read_blob(&digest, 1024).unwrap(),
        ONE_PIXEL_PNG
    );
}
