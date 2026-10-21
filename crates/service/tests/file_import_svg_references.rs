//! SVG admission must inspect XML/CSS meanings while preserving original bytes.
//! These fixtures are imported only; no renderer or network request is invoked.
use motionwright_service::StudioService;
use rusqlite::{Connection, OpenFlags};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

fn regular_files(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn visit(root: &Path, current: &Path, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
        for item in fs::read_dir(current).unwrap() {
            let path = item.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            if metadata.is_dir() {
                visit(root, &path, files);
            } else {
                assert!(metadata.is_file());
                files.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    fs::read(path).unwrap(),
                );
            }
        }
    }
    let mut files = BTreeMap::new();
    visit(root, root, &mut files);
    files
}

fn rejects_reference(svg: &str) {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("reference.svg");
    fs::write(&source, svg.as_bytes()).unwrap();
    let database = directory.path().join("state.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Decoded SVG admission").unwrap();
    let before = service.export_project(project.id).unwrap();
    let blob_root = service.store().lock().blob_root().to_path_buf();
    let blobs_before = regular_files(&blob_root);
    let connection =
        Connection::open_with_flags(&database, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let receipt_count = || {
        connection
            .query_row(
                "SELECT COUNT(*) FROM receipts WHERE project_id=?1",
                [project.id.to_string()],
                |row| row.get::<_, i64>(0),
            )
            .unwrap()
    };
    let receipts_before = receipt_count();

    let error = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "encoded-reference-new-request",
            &source,
            "Reference rejection".into(),
            "image/svg+xml".into(),
        )
        .expect_err("an encoded CSS reference was admitted as an immutable SVG asset");
    assert!(
        error
            .to_string()
            .contains("active or externally-referencing SVG assets are not admitted"),
        "expected active SVG admission error, got {error}"
    );
    let after = service.export_project(project.id).unwrap();
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
    assert_eq!(receipt_count(), receipts_before);
    assert_eq!(regular_files(&blob_root), blobs_before);
    assert_eq!(fs::read(source).unwrap(), svg.as_bytes());
}

fn imports_exact_static_svg(svg: &str) {
    imports_exact_asset(svg, "static.svg", "image/svg+xml");
}

fn imports_exact_asset(svg: &str, file_name: &str, media_type: &str) {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join(file_name);
    fs::write(&source, svg.as_bytes()).unwrap();
    let service = StudioService::open(directory.path().join("state.sqlite3")).unwrap();
    let project = service.create_project("Static SVG compatibility").unwrap();
    let outcome = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "static-svg-control",
            &source,
            "Static SVG".into(),
            media_type.into(),
        )
        .unwrap();
    assert!(!outcome.replayed);
    assert_eq!(outcome.project.revision, project.revision + 1);
    let digest = outcome.project.assets[0].content_sha256.as_ref().unwrap();
    assert_eq!(
        service.store().lock().read_blob(digest, 8192).unwrap(),
        svg.as_bytes()
    );
    assert_eq!(fs::read(source).unwrap(), svg.as_bytes());
}

#[test]
fn svg_style_attribute_decodes_xml_entities_before_reference_admission() {
    rejects_reference(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:u&#114;l(https://example.invalid/paint.svg#p)" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_presentation_attribute_decodes_xml_entities_before_reference_admission() {
    rejects_reference(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><rect fill="u&#x72;l(https://example.invalid/paint.svg#p)" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_style_attribute_decodes_css_identifier_escapes_before_admission() {
    rejects_reference(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:u\000072 l(https://example.invalid/paint.svg#p)" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_style_element_decodes_css_at_rule_escapes_before_admission() {
    rejects_reference(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><style>@im\70ort "https://example.invalid/style.css";</style><rect width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_namespaced_style_element_decodes_xml_entities_before_admission() {
    rejects_reference(
        r#"<s:svg xmlns:s="http://www.w3.org/2000/svg"><s:style>.mark { fill: u&#114;l(https://example.invalid/paint.svg#p); }</s:style><s:rect class="mark" width="8" height="8"/></s:svg>"#,
    );
}

#[test]
fn ordinary_svg_entities_and_static_styles_preserve_original_bytes() {
    imports_exact_static_svg(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><style>.mark { fill: red; stroke-width: 2; }</style><text class="mark" x="1">A &amp; B &#x26; C</text><rect fill="r&#101;d" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn ordinary_svg_css_identifier_escapes_preserve_original_bytes() {
    imports_exact_static_svg(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><rect style="fi\6cl: red; stroke-width: 2" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_style_text_fragments_are_joined_before_css_reference_admission() {
    rejects_reference(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><style>.mark { fill: u<!-- adjacent -->&#114;<![CDATA[l(]]>https://example.invalid/paint.svg#p); }</style><rect class="mark" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_nested_css_blocks_cannot_hide_decoded_references() {
    rejects_reference(
        r#"<svg xmlns="http://www.w3.org/2000/svg"><style>@media all { .mark { fill: u\72l(https://example.invalid/paint.svg#p); } }</style><rect class="mark" width="8" height="8"/></svg>"#,
    );
}

#[test]
fn svg_css_image_constructors_cannot_load_quoted_resources() {
    for css in [
        r#"background-image: image("https://example.invalid/image.png")"#,
        r#"background-image: image-set("https://example.invalid/image.png" 1x)"#,
        r#"background-image: -webkit-image-set("https://example.invalid/image.png" 1x)"#,
        r#"background-image: src("https://example.invalid/image.png")"#,
    ] {
        let svg = format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg"><rect style='{css}' width="8" height="8"/></svg>"#
        );
        rejects_reference(&svg);
    }
}

#[test]
fn svg_animation_cannot_replace_an_admitted_local_reference() {
    for mutation in [
        r#"<set attributeName="href" to="https://example.invalid/image.png" begin="0s"/>"#,
        r##"<animate attributeName="href" values="#local;https://example.invalid/image.png" dur="1s"/>"##,
    ] {
        let svg = format!(
            r##"<svg xmlns="http://www.w3.org/2000/svg"><image href="#local" width="8" height="8">{mutation}</image></svg>"##
        );
        rejects_reference(&svg);
    }
}

#[test]
fn ordinary_svg_nested_css_functions_and_local_fragments_keep_original_bytes() {
    imports_exact_static_svg(
        r##"<?xml version="1.0" encoding="UTF-8"?><s:svg xmlns:s="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><s:defs><s:path id="mark" d="M0 0 L8 8"/></s:defs><s:style>@media all { .mark { fill: rgb(12 34 56); stroke-width: calc(1 + 1); } }</s:style><s:use xlink:href="#mark" class="mark"/><s:text>Válido &amp; estático</s:text></s:svg>"##,
    );
}

#[test]
fn ordinary_xml_like_asset_remains_compatible_with_conservative_sniffing() {
    imports_exact_asset(
        r#"<?xml version="1.0"?><metadata><title>A &amp; B</title><value>42</value></metadata>"#,
        "metadata.xml",
        "application/xml",
    );
}
