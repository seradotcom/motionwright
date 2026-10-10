use crate::component_text::fixed_component_text;
use crate::expressive::{entry_motion, responsive_scene};
use motionwright_domain::{
    BlendMode, CanvasNode, CoordinateSpace, DeliverableProfile, FramingStrategy,
    MotionInterpolation, MotionProperty, Project, RationalTime, RendererKind, Scene,
};
use semwright_media_time::{CueGraph, Rate, Rational};
use semwright_motion_authoring::{
    AUTHORING_VERSION, Archetype, AspectFamily, AssetRef, Beat as AuthoringBeat, EditorialSystem,
    Film, FontFallback, FontSpec, Insets, Invocation, Layer, MotionEasing, NarrativeRole,
    OutputProfile, Point, Primitive, Sequence, Shot, Size, SpatialIntent, StartAnchor, Subject,
    SubjectContent, TemporalConstraint, TemporalGraph, TemporalSpan, TextDirection, TextRun,
    VisualConstraint, realize,
};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use semwright_semantic_composition::Digest;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use uuid::Uuid;

const CANVAS_WIDTH: f64 = 1920.0;
const CANVAS_HEIGHT: f64 = 1080.0;
const MAX_FILM_SECONDS: i64 = 600;
const MAX_SEQUENCES: usize = 32;
const MAX_TEMPORAL_SPANS: usize = 128;

pub const MOTION_CANVAS_FONT_FAMILY: &str = "Instrument Sans Variable";
pub const MOTION_CANVAS_MONO_FONT_FAMILY: &str = "IBM Plex Mono";

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn unsupported(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

fn contract(context: &str, error: impl std::fmt::Display) -> Error {
    invalid(format!("{context}: {error}"))
}

fn uid(prefix: &str, id: Uuid) -> String {
    format!("{prefix}-{}", id.simple())
}

fn add(left: RationalTime, right: RationalTime, context: &str) -> NativeResult<RationalTime> {
    left.checked_add(right)
        .map_err(|error| contract(context, error))
}

fn sub(left: RationalTime, right: RationalTime, context: &str) -> NativeResult<RationalTime> {
    left.checked_sub(right)
        .map_err(|error| contract(context, error))
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SceneFilmIntent {
    pub scene_id: Uuid,
    pub role: NarrativeRole,
    pub archetype: Archetype,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FilmBuildOptions {
    pub frame_rate: Rate,
    pub font_family: String,
    pub mono_font_family: String,
    pub scene_intents: Vec<SceneFilmIntent>,
}

impl FilmBuildOptions {
    fn intent_map(&self) -> NativeResult<HashMap<Uuid, &SceneFilmIntent>> {
        self.frame_rate
            .validate()
            .map_err(|error| contract("Film frame rate is invalid", error))?;
        if self.font_family.trim().is_empty() || self.mono_font_family.trim().is_empty() {
            return Err(invalid("Film fonts must be explicit"));
        }
        if self.font_family != MOTION_CANVAS_FONT_FAMILY
            || self.mono_font_family != MOTION_CANVAS_MONO_FONT_FAMILY
        {
            return Err(unsupported(format!(
                "Pinned Motion Canvas production requires bundled fonts {MOTION_CANVAS_FONT_FAMILY:?} and {MOTION_CANVAS_MONO_FONT_FAMILY:?}"
            )));
        }
        let mut map = HashMap::new();
        for intent in &self.scene_intents {
            if map.insert(intent.scene_id, intent).is_some() {
                return Err(invalid("Duplicate scene Film intent"));
            }
        }
        Ok(map)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct MotionCanvasSegment {
    pub id: String,
    pub global_start: Rational,
    pub duration: Rational,
    pub frame_count: u64,
    pub scene_ids: Vec<Uuid>,
    pub film: Film,
}

fn segment_end(scene: &Scene) -> NativeResult<RationalTime> {
    add(scene.start, scene.duration, "Scene end overflow")
}

fn max_film_duration() -> NativeResult<RationalTime> {
    RationalTime::new(MAX_FILM_SECONDS, 1)
        .map_err(|error| contract("Canonical Film duration bound is invalid", error))
}

fn variant_scene_sequence(
    project: &Project,
    profile: &DeliverableProfile,
) -> NativeResult<Vec<Scene>> {
    let source = if profile.included_scene_ids.is_empty() {
        project.scenes.iter().collect::<Vec<_>>()
    } else {
        profile
            .included_scene_ids
            .iter()
            .map(|scene_id| {
                project
                    .scenes
                    .iter()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or_else(|| invalid("Deliverable cut references an unknown scene"))
            })
            .collect::<NativeResult<Vec<_>>>()?
    };
    let mut cursor = RationalTime::ZERO;
    let mut output = Vec::with_capacity(source.len());
    for scene in source {
        let mut projected = responsive_scene(project, scene, profile)?;
        projected.start = cursor;
        cursor = add(cursor, projected.duration, "Variant scene timing overflow")?;
        output.push(projected);
    }
    Ok(output)
}

fn flush_run(runs: &mut Vec<Vec<Scene>>, current: &mut Vec<Scene>) {
    if !current.is_empty() {
        runs.push(std::mem::take(current));
    }
}

fn scene_span_cost(scene: &Scene) -> usize {
    // One additional bounded temporal span per admitted motion node. Count
    // conservatively even for unsupported curves, which fail separately.
    1 + scene.beats.len().max(1)
        + scene
            .nodes
            .iter()
            .filter(|node| !node.keyframes.is_empty())
            .count()
}

fn motion_canvas_runs(
    project: &Project,
    profile: &DeliverableProfile,
) -> NativeResult<Vec<Vec<Scene>>> {
    let sequence = variant_scene_sequence(project, profile)?;
    let mut runs = Vec::new();
    let mut current = Vec::new();
    let max_duration = max_film_duration()?;

    for scene in sequence {
        let end = segment_end(&scene)?;
        if scene.renderer != RendererKind::MotionCanvas {
            flush_run(&mut runs, &mut current);
            continue;
        }
        if scene.duration > max_duration {
            return Err(unsupported(format!(
                "Motion Canvas scene {} exceeds the 600 second canonical Film bound",
                scene.id
            )));
        }

        let starts_new = if let Some(first) = current.first() {
            let last_end = segment_end(current.last().expect("run is nonempty"))?;
            let candidate_duration = sub(end, first.start, "Film segment duration underflow")?;
            let span_cost = current.iter().map(scene_span_cost).sum::<usize>();
            scene.start != last_end
                || candidate_duration > max_duration
                || current.len() >= MAX_SEQUENCES
                || span_cost + scene_span_cost(&scene) > MAX_TEMPORAL_SPANS
        } else {
            false
        };
        if starts_new {
            flush_run(&mut runs, &mut current);
        }
        current.push(scene);
    }
    flush_run(&mut runs, &mut current);
    Ok(runs)
}

fn layer_id(order: i32) -> String {
    if order < 0 {
        format!("layer-n{}", order.unsigned_abs())
    } else {
        format!("layer-p{}", order as u32)
    }
}

#[derive(Debug, Clone, Copy)]
struct LayoutProjection {
    position_scale_x: f64,
    position_scale_y: f64,
    object_scale: f64,
    offset_x: f64,
    offset_y: f64,
    enforce_safe_area: bool,
}

fn layout_projection(profile: &DeliverableProfile) -> LayoutProjection {
    let output_width = f64::from(profile.width);
    let output_height = f64::from(profile.height);
    match profile.framing_strategy {
        FramingStrategy::Replan => {
            let scale_x = output_width / CANVAS_WIDTH;
            let scale_y = output_height / CANVAS_HEIGHT;
            LayoutProjection {
                position_scale_x: scale_x,
                position_scale_y: scale_y,
                object_scale: scale_x.min(scale_y),
                offset_x: 0.0,
                offset_y: 0.0,
                enforce_safe_area: true,
            }
        }
        FramingStrategy::Crop => {
            let scale = (output_width / CANVAS_WIDTH).max(output_height / CANVAS_HEIGHT);
            LayoutProjection {
                position_scale_x: scale,
                position_scale_y: scale,
                object_scale: scale,
                offset_x: (output_width - CANVAS_WIDTH * scale) / 2.0,
                offset_y: (output_height - CANVAS_HEIGHT * scale) / 2.0,
                enforce_safe_area: false,
            }
        }
    }
}

fn canonical_position(
    node: &CanvasNode,
    projection: LayoutProjection,
    output: &OutputProfile,
) -> Point {
    let center_x = (node.x + node.width / 2.0) * projection.position_scale_x + projection.offset_x;
    let center_y = (node.y + node.height / 2.0) * projection.position_scale_y + projection.offset_y;
    Point {
        x: center_x - f64::from(output.width) / 2.0,
        y: center_y - f64::from(output.height) / 2.0,
    }
}

/// Apply the exact scene-specific static camera translation once to the
/// native layout. Both the baseline node and a motion start pose must pass
/// the same transformed output frame/safe-area validation.
fn scene_layout_projection(scene: &Scene, mut projection: LayoutProjection) -> LayoutProjection {
    projection.offset_x +=
        (CANVAS_WIDTH / 2.0 - scene.camera.center_x) * projection.position_scale_x;
    projection.offset_y +=
        (CANVAS_HEIGHT / 2.0 - scene.camera.center_y) * projection.position_scale_y;
    projection
}

#[derive(Debug, Clone, Copy)]
struct NativeLinearPositionMotion {
    duration: RationalTime,
    start_x: f64,
    start_y: f64,
}

/// A deliberately narrow *exact* mapping to Semwright Primitive::Settle:
/// paired X/Y at scene-local 0, then paired X/Y back to the unchanged base
/// position at an exact output-frame boundary, all linear, single unparented
/// object, no other keyframe channels/scene beats.
fn native_linear_position_motion(
    node: &CanvasNode,
    scene: &Scene,
    frame_rate: Rate,
) -> NativeResult<Option<NativeLinearPositionMotion>> {
    if node.keyframes.is_empty() {
        return Ok(None);
    }
    if node
        .keyframes
        .iter()
        .any(|key| key.property == MotionProperty::Opacity)
    {
        entry_motion(node)?;
        return Ok(None);
    }
    let fail = || {
        unsupported(format!(
            "Canvas node {} has motion keyframes outside the exact native linear X/Y settle subset",
            node.id
        ))
    };
    if !scene.beats.is_empty()
        || node.parent_id.is_some()
        || node.kind == "group"
        || scene
            .nodes
            .iter()
            .any(|candidate| candidate.parent_id == Some(node.id))
        || node.keyframes.len() != 4
    {
        return Err(fail());
    }
    let mut xs = node
        .keyframes
        .iter()
        .filter(|keyframe| keyframe.property == MotionProperty::X)
        .collect::<Vec<_>>();
    let mut ys = node
        .keyframes
        .iter()
        .filter(|keyframe| keyframe.property == MotionProperty::Y)
        .collect::<Vec<_>>();
    if xs.len() != 2 || ys.len() != 2 {
        return Err(fail());
    }
    xs.sort_by_key(|keyframe| keyframe.at);
    ys.sort_by_key(|keyframe| keyframe.at);
    if xs[0].at != RationalTime::ZERO
        || ys[0].at != RationalTime::ZERO
        || xs[1].at != ys[1].at
        || xs[1].at <= RationalTime::ZERO
        || xs[1].at >= scene.duration
        || xs[1].value != node.x
        || ys[1].value != node.y
        || node
            .keyframes
            .iter()
            .any(|keyframe| keyframe.interpolation != MotionInterpolation::Linear)
    {
        return Err(fail());
    }
    // Unlike CSS-preview time in floating point, this exact rational check
    // rejects timestamps that would need silent frame quantization.
    let numerator = i128::from(xs[1].at.num)
        .checked_mul(i128::from(frame_rate.num))
        .ok_or_else(&fail)?;
    let denominator = i128::from(xs[1].at.den)
        .checked_mul(i128::from(frame_rate.den))
        .ok_or_else(&fail)?;
    if denominator <= 0 || numerator <= 0 || numerator % denominator != 0 {
        return Err(fail());
    }
    Ok(Some(NativeLinearPositionMotion {
        duration: xs[1].at,
        start_x: xs[0].value,
        start_y: ys[0].value,
    }))
}

fn subject_id(node_id: Uuid, beat_scope: Option<Uuid>) -> String {
    match beat_scope {
        Some(beat_id) => format!("node-{}-beat-{}", node_id.simple(), beat_id.simple()),
        None => uid("node", node_id),
    }
}

fn validate_static_safe_area(
    node: &CanvasNode,
    projection: LayoutProjection,
    output: &OutputProfile,
) -> NativeResult<()> {
    if !projection.enforce_safe_area {
        return Ok(());
    }
    let center_x = (node.x + node.width / 2.0) * projection.position_scale_x + projection.offset_x;
    let center_y = (node.y + node.height / 2.0) * projection.position_scale_y + projection.offset_y;
    let width = node.width * projection.object_scale;
    let height = node.height * projection.object_scale;
    let left = center_x - width / 2.0;
    let top = center_y - height / 2.0;
    let right = center_x + width / 2.0;
    let bottom = center_y + height / 2.0;
    let epsilon = 1e-6;
    if left + epsilon < output.safe_area.left
        || top + epsilon < output.safe_area.top
        || right - epsilon > f64::from(output.width) - output.safe_area.right
        || bottom - epsilon > f64::from(output.height) - output.safe_area.bottom
    {
        return Err(unsupported(format!(
            "Canvas node {} exceeds the deterministic Motionwright safe area after variant replan",
            node.id
        )));
    }
    Ok(())
}

fn validate_node_projection(node: &CanvasNode, font_family: &str) -> NativeResult<()> {
    // Keyframes are checked by native_position_motion() at Shot construction,
    // with exact timing/interpolation/position constraints before realization.
    if node.coordinate_space != CoordinateSpace::ProjectPixels {
        return Err(unsupported(format!(
            "Canvas node {} uses a coordinate space not yet representable by canonical Film",
            node.id
        )));
    }
    if node.rotation_deg != 0.0 || node.opacity != 1.0 {
        return Err(unsupported(format!(
            "Canvas node {} has rotation/opacity state that canonical Film cannot preserve yet",
            node.id
        )));
    }
    if node.style.blend_mode != BlendMode::Normal {
        return Err(unsupported(format!(
            "Canvas node {} uses a blend mode that canonical Film cannot preserve yet",
            node.id
        )));
    }
    if !node.relations.is_empty() {
        return Err(unsupported(format!(
            "Canvas node {} has semantic relations that require a dedicated Film constraint mapping",
            node.id
        )));
    }
    if node.kind == "text"
        && node
            .style
            .font_family
            .as_deref()
            .is_some_and(|family| family != font_family)
    {
        return Err(unsupported(format!(
            "Canvas node {} uses a font family different from the explicit Film font",
            node.id
        )));
    }
    Ok(())
}

fn text_styles(
    scenes: &[&Scene],
    font_family: &str,
    object_scale: f64,
) -> NativeResult<(BTreeMap<u64, String>, BTreeMap<String, f64>)> {
    let mut sizes = BTreeSet::new();
    for scene in scenes {
        for node in &scene.nodes {
            validate_node_projection(node, font_family)?;
            if node.kind == "text" {
                let size = node.style.font_size.ok_or_else(|| {
                    invalid(format!("Text node {} has no explicit font size", node.id))
                })?;
                if !size.is_finite() || !(4.0..=512.0).contains(&size) {
                    return Err(unsupported(format!(
                        "Text node {} font size is outside canonical Film bounds",
                        node.id
                    )));
                }
                sizes.insert(size.to_bits());
            }
        }
    }
    if sizes.len() > 32 {
        return Err(unsupported(
            "Motion Canvas segment has more than 32 distinct text sizes",
        ));
    }

    let mut by_bits = BTreeMap::new();
    let mut type_scale = BTreeMap::new();
    let mut ordered = sizes.into_iter().map(f64::from_bits).collect::<Vec<_>>();
    ordered.sort_by(f64::total_cmp);
    for (index, size) in ordered.into_iter().enumerate() {
        let id = format!("text-{index}");
        by_bits.insert(size.to_bits(), id.clone());
        type_scale.insert(id, size * object_scale);
    }
    if type_scale.is_empty() {
        type_scale.insert("body".into(), 16.0);
    }
    Ok((by_bits, type_scale))
}

fn global_stroke(scenes: &[&Scene], object_scale: f64) -> NativeResult<f64> {
    let mut stroke: Option<f64> = None;
    for scene in scenes {
        for node in &scene.nodes {
            if node.kind == "shape" || node.kind == "rectangle" || node.kind == "circle" {
                let value = node.style.stroke_width;
                if value > 0.0 {
                    if value > 64.0 {
                        return Err(unsupported(format!(
                            "Canvas node {} stroke width exceeds canonical Film bounds",
                            node.id
                        )));
                    }
                    if stroke.is_some_and(|existing| existing != value) {
                        return Err(unsupported(
                            "Canonical Film currently requires one shared nonzero stroke width per segment",
                        ));
                    }
                    stroke = Some(value);
                }
            }
        }
    }
    Ok(stroke.unwrap_or(0.0) * object_scale)
}

fn layers(scene: &Scene) -> NativeResult<(Vec<Layer>, BTreeMap<i32, String>)> {
    let orders = scene
        .nodes
        .iter()
        .map(|node| node.z_index)
        .collect::<BTreeSet<_>>();
    if orders.len() > 32 {
        return Err(unsupported(
            "Canonical Film supports at most 32 layer orders per shot",
        ));
    }
    let mut names = BTreeMap::new();
    let output = orders
        .into_iter()
        .map(|order| {
            let id = layer_id(order);
            names.insert(order, id.clone());
            Layer {
                id,
                order,
                intentional_overlay: true,
            }
        })
        .collect();
    Ok((output, names))
}

struct SubjectProjectionContext<'a> {
    text_style: &'a BTreeMap<u64, String>,
    profile: &'a DeliverableProfile,
    projection: LayoutProjection,
    output: &'a OutputProfile,
    font_family: &'a str,
}

fn subject(
    node: &CanvasNode,
    layer_names: &BTreeMap<i32, String>,
    context: &SubjectProjectionContext<'_>,
    beat_scope: Option<Uuid>,
) -> NativeResult<(Subject, Vec<VisualConstraint>)> {
    let text_style = context.text_style;
    let profile_language = context.profile.language.as_str();
    let projection = context.projection;
    let output = context.output;
    let font_family = context.font_family;
    validate_node_projection(node, font_family)?;
    validate_static_safe_area(node, projection, output)?;
    let id = subject_id(node.id, beat_scope);
    let layer = layer_names
        .get(&node.z_index)
        .cloned()
        .ok_or_else(|| invalid("Canvas node layer projection is missing"))?;
    let content = match node.kind.as_str() {
        "text" => {
            let text = context
                .profile
                .text_overrides
                .get(&node.id)
                .or(node.text.as_ref())
                .filter(|text| !text.is_empty())
                .ok_or_else(|| invalid(format!("Text node {} has no text", node.id)))?;
            if text.len() > 16_384 {
                return Err(unsupported(format!(
                    "Text node {} exceeds the canonical Film text budget",
                    node.id
                )));
            }
            let size = node
                .style
                .font_size
                .ok_or_else(|| invalid(format!("Text node {} has no font size", node.id)))?;
            let style = text_style
                .get(&size.to_bits())
                .cloned()
                .ok_or_else(|| invalid("Text style projection is missing"))?;
            let weight = node.style.font_weight.unwrap_or(400);
            if ![400, 500, 600, 700].contains(&weight) {
                return Err(unsupported(format!(
                    "Text node {} font weight has no exact face evidence in the pinned native runtime; author an explicit 400, 500, 600 or 700 weight instead of silently approximating it",
                    node.id
                )));
            }
            SubjectContent::Text {
                runs: vec![TextRun {
                    text: text.clone(),
                    weight,
                    color: node.style.fill.clone(),
                    emphasis: false,
                }],
                style,
                direction: TextDirection::Auto,
                language: profile_language.to_owned(),
                wrap: true,
                truncate: false,
            }
        }
        "shape" | "rectangle" => SubjectContent::Rectangle {
            fill: node.style.fill.clone().ok_or_else(|| {
                invalid(format!("Shape node {} requires an explicit fill", node.id))
            })?,
            stroke: node.style.stroke.clone(),
            radius: 0.0,
        },
        "circle" => SubjectContent::Circle {
            fill: node.style.fill.clone().ok_or_else(|| {
                invalid(format!("Circle node {} requires an explicit fill", node.id))
            })?,
            stroke: node.style.stroke.clone(),
        },
        "group" => SubjectContent::Group,
        other => {
            return Err(unsupported(format!(
                "Canvas node kind {other:?} has no canonical Film projection yet"
            )));
        }
    };
    // Semwright 1.0.0 deliberately does not guarantee native transformed-bounds
    // evidence for every frame. Motionwright therefore validates safe-area geometry
    // deterministically before projection and reserves native verification for
    // renderer-observable text, font, truncation, cue and lifecycle invariants.
    let mut constraints = Vec::new();
    if matches!(content, SubjectContent::Text { .. }) {
        constraints.extend([
            VisualConstraint::NativeText {
                subject: id.clone(),
            },
            VisualConstraint::FontLoaded {
                subject: id.clone(),
            },
            VisualConstraint::NoTruncation {
                subject: id.clone(),
            },
        ]);
    }

    Ok((
        Subject {
            id,
            role: node.kind.clone(),
            parent: node.parent_id.map(|id| subject_id(id, beat_scope)),
            layer,
            content,
            layout: SpatialIntent::Fixed {
                position: canonical_position(node, projection, output),
                size: Size {
                    width: node.width * projection.object_scale,
                    height: node.height * projection.object_scale,
                },
            },
            initially_visible: !node
                .keyframes
                .iter()
                .any(|key| key.property == MotionProperty::Opacity),
            clip_intentional: false,
        },
        constraints,
    ))
}

fn aspect(width: u32, height: u32) -> AspectFamily {
    match width.cmp(&height) {
        std::cmp::Ordering::Greater => AspectFamily::Landscape,
        std::cmp::Ordering::Less => AspectFamily::Portrait,
        std::cmp::Ordering::Equal => AspectFamily::Square,
    }
}

fn output_profile(
    profile: &DeliverableProfile,
    frame_rate: Rate,
    scenes: &[&Scene],
) -> NativeResult<(OutputProfile, LayoutProjection)> {
    if i64::from(frame_rate.num) != profile.frame_rate.num
        || i64::from(frame_rate.den) != profile.frame_rate.den
    {
        return Err(invalid(
            "Film frame rate must match the versioned deliverable profile",
        ));
    }
    let projection = layout_projection(profile);
    let safe_margin = scenes
        .iter()
        .map(|scene| scene.camera.safe_margin)
        .fold(0.0_f64, f64::max);
    for scene in scenes {
        // A static camera translation can be represented exactly by a
        // per-scene position offset on every native subject. Zoom/rotation
        // require transformed bounds and glyph geometry, which the pinned
        // Film contract cannot guarantee and therefore remain unsupported.
        if scene.camera.zoom != 1.0 || scene.camera.rotation_deg != 0.0 {
            return Err(unsupported(format!(
                "Scene {} uses camera zoom or rotation that canonical Film cannot preserve yet",
                scene.id
            )));
        }
    }
    Ok((
        OutputProfile {
            width: profile.width,
            height: profile.height,
            frame_rate,
            aspect: aspect(profile.width, profile.height),
            safe_area: Insets {
                top: f64::from(profile.height) * safe_margin,
                right: f64::from(profile.width) * safe_margin,
                bottom: f64::from(profile.height) * safe_margin,
                left: f64::from(profile.width) * safe_margin,
            },
        },
        projection,
    ))
}

fn visual_token(project: &Project, names: &[&str], purpose: &str) -> NativeResult<String> {
    project
        .visual_language
        .palette
        .iter()
        .find(|token| names.iter().any(|name| token.name.eq_ignore_ascii_case(name)))
        .map(|token| token.value.clone())
        .ok_or_else(|| {
            invalid(format!(
                "Visual language requires an explicit {purpose} palette token ({}) before native Motion Canvas production",
                names.join(" or ")
            ))
        })
}

/// A Native Motion Canvas Film is a visual composition. Include only media
/// that an actual authored visual subject references. Automatically copying
/// every Project/CAS asset into Film is incorrect: measured VO, music, fonts,
/// and unused binary imports belong to other subsystems, and have not been
/// registered with the Motion Canvas managed-asset registry. An unrelated
/// WAV asset caused a real driver composition-plan rejection in native CI.
/// Preserve true digest-bound image/video asset references and fail closed.
fn assets(project: &Project, sequences: &[Sequence]) -> NativeResult<Vec<AssetRef>> {
    let referenced = sequences
        .iter()
        .flat_map(|sequence| &sequence.beats)
        .flat_map(|beat| &beat.shots)
        .flat_map(|shot| &shot.subjects)
        .filter_map(|subject| match &subject.content {
            SubjectContent::Image { asset_id, .. } | SubjectContent::Video { asset_id, .. } => {
                Some(asset_id.clone())
            }
            _ => None,
        })
        .collect::<BTreeSet<_>>();
    if referenced.len() > 128 {
        return Err(unsupported(
            "Canonical Film supports at most 128 referenced digest-bound visual assets",
        ));
    }
    referenced
        .iter()
        .map(|reference| {
            let asset = project
                .assets
                .iter()
                .find(|asset| uid("asset", asset.id) == *reference)
                .ok_or_else(|| {
                    unsupported("Authored visual subject has no matching project media asset")
                })?;
            let digest = asset
                .content_sha256
                .clone()
                .ok_or_else(|| unsupported("Referenced visual asset has no content digest"))?;
            let sha256 = Digest::parse(digest)
                .map_err(|error| contract("Referenced visual asset digest is invalid", error))?;
            Ok(AssetRef {
                id: reference.clone(),
                sha256,
                media_type: asset.media_type.clone(),
                provenance: asset.source_revision.clone(),
                license: None,
            })
        })
        .collect()
}

struct ShotProjectionContext<'a> {
    component_texts: &'a BTreeSet<Uuid>,
    profile: &'a DeliverableProfile,
    projection: LayoutProjection,
    output: &'a OutputProfile,
    text_style: &'a BTreeMap<u64, String>,
    font_family: &'a str,
}

struct ShotIdentity {
    archetype: Archetype,
    shot_id: String,
    span_id: String,
    beat_scope: Option<Uuid>,
}

fn projected_shot(
    scene: &Scene,
    context: &ShotProjectionContext<'_>,
    identity: ShotIdentity,
) -> NativeResult<Shot> {
    let (layers, layer_names) = layers(scene)?;
    let mut subjects = Vec::with_capacity(scene.nodes.len());
    let mut visual_constraints = Vec::new();
    // Pan is a pure translation of all authored subjects before output
    // replan/crop. Applying it to the existing fixed-layout projection keeps
    // text size, stroke, z-order, and canonical safe-area validation intact.
    let projection = scene_layout_projection(scene, context.projection);
    let subject_context = SubjectProjectionContext {
        text_style: context.text_style,
        profile: context.profile,
        projection,
        output: context.output,
        font_family: context.font_family,
    };
    for node in &scene.nodes {
        let (mapped, mut node_constraints) =
            subject(node, &layer_names, &subject_context, identity.beat_scope)?;
        if node.kind == "text" && context.component_texts.contains(&node.id) {
            let (mut lines, mut constraints) =
                fixed_component_text(node, mapped, projection.object_scale)?;
            subjects.append(&mut lines);
            visual_constraints.append(&mut constraints);
        } else {
            subjects.push(mapped);
            visual_constraints.append(&mut node_constraints);
        }
        if subjects.len() > 512 {
            return Err(unsupported(
                "Component text realization exceeds the native subject budget",
            ));
        }
    }
    Ok(Shot {
        id: identity.shot_id,
        span_id: identity.span_id,
        archetype: identity.archetype,
        subjects,
        layers,
        annotations: vec![],
        captions: vec![],
        motion: vec![],
        constraints: visual_constraints,
    })
}

fn attach_entrance_motion(
    shot: &mut Shot,
    scene: &Scene,
    scene_start: RationalTime,
    projection: LayoutProjection,
    spans: &mut Vec<TemporalSpan>,
) -> NativeResult<()> {
    for node in &scene.nodes {
        if !node
            .keyframes
            .iter()
            .any(|key| key.property == MotionProperty::Opacity)
        {
            continue;
        }
        if node.kind == "group"
            || scene
                .nodes
                .iter()
                .any(|child| child.parent_id == Some(node.id))
        {
            return Err(unsupported(
                "Authored entrance motion on source hierarchies requires a dedicated mapping",
            ));
        }
        let Some(entry) = entry_motion(node)? else {
            continue;
        };
        let target = subject_id(node.id, None);
        let span_id = format!("entrance-{}", node.id.simple());
        let duration = sub(entry.end, entry.start, "Entrance duration underflow")?;
        spans.push(TemporalSpan {
            id: span_id.clone(),
            minimum: duration,
            preferred: duration,
            maximum: duration,
            anchor: StartAnchor::Absolute {
                time: add(scene_start, entry.start, "Entrance start overflow")?,
            },
            preference_priority: 0,
        });
        let offset = Point {
            x: 0.0,
            y: entry.offset_y * projection.position_scale_y,
        };
        if entry.rotation_deg == 0.0 {
            shot.motion.push(Invocation {
                id: format!("slide-{}", node.id.simple()),
                span_id,
                easing: MotionEasing::OutCubic,
                primitive: Primitive::SlideIn { target, offset },
            });
        } else {
            shot.motion.push(Invocation {
                id: format!("settle-{}", node.id.simple()),
                span_id: span_id.clone(),
                easing: MotionEasing::OutCubic,
                primitive: Primitive::Settle {
                    target: target.clone(),
                    offset,
                    rotation: entry.rotation_deg,
                },
            });
            shot.motion.push(Invocation {
                id: format!("fade-{}", node.id.simple()),
                span_id,
                easing: MotionEasing::OutCubic,
                primitive: Primitive::FadeIn { target },
            });
        }
    }
    Ok(())
}

fn authored_beats_tile_scene(scene: &Scene) -> NativeResult<()> {
    if !scene.beats.is_empty() && scene.nodes.iter().any(|node| !node.keyframes.is_empty()) {
        return Err(unsupported(
            "Authored motion keyframes across multiple beat scopes need an explicit continuity mapping; motion is not restarted silently",
        ));
    }
    if scene.beats.is_empty() {
        return Ok(());
    }
    let mut cursor = RationalTime::ZERO;
    for beat in &scene.beats {
        if beat.start != cursor {
            return Err(unsupported(format!(
                "Scene {} authored beats must tile the scene timeline exactly before native Film projection",
                scene.id
            )));
        }
        cursor = add(beat.start, beat.duration, "Scene beat end overflow")?;
    }
    if cursor != scene.duration {
        return Err(unsupported(format!(
            "Scene {} authored beats must cover the full scene duration before native Film projection",
            scene.id
        )));
    }
    Ok(())
}

fn build_segment(
    project: &Project,
    scenes: &[Scene],
    segment_index: usize,
    profile: &DeliverableProfile,
    options: &FilmBuildOptions,
    intents: &HashMap<Uuid, &SceneFilmIntent>,
) -> NativeResult<MotionCanvasSegment> {
    let first = scenes
        .first()
        .ok_or_else(|| invalid("Cannot build an empty Motion Canvas segment"))?;
    let last = scenes.last().expect("segment is nonempty");
    let start = first.start;
    let end = segment_end(last)?;
    let duration = sub(end, start, "Film segment duration underflow")?;
    let scene_refs = scenes.iter().collect::<Vec<_>>();
    let (output, projection) = output_profile(profile, options.frame_rate, &scene_refs)?;
    let (text_style, type_scale) =
        text_styles(&scene_refs, &options.font_family, projection.object_scale)?;
    let stroke = global_stroke(&scene_refs, projection.object_scale)?;
    let component_texts = project
        .production_design
        .heroes
        .iter()
        .flat_map(|hero| hero.baseline.iter().map(|node| node.id))
        .collect::<BTreeSet<_>>();
    let shot_context = ShotProjectionContext {
        component_texts: &component_texts,
        profile,
        projection,
        output: &output,
        text_style: &text_style,
        font_family: &options.font_family,
    };

    let span_capacity = scenes.iter().map(scene_span_cost).sum::<usize>();
    if span_capacity > MAX_TEMPORAL_SPANS {
        return Err(unsupported(
            "Canonical Film segment exceeds 128 bounded temporal spans including keyframe motion",
        ));
    }
    let mut spans = Vec::with_capacity(span_capacity);
    let mut constraints = Vec::with_capacity(span_capacity.saturating_sub(scenes.len()));
    let mut sequences = Vec::with_capacity(scenes.len());

    for scene in scenes {
        if scene.nodes.is_empty() {
            return Err(unsupported(format!(
                "Scene {} has no semantic canvas objects to render",
                scene.id
            )));
        }
        authored_beats_tile_scene(scene)?;
        if !scene.beats.is_empty() && scene.nodes.iter().any(|node| !node.keyframes.is_empty()) {
            return Err(unsupported(format!(
                "Scene {} authored beat spans cannot preserve Canvas motion keyframes yet",
                scene.id
            )));
        }
        let intent = intents.get(&scene.id).ok_or_else(|| {
            invalid(format!(
                "Scene {} requires explicit narrative role and archetype for canonical Film",
                scene.id
            ))
        })?;
        let local_start = sub(scene.start, start, "Scene local Film start underflow")?;
        let sequence_span = uid("seqspan", scene.id);
        spans.push(TemporalSpan {
            id: sequence_span.clone(),
            minimum: scene.duration,
            preferred: scene.duration,
            maximum: scene.duration,
            anchor: StartAnchor::Absolute { time: local_start },
            preference_priority: 0,
        });

        let mut authoring_beats = Vec::with_capacity(scene.beats.len().max(1));
        if scene.beats.is_empty() {
            let shot_span = uid("shotspan", scene.id);
            spans.push(TemporalSpan {
                id: shot_span.clone(),
                minimum: scene.duration,
                preferred: scene.duration,
                maximum: scene.duration,
                anchor: StartAnchor::Absolute { time: local_start },
                preference_priority: 0,
            });
            constraints.push(TemporalConstraint::Contains {
                id: uid("contains", scene.id),
                parent: sequence_span.clone(),
                child: shot_span.clone(),
            });
            let mut shot = projected_shot(
                scene,
                &shot_context,
                ShotIdentity {
                    archetype: intent.archetype,
                    shot_id: uid("shot", scene.id),
                    span_id: shot_span.clone(),
                    beat_scope: None,
                },
            )?;
            let source_projection = scene_layout_projection(scene, projection);
            for node in &scene.nodes {
                let Some(motion) = native_linear_position_motion(node, scene, options.frame_rate)?
                else {
                    continue;
                };
                let mut initial = node.clone();
                initial.x = motion.start_x;
                initial.y = motion.start_y;
                // For Replan, both endpoints are safe; the intervening
                // linear convex path is therefore safe as well.
                validate_static_safe_area(&initial, source_projection, &output)?;
                let motion_span = format!("keyspan-{}-{}", scene.id.simple(), node.id.simple());
                spans.push(TemporalSpan {
                    id: motion_span.clone(),
                    minimum: motion.duration,
                    preferred: motion.duration,
                    maximum: motion.duration,
                    anchor: StartAnchor::Absolute { time: local_start },
                    preference_priority: 0,
                });
                constraints.push(TemporalConstraint::Contains {
                    id: format!("key-contains-{}", node.id.simple()),
                    parent: shot_span.clone(),
                    child: motion_span.clone(),
                });
                shot.motion.push(Invocation {
                    id: format!("key-motion-{}", node.id.simple()),
                    span_id: motion_span,
                    easing: MotionEasing::Linear,
                    primitive: Primitive::Settle {
                        target: subject_id(node.id, None),
                        offset: Point {
                            x: (motion.start_x - node.x) * source_projection.position_scale_x,
                            y: (motion.start_y - node.y) * source_projection.position_scale_y,
                        },
                        rotation: 0.0,
                    },
                });
            }
            attach_entrance_motion(&mut shot, scene, local_start, projection, &mut spans)?;
            authoring_beats.push(AuthoringBeat {
                id: uid("beat", scene.id),
                role: intent.role,
                shots: vec![shot],
            });
        } else {
            for beat in &scene.beats {
                let beat_start = add(local_start, beat.start, "Scene beat local start overflow")?;
                let shot_span = format!("shotspan-{}-beat-{}", scene.id.simple(), beat.id.simple());
                spans.push(TemporalSpan {
                    id: shot_span.clone(),
                    minimum: beat.duration,
                    preferred: beat.duration,
                    maximum: beat.duration,
                    anchor: StartAnchor::Absolute { time: beat_start },
                    preference_priority: 0,
                });
                constraints.push(TemporalConstraint::Contains {
                    id: format!("contains-{}-beat-{}", scene.id.simple(), beat.id.simple()),
                    parent: sequence_span.clone(),
                    child: shot_span.clone(),
                });
                let shot = projected_shot(
                    scene,
                    &shot_context,
                    ShotIdentity {
                        archetype: intent.archetype,
                        shot_id: format!("shot-{}-beat-{}", scene.id.simple(), beat.id.simple()),
                        span_id: shot_span,
                        beat_scope: Some(beat.id),
                    },
                )?;
                authoring_beats.push(AuthoringBeat {
                    id: format!("beat-{}-{}", scene.id.simple(), beat.id.simple()),
                    role: intent.role,
                    shots: vec![shot],
                });
            }
        }

        sequences.push(Sequence {
            id: uid("scene", scene.id),
            span_id: sequence_span,
            beats: authoring_beats,
        });
    }

    // Semwright's native authoring runtime can time many source-bound shots
    // within one Motion Canvas scene: each shot root is shown only over its
    // exact rational start/end interval. Mapping *every* tiny editorial scene
    // into a separate Motion Canvas scene made the native scene-playback
    // transition path discard frames (issue #81; real 2/32 and 85/96 evidence).
    // Keep every editorial shot/beat and its original per-scene timing span;
    // only group contiguous native scene transitions behind one run envelope.
    // This is not flattening into pixels or changing the measured frame count.
    let sequences = if sequences.len() > 1 {
        if spans.len() >= MAX_TEMPORAL_SPANS {
            return Err(unsupported(
                "A multi-scene native Film requires one more bounded run span",
            ));
        }
        let run_span = format!("runspan-{segment_index}");
        spans.push(TemporalSpan {
            id: run_span.clone(),
            minimum: duration,
            preferred: duration,
            maximum: duration,
            anchor: StartAnchor::Absolute {
                time: RationalTime::ZERO,
            },
            preference_priority: 0,
        });
        let mut ordered_beats = Vec::new();
        for sequence in sequences {
            // Retain each source scene span as a real constrained interval.
            constraints.push(TemporalConstraint::Contains {
                id: format!("run-contains-{}", sequence.id),
                parent: run_span.clone(),
                child: sequence.span_id,
            });
            ordered_beats.extend(sequence.beats);
        }
        vec![Sequence {
            id: format!("mw-run-{segment_index}"),
            span_id: run_span,
            beats: ordered_beats,
        }]
    } else {
        sequences
    };

    let segment_assets = assets(project, &sequences)?;
    let film = Film {
        version: AUTHORING_VERSION,
        id: format!("mw-{}-{segment_index}", project.id.simple()),
        output,
        editorial: EditorialSystem {
            version: 1,
            font: FontSpec {
                family: options.font_family.clone(),
                asset_digest: None,
                fallback: FontFallback::AllowAndReport,
                permitted_fallbacks: vec![],
            },
            mono_font: FontSpec {
                family: options.mono_font_family.clone(),
                asset_digest: None,
                fallback: FontFallback::AllowAndReport,
                permitted_fallbacks: vec![],
            },
            type_scale,
            colors: BTreeMap::from([
                (
                    "text".into(),
                    visual_token(project, &["text", "ink"], "text/ink")?,
                ),
                (
                    "background".into(),
                    visual_token(project, &["background", "surface"], "background/surface")?,
                ),
            ]),
            spacing: BTreeMap::new(),
            stroke,
            corner_radius: 0.0,
        },
        timing: TemporalGraph {
            version: 1,
            duration,
            spans,
            constraints,
        },
        sequences,
        cues: CueGraph {
            version: 1,
            cues: vec![],
        },
        assets: segment_assets,
    };
    let realization = realize(&film)
        .map_err(|error| contract("Canonical Motion Canvas Film realization failed", error))?;
    let frame_count = realization
        .schedule
        .frame_count(&film.output)
        .map_err(|error| {
            contract(
                "Canonical Film does not end on an exact frame boundary",
                error,
            )
        })?;

    Ok(MotionCanvasSegment {
        id: format!("segment-{segment_index}"),
        global_start: start,
        duration,
        frame_count,
        scene_ids: scenes.iter().map(|scene| scene.id).collect(),
        film,
    })
}

pub fn build_motion_canvas_segments(
    project: &Project,
    deliverable_id: Uuid,
    options: &FilmBuildOptions,
) -> NativeResult<Vec<MotionCanvasSegment>> {
    project
        .validate()
        .map_err(|error| contract("Motionwright project is invalid", error))?;
    let intents = options.intent_map()?;
    let deliverable = project
        .deliverables
        .iter()
        .find(|profile| profile.id == deliverable_id)
        .ok_or_else(|| invalid("Deliverable profile not found"))?;
    let runs = motion_canvas_runs(project, deliverable)?;
    runs.iter()
        .enumerate()
        .map(|(index, scenes)| {
            build_segment(project, scenes, index, deliverable, options, &intents)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        Beat as SceneBeat, CanvasKeyframe, CanvasNode, Change, MotionInterpolation, MotionProperty,
        NodeStyle, RationalTime, RendererKind,
    };
    use std::collections::BTreeSet;

    fn node(kind: &str, text: Option<&str>) -> CanvasNode {
        CanvasNode {
            id: Uuid::now_v7(),
            name: format!("{kind} fixture"),
            kind: kind.into(),
            parent_id: None,
            x: 160.0,
            y: 140.0,
            width: 640.0,
            height: 160.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: text.map(str::to_owned),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 1,
            style: NodeStyle {
                fill: Some(if kind == "text" {
                    "#F5F5F2".into()
                } else {
                    "#1D242C".into()
                }),
                stroke: if kind == "text" {
                    None
                } else {
                    Some("#5A6570".into())
                },
                stroke_width: if kind == "text" { 0.0 } else { 1.0 },
                font_family: (kind == "text").then(|| "Instrument Sans Variable".into()),
                font_size: (kind == "text").then_some(64.0),
                font_weight: (kind == "text").then_some(700),
                line_height: (kind == "text").then_some(1.05),
                blend_mode: BlendMode::Normal,
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        }
    }

    fn fixture_project() -> Project {
        let mut project = Project::new("Canonical Film fixture").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Opening".into(),
                objective: "Open".into(),
                duration_seconds: 2,
            })
            .unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Proof".into(),
                objective: "Prove".into(),
                duration_seconds: 1,
            })
            .unwrap();
        let first = project.scenes[0].id;
        let second = project.scenes[1].id;
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id: first,
                node: node("text", Some("Motionwright")),
            })
            .unwrap();
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id: second,
                node: node("shape", None),
            })
            .unwrap();
        project
    }

    #[test]
    fn unrelated_measured_voice_does_not_enter_native_visual_film_assets() {
        // Regression from the real 33-frame H264/AAC E2E: importing an actual
        // voice take to Studio CAS must never force that audio asset through
        // Motion Canvas composition.plan, whose managed visual asset registry
        // did not import it. Rendering source objects remains unchanged.
        let mut project = fixture_project();
        let master_id = project
            .deliverables
            .iter()
            .find(|profile| profile.name == "Master 16:9")
            .unwrap()
            .id;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let original = build_motion_canvas_segments(&project, master_id, &options).unwrap();
        assert!(
            original
                .iter()
                .all(|segment| segment.film.assets.is_empty())
        );
        project.assets.push(motionwright_domain::Asset {
            id: Uuid::new_v4(),
            name: "Measured 48 kHz stereo voice WAV".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: Some("actual-source-import".into()),
        });
        let with_audio = build_motion_canvas_segments(&project, master_id, &options).unwrap();
        assert_eq!(original.len(), with_audio.len());
        for (before, after) in original.iter().zip(&with_audio) {
            assert_eq!(before.film.assets, after.film.assets);
            assert!(after.film.assets.is_empty());
            assert_eq!(before.frame_count, after.frame_count);
            assert_eq!(before.film.timing, after.film.timing);
            assert_eq!(before.film.sequences, after.film.sequences);
        }
    }

    #[test]
    fn projects_contiguous_motion_canvas_scenes_into_realized_film() {
        let project = fixture_project();
        let master = project
            .deliverables
            .iter()
            .find(|profile| profile.name == "Master 16:9")
            .unwrap();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let segments = build_motion_canvas_segments(&project, master.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].scene_ids.len(), 2);
        assert_eq!(segments[0].frame_count, 90);
        assert_eq!(segments[0].film.sequences.len(), 1);
        assert_eq!(segments[0].film.sequences[0].beats.len(), 2);
        let realized = realize(&segments[0].film).unwrap();
        assert_eq!(realized.scenes.len(), 1);
        assert_eq!(realized.scenes[0].shots.len(), 2);
        for (source, beat) in project
            .scenes
            .iter()
            .zip(&segments[0].film.sequences[0].beats)
        {
            let interval = realized.schedule.interval(&beat.shots[0].span_id).unwrap();
            assert_eq!(interval.start, source.start);
            assert_eq!(
                interval.end,
                source.start.checked_add(source.duration).unwrap()
            );
        }
        assert_eq!(
            segments[0].film.editorial.font.family,
            MOTION_CANVAS_FONT_FAMILY
        );
        assert_eq!(
            segments[0].film.editorial.mono_font.family,
            MOTION_CANVAS_MONO_FONT_FAMILY
        );
        assert_eq!(segments[0].film.editorial.colors["text"], "#F2F4F3");
        assert_eq!(segments[0].film.editorial.colors["background"], "#0F1216");
        assert_eq!(
            segments[0].film.timing.duration,
            Rational::new(3, 1).unwrap()
        );
        let constraints = &segments[0].film.sequences[0].beats[0].shots[0].constraints;
        assert!(
            !constraints
                .iter()
                .any(|constraint| matches!(constraint, VisualConstraint::SafeArea { .. }))
        );
        assert!(
            constraints
                .iter()
                .any(|constraint| matches!(constraint, VisualConstraint::NativeText { .. }))
        );
        assert!(realize(&segments[0].film).is_ok());
    }

    #[test]
    fn thirty_three_one_frame_editorial_scenes_keep_exact_native_shot_intervals() {
        // Reproduces issue #81 without starting a browser or turning a failed
        // provider receipt into PASS. The heavy CI still must prove the PNG
        // observations and the exact native 32+1 MLT output.
        let mut project = Project::new("Exact-frame multi-shot Film").unwrap();
        for index in 0..33 {
            project
                .apply_change(&Change::AddScene {
                    name: format!("Frame {index:02}"),
                    objective: "Preserve the authored scene cut".into(),
                    duration_seconds: 1,
                })
                .unwrap();
            let scene_id = project.scenes.last().unwrap().id;
            project
                .apply_change(&Change::SetSceneDuration {
                    scene_id,
                    duration: RationalTime::new(1, 30).unwrap(),
                })
                .unwrap();
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: node("shape", None),
                })
                .unwrap();
        }
        let options = fixture_options(&project);
        let profile = project
            .deliverables
            .iter()
            .find(|item| item.name == "Master 16:9")
            .unwrap();
        let segments = build_motion_canvas_segments(&project, profile.id, &options).unwrap();
        assert_eq!(segments.len(), 2);
        assert_eq!(segments[0].scene_ids.len(), 32);
        assert_eq!(segments[1].scene_ids.len(), 1);
        assert_eq!(segments[0].frame_count, 32);
        assert_eq!(segments[1].frame_count, 1);
        for (segment, first_scene) in [(&segments[0], 0usize), (&segments[1], 32)] {
            // A single managed Motion Canvas Scene owns each native render.
            // Individual authored shots remain separate and exact.
            assert_eq!(segment.film.sequences.len(), 1);
            let sequence = &segment.film.sequences[0];
            assert_eq!(sequence.beats.len(), segment.scene_ids.len());
            let realized = realize(&segment.film).unwrap();
            assert_eq!(realized.scenes.len(), 1);
            assert_eq!(realized.scenes[0].shots.len(), segment.scene_ids.len());
            for (offset, beat) in sequence.beats.iter().enumerate() {
                let original = &project.scenes[first_scene + offset];
                let interval = realized.schedule.interval(&beat.shots[0].span_id).unwrap();
                let expected_start = RationalTime::new(offset as i64, 30).unwrap();
                let expected_end = RationalTime::new(offset as i64 + 1, 30).unwrap();
                assert_eq!(interval.start, expected_start);
                assert_eq!(interval.end, expected_end);
                assert_eq!(segment.scene_ids[offset], original.id);
                assert_eq!(interval.duration().unwrap(), original.duration);
            }
        }
    }

    fn fixture_options(project: &Project) -> FilmBuildOptions {
        FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        }
    }

    fn fixed_position(segment: &MotionCanvasSegment, scene_index: usize) -> Point {
        let subject = &segment.film.sequences[0].beats[scene_index].shots[0].subjects[0];
        match &subject.layout {
            SpatialIntent::Fixed { position, .. } => *position,
            other => panic!("Expected fixed Film subject after static camera pan: {other:?}"),
        }
    }

    #[test]
    fn projects_exact_native_film_static_pan_per_scene_without_changing_source() {
        let baseline = fixture_project();
        let profile_id = baseline.deliverables[0].id;
        let before =
            build_motion_canvas_segments(&baseline, profile_id, &fixture_options(&baseline))
                .unwrap();
        let baseline_a = fixed_position(&before[0], 0);
        let baseline_b = fixed_position(&before[0], 1);
        let mut panned = baseline.clone();
        panned.scenes[0].camera.center_x -= 64.0;
        panned.scenes[0].camera.center_y -= 40.0;
        panned.scenes[1].camera.center_x += 30.0;
        panned.scenes[1].camera.center_y += 20.0;
        // Film pan translates subjects, not the versioned semantic Canvas
        // object base transforms, text glyph size, frame rate or cut duration.
        let base_a = (panned.scenes[0].nodes[0].x, panned.scenes[0].nodes[0].y);
        let base_b = (panned.scenes[1].nodes[0].x, panned.scenes[1].nodes[0].y);
        let native =
            build_motion_canvas_segments(&panned, profile_id, &fixture_options(&panned)).unwrap();
        assert_eq!(native.len(), 1);
        assert_eq!(native[0].frame_count, 90);
        assert_eq!(native[0].scene_ids, before[0].scene_ids);
        let a = fixed_position(&native[0], 0);
        let b = fixed_position(&native[0], 1);
        assert!((a.x - baseline_a.x - 64.0).abs() < 1e-8);
        assert!((a.y - baseline_a.y - 40.0).abs() < 1e-8);
        assert!((b.x - baseline_b.x + 30.0).abs() < 1e-8);
        assert!((b.y - baseline_b.y + 20.0).abs() < 1e-8);
        assert_eq!(
            (panned.scenes[0].nodes[0].x, panned.scenes[0].nodes[0].y),
            base_a
        );
        assert_eq!(
            (panned.scenes[1].nodes[0].x, panned.scenes[1].nodes[0].y),
            base_b
        );
        assert_eq!(
            native[0].film.editorial.type_scale,
            before[0].film.editorial.type_scale
        );
        assert!(realize(&native[0].film).is_ok());
    }

    #[test]
    fn native_film_pan_preserves_safe_area_and_rejects_zoom_or_rotation() {
        let mut project = fixture_project();
        let profile_id = project.deliverables[0].id;
        project.scenes[0].camera.center_x = 1100.0;
        let err = build_motion_canvas_segments(&project, profile_id, &fixture_options(&project))
            .unwrap_err();
        assert_eq!(err.code, ErrorCode::Unsupported);
        assert!(err.message.contains("safe area"));
        project.scenes[0].camera.center_x = CANVAS_WIDTH / 2.0;
        project.scenes[0].camera.zoom = 1.25;
        let err = build_motion_canvas_segments(&project, profile_id, &fixture_options(&project))
            .unwrap_err();
        assert_eq!(err.code, ErrorCode::Unsupported);
        assert!(err.message.contains("camera zoom or rotation"));
        project.scenes[0].camera.zoom = 1.0;
        project.scenes[0].camera.rotation_deg = 7.0;
        let err = build_motion_canvas_segments(&project, profile_id, &fixture_options(&project))
            .unwrap_err();
        assert_eq!(err.code, ErrorCode::Unsupported);
    }

    #[test]
    fn out_of_safe_area_canvas_nodes_fail_before_driver_dispatch() {
        let mut project = fixture_project();
        project.scenes[0].nodes[0].x = 0.0;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(
            error
                .message
                .contains("deterministic Motionwright safe area")
        );
    }

    #[test]
    fn tiled_scene_beats_project_to_distinct_realized_film_shots() {
        let mut project = fixture_project();
        let scene_id = project.scenes[0].id;
        let first_beat = Uuid::now_v7();
        let second_beat = Uuid::now_v7();
        for beat in [
            SceneBeat {
                id: first_beat,
                label: "Hook".into(),
                objective: "Open".into(),
                start: RationalTime::ZERO,
                duration: RationalTime::new(1, 1).unwrap(),
            },
            SceneBeat {
                id: second_beat,
                label: "Proof".into(),
                objective: "Prove".into(),
                start: RationalTime::new(1, 1).unwrap(),
                duration: RationalTime::new(1, 1).unwrap(),
            },
        ] {
            project
                .apply_change(&Change::UpsertSceneBeat { scene_id, beat })
                .unwrap();
        }

        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let segments =
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap();
        let sequence = &segments[0].film.sequences[0];
        // Two authored beats in the first scene plus the second scene's
        // implicit beat remain independently scheduled in one native run.
        assert_eq!(sequence.beats.len(), 3);
        assert_ne!(
            sequence.beats[0].shots[0].subjects[0].id,
            sequence.beats[1].shots[0].subjects[0].id
        );
        let realization = realize(&segments[0].film).unwrap();
        let first_interval = realization
            .schedule
            .interval(&sequence.beats[0].shots[0].span_id)
            .unwrap();
        let second_interval = realization
            .schedule
            .interval(&sequence.beats[1].shots[0].span_id)
            .unwrap();
        assert_eq!(first_interval.start, Rational::ZERO);
        assert_eq!(first_interval.end, Rational::new(1, 1).unwrap());
        assert_eq!(second_interval.start, Rational::new(1, 1).unwrap());
        assert_eq!(second_interval.end, Rational::new(2, 1).unwrap());
        let third_interval = realization
            .schedule
            .interval(&sequence.beats[2].shots[0].span_id)
            .unwrap();
        assert_eq!(third_interval.start, Rational::new(2, 1).unwrap());
        assert_eq!(third_interval.end, Rational::new(3, 1).unwrap());
        assert_ne!(
            sequence.beats[1].shots[0].subjects[0].id,
            sequence.beats[2].shots[0].subjects[0].id
        );
    }

    #[test]
    fn incomplete_scene_beat_coverage_fails_closed_before_driver_dispatch() {
        let mut project = fixture_project();
        let scene_id = project.scenes[0].id;
        project
            .apply_change(&Change::UpsertSceneBeat {
                scene_id,
                beat: SceneBeat {
                    id: Uuid::now_v7(),
                    label: "Partial".into(),
                    objective: "Intentionally incomplete".into(),
                    start: RationalTime::ZERO,
                    duration: RationalTime::new(1, 1).unwrap(),
                },
            })
            .unwrap();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("cover the full scene duration"));
    }

    #[test]
    fn unsupported_runtime_fonts_fail_before_driver_dispatch() {
        let project = fixture_project();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "system-ui".into(),
            mono_font_family: "monospace".into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("bundled fonts"));
    }

    #[test]
    fn renderer_switch_partitions_without_relabeling_foreign_scenes() {
        let mut project = fixture_project();
        let second = project.scenes[1].id;
        project
            .apply_change(&Change::SetSceneRenderer {
                scene_id: second,
                renderer: RendererKind::Blender,
            })
            .unwrap();
        let master = project.deliverables[0].id;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: vec![SceneFilmIntent {
                scene_id: project.scenes[0].id,
                role: NarrativeRole::Hook,
                archetype: Archetype::Statement,
            }],
        };
        let segments = build_motion_canvas_segments(&project, master, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].scene_ids, vec![project.scenes[0].id]);
        assert_eq!(segments[0].global_start, Rational::ZERO);
        assert_eq!(segments[0].duration, Rational::new(2, 1).unwrap());
    }

    fn set_exact_linear_position_keys(node: &mut CanvasNode) {
        // One source-authorized linear ease from (120,110) at frame 0 to the
        // *unchanged base* position (160,140) at frame 30.
        for (at, x, y) in [
            (RationalTime::ZERO, 120.0, 110.0),
            (RationalTime::new(1, 1).unwrap(), node.x, node.y),
        ] {
            node.keyframes.push(CanvasKeyframe {
                at,
                property: MotionProperty::X,
                value: x,
                interpolation: MotionInterpolation::Linear,
            });
            node.keyframes.push(CanvasKeyframe {
                at,
                property: MotionProperty::Y,
                value: y,
                interpolation: MotionInterpolation::Linear,
            });
        }
    }

    #[test]
    fn exact_linear_xy_keys_generate_source_bound_native_settle_motion() {
        let mut project = fixture_project();
        let source_id = project.scenes[0].nodes[0].id;
        let source_pose = (project.scenes[0].nodes[0].x, project.scenes[0].nodes[0].y);
        set_exact_linear_position_keys(&mut project.scenes[0].nodes[0]);
        let options = fixture_options(&project);
        let segments =
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].frame_count, 90);
        let shot = &segments[0].film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.motion.len(), 1);
        match &shot.motion[0].primitive {
            Primitive::Settle {
                target,
                offset,
                rotation,
            } => {
                assert_eq!(target, &subject_id(source_id, None));
                assert!((offset.x + 40.0).abs() < 1e-8);
                assert!((offset.y + 30.0).abs() < 1e-8);
                assert_eq!(*rotation, 0.0);
            }
            other => panic!("Expected exact native Settle, got {other:?}"),
        }
        assert_eq!(shot.motion[0].easing, MotionEasing::Linear);
        let compiled = realize(&segments[0].film).unwrap();
        assert_eq!(compiled.instructions.len(), 2);
        let position = compiled
            .instructions
            .iter()
            .find(|instruction| {
                matches!(
                    &instruction.operation,
                    semwright_motion_authoring::NativeOp::Tween {
                        channel: semwright_motion_authoring::Channel::Position,
                        ..
                    }
                )
            })
            .unwrap();
        assert_eq!(position.start, RationalTime::ZERO);
        assert_eq!(position.duration, RationalTime::new(1, 1).unwrap());
        match &position.operation {
            semwright_motion_authoring::NativeOp::Tween { from, to, .. } => {
                assert!(matches!(
                    from,
                    Some(semwright_motion_authoring::Operand::OriginalOffset(_))
                ));
                assert_eq!(to, &semwright_motion_authoring::Operand::Original);
            }
            _ => unreachable!("position tween already matched"),
        }
        assert_eq!(
            (project.scenes[0].nodes[0].x, project.scenes[0].nodes[0].y,),
            source_pose
        );
    }

    #[test]
    fn unsupported_position_curves_stay_out_of_canonical_film() {
        let baseline = fixture_project();
        let valid = fixture_options(&baseline);
        let profile = baseline.deliverables[0].id;

        let mut omitted_axis = baseline.clone();
        omitted_axis.scenes[0].nodes[0]
            .keyframes
            .push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::X,
                value: 120.0,
                interpolation: MotionInterpolation::Linear,
            });
        assert_eq!(
            build_motion_canvas_segments(&omitted_axis, profile, &valid)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );

        let mut easing = baseline.clone();
        set_exact_linear_position_keys(&mut easing.scenes[0].nodes[0]);
        easing.scenes[0].nodes[0].keyframes[2].interpolation = MotionInterpolation::EaseInOut;
        assert_eq!(
            build_motion_canvas_segments(&easing, profile, &valid)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );

        let mut wrong_target = baseline.clone();
        set_exact_linear_position_keys(&mut wrong_target.scenes[0].nodes[0]);
        wrong_target.scenes[0].nodes[0].keyframes[2].value += 1.0;
        assert_eq!(
            build_motion_canvas_segments(&wrong_target, profile, &valid)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );

        let mut off_frame = baseline.clone();
        set_exact_linear_position_keys(&mut off_frame.scenes[0].nodes[0]);
        for key in off_frame.scenes[0].nodes[0].keyframes.iter_mut().skip(2) {
            key.at = RationalTime::new(1, 7).unwrap();
        }
        assert_eq!(
            build_motion_canvas_segments(&off_frame, profile, &valid)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );

        let mut unsafe_start = baseline.clone();
        set_exact_linear_position_keys(&mut unsafe_start.scenes[0].nodes[0]);
        unsafe_start.scenes[0].nodes[0].keyframes[0].value = 0.0;
        let error = build_motion_canvas_segments(&unsafe_start, profile, &valid).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("safe area"));

        let mut mixed_property = baseline;
        set_exact_linear_position_keys(&mut mixed_property.scenes[0].nodes[0]);
        mixed_property.scenes[0].nodes[0]
            .keyframes
            .push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::Opacity,
                value: 0.5,
                interpolation: MotionInterpolation::Linear,
            });
        assert_eq!(
            build_motion_canvas_segments(&mixed_property, profile, &valid)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
    }

    #[test]
    fn authored_keyframes_fail_closed_until_film_can_preserve_them() {
        let mut project = fixture_project();
        project.scenes[0].nodes[0].keyframes.push(CanvasKeyframe {
            at: RationalTime::new(1, 2).unwrap(),
            property: MotionProperty::X,
            value: 320.0,
            interpolation: MotionInterpolation::EaseInOut,
        });
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("motion keyframes"));
    }

    #[test]
    fn unsupported_canvas_semantics_fail_closed() {
        let mut project = fixture_project();
        project.scenes[0].nodes[0].opacity = 0.5;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }

    #[test]
    fn portrait_replan_projects_without_implicit_crop() {
        let project = fixture_project();
        let vertical = project
            .deliverables
            .iter()
            .find(|profile| profile.name == "Vertical 9:16")
            .unwrap();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let segments = build_motion_canvas_segments(&project, vertical.id, &options).unwrap();
        assert_eq!(segments[0].film.output.width, 1080);
        assert_eq!(segments[0].film.output.height, 1920);
        assert_eq!(segments[0].film.output.aspect, AspectFamily::Portrait);
        assert_eq!(segments[0].frame_count, 90);
        assert!(realize(&segments[0].film).is_ok());
    }

    #[test]
    fn localized_text_override_enters_canonical_film() {
        let mut project = fixture_project();
        let vertical_id = project.deliverables[1].id;
        let text_node = project.scenes[0].nodes[0].id;
        let mut vertical = project.deliverables[1].clone();
        vertical.language = "es-MX".into();
        vertical
            .text_overrides
            .insert(text_node, "Movimiento editable".into());
        project
            .apply_change(&Change::UpsertDeliverable { profile: vertical })
            .unwrap();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let segments = build_motion_canvas_segments(&project, vertical_id, &options).unwrap();
        let subject = &segments[0].film.sequences[0].beats[0].shots[0].subjects[0];
        match &subject.content {
            SubjectContent::Text { runs, language, .. } => {
                assert_eq!(runs[0].text, "Movimiento editable");
                assert_eq!(language, "es-MX");
            }
            other => panic!("expected text subject, got {other:?}"),
        }
    }

    #[test]
    fn narrative_cut_reflows_selected_scenes_to_zero_based_timeline() {
        let mut project = fixture_project();
        let second = project.scenes[1].id;
        let profile_id = project.deliverables[0].id;
        let mut cut = project.deliverables[0].clone();
        cut.included_scene_ids = vec![second];
        cut.cut_label = Some("proof-only".into());
        project
            .apply_change(&Change::UpsertDeliverable { profile: cut })
            .unwrap();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: vec![SceneFilmIntent {
                scene_id: second,
                role: NarrativeRole::Evidence,
                archetype: Archetype::Statement,
            }],
        };
        let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].scene_ids, vec![second]);
        assert_eq!(segments[0].global_start, Rational::ZERO);
        assert_eq!(segments[0].duration, Rational::new(1, 1).unwrap());
        assert_eq!(segments[0].frame_count, 30);
    }

    #[test]
    fn native_render_rejects_frame_rate_drift_from_profile() {
        let project = fixture_project();
        let options = FilmBuildOptions {
            frame_rate: Rate::new(24, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert!(error.message.contains("versioned deliverable profile"));
    }
}
