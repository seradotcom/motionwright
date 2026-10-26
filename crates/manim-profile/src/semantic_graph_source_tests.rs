use super::*;
use motionwright_domain::{GraphContent, RationalTime};
use std::io::Write;

fn fixture() -> ManimSemanticGraphPlan {
    let plan: ManimSemanticGraphPlan =
        serde_json::from_str(include_str!("../testdata/semantic_graph_plan_v1.json")).unwrap();
    plan.validate().unwrap();
    plan
}

fn assets() -> BTreeMap<String, Vec<u8>> {
    [
        ("instrument-sans/LICENSE", include_bytes!("../testdata/graph-fonts/instrument-sans/LICENSE").as_slice()),
        ("instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2", include_bytes!("../testdata/graph-fonts/instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2").as_slice()),
        ("instrument-sans/files/instrument-sans-latin-wght-normal.woff2", include_bytes!("../testdata/graph-fonts/instrument-sans/files/instrument-sans-latin-wght-normal.woff2").as_slice()),
        ("ibm-plex-mono/LICENSE", include_bytes!("../testdata/graph-fonts/ibm-plex-mono/LICENSE").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2", include_bytes!("../testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2", include_bytes!("../testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2", include_bytes!("../testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2", include_bytes!("../testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2", include_bytes!("../testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2").as_slice()),
    ].into_iter().map(|(path, bytes): (&str, &[u8])| (path.to_owned(), bytes.to_vec())).collect()
}

fn archive_with(
    entries: &BTreeMap<String, Vec<u8>>,
    compression: zip::CompressionMethod,
) -> Vec<u8> {
    let mut archive = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(compression)
        .last_modified_time(zip::DateTime::default())
        .unix_permissions(0o600);
    for (path, bytes) in entries {
        archive.start_file(path, options).unwrap();
        archive.write_all(bytes).unwrap();
    }
    archive.finish().unwrap().into_inner()
}

#[test]
fn compiler_is_deterministic_and_binds_the_exact_canonical_source() {
    let plan = fixture();
    let first = compile_graph_program_source_only(&plan).unwrap();
    assert_eq!(first, compile_graph_program_source_only(&plan).unwrap());
    assert_eq!(
        first.plan_sha256,
        super::super::source_digest(&first.plan_document)
    );
    assert_eq!(
        first.source_sha256,
        super::super::source_digest(first.python_source.as_bytes())
    );
    assert_eq!(
        serde_json::from_slice::<ManimSemanticGraphPlan>(&first.plan_document).unwrap(),
        plan
    );
    assert!(!first.native_execution_performed);
    assert!(!first.runtime_sample_pts_verified);
    assert!(
        first
            .python_source
            .contains("class MotionwrightScene(Scene):")
    );
}

#[test]
fn payload_preserves_scene_relative_and_project_clocks_without_rounding() {
    let payload = graph_native_payload(&fixture()).unwrap();
    assert_eq!(
        payload["clock"],
        json!({
            "scene_frame_count": 100, "project_start_frame": 200,
            "graph_start_frame": 20, "graph_frame_count": 60,
        })
    );
    assert_eq!(payload["events"][0]["start_frame"], 20);
    assert_eq!(payload["events"][0]["end_frame_exclusive"], 30);
    assert_eq!(
        payload["render_profile"],
        json!({"width":640,"height":360,"frame_rate":10})
    );
}

#[test]
fn payload_uses_exact_canonical_geometry_and_fixed_arrow_convention() {
    let plan = fixture();
    let payload = graph_native_payload(&plan).unwrap();
    let sample = plan.sample(0).unwrap();
    assert_eq!(
        payload["edges"][0]["points"],
        serde_json::to_value(&sample.edges[0].points).unwrap()
    );
    assert_eq!(
        payload["edges"][0]["arrowhead"],
        serde_json::to_value(arrowhead(&sample.edges[0].points, 2.0).unwrap()).unwrap()
    );
    assert_eq!(payload["vertices"][0]["corner_radius"], 2.4);
    assert_eq!(
        payload["vertices"][0]["authored_box"],
        serde_json::to_value(&plan.source.content.vertices()[0].r#box).unwrap()
    );
}

#[test]
fn authored_python_looking_text_changes_only_the_inert_json_data() {
    let original = compile_graph_program_source_only(&fixture()).unwrap();
    let mut plan = fixture();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[0].label = r#""); __import__("os").system("not executed"); #"#.into();
    let changed = compile_graph_program_source_only(&plan).unwrap();
    let token = "# Fixed compiler-owned loader.";
    assert_eq!(
        original.python_source.split_once(token).unwrap().1,
        changed.python_source.split_once(token).unwrap().1
    );
    let data_line = changed
        .python_source
        .lines()
        .find(|line| line.starts_with("_DATA = "))
        .unwrap();
    let json_text: String =
        serde_json::from_str(data_line.strip_prefix("_DATA = ").unwrap()).unwrap();
    let value: Value = serde_json::from_str(&json_text).unwrap();
    assert_eq!(
        value["text_blocks"][0]["text"],
        plan.source.content.vertices()[0].label
    );
}

#[test]
fn impossible_literal_line_height_is_refused_before_program_generation() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[0].label = "A\nB\nC\nD\nE\nF".into();
    plan.validate().unwrap();
    let original = plan.clone();
    assert!(compile_graph_program_source_only(&plan).is_err());
    assert_eq!(plan, original);
}

#[test]
fn carriage_return_and_tab_are_rejected_without_normalizing_the_source() {
    for value in ["A\r\nB", "A\tB"] {
        let mut plan = fixture();
        let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
            unreachable!()
        };
        vertices[0].label = value.into();
        let source = plan.source.clone();
        assert!(compile_graph_program_source_only(&plan).is_err());
        assert_eq!(plan.source, source);
    }
}

#[test]
fn readback_paths_require_metrics_but_do_not_invent_native_ink() {
    let plan = fixture();
    let blocks = text_blocks(&plan).unwrap();
    let paths = graph_native_readback_paths_v1(&plan).unwrap();
    assert!(paths.required.contains("header.json"));
    assert!(paths.required.contains("frames.jsonl"));
    assert_eq!(paths.required.len(), blocks.len() + 2);
    for block in blocks {
        assert!(
            paths
                .required
                .contains(&format!("labels/text_block_{:04}.json", block.ordinal))
        );
        assert!(
            !paths
                .required
                .contains(&format!("labels/label_{:04}.svg", block.ordinal))
        );
        assert!(
            paths
                .allowed
                .contains(&format!("labels/label_{:04}.svg", block.ordinal))
        );
        assert!(
            paths
                .allowed
                .contains(&format!("labels/source_{:04}_0000.svg", block.ordinal))
        );
    }
    assert!(paths.required.is_subset(&paths.allowed));
}

#[test]
fn original_font_and_license_bytes_are_required_without_substitution() {
    let originals = assets();
    verify_assets(&originals).unwrap();
    assert_eq!(originals.len(), 9);
    for pin in graph_builtin_asset_pins() {
        assert_eq!(originals[pin.relative_path].len() as u64, pin.size_bytes);
        assert_eq!(
            super::super::source_digest(&originals[pin.relative_path]),
            pin.sha256
        );
    }
}

#[test]
fn missing_or_mutated_font_assets_never_trigger_a_fallback() {
    let mut missing = assets();
    missing.remove(BUILTIN_ASSETS[0].relative_path);
    assert!(graph_source_package_v1(&fixture(), &missing).is_err());
    let mut changed = assets();
    changed.get_mut(BUILTIN_ASSETS[1].relative_path).unwrap()[0] ^= 1;
    assert!(graph_source_package_v1(&fixture(), &changed).is_err());
}

#[test]
fn editable_bundle_contains_twenty_exact_entries_and_originals() {
    let plan = fixture();
    let originals = assets();
    let package = graph_source_package_v1(&plan, &originals).unwrap();
    assert_eq!(package.entries.len(), 20);
    for pin in graph_builtin_asset_pins() {
        assert_eq!(
            package.entries[&format!("graph-profile-v1/fonts/{}", pin.relative_path)],
            originals[pin.relative_path]
        );
    }
    for (module, bytes) in helper_sources() {
        assert_eq!(
            package.entries[&format!("graph-profile-v1/helpers/{module}.py")],
            bytes
        );
    }
    assert_eq!(package.closure.entries.len(), 20);
    let manifest: Value = serde_json::from_slice(&package.entries["manifest.json"]).unwrap();
    assert_eq!(manifest["profile_admitted"], false);
    assert_eq!(manifest["native_reopen_verified"], false);
}

#[test]
fn complete_source_bundle_reopens_as_typed_data_without_running_python() {
    let plan = fixture();
    let originals = assets();
    let raw = graph_source_bundle_v1(&plan, &originals).unwrap();
    let reopened = inspect_graph_source_bundle_v1(&raw).unwrap();
    assert_eq!(reopened.plan, plan);
    assert_eq!(
        super::super::encode_source_entries(&reopened.entries).unwrap(),
        raw
    );
}

#[test]
fn tampered_scene_python_is_rejected_even_if_zip_crc_is_valid() {
    let mut package = graph_source_package_v1(&fixture(), &assets()).unwrap();
    package
        .entries
        .get_mut("scene.py")
        .unwrap()
        .extend_from_slice(b"\nraise AssertionError('never executed')\n");
    let raw = archive_with(&package.entries, zip::CompressionMethod::Stored);
    assert!(inspect_graph_source_bundle_v1(&raw).is_err());
}

#[test]
fn tampered_helper_is_rejected_even_if_archive_metadata_is_rebuilt() {
    let mut package = graph_source_package_v1(&fixture(), &assets()).unwrap();
    package
        .entries
        .get_mut("graph-profile-v1/helpers/manim_graph_runtime.py")
        .unwrap()
        .push(b'\n');
    let raw = archive_with(&package.entries, zip::CompressionMethod::Stored);
    assert!(inspect_graph_source_bundle_v1(&raw).is_err());
}

#[test]
fn extra_archive_members_cannot_expand_the_closed_source_format() {
    let mut package = graph_source_package_v1(&fixture(), &assets()).unwrap();
    package
        .entries
        .insert("extra.py".into(), b"raise AssertionError".to_vec());
    assert!(
        inspect_graph_source_bundle_v1(&archive_with(
            &package.entries,
            zip::CompressionMethod::Stored
        ))
        .is_err()
    );
}

#[test]
fn mutated_license_is_not_accepted_as_equivalent_text() {
    let mut package = graph_source_package_v1(&fixture(), &assets()).unwrap();
    package
        .entries
        .get_mut("graph-profile-v1/fonts/instrument-sans/LICENSE")
        .unwrap()[0] ^= 1;
    assert!(
        inspect_graph_source_bundle_v1(&archive_with(
            &package.entries,
            zip::CompressionMethod::Stored
        ))
        .is_err()
    );
}

#[test]
fn source_edit_preserves_original_authorship_and_changes_bound_source() {
    let original = fixture();
    let mut edited = original.clone();
    let GraphContent::SystemFlow { vertices, .. } = &mut edited.source.content else {
        unreachable!()
    };
    vertices[0].label = "Edited".into();
    let originals = assets();
    let raw = graph_source_bundle_v1(&edited, &originals).unwrap();
    let reopened = inspect_graph_source_bundle_v1(&raw).unwrap();
    assert_eq!(reopened.plan.source, edited.source);
    assert_eq!(reopened.plan.source.authorship, original.source.authorship);
    assert_ne!(
        reopened.closure.plan_sha256,
        compile_graph_program_source_only(&original)
            .unwrap()
            .plan_sha256
    );
    assert_eq!(original.source.content.vertices()[0].label, "Origin\nA");
}

#[test]
fn context_rebinding_does_not_rewrite_the_historical_source_snapshot() {
    let original = fixture();
    let mut current = original.clone();
    current.execution_snapshot.revision += 1;
    let rebound = rebind_graph_source_v1(&original, &current, &assets()).unwrap();
    assert_eq!(rebound.plan.source, original.source);
    assert_eq!(rebound.plan.execution_snapshot.revision, 9);
    assert_eq!(rebound.plan.source.authorship.source_snapshot.revision, 7);
}

#[test]
fn semantic_changes_cannot_be_smuggled_through_context_rebinding() {
    let original = fixture();
    let mut changed = original.clone();
    changed.source.time_range.start = RationalTime::new(1, 1).unwrap();
    assert!(rebind_graph_source_v1(&original, &changed, &assets()).is_err());
    let mut foreign = original.clone();
    foreign.execution_snapshot.project_id = Uuid::from_u128(999);
    assert!(rebind_graph_source_v1(&original, &foreign, &assets()).is_err());
}

#[test]
fn oversized_or_invalid_archives_are_rejected_without_extraction() {
    assert!(inspect_graph_source_bundle_v1(&vec![0; MAX_SOURCE_BUNDLE_BYTES_V2 + 1]).is_err());
    assert!(inspect_graph_source_bundle_v1(b"not a zip").is_err());
}

#[test]
fn helper_inventory_is_complete_and_dependency_order_is_fixed() {
    let pins = graph_helper_pins();
    assert_eq!(
        pins.iter()
            .map(|row| row["module"].as_str().unwrap())
            .collect::<Vec<_>>(),
        [
            "manim_bundled_font_intake",
            "manim_graph_font_probe",
            "manim_graph_vector_text",
            "manim_graph_svg_import",
            "manim_graph_text_layout",
            "manim_graph_runtime",
        ]
    );
    for ((module, bytes), row) in helper_sources().into_iter().zip(pins) {
        assert_eq!(row["module"], module);
        assert_eq!(row["sha256"], super::super::source_digest(bytes));
        assert_eq!(row["size_bytes"], bytes.len());
    }
}

#[test]
fn shared_helper_loader_keeps_original_compiler_python_byte_identical() {
    const ORIGINAL_LOADER: &str =
        include_str!("../testdata/semantic_graph_loader_before_profile_probe.py");
    let combined = concat!(
        include_str!("semantic_graph_helper_loader.py"),
        include_str!("semantic_graph_loader.py"),
    );
    assert_eq!(combined.as_bytes(), ORIGINAL_LOADER.as_bytes());

    let plan = fixture();
    let data = serde_json::to_string(&graph_native_payload(&plan).unwrap()).unwrap();
    let quoted = serde_json::to_string(&data).unwrap();
    let original_program = format!(
        "# Generated by {GRAPH_COMPILER_V1}\n\
         # Canonical Graph data remains editable; imported Python is inert.\n\
         _DATA = {quoted}\n\
         {}",
        ORIGINAL_LOADER,
    );
    let actual = compile_graph_program_source_only(&plan).unwrap();
    assert_eq!(actual.python_source.as_bytes(), original_program.as_bytes());
    assert_eq!(
        actual.source_sha256,
        super::super::source_digest(original_program.as_bytes()),
    );
    let packaged = graph_source_package_v1(&plan, &assets()).unwrap();
    assert_eq!(
        packaged.entries["scene.py"].as_slice(),
        original_program.as_bytes()
    );
}
