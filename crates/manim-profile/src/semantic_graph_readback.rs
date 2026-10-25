//! Independent validation of retained originals from the fixed Graph job.
//!
//! This checks source bindings, closed records, file hashes, native text metrics,
//! exact frame indices and observed geometry/paint. It does not attest a runtime,
//! reconstruct pixels from point hashes, grant Ready, or certify native reopen.
use crate::semantic_graph::{ManimSemanticGraphPlan, exact_frame};
use crate::semantic_graph_source::{
    MAX_GRAPH_READBACK_BYTES, MAX_GRAPH_READBACK_FILES, compile_graph_program_source_only,
    graph_builtin_asset_pins, graph_helper_pins, graph_native_readback_paths_v1,
};
use crate::semantic_graph_text::{
    GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256, GraphAppearanceFrames, GraphTextBlock, GraphTextOwner,
    appearance_frames, arrowhead, text_blocks,
};
use crate::{ManimProfileError, ManimSourceEntryV1, Result};
use motionwright_domain::{
    GraphAppearance, GraphBox, GraphContent, GraphMotionPreference, GraphPoint, GraphVertexShape,
    SEMANTIC_GRAPH_PROFILE,
};
use serde::{Serialize, de::DeserializeOwned};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

#[path = "semantic_graph_readback_types.rs"]
mod types;
use types::*;

const MAX_POINTS: u32 = 1_048_576;
const MAX_OBJECT_POINTS: u32 = 131_072;
const MAX_SVG_BYTES: usize = 512 * 1024;
const PANGO_SCALE: f64 = 1024.0;
// Comparison of native f64 getters only; source coordinates and clocks are
// never quantized or rounded. Integer frame indices below are checked exactly.
const GETTER_EPSILON: f64 = 1e-9;

fn invalid(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

fn ensure(condition: bool, reason: &'static str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(reason))
    }
}

fn parse<T: DeserializeOwned>(raw: &[u8]) -> Result<T> {
    serde_json::from_slice(raw).map_err(|_| invalid("graph_observation_closed_json"))
}

fn finite(value: f64) -> bool {
    value.is_finite()
}

fn close(actual: f64, expected: f64) -> bool {
    finite(actual) && finite(expected) && (actual - expected).abs() <= GETTER_EPSILON
}

fn close_array<const N: usize>(actual: &[f64; N], expected: &[f64; N]) -> bool {
    actual.iter().zip(expected).all(|(a, b)| close(*a, *b))
}

fn digest_shape(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}

fn false_flags(flags: &[bool]) -> bool {
    flags.iter().all(|value| !value)
}

fn raw_binding(raw: &[u8], bytes: u64, sha256: &str) -> Result<()> {
    ensure(
        raw.len() as u64 == bytes && crate::source_digest(raw) == sha256,
        "graph_original_byte_binding",
    )
}

fn camera(plan: &ManimSemanticGraphPlan) -> Camera {
    Camera {
        pixel_width: plan.render_profile.width,
        pixel_height: plan.render_profile.height,
        frame_width: f64::from(plan.render_profile.width),
        frame_height: f64::from(plan.render_profile.height),
        frame_rate: f64::from(plan.render_profile.frame_rate),
        frame_center: [0.0; 3],
        cairo_line_width_multiple: 1.0,
    }
}

fn observed_owner(owner: &GraphTextOwner) -> Owner {
    match owner {
        GraphTextOwner::Vertex { id } => Owner::Vertex { id: *id },
        GraphTextOwner::Edge { id } => Owner::Edge { id: *id },
        GraphTextOwner::Disclosure => Owner::Disclosure,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct GraphNativeReadbackSummaryV1 {
    pub profile_id: String,
    pub plan_sha256: String,
    pub generated_source_sha256: String,
    pub sample_count: u32,
    pub total_bytes: u64,
    pub entries: Vec<ManimSourceEntryV1>,
    pub native_video_verified: bool,
    pub native_reopen_verified: bool,
    pub profile_admitted: bool,
}

type Bounds = ([f64; 3], [f64; 3]);

#[derive(Debug)]
struct ExpectedObject {
    name: String,
    native_class: &'static str,
    mode: &'static str,
    color: String,
    width: f64,
    bounds: Option<Bounds>,
    label: Option<usize>,
    appearance: Option<GraphAppearanceFrames>,
    active: (u32, u32),
}

fn world(plan: &ManimSemanticGraphPlan, point: &GraphPoint) -> [f64; 3] {
    [
        point.x - f64::from(plan.render_profile.width) / 2.0,
        f64::from(plan.render_profile.height) / 2.0 - point.y,
        0.0,
    ]
}

fn bounds(plan: &ManimSemanticGraphPlan, points: &[GraphPoint]) -> Result<Bounds> {
    ensure(!points.is_empty(), "graph_expected_geometry_empty")?;
    let mut low = [f64::INFINITY; 3];
    let mut high = [f64::NEG_INFINITY; 3];
    for point in points {
        let point = world(plan, point);
        for axis in 0..3 {
            low[axis] = low[axis].min(point[axis]);
            high[axis] = high[axis].max(point[axis]);
        }
    }
    Ok((low, high))
}

fn edge_appearances(plan: &ManimSemanticGraphPlan) -> BTreeMap<Uuid, &GraphAppearance> {
    match &plan.source.content {
        GraphContent::CausalDiagram { edges, .. } => edges
            .iter()
            .map(|edge| (edge.id, &edge.appearance))
            .collect(),
        GraphContent::SystemFlow { edges, .. } => edges
            .iter()
            .map(|edge| (edge.id, &edge.appearance))
            .collect(),
    }
}

fn append_labels(
    output: &mut Vec<ExpectedObject>,
    blocks: &[GraphTextBlock],
    owner: &GraphTextOwner,
    appearance: Option<&GraphAppearanceFrames>,
    color: &str,
) {
    for (index, block) in blocks.iter().enumerate() {
        if &block.owner == owner {
            output.push(ExpectedObject {
                name: format!("mw_graph_label_{:04}", block.ordinal),
                native_class: "GraphOutlineMobject",
                mode: "fill",
                color: color.into(),
                width: 0.0,
                bounds: None,
                label: Some(index),
                appearance: appearance.cloned(),
                active: (block.active_start_frame, block.active_end_frame_exclusive),
            });
        }
    }
}

fn expected_objects(
    plan: &ManimSemanticGraphPlan,
    blocks: &[GraphTextBlock],
) -> Result<Vec<ExpectedObject>> {
    let fps = plan.render_profile.frame_rate;
    let graph_count = exact_frame(plan.source.time_range.duration, fps)?;
    let style = &plan.source.style;
    let sample = plan.sample(0)?;
    let appearances = edge_appearances(plan);
    let mut output = Vec::new();
    for edge in &sample.edges {
        let appearance = appearance_frames(
            appearances
                .get(&edge.id)
                .ok_or_else(|| invalid("graph_edge_missing"))?,
            fps,
        )?;
        output.push(ExpectedObject {
            name: format!("mw_graph_edge_{}", edge.id.simple()),
            native_class: "VMobject",
            mode: "stroke",
            color: style.edge_color.clone(),
            width: style.edge_width,
            bounds: Some(bounds(plan, &edge.points)?),
            label: None,
            appearance: Some(appearance.clone()),
            active: (0, graph_count),
        });
        output.push(ExpectedObject {
            name: format!("mw_graph_arrow_{}", edge.id.simple()),
            native_class: "Polygon",
            mode: "fill",
            color: style.edge_color.clone(),
            width: 0.0,
            bounds: Some(bounds(plan, &arrowhead(&edge.points, style.edge_width)?)?),
            label: None,
            appearance: Some(appearance.clone()),
            active: (0, graph_count),
        });
        append_labels(
            &mut output,
            blocks,
            &GraphTextOwner::Edge { id: edge.id },
            Some(&appearance),
            &style.text_color,
        );
    }
    for vertex in plan.source.content.vertices() {
        let box_ = &vertex.r#box;
        let appearance = appearance_frames(&vertex.appearance, fps)?;
        output.push(ExpectedObject {
            name: format!("mw_graph_vertex_{}", vertex.id.simple()),
            native_class: match vertex.shape {
                GraphVertexShape::Circle => "Circle",
                GraphVertexShape::Rectangle => "RoundedRectangle",
            },
            mode: "fill",
            color: style.vertex_fill.clone(),
            width: 0.0,
            bounds: Some(bounds(
                plan,
                &[
                    GraphPoint {
                        x: box_.x,
                        y: box_.y,
                    },
                    GraphPoint {
                        x: box_.x + box_.width,
                        y: box_.y + box_.height,
                    },
                ],
            )?),
            label: None,
            appearance: Some(appearance.clone()),
            active: (0, graph_count),
        });
        append_labels(
            &mut output,
            blocks,
            &GraphTextOwner::Vertex { id: vertex.id },
            Some(&appearance),
            &style.text_color,
        );
    }
    append_labels(
        &mut output,
        blocks,
        &GraphTextOwner::Disclosure,
        None,
        &style.text_color,
    );
    if let GraphContent::SystemFlow { events, .. } = &plan.source.content {
        for event in events {
            let edge = sample
                .edges
                .iter()
                .find(|edge| edge.id == event.edge_id)
                .ok_or_else(|| invalid("graph_event_edge_missing"))?;
            let start = exact_frame(event.at, fps)?;
            let end = start
                .checked_add(exact_frame(event.duration, fps)?)
                .ok_or_else(|| invalid("graph_event_frame_overflow"))?;
            output.push(ExpectedObject {
                name: format!("mw_graph_event_{}", event.id.simple()),
                native_class: "VMobject",
                mode: "stroke",
                color: style.accent_color.clone(),
                width: style.edge_width * 2.0,
                bounds: Some(bounds(plan, &edge.points)?),
                label: None,
                appearance: None,
                active: (start, end),
            });
        }
    }
    Ok(output)
}

fn expected_opacity(
    object: &ExpectedObject,
    local: Option<u32>,
    motion: GraphMotionPreference,
) -> f64 {
    let Some(local) = local.filter(|index| object.active.0 <= *index && *index < object.active.1)
    else {
        return 0.0;
    };
    let Some(appearance) = &object.appearance else {
        return 1.0;
    };
    if local < appearance.start_frame {
        return 0.0;
    }
    let elapsed = local - appearance.start_frame;
    if motion == GraphMotionPreference::Off || elapsed >= appearance.duration_frames {
        1.0
    } else {
        f64::from(elapsed) / f64::from(appearance.duration_frames)
    }
}

fn validate_header(
    header: &Header,
    plan: &ManimSemanticGraphPlan,
    plan_sha: &str,
    source_sha: &str,
) -> Result<()> {
    ensure(
        header.schema == "motionwright.manim.graph-native-observation/1"
            && header.profile_id == SEMANTIC_GRAPH_PROFILE
            && header.plan_sha256 == plan_sha
            && header.generated_source_sha256 == source_sha
            && header.execution_snapshot == plan.execution_snapshot
            && header.graph_id == plan.source.id
            && header.scene_id == plan.source.scene_id
            && header.camera == camera(plan)
            && header.derived_byte_limit == MAX_GRAPH_READBACK_BYTES as u64
            && header.one_svg_parser_temporary_byte_limit == MAX_SVG_BYTES as u64
            && header.point_limit == MAX_POINTS
            && false_flags(&[
                header.decoded_video_verified,
                header.native_reopen_verified,
                header.profile_admitted,
                header.human_acceptance,
            ]),
        "graph_header_binding_or_scope",
    )?;
    let helpers = serde_json::to_value(&header.helper_modules)
        .map_err(|_| invalid("graph_helper_observation_encoding"))?;
    ensure(
        helpers == serde_json::Value::Array(graph_helper_pins()),
        "graph_helper_pins_changed",
    )?;
    let expected_fonts = graph_builtin_asset_pins()
        .iter()
        .map(|pin| Asset {
            relative_path: pin.relative_path.into(),
            sha256: pin.sha256.into(),
            size_bytes: pin.size_bytes,
        })
        .collect::<Vec<_>>();
    ensure(
        header.font_files == expected_fonts,
        "graph_font_or_license_pins_changed",
    )
}

#[derive(Default)]
struct Versions {
    pango_and_fontconfig: Option<(String, u32)>,
    cairo: Option<String>,
}

fn version_tuple(value: &str) -> Result<[u32; 3]> {
    ensure(
        !value.is_empty() && value.len() <= 64,
        "graph_native_version_shape",
    )?;
    let values = value
        .split('.')
        .map(str::parse::<u32>)
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|_| invalid("graph_native_version_shape"))?;
    values
        .try_into()
        .map_err(|_| invalid("graph_native_version_shape"))
}

impl Versions {
    fn pango(&mut self, version: &str, fontconfig: u32) -> Result<()> {
        ensure(
            version_tuple(version)? >= [1, 48, 0] && fontconfig > 0,
            "graph_native_font_version",
        )?;
        let current = (version.to_owned(), fontconfig);
        if let Some(previous) = &self.pango_and_fontconfig {
            ensure(previous == &current, "graph_native_font_version_changed")?;
        } else {
            self.pango_and_fontconfig = Some(current);
        }
        Ok(())
    }

    fn cairo(&mut self, version: &str) -> Result<()> {
        version_tuple(version)?;
        if let Some(previous) = &self.cairo {
            ensure(previous == version, "graph_native_cairo_version_changed")?;
        } else {
            self.cairo = Some(version.to_owned());
        }
        Ok(())
    }
}

fn validate_runs(runs: &[FontRun], text: &str, family: &str) -> Result<()> {
    if text.is_empty() {
        return ensure(runs.is_empty(), "graph_empty_line_has_runs");
    }
    ensure(
        !runs.is_empty() && runs.len() <= 4096,
        "graph_font_run_budget",
    )?;
    let prefix = match family {
        "Instrument Sans" => "instrument-sans/files/",
        "IBM Plex Mono" => "ibm-plex-mono/files/",
        _ => return Err(invalid("graph_font_family_outside_profile")),
    };
    let mut covered = vec![false; text.len()];
    for run in runs {
        ensure(
            run.family == family && run.weight == 400 && run.style == 0 && run.face_index == 0,
            "graph_font_face_substituted",
        )?;
        let pin = graph_builtin_asset_pins()
            .iter()
            .find(|pin| {
                pin.relative_path == run.font_relative_path
                    && pin.relative_path.starts_with(prefix)
                    && pin.relative_path.ends_with(".woff2")
            })
            .ok_or_else(|| invalid("graph_font_file_substituted"))?;
        ensure(
            pin.sha256 == run.font_sha256,
            "graph_font_bytes_substituted",
        )?;
        let end = run
            .offset
            .checked_add(run.length)
            .ok_or_else(|| invalid("graph_font_run_overflow"))?;
        ensure(run.length > 0 && end <= text.len(), "graph_font_run_range")?;
        let fragment = text
            .get(run.offset..end)
            .ok_or_else(|| invalid("graph_font_run_utf8_boundary"))?;
        ensure(
            fragment.chars().count() == run.num_chars,
            "graph_font_run_character_count",
        )?;
        let coverage = &mut covered[run.offset..end];
        ensure(
            !coverage.iter().any(|value| *value),
            "graph_font_run_overlap",
        )?;
        coverage.fill(true);
    }
    ensure(
        covered.iter().all(|value| *value),
        "graph_font_run_missing_bytes",
    )
}

fn placed_bounds(origin: [f64; 2], rect: [i32; 4]) -> [f64; 4] {
    [
        origin[0] + f64::from(rect[0]) / PANGO_SCALE,
        origin[1] + f64::from(rect[1]) / PANGO_SCALE,
        origin[0] + (f64::from(rect[0]) + f64::from(rect[2])) / PANGO_SCALE,
        origin[1] + (f64::from(rect[1]) + f64::from(rect[3])) / PANGO_SCALE,
    ]
}

fn inside(value: &[f64; 4], box_: &GraphBox) -> bool {
    value.iter().all(|v| finite(*v))
        && box_.x <= value[0]
        && value[0] <= value[2]
        && value[2] <= box_.x + box_.width
        && box_.y <= value[1]
        && value[1] <= value[3]
        && value[3] <= box_.y + box_.height
}

fn validate_placement(
    line: &Line,
    box_: &GraphBox,
    font_size: f64,
    line_count: usize,
    index: usize,
) -> Result<()> {
    let native = &line.native_layout;
    ensure(
        native.unknown_glyph_count == 0
            && native.line_count == 1
            && !native.is_wrapped
            && !native.is_ellipsized
            && native.ink_pango_units[2] >= 0
            && native.ink_pango_units[3] >= 0
            && native.logical_pango_units[2] >= 0
            && native.logical_pango_units[3] >= 0,
        "graph_native_layout_unknown_or_wrapped",
    )?;
    let line_height = 1.2 * font_size;
    let advance = f64::from(native.logical_pango_units[2]) / PANGO_SCALE;
    ensure(
        line_count > 0
            && index < line_count
            && (line_count as f64) * line_height <= box_.height
            && advance >= 0.0
            && advance <= box_.width,
        "graph_native_text_advance_or_line_height_overflow",
    )?;
    let start_x = box_.x + (box_.width - advance) / 2.0;
    let baseline = box_.y
        + box_.height / 2.0
        + (index as f64 - (line_count - 1) as f64 / 2.0) * line_height
        + 0.35 * font_size;
    let origin = [
        start_x - f64::from(native.logical_pango_units[0]) / PANGO_SCALE,
        baseline - f64::from(native.baseline_pango_units) / PANGO_SCALE,
    ];
    let placement = &line.placement;
    ensure(
        close(placement.line_start_x_px, start_x)
            && close(placement.advance_px, advance)
            && close(placement.baseline_px, baseline)
            && close_array(&placement.layout_origin_px, &origin)
            && close(placement.line_height_px, line_height),
        "graph_native_text_placement_changed",
    )?;
    if line.text.is_empty() {
        ensure(advance == 0.0, "graph_empty_line_has_advance")?;
    }
    let ink = native.ink_pango_units;
    let has_ink = ink[2] > 0 && ink[3] > 0;
    ensure(
        line.has_ink == has_ink && (has_ink || (ink[2] == 0 && ink[3] == 0)),
        "graph_native_ink_shape",
    )?;
    match (has_ink, line.native_ink_bounds_px) {
        (true, Some(actual)) => {
            let expected = placed_bounds(origin, ink);
            ensure(
                close_array(&actual, &expected) && inside(&actual, box_),
                "graph_native_ink_overflow_or_changed",
            )
        }
        (false, None) => Ok(()),
        _ => Err(invalid("graph_native_ink_presence")),
    }
}

fn validate_vector(vector: &Vector, line: &Line, box_: &GraphBox) -> Result<()> {
    ensure(
        vector.schema == "motionwright.manim.graph-text-vector-observation/1"
            && vector.status == "OBSERVED_PANGO_CAIRO_OUTLINES_ONLY"
            && vector.text_sha256 == line.text_sha256
            && vector.text_bytes == line.text_bytes
            && vector.runs == line.runs
            && vector.unknown_glyph_count == 0
            && vector.units_per_pixel == 1024
            && vector.pango_version == line.pango_version
            && vector.fontconfig_version == line.fontconfig_version
            && (1..=4096).contains(&vector.svg_outline_paths)
            && false_flags(&[
                vector.fallback_allowed,
                vector.manim_import_verified,
                vector.manim_frame_verified,
                vector.profile_admitted,
                vector.human_acceptance,
            ]),
        "graph_vector_observation_binding",
    )?;
    let expected_logical = placed_bounds(
        line.placement.layout_origin_px,
        line.native_layout.logical_pango_units,
    );
    // Logical font height is observed, not substituted for the authored 1.2F line height.
    ensure(
        close_array(&vector.logical_bounds_px, &expected_logical)
            && close(vector.baseline_px, line.placement.baseline_px)
            && close_array(&vector.layout_origin_px, &line.placement.layout_origin_px)
            && inside(&vector.outline_bounds_px, box_)
            && vector.outline_bounds_px[0] < vector.outline_bounds_px[2]
            && vector.outline_bounds_px[1] < vector.outline_bounds_px[3],
        "graph_vector_origin_or_outline_overflow",
    )
}

fn point_bounds_valid(low: &[f64; 3], high: &[f64; 3]) -> bool {
    low.iter().chain(high).all(|value| finite(*value))
        && (0..3).all(|axis| low[axis] <= high[axis])
        && low[2] == 0.0
        && high[2] == 0.0
}

fn validate_imported(
    imported: &Imported,
    plan: &ManimSemanticGraphPlan,
    ordinal: u16,
    raw: &[u8],
    sha256: &str,
) -> Result<()> {
    ensure(
        imported.schema == "motionwright.manim.graph-svg-import-observation/1"
            && imported.manim_version == "0.21.0"
            && imported.svg_sha256 == sha256
            && imported.svg_bytes == raw.len() as u64
            && imported.private_svg_name == format!("label_{ordinal:04}.svg")
            && imported.origin_reflection == [1.0, -1.0, 1.0]
            && imported.viewport_translation
                == [
                    -f64::from(plan.render_profile.width) / 2.0,
                    f64::from(plan.render_profile.height) / 2.0,
                    0.0,
                ]
            && imported.scale == 1.0
            && (1..=MAX_OBJECT_POINTS).contains(&imported.point_count)
            && imported.point_encoding == "xyz-f64le"
            && digest_shape(&imported.points_sha256)
            && point_bounds_valid(&imported.control_point_min, &imported.control_point_max)
            && false_flags(&[
                imported.automatic_centering,
                imported.automatic_fit,
                imported.control_bounds_are_glyph_ink_bounds,
                imported.manim_frame_verified,
                imported.native_reopen_verified,
                imported.profile_admitted,
                imported.human_acceptance,
            ]),
        "graph_svg_import_origin_scale_or_binding",
    )
}

fn validate_text_block(
    plan: &ManimSemanticGraphPlan,
    expected: &GraphTextBlock,
    reference: &TextReference,
    files: &BTreeMap<String, Vec<u8>>,
    seen: &mut BTreeSet<String>,
    versions: &mut Versions,
) -> Result<Option<Imported>> {
    let name = format!("labels/text_block_{:04}.json", expected.ordinal);
    ensure(
        reference.ordinal == expected.ordinal
            && reference.owner == observed_owner(&expected.owner)
            && reference.file_name == format!("text_block_{:04}.json", expected.ordinal),
        "graph_text_reference_order_or_owner",
    )?;
    let raw = files
        .get(&name)
        .ok_or_else(|| invalid("graph_text_original_missing"))?;
    raw_binding(raw, reference.size_bytes, &reference.sha256)?;
    ensure(seen.insert(name), "graph_text_original_repeated")?;
    let evidence: TextBlock = parse(raw)?;
    let layout = &evidence.layout;
    let typography = plan.typography_requirement();
    ensure(
        evidence.ordinal == expected.ordinal
            && evidence.owner == observed_owner(&expected.owner)
            && layout.schema == "motionwright.manim.graph-text-layout-observation/1"
            && layout.status == "OBSERVED_NATIVE_TEXT_GEOMETRY_ONLY"
            && layout.contract_sha256 == GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256
            && layout.resource == expected.resource
            && layout.text_sha256 == crate::source_digest(expected.text.as_bytes())
            && layout.text_bytes == expected.text.len() as u64
            && layout.font_size_px == typography.font_size_px
            && layout.editorial_family == typography.editorial_family
            && layout.native_family == typography.native_family
            && layout.weight == 400
            && layout.authored_box == expected.authored_box
            && false_flags(&[
                layout.automatic_wrap,
                layout.automatic_scale,
                layout.clipping,
                layout.fallback_allowed,
                layout.manim_frame_verified,
                layout.native_reopen_verified,
                layout.profile_admitted,
                layout.human_acceptance,
            ]),
        "graph_text_source_contract_or_scope",
    )?;
    let literal_lines = expected.text.split('\n').collect::<Vec<_>>();
    ensure(
        layout.literal_line_count == literal_lines.len()
            && layout.lines.len() == literal_lines.len(),
        "graph_literal_line_count_changed",
    )?;
    let mut ink_index = 0usize;
    let mut original_bytes = 0usize;
    for (index, (line, text)) in layout.lines.iter().zip(literal_lines).enumerate() {
        ensure(
            line.index == index
                && line.text == text
                && line.text_bytes == text.len() as u64
                && line.text_sha256 == crate::source_digest(text.as_bytes()),
            "graph_literal_line_text_changed",
        )?;
        validate_runs(&line.runs, text, typography.native_family)?;
        versions.pango(&line.pango_version, line.fontconfig_version)?;
        validate_placement(
            line,
            &expected.authored_box,
            typography.font_size_px,
            layout.lines.len(),
            index,
        )?;
        match (line.has_ink, &line.vector) {
            (true, Some(vector)) => {
                validate_vector(vector, line, &expected.authored_box)?;
                versions.cairo(&vector.cairo_version)?;
                let original = layout
                    .original_line_svgs
                    .get(ink_index)
                    .ok_or_else(|| invalid("graph_original_line_descriptor_missing"))?;
                ensure(
                    original.sha256 == vector.svg_sha256 && original.size_bytes == vector.svg_bytes,
                    "graph_original_line_descriptor_changed",
                )?;
                let path = format!("labels/source_{:04}_{ink_index:04}.svg", expected.ordinal);
                let raw = files
                    .get(&path)
                    .ok_or_else(|| invalid("graph_original_line_svg_missing"))?;
                ensure(
                    !raw.is_empty() && raw.len() <= MAX_SVG_BYTES,
                    "graph_original_line_svg_budget",
                )?;
                raw_binding(raw, vector.svg_bytes, &vector.svg_sha256)?;
                original_bytes = original_bytes
                    .checked_add(raw.len())
                    .ok_or_else(|| invalid("graph_original_line_svg_budget"))?;
                ensure(
                    original_bytes <= MAX_SVG_BYTES,
                    "graph_original_line_svg_budget",
                )?;
                ensure(seen.insert(path), "graph_original_line_svg_repeated")?;
                ink_index += 1;
            }
            (false, None) => {}
            _ => return Err(invalid("graph_line_ink_vector_presence")),
        }
    }
    ensure(
        layout.original_line_svgs.len() == ink_index,
        "graph_original_line_svg_roster",
    )?;
    match (ink_index, &layout.svg_sha256, &evidence.manim_import) {
        (0, None, None) => {
            ensure(layout.svg_bytes == 0, "graph_empty_label_composite_bytes")?;
            Ok(None)
        }
        (count, Some(sha256), Some(imported)) if count > 0 => {
            let path = format!("labels/label_{:04}.svg", expected.ordinal);
            let raw = files
                .get(&path)
                .ok_or_else(|| invalid("graph_composite_svg_missing"))?;
            ensure(
                !raw.is_empty() && raw.len() <= MAX_SVG_BYTES,
                "graph_composite_svg_budget",
            )?;
            raw_binding(raw, layout.svg_bytes, sha256)?;
            validate_imported(imported, plan, expected.ordinal, raw, sha256)?;
            ensure(seen.insert(path), "graph_composite_svg_repeated")?;
            Ok(Some(imported.clone()))
        }
        _ => Err(invalid("graph_label_ink_composite_presence")),
    }
}

fn validate_geometry(
    geometry: &[Geometry],
    expected: &[ExpectedObject],
    blocks: &[GraphTextBlock],
    imports: &[Option<Imported>],
) -> Result<()> {
    ensure(
        geometry.len() == expected.len(),
        "graph_native_object_roster",
    )?;
    let mut points = 0u32;
    for (index, (actual, expected)) in geometry.iter().zip(expected).enumerate() {
        let empty_label = expected
            .label
            .is_some_and(|ordinal| imports[ordinal].is_none());
        let name = if empty_label {
            format!(
                "mw_graph_empty_label_{:04}",
                blocks[expected
                    .label
                    .ok_or_else(|| invalid("graph_empty_label_ordinal"))?]
                .ordinal
            )
        } else {
            expected.name.clone()
        };
        let class = if empty_label {
            "VGroup"
        } else {
            expected.native_class
        };
        ensure(
            actual.name == name
                && actual.native_class == class
                && actual.z_index == index as f64
                && actual.point_count <= MAX_OBJECT_POINTS
                && actual.point_encoding == "xyz-f64le"
                && digest_shape(&actual.points_sha256)
                && !actual.control_bounds_are_glyph_ink_bounds
                && actual.paint_mode == expected.mode
                && actual.paint_color.eq_ignore_ascii_case(&expected.color)
                && actual.stroke_width == expected.width,
            "graph_native_object_identity_or_paint",
        )?;
        ensure(
            if expected.mode == "stroke" {
                actual.stroke_join.as_deref() == Some("MITER")
                    && actual.stroke_cap.as_deref() == Some("BUTT")
            } else {
                actual.stroke_join.is_none() && actual.stroke_cap.is_none()
            },
            "graph_native_stroke_join_or_cap",
        )?;
        points = points
            .checked_add(actual.point_count)
            .ok_or_else(|| invalid("graph_native_total_point_budget"))?;
        ensure(points <= MAX_POINTS, "graph_native_total_point_budget")?;
        if empty_label {
            ensure(
                actual.point_count == 0
                    && actual.members_with_points == 0
                    && actual.control_point_min.is_none()
                    && actual.control_point_max.is_none()
                    && actual.points_sha256 == crate::source_digest(&[]),
                "graph_empty_label_has_native_geometry",
            )?;
            continue;
        }
        ensure(
            actual.point_count >= 4
                && actual.members_with_points > 0
                && actual.members_with_points <= actual.point_count,
            "graph_native_object_point_members",
        )?;
        let low = actual
            .control_point_min
            .ok_or_else(|| invalid("graph_native_point_bounds_missing"))?;
        let high = actual
            .control_point_max
            .ok_or_else(|| invalid("graph_native_point_bounds_missing"))?;
        ensure(
            point_bounds_valid(&low, &high),
            "graph_native_point_bounds_invalid",
        )?;
        if let Some(ordinal) = expected.label {
            let imported = imports[ordinal]
                .as_ref()
                .ok_or_else(|| invalid("graph_label_import_observation_missing"))?;
            ensure(
                actual.point_count == imported.point_count
                    && actual.points_sha256 == imported.points_sha256
                    && close_array(&low, &imported.control_point_min)
                    && close_array(&high, &imported.control_point_max),
                "graph_label_native_geometry_changed",
            )?;
        } else {
            let (expected_low, expected_high) = expected
                .bounds
                .ok_or_else(|| invalid("graph_expected_bounds_missing"))?;
            ensure(
                actual.members_with_points == 1
                    && close_array(&low, &expected_low)
                    && close_array(&high, &expected_high),
                "graph_native_shape_bounds_changed",
            )?;
        }
    }
    Ok(())
}

fn validate_frames(
    raw: &[u8],
    plan: &ManimSemanticGraphPlan,
    objects: &[ExpectedObject],
    geometry: &[Geometry],
    plan_sha: &str,
) -> Result<()> {
    let mut lines = raw.split(|byte| *byte == b'\n');
    let count = plan.scene_clock.frame_count;
    let first = exact_frame(plan.source.time_range.start, plan.render_profile.frame_rate)?;
    let graph_count = exact_frame(
        plan.source.time_range.duration,
        plan.render_profile.frame_rate,
    )?;
    let expected_camera = camera(plan);
    for index in 0..count {
        let bytes = lines
            .next()
            .ok_or_else(|| invalid("graph_frame_record_missing"))?;
        let frame: Frame = parse(bytes)?;
        let local = index
            .checked_sub(first)
            .filter(|value| *value < graph_count);
        let project = plan
            .scene_clock
            .project_start_frame
            .checked_add(u64::from(index))
            .ok_or_else(|| invalid("graph_project_frame_overflow"))?;
        let background = if local.is_some() {
            &plan.source.style.background
        } else {
            &plan.scene_matte.color
        };
        let expected_alpha = f64::from(index) / (f64::from(count) - 0.5);
        ensure(
            frame.record_type == "frame"
                && frame.scene_frame == index
                && frame.project_frame == project
                && frame.graph_local_frame == local
                && frame.callback_alpha.is_finite()
                && (frame.callback_alpha - expected_alpha).abs() <= 1e-12
                && frame.camera == expected_camera
                && frame.background_hex.eq_ignore_ascii_case(background)
                && frame.background_opacity == 1.0
                && frame.paint_opacity.len() == objects.len(),
            "graph_native_frame_clock_camera_or_background",
        )?;
        for ((actual, expected), native) in frame.paint_opacity.iter().zip(objects).zip(geometry) {
            if native.members_with_points == 0 {
                ensure(actual.is_none(), "graph_empty_native_family_paint")?;
            } else {
                let expected = expected_opacity(expected, local, plan.source.motion);
                ensure(
                    actual.is_some_and(|value| {
                        value.is_finite() && (value - expected).abs() <= 1e-12
                    }),
                    "graph_native_frame_opacity",
                )?;
            }
        }
    }
    let terminal: Terminal = parse(
        lines
            .next()
            .ok_or_else(|| invalid("graph_terminal_missing"))?,
    )?;
    ensure(
        terminal.record_type == "observed_terminal"
            && terminal.sample_count == count
            && terminal.static_geometry_reobserved_unchanged
            && terminal.plan_sha256 == plan_sha
            && terminal.terminal_editing_scene_frame == count - 1
            && !terminal.decoded_video_verified
            && !terminal.native_reopen_verified,
        "graph_native_terminal_binding",
    )?;
    ensure(
        lines.next() == Some(&[][..]) && lines.next().is_none(),
        "graph_frame_extra_record_or_final_lf_missing",
    )
}

/// The caller supplies already retained original bytes from one known terminal
/// job. This pure verifier reads no paths, starts no process and changes no lease.
/// A successful summary is a scoped getter observation, never native admission.
pub fn inspect_native_graph_readback_v1(
    plan: &ManimSemanticGraphPlan,
    files: &BTreeMap<String, Vec<u8>>,
) -> Result<GraphNativeReadbackSummaryV1> {
    let prepared = compile_graph_program_source_only(plan)?;
    let paths = graph_native_readback_paths_v1(plan)?;
    let observed = files.keys().cloned().collect::<BTreeSet<_>>();
    ensure(
        !files.is_empty()
            && files.len() <= MAX_GRAPH_READBACK_FILES
            && paths.required.is_subset(&observed)
            && observed.is_subset(&paths.allowed),
        "graph_original_file_roster",
    )?;
    let mut total = 0usize;
    for bytes in files.values() {
        ensure(!bytes.is_empty(), "graph_original_file_empty")?;
        total = total
            .checked_add(bytes.len())
            .ok_or_else(|| invalid("graph_original_byte_budget"))?;
        ensure(
            total <= MAX_GRAPH_READBACK_BYTES,
            "graph_original_byte_budget",
        )?;
    }
    let header: Header = parse(
        files
            .get("header.json")
            .ok_or_else(|| invalid("graph_header_missing"))?,
    )?;
    validate_header(
        &header,
        plan,
        &prepared.plan_sha256,
        &prepared.source_sha256,
    )?;
    let blocks = text_blocks(plan)?;
    ensure(
        header.text_blocks.len() == blocks.len(),
        "graph_text_reference_roster",
    )?;
    let mut seen = BTreeSet::from(["header.json".into(), "frames.jsonl".into()]);
    let mut versions = Versions::default();
    let mut imports = Vec::with_capacity(blocks.len());
    for (expected, reference) in blocks.iter().zip(&header.text_blocks) {
        imports.push(validate_text_block(
            plan,
            expected,
            reference,
            files,
            &mut seen,
            &mut versions,
        )?);
    }
    ensure(seen == observed, "graph_original_file_roster_not_exact")?;
    let objects = expected_objects(plan, &blocks)?;
    validate_geometry(&header.objects, &objects, &blocks, &imports)?;
    validate_frames(
        files
            .get("frames.jsonl")
            .ok_or_else(|| invalid("graph_frames_missing"))?,
        plan,
        &objects,
        &header.objects,
        &prepared.plan_sha256,
    )?;
    Ok(GraphNativeReadbackSummaryV1 {
        profile_id: SEMANTIC_GRAPH_PROFILE.into(),
        plan_sha256: prepared.plan_sha256,
        generated_source_sha256: prepared.source_sha256,
        sample_count: plan.scene_clock.frame_count,
        total_bytes: total as u64,
        entries: files
            .iter()
            .map(|(path, bytes)| ManimSourceEntryV1 {
                path: path.clone(),
                bytes: bytes.len() as u64,
                sha256: crate::source_digest(bytes),
            })
            .collect(),
        native_video_verified: false,
        native_reopen_verified: false,
        profile_admitted: false,
    })
}

#[cfg(test)]
#[path = "semantic_graph_readback_tests.rs"]
mod tests;
