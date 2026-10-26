//! Controlled synthetic observation records exercise the pure verifier.
//! They are deliberately not Pango/Manim output, native admission or G1 evidence.
use super::*;
use motionwright_domain::GraphContent;
use serde_json::{Value, json};

fn fixture_plan() -> ManimSemanticGraphPlan {
    let plan: ManimSemanticGraphPlan =
        serde_json::from_str(include_str!("../testdata/semantic_graph_plan_v1.json")).unwrap();
    plan.validate().unwrap();
    plan
}

fn encoded(value: &Value) -> Vec<u8> {
    let mut raw = serde_json::to_vec(value).unwrap();
    raw.push(b'\n');
    raw
}

fn points_bytes(points: &[[f64; 3]]) -> Vec<u8> {
    points
        .iter()
        .flatten()
        .flat_map(|value| value.to_le_bytes())
        .collect()
}

fn rectangle_points(low: [f64; 3], high: [f64; 3]) -> [[f64; 3]; 4] {
    [low, [high[0], low[1], 0.0], high, [low[0], high[1], 0.0]]
}

fn controlled_text(
    plan: &ManimSemanticGraphPlan,
    block: &GraphTextBlock,
    files: &mut BTreeMap<String, Vec<u8>>,
) -> (Value, Option<Value>) {
    let literal = block.text.split('\n').collect::<Vec<_>>();
    let mut lines = Vec::new();
    let mut original_rows = Vec::new();
    let mut composite_paths = String::new();
    let mut imported_points = Vec::new();
    let box_ = &block.authored_box;
    let typography = plan.typography_requirement();
    let family = typography.native_family;
    let pin = graph_builtin_asset_pins()
        .iter()
        .find(|pin| {
            pin.relative_path == "instrument-sans/files/instrument-sans-latin-wght-normal.woff2"
        })
        .unwrap();
    let width = plan.render_profile.width;
    let height = plan.render_profile.height;
    for (index, text) in literal.iter().enumerate() {
        let advance = text.chars().count() as f64 * 6.0;
        let x = box_.x + (box_.width - advance) / 2.0;
        let baseline = box_.y
            + box_.height / 2.0
            + (index as f64 - (literal.len() - 1) as f64 / 2.0) * 12.0
            + 3.5;
        let origin = [x, baseline - 8.0];
        let has_ink = !text.trim_matches(' ').is_empty();
        let logical = [0, 0, (advance * 1024.0) as i32, 10 * 1024];
        let ink = if has_ink {
            [0, 0, logical[2], 6 * 1024]
        } else {
            [0; 4]
        };
        let ink_bounds = [x, origin[1], x + advance, origin[1] + 6.0];
        let runs = if text.is_empty() {
            Vec::new()
        } else {
            vec![json!({
                "offset":0,"length":text.len(),"num_chars":text.chars().count(),
                "family":family,"weight":400,"style":0,"face_index":0,
                "font_relative_path":pin.relative_path,"font_sha256":pin.sha256,
            })]
        };
        let mut vector = Value::Null;
        if has_ink {
            let path = format!("M {} {} h {} v 6 h -{} Z", x, origin[1], advance, advance);
            let svg = format!(
                "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}px\" height=\"{height}px\" viewBox=\"0 0 {width} {height}\"><path d=\"{path}\"/></svg>"
            ).into_bytes();
            let sha = crate::source_digest(&svg);
            let name = format!(
                "labels/source_{:04}_{:04}.svg",
                block.ordinal,
                original_rows.len()
            );
            original_rows.push(json!({"sha256":sha,"size_bytes":svg.len()}));
            files.insert(name, svg.clone());
            composite_paths.push_str(&format!("<path d=\"{path}\"/>"));
            let low = [
                x - f64::from(width) / 2.0,
                f64::from(height) / 2.0 - ink_bounds[3],
                0.0,
            ];
            let high = [
                ink_bounds[2] - f64::from(width) / 2.0,
                f64::from(height) / 2.0 - origin[1],
                0.0,
            ];
            imported_points.extend(rectangle_points(low, high));
            vector = json!({
                "schema":"motionwright.manim.graph-text-vector-observation/1",
                "status":"OBSERVED_PANGO_CAIRO_OUTLINES_ONLY",
                "text_sha256":crate::source_digest(text.as_bytes()),"text_bytes":text.len(),
                "runs":runs,"unknown_glyph_count":0,"fallback_allowed":false,
                "logical_bounds_px":[x,origin[1],x+advance,origin[1]+10.0],
                "outline_bounds_px":ink_bounds,"baseline_px":baseline,"layout_origin_px":origin,
                "units_per_pixel":1024,"svg_sha256":sha,"svg_bytes":svg.len(),"svg_outline_paths":1,
                "cairo_version":"1.18.0","pango_version":"1.50.14","fontconfig_version":21301,
                "manim_import_verified":false,"manim_frame_verified":false,
                "profile_admitted":false,"human_acceptance":false,
            });
        }
        lines.push(json!({
            "index":index,"text":text,"text_sha256":crate::source_digest(text.as_bytes()),
            "text_bytes":text.len(),"placement":{
                "line_start_x_px":x,"advance_px":advance,"baseline_px":baseline,
                "layout_origin_px":origin,"line_height_px":12.0,
            },
            "native_ink_bounds_px":if has_ink {json!(ink_bounds)} else {Value::Null},
            "has_ink":has_ink,"runs":runs,"vector":vector,
            "native_layout":{
                "unknown_glyph_count":0,"ink_pango_units":ink,"logical_pango_units":logical,
                "baseline_pango_units":8192,"line_count":1,"is_wrapped":false,"is_ellipsized":false,
            },
            "pango_version":"1.50.14","fontconfig_version":21301,
        }));
    }
    let mut imported = None;
    let mut svg_sha = Value::Null;
    let mut svg_bytes = 0usize;
    if !original_rows.is_empty() {
        let composite = format!(
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}px\" height=\"{height}px\" viewBox=\"0 0 {width} {height}\">{composite_paths}</svg>"
        ).into_bytes();
        let digest = crate::source_digest(&composite);
        svg_sha = json!(digest);
        svg_bytes = composite.len();
        let mut low = [f64::INFINITY; 3];
        let mut high = [f64::NEG_INFINITY; 3];
        for point in &imported_points {
            for axis in 0..3 {
                low[axis] = low[axis].min(point[axis]);
                high[axis] = high[axis].max(point[axis]);
            }
        }
        imported = Some(json!({
            "schema":"motionwright.manim.graph-svg-import-observation/1","manim_version":"0.21.0",
            "svg_sha256":digest,"svg_bytes":svg_bytes,
            "private_svg_name":format!("label_{:04}.svg",block.ordinal),
            "origin_reflection":[1,-1,1],
            "viewport_translation":[-f64::from(width)/2.0,f64::from(height)/2.0,0.0],
            "scale":1,"automatic_centering":false,"automatic_fit":false,
            "point_count":imported_points.len(),"point_encoding":"xyz-f64le",
            "points_sha256":crate::source_digest(&points_bytes(&imported_points)),
            "control_point_min":low,"control_point_max":high,
            "control_bounds_are_glyph_ink_bounds":false,"manim_frame_verified":false,
            "native_reopen_verified":false,"profile_admitted":false,"human_acceptance":false,
        }));
        files.insert(format!("labels/label_{:04}.svg", block.ordinal), composite);
    }
    let layout = json!({
        "schema":"motionwright.manim.graph-text-layout-observation/1",
        "status":"OBSERVED_NATIVE_TEXT_GEOMETRY_ONLY",
        "contract_sha256":GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,"resource":block.resource,
        "text_sha256":crate::source_digest(block.text.as_bytes()),"text_bytes":block.text.len(),
        "literal_line_count":literal.len(),"font_size_px":10.0,
        "editorial_family":typography.editorial_family,"native_family":family,"weight":400,
        "authored_box":box_,"lines":lines,"svg_sha256":svg_sha,"svg_bytes":svg_bytes,
        "original_line_svgs":original_rows,"automatic_wrap":false,"automatic_scale":false,
        "clipping":false,"fallback_allowed":false,"manim_frame_verified":false,
        "native_reopen_verified":false,"profile_admitted":false,"human_acceptance":false,
    });
    let block_json = json!({
        "ordinal":block.ordinal,"owner":block.owner,"layout":layout,"manim_import":imported,
    });
    let raw = encoded(&block_json);
    let descriptor = json!({
        "ordinal":block.ordinal,"owner":block.owner,
        "file_name":format!("text_block_{:04}.json",block.ordinal),
        "size_bytes":raw.len(),"sha256":crate::source_digest(&raw),
    });
    files.insert(format!("labels/text_block_{:04}.json", block.ordinal), raw);
    (descriptor, imported)
}

fn controlled_observation(plan: &ManimSemanticGraphPlan) -> BTreeMap<String, Vec<u8>> {
    assert_eq!(plan.scene_clock.frame_count, 100);
    assert_eq!(plan.scene_clock.project_start_frame, 200);
    assert_eq!(plan.render_profile.frame_rate, 10);
    let prepared = compile_graph_program_source_only(plan).unwrap();
    let blocks = text_blocks(plan).unwrap();
    assert_eq!(blocks.len(), 6);
    let mut files = BTreeMap::new();
    let mut references = Vec::new();
    let mut imports = Vec::new();
    for block in &blocks {
        let (reference, imported) = controlled_text(plan, block, &mut files);
        references.push(reference);
        imports.push(imported);
    }
    let objects = expected_objects(plan, &blocks).unwrap();
    assert_eq!(objects.len(), 11);
    let mut geometry = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        let (point_count, point_sha, low, high) = if let Some(ordinal) = object.label {
            let value = imports[ordinal].as_ref().unwrap();
            (
                value["point_count"].clone(),
                value["points_sha256"].clone(),
                value["control_point_min"].clone(),
                value["control_point_max"].clone(),
            )
        } else {
            let (low, high) = object.bounds.unwrap();
            (
                json!(4),
                json!(crate::source_digest(&points_bytes(&rectangle_points(
                    low, high
                )))),
                json!(low),
                json!(high),
            )
        };
        geometry.push(json!({
            "name":object.name,"native_class":object.native_class,
            "point_count":point_count,"point_encoding":"xyz-f64le","points_sha256":point_sha,
            "control_point_min":low,"control_point_max":high,
            "control_bounds_are_glyph_ink_bounds":false,"z_index":index,
            "members_with_points":1,"paint_mode":object.mode,"paint_color":object.color,
            "stroke_width":object.width,
            "stroke_join":if object.mode=="stroke" {json!("MITER")} else {Value::Null},
            "stroke_cap":if object.mode=="stroke" {json!("BUTT")} else {Value::Null},
        }));
    }
    let header = json!({
        "schema":"motionwright.manim.graph-native-observation/1","profile_id":SEMANTIC_GRAPH_PROFILE,
        "plan_sha256":prepared.plan_sha256,"generated_source_sha256":prepared.source_sha256,
        "execution_snapshot":plan.execution_snapshot,"graph_id":plan.source.id,"scene_id":plan.source.scene_id,
        "camera":camera(plan),"helper_modules":graph_helper_pins(),"font_files":graph_builtin_asset_pins(),
        "text_blocks":references,"objects":geometry,"derived_byte_limit":MAX_GRAPH_READBACK_BYTES,
        "one_svg_parser_temporary_byte_limit":MAX_SVG_BYTES,"point_limit":MAX_POINTS,
        "decoded_video_verified":false,"native_reopen_verified":false,"profile_admitted":false,
        "human_acceptance":false,
    });
    files.insert("header.json".into(), encoded(&header));
    let mut frames = Vec::new();
    for index in 0..100u32 {
        let local = if (20..80).contains(&index) {
            Some(index - 20)
        } else {
            None
        };
        // Independent hand-written clock oracle for the canonical fixture.
        let vertex = local.map_or(0.0, |n| (f64::from(n) / 10.0).min(1.0));
        let edge = local.map_or(0.0, |n| {
            if n < 10 {
                0.0
            } else {
                (f64::from(n - 10) / 10.0).min(1.0)
            }
        });
        let active = |first, end| {
            if local.is_some_and(|n| first <= n && n < end) {
                1.0
            } else {
                0.0
            }
        };
        frames.extend(encoded(&json!({
            "record_type":"frame","scene_frame":index,"project_frame":200+u64::from(index),
            "graph_local_frame":local,"callback_alpha":f64::from(index)/99.5,"camera":camera(plan),
            "background_hex":if local.is_some(){&plan.source.style.background}else{&plan.scene_matte.color},
            "background_opacity":1.0,
            "paint_opacity":[edge,edge,edge*active(0,20),edge*active(20,30),edge*active(30,60),
                vertex,vertex,vertex,vertex,active(0,60),active(20,30)],
        })));
    }
    frames.extend(encoded(&json!({
        "record_type":"observed_terminal","sample_count":100,"static_geometry_reobserved_unchanged":true,
        "plan_sha256":prepared.plan_sha256,"terminal_editing_scene_frame":99,
        "decoded_video_verified":false,"native_reopen_verified":false,
    })));
    files.insert("frames.jsonl".into(), frames);
    files
}

fn edit_json(files: &mut BTreeMap<String, Vec<u8>>, name: &str, edit: impl FnOnce(&mut Value)) {
    let mut value: Value = serde_json::from_slice(&files[name]).unwrap();
    edit(&mut value);
    files.insert(name.into(), encoded(&value));
}

fn edit_block(
    files: &mut BTreeMap<String, Vec<u8>>,
    ordinal: usize,
    edit: impl FnOnce(&mut Value),
) {
    let name = format!("labels/text_block_{ordinal:04}.json");
    edit_json(files, &name, edit);
    let sha = crate::source_digest(&files[&name]);
    let count = files[&name].len();
    edit_json(files, "header.json", |header| {
        header["text_blocks"][ordinal]["sha256"] = json!(sha);
        header["text_blocks"][ordinal]["size_bytes"] = json!(count);
    });
}

fn edit_frame(files: &mut BTreeMap<String, Vec<u8>>, index: usize, edit: impl FnOnce(&mut Value)) {
    let mut rows = files["frames.jsonl"]
        .split(|b| *b == b'\n')
        .filter(|raw| !raw.is_empty())
        .map(|raw| serde_json::from_slice::<Value>(raw).unwrap())
        .collect::<Vec<_>>();
    edit(&mut rows[index]);
    files.insert(
        "frames.jsonl".into(),
        rows.iter().flat_map(encoded).collect(),
    );
}

fn rejected(plan: &ManimSemanticGraphPlan, files: &BTreeMap<String, Vec<u8>>, reason: &str) {
    let error = inspect_native_graph_readback_v1(plan, files)
        .unwrap_err()
        .to_string();
    assert!(error.contains(reason), "{error}");
}

#[test]
fn complete_controlled_getters_have_no_native_admission_or_video_authority() {
    let plan = fixture_plan();
    let files = controlled_observation(&plan);
    let summary = inspect_native_graph_readback_v1(&plan, &files).unwrap();
    assert_eq!(summary.sample_count, 100);
    assert_eq!(summary.entries.len(), files.len());
    assert_eq!(
        summary.total_bytes,
        files.values().map(|raw| raw.len() as u64).sum::<u64>()
    );
    assert!(
        !summary.native_video_verified
            && !summary.native_reopen_verified
            && !summary.profile_admitted
    );
}

#[test]
fn current_execution_stamp_and_generated_source_are_bound_before_observations() {
    let plan = fixture_plan();
    for field in ["revision", "branch_id"] {
        let mut files = controlled_observation(&plan);
        edit_json(&mut files, "header.json", |header| {
            header["execution_snapshot"][field] = if field == "revision" {
                json!(9)
            } else {
                json!(Uuid::from_u128(99))
            };
        });
        rejected(&plan, &files, "graph_header_binding_or_scope");
    }
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["generated_source_sha256"] = json!("ab".repeat(32))
    });
    rejected(&plan, &files, "graph_header_binding_or_scope");
}

#[test]
fn an_old_observation_cannot_be_reused_after_an_authored_text_edit() {
    let mut plan = fixture_plan();
    let original_plan = plan.clone();
    let original_source = serde_json::to_value(&original_plan.source).unwrap();
    let files = controlled_observation(&original_plan);
    let original_files = files.clone();
    let original_paths = graph_native_readback_paths_v1(&original_plan).unwrap();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    assert_eq!(vertices[0].label, "Origin\nA");
    vertices[0].label = "Edited\nA".into();

    // Keep both authored lines so this case reaches the original header binding.
    let edited_paths = graph_native_readback_paths_v1(&plan).unwrap();
    assert_eq!(edited_paths.required, original_paths.required);
    assert_eq!(edited_paths.allowed, original_paths.allowed);
    let observed = files
        .keys()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    assert!(edited_paths.required.is_subset(&observed));
    assert!(observed.is_subset(&edited_paths.allowed));
    assert_ne!(serde_json::to_value(&plan.source).unwrap(), original_source);
    rejected(&plan, &files, "graph_header_binding_or_scope");

    assert_eq!(
        serde_json::to_value(&original_plan.source).unwrap(),
        original_source
    );
    assert_eq!(files, original_files);
}

#[test]
fn shortening_an_authored_label_rejects_the_old_original_file_roster() {
    let mut plan = fixture_plan();
    let original_plan = plan.clone();
    let original_source = serde_json::to_value(&original_plan.source).unwrap();
    let files = controlled_observation(&original_plan);
    let original_files = files.clone();
    let original_paths = graph_native_readback_paths_v1(&original_plan).unwrap();
    let observed = files
        .keys()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    assert!(original_paths.required.is_subset(&observed));
    assert!(observed.is_subset(&original_paths.allowed));
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    assert_eq!(vertices[0].label, "Origin\nA");
    vertices[0].label = "Edited".into();

    // The old two-line originals must fail the roster guard before header parsing.
    let edited_paths = graph_native_readback_paths_v1(&plan).unwrap();
    assert!(
        !edited_paths.required.is_subset(&observed) || !observed.is_subset(&edited_paths.allowed)
    );
    rejected(&plan, &files, "graph_original_file_roster");

    assert_eq!(
        serde_json::to_value(&original_plan.source).unwrap(),
        original_source
    );
    assert_eq!(files, original_files);
}

#[test]
fn every_helper_font_and_license_pin_is_required() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["helper_modules"][0]["sha256"] = json!("ab".repeat(32))
    });
    rejected(&plan, &files, "graph_helper_pins_changed");
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["font_files"][0]["sha256"] = json!("ab".repeat(32))
    });
    rejected(&plan, &files, "graph_font_or_license_pins_changed");
}

#[test]
fn foreign_font_file_and_synthetic_weight_are_rejected_after_rehashing_the_report() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        block["layout"]["lines"][0]["runs"][0]["font_relative_path"] =
            json!("/usr/share/fonts/substitute.ttf");
    });
    rejected(&plan, &files, "graph_font_file_substituted");
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        block["layout"]["lines"][0]["runs"][0]["weight"] = json!(700)
    });
    rejected(&plan, &files, "graph_font_face_substituted");
}

#[test]
fn missing_and_overlapping_font_runs_are_not_accepted_as_full_coverage() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        block["layout"]["lines"][0]["runs"][0]["length"] = json!(5);
        block["layout"]["lines"][0]["runs"][0]["num_chars"] = json!(5);
    });
    rejected(&plan, &files, "graph_font_run_missing_bytes");
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        let run = block["layout"]["lines"][0]["runs"][0].clone();
        block["layout"]["lines"][0]["runs"]
            .as_array_mut()
            .unwrap()
            .push(run);
    });
    rejected(&plan, &files, "graph_font_run_overlap");
}

#[test]
fn a_font_run_cannot_split_the_middle_of_a_utf8_character() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 2, |block| {
        let length = block["layout"]["lines"][0]["text"].as_str().unwrap().len();
        block["layout"]["lines"][0]["runs"][0]["offset"] = json!(9);
        block["layout"]["lines"][0]["runs"][0]["length"] = json!(length - 9);
    });
    rejected(&plan, &files, "graph_font_run_utf8_boundary");
}

#[test]
fn unknown_glyphs_wrapping_and_ellipsis_are_rejected_from_native_metrics() {
    let plan = fixture_plan();
    for field in ["unknown_glyph_count", "is_wrapped", "is_ellipsized"] {
        let mut files = controlled_observation(&plan);
        edit_block(&mut files, 0, |block| {
            block["layout"]["lines"][0]["native_layout"][field] = if field == "unknown_glyph_count"
            {
                json!(1)
            } else {
                json!(true)
            };
        });
        rejected(&plan, &files, "graph_native_layout_unknown_or_wrapped");
    }
}

#[test]
fn literal_empty_lines_and_trailing_spaces_are_preserved() {
    let mut plan = fixture_plan();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[0].label = "Origin\n\nA ".into();
    let mut files = controlled_observation(&plan);
    inspect_native_graph_readback_v1(&plan, &files).unwrap();
    let block: Value = serde_json::from_slice(&files["labels/text_block_0000.json"]).unwrap();
    assert_eq!(block["layout"]["literal_line_count"], 3);
    assert_eq!(block["layout"]["lines"][1]["has_ink"], false);
    assert_eq!(block["layout"]["lines"][1]["vector"], Value::Null);
    assert!(files.contains_key("labels/source_0000_0001.svg"));
    assert!(!files.contains_key("labels/source_0000_0002.svg"));
    edit_block(&mut files, 0, |block| {
        block["layout"]["lines"][2]["text"] = json!("A")
    });
    rejected(&plan, &files, "graph_literal_line_text_changed");
}

#[test]
fn advance_overflow_is_not_resolved_by_scaling_or_using_logical_font_height() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        block["layout"]["lines"][0]["native_layout"]["logical_pango_units"][2] = json!(101 * 1024);
    });
    rejected(
        &plan,
        &files,
        "graph_native_text_advance_or_line_height_overflow",
    );
}

#[test]
fn a_shifted_baseline_is_rejected_even_when_both_reports_repeat_the_shift() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        let value = block["layout"]["lines"][0]["placement"]["baseline_px"]
            .as_f64()
            .unwrap()
            + 0.5;
        block["layout"]["lines"][0]["placement"]["baseline_px"] = json!(value);
        block["layout"]["lines"][0]["vector"]["baseline_px"] = json!(value);
    });
    rejected(&plan, &files, "graph_native_text_placement_changed");
}

#[test]
fn native_ink_or_outline_overflow_is_not_treated_as_a_valid_box_fit() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        block["layout"]["lines"][0]["vector"]["outline_bounds_px"][0] = json!(-1.0);
    });
    rejected(&plan, &files, "graph_vector_origin_or_outline_overflow");
}

#[test]
fn every_original_svg_is_verified_by_its_original_bytes() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    files
        .get_mut("labels/source_0000_0000.svg")
        .unwrap()
        .push(b' ');
    rejected(&plan, &files, "graph_original_byte_binding");
    let mut files = controlled_observation(&plan);
    files.get_mut("labels/label_0000.svg").unwrap().push(b' ');
    rejected(&plan, &files, "graph_original_byte_binding");
}

#[test]
fn allowed_but_unreported_svg_is_rejected_by_the_exact_native_ink_roster() {
    let mut plan = fixture_plan();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[0].label = "Origin\n\nA".into();
    let mut files = controlled_observation(&plan);
    assert!(
        graph_native_readback_paths_v1(&plan)
            .unwrap()
            .allowed
            .contains("labels/source_0000_0002.svg")
    );
    files.insert("labels/source_0000_0002.svg".into(), b"<svg/>".to_vec());
    rejected(&plan, &files, "graph_original_file_roster_not_exact");
}

#[test]
fn importer_cannot_center_fit_scale_or_claim_native_reopen() {
    let plan = fixture_plan();
    for (field, value) in [
        ("automatic_centering", json!(true)),
        ("automatic_fit", json!(true)),
        ("scale", json!(0.5)),
        ("native_reopen_verified", json!(true)),
    ] {
        let mut files = controlled_observation(&plan);
        edit_block(&mut files, 0, |block| block["manim_import"][field] = value);
        rejected(&plan, &files, "graph_svg_import_origin_scale_or_binding");
    }
}

#[test]
fn actual_camera_units_and_cairo_stroke_factor_must_match_every_frame() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_frame(&mut files, 3, |frame| {
        frame["camera"]["cairo_line_width_multiple"] = json!(0.01)
    });
    rejected(
        &plan,
        &files,
        "graph_native_frame_clock_camera_or_background",
    );
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["camera"]["frame_width"] = json!(14.222)
    });
    rejected(&plan, &files, "graph_header_binding_or_scope");
}

#[test]
fn changed_shape_bounds_layer_order_and_stroke_join_are_rejected() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["objects"][0]["control_point_min"][0] = json!(-220.0)
    });
    rejected(&plan, &files, "graph_native_shape_bounds_changed");
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["objects"].as_array_mut().unwrap().swap(0, 1)
    });
    rejected(&plan, &files, "graph_native_object_identity_or_paint");
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["objects"][0]["stroke_join"] = json!("ROUND")
    });
    rejected(&plan, &files, "graph_native_stroke_join_or_cap");
}

#[test]
fn scene_project_and_graph_clocks_are_checked_without_frame_rounding() {
    let plan = fixture_plan();
    for (field, value) in [
        ("scene_frame", json!(0)),
        ("project_frame", json!(202)),
        ("graph_local_frame", json!(0)),
    ] {
        let mut files = controlled_observation(&plan);
        edit_frame(&mut files, 1, |frame| frame[field] = value);
        rejected(
            &plan,
            &files,
            "graph_native_frame_clock_camera_or_background",
        );
    }
}

#[test]
fn event_end_is_half_open_and_graph_end_restores_the_scene_matte() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_frame(&mut files, 50, |frame| {
        frame["paint_opacity"][10] = json!(1.0)
    });
    rejected(&plan, &files, "graph_native_frame_opacity");
    let mut files = controlled_observation(&plan);
    edit_frame(&mut files, 80, |frame| {
        frame["background_hex"] = json!("#102030")
    });
    rejected(
        &plan,
        &files,
        "graph_native_frame_clock_camera_or_background",
    );
}

#[test]
fn missing_or_additional_terminal_records_do_not_complete_the_observation() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    let rows = files["frames.jsonl"]
        .split(|b| *b == b'\n')
        .take(100)
        .flat_map(|raw| raw.iter().copied().chain(*b"\n"))
        .collect();
    files.insert("frames.jsonl".into(), rows);
    rejected(&plan, &files, "graph_observation_closed_json");
    let mut files = controlled_observation(&plan);
    files
        .get_mut("frames.jsonl")
        .unwrap()
        .extend_from_slice(b"{}\n");
    rejected(
        &plan,
        &files,
        "graph_frame_extra_record_or_final_lf_missing",
    );
}

#[test]
fn duplicate_or_unknown_fields_and_missing_nullable_keys_are_rejected() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_block(&mut files, 0, |block| {
        block["layout"]["unexpected_authority"] = json!(true);
    });
    rejected(&plan, &files, "graph_observation_closed_json");
    let mut files = controlled_observation(&plan);
    let header = String::from_utf8(files["header.json"].clone()).unwrap();
    files.insert(
        "header.json".into(),
        format!("{{\"schema\":\"duplicate\",{}", &header[1..]).into_bytes(),
    );
    rejected(&plan, &files, "graph_observation_closed_json");
    let mut files = controlled_observation(&plan);
    edit_frame(&mut files, 0, |frame| {
        frame.as_object_mut().unwrap().remove("graph_local_frame");
    });
    rejected(&plan, &files, "graph_observation_closed_json");
}

#[test]
fn a_header_or_typed_report_cannot_promote_admission_or_video_status() {
    let plan = fixture_plan();
    for field in [
        "decoded_video_verified",
        "native_reopen_verified",
        "profile_admitted",
        "human_acceptance",
    ] {
        let mut files = controlled_observation(&plan);
        edit_json(&mut files, "header.json", |header| {
            header[field] = json!(true)
        });
        rejected(&plan, &files, "graph_header_binding_or_scope");
    }
}

#[test]
fn native_point_and_original_byte_budgets_are_applied_before_large_processing() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    edit_json(&mut files, "header.json", |header| {
        header["objects"][0]["point_count"] = json!(MAX_OBJECT_POINTS + 1)
    });
    rejected(&plan, &files, "graph_native_object_identity_or_paint");
    let mut files = controlled_observation(&plan);
    files.insert(
        "header.json".into(),
        vec![b' '; MAX_GRAPH_READBACK_BYTES + 1],
    );
    rejected(&plan, &files, "graph_original_byte_budget");
}

#[test]
fn a_required_report_cannot_be_replaced_with_an_arbitrary_path_or_empty_file() {
    let plan = fixture_plan();
    let mut files = controlled_observation(&plan);
    let raw = files.remove("labels/text_block_0000.json").unwrap();
    files.insert("../text_block_0000.json".into(), raw);
    rejected(&plan, &files, "graph_original_file_roster");
    let mut files = controlled_observation(&plan);
    files.insert("labels/text_block_0000.json".into(), Vec::new());
    rejected(&plan, &files, "graph_original_file_empty");
}
