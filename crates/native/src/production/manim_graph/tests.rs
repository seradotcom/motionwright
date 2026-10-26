use super::*;
use motionwright_domain::{
    Change, GraphCausalBasis, GraphContent, OutputColorSpace, OutputContainer, VideoCodec,
};
use motionwright_storage::Store;

use crate::semantic_graph_source_fixture as authored;

pub(in crate::production) fn prepared() -> (Project, ManimSemanticGraphPlan) {
    let mut project = authored::seed_domain();
    project.deliverables.truncate(1);
    project.deliverables[0].container = OutputContainer::Mp4;
    project.deliverables[0].video_codec = VideoCodec::H264;
    project.deliverables[0].color_space = OutputColorSpace::Rec709;
    project.deliverables[0].burn_in_captions = false;
    let mut graph = authored::graph_for(&project, false);
    graph.time_range.start = motionwright_domain::RationalTime { num: 0, den: 1 };
    graph.time_range.duration = project.scenes[0].duration;
    graph.style.font_size = 10.0;
    let GraphContent::CausalDiagram { vertices, edges } = &mut graph.content else {
        unreachable!()
    };
    vertices[0].label = "One".into();
    vertices[1].label = "Two".into();
    edges[0].label = "Link".into();
    edges[0].basis = GraphCausalBasis::AuthorHypothesis {
        disclosure: "Authored example.".into(),
    };
    let graph_id = graph.id;
    let deliverable_id = graph.variant_profile_ref;
    project
        .apply_change(&Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        })
        .unwrap();
    project.validate().unwrap();
    let plan = crate::manim_graph_projection::build_manim_semantic_graph_source_plan(
        &project,
        &GraphSourceSnapshot::from_project(&project),
        graph_id,
        deliverable_id,
    )
    .unwrap();
    (project, plan)
}

pub(in crate::production) fn persisted() -> (
    StudioService,
    Project,
    ManimSemanticGraphPlan,
    tempfile::TempDir,
) {
    let (project, plan) = prepared();
    let temp = tempfile::tempdir().unwrap();
    let mut store = Store::open(temp.path().join("motionwright.sqlite3")).unwrap();
    store.create_project(&project).unwrap();
    let service = StudioService::from_store(store);
    assert_eq!(service.project(project.id).unwrap(), project);
    (service, project, plan, temp)
}

fn start_args(plan: &ManimSemanticGraphPlan) -> Value {
    json!({"plan": plan, "profile": plan.render_profile})
}

#[test]
fn current_saved_graph_preflight_is_pure_and_preserves_historical_authorship() {
    let (project, plan) = prepared();
    let before = project.clone();
    validate_dispatch_source(&project, START_COMMAND, &start_args(&plan)).unwrap();
    let compiled = compile_graph_program_source_only(&plan).unwrap();
    assert_eq!(compiled.plan_document, source_document(&plan).unwrap());
    assert!(!compiled.native_execution_performed);
    assert!(!compiled.runtime_sample_pts_verified);
    assert_eq!(project, before);
    assert_eq!(
        plan.source.authorship,
        project.production_design.semantic_graphs[0].authorship
    );
}

#[test]
fn each_current_snapshot_field_is_checked_before_graph_source_use() {
    let (project, plan) = prepared();
    let before = project.clone();
    for dimension in 0..4 {
        let mut changed = plan.clone();
        match dimension {
            0 => changed.execution_snapshot.project_id = Uuid::new_v4(),
            1 => changed.execution_snapshot.generation = Uuid::new_v4(),
            2 => changed.execution_snapshot.branch_id = Uuid::new_v4(),
            _ => changed.execution_snapshot.revision += 1,
        }
        let error =
            validate_dispatch_source(&project, START_COMMAND, &start_args(&changed)).unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert_eq!(project, before);
    }
}

#[test]
fn current_stamp_does_not_authorize_changed_unpersisted_graph_text() {
    let (project, mut plan) = prepared();
    let before = project.clone();
    let GraphContent::CausalDiagram { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[0].label = "Changed".into();
    let error = validate_dispatch_source(&project, START_COMMAND, &start_args(&plan)).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert_eq!(project, before);
}

#[test]
fn selected_graph_blocks_other_profiles_only_for_its_scene_and_deliverable() {
    let (mut project, plan) = prepared();
    let mut independent = project.deliverables[0].clone();
    independent.id = Uuid::new_v4();
    independent.profile_name = format!("{} independent", independent.profile_name);
    let other_id = independent.id;
    project.deliverables.push(independent);
    project.validate().unwrap();
    let scene_id = plan.source.scene_id;
    assert_eq!(
        require_non_graph_selection(&project, scene_id, Some(plan.source.variant_profile_ref))
            .unwrap_err()
            .code,
        ErrorCode::Unsupported,
    );
    require_non_graph_selection(&project, scene_id, Some(other_id)).unwrap();
    assert_eq!(
        require_non_graph_selection(&project, scene_id, None)
            .unwrap_err()
            .code,
        ErrorCode::Unsupported,
    );
}

#[test]
fn all_other_native_start_routes_refuse_silently_omitting_the_selected_graph() {
    let (project, plan) = prepared();
    let binding = json!({"scene_id": plan.source.scene_id, "deliverable_id": plan.source.variant_profile_ref});
    for command in [
        "driver.manim-community.static.render.start",
        "driver.manim-community.editable.render.start",
        "driver.manim-community.camera.render.start",
    ] {
        let error =
            validate_dispatch_source(&project, command, &json!({"plan": {"binding": binding}}))
                .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }
    assert_eq!(
        validate_dispatch_source(
            &project,
            "driver.manim-community.render.start",
            &json!({"plan": {"scene_id": plan.source.scene_id}}),
        )
        .unwrap_err()
        .code,
        ErrorCode::Unsupported,
    );
    validate_dispatch_source(
        &project,
        "driver.manim-community.render.status",
        &json!({"job_ref": "retained"}),
    )
    .unwrap();
}

#[test]
fn graph_start_rejects_profile_override_and_unknown_top_level_keys() {
    let (project, plan) = prepared();
    let mut wrong = start_args(&plan);
    wrong["profile"]["width"] = json!(plan.render_profile.width + 2);
    assert_eq!(
        validate_dispatch_source(&project, START_COMMAND, &wrong)
            .unwrap_err()
            .code,
        ErrorCode::InvalidArgument
    );
    let mut extra = start_args(&plan);
    extra["runtime_root"] = json!("caller-owned");
    assert_eq!(
        validate_dispatch_source(&project, START_COMMAND, &extra)
            .unwrap_err()
            .code,
        ErrorCode::InvalidArgument
    );
    let mut oversized = start_args(&plan);
    oversized["program"] = json!("x".repeat(MAX_ARGS_BYTES));
    assert_eq!(
        validate_dispatch_source(&project, START_COMMAND, &oversized)
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
}

#[test]
fn native_graph_paths_bind_one_lowercase_job_and_exact_profile() {
    let profile = ManimRenderProfile {
        width: 640,
        height: 360,
        frame_rate: 10,
    };
    let token = "1234567890abcdef1234567890abcdef";
    let valid = format!("videos/mw_{token}/360p10/mw_{token}.mp4");
    assert_eq!(
        job_paths(&valid, profile).unwrap(),
        (
            format!("mw_{token}.py"),
            format!("mw_{token}.mwgraph.zip"),
            format!("mw_{token}.graph-readback")
        ),
    );
    for value in [
        format!("../{valid}"),
        valid.replace("360p10", "360p30"),
        valid.replace("abcdef", "ABCDEF"),
        format!("videos/mw_{}0/360p10/mw_{token}.mp4", &token[..31]),
        format!("mw_{token}.mp4"),
    ] {
        assert!(job_paths(&value, profile).is_err(), "{value}");
    }
}

fn roles() -> (BTreeSet<String>, BTreeSet<String>) {
    let required: BTreeSet<String> = ["header.json", "frames.jsonl", "labels/text_block_0000.json"]
        .map(str::to_owned)
        .into();
    let mut allowed = required.clone();
    allowed.insert("labels/source_0000_0000.svg".into());
    (required, allowed)
}

fn synthetic_index() -> Value {
    let (required, _) = roles();
    json!({
        "schema": INDEX_SCHEMA, "profile_id": PROFILE_ID,
        "source_file": "mw_1234567890abcdef1234567890abcdef.py",
        "plan_sha256": "ab".repeat(32), "generated_source_sha256": "cd".repeat(32),
        "original_directory": "mw_1234567890abcdef1234567890abcdef.graph-readback",
        "entries": required.into_iter().map(|path| json!({
            "path": path, "bytes": 2, "sha256": hex::encode(Sha256::digest(b"{}"))
        })).collect::<Vec<_>>(),
        "inspector_summary": {"synthetic_parser_fixture_only": true},
        "native_video_verified": false, "native_reopen_verified": false, "profile_admitted": false
    })
}

fn parse_synthetic(raw: &[u8]) -> NativeResult<OriginalIndex> {
    let (required, allowed) = roles();
    decode_index(
        raw,
        "mw_1234567890abcdef1234567890abcdef.py",
        "mw_1234567890abcdef1234567890abcdef.graph-readback",
        &"ab".repeat(32),
        &"cd".repeat(32),
        &required,
        &allowed,
    )
}

fn parse_value(value: &Value) -> NativeResult<OriginalIndex> {
    parse_synthetic(&serde_json::to_vec(value).unwrap())
}

#[test]
fn typed_index_parser_fixture_confers_no_native_evidence() {
    let parsed = parse_value(&synthetic_index()).unwrap();
    assert_eq!(parsed.entries.len(), 3);
    assert!(!parsed.native_video_verified);
    assert!(!parsed.native_reopen_verified);
    assert!(!parsed.profile_admitted);
    assert_eq!(
        parsed.inspector_summary,
        json!({"synthetic_parser_fixture_only": true})
    );
}

#[test]
fn index_binds_all_original_identifiers_and_rejects_claimed_authority() {
    for key in [
        "schema",
        "profile_id",
        "source_file",
        "original_directory",
        "plan_sha256",
        "generated_source_sha256",
    ] {
        let mut value = synthetic_index();
        value[key] = json!("different");
        assert!(parse_value(&value).is_err(), "{key}");
    }
    for key in [
        "native_video_verified",
        "native_reopen_verified",
        "profile_admitted",
    ] {
        for alias in [
            json!(true),
            json!(0),
            json!(0.0),
            json!("false"),
            Value::Null,
        ] {
            let mut value = synthetic_index();
            value[key] = alias;
            assert!(parse_value(&value).is_err(), "{key}");
        }
    }
}

#[test]
fn index_rejects_omitted_duplicate_traversal_and_undeclared_original_roles() {
    let mut missing = synthetic_index();
    missing["entries"].as_array_mut().unwrap().remove(0);
    assert!(parse_value(&missing).is_err());
    let mut duplicate = synthetic_index();
    let first = duplicate["entries"][0].clone();
    duplicate["entries"].as_array_mut().unwrap().push(first);
    assert!(parse_value(&duplicate).is_err());
    for path in [
        "../header.json",
        "labels/../header.json",
        "labels/unknown.svg",
        "/header.json",
        "labels\\text_block_0000.json",
    ] {
        let mut value = synthetic_index();
        value["entries"][0]["path"] = json!(path);
        assert!(parse_value(&value).is_err(), "{path}");
    }
}

#[test]
fn index_keeps_integer_types_and_both_byte_limits_strict() {
    for alias in [json!(2.0), json!(true), json!("2"), json!(-1), json!(0)] {
        let mut value = synthetic_index();
        value["entries"][0]["bytes"] = alias;
        assert!(parse_value(&value).is_err());
    }
    let mut value = synthetic_index();
    value["entries"][0]["bytes"] = json!(MAX_READBACK_BYTES);
    assert!(parse_value(&value).is_err());
    let oversized = vec![b' '; MAX_INDEX_BYTES as usize + 1];
    assert!(parse_synthetic(&oversized).is_err());
}

#[test]
fn index_rejects_unknown_duplicate_and_missing_schema_fields() {
    let mut top = synthetic_index();
    top["ready"] = json!(true);
    assert!(parse_value(&top).is_err());
    let mut entry = synthetic_index();
    entry["entries"][0]["decoded"] = json!(true);
    assert!(parse_value(&entry).is_err());
    let mut missing = synthetic_index();
    missing.as_object_mut().unwrap().remove("inspector_summary");
    assert!(parse_value(&missing).is_err());
    let raw = serde_json::to_string(&synthetic_index()).unwrap();
    let duplicate = format!("{{\"profile_admitted\":false,{}", &raw[1..]);
    assert!(parse_synthetic(duplicate.as_bytes()).is_err());
}

#[test]
fn digest_checked_original_read_rejects_content_changes_and_size_aliases() {
    let temp = tempfile::tempdir().unwrap();
    let raw = b"retained original";
    fs::write(temp.path().join("original.bin"), raw).unwrap();
    let digest = hex::encode(Sha256::digest(raw));
    assert_eq!(
        checked_bytes(temp.path(), "original.bin", &digest, raw.len() as u64, 128).unwrap(),
        raw
    );
    assert!(
        checked_bytes(
            temp.path(),
            "original.bin",
            &digest,
            raw.len() as u64 + 1,
            128
        )
        .is_err()
    );
    fs::write(temp.path().join("original.bin"), b"different original").unwrap();
    assert!(checked_bytes(temp.path(), "original.bin", &digest, raw.len() as u64, 128).is_err());
    assert_eq!(
        fs::read(temp.path().join("original.bin")).unwrap(),
        b"different original"
    );
}

fn retained_roster() -> tempfile::TempDir {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("originals");
    fs::create_dir(&root).unwrap();
    fs::create_dir(root.join("labels")).unwrap();
    for name in [
        "header.json",
        "frames.jsonl",
        "index.json",
        "labels/text_block_0000.json",
    ] {
        fs::write(root.join(name), b"{}").unwrap();
    }
    temp
}

#[test]
fn actual_retained_roster_rejects_unindexed_or_missing_originals() {
    let temp = retained_roster();
    assert_eq!(observed_paths(temp.path(), "originals").unwrap(), roles().0);
    let unexpected = temp.path().join("originals/unindexed.json");
    fs::write(&unexpected, b"{}").unwrap();
    assert!(observed_paths(temp.path(), "originals").is_err());
    fs::remove_file(unexpected).unwrap();
    fs::remove_file(temp.path().join("originals/frames.jsonl")).unwrap();
    assert!(observed_paths(temp.path(), "originals").is_err());
}

#[cfg(unix)]
#[test]
fn actual_retained_roster_rejects_linked_or_nested_label_members() {
    use std::os::unix::fs::symlink;
    let temp = retained_roster();
    let label = temp.path().join("originals/labels/text_block_0000.json");
    fs::remove_file(&label).unwrap();
    symlink("../header.json", &label).unwrap();
    assert!(observed_paths(temp.path(), "originals").is_err());
    fs::remove_file(&label).unwrap();
    fs::create_dir(&label).unwrap();
    assert!(observed_paths(temp.path(), "originals").is_err());
}

#[test]
fn missing_graph_originals_cannot_be_replaced_by_success_metadata() {
    let (service, project, plan, temp) = persisted();
    let video = format!(
        "videos/mw_{0}/{1}/mw_{0}.mp4",
        "1234567890abcdef1234567890abcdef",
        plan.render_profile.quality_directory()
    );
    let artifact = json!({"graph_source_verified": true, "graph_getters_verified": true, "profile_admitted": true});
    let error =
        verify_rendered_source(&service, temp.path(), &video, &plan, &artifact).unwrap_err();
    assert_eq!(error.code, ErrorCode::InvalidArgument);
    assert_eq!(service.project(project.id).unwrap(), project);
    assert!(
        service
            .production_receipts(project.id, 64)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn matching_zip_metadata_does_not_make_noncompiler_bytes_editable_graph_source() {
    let (service, project, plan, temp) = persisted();
    let token = "1234567890abcdef1234567890abcdef";
    let path = format!("mw_{token}.mwgraph.zip");
    let raw = b"synthetic invalid ZIP; no runtime execution";
    fs::write(temp.path().join(&path), raw).unwrap();
    let artifact = json!({"graph_source": {
        "relative_path": path, "sha256": hex::encode(Sha256::digest(raw)),
        "bytes": raw.len(), "media_type": GRAPH_SOURCE_MIME_V1, "closure": {}
    }});
    let video = format!(
        "videos/mw_{token}/{}/mw_{token}.mp4",
        plan.render_profile.quality_directory()
    );
    assert_eq!(
        verify_rendered_source(&service, temp.path(), &video, &plan, &artifact)
            .unwrap_err()
            .code,
        ErrorCode::InvalidArgument,
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(fs::read(temp.path().join(path)).unwrap(), raw);
    assert!(
        service
            .production_receipts(project.id, 64)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn stale_graph_branch_is_rejected_before_touching_any_output_path() {
    let (service, project, mut plan, temp) = persisted();
    plan.execution_snapshot.branch_id = Uuid::new_v4();
    let error = verify_rendered_source(
        &service,
        &temp.path().join("never-created"),
        "invalid-video",
        &plan,
        &Value::Null,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::StaleReference);
    assert!(!temp.path().join("never-created").exists());
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn graph_original_input_carrier_is_closed_before_dispatch() {
    let (project, plan) = prepared();
    let before = project.clone();
    let mut args = start_args(&plan);
    args["source_bundle"] = json!({
        "root": "output", "relative_path": "source.mwgraph.zip",
        "sha256": "a".repeat(64), "bytes": 1,
    });
    // This validates only the descriptor. The original ZIP is read by the
    // current-snapshot caller and again by the Driver before starting a job.
    validate_dispatch_source(&project, START_COMMAND, &args).unwrap();
    for value in [
        Value::Null,
        json!({"root": "work", "relative_path": "source.mwgraph.zip", "sha256": "a".repeat(64), "bytes": 1}),
        json!({"root": "output", "relative_path": "../source.mwgraph.zip", "sha256": "a".repeat(64), "bytes": 1}),
        json!({"root": "output", "relative_path": "source.mwedit.zip", "sha256": "a".repeat(64), "bytes": 1}),
        json!({"root": "output", "relative_path": "source.mwgraph.zip", "sha256": "A".repeat(64), "bytes": 1}),
        json!({"root": "output", "relative_path": "source.mwgraph.zip", "sha256": "a".repeat(64), "bytes": 0}),
        json!({"root": "output", "relative_path": "source.mwgraph.zip", "sha256": "a".repeat(64), "bytes": 1, "grant": true}),
    ] {
        args["source_bundle"] = value;
        assert!(validate_dispatch_source(&project, START_COMMAND, &args).is_err());
    }
    assert_eq!(project, before);
}

#[test]
fn graph_original_input_does_not_relax_the_current_plan_snapshot() {
    let (project, plan) = prepared();
    let before = project.clone();
    let mut args = start_args(&plan);
    args["source_bundle"] = json!({
        "root": "output", "relative_path": "source.mwgraph.zip",
        "sha256": "a".repeat(64), "bytes": 1,
    });
    args["plan"]["execution_snapshot"]["revision"] = json!(project.revision + 1);
    assert!(validate_dispatch_source(&project, START_COMMAND, &args).is_err());
    assert_eq!(project, before);
}
