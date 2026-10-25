//! Source/import regressions only. These use real canonical domain fixtures and
//! pinned original font bytes; they do not manufacture a StudioService admission,
//! provider, native result, pixels, receipt, or human approval.
use super::*;
use motionwright_domain::{Change, GraphContent, LockKind, ProjectLock};
use motionwright_manim_profile::semantic_graph_source::{
    graph_source_bundle_v1, graph_source_package_v1,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

use crate::semantic_graph_source_fixture as source;

fn assets() -> BTreeMap<String, Vec<u8>> {
    [
        ("instrument-sans/LICENSE", include_bytes!("../../manim-profile/testdata/graph-fonts/instrument-sans/LICENSE").as_slice()),
        ("instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2").as_slice()),
        ("instrument-sans/files/instrument-sans-latin-wght-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/instrument-sans/files/instrument-sans-latin-wght-normal.woff2").as_slice()),
        ("ibm-plex-mono/LICENSE", include_bytes!("../../manim-profile/testdata/graph-fonts/ibm-plex-mono/LICENSE").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2").as_slice()),
        ("ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2", include_bytes!("../../manim-profile/testdata/graph-fonts/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2").as_slice()),
    ].into_iter().map(|(name, bytes)| (name.into(), bytes.to_vec())).collect()
}

fn fixture() -> (Project, GraphPackage, BTreeMap<String, Vec<u8>>) {
    let mut project = source::seed_domain();
    let output = &mut project.deliverables[0];
    output.width = 640;
    output.height = 360;
    output.container = OutputContainer::Mp4;
    output.video_codec = VideoCodec::H264;
    output.color_space = OutputColorSpace::Rec709;
    output.burn_in_captions = false;
    let mut graph = source::graph_for(&project, true);
    graph.time_range.start = motionwright_domain::RationalTime::new(0, 1).unwrap();
    graph.time_range.duration = project.scenes[0].duration;
    let graph_id = graph.id;
    let deliverable_id = graph.variant_profile_ref;
    project
        .apply_change(&Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        })
        .unwrap();
    project.validate().unwrap();
    let plan = build_manim_semantic_graph_source_plan(
        &project,
        &GraphSourceSnapshot::from_project(&project),
        graph_id,
        deliverable_id,
    )
    .unwrap();
    let assets = assets();
    let package = graph_source_package_v1(&plan, &assets).unwrap();
    (project, package, assets)
}

fn edited(origin: &GraphPackage, assets: &BTreeMap<String, Vec<u8>>) -> GraphPackage {
    let mut plan = origin.plan.clone();
    let vertex_id = plan.source.content.vertices()[0].id;
    *vertex_label_mut(&mut plan.source, vertex_id).unwrap() = "Edited concept".into();
    graph_source_package_v1(&plan, assets).unwrap()
}

fn input(bytes: &[u8]) -> OriginalGraphBundleInput {
    OriginalGraphBundleInput {
        root: crate::manim_editable_projection::OriginalEditableBundleRoot::Output,
        relative_path: "source-input.mwgraph.zip".into(),
        sha256: hex::encode(Sha256::digest(bytes)),
        bytes: bytes.len() as u64,
    }
}

fn retained(root: &std::path::Path, name: &str, bytes: &[u8]) {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(root.join(name)).unwrap();
    file.write_all(bytes).unwrap();
    file.sync_all().unwrap();
}

#[test]
fn original_graph_carrier_reuses_only_the_existing_output_mount() {
    let (_, original, assets) = fixture();
    let bytes = graph_source_bundle_v1(&original.plan, &assets).unwrap();
    let descriptor = input(&bytes);
    validate_original_manim_graph_input(&descriptor).unwrap();
    let mut value = serde_json::to_value(&descriptor).unwrap();
    value["root"] = serde_json::json!("media");
    assert!(serde_json::from_value::<OriginalGraphBundleInput>(value).is_err());
}

#[test]
fn original_graph_carrier_rejects_escaped_names_wrong_formats_and_unbounded_bytes() {
    let (_, original, assets) = fixture();
    let bytes = graph_source_bundle_v1(&original.plan, &assets).unwrap();
    let base = input(&bytes);
    for name in [
        "",
        "../source.mwgraph.zip",
        "sub/source.mwgraph.zip",
        "sub\\source.mwgraph.zip",
        ".source.mwgraph.zip",
        "source.mwedit.zip",
        "source.MWGRAPH.ZIP",
        "/source.mwgraph.zip",
        "a\u{00e9}.mwgraph.zip",
    ] {
        let mut bad = base.clone();
        bad.relative_path = name.into();
        assert!(
            validate_original_manim_graph_input(&bad).is_err(),
            "{name:?}"
        );
    }
    for size in [
        0,
        motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2 as u64 + 1,
    ] {
        let mut bad = base.clone();
        bad.bytes = size;
        assert!(validate_original_manim_graph_input(&bad).is_err());
    }
    let mut bad = base.clone();
    bad.sha256 = "AB".repeat(32);
    assert!(validate_original_manim_graph_input(&bad).is_err());
}

#[test]
fn exact_original_zip_is_read_and_reconciled_with_its_own_source_closure() {
    let (_, original, assets) = fixture();
    let bytes = graph_source_bundle_v1(&original.plan, &assets).unwrap();
    let descriptor = input(&bytes);
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap();
    retained(&root, &descriptor.relative_path, &bytes);
    let inspected = graph_original_bundle(&root, &descriptor).unwrap();
    assert_eq!(inspected.plan, original.plan);
    assert_eq!(inspected.entries, original.entries);
    assert_eq!(
        std::fs::read(root.join(&descriptor.relative_path)).unwrap(),
        bytes
    );
}

#[test]
fn changed_original_zip_and_false_byte_length_are_rejected() {
    let (_, original, assets) = fixture();
    let bytes = graph_source_bundle_v1(&original.plan, &assets).unwrap();
    let mut descriptor = input(&bytes);
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap();
    retained(&root, &descriptor.relative_path, &bytes);
    descriptor.bytes += 1;
    assert!(graph_original_bundle(&root, &descriptor).is_err());
    descriptor.bytes -= 1;
    let mut changed = bytes.clone();
    changed[0] ^= 1;
    std::fs::write(root.join(&descriptor.relative_path), &changed).unwrap();
    assert!(graph_original_bundle(&root, &descriptor).is_err());
    assert_eq!(
        std::fs::read(root.join(&descriptor.relative_path)).unwrap(),
        changed
    );
}

#[test]
fn postrender_graph_input_matches_only_its_original_bundle_and_output_descriptor() {
    let (_, original, assets) = fixture();
    let bytes = graph_source_bundle_v1(&original.plan, &assets).unwrap();
    let descriptor = input(&bytes);
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap();
    retained(&root, &descriptor.relative_path, &bytes);
    let observation = serde_json::json!({
        "input": descriptor, "source_sha256": original.closure.source_sha256,
        "plan_sha256": original.closure.plan_sha256, "closure": original.closure,
    });
    let artifact = serde_json::json!({
        "graph_input_source": observation,
        "graph_source": {
            "sha256": descriptor.sha256, "bytes": descriptor.bytes, "closure": original.closure
        }
    });
    assert_eq!(
        verify_rendered_manim_semantic_graph_bundle_input(
            &root,
            &original.plan,
            &descriptor,
            &artifact,
        )
        .unwrap(),
        observation
    );
    for pointer in ["/graph_input_source/source_sha256", "/graph_source/sha256"] {
        let mut wrong = artifact.clone();
        *wrong.pointer_mut(pointer).unwrap() = serde_json::json!("ab".repeat(32));
        assert!(
            verify_rendered_manim_semantic_graph_bundle_input(
                &root,
                &original.plan,
                &descriptor,
                &wrong,
            )
            .is_err()
        );
        assert_eq!(
            std::fs::read(root.join(&descriptor.relative_path)).unwrap(),
            bytes
        );
    }
}

#[test]
fn preview_produces_one_canonical_label_change_and_the_real_current_authorship_stamp() {
    let (mut project, original, assets) = fixture();
    let candidate = edited(&original, &assets);
    let vertex_id = original.plan.source.content.vertices()[0].id;
    // This is a pure clock-input regression: no Store or admission is forged.
    // The source keeps its historical snapshot while current execution moves on.
    project.revision += 2;
    project.validate().unwrap();
    let expected = GraphSourceSnapshot::from_project(&project);
    let before = project.clone();
    let change =
        preview_graph_label_change(&project, &expected, &original, &candidate, vertex_id).unwrap();
    let Change::UpsertSemanticGraph { graph } = &change else {
        panic!("wrong canonical Change")
    };
    assert_eq!(graph.authorship.source_snapshot, expected);
    assert_eq!(graph.content.vertices()[0].label, "Edited concept");
    let mut restored = graph.as_ref().clone();
    *vertex_label_mut(&mut restored, vertex_id).unwrap() =
        original.plan.source.content.vertices()[0].label.clone();
    restored.authorship = original.plan.source.authorship.clone();
    assert_eq!(restored, original.plan.source);
    assert_eq!(project, before);
    assert_ne!(original.plan.execution_snapshot, expected);
}

#[test]
fn preview_rejects_a_second_label_change_in_a_closed_valid_bundle() {
    let (project, original, assets) = fixture();
    let mut plan = edited(&original, &assets).plan;
    let other = plan.source.content.vertices()[1].id;
    *vertex_label_mut(&mut plan.source, other).unwrap() = "Second edit".into();
    let candidate = graph_source_package_v1(&plan, &assets).unwrap();
    assert!(
        preview_graph_label_change(
            &project,
            &GraphSourceSnapshot::from_project(&project),
            &original,
            &candidate,
            original.plan.source.content.vertices()[0].id,
        )
        .is_err()
    );
}

#[test]
fn preview_rejects_style_or_historical_authorship_changes() {
    let (project, original, assets) = fixture();
    for mutate_authorship in [false, true] {
        let mut plan = edited(&original, &assets).plan;
        if mutate_authorship {
            plan.source.authorship.author_label = "Different claimed author".into();
        } else {
            plan.source.style.accent_color = "#112233".into();
        }
        let candidate = graph_source_package_v1(&plan, &assets).unwrap();
        assert!(
            preview_graph_label_change(
                &project,
                &GraphSourceSnapshot::from_project(&project),
                &original,
                &candidate,
                original.plan.source.content.vertices()[0].id,
            )
            .is_err()
        );
    }
}

#[test]
fn preview_rejects_changed_timing_even_when_both_bundles_are_closed() {
    let (project, original, assets) = fixture();
    let mut plan = edited(&original, &assets).plan;
    let GraphContent::SystemFlow { events, .. } = &mut plan.source.content else {
        panic!("fixture is a system flow")
    };
    events[0].duration = motionwright_domain::RationalTime::new(2, 30).unwrap();
    let candidate = graph_source_package_v1(&plan, &assets).unwrap();
    assert!(
        preview_graph_label_change(
            &project,
            &GraphSourceSnapshot::from_project(&project),
            &original,
            &candidate,
            original.plan.source.content.vertices()[0].id,
        )
        .is_err()
    );
}

#[test]
fn preview_rejects_a_noop_and_an_absent_target() {
    let (project, original, assets) = fixture();
    let candidate = edited(&original, &assets);
    let expected = GraphSourceSnapshot::from_project(&project);
    assert!(
        preview_graph_label_change(
            &project,
            &expected,
            &original,
            &original,
            original.plan.source.content.vertices()[0].id,
        )
        .is_err()
    );
    assert!(
        preview_graph_label_change(&project, &expected, &original, &candidate, Uuid::new_v4(),)
            .is_err()
    );
}

#[test]
fn preview_preserves_all_four_snapshot_fences() {
    let (project, original, assets) = fixture();
    let candidate = edited(&original, &assets);
    for field in 0..4 {
        let mut expected = GraphSourceSnapshot::from_project(&project);
        match field {
            0 => expected.project_id = Uuid::new_v4(),
            1 => expected.generation = Uuid::new_v4(),
            2 => expected.branch_id = Uuid::new_v4(),
            _ => expected.revision += 1,
        }
        let error = preview_graph_label_change(
            &project,
            &expected,
            &original,
            &candidate,
            original.plan.source.content.vertices()[0].id,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
    }
}

#[test]
fn preview_respects_the_existing_canonical_graph_vertex_content_lock() {
    let (mut project, original, assets) = fixture();
    let candidate = edited(&original, &assets);
    let vertex_id = original.plan.source.content.vertices()[0].id;
    project.locks.push(ProjectLock {
        id: Uuid::new_v4(),
        resource: format!("graph-vertex:{}:{vertex_id}", original.plan.source.id),
        kind: LockKind::Content,
        note: "A real canonical lock in a pure source fixture".into(),
    });
    project.validate().unwrap();
    let before = project.clone();
    assert!(
        preview_graph_label_change(
            &project,
            &GraphSourceSnapshot::from_project(&project),
            &original,
            &candidate,
            vertex_id,
        )
        .is_err()
    );
    assert_eq!(project, before);
}
