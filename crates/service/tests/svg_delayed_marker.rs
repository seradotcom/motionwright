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

fn encoded_utf16_xml(text: &str, little: bool, with_bom: bool) -> Vec<u8> {
    let mut bytes = Vec::new();
    if with_bom {
        bytes.extend_from_slice(if little { &[0xff, 0xfe] } else { &[0xfe, 0xff] });
    }
    for unit in text.encode_utf16() {
        bytes.extend_from_slice(&if little {
            unit.to_le_bytes()
        } else {
            unit.to_be_bytes()
        });
    }
    bytes
}

fn encoded_utf32_xml(text: &str, little: bool) -> Vec<u8> {
    let mut bytes = if little {
        vec![0xff, 0xfe, 0, 0]
    } else {
        vec![0, 0, 0xfe, 0xff]
    };
    for letter in text.chars() {
        let unit = letter as u32;
        bytes.extend_from_slice(&if little {
            unit.to_le_bytes()
        } else {
            unit.to_be_bytes()
        });
    }
    bytes
}

#[test]
fn utf16_and_utf32_disguised_active_svg_never_becomes_raster_cas() {
    let active = r#"<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>"#;
    let padded = format!("{}{}", " ".repeat(17_031), active);
    let cases = [
        (
            "utf16le_bom",
            encoded_utf16_xml(active, true, true),
            "image/png",
        ),
        (
            "utf16be_padded",
            encoded_utf16_xml(&padded, false, true),
            "image/png",
        ),
        (
            "utf16le_no_bom",
            encoded_utf16_xml(&padded, true, false),
            "image/png",
        ),
        ("utf32le_bom", encoded_utf32_xml(active, true), "image/png"),
        (
            "utf32be_bom",
            encoded_utf32_xml(active, false),
            "application/octet-stream",
        ),
    ];

    for (name, bytes, declared_type) in cases {
        let root = tempfile::tempdir().unwrap();
        let database = root.path().join("project.sqlite3");
        let source = root.path().join("masquerade.png");
        fs::write(&source, &bytes).unwrap();
        let service = StudioService::open(&database).unwrap();
        let project = service.create_project("No encoded SVG imports").unwrap();
        let before = service.export_project(project.id).unwrap();
        let sha = hex::encode(Sha256::digest(&bytes));
        let result = service.import_asset_file(
            project.id,
            &project.stamp(),
            &format!("encoded-svg-{name}"),
            &source,
            "masquerade.png".into(),
            declared_type.into(),
        );
        assert!(
            result.is_err(),
            "encoded active SVG admitted as media: {name}"
        );
        let after = service.export_project(project.id).unwrap();
        assert_eq!(after.project, before.project, "case={name}");
        assert_eq!(after.events, before.events, "case={name}");
        assert_eq!(after.payload_sha256, before.payload_sha256, "case={name}");
        assert!(
            service.store().lock().verify_blob(&sha).is_err(),
            "case={name}"
        );
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(
            reopened.project(project.id).unwrap(),
            project,
            "case={name}"
        );
        assert!(
            reopened.store().lock().verify_blob(&sha).is_err(),
            "case={name}"
        );
        let staged = root.path().join("blobs/.staging");
        assert!(
            fs::read_dir(staged).unwrap().next().is_none(),
            "case={name}"
        );
        assert_eq!(fs::read(&source).unwrap(), bytes, "case={name}");
    }
}

#[test]
fn legitimate_utf16_plain_text_without_svg_remains_importable() {
    let root = tempfile::tempdir().unwrap();
    let database = root.path().join("project.sqlite3");
    let source = root.path().join("notes.txt");
    let note = encoded_utf16_xml("Ordinary owner notes ? no executable markup", true, true);
    fs::write(&source, &note).unwrap();
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Unicode text imported").unwrap();
    let committed = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "legitimate-utf16-text",
            &source,
            "notes.txt".into(),
            "text/plain".into(),
        )
        .unwrap();
    assert_eq!(committed.project.revision, 1);
    let digest = hex::encode(Sha256::digest(&note));
    assert_eq!(
        service.store().lock().read_blob(&digest, 4096).unwrap(),
        note
    );
    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), committed.project);
    assert_eq!(
        reopened.store().lock().read_blob(&digest, 4096).unwrap(),
        note
    );
}
