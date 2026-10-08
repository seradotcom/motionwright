use motionwright_domain::{
    BlendMode, CanvasNode, CoordinateSpace, DeliverableProfile, FramingStrategy, Project,
    RationalTime, RendererKind, Scene,
};
use semwright_media_time::{CueGraph, Rate, Rational};
use semwright_motion_authoring::{
    AUTHORING_VERSION, Archetype, AspectFamily, AssetRef, Beat as AuthoringBeat, EditorialSystem,
    Film, FontFallback, FontSpec, Insets, Layer, NarrativeRole, OutputProfile, Point, Sequence,
    Shot, Size, SpatialIntent, StartAnchor, Subject, SubjectContent, TemporalConstraint,
    TemporalGraph, TemporalSpan, TextDirection, TextRun, VisualConstraint, realize,
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
        let mut projected = scene.clone();
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
    1 + scene.beats.len().max(1)
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
    if !node.keyframes.is_empty() {
        return Err(unsupported(format!(
            "Canvas node {} has authored motion keyframes that require an exact canonical Film motion projection",
            node.id
        )));
    }
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
            if !(100..=900).contains(&weight) {
                return Err(unsupported(format!(
                    "Text node {} font weight is outside canonical Film bounds",
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
            initially_visible: true,
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
        if scene.camera.center_x != CANVAS_WIDTH / 2.0
            || scene.camera.center_y != CANVAS_HEIGHT / 2.0
            || scene.camera.zoom != 1.0
            || scene.camera.rotation_deg != 0.0
        {
            return Err(unsupported(format!(
                "Scene {} uses camera state that canonical Film cannot preserve yet",
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

fn assets(project: &Project) -> NativeResult<Vec<AssetRef>> {
    if project.assets.len() > 128 {
        return Err(unsupported(
            "Canonical Film supports at most 128 digest-bound assets",
        ));
    }
    project
        .assets
        .iter()
        .map(|asset| {
            let digest = asset.content_sha256.clone().ok_or_else(|| {
                unsupported(format!(
                    "Asset {} has no content digest and cannot enter canonical Film",
                    asset.id
                ))
            })?;
            let sha256 = Digest::parse(digest)
                .map_err(|error| contract("Asset digest is invalid", error))?;
            Ok(AssetRef {
                id: uid("asset", asset.id),
                sha256,
                media_type: asset.media_type.clone(),
                provenance: asset.source_revision.clone(),
                license: None,
            })
        })
        .collect()
}

struct ShotProjectionContext<'a> {
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
    let subject_context = SubjectProjectionContext {
        text_style: context.text_style,
        profile: context.profile,
        projection: context.projection,
        output: context.output,
        font_family: context.font_family,
    };
    for node in &scene.nodes {
        let (mapped, mut node_constraints) =
            subject(node, &layer_names, &subject_context, identity.beat_scope)?;
        subjects.push(mapped);
        visual_constraints.append(&mut node_constraints);
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

fn authored_beats_tile_scene(scene: &Scene) -> NativeResult<()> {
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
    let shot_context = ShotProjectionContext {
        profile,
        projection,
        output: &output,
        text_style: &text_style,
        font_family: &options.font_family,
    };

    let span_capacity = scenes.iter().map(scene_span_cost).sum::<usize>();
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
            let shot = projected_shot(
                scene,
                &shot_context,
                ShotIdentity {
                    archetype: intent.archetype,
                    shot_id: uid("shot", scene.id),
                    span_id: shot_span,
                    beat_scope: None,
                },
            )?;
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
        assets: assets(project)?,
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
        assert_eq!(segments[0].film.sequences.len(), 2);
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
        assert_eq!(sequence.beats.len(), 2);
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
