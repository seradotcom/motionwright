#[path = "film_boundary_appearance.rs"]
pub(crate) mod film_boundary_appearance;
#[path = "film_brand_fonts.rs"]
mod film_brand_fonts;
#[path = "film_captions.rs"]
mod film_captions;

#[cfg(test)]
use crate::semantic_graph_source_fixture as graph_source_guard_fixture;

use crate::component_text::{fixed_canvas_text, fixed_component_text};
use crate::expressive::{entry_motion, responsive_scene};
use motionwright_domain::{
    BlendMode, CameraReframe, CanvasNode, CoordinateSpace, DeliverableProfile, FramingStrategy,
    MeasuredView, MotionInterpolation, MotionProperty, Project, RationalTime, RendererKind, Scene,
    stage_node_id,
};
use semwright_media_time::{CueGraph, Rate, Rational};
use semwright_motion_authoring::{
    AUTHORING_BLEND_VERSION, AUTHORING_VERSION, Archetype, AspectFamily, AssetRef,
    Beat as AuthoringBeat, Channel, EditorialSystem, Film, FontFallback, FontSpec, Insets,
    Invocation, Layer, MediaFit, MotionEasing, NarrativeRole, Operand, OutputProfile, Point,
    Primitive, Sequence, Shot, Size, SpatialIntent, StartAnchor, Subject, SubjectBlendMode,
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

/// Geometry and opacity write disjoint native channels. Keep both original
/// key sets in the document and split only their read-only Film projection.
fn composed_shape_motion(node: &CanvasNode) -> Option<(CanvasNode, CanvasNode)> {
    // Existing staggered entrances carry a t0 Hold and optional delayed Hold.
    // Keep their exact SlideIn/Settle route; splitting them would reinterpret
    // those holds as a different pose/opacity grammar.
    if matches!(entry_motion(node), Ok(Some(_))) {
        return None;
    }
    if !matches!(
        node.kind.as_str(),
        "shape" | "rectangle" | "circle" | "text"
    ) || !node
        .keyframes
        .iter()
        .any(|key| key.property == MotionProperty::Opacity)
        || !node
            .keyframes
            .iter()
            .any(|key| key.property != MotionProperty::Opacity)
    {
        return None;
    }
    let mut geometry = node.clone();
    let mut opacity = node.clone();
    geometry
        .keyframes
        .retain(|key| key.property != MotionProperty::Opacity);
    opacity
        .keyframes
        .retain(|key| key.property == MotionProperty::Opacity);
    Some((geometry, opacity))
}

fn has_camera_reframe(project: &Project, scene: &Scene) -> bool {
    project
        .production_design
        .camera_reframes
        .iter()
        .any(|camera| camera.scene_id == scene.id)
}

/// Editorial beats describe timing inside the scene; they are not cuts in
/// an object's scene-local animation clock. A continuous shot owns all visual
/// channels while the original beat intervals remain constrained Film spans.
fn uses_continuous_visual_clock(scene: &Scene, camera_reframe: bool) -> bool {
    camera_reframe || scene.nodes.iter().any(|node| !node.keyframes.is_empty())
}

fn project_scene_span_cost(
    project: &Project,
    scene: &Scene,
    fps: RationalTime,
) -> NativeResult<usize> {
    let extra = project
        .production_design
        .camera_reframes
        .iter()
        .any(|camera| {
            camera.scene_id == scene.id
                && camera.component_version == motionwright_domain::CAMERA_DETAIL_RETURN_VERSION
        });
    Ok(scene_span_cost(scene, has_camera_reframe(project, scene))
        + usize::from(extra)
        + smoothstep_extra_spans(scene, fps)?)
}

fn easing_interval_frames(
    from: RationalTime,
    to: RationalTime,
    fps: RationalTime,
) -> NativeResult<usize> {
    let duration = sub(to, from, "Sampled easing duration underflow")?;
    let numerator = i128::from(duration.num) * i128::from(fps.num);
    let denominator = i128::from(duration.den) * i128::from(fps.den);
    if numerator <= 0 || denominator <= 0 || numerator % denominator != 0 {
        return Err(unsupported(
            "Sampled easing requires positive frame-aligned intervals",
        ));
    }
    usize::try_from(numerator / denominator)
        .ok()
        .filter(|&count| count <= MAX_TEMPORAL_SPANS)
        .ok_or_else(|| unsupported("Sampled easing exceeds the canonical Film span budget"))
}

// The ordinary estimate counts authored intervals. A track with smoothstep
// also samples its other intervals, so they share one frame-clock convention.
fn smoothstep_extra_spans(scene: &Scene, fps: RationalTime) -> NativeResult<usize> {
    let mut extra = 0;
    for node in &scene.nodes {
        for opacity in [false, true] {
            let in_track = |key: &&motionwright_domain::CanvasKeyframe| {
                (key.property == MotionProperty::Opacity) == opacity
            };
            if !node
                .keyframes
                .iter()
                .filter(in_track)
                .any(|key| key.interpolation == MotionInterpolation::EaseInOut)
            {
                continue;
            }
            if opacity {
                let intervals = motionwright_domain::exact_canvas_opacity_spans(node, scene, fps)
                    .map_err(|error| unsupported(error.to_string()))?
                    .unwrap_or_default();
                if !intervals
                    .iter()
                    .any(|span| span.curve == MotionInterpolation::EaseInOut)
                {
                    continue;
                }
                let frames = intervals.iter().try_fold(0, |total, span| {
                    easing_interval_frames(span.start, span.end, fps).map(|frames| total + frames)
                })?;
                let prior = node
                    .keyframes
                    .iter()
                    .filter(in_track)
                    .count()
                    .saturating_sub(1);
                extra += frames.saturating_sub(prior);
            } else {
                let times = node
                    .keyframes
                    .iter()
                    .filter(in_track)
                    .map(|key| key.at)
                    .collect::<BTreeSet<_>>();
                let end = *times.last().expect("smoothstep geometry track is nonempty");
                let last_is_hold = node
                    .keyframes
                    .iter()
                    .filter(in_track)
                    .any(|key| key.at == end && key.interpolation == MotionInterpolation::Hold);
                let frames = easing_interval_frames(RationalTime::ZERO, end, fps)?;
                extra += (frames + 1)
                    .saturating_sub(times.len().saturating_sub(1) + usize::from(last_is_hold));
            }
        }
    }
    Ok(extra)
}

fn easing_at_sample(curve: MotionInterpolation, t: f64) -> f64 {
    match curve {
        MotionInterpolation::Linear => t,
        MotionInterpolation::EaseOutCubic => 1.0 - (1.0 - t).powi(3),
        MotionInterpolation::EaseInOut => t * t * (3.0 - 2.0 * t),
        MotionInterpolation::Hold => 0.0,
    }
}

fn output_frame_offset(frame: RationalTime, index: usize) -> NativeResult<RationalTime> {
    RationalTime::new(frame.num * index as i64, frame.den)
        .map_err(|error| contract("Native easing frame offset", error))
}

fn scene_span_cost(scene: &Scene, camera_reframe: bool) -> usize {
    let continuous = uses_continuous_visual_clock(scene, camera_reframe);
    let visual_shots = if continuous {
        1
    } else {
        scene.beats.len().max(1)
    };
    let editorial_spans = if continuous { scene.beats.len() } else { 0 };
    // Native Hold requires two anchored one-frame spans (Set at t0, Set at tN)
    // instead of a tween's single span. Budget before provider realization,
    // retaining at least the original one-span bound for unsupported keys.
    // One shared first-frame span per shot, independent of shape count.
    let static_rotation_spans = if scene
        .nodes
        .iter()
        .any(motionwright_domain::native_constant_shape_rotation)
    {
        visual_shots
    } else {
        0
    };
    1 + visual_shots
        + editorial_spans
        + usize::from(camera_reframe)
        + static_rotation_spans
        + scene
            .nodes
            .iter()
            .map(|node| {
                let opacity_keys = node
                    .keyframes
                    .iter()
                    .filter(|key| key.property == MotionProperty::Opacity)
                    .count();
                // Equal-value holds emit no span; this is a conservative upper
                // bound before negotiation, including malformed source keys.
                let opacity_span = if composed_shape_motion(node).is_some() {
                    opacity_keys.saturating_sub(1).max(1)
                } else {
                    0
                };
                let geometry_times = node
                    .keyframes
                    .iter()
                    .filter(|key| key.property != MotionProperty::Opacity)
                    .map(|key| key.at)
                    .collect::<BTreeSet<_>>();
                if geometry_times.len() > 2 {
                    let final_hold = geometry_times.last().is_some_and(|time| {
                        node.keyframes.iter().any(|key| {
                            key.at == *time
                                && key.property != MotionProperty::Opacity
                                && key.interpolation == MotionInterpolation::Hold
                        })
                    });
                    geometry_times.len() - 1 + usize::from(final_hold) + opacity_span
                } else if node.keyframes.is_empty() {
                    0
                } else if geometry_times.is_empty() && opacity_keys > 0 {
                    opacity_keys.saturating_sub(1).max(1)
                } else if node.keyframes.iter().any(|key| {
                    key.interpolation == MotionInterpolation::Hold
                        && matches!(
                            key.property,
                            MotionProperty::X
                                | MotionProperty::Y
                                | MotionProperty::Width
                                | MotionProperty::Height
                                | MotionProperty::RotationDeg
                        )
                }) {
                    2 + opacity_span
                } else {
                    1 + opacity_span
                }
            })
            .sum::<usize>()
}

/// Resolve only relations that the exact static authored-box solver supports.
/// This is a Film-only copy; the full original relation graph remains in
/// Project/SQLite, including unsupported and historically authored inputs.
fn aligned_motion_canvas_scene(
    project: &Project,
    source: &Scene,
    mut projected: Scene,
    profile: &DeliverableProfile,
) -> NativeResult<Scene> {
    let participants = source
        .nodes
        .iter()
        .flat_map(|node| {
            node.relations
                .iter()
                .flat_map(move |relation| [node.id, relation.target_id])
        })
        .collect::<BTreeSet<_>>();
    if participants.is_empty()
        && !projected
            .nodes
            .iter()
            .any(|node| !node.relations.is_empty())
    {
        return Ok(projected);
    }
    // Component replanning must not silently remove or rewrite relation
    // authority, including an unconstrained target of a linked object.
    for id in &participants {
        let original = source.nodes.iter().find(|node| node.id == *id);
        let actual = projected.nodes.iter().find(|node| node.id == *id);
        if original.is_none() || original != actual {
            return Err(unsupported(
                "Responsive layout changed an authored alignment participant",
            ));
        }
    }
    if projected
        .nodes
        .iter()
        .any(|node| !node.relations.is_empty() && !participants.contains(&node.id))
    {
        return Err(unsupported(
            "Responsive layout introduced a relation without an authored source",
        ));
    }
    let locked = project.locks.iter().any(|lock| {
        lock.kind == motionwright_domain::LockKind::Position
            && (lock.resource == project.resource_key()
                || lock.resource == format!("scene:{}", source.id))
    });
    let projection = layout_projection(profile);
    let follow_tracks =
        motionwright_domain::resolve_canvas_follow_tracks(&projected, profile.frame_rate, locked)
            .map_err(|error| unsupported(error.to_string()))?;
    for node in &mut projected.nodes {
        if let Some(track) = follow_tracks.get(&node.id) {
            node.keyframes = track.clone();
            node.relations.clear();
        }
    }
    let positions = motionwright_domain::resolve_canvas_alignments(
        &projected,
        motionwright_domain::AlignmentScale {
            x: projection.position_scale_x,
            y: projection.position_scale_y,
            object: projection.object_scale,
        },
        locked,
    )
    .map_err(|error| unsupported(error.to_string()))?;
    for node in &mut projected.nodes {
        if let Some(position) = positions.get(&node.id) {
            node.x = position.x;
            node.y = position.y;
            // Every consumed relation has been resolved into this equivalent
            // fixed native box. Never clear an unresolved relation.
            node.relations.clear();
        }
    }
    Ok(projected)
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
        let source = project
            .scenes
            .iter()
            .find(|source| source.id == scene.id)
            .ok_or_else(|| invalid("Motion Canvas source scene is missing"))?;
        let scene = aligned_motion_canvas_scene(project, source, scene, profile)?;
        if project
            .production_design
            .semantic_graphs
            .iter()
            .any(|graph| graph.scene_id == scene.id && graph.variant_profile_ref == profile.id)
        {
            return Err(unsupported(
                "Selected Motion Canvas scene contains a semantic Graph that this projection cannot preserve",
            ));
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
            let span_cost = current.iter().try_fold(0, |total, item| {
                project_scene_span_cost(project, item, profile.frame_rate).map(|cost| total + cost)
            })?;
            scene.start != last_end
                || candidate_duration > max_duration
                || current.len() >= MAX_SEQUENCES
                || span_cost + project_scene_span_cost(project, &scene, profile.frame_rate)? + 1
                    > MAX_TEMPORAL_SPANS
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
    easing: MotionEasing,
}

/// Only curves that are mathematically identical in Studio's authored
/// preview and the *pinned* Semwright MotionEasing grammar are admissible.
/// Studio stores smoothstep separately and lowers its geometry to literal frame poses.
fn exact_native_easing(interpolation: MotionInterpolation) -> Option<MotionEasing> {
    match interpolation {
        MotionInterpolation::Linear => Some(MotionEasing::Linear),
        MotionInterpolation::EaseOutCubic => Some(MotionEasing::OutCubic),
        MotionInterpolation::Hold | MotionInterpolation::EaseInOut => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum NativeOpacityDirection {
    In,
    Out,
    Sample { opacity: f64 },
}

#[derive(Debug, Clone, Copy)]
struct NativeOpacityFade {
    start: RationalTime,
    duration: RationalTime,
    easing: MotionEasing,
    direction: NativeOpacityDirection,
}

/// Children of unchanged container chains use literal local geometry.
/// Every ancestor retains its own clipping plane and authored identity.
fn native_static_root_group_parent(node: &CanvasNode, scene: &Scene) -> bool {
    motionwright_domain::static_native_group_parent(node, scene).is_some()
}

/// Only the two exact native opacity endpoints are admitted: 0 -> 1 (FadeIn)
/// or 1 -> 0 (FadeOut), at an integer output frame. The Canvas base opacity
/// remains 1; the terminal FadeOut value is a saved keyframe, not a mutation
/// of the underlying object. No quantization or mixed channel substitution.
fn native_exact_opacity_fade(
    node: &CanvasNode,
    scene: &Scene,
    frame_rate: Rate,
) -> NativeResult<Option<Vec<NativeOpacityFade>>> {
    if node.keyframes.is_empty()
        || node
            .keyframes
            .iter()
            .any(|key| key.property != MotionProperty::Opacity)
    {
        return Ok(None);
    }
    let fail = || {
        unsupported(format!(
            "Canvas node {} has opacity keys outside the exact native FadeIn/FadeOut interval contract",
            node.id
        ))
    };
    // Each unchanged Group/Clip keeps its identity and clipping plane.
    if (node.parent_id.is_some() && !native_static_root_group_parent(node, scene))
        || matches!(node.kind.as_str(), "group" | "clip_group")
        || scene
            .nodes
            .iter()
            .any(|candidate| candidate.parent_id == Some(node.id))
        || node.opacity != 1.0
        || (node.rotation_deg != 0.0 && !motionwright_domain::native_shape_rotation(node))
    {
        return Err(fail());
    }
    let fps = RationalTime::new(i64::from(frame_rate.num), i64::from(frame_rate.den))
        .map_err(|_| fail())?;
    let Some(intervals) = motionwright_domain::exact_canvas_opacity_spans(node, scene, fps)
        .map_err(|error| unsupported(error.to_string()))?
    else {
        return Ok(None);
    };
    let sample = intervals
        .iter()
        .any(|span| span.curve == MotionInterpolation::EaseInOut);
    let mut fades = Vec::new();
    if sample {
        let frame = RationalTime::new(fps.den, fps.num).map_err(|_| fail())?;
        let half = RationalTime::new(frame.num, 2 * frame.den).map_err(|_| fail())?;
        let quarter = RationalTime::new(frame.num, 4 * frame.den).map_err(|_| fail())?;
        for interval in intervals {
            let count = easing_interval_frames(interval.start, interval.end, fps)?;
            if fades.len() + count > MAX_TEMPORAL_SPANS {
                return Err(unsupported(
                    "Opacity samples exceed the canonical Film span budget",
                ));
            }
            for index in 1..=count {
                let at = add(
                    interval.start,
                    output_frame_offset(frame, index)?,
                    "Opacity sample time overflow",
                )?;
                let t = index as f64 / count as f64;
                let opacity = if index == count {
                    interval.to
                } else {
                    interval.from
                        + (interval.to - interval.from) * easing_at_sample(interval.curve, t)
                };
                // The renderer's wait can wake a frame early. At that tick
                // Deemphasize writes the current value. The tween then ends
                // inside the same frame interval and writes its target at
                // the next fixed tick, independent of floating-point ties.
                fades.push(NativeOpacityFade {
                    start: sub(at, half, "Opacity sample start underflow")?,
                    duration: quarter,
                    easing: MotionEasing::Linear,
                    direction: NativeOpacityDirection::Sample { opacity },
                });
            }
        }
        return Ok(Some(fades));
    }
    for interval in intervals {
        fades.push(NativeOpacityFade {
            start: interval.start,
            duration: sub(interval.end, interval.start, "Opacity interval underflow")?,
            easing: exact_native_easing(interval.curve).ok_or_else(&fail)?,
            direction: if interval.to > interval.from {
                NativeOpacityDirection::In
            } else {
                NativeOpacityDirection::Out
            },
        });
    }
    Ok(Some(fades))
}

/// A deliberately narrow *exact* mapping to Semwright Primitive::Settle:
/// paired X/Y at scene-local 0, then paired X/Y back to the unchanged base
/// position at an exact output-frame boundary, all linear, single unparented
/// object, no other keyframe channels. Editorial beats retain the scene clock.
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
        if native_exact_opacity_fade(node, scene, frame_rate)?.is_none() {
            entry_motion(node)?;
        }
        return Ok(None);
    }
    let fail = || {
        unsupported(format!(
            "Canvas node {} has motion keyframes outside the exact native linear X/Y settle subset",
            node.id
        ))
    };
    if (node.parent_id.is_some() && !native_static_root_group_parent(node, scene))
        || matches!(node.kind.as_str(), "group" | "clip_group")
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
        || xs[0].interpolation != MotionInterpolation::Linear
        || ys[0].interpolation != MotionInterpolation::Linear
        || xs[1].interpolation != ys[1].interpolation
    {
        return Err(fail());
    }
    let easing = exact_native_easing(xs[1].interpolation).ok_or_else(&fail)?;
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
        easing,
    }))
}

#[derive(Debug, Clone)]
struct NativeGeometryKeyMotion {
    duration: RationalTime,
    easing: MotionEasing,
    // A held destination is an instantaneous GeometrySet at an exact
    // output-frame boundary, never a one-frame GeometryTween approximation.
    held: bool,
    start: CanvasNode,
    width_keyed: bool,
    height_keyed: bool,
    rotation_keyed: bool,
}

/// The pinned Motion Canvas SDK's GeometryTween represents literal width,
/// height, rotation and position values. This exact subset preserves Studio's
/// top-left anchored resize by also tweening the *center* of the fixed Subject.
/// No native scale inference, beat remapping or per-channel timing conversion.
fn native_linear_geometry_motion(
    node: &CanvasNode,
    scene: &Scene,
    frame_rate: Rate,
) -> NativeResult<Option<NativeGeometryKeyMotion>> {
    if matches!(entry_motion(node), Ok(Some(_))) {
        return Ok(None);
    }
    // A single authored X *or* Y curve is exactly expressible as one
    // Position Vector tween/Set, with the untouched axis held at its saved
    // value. The paired X/Y route remains the existing Settle projection.
    let single_axis_motion = node.keyframes.len() == 2
        && (node
            .keyframes
            .iter()
            .all(|key| key.property == MotionProperty::X)
            || node
                .keyframes
                .iter()
                .all(|key| key.property == MotionProperty::Y));
    // A rotated shape's X/Y motion uses literal Position geometry. Settle
    // also writes Rotation and would otherwise flatten its authored angle.
    let has_geometry = single_axis_motion
        || (node.rotation_deg != 0.0
            && node
                .keyframes
                .iter()
                .any(|key| key.property != MotionProperty::Opacity))
        || node.keyframes.iter().any(|key| {
            matches!(
                key.property,
                MotionProperty::Width | MotionProperty::Height | MotionProperty::RotationDeg
            ) || (key.interpolation == MotionInterpolation::Hold
                && matches!(key.property, MotionProperty::X | MotionProperty::Y))
        });
    if !has_geometry {
        return Ok(None);
    }
    let fail = || {
        unsupported(format!(
            "Canvas node {} has geometry keys outside the exact aligned-frame shape tween subset",
            node.id
        ))
    };
    if (node.parent_id.is_some() && !native_static_root_group_parent(node, scene))
        || !matches!(node.kind.as_str(), "shape" | "rectangle" | "circle")
        || scene
            .nodes
            .iter()
            .any(|other| other.parent_id == Some(node.id))
        || node.opacity != 1.0
    {
        return Err(fail());
    }
    let mut start = node.clone();
    let mut duration: Option<RationalTime> = None;
    let mut easing: Option<MotionEasing> = None;
    let mut held: Option<bool> = None;
    let mut admitted_count = 0;
    let mut width_keyed = false;
    let mut height_keyed = false;
    let mut rotation_keyed = false;
    for property in [
        MotionProperty::X,
        MotionProperty::Y,
        MotionProperty::Width,
        MotionProperty::Height,
        MotionProperty::RotationDeg,
    ] {
        let mut keys = node
            .keyframes
            .iter()
            .filter(|key| key.property == property)
            .collect::<Vec<_>>();
        if keys.is_empty() {
            continue;
        }
        admitted_count += keys.len();
        if keys.len() != 2 {
            return Err(fail());
        }
        keys.sort_by_key(|key| key.at);
        let base_value = match property {
            MotionProperty::X => node.x,
            MotionProperty::Y => node.y,
            MotionProperty::Width => node.width,
            MotionProperty::Height => node.height,
            MotionProperty::RotationDeg => node.rotation_deg,
            _ => return Err(fail()),
        };
        if keys[0].at != RationalTime::ZERO
            || keys[1].at <= RationalTime::ZERO
            || keys[1].at >= scene.duration
            || keys[1].value != base_value
            || keys[0].interpolation != MotionInterpolation::Linear
        {
            return Err(fail());
        }
        let destination_held = keys[1].interpolation == MotionInterpolation::Hold;
        let native_easing = if destination_held {
            MotionEasing::Linear // GeometrySet has zero duration; no interpolation.
        } else {
            exact_native_easing(keys[1].interpolation).ok_or_else(&fail)?
        };
        if duration.is_some_and(|previous| previous != keys[1].at)
            || easing.is_some_and(|previous| previous != native_easing)
            || held.is_some_and(|previous| previous != destination_held)
        {
            return Err(fail());
        }
        duration = Some(keys[1].at);
        easing = Some(native_easing);
        held = Some(destination_held);
        match property {
            MotionProperty::X => start.x = keys[0].value,
            MotionProperty::Y => start.y = keys[0].value,
            MotionProperty::Width => {
                width_keyed = true;
                start.width = keys[0].value;
            }
            MotionProperty::Height => {
                height_keyed = true;
                start.height = keys[0].value;
            }
            MotionProperty::RotationDeg => {
                rotation_keyed = true;
                start.rotation_deg = keys[0].value;
            }
            _ => return Err(fail()),
        }
    }
    if admitted_count != node.keyframes.len()
        || !start.x.is_finite()
        || !start.y.is_finite()
        || !start.width.is_finite()
        || !start.height.is_finite()
        || start.width <= 0.0
        || start.height <= 0.0
        || !start.rotation_deg.is_finite()
        || start.rotation_deg.abs() > 360.0
    {
        return Err(fail());
    }
    let duration = duration.ok_or_else(&fail)?;
    let numerator = i128::from(duration.num)
        .checked_mul(i128::from(frame_rate.num))
        .ok_or_else(&fail)?;
    let denominator = i128::from(duration.den)
        .checked_mul(i128::from(frame_rate.den))
        .ok_or_else(&fail)?;
    if denominator <= 0 || numerator <= 0 || numerator % denominator != 0 {
        return Err(fail());
    }
    Ok(Some(NativeGeometryKeyMotion {
        duration,
        easing: easing.ok_or_else(&fail)?,
        held: held.ok_or_else(&fail)?,
        start,
        width_keyed,
        height_keyed,
        rotation_keyed,
    }))
}

/// Use the *actual* native fixed-layout width scale for half-width
/// compensation. Replan may scale positions anisotropically while native
/// geometry uses the minimum isotropic object scale; sampling
/// canonical_position(start) would shift the authored top-left on 9:16 output.
fn projected_geometry_centers(
    end: &CanvasNode,
    start: &CanvasNode,
    projection: LayoutProjection,
    output: &OutputProfile,
) -> (Point, Point) {
    let to = canonical_position(end, projection, output);
    let from = Point {
        x: to.x
            + (start.x - end.x) * projection.position_scale_x
            + (start.width - end.width) * projection.object_scale / 2.0,
        y: to.y
            + (start.y - end.y) * projection.position_scale_y
            + (start.height - end.height) * projection.object_scale / 2.0,
    };
    (from, to)
}

/// Literal geometry operands use the same local coordinate system as the
/// Subject. Saved Canvas poses remain world coordinates under a real Clip.
fn projected_local_geometry_centers(
    end: &CanvasNode,
    start: &CanvasNode,
    scene: &Scene,
    projection: LayoutProjection,
    output: &OutputProfile,
) -> (Point, Point) {
    let (mut from, mut to) = projected_geometry_centers(end, start, projection, output);
    if let Some(parent) = end
        .parent_id
        .and_then(|id| scene.nodes.iter().find(|node| node.id == id))
    {
        let center = canonical_position(parent, projection, output);
        from.x -= center.x;
        from.y -= center.y;
        to.x -= center.x;
        to.y -= center.y;
    }
    (from, to)
}

/// A rotating rectangle can attain its longest diagonal before the endpoint;
/// use the maximum full diagonal for every intermediate frame. Monotone
/// linear/out-cubic center paths then stay in the convex safe rectangle.
fn validate_native_geometry_safety(
    end: &CanvasNode,
    start: &CanvasNode,
    projection: LayoutProjection,
    output: &OutputProfile,
) -> NativeResult<()> {
    let maximum_width = start.width.max(end.width) * projection.object_scale;
    let maximum_height = start.height.max(end.height) * projection.object_scale;
    if !maximum_width.is_finite()
        || !maximum_height.is_finite()
        || maximum_width <= 0.0
        || maximum_height <= 0.0
        || maximum_width > 16384.0
        || maximum_height > 16384.0
    {
        return Err(unsupported(
            "Animated canvas dimensions exceed native geometry bounds",
        ));
    }
    let (from_center, to_center) = projected_geometry_centers(end, start, projection, output);
    if ![from_center.x, from_center.y, to_center.x, to_center.y]
        .iter()
        .all(|value| value.is_finite())
    {
        return Err(unsupported(
            "Animated canvas center exceeds finite native geometry coordinates",
        ));
    }
    if !projection.enforce_safe_area {
        return Ok(());
    }
    let radius = maximum_width.hypot(maximum_height) / 2.0;
    for center in [from_center, to_center] {
        let x = center.x + f64::from(output.width) / 2.0;
        let y = center.y + f64::from(output.height) / 2.0;
        if x - radius < output.safe_area.left - 1e-6
            || y - radius < output.safe_area.top - 1e-6
            || x + radius > f64::from(output.width) - output.safe_area.right + 1e-6
            || y + radius > f64::from(output.height) - output.safe_area.bottom + 1e-6
        {
            return Err(unsupported(format!(
                "Canvas node {} may rotate or resize outside the deterministic Film safe area",
                end.id
            )));
        }
    }
    Ok(())
}

fn native_pose_waypoints(
    node: &CanvasNode,
    scene: &Scene,
    rate: Rate,
) -> NativeResult<Option<Vec<motionwright_domain::CanvasPosePoint>>> {
    if matches!(entry_motion(node), Ok(Some(_))) {
        return Ok(None);
    }
    let fps = RationalTime::new(i64::from(rate.num), i64::from(rate.den))
        .map_err(|error| contract("Native waypoint frame rate", error))?;
    motionwright_domain::exact_canvas_pose_waypoints(node, scene, fps)
        .map_err(|error| unsupported(error.to_string()))
}

/// Every interval uses the same interpolation on all geometry channels.
/// Compensated centers are monotone between literal endpoints; a common
/// maximum diagonal bounds every intermediate rotation and dimension.
fn validate_pose_track_safety(
    node: &CanvasNode,
    points: &[motionwright_domain::CanvasPosePoint],
    projection: LayoutProjection,
    output: &OutputProfile,
) -> NativeResult<()> {
    let width = points
        .iter()
        .map(|point| point.pose.width)
        .fold(0.0_f64, f64::max)
        * projection.object_scale;
    let height = points
        .iter()
        .map(|point| point.pose.height)
        .fold(0.0_f64, f64::max)
        * projection.object_scale;
    if !width.is_finite()
        || !height.is_finite()
        || width <= 0.0
        || height <= 0.0
        || width > 16384.0
        || height > 16384.0
    {
        return Err(unsupported(
            "Waypoint dimensions exceed native geometry bounds",
        ));
    }
    let rotated = points.iter().any(|point| point.pose.rotation_deg != 0.0);
    let half_width = if rotated {
        width.hypot(height) / 2.0
    } else {
        width / 2.0
    };
    let half_height = if rotated { half_width } else { height / 2.0 };
    for point in points {
        // Anchor every literal pose to the same canonical terminal layout.
        // Re-anchoring each interval would drift on nonuniform replans.
        let center = projected_geometry_centers(node, &point.pose, projection, output).0;
        if !center.x.is_finite() || !center.y.is_finite() {
            return Err(unsupported(
                "Waypoint center exceeds finite native geometry",
            ));
        }
        let x = center.x + f64::from(output.width) / 2.0;
        let y = center.y + f64::from(output.height) / 2.0;
        if projection.enforce_safe_area
            && (x - half_width < output.safe_area.left - 1e-6
                || y - half_height < output.safe_area.top - 1e-6
                || x + half_width > f64::from(output.width) - output.safe_area.right + 1e-6
                || y + half_height > f64::from(output.height) - output.safe_area.bottom + 1e-6)
        {
            return Err(unsupported(format!(
                "Canvas node {} waypoint path exceeds the Film safe area",
                node.id
            )));
        }
    }
    Ok(())
}

/// Sample each authored curve at the exact output frames. All intervals of
/// the track use the same literal Set schedule to avoid early tween writes
/// at the edge of a sampled interval.
fn sample_smoothstep_geometry(
    points: &[motionwright_domain::CanvasPosePoint],
    frame: RationalTime,
) -> NativeResult<Vec<motionwright_domain::CanvasPosePoint>> {
    let fps = RationalTime::new(frame.den, frame.num)
        .map_err(|error| contract("Native smoothstep frame rate", error))?;
    let mut sampled = vec![points[0].clone()];
    for pair in points.windows(2) {
        let [from, to] = [&pair[0], &pair[1]];
        let frames = easing_interval_frames(from.at, to.at, fps)?;
        if sampled.len() + frames > MAX_TEMPORAL_SPANS {
            return Err(unsupported(
                "Smoothstep geometry exceeds the canonical Film span budget",
            ));
        }
        for index in 1..frames {
            let t = index as f64 / frames as f64;
            let eased = easing_at_sample(to.curve, t);
            let value = |start: f64, end: f64| start + (end - start) * eased;
            let mut pose = from.pose.clone();
            pose.x = value(from.pose.x, to.pose.x);
            pose.y = value(from.pose.y, to.pose.y);
            pose.width = value(from.pose.width, to.pose.width);
            pose.height = value(from.pose.height, to.pose.height);
            pose.rotation_deg = value(from.pose.rotation_deg, to.pose.rotation_deg);
            let offset = output_frame_offset(frame, index)?;
            sampled.push(motionwright_domain::CanvasPosePoint {
                at: add(from.at, offset, "Native smoothstep time overflow")?,
                pose,
                curve: MotionInterpolation::Hold,
            });
        }
        let mut end = to.clone();
        end.curve = MotionInterpolation::Hold;
        sampled.push(end);
    }
    Ok(sampled)
}

fn append_native_pose_track(
    shot: &mut Shot,
    node: &CanvasNode,
    scene: &Scene,
    points: &[motionwright_domain::CanvasPosePoint],
    context: &CameraFilmContext<'_>,
    spans: &mut Vec<TemporalSpan>,
    constraints: &mut Vec<TemporalConstraint>,
) -> NativeResult<()> {
    validate_pose_track_safety(node, points, context.projection, context.output)?;
    let frame = RationalTime::new(
        i64::from(context.frame_rate.den),
        i64::from(context.frame_rate.num),
    )
    .map_err(|error| contract("Native waypoint frame duration", error))?;
    let sampled;
    let (points, phase, held_span) = if points
        .iter()
        .any(|point| point.curve == MotionInterpolation::EaseInOut)
    {
        sampled = sample_smoothstep_geometry(points, frame)?;
        let half = RationalTime::new(frame.num, 2 * frame.den)
            .map_err(|error| contract("Native easing frame phase", error))?;
        // Motion Canvas 3.17.2 resumes its delay on the first fixed tick
        // at or after (anchor - one frame). Placing the anchor half a
        // frame later stages each Set on its authored output frame.
        (sampled.as_slice(), half, half)
    } else {
        (points, RationalTime::ZERO, frame)
    };
    let target = subject_id(node.id, None);
    let animated = |property| node.keyframes.iter().any(|key| key.property == property);
    let position_animated = [
        MotionProperty::X,
        MotionProperty::Y,
        MotionProperty::Width,
        MotionProperty::Height,
    ]
    .into_iter()
    .any(animated);
    for (index, from) in points.iter().enumerate() {
        let next = points.get(index + 1);
        // A Hold interval changes only at its start. Its next literal pose
        // is set by the following invocation, or by the last-frame Set.
        if next.is_none() && from.curve != MotionInterpolation::Hold {
            continue;
        }
        let held = next.is_none_or(|to| to.curve == MotionInterpolation::Hold);
        let to = next.unwrap_or(from);
        let duration = if held {
            held_span
        } else {
            sub(to.at, from.at, "Native waypoint interval underflow")?
        };
        let curve = if held {
            MotionEasing::Linear
        } else {
            exact_native_easing(to.curve)
                .ok_or_else(|| unsupported("Unsupported native waypoint curve"))?
        };
        if spans.len() >= MAX_TEMPORAL_SPANS {
            return Err(unsupported(
                "Waypoint track exceeds canonical Film temporal span budget",
            ));
        }
        let span_id = format!("pose-track-{}-{}", index, node.id.simple());
        spans.push(TemporalSpan {
            id: span_id.clone(),
            minimum: duration,
            preferred: duration,
            maximum: duration,
            anchor: StartAnchor::Absolute {
                time: add(
                    context.scene_start,
                    add(from.at, phase, "Native waypoint phase overflow")?,
                    "Native waypoint span start overflow",
                )?,
            },
            preference_priority: 0,
        });
        constraints.push(TemporalConstraint::Contains {
            id: format!("pose-track-contains-{}-{}", index, node.id.simple()),
            parent: context.shot_span.to_owned(),
            child: span_id.clone(),
        });
        let from_center = projected_local_geometry_centers(
            node,
            &from.pose,
            scene,
            context.projection,
            context.output,
        )
        .0;
        let to_center = projected_local_geometry_centers(
            node,
            &to.pose,
            scene,
            context.projection,
            context.output,
        )
        .0;
        let scale = context.projection.object_scale;
        let operands = [
            (
                "position",
                Channel::Position,
                Operand::Vector(from_center),
                Operand::Vector(to_center),
            ),
            (
                "width",
                Channel::Width,
                Operand::Number(from.pose.width * scale),
                Operand::Number(to.pose.width * scale),
            ),
            (
                "height",
                Channel::Height,
                Operand::Number(from.pose.height * scale),
                Operand::Number(to.pose.height * scale),
            ),
            (
                "rotation",
                Channel::Rotation,
                Operand::Number(from.pose.rotation_deg),
                Operand::Number(to.pose.rotation_deg),
            ),
        ];
        for (name, channel, start, end) in operands {
            // A resize also changes the center under Studio's top-left anchoring.
            // Constant rotation remains owned by the existing frame-zero Set.
            let keyed = match channel {
                Channel::Position => position_animated,
                Channel::Width => animated(MotionProperty::Width),
                Channel::Height => animated(MotionProperty::Height),
                Channel::Rotation => animated(MotionProperty::RotationDeg),
                _ => false,
            };
            if !keyed || (node.kind == "text" && channel != Channel::Position) {
                continue;
            }
            let primitive = if held {
                Primitive::GeometrySet {
                    target: target.clone(),
                    channel,
                    value: start,
                }
            } else {
                Primitive::GeometryTween {
                    target: target.clone(),
                    channel,
                    from: start,
                    to: end,
                }
            };
            shot.motion.push(Invocation {
                id: format!("pose-track-{}-{}-{}", index, name, node.id.simple()),
                span_id: span_id.clone(),
                easing: curve,
                primitive,
            });
        }
    }
    Ok(())
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
    // The saved shape rotates about its center after the profile projection.
    // The rotated rectangle is also a conservative envelope for an ellipse.
    let (sin, cos) = node.rotation_deg.to_radians().sin_cos();
    let half_width = (width * cos.abs() + height * sin.abs()) / 2.0;
    let half_height = (width * sin.abs() + height * cos.abs()) / 2.0;
    let left = center_x - half_width;
    let top = center_y - half_height;
    let right = center_x + half_width;
    let bottom = center_y + half_height;
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
    if node.opacity != 1.0 {
        return Err(unsupported(format!(
            "Canvas node {} has saved static opacity {} that canonical Film cannot preserve",
            node.id, node.opacity
        )));
    }
    if node.rotation_deg != 0.0 && !motionwright_domain::native_shape_rotation(node) {
        return Err(unsupported(format!(
            "Canvas node {} has saved rotation that canonical Film cannot preserve",
            node.id
        )));
    }
    // Non-normal native compositing is a leaf operation. Existing static
    // normal Group/Clip ancestry is preserved; blending an entire saved group
    // would require a distinct cache/isolation and ordering contract.
    if node.style.blend_mode != BlendMode::Normal
        && !matches!(
            node.kind.as_str(),
            "shape" | "rectangle" | "circle" | "text"
        )
    {
        return Err(unsupported(format!(
            "Canvas node {} uses non-normal blend on an unsupported subject kind",
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

// The native authoring runtime applies editorial.stroke to every outlined
// shape. A zero-width Canvas stroke must therefore be absent in the native
// subject, or it would inherit another subject's nonzero segment-wide width.
fn projected_shape_stroke(node: &CanvasNode) -> Option<String> {
    if node.style.stroke_width == 0.0 {
        None
    } else {
        node.style.stroke.clone()
    }
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
            // responsive_scene resolves the delivery-local copy exactly once.
            let text = node
                .text
                .as_ref()
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
                line_height: node.style.line_height,
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
            stroke: projected_shape_stroke(node),
            radius: 0.0,
        },
        "circle" => SubjectContent::Circle {
            fill: node.style.fill.clone().ok_or_else(|| {
                invalid(format!("Circle node {} requires an explicit fill", node.id))
            })?,
            stroke: projected_shape_stroke(node),
        },
        "group" | "clip_group" => SubjectContent::Group,
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
            // A FadeIn starts hidden. A FadeOut must start visible: every
            // original saved key is still validated against the exact native
            // endpoint grammar before the Film can be admitted.
            initially_visible: !node.keyframes.iter().any(|key| {
                key.property == MotionProperty::Opacity
                    && key.at == RationalTime::ZERO
                    && key.value == 0.0
            }),
            clip_intentional: node.kind == "clip_group",
            blend_mode: match node.style.blend_mode {
                BlendMode::Normal => SubjectBlendMode::Normal,
                BlendMode::Multiply => SubjectBlendMode::Multiply,
                BlendMode::Screen => SubjectBlendMode::Screen,
                BlendMode::Add => SubjectBlendMode::Add,
            },
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
        return Err(unsupported(format!(
            "Film frame-rate conversion is unsupported: saved_deliverable_rate={}/{}; requested_output_rate={}/{}; policy=preserve-saved-frame-rate-v1; no rounding or retiming was applied",
            profile.frame_rate.num, profile.frame_rate.den, frame_rate.num, frame_rate.den,
        )));
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
/// every Project/CAS asset into Film is incorrect: measured VO, music and
/// unused imports are not managed visual resources. Selected brand fonts
/// enter separately through FontRight/CAS and the public provider. An unrelated
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
    project: &'a Project,
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
    motionwright_domain::validate_static_native_group_order(scene)
        .map_err(|error| unsupported(error.to_string()))?;
    if has_camera_reframe(context.project, scene)
        && scene
            .nodes
            .iter()
            .any(|node| node.style.blend_mode != BlendMode::Normal)
    {
        return Err(unsupported(
            "Native non-normal blend with animated camera reframe needs an exact cache-boundary projection",
        ));
    }
    for node in &scene.nodes {
        if node.style.blend_mode != BlendMode::Normal
            && scene
                .nodes
                .iter()
                .any(|child| child.parent_id == Some(node.id))
        {
            return Err(unsupported(
                "Native non-normal blend cannot reinterpret a painted subject subtree",
            ));
        }
        let (mut mapped, mut node_constraints) =
            subject(node, &layer_names, &subject_context, identity.beat_scope)?;
        if let Some(board) = context
            .project
            .production_design
            .capture_boards
            .iter()
            .find(|board| board.scene_id == scene.id)
        {
            for (index, role) in ["image-1", "image-2"].into_iter().enumerate() {
                if node.id == motionwright_domain::capture_board_node_id(board.id, role) {
                    let source = board
                        .sources(context.project)
                        .map_err(|error| invalid(error.to_string()))?[index];
                    mapped.role = "first-party-synthetic-board-image".into();
                    mapped.content = SubjectContent::Image {
                        asset_id: uid("asset", source.screenshot_asset_id),
                        fit: MediaFit::Contain,
                        ratio: f64::from(source.viewport_width) / f64::from(source.viewport_height),
                    };
                }
            }
        }
        if let Some(parent) = motionwright_domain::static_native_group_parent(node, scene) {
            // Native fixed children use local coordinates. The authored
            // Group/Clip is unpainted and its children remain world-space
            // objects in the saved Canvas. Convert exactly once.
            let native_parent = canonical_position(parent, projection, context.output);
            match &mut mapped.layout {
                SpatialIntent::Fixed { position, .. } => {
                    position.x -= native_parent.x;
                    position.y -= native_parent.y;
                }
                _ => {
                    return Err(unsupported(
                        "Native Group requires exact fixed child geometry",
                    ));
                }
            }
        }
        // A saved deliverable may add hard line breaks via text_overrides.
        // Preserve single-line lowering: changing its native hierarchy would
        // alter existing saved projects without any authored line advance.
        let has_hard_lines = matches!(
            &mapped.content,
            SubjectContent::Text { runs, .. }
                if runs.len() == 1 && runs[0].text.contains('\n')
        );
        if node.kind == "text" && (context.component_texts.contains(&node.id) || has_hard_lines) {
            if mapped.blend_mode != SubjectBlendMode::Normal {
                return Err(unsupported(
                    "Non-normal blend on generated fixed text requires verified combined glyph-subtree compositing",
                ));
            }
            let (mut lines, mut constraints) = if context.component_texts.contains(&node.id) {
                fixed_component_text(node, mapped, projection.object_scale)?
            } else {
                fixed_canvas_text(
                    node,
                    mapped,
                    projection.object_scale,
                    512usize.saturating_sub(subjects.len()),
                )?
            };
            subjects.append(&mut lines);
            visual_constraints.append(&mut constraints);
        } else {
            subjects.push(mapped);
            visual_constraints.append(&mut node_constraints);
        }
        // Only an explicitly registered, immutable first-party capture may
        // enter a ProductStage source card. It is a CONTAINED image inside
        // the existing editable Canvas card, never a replacement for the
        // authored scene, a fabricated screenshot, or a new provider route.
        let linked_stage = context
            .project
            .production_design
            .stages
            .iter()
            .find(|stage| stage.scene_id == scene.id && node.id == stage_node_id(stage.id, "card"));
        if let Some(stage) = linked_stage
            && let Some(link) = context
                .project
                .production_design
                .stage_capture_cards
                .iter()
                .find(|binding| binding.stage_id == stage.id)
        {
            let source = context
                .project
                .production_design
                .captures
                .iter()
                .find(|capture| capture.id == link.capture_id)
                .ok_or_else(|| invalid("Stage screenshot source was removed"))?;
            if node.rotation_deg != 0.0 || node.style.blend_mode != BlendMode::Normal {
                return Err(unsupported(
                    "A rotated or non-normal blended source-capture card requires a dedicated coupled tile projection",
                ));
            }
            let mut tile = node.clone();
            tile.id = stage_node_id(stage.id, "source_capture_tile");
            tile.name = "Source-bound first-party synthetic screenshot tile".into();
            tile.x = node.x + node.width * 0.39;
            tile.y = node.y + node.height * 0.08;
            tile.width = node.width * 0.57;
            tile.height = node.height * 0.82;
            validate_static_safe_area(&tile, projection, context.output)?;
            let image = Subject {
                id: subject_id(tile.id, identity.beat_scope),
                role: "first-party-synthetic-source-tile".into(),
                parent: None,
                layer: layer_names
                    .get(&node.z_index)
                    .cloned()
                    .ok_or_else(|| invalid("Source tile Canvas card layer is missing"))?,
                content: SubjectContent::Image {
                    asset_id: uid("asset", source.screenshot_asset_id),
                    fit: MediaFit::Contain,
                    ratio: f64::from(source.viewport_width) / f64::from(source.viewport_height),
                },
                layout: SpatialIntent::Fixed {
                    position: canonical_position(&tile, projection, context.output),
                    size: Size {
                        width: tile.width * projection.object_scale,
                        height: tile.height * projection.object_scale,
                    },
                },
                initially_visible: true,
                clip_intentional: false,
                blend_mode: semwright_motion_authoring::SubjectBlendMode::Normal,
            };
            subjects.push(image);
        }
        if subjects.len() > 512 {
            return Err(unsupported(
                "Component source realization exceeds the native subject budget",
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

/// Lower one shared frame-zero span per shot for constant shape orientation,
/// including shapes with position, size or opacity motion. Keyed rotation is
/// owned only by its literal pose track; no second writer is introduced.
struct StaticRotationClock {
    start: RationalTime,
    duration: RationalTime,
    rate: Rate,
    beat_scope: Option<Uuid>,
}
fn attach_static_rotations(
    shot: &mut Shot,
    scene: &Scene,
    clock: StaticRotationClock,
    spans: &mut Vec<TemporalSpan>,
    constraints: &mut Vec<TemporalConstraint>,
) -> NativeResult<()> {
    let rotated = scene
        .nodes
        .iter()
        .filter(|node| motionwright_domain::native_constant_shape_rotation(node))
        .collect::<Vec<_>>();
    if rotated.is_empty() {
        return Ok(());
    }
    let frame = RationalTime::new(i64::from(clock.rate.den), i64::from(clock.rate.num))
        .map_err(|error| contract("Static rotation frame duration", error))?;
    if frame > clock.duration || spans.len() >= MAX_TEMPORAL_SPANS {
        return Err(unsupported(
            "Static shape rotation requires one complete first frame and a bounded Film span",
        ));
    }
    let span_id = format!("static-rotation-span-{}", shot.id);
    spans.push(TemporalSpan {
        id: span_id.clone(),
        minimum: frame,
        preferred: frame,
        maximum: frame,
        anchor: StartAnchor::Absolute { time: clock.start },
        preference_priority: 0,
    });
    constraints.push(TemporalConstraint::Contains {
        id: format!("static-rotation-contains-{}", shot.id),
        parent: shot.span_id.clone(),
        child: span_id.clone(),
    });
    for node in rotated {
        let target = subject_id(node.id, clock.beat_scope);
        if !shot.subjects.iter().any(|subject| subject.id == target) {
            return Err(unsupported(
                "Static rotation cannot find its original projected shape",
            ));
        }
        shot.motion.push(Invocation {
            id: format!("static-rotation-{target}"),
            span_id: span_id.clone(),
            easing: MotionEasing::Linear,
            primitive: Primitive::GeometrySet {
                target,
                channel: Channel::Rotation,
                value: Operand::Number(node.rotation_deg),
            },
        });
    }
    Ok(())
}

/// MW05-E07-03: authored bounded planar camera reframe as native source motion.
/// The existing canonical Film render path moves every semantic subject by
/// the same exact scene-camera displacement. All source objects, text groups,
/// source IDs and layers stay editable. This is not a 3D or optical dolly.
struct CameraFilmContext<'a> {
    scene_start: RationalTime,
    shot_span: &'a str,
    frame_rate: Rate,
    projection: LayoutProjection,
    output: &'a OutputProfile,
}
/// Two nonoverlapping literal transform spans on one unpainted root.
/// All native subject identities, fixed text leaves and saved coordinates
/// stay intact. Only the shared frame's position and scale are animated.
fn attach_camera_detail_return(
    shot: &mut Shot,
    scene: &Scene,
    reframe: &CameraReframe,
    context: &CameraFilmContext<'_>,
    spans: &mut Vec<TemporalSpan>,
    constraints: &mut Vec<TemporalConstraint>,
) -> NativeResult<()> {
    let detail = reframe
        .detail_return
        .ok_or_else(|| invalid("Camera detail return parameters are missing"))?;
    if !context.projection.enforce_safe_area || spans.len().saturating_add(2) > MAX_TEMPORAL_SPANS {
        return Err(unsupported(
            "Camera detail return requires Replan and two available temporal spans",
        ));
    }
    let width = f64::from(context.output.width);
    let height = f64::from(context.output.height);
    let margin = (context.output.safe_area.left / width)
        .max(context.output.safe_area.top / height)
        .max(context.output.safe_area.right / width)
        .max(context.output.safe_area.bottom / height);
    reframe
        .validate_detail_return_bounds(scene, width, height, margin)
        .map_err(|error| unsupported(error.to_string()))?;
    let frame_id = uid("camera-frame", reframe.id);
    let layer = shot
        .layers
        .first()
        .ok_or_else(|| invalid("Camera source has no native layer"))?
        .id
        .clone();
    for subject in &mut shot.subjects {
        if subject.parent.is_none() {
            subject.parent = Some(frame_id.clone());
        }
    }
    shot.subjects.insert(
        0,
        Subject {
            id: frame_id.clone(),
            role: "camera-detail-return-frame".into(),
            parent: None,
            layer,
            content: SubjectContent::Group,
            layout: SpatialIntent::Fixed {
                position: Point { x: 0.0, y: 0.0 },
                size: Size {
                    width: 1.0,
                    height: 1.0,
                },
            },
            initially_visible: true,
            clip_intentional: false,
            blend_mode: SubjectBlendMode::Normal,
        },
    );
    let zero = Point { x: 0.0, y: 0.0 };
    let unit = Point { x: 1.0, y: 1.0 };
    let shift = Point {
        x: (reframe.end_center_x - detail.center_x)
            * context.projection.position_scale_x
            * detail.zoom,
        y: (reframe.end_center_y - detail.center_y)
            * context.projection.position_scale_y
            * detail.zoom,
    };
    let zoom = Point {
        x: detail.zoom,
        y: detail.zoom,
    };
    let return_start = i64::from(detail.approach_frames) + i64::from(detail.hold_frames);
    for (name, start, length, from_pos, to_pos, from_scale, to_scale) in [
        (
            "approach",
            0,
            i64::from(detail.approach_frames),
            zero,
            shift,
            unit,
            zoom,
        ),
        (
            "return",
            return_start,
            i64::from(reframe.duration_frames) - return_start,
            shift,
            zero,
            zoom,
            unit,
        ),
    ] {
        let span = format!("camera-detail-{name}-{}", reframe.id.simple());
        let duration = RationalTime::new(length, 30).expect("validated detail duration");
        spans.push(TemporalSpan {
            id: span.clone(),
            minimum: duration,
            preferred: duration,
            maximum: duration,
            anchor: StartAnchor::Absolute {
                time: add(
                    context.scene_start,
                    RationalTime::new(start, 30).expect("detail start"),
                    "Camera detail start overflow",
                )?,
            },
            preference_priority: 0,
        });
        constraints.push(TemporalConstraint::Contains {
            id: format!("camera-detail-contains-{name}-{}", reframe.id.simple()),
            parent: context.shot_span.to_owned(),
            child: span.clone(),
        });
        for (channel, field, from, to) in [
            (Channel::Position, "position", from_pos, to_pos),
            (Channel::Scale, "scale", from_scale, to_scale),
        ] {
            shot.motion.push(Invocation {
                id: format!("camera-detail-{name}-{field}-{}", reframe.id.simple()),
                span_id: span.clone(),
                easing: MotionEasing::Linear,
                primitive: Primitive::GeometryTween {
                    target: frame_id.clone(),
                    channel,
                    from: Operand::Vector(from),
                    to: Operand::Vector(to),
                },
            });
        }
    }
    Ok(())
}

fn attach_camera_reframe(
    shot: &mut Shot,
    scene: &Scene,
    reframe: &CameraReframe,
    context: &CameraFilmContext<'_>,
    spans: &mut Vec<TemporalSpan>,
    constraints: &mut Vec<TemporalConstraint>,
) -> NativeResult<()> {
    reframe
        .validate(scene)
        .map_err(|err| invalid(err.to_string()))?;
    if context.frame_rate.num != 30 || context.frame_rate.den != 1 {
        return Err(unsupported(
            "Bounded canonical camera reframe supports exact 30fps deliverables only",
        ));
    }
    if reframe.component_version == motionwright_domain::CAMERA_DETAIL_RETURN_VERSION {
        return attach_camera_detail_return(shot, scene, reframe, context, spans, constraints);
    }
    if spans.len() >= MAX_TEMPORAL_SPANS {
        return Err(unsupported(
            "Camera reframe would exceed canonical Film temporal span budget",
        ));
    }
    let mut initial = scene.clone();
    initial.camera.center_x = reframe.start_center_x;
    initial.camera.center_y = reframe.start_center_y;
    let first_projection = scene_layout_projection(&initial, context.projection);
    let last_projection = scene_layout_projection(scene, context.projection);
    for node in &scene.nodes {
        // The original and terminal layouts must be independently valid for
        // this exact output profile. A linear camera translation is a convex
        // path between safe endpoints and thus cannot crop in between.
        let composed = composed_shape_motion(node);
        let geometry = composed.as_ref().map_or(node, |(geometry, _)| geometry);
        if let Some(points) = native_pose_waypoints(geometry, scene, context.frame_rate)? {
            validate_pose_track_safety(geometry, &points, first_projection, context.output)?;
            validate_pose_track_safety(geometry, &points, last_projection, context.output)?;
        } else if let Some(motion) =
            native_linear_geometry_motion(geometry, scene, context.frame_rate)?
        {
            validate_native_geometry_safety(
                geometry,
                &motion.start,
                first_projection,
                context.output,
            )?;
            validate_native_geometry_safety(
                geometry,
                &motion.start,
                last_projection,
                context.output,
            )?;
        } else if let Some(motion) =
            native_linear_position_motion(geometry, scene, context.frame_rate)?
        {
            let mut start = geometry.clone();
            start.x = motion.start_x;
            start.y = motion.start_y;
            for pose in [&start, geometry] {
                validate_static_safe_area(pose, first_projection, context.output)?;
                validate_static_safe_area(pose, last_projection, context.output)?;
            }
        } else {
            validate_static_safe_area(node, first_projection, context.output)?;
            validate_static_safe_area(node, last_projection, context.output)?;
        }
    }
    let span = format!("camera-reframe-{}", reframe.id.simple());
    let duration = RationalTime::new(i64::from(reframe.duration_frames), 30)
        .expect("30fps camera frame duration is always a valid rational");
    spans.push(TemporalSpan {
        id: span.clone(),
        minimum: duration,
        preferred: duration,
        maximum: duration,
        anchor: StartAnchor::Absolute {
            time: context.scene_start,
        },
        preference_priority: 0,
    });
    constraints.push(TemporalConstraint::Contains {
        id: format!("camera-shot-contains-{}", reframe.id.simple()),
        parent: context.shot_span.to_owned(),
        child: span.clone(),
    });
    let shift = Point {
        x: (reframe.end_center_x - reframe.start_center_x) * context.projection.position_scale_x,
        y: (reframe.end_center_y - reframe.start_center_y) * context.projection.position_scale_y,
    };
    if motionwright_domain::camera_uses_composition(scene) {
        let frame_id = uid("camera-frame", reframe.id);
        let layer = shot
            .layers
            .first()
            .ok_or_else(|| invalid("Camera source has no native layer"))?
            .id
            .clone();
        for subject in &mut shot.subjects {
            if subject.parent.is_none() {
                subject.parent = Some(frame_id.clone());
            }
        }
        // An unpainted, unclipped Group carries translation only. Its centered
        // unit box does not constrain child layout: all existing subjects keep
        // their exact fixed coordinates and source layer/identity/clip owners.
        shot.subjects.insert(
            0,
            Subject {
                id: frame_id.clone(),
                role: "camera-frame".into(),
                parent: None,
                layer,
                content: SubjectContent::Group,
                layout: SpatialIntent::Fixed {
                    position: Point { x: 0.0, y: 0.0 },
                    size: Size {
                        width: 1.0,
                        height: 1.0,
                    },
                },
                initially_visible: true,
                clip_intentional: false,
                blend_mode: semwright_motion_authoring::SubjectBlendMode::Normal,
            },
        );
        shot.motion.push(Invocation {
            id: format!("camera-reframe-{}", reframe.id.simple()),
            span_id: span,
            easing: MotionEasing::Linear,
            primitive: Primitive::Settle {
                target: frame_id,
                offset: shift,
                rotation: 0.0,
            },
        });
        return Ok(());
    }
    for node in &scene.nodes {
        shot.motion.push(Invocation {
            id: format!(
                "camera-reframe-{}-{}",
                reframe.id.simple(),
                node.id.simple()
            ),
            span_id: span.clone(),
            easing: MotionEasing::Linear,
            primitive: Primitive::Settle {
                target: subject_id(node.id, None),
                offset: shift,
                rotation: 0.0,
            },
        });
    }
    Ok(())
}

fn attach_entrance_motion(
    shot: &mut Shot,
    scene: &Scene,
    scene_start: RationalTime,
    projection: LayoutProjection,
    frame_rate: Rate,
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
        if matches!(node.kind.as_str(), "group" | "clip_group")
            || scene
                .nodes
                .iter()
                .any(|child| child.parent_id == Some(node.id))
        {
            return Err(unsupported(
                "Authored entrance motion on source hierarchies requires a dedicated mapping",
            ));
        }
        let composed = composed_shape_motion(node);
        let opacity_source = composed.as_ref().map_or(node, |(_, opacity)| opacity);
        if let Some(fades) = native_exact_opacity_fade(opacity_source, scene, frame_rate)? {
            if spans.len().saturating_add(fades.len()) > MAX_TEMPORAL_SPANS {
                return Err(unsupported(
                    "Opacity intervals exceed the Native temporal span budget",
                ));
            }
            let multiple = fades.len() > 1;
            for (index, fade) in fades.into_iter().enumerate() {
                let suffix = if multiple {
                    format!("-{index}")
                } else {
                    String::new()
                };
                let span_id = format!("linear-opacity-{}{suffix}", node.id.simple());
                spans.push(TemporalSpan {
                    id: span_id.clone(),
                    minimum: fade.duration,
                    preferred: fade.duration,
                    maximum: fade.duration,
                    anchor: StartAnchor::Absolute {
                        time: add(scene_start, fade.start, "Opacity interval start overflow")?,
                    },
                    preference_priority: 0,
                });
                let target = subject_id(node.id, None);
                let primitive = match fade.direction {
                    NativeOpacityDirection::In => Primitive::FadeIn { target },
                    NativeOpacityDirection::Out => Primitive::FadeOut { target },
                    NativeOpacityDirection::Sample { opacity } => {
                        Primitive::Deemphasize { target, opacity }
                    }
                };
                shot.motion.push(Invocation {
                    id: format!("native-fade-{}{suffix}", node.id.simple()),
                    span_id,
                    easing: fade.easing,
                    primitive,
                });
            }
            continue;
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

/// Keep every authored beat as its own exact source-identified interval.
/// These nonvisual spans are contained by the one continuous visual shot,
/// just as source scene spans remain constrained after native run grouping.
/// No subject, curve or camera is restarted at an editorial boundary.
fn attach_continuous_editorial_spans(
    scene: &Scene,
    scene_start: RationalTime,
    shot_span: &str,
    spans: &mut Vec<TemporalSpan>,
    constraints: &mut Vec<TemporalConstraint>,
) -> NativeResult<()> {
    for beat in &scene.beats {
        if spans.len() >= MAX_TEMPORAL_SPANS {
            return Err(unsupported(
                "Editorial beat continuity exceeds the Film temporal span budget",
            ));
        }
        let id = format!("editorial-beat-{}-{}", scene.id.simple(), beat.id.simple());
        spans.push(TemporalSpan {
            id: id.clone(),
            minimum: beat.duration,
            preferred: beat.duration,
            maximum: beat.duration,
            anchor: StartAnchor::Absolute {
                time: add(
                    scene_start,
                    beat.start,
                    "Editorial beat continuity start overflow",
                )?,
            },
            preference_priority: 0,
        });
        constraints.push(TemporalConstraint::Contains {
            id: format!("continuous-contains-{}", beat.id.simple()),
            parent: shot_span.to_owned(),
            child: id,
        });
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
    // Bound ProductStage text must remain within authored screen-anchor
    // glyph boxes. Reuse the proven native fixed-component text grammar
    // rather than allowing provider auto-layout to shift or drop text.
    // A measured counter/label is a native, editable text object whose
    // glyph position must remain inside its *authored* Canvas region.
    // The generic Motion Canvas Txt automatic layout can silently drop
    // otherwise valid unparented measured text; the source-pinned Hero
    // fixed-container grammar already preserves exact typographic regions.
    // Scope that grammar to bound measured text only, not arbitrary nodes.
    let mut component_texts = project
        .production_design
        .heroes
        .iter()
        .flat_map(|hero| hero.baseline.iter().map(|node| node.id))
        .collect::<BTreeSet<_>>();
    component_texts.extend(project.production_design.stages.iter().flat_map(|stage| {
        stage
            .baseline
            .iter()
            .filter(|node| node.kind == "text")
            .map(|node| node.id)
    }));
    component_texts.extend(
        project
            .production_design
            .measured_bindings
            .iter()
            .filter(|binding| {
                matches!(
                    binding.view,
                    MeasuredView::Counter { .. } | MeasuredView::Label { .. }
                )
            })
            .map(|binding| binding.node_id),
    );
    component_texts.extend(
        project
            .production_design
            .capture_boards
            .iter()
            .flat_map(|board| {
                ["title", "label-1", "label-2", "disclosure"]
                    .into_iter()
                    .map(move |role| motionwright_domain::capture_board_node_id(board.id, role))
            }),
    );
    let shot_context = ShotProjectionContext {
        project,
        component_texts: &component_texts,
        profile,
        projection,
        output: &output,
        text_style: &text_style,
        font_family: &options.font_family,
    };

    let span_capacity = scenes
        .iter()
        .try_fold(usize::from(scenes.len() > 1), |total, scene| {
            project_scene_span_cost(project, scene, profile.frame_rate).map(|cost| total + cost)
        })?;
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
        if scene.beats.is_empty()
            || uses_continuous_visual_clock(scene, has_camera_reframe(project, scene))
        {
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
            attach_continuous_editorial_spans(
                scene,
                local_start,
                &shot_span,
                &mut spans,
                &mut constraints,
            )?;
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
            attach_static_rotations(
                &mut shot,
                scene,
                StaticRotationClock {
                    start: local_start,
                    duration: scene.duration,
                    rate: options.frame_rate,
                    beat_scope: None,
                },
                &mut spans,
                &mut constraints,
            )?;
            let source_projection = scene_layout_projection(scene, projection);
            for source_node in &scene.nodes {
                let composed = composed_shape_motion(source_node);
                let node = composed
                    .as_ref()
                    .map_or(source_node, |(geometry, _)| geometry);
                if let Some(points) = native_pose_waypoints(node, scene, options.frame_rate)? {
                    let context = CameraFilmContext {
                        scene_start: local_start,
                        shot_span: &shot_span,
                        frame_rate: options.frame_rate,
                        projection: source_projection,
                        output: &output,
                    };
                    append_native_pose_track(
                        &mut shot,
                        node,
                        scene,
                        &points,
                        &context,
                        &mut spans,
                        &mut constraints,
                    )?;
                    continue;
                }
                if let Some(motion) =
                    native_linear_geometry_motion(node, scene, options.frame_rate)?
                {
                    validate_native_geometry_safety(
                        node,
                        &motion.start,
                        source_projection,
                        &output,
                    )?;
                    if motion.held {
                        // Studio's Hold property keeps the first authored pose
                        // until *exactly* the next keyframe, then jumps. The
                        // pinned Semwright grammar emits zero-duration Set at
                        // each explicit span start. A one-frame Tween would
                        // silently change the author's interpolation.
                        let frame = RationalTime::new(
                            i64::from(options.frame_rate.den),
                            i64::from(options.frame_rate.num),
                        )
                        .map_err(|error| contract("Native Hold frame duration", error))?;
                        if add(motion.duration, frame, "Hold final span overflow")? > scene.duration
                        {
                            return Err(unsupported(
                                "Native held shape key must leave one complete output frame in the scene",
                            ));
                        }
                        let start_span = format!("geometry-hold-start-{}", node.id.simple());
                        let end_span = format!("geometry-hold-end-{}", node.id.simple());
                        for (label, span_id, when) in [
                            ("start", &start_span, local_start),
                            (
                                "end",
                                &end_span,
                                add(local_start, motion.duration, "Hold anchor overflow")?,
                            ),
                        ] {
                            spans.push(TemporalSpan {
                                id: span_id.clone(),
                                minimum: frame,
                                preferred: frame,
                                maximum: frame,
                                anchor: StartAnchor::Absolute { time: when },
                                preference_priority: 0,
                            });
                            constraints.push(TemporalConstraint::Contains {
                                id: format!("geometry-hold-contains-{label}-{}", node.id.simple()),
                                parent: shot_span.clone(),
                                child: span_id.clone(),
                            });
                        }
                        let (from_center, to_center) = projected_local_geometry_centers(
                            node,
                            &motion.start,
                            scene,
                            source_projection,
                            &output,
                        );
                        let target = subject_id(node.id, None);
                        let mut writes = Vec::new();
                        if from_center != to_center {
                            writes.push((
                                "position",
                                Channel::Position,
                                Operand::Vector(from_center),
                                Operand::Vector(to_center),
                            ));
                        }
                        for (keyed, name, channel, initial, final_value) in [
                            (
                                motion.width_keyed,
                                "width",
                                Channel::Width,
                                motion.start.width * source_projection.object_scale,
                                node.width * source_projection.object_scale,
                            ),
                            (
                                motion.height_keyed,
                                "height",
                                Channel::Height,
                                motion.start.height * source_projection.object_scale,
                                node.height * source_projection.object_scale,
                            ),
                            (
                                motion.rotation_keyed,
                                "rotation",
                                Channel::Rotation,
                                motion.start.rotation_deg,
                                node.rotation_deg,
                            ),
                        ] {
                            if keyed {
                                writes.push((
                                    name,
                                    channel,
                                    Operand::Number(initial),
                                    Operand::Number(final_value),
                                ));
                            }
                        }
                        for (name, channel, initial, final_value) in writes {
                            for (phase, span_id, value) in [
                                ("start", &start_span, initial),
                                ("end", &end_span, final_value),
                            ] {
                                shot.motion.push(Invocation {
                                    id: format!(
                                        "geometry-hold-{phase}-{name}-{}",
                                        node.id.simple()
                                    ),
                                    span_id: span_id.clone(),
                                    easing: MotionEasing::Linear,
                                    primitive: Primitive::GeometrySet {
                                        target: target.clone(),
                                        channel,
                                        value,
                                    },
                                });
                            }
                        }
                        continue;
                    }
                    let span_id = format!("geometry-span-{}", node.id.simple());
                    spans.push(TemporalSpan {
                        id: span_id.clone(),
                        minimum: motion.duration,
                        preferred: motion.duration,
                        maximum: motion.duration,
                        anchor: StartAnchor::Absolute { time: local_start },
                        preference_priority: 0,
                    });
                    constraints.push(TemporalConstraint::Contains {
                        id: format!("geometry-contains-{}", node.id.simple()),
                        parent: shot_span.clone(),
                        child: span_id.clone(),
                    });
                    let target = subject_id(node.id, None);
                    let (from_center, to_center) = projected_local_geometry_centers(
                        node,
                        &motion.start,
                        scene,
                        source_projection,
                        &output,
                    );
                    if from_center != to_center {
                        shot.motion.push(Invocation {
                            id: format!("geometry-position-{}", node.id.simple()),
                            span_id: span_id.clone(),
                            easing: motion.easing,
                            primitive: Primitive::GeometryTween {
                                target: target.clone(),
                                channel: Channel::Position,
                                from: Operand::Vector(from_center),
                                to: Operand::Vector(to_center),
                            },
                        });
                    }
                    for (keyed, name, channel, from, to) in [
                        (
                            motion.width_keyed,
                            "width",
                            Channel::Width,
                            motion.start.width * source_projection.object_scale,
                            node.width * source_projection.object_scale,
                        ),
                        (
                            motion.height_keyed,
                            "height",
                            Channel::Height,
                            motion.start.height * source_projection.object_scale,
                            node.height * source_projection.object_scale,
                        ),
                        (
                            motion.rotation_keyed,
                            "rotation",
                            Channel::Rotation,
                            motion.start.rotation_deg,
                            node.rotation_deg,
                        ),
                    ] {
                        if !keyed {
                            continue;
                        }
                        shot.motion.push(Invocation {
                            id: format!("geometry-{name}-{}", node.id.simple()),
                            span_id: span_id.clone(),
                            easing: motion.easing,
                            primitive: Primitive::GeometryTween {
                                target: target.clone(),
                                channel,
                                from: Operand::Number(from),
                                to: Operand::Number(to),
                            },
                        });
                    }
                    continue;
                }
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
                    easing: motion.easing,
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
            if let Some(reframe) = project
                .production_design
                .camera_reframes
                .iter()
                .find(|reframe| reframe.scene_id == scene.id)
            {
                let camera = CameraFilmContext {
                    scene_start: local_start,
                    shot_span: &shot_span,
                    frame_rate: options.frame_rate,
                    projection,
                    output: &output,
                };
                attach_camera_reframe(
                    &mut shot,
                    scene,
                    reframe,
                    &camera,
                    &mut spans,
                    &mut constraints,
                )?;
            }
            attach_entrance_motion(
                &mut shot,
                scene,
                local_start,
                projection,
                options.frame_rate,
                &mut spans,
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
                let mut shot = projected_shot(
                    scene,
                    &shot_context,
                    ShotIdentity {
                        archetype: intent.archetype,
                        shot_id: format!("shot-{}-beat-{}", scene.id.simple(), beat.id.simple()),
                        span_id: shot_span,
                        beat_scope: Some(beat.id),
                    },
                )?;
                attach_static_rotations(
                    &mut shot,
                    scene,
                    StaticRotationClock {
                        start: beat_start,
                        duration: beat.duration,
                        rate: options.frame_rate,
                        beat_scope: Some(beat.id),
                    },
                    &mut spans,
                    &mut constraints,
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

    let fonts =
        film_brand_fonts::project_fonts(project, &options.font_family, &options.mono_font_family)?;
    let mut segment_assets = assets(project, &sequences)?;
    for asset in fonts.assets {
        if let Some(existing) = segment_assets
            .iter()
            .find(|existing| existing.id == asset.id)
        {
            if existing != &asset {
                return Err(unsupported(
                    "Selected font and visual asset identities conflict",
                ));
            }
        } else {
            segment_assets.push(asset);
        }
    }
    if segment_assets.len() > 128 {
        return Err(unsupported(
            "Canonical Film supports at most 128 selected media and font assets",
        ));
    }
    // Select the new protocol only when a retained projected subject really
    // needs it. Normal-only projects keep their existing v1 wire and digest.
    let version = if sequences
        .iter()
        .flat_map(|sequence| &sequence.beats)
        .flat_map(|beat| &beat.shots)
        .flat_map(|shot| &shot.subjects)
        .any(|subject| !subject.blend_mode.is_normal())
    {
        AUTHORING_BLEND_VERSION
    } else {
        AUTHORING_VERSION
    };
    let film = Film {
        version,
        id: format!("mw-{}-{segment_index}", project.id.simple()),
        output,
        editorial: EditorialSystem {
            version: 1,
            font: fonts.primary,
            mono_font: fonts.mono,
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
    build_motion_canvas_segments_inner(project, deliverable_id, options, true)
}

/// Pure planning for callers that must inspect original receipts before new
/// SceneBoundary admission. This does not authorize staging or native effects.
pub(crate) fn plan_motion_canvas_segments(
    project: &Project,
    deliverable_id: Uuid,
    options: &FilmBuildOptions,
) -> NativeResult<Vec<MotionCanvasSegment>> {
    build_motion_canvas_segments_inner(project, deliverable_id, options, false)
}

fn build_motion_canvas_segments_inner(
    project: &Project,
    deliverable_id: Uuid,
    options: &FilmBuildOptions,
    guard_full_selection: bool,
) -> NativeResult<Vec<MotionCanvasSegment>> {
    project
        .validate()
        .map_err(|error| contract("Motionwright project is invalid", error))?;
    // Historical projects remain readable, but a tampered attached metric
    // notice must never be submitted as an authoritative native Film.
    project
        .validate_metric_source_notices()
        .map_err(|error| contract("MetricEvidence source disclosure is invalid", error))?;
    let intents = options.intent_map()?;
    // Resolve the existing FontRight/Asset contract before native effects.
    film_brand_fonts::project_fonts(project, &options.font_family, &options.mono_font_family)?;
    let deliverable = project
        .deliverables
        .iter()
        .find(|profile| profile.id == deliverable_id)
        .ok_or_else(|| invalid("Deliverable profile not found"))?;
    let runs = motion_canvas_runs(project, deliverable)?;
    if guard_full_selection {
        let scene_ids = runs
            .iter()
            .flat_map(|run| run.iter().map(|scene| scene.id))
            .collect::<Vec<_>>();
        crate::native_scene_boundary::guard_motion_scenes(
            project,
            deliverable_id,
            options,
            &scene_ids,
        )?;
    }
    let mut segments = runs
        .iter()
        .enumerate()
        .map(|(index, scenes)| {
            build_segment(project, scenes, index, deliverable, options, &intents)
        })
        .collect::<NativeResult<Vec<_>>>()?;
    // Burn-in is an explicit saved deliverable choice. Sidecar-only profiles
    // retain their historical native pixels and do not import voice audio.
    film_captions::attach_native_caption_cues(
        project,
        deliverable,
        options.frame_rate,
        &mut segments,
    )?;
    // Captions add native Text runs after segment construction.
    for segment in &segments {
        film_brand_fonts::validate_bundled_weights(&segment.film)?;
    }
    Ok(segments)
}

/// A source-timeline window mapped into the already realized canonical run.
/// The complete Film is retained; no per-scene replacement Film is authored.
#[derive(Debug, Clone)]
pub struct MotionCanvasWindowPlan {
    pub segment_index: usize,
    pub segment: MotionCanvasSegment,
    pub source_scene_id: Uuid,
    pub source_window: motionwright_domain::SourceFrameWindow,
    pub segment_window: motionwright_domain::SourceFrameWindow,
}

fn exact_window_frame(time: RationalTime, rate: Rate) -> NativeResult<u64> {
    time.validate()
        .map_err(|error| contract("Window source clock is invalid", error))?;
    rate.validate()
        .map_err(|error| contract("Window frame rate is invalid", error))?;
    let numerator = i128::from(time.num)
        .checked_mul(i128::from(rate.num))
        .ok_or_else(|| invalid("Window source frame numerator overflow"))?;
    let denominator = i128::from(time.den)
        .checked_mul(i128::from(rate.den))
        .ok_or_else(|| invalid("Window source frame denominator overflow"))?;
    if numerator < 0 || denominator <= 0 || numerator % denominator != 0 {
        return Err(unsupported(
            "Native window boundaries must lie on exact source frames",
        ));
    }
    u64::try_from(numerator / denominator)
        .map_err(|_| invalid("Native window frame index overflow"))
}

pub fn build_motion_canvas_window(
    project: &Project,
    deliverable_id: Uuid,
    source_scene_id: Uuid,
    source_window: motionwright_domain::SourceFrameWindow,
    options: &FilmBuildOptions,
) -> NativeResult<MotionCanvasWindowPlan> {
    if source_window.start >= source_window.end || source_window.end > 1_080_000 {
        return Err(invalid(
            "Native source window is empty or exceeds the source clock bound",
        ));
    }
    // Window planning is pure. Production admits its actual selected run after
    // checking the original request identity and before staging/native effects.
    let segments = plan_motion_canvas_segments(project, deliverable_id, options)?;
    let mut selected = segments
        .into_iter()
        .enumerate()
        .filter(|(_, segment)| segment.scene_ids.contains(&source_scene_id));
    let (segment_index, segment) = selected.next().ok_or_else(|| {
        unsupported("Selected source scene has no Motion Canvas run in this profile")
    })?;
    if selected.next().is_some() {
        return Err(invalid(
            "Source scene belongs to more than one canonical run",
        ));
    }
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == source_scene_id)
        .ok_or_else(|| invalid("Native window source scene is absent"))?;
    let source_start = exact_window_frame(scene.start, options.frame_rate)?;
    let source_end = exact_window_frame(segment_end(scene)?, options.frame_rate)?;
    if source_window.start < source_start || source_window.end > source_end {
        return Err(invalid(
            "Native window must remain inside the selected original source scene",
        ));
    }
    let realized = realize(&segment.film)
        .map_err(|error| contract("Native window Film realization failed", error))?;
    let interval = realized
        .schedule
        .interval(&uid("seqspan", source_scene_id))
        .map_err(|error| contract("Native window source interval is absent", error))?;
    if interval
        .duration()
        .map_err(|error| contract("Window interval duration failed", error))?
        != scene.duration
    {
        return Err(unsupported(
            "Native window mapping changed the original scene duration",
        ));
    }
    let local_start = exact_window_frame(interval.start, options.frame_rate)?;
    let local_end = exact_window_frame(interval.end, options.frame_rate)?;
    let start = local_start
        .checked_add(source_window.start - source_start)
        .ok_or_else(|| invalid("Native window local start overflow"))?;
    let end = local_start
        .checked_add(source_window.end - source_start)
        .ok_or_else(|| invalid("Native window local end overflow"))?;
    if start >= end || end > local_end || end > segment.frame_count {
        return Err(invalid(
            "Native window is outside the realized canonical run",
        ));
    }
    Ok(MotionCanvasWindowPlan {
        segment_index,
        segment,
        source_scene_id,
        source_window,
        segment_window: motionwright_domain::SourceFrameWindow { start, end },
    })
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
    fn native_subject_blend_selects_v2_only_for_saved_non_normal_leaves() {
        let (baseline, parent_id, child_id) = clipped_opacity_source();
        let options = fixture_options(&baseline);
        let profile_id = baseline.deliverables[0].id;
        let original = build_motion_canvas_segments(&baseline, profile_id, &options).unwrap();
        assert!(
            original
                .iter()
                .all(|segment| segment.film.version == AUTHORING_VERSION)
        );
        assert!(original.iter().all(|segment| {
            !serde_json::to_string(&segment.film)
                .unwrap()
                .contains("blend_mode")
        }));
        for (mode, native) in [
            (BlendMode::Multiply, SubjectBlendMode::Multiply),
            (BlendMode::Screen, SubjectBlendMode::Screen),
            (BlendMode::Add, SubjectBlendMode::Add),
        ] {
            let mut project = baseline.clone();
            let child = project.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap();
            child.style.blend_mode = mode;
            let before = project.clone();
            let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
            assert_eq!(segments.len(), original.len());
            assert_eq!(segments[0].frame_count, original[0].frame_count);
            let film = &segments[0].film;
            assert_eq!(film.version, AUTHORING_BLEND_VERSION);
            let shot = film
                .shots()
                .find(|shot| {
                    shot.subjects
                        .iter()
                        .any(|subject| subject.id == subject_id(child_id, None))
                })
                .unwrap();
            let selected = shot
                .subjects
                .iter()
                .find(|subject| subject.id == subject_id(child_id, None))
                .unwrap();
            let parent = shot
                .subjects
                .iter()
                .find(|subject| subject.id == subject_id(parent_id, None))
                .unwrap();
            assert_eq!(selected.blend_mode, native);
            assert_eq!(selected.parent.as_deref(), Some(parent.id.as_str()));
            assert_eq!(parent.blend_mode, SubjectBlendMode::Normal);
            assert!(parent.clip_intentional);
            let baseline_subject = original[0]
                .film
                .shots()
                .flat_map(|shot| &shot.subjects)
                .find(|subject| subject.id == selected.id)
                .unwrap();
            assert_eq!(selected.layout, baseline_subject.layout);
            assert_eq!(selected.layer, baseline_subject.layer);
            let encoded = serde_json::to_vec(film).unwrap();
            assert_eq!(serde_json::from_slice::<Film>(&encoded).unwrap(), *film);
            let mut forbidden_v1 = film.clone();
            forbidden_v1.version = AUTHORING_VERSION;
            assert!(forbidden_v1.validate().is_err());
            assert_eq!(project, before);
        }
    }

    #[test]
    fn native_blend_refuses_unmapped_group_fixed_text_and_rotation() {
        let (baseline, parent_id, child_id) = clipped_opacity_source();
        let profile_id = baseline.deliverables[0].id;
        for mode in ["group", "hard_lines", "rotation"] {
            let mut source = baseline.clone();
            match mode {
                "group" => {
                    source.scenes[0]
                        .nodes
                        .iter_mut()
                        .find(|node| node.id == parent_id)
                        .unwrap()
                        .style
                        .blend_mode = BlendMode::Screen
                }
                "hard_lines" => {
                    let text = source.scenes[0]
                        .nodes
                        .iter_mut()
                        .find(|node| node.kind == "text")
                        .unwrap();
                    text.text = Some("One\nTwo".into());
                    text.style.blend_mode = BlendMode::Multiply;
                }
                "rotation" => {
                    let child = source.scenes[0]
                        .nodes
                        .iter_mut()
                        .find(|node| node.id == child_id)
                        .unwrap();
                    child.rotation_deg = 15.0;
                    child.style.blend_mode = BlendMode::Add;
                }
                _ => unreachable!(),
            }
            let before = source.clone();
            assert!(
                build_motion_canvas_segments(&source, profile_id, &fixture_options(&source))
                    .is_err(),
                "{mode}"
            );
            assert_eq!(source, before);
        }
    }

    #[test]
    fn native_blend_reads_the_exact_saved_project_after_sqlite_reopen() {
        use motionwright_domain::RevisionStamp;
        use motionwright_service::StudioService;
        let dir = tempfile::tempdir().unwrap();
        let db = dir.path().join("blend.sqlite3");
        let service = StudioService::open(&db).unwrap();
        let initial = service
            .create_project("Original native blend source")
            .unwrap();
        let mut saved = service
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "blend-scene",
                &Change::AddScene {
                    name: "Original overlap".into(),
                    objective: "Blend saved subjects".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let scene_id = saved.scenes[0].id;
        let mut base = node("shape", None);
        base.style.fill = Some("#6080A0".into());
        base.style.stroke = None;
        base.style.stroke_width = 0.0;
        let mut overlay = node("rectangle", None);
        overlay.x += 80.0;
        overlay.z_index = base.z_index + 1;
        overlay.style.fill = Some("#C06040".into());
        overlay.style.stroke = None;
        overlay.style.stroke_width = 0.0;
        overlay.style.blend_mode = BlendMode::Multiply;
        let overlay_id = overlay.id;
        for (id, source) in [("blend-base", base), ("blend-overlay", overlay)] {
            saved = service
                .apply(
                    saved.id,
                    &RevisionStamp::from(&saved),
                    id,
                    &Change::AddCanvasNode {
                        scene_id,
                        node: source,
                    },
                )
                .unwrap()
                .project;
        }
        let options = fixture_options(&saved);
        let film = build_motion_canvas_segments(&saved, saved.deliverables[0].id, &options)
            .unwrap()
            .remove(0)
            .film;
        assert_eq!(film.version, AUTHORING_BLEND_VERSION);
        assert_eq!(
            film.shots()
                .flat_map(|shot| &shot.subjects)
                .find(|subject| subject.id == subject_id(overlay_id, None))
                .unwrap()
                .blend_mode,
            SubjectBlendMode::Multiply
        );
        drop(service);
        let reopened = StudioService::open(&db).unwrap();
        let original = reopened.project(saved.id).unwrap();
        assert_eq!(original, saved);
        let after = build_motion_canvas_segments(&original, original.deliverables[0].id, &options)
            .unwrap()
            .remove(0)
            .film;
        assert_eq!(after, film);
        assert_eq!(reopened.project(saved.id).unwrap(), saved);
    }

    #[test]
    fn saved_static_shape_rotation_compiles_at_every_original_shot_start() {
        use semwright_motion_authoring::NativeOp;
        for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
            for clipped in [false, true] {
                let (mut project, _parent, child_id) = clipped_opacity_source();
                let child = project.scenes[0]
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == child_id)
                    .unwrap();
                child.x = 650.0;
                child.y = 400.0;
                child.width = 240.0;
                child.height = 120.0;
                child.rotation_deg = -33.0;
                if !clipped {
                    child.parent_id = None;
                }
                project.deliverables[0].width = width;
                project.deliverables[0].height = height;
                project.deliverables[0].framing_strategy = FramingStrategy::Replan;
                let scene_id = project.scenes[0].id;
                for (label, start) in [("First", RationalTime::ZERO), ("Second", RationalTime::ONE)]
                {
                    project
                        .apply_change(&Change::UpsertSceneBeat {
                            scene_id,
                            beat: SceneBeat {
                                id: Uuid::now_v7(),
                                label: label.into(),
                                objective: label.into(),
                                start,
                                duration: RationalTime::ONE,
                            },
                        })
                        .unwrap();
                }
                let before = project.clone();
                let segments = build_motion_canvas_segments(
                    &project,
                    project.deliverables[0].id,
                    &fixture_options(&project),
                )
                .unwrap();
                let film = &segments[0].film;
                let realized = realize(film).unwrap();
                for beat in &project.scenes[0].beats {
                    let target = subject_id(child_id, Some(beat.id));
                    let rotation=realized.instructions.iter().filter(|instruction|matches!(
                        &instruction.operation,NativeOp::Set {target:actual,channel:Channel::Rotation,..} if actual==&target
                    )).collect::<Vec<_>>();
                    assert_eq!(rotation.len(), 1);
                    assert_eq!(rotation[0].start, beat.start);
                    assert_eq!(rotation[0].duration, RationalTime::ZERO);
                    assert!(matches!(&rotation[0].operation,NativeOp::Set {
                        value:Operand::Number(value),..} if *value == -33.0));
                }
                assert_eq!(project, before);
            }
        }
    }

    #[test]
    fn saved_static_rotation_preserves_camera_translation_and_rejects_unsafe_geometry() {
        use semwright_motion_authoring::NativeOp;
        let mut project = fixture_project();
        let mut shape = node("rectangle", None);
        shape.x = 720.0;
        shape.y = 440.0;
        shape.width = 250.0;
        shape.height = 120.0;
        shape.rotation_deg = 27.0;
        let node_id = shape.id;
        let scene_id = project.scenes[0].id;
        project.scenes[0].nodes = vec![shape];
        let camera_id = Uuid::now_v7();
        project
            .apply_change(&Change::UpsertCameraReframe {
                reframe: motionwright_domain::CameraReframe {
                    id: camera_id,
                    scene_id,
                    component_version: 1,
                    start_center_x: 960.0,
                    start_center_y: 540.0,
                    end_center_x: 980.0,
                    end_center_y: 550.0,
                    duration_frames: 30,
                    detail_return: None,
                },
            })
            .unwrap();
        let before = project.clone();
        let options = fixture_options(&project);
        let profile = project.deliverables[0].id;
        let segments = build_motion_canvas_segments(&project, profile, &options).unwrap();
        let shot = &segments[0].film.sequences[0].beats[0].shots[0];
        let target = subject_id(node_id, None);
        let frame = uid("camera-frame", camera_id);
        let actual = shot
            .subjects
            .iter()
            .find(|subject| subject.id == target)
            .unwrap();
        assert_eq!(actual.parent.as_deref(), Some(frame.as_str()));
        let realized = realize(&segments[0].film).unwrap();
        assert!(realized.instructions.iter().any(
            |instruction| matches!(&instruction.operation,NativeOp::Set {
                target:actual,channel:Channel::Rotation,value:Operand::Number(value),
            } if actual==&target && *value==27.0)
        ));
        assert_eq!(project, before);

        let mut unsafe_project = fixture_project();
        let mut unsafe_shape = node("rectangle", None);
        unsafe_shape.x = 98.0;
        unsafe_shape.y = 100.0;
        unsafe_shape.width = 100.0;
        unsafe_shape.height = 400.0;
        unsafe_project.scenes[0].nodes = vec![unsafe_shape];
        let opts = fixture_options(&unsafe_project);
        let profile = unsafe_project.deliverables[0].id;
        assert!(build_motion_canvas_segments(&unsafe_project, profile, &opts).is_ok());
        unsafe_project.scenes[0].nodes[0].rotation_deg = 90.0;
        assert!(build_motion_canvas_segments(&unsafe_project, profile, &opts).is_err());
        for angle in [361.0, -361.0] {
            let mut invalid_project = fixture_project();
            let mut shape = node("rectangle", None);
            shape.rotation_deg = angle;
            invalid_project.scenes[0].nodes = vec![shape];
            assert!(
                build_motion_canvas_segments(
                    &invalid_project,
                    invalid_project.deliverables[0].id,
                    &fixture_options(&invalid_project)
                )
                .is_err()
            );
        }
    }

    #[test]
    fn contextual_text_follows_curves_holds_and_own_opacity_without_metric_or_source_writes() {
        use motionwright_domain::{
            CAMERA_REFRAME_VERSION, CameraReframe, NodeRelation, RelationKind,
        };
        for camera in [false, true] {
            let mut project = fixture_project();
            let mut root = node("rectangle", None);
            root.x = 720.0;
            root.y = 400.0;
            root.width = 240.0;
            root.height = 160.0;
            for (at, x, y, interpolation) in [
                (
                    RationalTime::ZERO,
                    520.0,
                    380.0,
                    MotionInterpolation::Linear,
                ),
                (
                    RationalTime::new(1, 2).unwrap(),
                    620.0,
                    420.0,
                    MotionInterpolation::EaseOutCubic,
                ),
                (RationalTime::ONE, 580.0, 390.0, MotionInterpolation::Hold),
                (
                    RationalTime::new(3, 2).unwrap(),
                    root.x,
                    root.y,
                    MotionInterpolation::Linear,
                ),
            ] {
                for (property, value) in [
                    (MotionProperty::X, x),
                    (MotionProperty::Y, y),
                    (MotionProperty::Width, root.width),
                    (MotionProperty::Height, root.height),
                    (MotionProperty::RotationDeg, 0.0),
                ] {
                    root.keyframes.push(CanvasKeyframe {
                        at,
                        property,
                        value,
                        interpolation,
                    });
                }
            }
            let mut label = node("text", Some("Measured\nsource"));
            label.x = 860.0;
            label.y = 500.0;
            label.width = 320.0;
            label.height = 100.0;
            label.z_index = 2;
            label.style.font_size = Some(24.0);
            label.relations.push(NodeRelation {
                id: Uuid::now_v7(),
                target_id: root.id,
                kind: RelationKind::Follow,
            });
            for (at, value, interpolation) in [
                (RationalTime::ZERO, 0.0, MotionInterpolation::Linear),
                (
                    RationalTime::new(1, 2).unwrap(),
                    1.0,
                    MotionInterpolation::Linear,
                ),
                (RationalTime::ONE, 1.0, MotionInterpolation::Hold),
                (
                    RationalTime::new(9, 5).unwrap(),
                    0.0,
                    MotionInterpolation::EaseOutCubic,
                ),
            ] {
                label.keyframes.push(CanvasKeyframe {
                    at,
                    property: MotionProperty::Opacity,
                    value,
                    interpolation,
                });
            }
            project.scenes[0].nodes = vec![root.clone(), label.clone()];
            // Editorial boundaries cross both the exact translation and the
            // independent text fade, without changing the scene-local clock.
            project.scenes[0].beats = vec![
                SceneBeat {
                    id: Uuid::now_v7(),
                    label: "Follow lead".into(),
                    objective: "Keep source timing".into(),
                    start: RationalTime::ZERO,
                    duration: RationalTime::new(2, 3).unwrap(),
                },
                SceneBeat {
                    id: Uuid::now_v7(),
                    label: "Follow evidence".into(),
                    objective: "Keep source timing".into(),
                    start: RationalTime::new(2, 3).unwrap(),
                    duration: RationalTime::new(4, 3).unwrap(),
                },
            ];
            if camera {
                project
                    .production_design
                    .camera_reframes
                    .push(CameraReframe {
                        id: Uuid::now_v7(),
                        scene_id: project.scenes[0].id,
                        component_version: CAMERA_REFRAME_VERSION,
                        start_center_x: 920.0,
                        start_center_y: 540.0,
                        end_center_x: 960.0,
                        end_center_y: 540.0,
                        duration_frames: 30,
                        detail_return: None,
                    });
            }
            let id = project.deliverables[0].id;
            for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
                project.deliverables[0].width = width;
                project.deliverables[0].height = height;
                project.deliverables[0].framing_strategy = FramingStrategy::Replan;
                let before = project.clone();
                let segments =
                    build_motion_canvas_segments(&project, id, &fixture_options(&project)).unwrap();
                let film = &segments[0].film;
                let shot = &film.sequences[0].beats[0].shots[0];
                let target = subject_id(label.id, None);
                let mut tweens = 0;
                let mut sets = 0;
                let mut fades = 0;
                for motion in &shot.motion {
                    match &motion.primitive {
                        Primitive::GeometryTween {
                            target: actual,
                            channel,
                            ..
                        } if actual == &target => {
                            assert_eq!(*channel, Channel::Position);
                            tweens += 1;
                        }
                        Primitive::GeometrySet {
                            target: actual,
                            channel,
                            ..
                        } if actual == &target => {
                            assert_eq!(*channel, Channel::Position);
                            sets += 1;
                        }
                        Primitive::FadeIn { target: actual }
                        | Primitive::FadeOut { target: actual }
                            if actual == &target =>
                        {
                            fades += 1
                        }
                        Primitive::Settle { target: actual, .. } if actual == &target => {
                            panic!("Follow must not reset text orientation")
                        }
                        _ => {}
                    }
                }
                assert_eq!((tweens, sets, fades), (2, 1, 2));
                realize(film).unwrap();
                assert_eq!(project, before);
                assert_eq!(project.scenes[0].nodes[1], label);
            }
        }
    }

    #[test]
    fn paired_hold_text_follow_sets_exact_frames_without_resizing_fixed_text() {
        use motionwright_domain::{NodeRelation, RelationKind};
        let mut project = fixture_project();
        let mut root = node("rectangle", None);
        root.x = 720.0;
        root.y = 400.0;
        root.width = 160.0;
        root.height = 100.0;
        for (at, x, curve) in [
            (RationalTime::ZERO, 620.0, MotionInterpolation::Linear),
            (RationalTime::ONE, 720.0, MotionInterpolation::Hold),
        ] {
            for (property, value) in [(MotionProperty::X, x), (MotionProperty::Y, root.y)] {
                root.keyframes.push(CanvasKeyframe {
                    at,
                    property,
                    value,
                    interpolation: curve,
                });
            }
        }
        let mut label = node("text", Some("Exact hold"));
        label.x = 920.0;
        label.y = 500.0;
        label.width = 240.0;
        label.height = 80.0;
        label.style.font_size = Some(24.0);
        label.relations.push(NodeRelation {
            id: Uuid::now_v7(),
            target_id: root.id,
            kind: RelationKind::Follow,
        });
        project.scenes[0].nodes = vec![root, label.clone()];
        let before = project.clone();
        let segments = build_motion_canvas_segments(
            &project,
            project.deliverables[0].id,
            &fixture_options(&project),
        )
        .unwrap();
        let film = &segments[0].film;
        let target = subject_id(label.id, None);
        let sets=film.sequences[0].beats[0].shots[0].motion.iter().filter(|motion|
            matches!(&motion.primitive,Primitive::GeometrySet {target:actual,channel:Channel::Position,..} if actual==&target)).count();
        assert_eq!(sets, 2);
        realize(film).unwrap();
        assert_eq!(project, before);
    }

    #[test]
    fn authored_follow_chain_compiles_exact_native_translation_without_saved_keys() {
        use motionwright_domain::{NodeRelation, RelationKind};
        for (curve, easing) in [
            (MotionInterpolation::Linear, MotionEasing::Linear),
            (MotionInterpolation::EaseOutCubic, MotionEasing::OutCubic),
        ] {
            let mut project = fixture_project();
            let mut a = node("rectangle", None);
            a.x = 620.0;
            a.y = 340.0;
            a.width = 160.0;
            a.height = 100.0;
            let mut b = a.clone();
            b.id = Uuid::now_v7();
            b.x = 920.0;
            b.y = 480.0;
            let mut c = b.clone();
            c.id = Uuid::now_v7();
            c.x = 1200.0;
            c.y = 590.0;
            for (at, x, y, interpolation) in [
                (
                    RationalTime::ZERO,
                    420.0,
                    240.0,
                    MotionInterpolation::Linear,
                ),
                (RationalTime::ONE, a.x, a.y, curve),
            ] {
                for (property, value) in [(MotionProperty::X, x), (MotionProperty::Y, y)] {
                    a.keyframes.push(CanvasKeyframe {
                        at,
                        property,
                        value,
                        interpolation,
                    });
                }
            }
            b.relations = vec![NodeRelation {
                id: Uuid::now_v7(),
                kind: RelationKind::Follow,
                target_id: a.id,
            }];
            c.relations = vec![NodeRelation {
                id: Uuid::now_v7(),
                kind: RelationKind::Follow,
                target_id: b.id,
            }];
            project.scenes[0].nodes = vec![c.clone(), b.clone(), a.clone()];
            let options = fixture_options(&project);
            let id = project.deliverables[0].id;
            for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
                project.deliverables[0].width = width;
                project.deliverables[0].height = height;
                project.deliverables[0].framing_strategy = FramingStrategy::Replan;
                let before = project.clone();
                let segments = build_motion_canvas_segments(&project, id, &options).unwrap();
                let film = &segments[0].film;
                let shot = &film.sequences[0].beats[0].shots[0];
                assert_eq!(shot.motion.len(), 3);
                let expected_x = -200.0 * f64::from(width) / 1920.0;
                let expected_y = -100.0 * f64::from(height) / 1080.0;
                for node in [&a, &b, &c] {
                    let target = subject_id(node.id, None);
                    let invocation = shot.motion.iter().find(|motion|
                        matches!(&motion.primitive,Primitive::Settle {target: actual,..} if actual == &target)
                    ).unwrap();
                    assert_eq!(invocation.easing, easing);
                    let Primitive::Settle {
                        offset, rotation, ..
                    } = &invocation.primitive
                    else {
                        unreachable!()
                    };
                    assert!((offset.x - expected_x).abs() <= 1e-6);
                    assert!((offset.y - expected_y).abs() <= 1e-6);
                    assert_eq!(*rotation, 0.0);
                }
                realize(film).unwrap();
                assert_eq!(project, before);
            }
            for case in [
                "cycle",
                "missing",
                "conflict",
                "locked",
                "off-frame",
                "unsupported-curve",
                "static",
                "independent-keys",
            ] {
                let mut invalid_project = project.clone();
                let nodes = &mut invalid_project.scenes[0].nodes;
                match case {
                    "cycle" => {
                        nodes[2].keyframes.clear();
                        nodes[2].relations.push(NodeRelation {
                            id: Uuid::now_v7(),
                            kind: RelationKind::Follow,
                            target_id: c.id,
                        });
                    }
                    "missing" => nodes[1].relations[0].target_id = Uuid::now_v7(),
                    "conflict" => nodes[1].relations.push(NodeRelation {
                        id: Uuid::now_v7(),
                        kind: RelationKind::AlignTop,
                        target_id: a.id,
                    }),
                    "locked" => {
                        nodes[1]
                            .property_locks
                            .insert(motionwright_domain::NodeProperty::Position);
                    }
                    "off-frame" => {
                        nodes[2].keyframes[2].at = RationalTime::new(1, 31).unwrap();
                        nodes[2].keyframes[3].at = RationalTime::new(1, 31).unwrap();
                    }
                    "unsupported-curve" => {
                        nodes[2].keyframes[2].interpolation = MotionInterpolation::EaseInOut;
                        nodes[2].keyframes[3].interpolation = MotionInterpolation::EaseInOut;
                    }
                    "static" => nodes[2].keyframes.clear(),
                    "independent-keys" => nodes[1].keyframes = a.keyframes.clone(),
                    _ => unreachable!(),
                }
                let before = invalid_project.clone();
                assert!(
                    build_motion_canvas_segments(&invalid_project, id, &options).is_err(),
                    "{case}"
                );
                assert_eq!(invalid_project, before);
            }
        }
    }

    #[test]
    fn authored_static_alignment_preserves_exact_output_box_edges_and_source() {
        use motionwright_domain::{NodeRelation, RelationKind};
        for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
            let mut project = fixture_project();
            let mut a = node("rectangle", None);
            a.x = 650.0;
            a.y = 350.0;
            a.width = 300.0;
            a.height = 200.0;
            let mut b = a.clone();
            b.id = Uuid::now_v7();
            b.x = 1000.0;
            b.y = 430.0;
            b.width = 150.0;
            b.height = 100.0;
            let mut c = b.clone();
            c.id = Uuid::now_v7();
            c.width = 100.0;
            c.height = 80.0;
            b.relations = [RelationKind::AlignRight, RelationKind::AlignBottom]
                .into_iter()
                .map(|kind| NodeRelation {
                    id: Uuid::now_v7(),
                    kind,
                    target_id: a.id,
                })
                .collect();
            c.relations = [RelationKind::AlignCenterX, RelationKind::AlignTop]
                .into_iter()
                .map(|kind| NodeRelation {
                    id: Uuid::now_v7(),
                    kind,
                    target_id: b.id,
                })
                .collect();
            project.scenes[0].nodes = vec![c.clone(), b.clone(), a.clone()];
            project.deliverables[0].width = width;
            project.deliverables[0].height = height;
            project.deliverables[0].framing_strategy = FramingStrategy::Replan;
            let before = project.clone();
            let options = fixture_options(&project);
            let segments =
                build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
                    .unwrap();
            let shot = &segments[0].film.sequences[0].beats[0].shots[0];
            let actual = |id| {
                let subject = shot
                    .subjects
                    .iter()
                    .find(|item| item.id == subject_id(id, None))
                    .unwrap();
                match &subject.layout {
                    SpatialIntent::Fixed { position, size } => {
                        (position.x, position.y, size.width, size.height)
                    }
                    _ => panic!("Alignment must compile to an actual fixed native box"),
                }
            };
            let (ax, ay, aw, ah) = actual(a.id);
            let (bx, by, bw, bh) = actual(b.id);
            let (cx, cy, _cw, ch) = actual(c.id);
            assert!(((bx + bw / 2.0) - (ax + aw / 2.0)).abs() <= 1e-6);
            assert!(((by + bh / 2.0) - (ay + ah / 2.0)).abs() <= 1e-6);
            assert!((cx - bx).abs() <= 1e-6);
            assert!(((cy - ch / 2.0) - (by - bh / 2.0)).abs() <= 1e-6);
            realize(&segments[0].film).unwrap();
            assert_eq!(project, before);

            // Every failure must be resolved or refused before a Native effect.
            for case in ["cycle", "competing", "follow", "animated", "locked"] {
                let mut invalid_project = before.clone();
                let nodes = &mut invalid_project.scenes[0].nodes;
                match case {
                    "cycle" => nodes[2].relations.push(NodeRelation {
                        id: Uuid::now_v7(),
                        kind: RelationKind::AlignLeft,
                        target_id: b.id,
                    }),
                    "competing" => nodes[1].relations.push(NodeRelation {
                        id: Uuid::now_v7(),
                        kind: RelationKind::AlignLeft,
                        target_id: a.id,
                    }),
                    "follow" => nodes[1].relations[0].kind = RelationKind::Follow,
                    "animated" => nodes[2].keyframes.push(CanvasKeyframe {
                        at: RationalTime::ZERO,
                        property: MotionProperty::X,
                        value: a.x,
                        interpolation: MotionInterpolation::Linear,
                    }),
                    "locked" => {
                        nodes[1]
                            .property_locks
                            .insert(motionwright_domain::NodeProperty::Position);
                    }
                    _ => unreachable!(),
                }
                let invalid_before = invalid_project.clone();
                assert!(
                    build_motion_canvas_segments(
                        &invalid_project,
                        invalid_project.deliverables[0].id,
                        &options
                    )
                    .is_err(),
                    "{case}"
                );
                assert_eq!(invalid_project, invalid_before);
            }
        }
    }

    #[test]
    fn authored_rectangular_clip_group_has_real_native_parent_and_local_child_geometry() {
        let mut project = fixture_project();
        let scene_id = project.scenes[0].id;
        let mut group = node("clip_group", None);
        group.x = 600.0;
        group.y = 340.0;
        group.width = 700.0;
        group.height = 400.0;
        group.z_index = 2;
        group.style = NodeStyle::default();
        let parent_id = group.id;
        let mut child = node("rectangle", None);
        child.name = "Clipped child with editable world coordinates".into();
        child.parent_id = Some(parent_id);
        child.x = 950.0;
        child.y = 450.0;
        child.width = 560.0;
        child.height = 160.0;
        child.z_index = 3;
        child.style.fill = Some("#E0A940".into());
        let child_id = child.id;
        for next in [group.clone(), child.clone()] {
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: next,
                })
                .unwrap();
        }
        let options = fixture_options(&project);
        let film = &build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap()[0]
            .film;
        let shot = &film.sequences[0].beats[0].shots[0];
        let group_subject = shot
            .subjects
            .iter()
            .find(|subject| subject.id == subject_id(parent_id, None))
            .unwrap();
        let child_subject = shot
            .subjects
            .iter()
            .find(|subject| subject.id == subject_id(child_id, None))
            .unwrap();
        assert!(matches!(&group_subject.content, SubjectContent::Group));
        assert!(
            group_subject.clip_intentional,
            "Native Group must actually own the clipping plane"
        );
        assert_eq!(
            child_subject.parent.as_deref(),
            Some(group_subject.id.as_str())
        );
        assert!(!child_subject.clip_intentional);
        match &child_subject.layout {
            SpatialIntent::Fixed { position, .. } => {
                assert!((position.x - 280.0).abs() < 1.0e-6);
                assert!((position.y + 10.0).abs() < 1.0e-6);
            }
            _ => panic!("Editable clipped child was not projected to exact local Fixed geometry"),
        }
        let live = &project.scenes[0].nodes;
        let after_group = live.iter().find(|node| node.id == parent_id).unwrap();
        let after_child = live.iter().find(|node| node.id == child_id).unwrap();
        assert_eq!(
            (
                after_group.x,
                after_group.y,
                after_group.width,
                after_group.height
            ),
            (600.0, 340.0, 700.0, 400.0)
        );
        assert_eq!(
            (
                after_child.x,
                after_child.y,
                after_child.width,
                after_child.height
            ),
            (950.0, 450.0, 560.0, 160.0)
        );
        assert_eq!(after_child.parent_id, Some(parent_id));
        realize(film).expect("Pinned Native grammar must admit the authored rectangular clip");
    }

    #[test]
    fn static_native_group_preserves_world_pose_motion_and_rejects_interleaved_layers() {
        let (mut project, parent_id, child_id) = clipped_opacity_source();
        let scene = &mut project.scenes[0];
        let group = scene
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap();
        group.kind = "group".into();
        let group = group.clone();
        let child = scene
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap();
        set_clipped_position_keys(child, 1140.0, MotionInterpolation::EaseOutCubic);
        let child = child.clone();
        let source = project.clone();
        let options = fixture_options(&project);
        let segments =
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap();
        let shot = &segments[0].film.sequences[0].beats[0].shots[0];
        let parent = shot
            .subjects
            .iter()
            .find(|item| item.id == subject_id(parent_id, None))
            .unwrap();
        let actual = shot
            .subjects
            .iter()
            .find(|item| item.id == subject_id(child_id, None))
            .unwrap();
        assert!(!parent.clip_intentional);
        assert_eq!(actual.parent.as_deref(), Some(parent.id.as_str()));
        match (&parent.layout, &actual.layout) {
            (
                SpatialIntent::Fixed { position: root, .. },
                SpatialIntent::Fixed {
                    position: local, ..
                },
            ) => {
                assert!((root.x + local.x - (child.x + child.width / 2.0 - 960.0)).abs() < 1e-6);
                assert!((root.y + local.y - (child.y + child.height / 2.0 - 540.0)).abs() < 1e-6);
                assert!((root.x - (group.x + group.width / 2.0 - 960.0)).abs() < 1e-6);
            }
            _ => panic!("Group child must retain local fixed geometry"),
        }
        assert!(shot.motion.iter().any(|item| matches!(&item.primitive,
            Primitive::Settle { target, .. } if target == &actual.id)));
        realize(&segments[0].film).unwrap();
        assert_eq!(project, source);
        // This exact saved child layer is above the independent root, while
        // its group is below it. A renderer subtree cannot preserve both.
        project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .z_index = -2;
        assert!(
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).is_err()
        );
    }

    #[test]
    fn nested_static_group_clip_preserves_exact_world_pose_camera_and_independent_motion() {
        for curve in [
            MotionInterpolation::Linear,
            MotionInterpolation::EaseOutCubic,
            MotionInterpolation::Hold,
        ] {
            let (mut project, outer_id, child_id) = clipped_opacity_source();
            let scene = &mut project.scenes[0];
            let outer = scene
                .nodes
                .iter()
                .find(|node| node.id == outer_id)
                .unwrap()
                .clone();
            let mut middle = outer.clone();
            middle.id = Uuid::now_v7();
            middle.kind = "group".into();
            middle.parent_id = Some(outer_id);
            middle.x = 520.0;
            middle.y = 360.0;
            middle.width = 520.0;
            middle.height = 340.0;
            middle.z_index = 3;
            let middle_id = middle.id;
            let mut inner = middle.clone();
            inner.id = Uuid::now_v7();
            inner.kind = "clip_group".into();
            inner.parent_id = Some(middle_id);
            inner.x = 600.0;
            inner.y = 380.0;
            inner.width = 540.0;
            inner.height = 280.0;
            inner.z_index = 4;
            let inner_id = inner.id;
            let child = scene
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap();
            child.parent_id = Some(inner_id);
            child.z_index = 5;
            for (property, from, to) in [
                (MotionProperty::X, child.x - 20.0, child.x),
                (MotionProperty::Y, child.y - 20.0, child.y),
                (MotionProperty::Width, 280.0, child.width),
                (MotionProperty::Height, 90.0, child.height),
                (MotionProperty::RotationDeg, -18.0, child.rotation_deg),
            ] {
                child.keyframes.extend([
                    CanvasKeyframe {
                        at: RationalTime::ZERO,
                        property,
                        value: from,
                        interpolation: MotionInterpolation::Linear,
                    },
                    CanvasKeyframe {
                        at: RationalTime::ONE,
                        property,
                        value: to,
                        interpolation: curve,
                    },
                ]);
            }
            for (at, value, interpolation) in [
                (RationalTime::ZERO, 0.0, MotionInterpolation::Linear),
                (
                    RationalTime::new(1, 2).unwrap(),
                    1.0,
                    MotionInterpolation::EaseOutCubic,
                ),
                (RationalTime::ONE, 1.0, MotionInterpolation::Hold),
                (
                    RationalTime::new(3, 2).unwrap(),
                    0.0,
                    MotionInterpolation::Linear,
                ),
            ] {
                child.keyframes.push(CanvasKeyframe {
                    at,
                    property: MotionProperty::Opacity,
                    value,
                    interpolation,
                });
            }
            scene.nodes.extend([middle, inner]);
            let camera_id = Uuid::now_v7();
            let reframe = CameraReframe {
                id: camera_id,
                scene_id: scene.id,
                component_version: 1,
                start_center_x: scene.camera.center_x - 18.0,
                start_center_y: scene.camera.center_y - 8.0,
                end_center_x: scene.camera.center_x,
                end_center_y: scene.camera.center_y,
                duration_frames: 45,
                detail_return: None,
            };
            project
                .apply_change(&Change::UpsertCameraReframe { reframe })
                .unwrap();
            let original = project.clone();
            let options = fixture_options(&project);
            let segments =
                build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
                    .unwrap();
            let shot = &segments[0].film.sequences[0].beats[0].shots[0];
            let scene = &project.scenes[0];
            for (id, parent_id) in [
                (outer_id, None),
                (middle_id, Some(outer_id)),
                (inner_id, Some(middle_id)),
                (child_id, Some(inner_id)),
            ] {
                let authored = scene.nodes.iter().find(|node| node.id == id).unwrap();
                let actual = shot
                    .subjects
                    .iter()
                    .find(|subject| subject.id == subject_id(id, None))
                    .unwrap();
                let expected_parent = parent_id
                    .map(|parent| subject_id(parent, None))
                    .or_else(|| Some(uid("camera-frame", camera_id)));
                assert_eq!(actual.parent, expected_parent);
                assert_eq!(actual.clip_intentional, id == outer_id || id == inner_id);
                let mut x = 0.0;
                let mut y = 0.0;
                let mut cursor = Some(actual);
                while let Some(subject) = cursor {
                    match &subject.layout {
                        SpatialIntent::Fixed { position, .. } => {
                            x += position.x;
                            y += position.y;
                        }
                        _ => panic!("Nested native geometry must remain literal and editable"),
                    }
                    cursor = subject.parent.as_ref().and_then(|parent| {
                        shot.subjects
                            .iter()
                            .find(|candidate| &candidate.id == parent)
                    });
                }
                assert!((x - (authored.x + authored.width / 2.0 - 960.0)).abs() < 1e-6);
                assert!((y - (authored.y + authored.height / 2.0 - 540.0)).abs() < 1e-6);
            }
            assert!(
                shot.motion
                    .iter()
                    .any(|invocation| matches!(&invocation.primitive,
                Primitive::FadeIn { target } if target == &subject_id(child_id, None)))
            );
            let realized = realize(&segments[0].film).expect(
                "Pinned Film must preserve nested clipping, geometry, camera and visibility",
            );
            let alpha = realized
                .instructions
                .iter()
                .filter(|instruction| {
                    matches!(
                        &instruction.operation,
                        semwright_motion_authoring::NativeOp::Tween {
                            channel: Channel::Opacity,
                            ..
                        }
                    )
                })
                .collect::<Vec<_>>();
            assert_eq!(alpha.len(), 2, "Hold emits no additional alpha write");
            assert_eq!(alpha[0].start, RationalTime::ZERO);
            assert_eq!(alpha[1].start, RationalTime::ONE);
            for instruction in &alpha {
                assert_eq!(instruction.duration, RationalTime::new(1, 2).unwrap());
            }
            assert_eq!(alpha[0].easing, MotionEasing::OutCubic);
            assert_eq!(alpha[1].easing, MotionEasing::Linear);
            assert!(matches!(
                &alpha[0].operation,
                semwright_motion_authoring::NativeOp::Tween {
                    from: Some(Operand::Number(0.0)),
                    to: Operand::Number(1.0),
                    ..
                }
            ));
            assert!(matches!(
                &alpha[1].operation,
                semwright_motion_authoring::NativeOp::Tween {
                    from: None,
                    to: Operand::Number(0.0),
                    ..
                }
            ));
            assert_eq!(
                project, original,
                "Projection must not flatten or rewrite the saved tree"
            );
        }
    }

    fn stroke_projection_fixture(
        kind: &str,
        first_width: f64,
        second_width: f64,
    ) -> (Project, Uuid, FilmBuildOptions) {
        let mut project = Project::new("Native stroke projection fixture").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Two independently authored outlines".into(),
                objective: "Preserve inactive and visible strokes".into(),
                duration_seconds: 2,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        let mut first = node(kind, None);
        first.style.stroke_width = first_width;
        first.style.stroke = Some("#FF8800".into());
        let mut second = node("rectangle", None);
        second.x = 960.0;
        second.y = 540.0;
        second.style.stroke_width = second_width;
        second.style.stroke = Some("#2080E0".into());
        for shape in [first, second] {
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: shape,
                })
                .unwrap();
        }
        let profile_id = project
            .deliverables
            .iter()
            .find(|profile| profile.name == "Master 16:9")
            .unwrap()
            .id;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: vec![SceneFilmIntent {
                scene_id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            }],
        };
        (project, profile_id, options)
    }

    fn native_subject_stroke(subject: &Subject) -> Option<&str> {
        match &subject.content {
            SubjectContent::Rectangle { stroke, .. } | SubjectContent::Circle { stroke, .. } => {
                stroke.as_deref()
            }
            other => panic!("Expected an authored shape, got {other:?}"),
        }
    }

    #[test]
    fn zero_width_strokes_do_not_inherit_another_subjects_width() {
        // Real Film projection only. No browser, generated observation or native
        // receipt is used; the provider and decoded-pixel gate remain separate.
        for kind in ["shape", "rectangle", "circle"] {
            let (project, profile_id, options) = stroke_projection_fixture(kind, 0.0, 8.0);
            let original = serde_json::to_value(&project).unwrap();
            let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
            assert_eq!(segments.len(), 1);
            assert_eq!(segments[0].frame_count, 60);
            assert_eq!(segments[0].film.editorial.stroke, 8.0);
            let subjects = &segments[0].film.sequences[0].beats[0].shots[0].subjects;
            assert_eq!(
                subjects[0].id,
                subject_id(project.scenes[0].nodes[0].id, None)
            );
            assert_eq!(
                native_subject_stroke(&subjects[0]),
                None,
                "inactive {kind} outline must stay invisible"
            );
            assert_eq!(native_subject_stroke(&subjects[1]), Some("#2080E0"));
            assert_eq!(
                serde_json::to_value(&project).unwrap(),
                original,
                "authored inactive color and width must remain editable in Canvas"
            );
        }
    }

    #[test]
    fn zero_width_strokes_remain_absent_when_all_shapes_are_unoutlined() {
        let (project, profile_id, options) = stroke_projection_fixture("circle", 0.0, 0.0);
        let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
        assert_eq!(segments[0].film.editorial.stroke, 0.0);
        for subject in &segments[0].film.sequences[0].beats[0].shots[0].subjects {
            assert_eq!(native_subject_stroke(subject), None);
        }
    }

    #[test]
    fn visible_stroke_color_and_profile_scaling_are_preserved() {
        let (mut project, profile_id, options) = stroke_projection_fixture("circle", 8.0, 8.0);
        let profile = project
            .deliverables
            .iter_mut()
            .find(|profile| profile.id == profile_id)
            .unwrap();
        profile.width = 960;
        profile.height = 540;
        let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
        assert_eq!(segments[0].film.editorial.stroke, 4.0);
        let subjects = &segments[0].film.sequences[0].beats[0].shots[0].subjects;
        assert_eq!(native_subject_stroke(&subjects[0]), Some("#FF8800"));
        assert_eq!(native_subject_stroke(&subjects[1]), Some("#2080E0"));
    }

    #[test]
    fn absent_stroke_color_does_not_become_visible() {
        let (mut project, profile_id, options) = stroke_projection_fixture("rectangle", 8.0, 8.0);
        project.scenes[0].nodes[0].style.stroke = None;
        let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
        let subjects = &segments[0].film.sequences[0].beats[0].shots[0].subjects;
        assert_eq!(native_subject_stroke(&subjects[0]), None);
        assert_eq!(native_subject_stroke(&subjects[1]), Some("#2080E0"));
    }

    #[test]
    fn distinct_nonzero_stroke_widths_remain_unsupported() {
        let (project, profile_id, options) = stroke_projection_fixture("rectangle", 4.0, 8.0);
        let error = build_motion_canvas_segments(&project, profile_id, &options).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("one shared nonzero stroke width"));
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

    fn assert_continuous_editorial_projection(mut project: Project) {
        project.scenes.truncate(1);
        let id = project.deliverables[0].id;
        let baseline =
            build_motion_canvas_segments(&project, id, &fixture_options(&project)).unwrap();
        let compiled_before = realize(&baseline[0].film).unwrap();
        let scene_id = project.scenes[0].id;
        for (start, duration) in [
            (RationalTime::ZERO, RationalTime::new(1, 3).unwrap()),
            (
                RationalTime::new(1, 3).unwrap(),
                RationalTime::new(1, 3).unwrap(),
            ),
            (RationalTime::new(2, 3).unwrap(), RationalTime::ONE),
            (
                RationalTime::new(5, 3).unwrap(),
                RationalTime::new(1, 3).unwrap(),
            ),
        ] {
            project
                .apply_change(&Change::UpsertSceneBeat {
                    scene_id,
                    beat: SceneBeat {
                        id: Uuid::now_v7(),
                        label: "Editorial interval".into(),
                        objective: "Preserve continuous source animation".into(),
                        start,
                        duration,
                    },
                })
                .unwrap();
        }
        let source = project.clone();
        let output =
            build_motion_canvas_segments(&project, id, &fixture_options(&project)).unwrap();
        let film = &output[0].film;
        let compiled = realize(film).unwrap();
        assert_eq!(
            serde_json::to_value(&film.sequences).unwrap(),
            serde_json::to_value(&baseline[0].film.sequences).unwrap(),
            "Editorial boundaries must not duplicate subjects, change IDs or restart curves"
        );
        assert_eq!(
            format!("{:?}", compiled.instructions),
            format!("{:?}", compiled_before.instructions)
        );
        assert_eq!(
            film.timing.spans.len(),
            baseline[0].film.timing.spans.len() + 4
        );
        for span in &baseline[0].film.timing.spans {
            let before = compiled_before.schedule.interval(&span.id).unwrap();
            let after = compiled.schedule.interval(&span.id).unwrap();
            assert_eq!(before.start, after.start);
            assert_eq!(before.end, after.end);
        }
        for beat in &project.scenes[0].beats {
            let id = format!("editorial-beat-{}-{}", scene_id.simple(), beat.id.simple());
            let interval = compiled.schedule.interval(&id).unwrap();
            assert_eq!(
                interval.start,
                Rational::new(beat.start.num, beat.start.den).unwrap()
            );
            let end = beat.start.checked_add(beat.duration).unwrap();
            assert_eq!(interval.end, Rational::new(end.num, end.den).unwrap());
        }
        assert!(
            film.timing.spans.len()
                <= scene_span_cost(
                    &project.scenes[0],
                    has_camera_reframe(&project, &project.scenes[0])
                )
        );
        assert_eq!(
            project, source,
            "Film projection never rewrites the editable Project"
        );
    }

    #[test]
    fn editorial_beats_keep_full_pose_opacity_clip_and_camera_on_one_clock() {
        for curve in [
            MotionInterpolation::Linear,
            MotionInterpolation::EaseOutCubic,
            MotionInterpolation::Hold,
        ] {
            let (mut project, _, child_id) = clipped_opacity_source();
            let child = project.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap();
            for (at, delta, width, height, rotation, interpolation) in [
                (
                    RationalTime::ZERO,
                    -60.0,
                    280.0,
                    90.0,
                    -18.0,
                    MotionInterpolation::Linear,
                ),
                (
                    RationalTime::new(3, 2).unwrap(),
                    0.0,
                    child.width,
                    child.height,
                    child.rotation_deg,
                    curve,
                ),
            ] {
                for (property, value) in [
                    (MotionProperty::X, child.x + delta),
                    (MotionProperty::Y, child.y + delta / 3.0),
                    (MotionProperty::Width, width),
                    (MotionProperty::Height, height),
                    (MotionProperty::RotationDeg, rotation),
                ] {
                    child.keyframes.push(CanvasKeyframe {
                        at,
                        property,
                        value,
                        interpolation,
                    });
                }
            }
            for (at, value, interpolation) in [
                (RationalTime::ZERO, 0.0, MotionInterpolation::Linear),
                (
                    RationalTime::new(1, 2).unwrap(),
                    1.0,
                    MotionInterpolation::Linear,
                ),
                (RationalTime::ONE, 1.0, MotionInterpolation::Hold),
                (
                    RationalTime::new(9, 5).unwrap(),
                    0.0,
                    MotionInterpolation::EaseOutCubic,
                ),
            ] {
                child.keyframes.push(CanvasKeyframe {
                    at,
                    property: MotionProperty::Opacity,
                    value,
                    interpolation,
                });
            }
            project
                .apply_change(&Change::UpsertCameraReframe {
                    reframe: CameraReframe {
                        id: Uuid::now_v7(),
                        scene_id: project.scenes[0].id,
                        component_version: 1,
                        start_center_x: 942.0,
                        start_center_y: 532.0,
                        end_center_x: 960.0,
                        end_center_y: 540.0,
                        duration_frames: 45,
                        detail_return: None,
                    },
                })
                .unwrap();
            assert_continuous_editorial_projection(project);
        }
    }

    #[test]
    fn editorial_beats_keep_camera_only_motion_and_conservative_resource_limits() {
        let mut project = fixture_project();
        project
            .apply_change(&Change::UpsertCameraReframe {
                reframe: CameraReframe {
                    id: Uuid::now_v7(),
                    scene_id: project.scenes[0].id,
                    component_version: 1,
                    start_center_x: 942.0,
                    start_center_y: 532.0,
                    end_center_x: 960.0,
                    end_center_y: 540.0,
                    duration_frames: 45,
                    detail_return: None,
                },
            })
            .unwrap();
        assert_continuous_editorial_projection(project);
    }

    #[test]
    fn adjacent_saved_scenes_emit_one_frame_exact_native_shot_each() {
        for (num, den) in [(30, 1), (30_000, 1_001)] {
            let mut project = Project::new("Synthetic native shot cut").unwrap();
            project.deliverables[0].frame_rate = RationalTime::new(num, den).unwrap();
            for (label, count, fill) in [("Opening", 1, "#f02020"), ("Proof", 3, "#20f020")] {
                project
                    .apply_change(&Change::AddScene {
                        name: label.into(),
                        objective: label.into(),
                        duration_seconds: 1,
                    })
                    .unwrap();
                let scene_id = project.scenes.last().unwrap().id;
                project
                    .apply_change(&Change::SetSceneDuration {
                        scene_id,
                        duration: RationalTime::new(count * den, num).unwrap(),
                    })
                    .unwrap();
                let mut shape = node("rectangle", None);
                shape.x = 840.0;
                shape.y = 450.0;
                shape.width = 240.0;
                shape.height = 180.0;
                shape.style.fill = Some(fill.into());
                project
                    .apply_change(&Change::AddCanvasNode {
                        scene_id,
                        node: shape,
                    })
                    .unwrap();
            }
            let mut options = fixture_options(&project);
            options.frame_rate =
                Rate::new(num.try_into().unwrap(), den.try_into().unwrap()).unwrap();
            let originals = project.clone();
            let segments =
                build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
                    .unwrap();
            assert_eq!(segments.len(), 1);
            assert_eq!(segments[0].frame_count, 4);
            let film = &segments[0].film;
            let realized = realize(film).unwrap();
            assert_eq!(film.sequences.len(), 1);
            assert_eq!(realized.scenes.len(), 1);
            let shots = film.sequences[0]
                .beats
                .iter()
                .flat_map(|beat| &beat.shots)
                .collect::<Vec<_>>();
            assert_eq!(shots.len(), 2);
            assert_ne!(shots[0].id, shots[1].id);
            assert_ne!(shots[0].subjects[0].id, shots[1].subjects[0].id);
            let cut = RationalTime::new(den, num).unwrap();
            let end = RationalTime::new(4 * den, num).unwrap();
            let mut observed = Vec::new();
            for (shot, start, finish, color) in [
                (shots[0], RationalTime::ZERO, cut, "#f02020"),
                (shots[1], cut, end, "#20f020"),
            ] {
                let range = realized.schedule.interval(&shot.span_id).unwrap();
                assert_eq!((range.start, range.end), (start, finish));
                let subject = &shot.subjects[0];
                let SubjectContent::Rectangle { fill, .. } = &subject.content else {
                    panic!("expected original editable rectangle");
                };
                assert!(fill.eq_ignore_ascii_case(color), "{fill}");
                observed.push(serde_json::json!({
                    "id":shot.id,"subject":subject,"start":range.start,"end":range.end
                }));
            }
            assert_eq!(project, originals);
            println!(
                "ADJACENT_SHOT_FILM={}",
                serde_json::json!({
                    "rate":options.frame_rate,"duration":end,"frames":segments[0].frame_count,
                    "run":realized.scenes[0].id,"output":film.output,"shots":observed
                })
            );
        }
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
        assert!(
            error
                .message
                .contains("requires a cleared digest-bound Asset source")
        );
        assert!(error.message.contains(&project.resource_key()));
        assert!(
            error
                .message
                .contains(&format!("revision {}", project.revision))
        );
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

    fn set_exact_linear_opacity_keys(node: &mut CanvasNode) {
        for (at, value) in [
            (RationalTime::ZERO, 0.0),
            (RationalTime::new(1, 1).unwrap(), 1.0),
        ] {
            node.keyframes.push(CanvasKeyframe {
                at,
                property: MotionProperty::Opacity,
                value,
                interpolation: MotionInterpolation::Linear,
            });
        }
    }

    #[test]
    fn exact_opacity_holds_fractional_intervals_and_budget_are_preserved() {
        for value in [0.0, 1.0] {
            let mut project = fixture_project();
            let node = &mut project.scenes[0].nodes[0];
            set_exact_linear_opacity_keys(node);
            for key in &mut node.keyframes {
                key.value = value;
            }
            let original = project.clone();
            let segments = build_motion_canvas_segments(
                &project,
                project.deliverables[0].id,
                &fixture_options(&project),
            )
            .unwrap();
            let shot = &segments[0].film.sequences[0].beats[0].shots[0];
            assert!(
                shot.motion.is_empty(),
                "Equal alpha endpoints need no invented fade"
            );
            assert_eq!(shot.subjects[0].initially_visible, value == 1.0);
            realize(&segments[0].film).unwrap();
            assert_eq!(project, original);
        }
        let mut project = fixture_project();
        let node = &mut project.scenes[0].nodes[0];
        node.keyframes.clear();
        for (num, den, value, interpolation) in [
            (0, 1, 0.0, MotionInterpolation::Linear),
            (1001, 3000, 1.0, MotionInterpolation::EaseOutCubic),
            (1001, 1500, 1.0, MotionInterpolation::Hold),
            (1001, 1000, 0.0, MotionInterpolation::Linear),
        ] {
            node.keyframes.push(CanvasKeyframe {
                at: RationalTime::new(num, den).unwrap(),
                property: MotionProperty::Opacity,
                value,
                interpolation,
            });
        }
        let intervals = native_exact_opacity_fade(
            &project.scenes[0].nodes[0],
            &project.scenes[0],
            Rate::new(30000, 1001).unwrap(),
        )
        .unwrap()
        .unwrap();
        assert_eq!(intervals.len(), 2);
        assert_eq!(intervals[1].start, RationalTime::new(1001, 1500).unwrap());
        assert_eq!(
            intervals[1].duration,
            RationalTime::new(1001, 3000).unwrap()
        );
        let scene = &mut project.scenes[0];
        let mut template = scene.nodes[0].clone();
        template.keyframes = (0..8)
            .map(|frame| CanvasKeyframe {
                at: RationalTime::new(frame, 30).unwrap(),
                property: MotionProperty::Opacity,
                value: (frame % 2) as f64,
                interpolation: MotionInterpolation::Linear,
            })
            .collect();
        scene.nodes = (0..20)
            .map(|index| {
                let mut node = template.clone();
                node.id = Uuid::now_v7();
                node.z_index = index;
                node
            })
            .collect();
        assert!(scene_span_cost(scene, false) > MAX_TEMPORAL_SPANS);
        assert!(
            build_motion_canvas_segments(
                &project,
                project.deliverables[0].id,
                &fixture_options(&project)
            )
            .is_err()
        );
    }

    #[test]
    fn exact_linear_opacity_keys_preserve_native_fade_in_and_original_canvas() {
        let mut project = fixture_project();
        let before = project.scenes[0].nodes[0].clone();
        set_exact_linear_opacity_keys(&mut project.scenes[0].nodes[0]);
        let options = fixture_options(&project);
        let segment = &build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap()[0];
        let shot = &segment.film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.motion.len(), 1);
        assert!(!shot.subjects[0].initially_visible);
        assert_eq!(shot.motion[0].easing, MotionEasing::Linear);
        assert!(matches!(
            &shot.motion[0].primitive,
            Primitive::FadeIn { target } if target == &subject_id(before.id, None)
        ));
        let compiled = realize(&segment.film).unwrap();
        let fade = compiled
            .instructions
            .iter()
            .find(|instruction| {
                matches!(
                    &instruction.operation,
                    semwright_motion_authoring::NativeOp::Tween {
                        channel: semwright_motion_authoring::Channel::Opacity,
                        ..
                    }
                )
            })
            .expect("exact authored opacity must generate a native opacity tween");
        assert_eq!(fade.start, RationalTime::ZERO);
        assert_eq!(fade.duration, RationalTime::new(1, 1).unwrap());
        assert_eq!(fade.easing, MotionEasing::Linear);
        match &fade.operation {
            semwright_motion_authoring::NativeOp::Tween { from, to, .. } => {
                assert_eq!(
                    from,
                    &Some(semwright_motion_authoring::Operand::Number(0.0))
                );
                assert_eq!(to, &semwright_motion_authoring::Operand::Number(1.0));
            }
            _ => unreachable!("filtered to opacity tween"),
        }
        assert_eq!(project.scenes[0].nodes[0].x, before.x);
        assert_eq!(project.scenes[0].nodes[0].y, before.y);
        assert_eq!(project.scenes[0].nodes[0].opacity, before.opacity);
        assert_eq!(project.scenes[0].nodes[0].text, before.text);
    }

    #[test]
    fn exact_fade_out_opacity_keeps_visible_source_and_real_native_zero_target() {
        for interpolation in [
            MotionInterpolation::Linear,
            MotionInterpolation::EaseOutCubic,
        ] {
            let mut project = fixture_project();
            set_exact_linear_opacity_keys(&mut project.scenes[0].nodes[0]);
            let node = &mut project.scenes[0].nodes[0];
            node.keyframes[0].value = 1.0;
            node.keyframes[1].value = 0.0;
            node.keyframes[1].interpolation = interpolation;
            let original = node.clone();
            let film = &build_motion_canvas_segments(
                &project,
                project.deliverables[0].id,
                &fixture_options(&project),
            )
            .unwrap()[0]
                .film;
            let shot = &film.sequences[0].beats[0].shots[0];
            assert_eq!(shot.motion.len(), 1);
            assert!(
                shot.subjects[0].initially_visible,
                "FadeOut starts with source visible"
            );
            let easing = match interpolation {
                MotionInterpolation::Linear => MotionEasing::Linear,
                MotionInterpolation::EaseOutCubic => MotionEasing::OutCubic,
                _ => unreachable!(),
            };
            assert_eq!(shot.motion[0].easing, easing);
            assert!(matches!(
                &shot.motion[0].primitive,
                Primitive::FadeOut { target } if target == &subject_id(original.id, None)
            ));
            let realized = realize(film).unwrap();
            let opacity = realized
                .instructions
                .iter()
                .filter(|instruction| {
                    matches!(
                        &instruction.operation,
                        semwright_motion_authoring::NativeOp::Tween {
                            channel: semwright_motion_authoring::Channel::Opacity,
                            ..
                        }
                    )
                })
                .collect::<Vec<_>>();
            assert_eq!(opacity.len(), 1, "no sampled or duplicated alpha effect");
            assert_eq!(opacity[0].start, RationalTime::ZERO);
            assert_eq!(opacity[0].duration, RationalTime::new(1, 1).unwrap());
            assert_eq!(opacity[0].easing, easing);
            match &opacity[0].operation {
                semwright_motion_authoring::NativeOp::Tween { from, to, .. } => {
                    assert_eq!(from, &None);
                    assert_eq!(to, &semwright_motion_authoring::Operand::Number(0.0));
                }
                _ => unreachable!(),
            }
            assert_eq!(
                project.scenes[0].nodes[0], original,
                "the original editable Canvas source remains unchanged"
            );
        }
    }

    fn clipped_opacity_source() -> (Project, Uuid, Uuid) {
        let mut project = fixture_project();
        let scene_id = project.scenes[0].id;
        let mut parent = node("clip_group", None);
        parent.name = "Bounded editable clipping parent".into();
        parent.x = 400.0;
        parent.y = 340.0;
        parent.width = 650.0;
        parent.height = 400.0;
        parent.z_index = 2;
        parent.style = NodeStyle::default();
        let parent_id = parent.id;
        let mut child = node("shape", None);
        child.name = "Native opacity child inside Clip".into();
        child.x = 690.0;
        child.y = 420.0;
        child.width = 510.0;
        child.height = 180.0;
        child.z_index = 3;
        child.parent_id = Some(parent_id);
        child.style.fill = Some("#E0A940".into());
        let child_id = child.id;
        for object in [parent, child] {
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: object,
                })
                .unwrap();
        }
        (project, parent_id, child_id)
    }

    #[test]
    fn composed_pose_motion_preserves_all_channels_clip_coordinates_and_hold() {
        use semwright_motion_authoring::NativeOp;
        for with_camera in [false, true] {
            for clipped in [false, true] {
                for curve in [
                    MotionInterpolation::Linear,
                    MotionInterpolation::EaseOutCubic,
                    MotionInterpolation::Hold,
                ] {
                    for fade_in in [false, true] {
                        let (mut project, parent_id, child_id) = clipped_opacity_source();
                        let child = project.scenes[0]
                            .nodes
                            .iter_mut()
                            .find(|node| node.id == child_id)
                            .unwrap();
                        if !clipped {
                            child.parent_id = None;
                        }
                        let mut initial = child.clone();
                        initial.x -= 20.0;
                        initial.y -= 20.0;
                        initial.width = 280.0;
                        initial.height = 90.0;
                        initial.rotation_deg = -18.0;
                        for (property, from, to) in [
                            (MotionProperty::X, initial.x, child.x),
                            (MotionProperty::Y, initial.y, child.y),
                            (MotionProperty::Width, initial.width, child.width),
                            (MotionProperty::Height, initial.height, child.height),
                            (
                                MotionProperty::RotationDeg,
                                initial.rotation_deg,
                                child.rotation_deg,
                            ),
                            (
                                MotionProperty::Opacity,
                                if fade_in { 0.0 } else { 1.0 },
                                if fade_in { 1.0 } else { 0.0 },
                            ),
                        ] {
                            child.keyframes.push(CanvasKeyframe {
                                at: RationalTime::ZERO,
                                property,
                                value: from,
                                interpolation: MotionInterpolation::Linear,
                            });
                            child.keyframes.push(CanvasKeyframe {
                                at: RationalTime::ONE,
                                property,
                                value: to,
                                interpolation: if property == MotionProperty::Opacity {
                                    MotionInterpolation::EaseOutCubic
                                } else {
                                    curve
                                },
                            });
                        }
                        let camera_id = Uuid::now_v7();
                        if with_camera {
                            let saved_camera = project.scenes[0].camera.clone();
                            let reframe = CameraReframe {
                                id: camera_id,
                                scene_id: project.scenes[0].id,
                                component_version: 1,
                                start_center_x: saved_camera.center_x - 18.0,
                                start_center_y: saved_camera.center_y - 8.0,
                                end_center_x: saved_camera.center_x,
                                end_center_y: saved_camera.center_y,
                                duration_frames: 45,
                                detail_return: None,
                            };
                            project
                                .apply_change(&Change::UpsertCameraReframe { reframe })
                                .unwrap();
                        }
                        let source = project.clone();
                        let options = fixture_options(&project);
                        let segments = build_motion_canvas_segments(
                            &project,
                            project.deliverables[0].id,
                            &options,
                        )
                        .unwrap();
                        let segment = &segments[0];
                        let shot = &segment.film.sequences[0].beats[0].shots[0];
                        let target = subject_id(child_id, None);
                        let subject = shot
                            .subjects
                            .iter()
                            .find(|subject| subject.id == target)
                            .unwrap();
                        let frame_id = uid("camera-frame", camera_id);
                        let expected_parent = if clipped {
                            Some(subject_id(parent_id, None))
                        } else {
                            with_camera.then(|| frame_id.clone())
                        };
                        assert_eq!(subject.parent, expected_parent);
                        if with_camera {
                            let frame = shot
                                .subjects
                                .iter()
                                .find(|subject| subject.id == frame_id)
                                .unwrap();
                            assert!(frame.parent.is_none() && !frame.clip_intentional);
                            let camera_motion = shot
                                .motion
                                .iter()
                                .filter(|motion| {
                                    motion.id == format!("camera-reframe-{}", camera_id.simple())
                                })
                                .collect::<Vec<_>>();
                            assert_eq!(camera_motion.len(), 1);
                            assert!(
                                matches!(&camera_motion[0].primitive, Primitive::Settle { target, .. } if target == &frame_id)
                            );
                            let clip = shot
                                .subjects
                                .iter()
                                .find(|subject| subject.id == subject_id(parent_id, None))
                                .unwrap();
                            assert_eq!(clip.parent, Some(frame_id.clone()));
                        }
                        assert_eq!(subject.initially_visible, !fade_in);
                        if clipped {
                            assert!(
                                shot.subjects
                                    .iter()
                                    .find(|subject| subject.id == subject_id(parent_id, None))
                                    .unwrap()
                                    .clip_intentional
                            );
                        }
                        let (output, projection) = output_profile(
                            &project.deliverables[0],
                            options.frame_rate,
                            &[&project.scenes[0], &project.scenes[1]],
                        )
                        .unwrap();
                        let source_projection =
                            scene_layout_projection(&project.scenes[0], projection);
                        let saved = project.scenes[0]
                            .nodes
                            .iter()
                            .find(|node| node.id == child_id)
                            .unwrap();
                        let (mut from_center, mut to_center) =
                            projected_geometry_centers(saved, &initial, source_projection, &output);
                        if clipped {
                            let parent = project.scenes[0]
                                .nodes
                                .iter()
                                .find(|node| node.id == parent_id)
                                .unwrap();
                            let parent_center =
                                canonical_position(parent, source_projection, &output);
                            from_center.x -= parent_center.x;
                            from_center.y -= parent_center.y;
                            to_center.x -= parent_center.x;
                            to_center.y -= parent_center.y;
                        }
                        let realized = realize(&segment.film).unwrap();
                        let mut channels = BTreeMap::<Channel, usize>::new();
                        for instruction in &realized.instructions {
                            let (actual_target, channel) = match &instruction.operation {
                                NativeOp::Tween {
                                    target, channel, ..
                                }
                                | NativeOp::Set {
                                    target, channel, ..
                                } => (target, *channel),
                                _ => continue,
                            };
                            if actual_target != &target {
                                continue;
                            }
                            *channels.entry(channel).or_default() += 1;
                            if channel == Channel::Opacity {
                                assert!(matches!(instruction.operation, NativeOp::Tween { .. }));
                                assert_eq!(instruction.duration, RationalTime::ONE);
                                assert_eq!(instruction.easing, MotionEasing::OutCubic);
                            } else if curve == MotionInterpolation::Hold {
                                assert!(matches!(instruction.operation, NativeOp::Set { .. }));
                                assert_eq!(instruction.duration, RationalTime::ZERO);
                                assert!(
                                    [RationalTime::ZERO, RationalTime::ONE]
                                        .contains(&instruction.start)
                                );
                            } else {
                                assert!(matches!(instruction.operation, NativeOp::Tween { .. }));
                                assert_eq!(instruction.duration, RationalTime::ONE);
                                assert_eq!(instruction.easing, exact_native_easing(curve).unwrap());
                            }
                            if channel == Channel::Position {
                                match &instruction.operation {
                                    NativeOp::Tween { from, to, .. } => {
                                        assert_eq!(from, &Some(Operand::Vector(from_center)));
                                        assert_eq!(to, &Operand::Vector(to_center));
                                    }
                                    NativeOp::Set { value, .. } => assert_eq!(
                                        value,
                                        &Operand::Vector(
                                            if instruction.start == RationalTime::ZERO {
                                                from_center
                                            } else {
                                                to_center
                                            }
                                        )
                                    ),
                                    _ => unreachable!(),
                                }
                            }
                        }
                        let count = if curve == MotionInterpolation::Hold {
                            2
                        } else {
                            1
                        };
                        assert_eq!(
                            channels,
                            BTreeMap::from([
                                (Channel::Position, count),
                                (Channel::Width, count),
                                (Channel::Height, count),
                                (Channel::Rotation, count),
                                (Channel::Opacity, 1),
                            ])
                        );
                        assert_eq!(
                            project, source,
                            "Film must preserve every editable source key"
                        );
                        assert_eq!(
                            scene_span_cost(&project.scenes[0], with_camera),
                            2 + usize::from(with_camera)
                                + if curve == MotionInterpolation::Hold {
                                    3
                                } else {
                                    2
                                }
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn rotated_terminal_pose_family_keeps_clip_camera_hold_and_independent_opacity() {
        use semwright_motion_authoring::NativeOp;
        for curve in [
            MotionInterpolation::Linear,
            MotionInterpolation::EaseOutCubic,
            MotionInterpolation::Hold,
        ] {
            for waypoint in [false, true] {
                let (mut project, parent_id, child_id) = clipped_opacity_source();
                let child = project.scenes[0]
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == child_id)
                    .unwrap();
                child.x = 720.0;
                child.y = 420.0;
                child.width = 240.0;
                child.height = 120.0;
                child.rotation_deg = 27.0;
                child.keyframes.clear();
                let mut poses = vec![(
                    RationalTime::ZERO,
                    MotionInterpolation::Linear,
                    [680.0, 400.0, 200.0, 100.0, -12.0],
                )];
                if waypoint {
                    poses.push((
                        RationalTime::new(1, 2).unwrap(),
                        curve,
                        [700.0, 410.0, 220.0, 110.0, 15.0],
                    ));
                }
                poses.push((
                    RationalTime::ONE,
                    curve,
                    [
                        child.x,
                        child.y,
                        child.width,
                        child.height,
                        child.rotation_deg,
                    ],
                ));
                for (at, interpolation, values) in poses {
                    for (property, value) in [
                        MotionProperty::X,
                        MotionProperty::Y,
                        MotionProperty::Width,
                        MotionProperty::Height,
                        MotionProperty::RotationDeg,
                    ]
                    .into_iter()
                    .zip(values)
                    {
                        child.keyframes.push(CanvasKeyframe {
                            at,
                            property,
                            value,
                            interpolation,
                        });
                    }
                }
                for (at, value, interpolation) in [
                    (RationalTime::ZERO, 0.0, MotionInterpolation::Linear),
                    (
                        RationalTime::new(4, 15).unwrap(),
                        1.0,
                        MotionInterpolation::EaseOutCubic,
                    ),
                    (
                        RationalTime::new(1, 2).unwrap(),
                        1.0,
                        MotionInterpolation::Hold,
                    ),
                    (
                        RationalTime::new(3, 2).unwrap(),
                        0.0,
                        MotionInterpolation::Linear,
                    ),
                ] {
                    child.keyframes.push(CanvasKeyframe {
                        at,
                        property: MotionProperty::Opacity,
                        value,
                        interpolation,
                    });
                }
                let camera = project.scenes[0].camera.clone();
                project
                    .apply_change(&Change::UpsertCameraReframe {
                        reframe: CameraReframe {
                            id: Uuid::now_v7(),
                            scene_id: project.scenes[0].id,
                            component_version: 1,
                            start_center_x: camera.center_x - 18.0,
                            start_center_y: camera.center_y - 8.0,
                            end_center_x: camera.center_x,
                            end_center_y: camera.center_y,
                            duration_frames: 45,
                            detail_return: None,
                        },
                    })
                    .unwrap();
                let original = project.clone();
                let segments = build_motion_canvas_segments(
                    &project,
                    project.deliverables[0].id,
                    &fixture_options(&project),
                )
                .unwrap();
                let shot = &segments[0].film.sequences[0].beats[0].shots[0];
                let target = subject_id(child_id, None);
                assert_eq!(
                    shot.subjects
                        .iter()
                        .find(|subject| subject.id == target)
                        .unwrap()
                        .parent,
                    Some(subject_id(parent_id, None))
                );
                let realized = realize(&segments[0].film).unwrap();
                let rotations = realized.instructions.iter().filter(|instruction| matches!(&instruction.operation,
                    NativeOp::Set { target: actual, channel: Channel::Rotation, .. }
                    | NativeOp::Tween { target: actual, channel: Channel::Rotation, .. } if actual == &target)).collect::<Vec<_>>();
                assert_eq!(
                    rotations.len(),
                    if curve == MotionInterpolation::Hold {
                        if waypoint { 3 } else { 2 }
                    } else if waypoint {
                        2
                    } else {
                        1
                    }
                );
                assert!(
                    matches!(&rotations.last().unwrap().operation,
                    NativeOp::Set { value: Operand::Number(value), .. } if *value == 27.0)
                        || matches!(&rotations.last().unwrap().operation,
                    NativeOp::Tween { to: Operand::Number(value), .. } if *value == 27.0)
                );
                if curve == MotionInterpolation::Hold {
                    assert!(
                        rotations
                            .iter()
                            .all(|instruction| instruction.duration == RationalTime::ZERO)
                    );
                    assert_eq!(rotations.last().unwrap().start, RationalTime::ONE);
                }
                let alpha = realized.instructions.iter().filter(|instruction| matches!(&instruction.operation,
                    NativeOp::Tween { target: actual, channel: Channel::Opacity, .. } if actual == &target)).count();
                assert_eq!(alpha, 2);
                assert_eq!(project, original);
                let reopened: Project =
                    serde_json::from_str(&serde_json::to_string(&project).unwrap()).unwrap();
                assert_eq!(reopened, original);
            }
        }
    }

    #[test]
    fn constant_orientation_uses_one_initial_write_while_other_channels_move() {
        use semwright_motion_authoring::NativeOp;
        for mode in ["opacity", "position", "resize", "position-opacity"] {
            let mut project = fixture_project();
            let mut shape = node("rectangle", None);
            shape.x = 720.0;
            shape.y = 420.0;
            shape.width = 240.0;
            shape.height = 120.0;
            shape.rotation_deg = -31.0;
            let target = subject_id(shape.id, None);
            for (property, initial, terminal) in [
                (MotionProperty::X, 680.0, shape.x),
                (MotionProperty::Y, 400.0, shape.y),
                (MotionProperty::Width, 200.0, shape.width),
                (MotionProperty::Opacity, 0.0, 1.0),
            ] {
                let selected = match property {
                    MotionProperty::Opacity => mode.contains("opacity"),
                    MotionProperty::Width => mode == "resize",
                    _ => mode.contains("position"),
                };
                if selected {
                    shape.keyframes.extend([
                        CanvasKeyframe {
                            at: RationalTime::ZERO,
                            property,
                            value: initial,
                            interpolation: MotionInterpolation::Linear,
                        },
                        CanvasKeyframe {
                            at: RationalTime::ONE,
                            property,
                            value: terminal,
                            interpolation: MotionInterpolation::EaseOutCubic,
                        },
                    ]);
                }
            }
            project.scenes[0].nodes = vec![shape];
            let original = project.clone();
            let segments = build_motion_canvas_segments(
                &project,
                project.deliverables[0].id,
                &fixture_options(&project),
            )
            .unwrap();
            let realized = realize(&segments[0].film).unwrap();
            let rotations = realized.instructions.iter().filter(|instruction| matches!(&instruction.operation,
                NativeOp::Set { target: actual, channel: Channel::Rotation, .. }
                | NativeOp::Tween { target: actual, channel: Channel::Rotation, .. } if actual == &target)).collect::<Vec<_>>();
            assert_eq!(rotations.len(), 1);
            assert_eq!(rotations[0].start, RationalTime::ZERO);
            assert_eq!(rotations[0].duration, RationalTime::ZERO);
            assert!(
                matches!(&rotations[0].operation, NativeOp::Set { value: Operand::Number(value), .. } if *value == -31.0)
            );
            assert_eq!(project, original);
        }
    }

    fn assert_sparse_waypoint_track_preserves_authored_channels() {
        use semwright_motion_authoring::NativeOp;
        let (mut project, _, child_id) = clipped_opacity_source();
        let child = project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap();
        child.rotation_deg = -31.0;
        let half = RationalTime::new(1, 2).unwrap();
        child.keyframes = [
            (
                RationalTime::ZERO,
                child.x - 40.0,
                MotionInterpolation::Linear,
            ),
            (half, child.x - 20.0, MotionInterpolation::EaseOutCubic),
            (RationalTime::ONE, child.x, MotionInterpolation::Hold),
        ]
        .into_iter()
        .map(|(at, value, interpolation)| CanvasKeyframe {
            at,
            property: MotionProperty::X,
            value,
            interpolation,
        })
        .collect();

        let saved = project.clone();
        let id = project.deliverables[0].id;
        let options = fixture_options(&project);
        let segments = build_motion_canvas_segments(&project, id, &options)
            .expect("source-bound X waypoints must become a real native Film");
        let compiled =
            realize(&segments[0].film).expect("pinned SDK must realize sparse waypoints");
        let target = subject_id(child_id, None);
        let mut position_writes = vec![];
        let mut rotation_writes = vec![];
        for instruction in &compiled.instructions {
            match &instruction.operation {
                NativeOp::Tween {
                    target: actual,
                    channel: Channel::Position,
                    ..
                }
                | NativeOp::Set {
                    target: actual,
                    channel: Channel::Position,
                    ..
                } if actual == &target => position_writes.push(instruction),
                NativeOp::Tween {
                    target: actual,
                    channel: Channel::Rotation,
                    ..
                }
                | NativeOp::Set {
                    target: actual,
                    channel: Channel::Rotation,
                    ..
                } if actual == &target => rotation_writes.push(instruction),
                _ => {}
            }
        }
        assert_eq!(position_writes.len(), 3);
        let tween = position_writes
            .iter()
            .find(|item| matches!(item.operation, NativeOp::Tween { .. }))
            .unwrap();
        assert_eq!(tween.easing, MotionEasing::OutCubic);
        assert_eq!(tween.start, RationalTime::ZERO);
        assert_eq!(tween.duration, half);
        let NativeOp::Tween {
            from: Some(Operand::Vector(from)),
            to: Operand::Vector(to),
            ..
        } = &tween.operation
        else {
            panic!("expected literal local position endpoints");
        };
        let sets: BTreeMap<_, _> = position_writes
            .iter()
            .filter_map(|item| match &item.operation {
                NativeOp::Set {
                    value: Operand::Vector(value),
                    ..
                } => Some((item.start, value)),
                _ => None,
            })
            .collect();
        assert_eq!(sets.len(), 2);
        assert_eq!(sets[&half], to);
        let end = sets[&RationalTime::ONE];
        assert_eq!(from.y, to.y);
        assert_eq!(to.y, end.y);
        assert!(from.x < to.x && to.x < end.x);
        assert!(((to.x - from.x) - (end.x - to.x)).abs() < 1e-9);
        assert_eq!(rotation_writes.len(), 1);
        assert_eq!(rotation_writes[0].start, RationalTime::ZERO);
        assert!(matches!(
            &rotation_writes[0].operation,
            NativeOp::Set { value: Operand::Number(value), .. } if *value == -31.0
        ));
        assert_eq!(project, saved, "rendering must preserve authored keyframes");

        let child = project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap();
        child.keyframes[1].property = MotionProperty::Y;
        let error = build_motion_canvas_segments(&project, id, &options).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }

    #[test]
    fn waypoint_track_realizes_mixed_curves_with_independent_fade_inside_clip() {
        use semwright_motion_authoring::NativeOp;
        assert_sparse_waypoint_track_preserves_authored_channels();
        let (mut project, parent_id, child_id) = clipped_opacity_source();
        let child = project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap();
        let half = RationalTime::new(1, 2).unwrap();
        let quarter = RationalTime::new(1, 4).unwrap();
        for (at, curve, pose) in [
            (
                RationalTime::ZERO,
                MotionInterpolation::Linear,
                [650.0, 400.0, 400.0, 160.0, -10.0],
            ),
            (
                half,
                MotionInterpolation::Hold,
                [680.0, 430.0, 450.0, 170.0, 12.0],
            ),
            (
                RationalTime::ONE,
                MotionInterpolation::EaseOutCubic,
                [
                    child.x,
                    child.y,
                    child.width,
                    child.height,
                    child.rotation_deg,
                ],
            ),
        ] {
            for (property, value) in [
                MotionProperty::X,
                MotionProperty::Y,
                MotionProperty::Width,
                MotionProperty::Height,
                MotionProperty::RotationDeg,
            ]
            .into_iter()
            .zip(pose)
            {
                child.keyframes.push(CanvasKeyframe {
                    at,
                    property,
                    value,
                    interpolation: curve,
                });
            }
        }
        child.keyframes.extend([
            CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::Opacity,
                value: 0.0,
                interpolation: MotionInterpolation::Linear,
            },
            CanvasKeyframe {
                at: RationalTime::new(4, 15).unwrap(),
                property: MotionProperty::Opacity,
                value: 1.0,
                interpolation: MotionInterpolation::Linear,
            },
        ]);
        let camera = project.scenes[0].camera.clone();
        let camera_id = Uuid::now_v7();
        project
            .apply_change(&Change::UpsertCameraReframe {
                reframe: CameraReframe {
                    id: camera_id,
                    scene_id: project.scenes[0].id,
                    component_version: 1,
                    start_center_x: camera.center_x - 18.0,
                    start_center_y: camera.center_y - 8.0,
                    end_center_x: camera.center_x,
                    end_center_y: camera.center_y,
                    duration_frames: 45,
                    detail_return: None,
                },
            })
            .unwrap();
        let source = project.clone();
        let options = fixture_options(&project);
        let segments =
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap();
        let shot = &segments[0].film.sequences[0].beats[0].shots[0];
        let target = subject_id(child_id, None);
        assert_eq!(
            shot.subjects
                .iter()
                .find(|s| s.id == target)
                .unwrap()
                .parent,
            Some(subject_id(parent_id, None))
        );
        let realized = realize(&segments[0].film).unwrap();
        let mut counts = BTreeMap::<Channel, usize>::new();
        for instruction in &realized.instructions {
            match &instruction.operation {
                NativeOp::Set {
                    target: actual,
                    channel,
                    ..
                } if actual == &target => {
                    assert_ne!(*channel, Channel::Opacity);
                    assert_eq!(instruction.start, RationalTime::ZERO);
                    assert_eq!(instruction.duration, RationalTime::ZERO);
                    *counts.entry(*channel).or_default() += 1;
                }
                NativeOp::Tween {
                    target: actual,
                    channel,
                    ..
                } if actual == &target => {
                    if *channel == Channel::Opacity {
                        assert_eq!(instruction.start, RationalTime::ZERO);
                        assert_eq!(instruction.duration, RationalTime::new(4, 15).unwrap());
                    } else {
                        assert_eq!(instruction.start, half);
                        assert_eq!(instruction.duration, half);
                        assert_eq!(instruction.easing, MotionEasing::OutCubic);
                    }
                    *counts.entry(*channel).or_default() += 1;
                }
                _ => {}
            }
        }
        assert_eq!(
            counts,
            BTreeMap::from([
                (Channel::Position, 2),
                (Channel::Width, 2),
                (Channel::Height, 2),
                (Channel::Rotation, 2),
                (Channel::Opacity, 1)
            ])
        );
        assert_eq!(project, source);
        let invalid = project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap();
        invalid
            .keyframes
            .iter_mut()
            .find(|key| key.at == half && key.property == MotionProperty::X)
            .unwrap()
            .value = 0.0;
        assert!(
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).is_err()
        );
        // The intermediate slot also fails when it is not on the selected frame grid.
        let invalid = project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap();
        for key in &mut invalid.keyframes {
            if key.at == half {
                key.at = quarter;
                if key.property == MotionProperty::X {
                    key.value = 680.0;
                }
            }
        }
        assert!(
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).is_err()
        );
    }

    #[test]
    fn composed_pose_refuses_inexact_curves_parent_motion_and_invalid_alpha() {
        for invalid in 0..4 {
            let (mut project, parent_id, child_id) = clipped_opacity_source();
            let child = project.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap();
            set_exact_geometry_keys(child);
            child.keyframes.extend([
                CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property: MotionProperty::Opacity,
                    value: 1.0,
                    interpolation: MotionInterpolation::Linear,
                },
                CanvasKeyframe {
                    at: RationalTime::ONE,
                    property: MotionProperty::Opacity,
                    value: 0.0,
                    interpolation: MotionInterpolation::EaseOutCubic,
                },
            ]);
            match invalid {
                0 => child.keyframes.last_mut().unwrap().value = 0.25,
                1 => {
                    child.keyframes.last_mut().unwrap().interpolation =
                        MotionInterpolation::EaseInOut
                }
                2 => child.keyframes[1].at = RationalTime::new(1, 37).unwrap(),
                3 => {
                    project.scenes[0]
                        .nodes
                        .iter_mut()
                        .find(|node| node.id == parent_id)
                        .unwrap()
                        .rotation_deg = 5.0
                }
                _ => unreachable!(),
            }
            let result = build_motion_canvas_segments(
                &project,
                project.deliverables[0].id,
                &fixture_options(&project),
            );
            assert!(
                result.is_err(),
                "unrepresentable composition {invalid} must refuse before Host"
            );
        }
    }

    #[test]
    fn original_editable_clip_child_opacity_fades_in_and_out_in_native_film() {
        for (start, end, interpolation, direction) in [
            (
                0.0,
                1.0,
                MotionInterpolation::Linear,
                NativeOpacityDirection::In,
            ),
            (
                1.0,
                0.0,
                MotionInterpolation::EaseOutCubic,
                NativeOpacityDirection::Out,
            ),
        ] {
            let (mut project, parent_id, child_id) = clipped_opacity_source();
            let scene_id = project.scenes[0].id;
            let child = project.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap();
            set_exact_linear_opacity_keys(child);
            child.keyframes[0].value = start;
            child.keyframes[1].value = end;
            child.keyframes[1].interpolation = interpolation;
            let original = project.clone();
            let opts = fixture_options(&project);
            let film = &build_motion_canvas_segments(&project, project.deliverables[0].id, &opts)
                .unwrap()[0]
                .film;
            let shot = &film.sequences[0].beats[0].shots[0];
            let parent = shot
                .subjects
                .iter()
                .find(|subject| subject.id == subject_id(parent_id, None))
                .unwrap();
            let child = shot
                .subjects
                .iter()
                .find(|subject| subject.id == subject_id(child_id, None))
                .unwrap();
            assert!(matches!(&parent.content, SubjectContent::Group));
            assert!(
                parent.clip_intentional,
                "authoritative clipping remains a Native Group"
            );
            assert!(
                !child.clip_intentional,
                "child does not claim to own a fake mask"
            );
            assert_eq!(child.parent.as_deref(), Some(parent.id.as_str()));
            assert_eq!(
                child.initially_visible,
                direction == NativeOpacityDirection::Out
            );
            let fade = shot
                .motion
                .iter()
                .filter(|motion| {
                    matches!(
                        (&motion.primitive, direction),
                        (Primitive::FadeIn { target }, NativeOpacityDirection::In)
                            if target == &subject_id(child_id, None)
                    ) || matches!(
                        (&motion.primitive, direction),
                        (Primitive::FadeOut { target }, NativeOpacityDirection::Out)
                            if target == &subject_id(child_id, None)
                    )
                })
                .collect::<Vec<_>>();
            assert_eq!(fade.len(), 1, "original Clip child has one native fade");
            let realized = realize(film).expect("pinned native SDK must admit fading a Clip child");
            let tween = realized
                .instructions
                .iter()
                .filter(|instruction| {
                    matches!(
                        &instruction.operation,
                        semwright_motion_authoring::NativeOp::Tween {
                            channel: semwright_motion_authoring::Channel::Opacity,
                            ..
                        }
                    )
                })
                .collect::<Vec<_>>();
            assert_eq!(
                tween.len(),
                1,
                "no opacity approximation or duplicate effects"
            );
            let expected_easing = if interpolation == MotionInterpolation::Linear {
                MotionEasing::Linear
            } else {
                MotionEasing::OutCubic
            };
            assert_eq!(tween[0].start, RationalTime::ZERO);
            assert_eq!(tween[0].duration, RationalTime::new(1, 1).unwrap());
            assert_eq!(tween[0].easing, expected_easing);
            match &tween[0].operation {
                semwright_motion_authoring::NativeOp::Tween { to, .. } => {
                    assert_eq!(to, &semwright_motion_authoring::Operand::Number(end));
                }
                _ => unreachable!("native opacity tween was selected"),
            }
            assert_eq!(
                project, original,
                "Native Film projection cannot change saved Clip source"
            );
            assert_eq!(project.scenes[0].id, scene_id);
        }
    }

    #[test]
    fn clipped_opacity_requires_one_static_root_clip_and_exact_native_curve() {
        let (mut baseline, parent_id, child_id) = clipped_opacity_source();
        set_exact_linear_opacity_keys(
            baseline.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap(),
        );
        let mut cases = Vec::new();
        let mut ordinary_parent = baseline.clone();
        ordinary_parent.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .kind = "rectangle".into();
        cases.push(ordinary_parent);
        let mut rotated_parent = baseline.clone();
        rotated_parent.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .rotation_deg = 5.0;
        cases.push(rotated_parent);
        let mut animated_parent = baseline.clone();
        set_exact_linear_opacity_keys(
            animated_parent.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == parent_id)
                .unwrap(),
        );
        cases.push(animated_parent);
        let mut nested_parent = baseline.clone();
        let mut top = node("group", None);
        top.x = 120.0;
        top.y = 180.0;
        top.width = 400.0;
        top.height = 400.0;
        top.rotation_deg = 8.0;
        let top_id = top.id;
        nested_parent
            .apply_change(&Change::AddCanvasNode {
                scene_id: nested_parent.scenes[0].id,
                node: top,
            })
            .unwrap();
        nested_parent.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .parent_id = Some(top_id);
        cases.push(nested_parent);
        let mut bad_curve = baseline.clone();
        bad_curve.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap()
            .keyframes[1]
            .interpolation = MotionInterpolation::EaseInOut;
        cases.push(bad_curve);
        let mut mixed_keys = baseline.clone();
        mixed_keys.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap()
            .keyframes
            .push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::X,
                value: 700.0,
                interpolation: MotionInterpolation::Linear,
            });
        cases.push(mixed_keys);
        for (case_index, project) in cases.iter().enumerate() {
            let result = build_motion_canvas_segments(
                project,
                project.deliverables[0].id,
                &fixture_options(project),
            );
            assert!(
                result.is_err(),
                "unsupported Clip/opacity combination {case_index} admitted"
            );
        }
    }

    #[test]
    fn noncanonical_linear_opacity_keys_fail_without_approximation() {
        let mut baseline = fixture_project();
        set_exact_linear_opacity_keys(&mut baseline.scenes[0].nodes[0]);
        let opts = fixture_options(&baseline);
        let deliverable_id = baseline.deliverables[0].id;

        let mut off_frame = baseline.clone();
        off_frame.scenes[0].nodes[0].keyframes[1].at = RationalTime::new(1, 7).unwrap();
        let mut duplicate_time = baseline.clone();
        duplicate_time.scenes[0].nodes[0].keyframes[1].at = RationalTime::ZERO;
        let mut partial_exit = baseline.clone();
        partial_exit.scenes[0].nodes[0].keyframes[0].value = 1.0;
        partial_exit.scenes[0].nodes[0].keyframes[1].value = 0.2;
        let mut held_exit = baseline.clone();
        held_exit.scenes[0].nodes[0].keyframes[0].value = 1.0;
        held_exit.scenes[0].nodes[0].keyframes[1].value = 0.0;
        held_exit.scenes[0].nodes[0].keyframes[1].interpolation = MotionInterpolation::Hold;
        let mut wrong_target = baseline.clone();
        wrong_target.scenes[0].nodes[0].keyframes[1].value = 0.8;
        let mut wrong_curve = baseline.clone();
        wrong_curve.scenes[0].nodes[0].keyframes[1].interpolation = MotionInterpolation::EaseInOut;
        let mut mixed = baseline.clone();
        mixed.scenes[0].nodes[0].keyframes.push(CanvasKeyframe {
            at: RationalTime::ZERO,
            property: MotionProperty::X,
            value: 120.0,
            interpolation: MotionInterpolation::Linear,
        });
        let mut extra_key = baseline.clone();
        extra_key.scenes[0].nodes[0].keyframes.push(CanvasKeyframe {
            at: RationalTime::new(1, 2).unwrap(),
            property: MotionProperty::Opacity,
            value: 0.5,
            interpolation: MotionInterpolation::Linear,
        });
        let mut incompatible_persisted_opacity = baseline.clone();
        incompatible_persisted_opacity.scenes[0].nodes[0].opacity = 0.7;
        let mut parented = baseline.clone();
        parented.scenes[0].nodes[0].parent_id = Some(Uuid::now_v7());
        let mut exceeded_scene = baseline.clone();
        exceeded_scene.scenes[0].nodes[0].keyframes[1].at = exceeded_scene.scenes[0].duration;

        for variant in [
            off_frame,
            duplicate_time,
            partial_exit,
            held_exit,
            wrong_target,
            wrong_curve,
            mixed,
            extra_key,
            incompatible_persisted_opacity,
            parented,
            exceeded_scene,
        ] {
            // Project::validate may reject a malformed historical variant
            // before Film negotiation; both layers must refuse it rather
            // than silently flattening the authored opacity keys.
            assert!(build_motion_canvas_segments(&variant, deliverable_id, &opts).is_err());
        }
    }

    #[test]
    fn exact_cubic_out_opacity_reaches_native_tween_without_curve_approximation() {
        let mut project = fixture_project();
        set_exact_linear_opacity_keys(&mut project.scenes[0].nodes[0]);
        project.scenes[0].nodes[0].keyframes[1].interpolation = MotionInterpolation::EaseOutCubic;
        let source = project.scenes[0].nodes[0].clone();
        let segment = &build_motion_canvas_segments(
            &project,
            project.deliverables[0].id,
            &fixture_options(&project),
        )
        .unwrap()[0];
        let shot = &segment.film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.motion.len(), 1);
        assert_eq!(shot.motion[0].easing, MotionEasing::OutCubic);
        let compiled = realize(&segment.film).unwrap();
        let fade = compiled
            .instructions
            .iter()
            .find(|instruction| {
                matches!(
                    instruction.operation,
                    semwright_motion_authoring::NativeOp::Tween {
                        channel: semwright_motion_authoring::Channel::Opacity,
                        ..
                    }
                )
            })
            .expect("native opacity tween from exact source");
        assert_eq!(fade.easing, MotionEasing::OutCubic);
        assert_eq!(fade.duration, RationalTime::new(1, 1).unwrap());
        assert_eq!(project.scenes[0].nodes[0], source);
    }

    #[test]
    fn exact_cubic_out_xy_keys_generate_native_settle_with_source_identity() {
        let mut project = fixture_project();
        let node_id = project.scenes[0].nodes[0].id;
        let pose = (project.scenes[0].nodes[0].x, project.scenes[0].nodes[0].y);
        set_exact_linear_position_keys(&mut project.scenes[0].nodes[0]);
        for key in &mut project.scenes[0].nodes[0].keyframes {
            if key.at != RationalTime::ZERO {
                key.interpolation = MotionInterpolation::EaseOutCubic;
            }
        }
        let segment = &build_motion_canvas_segments(
            &project,
            project.deliverables[0].id,
            &fixture_options(&project),
        )
        .unwrap()[0];
        let shot = &segment.film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.motion.len(), 1);
        assert_eq!(shot.motion[0].easing, MotionEasing::OutCubic);
        match &shot.motion[0].primitive {
            Primitive::Settle {
                target, rotation, ..
            } => {
                assert_eq!(target, &subject_id(node_id, None));
                assert_eq!(*rotation, 0.0);
            }
            other => panic!("Expected source-bound native Settle: {other:?}"),
        }
        let compiled = realize(&segment.film).unwrap();
        let position = compiled
            .instructions
            .iter()
            .find(|instruction| {
                matches!(
                    instruction.operation,
                    semwright_motion_authoring::NativeOp::Tween {
                        channel: semwright_motion_authoring::Channel::Position,
                        ..
                    }
                )
            })
            .expect("native position tween from source");
        assert_eq!(position.easing, MotionEasing::OutCubic);
        assert_eq!(
            (project.scenes[0].nodes[0].x, project.scenes[0].nodes[0].y),
            pose
        );
    }

    #[test]
    fn one_axis_cubic_and_one_axis_linear_are_not_silently_approximated() {
        let mut project = fixture_project();
        let id = project.deliverables[0].id;
        set_exact_linear_position_keys(&mut project.scenes[0].nodes[0]);
        project.scenes[0].nodes[0].keyframes[2].interpolation = MotionInterpolation::EaseOutCubic;
        let error =
            build_motion_canvas_segments(&project, id, &fixture_options(&project)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }

    #[test]
    fn smoothstep_opacity_rejects_partial_authored_endpoints() {
        let mut project = fixture_project();
        set_exact_linear_opacity_keys(&mut project.scenes[0].nodes[0]);
        project.scenes[0].nodes[0].keyframes[1].interpolation = MotionInterpolation::EaseInOut;
        project.scenes[0].nodes[0].keyframes[1].value = 0.4;
        let error = build_motion_canvas_segments(
            &project,
            project.deliverables[0].id,
            &fixture_options(&project),
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }

    fn set_clipped_position_keys(
        node: &mut CanvasNode,
        start_x: f64,
        interpolation: MotionInterpolation,
    ) {
        for (at, x, y, curve) in [
            (
                RationalTime::ZERO,
                start_x,
                node.y,
                MotionInterpolation::Linear,
            ),
            (
                RationalTime::new(1, 1).unwrap(),
                node.x,
                node.y,
                interpolation,
            ),
        ] {
            node.keyframes.push(CanvasKeyframe {
                at,
                property: MotionProperty::X,
                value: x,
                interpolation: curve,
            });
            node.keyframes.push(CanvasKeyframe {
                at,
                property: MotionProperty::Y,
                value: y,
                interpolation: curve,
            });
        }
    }

    #[test]
    fn exact_native_settle_moves_saved_child_inside_original_clipped_parent() {
        for (interpolation, easing) in [
            (MotionInterpolation::Linear, MotionEasing::Linear),
            (MotionInterpolation::EaseOutCubic, MotionEasing::OutCubic),
        ] {
            let (mut project, parent_id, child_id) = clipped_opacity_source();
            let child = project.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap();
            // At frame zero the child is fully outside its saved parent's
            // right edge. It moves into view, but only through the real Clip.
            set_clipped_position_keys(child, 1140.0, interpolation);
            let source = project.clone();
            let segments = build_motion_canvas_segments(
                &project,
                project.deliverables[0].id,
                &fixture_options(&project),
            )
            .unwrap();
            let output = &segments[0];
            let shot = &output.film.sequences[0].beats[0].shots[0];
            let group = shot
                .subjects
                .iter()
                .find(|subject| subject.id == subject_id(parent_id, None))
                .unwrap();
            let clipped_child = shot
                .subjects
                .iter()
                .find(|subject| subject.id == subject_id(child_id, None))
                .unwrap();
            assert!(matches!(&group.content, SubjectContent::Group));
            assert!(
                group.clip_intentional,
                "source parent must own the real native Clip"
            );
            assert_eq!(clipped_child.parent.as_deref(), Some(group.id.as_str()));
            assert!(!clipped_child.clip_intentional);
            assert_eq!(shot.motion.len(), 1, "no duplicate or substituted effect");
            assert_eq!(shot.motion[0].easing, easing);
            match &shot.motion[0].primitive {
                Primitive::Settle {
                    target,
                    offset,
                    rotation,
                } => {
                    assert_eq!(target, &subject_id(child_id, None));
                    assert!(offset.x > 0.0, "child starts outside the right Clip edge");
                    assert_eq!(offset.y, 0.0);
                    assert_eq!(*rotation, 0.0);
                }
                other => panic!("Clipped movement lost exact Native Settle: {other:?}"),
            }
            let compiled = realize(&output.film).unwrap();
            let position = compiled
                .instructions
                .iter()
                .filter(|instruction| {
                    matches!(
                        &instruction.operation,
                        semwright_motion_authoring::NativeOp::Tween {
                            channel: semwright_motion_authoring::Channel::Position,
                            ..
                        }
                    )
                })
                .collect::<Vec<_>>();
            assert_eq!(position.len(), 1);
            assert_eq!(position[0].start, RationalTime::ZERO);
            assert_eq!(position[0].duration, RationalTime::new(1, 1).unwrap());
            assert_eq!(position[0].easing, easing);
            match &position[0].operation {
                semwright_motion_authoring::NativeOp::Tween { from, to, .. } => {
                    assert!(matches!(
                        from,
                        Some(semwright_motion_authoring::Operand::OriginalOffset(_))
                    ));
                    assert_eq!(to, &semwright_motion_authoring::Operand::Original);
                }
                other => panic!("Clipped source must produce one Native position Tween: {other:?}"),
            }
            assert_eq!(
                project, source,
                "Native preparation never edits saved Clip source"
            );
        }
    }

    #[test]
    fn native_clipped_position_refuses_dynamic_or_unrepresentable_parentage() {
        let (mut original, parent_id, child_id) = clipped_opacity_source();
        set_clipped_position_keys(
            original.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == child_id)
                .unwrap(),
            1140.0,
            MotionInterpolation::Linear,
        );
        let mut negatives = Vec::new();
        let mut ordinary_parent = original.clone();
        ordinary_parent.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .kind = "rectangle".into();
        negatives.push(ordinary_parent);
        let mut rotated_parent = original.clone();
        rotated_parent.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .rotation_deg = 6.0;
        negatives.push(rotated_parent);
        let mut animated_parent = original.clone();
        set_exact_linear_opacity_keys(
            animated_parent.scenes[0]
                .nodes
                .iter_mut()
                .find(|node| node.id == parent_id)
                .unwrap(),
        );
        negatives.push(animated_parent);
        let mut nested_parent = original.clone();
        let root_id = nested_parent.scenes[0].nodes[0].id;
        nested_parent.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == parent_id)
            .unwrap()
            .parent_id = Some(root_id);
        negatives.push(nested_parent);
        let mut smoothstep = original.clone();
        smoothstep.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap()
            .keyframes[2]
            .interpolation = MotionInterpolation::EaseInOut;
        negatives.push(smoothstep);
        let mut mixed = original.clone();
        mixed.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == child_id)
            .unwrap()
            .keyframes
            .push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::Opacity,
                value: 0.5,
                interpolation: MotionInterpolation::Linear,
            });
        negatives.push(mixed);
        for (index, candidate) in negatives.into_iter().enumerate() {
            let result = build_motion_canvas_segments(
                &candidate,
                candidate.deliverables[0].id,
                &fixture_options(&candidate),
            );
            assert!(
                result.is_err(),
                "Clip motion negative case {index} was silently admitted"
            );
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

    fn set_exact_geometry_keys(node: &mut CanvasNode) {
        for (property, first, last) in [
            (MotionProperty::Width, 280.0, node.width),
            (MotionProperty::Height, 90.0, node.height),
            (MotionProperty::RotationDeg, -18.0, node.rotation_deg),
        ] {
            node.keyframes.push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property,
                value: first,
                interpolation: MotionInterpolation::Linear,
            });
            node.keyframes.push(CanvasKeyframe {
                at: RationalTime::new(1, 1).unwrap(),
                property,
                value: last,
                interpolation: MotionInterpolation::EaseOutCubic,
            });
        }
    }

    #[test]
    fn smoothstep_keys_preserve_geometry_and_opacity_on_output_frames() {
        use semwright_motion_authoring::NativeOp;

        let mut project = fixture_project();
        let mut shape = node("shape", None);
        shape.x = 720.0;
        shape.y = 460.0;
        shape.width = 480.0;
        shape.height = 160.0;
        let end = RationalTime::new(24, 30).unwrap();
        for (property, from, to) in [
            (MotionProperty::X, 700.0, shape.x),
            (MotionProperty::Y, 440.0, shape.y),
            (MotionProperty::Width, 280.0, shape.width),
            (MotionProperty::Height, 90.0, shape.height),
            (MotionProperty::RotationDeg, -18.0, shape.rotation_deg),
        ] {
            shape.keyframes.extend([
                CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property,
                    value: from,
                    interpolation: MotionInterpolation::Linear,
                },
                CanvasKeyframe {
                    at: end,
                    property,
                    value: to,
                    interpolation: MotionInterpolation::EaseInOut,
                },
            ]);
        }
        shape.keyframes.extend([
            CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::Opacity,
                value: 0.0,
                interpolation: MotionInterpolation::Linear,
            },
            CanvasKeyframe {
                at: end,
                property: MotionProperty::Opacity,
                value: 1.0,
                interpolation: MotionInterpolation::EaseInOut,
            },
        ]);
        let target = subject_id(shape.id, None);
        project.scenes[0].nodes[0] = shape;
        let saved = project.clone();
        let segments = build_motion_canvas_segments(
            &project,
            project.deliverables[0].id,
            &fixture_options(&project),
        )
        .expect("frame-aligned smoothstep geometry must realize");
        let realized = realize(&segments[0].film).unwrap();
        let mut writes = BTreeMap::<RationalTime, Vec<(Channel, Operand)>>::new();
        let mut alpha = BTreeMap::new();
        for instruction in &realized.instructions {
            match &instruction.operation {
                NativeOp::Set {
                    target: actual,
                    channel,
                    value,
                } if actual == &target => {
                    assert_eq!(instruction.duration, RationalTime::ZERO);
                    writes
                        .entry(instruction.start)
                        .or_default()
                        .push((*channel, value.clone()));
                }
                NativeOp::Tween {
                    target: actual,
                    channel: Channel::Opacity,
                    from: None,
                    to: Operand::Number(value),
                } if actual == &target => {
                    assert_eq!(instruction.duration, RationalTime::new(1, 120).unwrap());
                    assert_eq!(instruction.easing, MotionEasing::Linear);
                    assert!(alpha.insert(instruction.start, *value).is_none());
                }
                NativeOp::Tween { target: actual, .. } if actual == &target => {
                    panic!("smoothstep must use its clock-aligned sampled values");
                }
                _ => {}
            }
        }
        let near = |actual: f64, expected: f64| assert!((actual - expected).abs() < 1e-9);
        assert_eq!(writes.len(), 25);
        assert_eq!(alpha.len(), 24);
        assert!(
            !segments[0].film.sequences[0].beats[0].shots[0]
                .subjects
                .iter()
                .find(|subject| subject.id == target)
                .unwrap()
                .initially_visible
        );
        for frame in 0..=24 {
            let clock = RationalTime::new(2 * frame + 1, 60).unwrap();
            let progress = frame as f64 / 24.0;
            let eased = progress * progress * (3.0 - 2.0 * progress);
            let values = &writes[&clock];
            if frame > 0 {
                near(alpha[&RationalTime::new(2 * frame - 1, 60).unwrap()], eased);
            }
            assert_eq!(values.len(), 4);
            for (channel, value) in values {
                match (channel, value) {
                    (Channel::Position, Operand::Vector(point)) => {
                        near(point.x, -120.0 + 120.0 * eased);
                        near(point.y, -55.0 + 55.0 * eased);
                    }
                    (Channel::Width, Operand::Number(value)) => near(*value, 280.0 + 200.0 * eased),
                    (Channel::Height, Operand::Number(value)) => near(*value, 90.0 + 70.0 * eased),
                    (Channel::Rotation, Operand::Number(value)) => {
                        near(*value, -18.0 + 18.0 * eased)
                    }
                    _ => panic!("unexpected projected geometry: {channel:?} {value:?}"),
                }
            }
        }
        assert_eq!(project, saved);

        let mut oversized = project.clone();
        let shape = oversized.scenes[0].nodes[0].clone();
        for _ in 0..2 {
            let mut copy = shape.clone();
            copy.id = Uuid::now_v7();
            oversized.scenes[0].nodes.push(copy);
        }
        let error = build_motion_canvas_segments(
            &oversized,
            oversized.deliverables[0].id,
            &fixture_options(&oversized),
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(
            error.message.contains("128 bounded temporal spans"),
            "{error:?}"
        );
    }

    #[test]
    fn exact_shape_resize_and_rotation_preserve_top_left_authoring_in_native_tweens() {
        use semwright_motion_authoring::NativeOp;
        let mut project = fixture_project();
        let mut shape = node("shape", None);
        shape.x = 720.0;
        shape.y = 460.0;
        shape.width = 480.0;
        shape.height = 160.0;
        let saved_baseline = shape.clone();
        set_exact_geometry_keys(&mut shape);
        for (property, first, last) in [
            (MotionProperty::X, 700.0, shape.x),
            (MotionProperty::Y, 440.0, shape.y),
        ] {
            shape.keyframes.push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property,
                value: first,
                interpolation: MotionInterpolation::Linear,
            });
            shape.keyframes.push(CanvasKeyframe {
                at: RationalTime::new(1, 1).unwrap(),
                property,
                value: last,
                interpolation: MotionInterpolation::EaseOutCubic,
            });
        }
        let rendered_id = shape.id;
        project.scenes[0].nodes[0] = shape.clone();
        let options = fixture_options(&project);
        let segment = &build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap()[0];
        let shot = &segment.film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.motion.len(), 4);
        let compiled = realize(&segment.film).unwrap();
        let (output, projection) = output_profile(
            &project.deliverables[0],
            options.frame_rate,
            &[&project.scenes[0], &project.scenes[1]],
        )
        .unwrap();
        let source_projection = scene_layout_projection(&project.scenes[0], projection);
        let mut start = shape.clone();
        start.x = 700.0;
        start.y = 440.0;
        start.width = 280.0;
        start.height = 90.0;
        start.rotation_deg = -18.0;
        let (start_center, end_center) =
            projected_geometry_centers(&shape, &start, source_projection, &output);
        let mut channels = BTreeSet::new();
        for instruction in &compiled.instructions {
            let NativeOp::Tween {
                target,
                channel,
                from,
                to,
            } = &instruction.operation
            else {
                continue;
            };
            if target != &subject_id(rendered_id, None) {
                continue;
            }
            assert!(channels.insert(*channel));
            assert_eq!(instruction.start, RationalTime::ZERO);
            assert_eq!(instruction.duration, RationalTime::new(1, 1).unwrap());
            assert_eq!(instruction.easing, MotionEasing::OutCubic);
            match channel {
                Channel::Position => {
                    assert_eq!(from, &Some(Operand::Vector(start_center)));
                    assert_eq!(to, &Operand::Vector(end_center));
                }
                Channel::Width => {
                    assert_eq!(
                        from,
                        &Some(Operand::Number(280.0 * source_projection.object_scale))
                    );
                    assert_eq!(to, &Operand::Number(480.0 * source_projection.object_scale));
                }
                Channel::Height => {
                    assert_eq!(
                        from,
                        &Some(Operand::Number(90.0 * source_projection.object_scale))
                    );
                    assert_eq!(to, &Operand::Number(160.0 * source_projection.object_scale));
                }
                Channel::Rotation => {
                    assert_eq!(from, &Some(Operand::Number(-18.0)));
                    assert_eq!(to, &Operand::Number(0.0));
                }
                other => panic!("unadmitted native geometry channel: {other:?}"),
            }
        }
        assert_eq!(
            channels,
            BTreeSet::from([
                Channel::Position,
                Channel::Width,
                Channel::Height,
                Channel::Rotation,
            ])
        );
        assert_eq!(project.scenes[0].nodes[0].x, saved_baseline.x);
        assert_eq!(project.scenes[0].nodes[0].y, saved_baseline.y);
        assert_eq!(project.scenes[0].nodes[0].width, saved_baseline.width);
        assert_eq!(project.scenes[0].nodes[0].height, saved_baseline.height);
        assert_eq!(
            project.scenes[0].nodes[0].rotation_deg,
            saved_baseline.rotation_deg
        );
    }

    #[test]
    fn single_axis_native_shape_tween_preserves_unanimated_axis_and_exact_easing() {
        use semwright_motion_authoring::NativeOp;
        for (property, authored_start, interpolation) in [
            (MotionProperty::X, 700.0, MotionInterpolation::EaseOutCubic),
            (MotionProperty::Y, 440.0, MotionInterpolation::Linear),
        ] {
            let mut project = fixture_project();
            let mut shape = node("rectangle", None);
            shape.x = 720.0;
            shape.y = 460.0;
            shape.width = 480.0;
            shape.height = 160.0;
            let terminal = match property {
                MotionProperty::X => shape.x,
                MotionProperty::Y => shape.y,
                _ => unreachable!("exact single axis only"),
            };
            shape.keyframes = vec![
                CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property,
                    value: authored_start,
                    interpolation: MotionInterpolation::Linear,
                },
                CanvasKeyframe {
                    at: RationalTime::new(1, 1).unwrap(),
                    property,
                    value: terminal,
                    interpolation,
                },
            ];
            let saved = shape.clone();
            let id = shape.id;
            project.scenes[0].nodes[0] = shape;
            let opts = fixture_options(&project);
            let segments =
                build_motion_canvas_segments(&project, project.deliverables[0].id, &opts)
                    .expect("single authored shape axis must be a Native geometry vector");
            let realized = realize(&segments[0].film).unwrap();
            let target = subject_id(id, None);
            let selected = realized
                .instructions
                .iter()
                .filter(|instruction| {
                    matches!(
                        &instruction.operation,
                        NativeOp::Tween {
                            target: actual,
                            channel: Channel::Position,
                            ..
                        } if actual == &target
                    )
                })
                .collect::<Vec<_>>();
            assert_eq!(selected.len(), 1, "{property:?}");
            assert_eq!(selected[0].start, RationalTime::ZERO);
            assert_eq!(selected[0].duration, RationalTime::new(1, 1).unwrap());
            assert_eq!(
                selected[0].easing,
                match interpolation {
                    MotionInterpolation::EaseOutCubic => MotionEasing::OutCubic,
                    MotionInterpolation::Linear => MotionEasing::Linear,
                    _ => unreachable!(),
                },
            );
            let expected = match property {
                MotionProperty::X => Point { x: -20.0, y: 0.0 },
                MotionProperty::Y => Point { x: 0.0, y: -20.0 },
                _ => unreachable!(),
            };
            match &selected[0].operation {
                NativeOp::Tween { from, to, .. } => {
                    assert_eq!(from, &Some(Operand::Vector(expected)));
                    assert_eq!(to, &Operand::Vector(Point { x: 0.0, y: 0.0 }));
                }
                _ => unreachable!(),
            }
            assert_eq!(project.scenes[0].nodes[0], saved);
        }
    }

    #[test]
    fn single_axis_shape_tween_rejects_off_frame_or_unmatched_saved_end() {
        for invalid in 0..3 {
            let mut project = fixture_project();
            let mut shape = node("rectangle", None);
            shape.x = 720.0;
            shape.y = 460.0;
            shape.width = 480.0;
            shape.height = 160.0;
            shape.keyframes = vec![
                CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property: MotionProperty::Y,
                    value: 440.0,
                    interpolation: MotionInterpolation::Linear,
                },
                CanvasKeyframe {
                    at: RationalTime::new(1, 1).unwrap(),
                    property: MotionProperty::Y,
                    value: shape.y,
                    interpolation: MotionInterpolation::EaseOutCubic,
                },
            ];
            match invalid {
                0 => shape.keyframes[1].at = RationalTime::new(1, 7).unwrap(),
                1 => shape.keyframes[1].value += 1.0,
                2 => shape.keyframes[0].at = RationalTime::new(1, 30).unwrap(),
                _ => unreachable!(),
            }
            project.scenes[0].nodes[0] = shape;
            let opts = fixture_options(&project);
            let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &opts)
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported, "{invalid}");
        }
    }

    #[test]
    fn authored_hold_shape_geometry_emits_two_exact_frame_sets_not_a_tween() {
        use semwright_motion_authoring::NativeOp;
        let mut project = fixture_project();
        let mut rectangle = node("rectangle", None);
        rectangle.x = 720.0;
        rectangle.y = 460.0;
        rectangle.width = 480.0;
        rectangle.height = 160.0;
        set_exact_geometry_keys(&mut rectangle);
        for key in rectangle
            .keyframes
            .iter_mut()
            .filter(|key| key.at > RationalTime::ZERO)
        {
            key.interpolation = MotionInterpolation::Hold;
        }
        let untouched = rectangle.clone();
        let id = rectangle.id;
        project.scenes[0].nodes[0] = rectangle;
        assert_eq!(scene_span_cost(&project.scenes[0], false), 4); // sequence + beat + two sets
        let options = fixture_options(&project);
        let segments = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .expect("source-bound held shape must become a real native Film");
        let shot = &segments[0].film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.motion.len(), 8);
        assert!(shot.motion.iter().all(|invocation| {
            invocation.easing == MotionEasing::Linear
                && matches!(invocation.primitive, Primitive::GeometrySet { .. })
        }));
        let compiled = realize(&segments[0].film)
            .expect("pinned SDK must accept simultaneous disjoint Set channels");
        let target = subject_id(id, None);
        let selected = compiled
            .instructions
            .iter()
            .filter(|instruction| match &instruction.operation {
                NativeOp::Set { target: source, .. } => source == &target,
                _ => false,
            })
            .collect::<Vec<_>>();
        assert_eq!(selected.len(), 8);
        let mut writes = BTreeSet::new();
        for instruction in selected {
            let NativeOp::Set {
                target: actual_target,
                channel,
                value,
            } = &instruction.operation
            else {
                unreachable!("Selected native set");
            };
            assert_eq!(actual_target, &target);
            assert_eq!(instruction.duration, RationalTime::ZERO);
            assert_eq!(instruction.easing, MotionEasing::Linear);
            assert!(
                instruction.start == RationalTime::ZERO
                    || instruction.start == RationalTime::new(1, 1).unwrap()
            );
            assert!(writes.insert((instruction.start, *channel)));
            if *channel == Channel::Rotation {
                assert_eq!(
                    value,
                    &Operand::Number(if instruction.start == RationalTime::ZERO {
                        -18.0
                    } else {
                        0.0
                    },)
                );
            }
        }
        for channel in [
            Channel::Position,
            Channel::Width,
            Channel::Height,
            Channel::Rotation,
        ] {
            assert!(writes.contains(&(RationalTime::ZERO, channel)));
            assert!(writes.contains(&(RationalTime::new(1, 1).unwrap(), channel)));
        }
        assert_eq!(project.scenes[0].nodes[0], untouched);
    }

    #[test]
    fn single_axis_held_position_uses_instant_native_sets_without_a_size_tween() {
        use semwright_motion_authoring::NativeOp;
        let mut project = fixture_project();
        let mut circle = node("circle", None);
        circle.x = 720.0;
        circle.y = 460.0;
        circle.width = 480.0;
        circle.height = 160.0;
        circle.keyframes = vec![
            CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::X,
                value: 700.0,
                interpolation: MotionInterpolation::Linear,
            },
            CanvasKeyframe {
                at: RationalTime::new(1, 1).unwrap(),
                property: MotionProperty::X,
                value: circle.x,
                interpolation: MotionInterpolation::Hold,
            },
        ];
        let id = circle.id;
        project.scenes[0].nodes[0] = circle.clone();
        let options = fixture_options(&project);
        let result = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .expect("single-axis Hold must lower to exact Native Set");
        let realized = realize(&result[0].film).unwrap();
        let target = subject_id(id, None);
        let mut sets = realized
            .instructions
            .iter()
            .filter_map(|instruction| {
                if let NativeOp::Set {
                    target: source,
                    channel: Channel::Position,
                    value,
                } = &instruction.operation
                    && source == &target
                {
                    return Some((instruction.start, value));
                }
                None
            })
            .collect::<Vec<_>>();
        sets.sort_by_key(|(at, _)| *at);
        assert_eq!(sets.len(), 2);
        assert_eq!(sets[0].0, RationalTime::ZERO);
        assert_eq!(sets[1].0, RationalTime::new(1, 1).unwrap());
        let first = Operand::Vector(Point { x: -20.0, y: 0.0 });
        let second = Operand::Vector(Point { x: 0.0, y: 0.0 });
        assert_eq!(sets[0].1, &first);
        assert_eq!(sets[1].1, &second);
        assert_eq!(project.scenes[0].nodes[0], circle);
    }

    #[test]
    fn authored_hold_geometry_mixed_with_cubic_is_rejected_not_interpolated() {
        let mut project = fixture_project();
        let mut rectangle = node("shape", None);
        rectangle.x = 720.0;
        rectangle.y = 460.0;
        rectangle.width = 480.0;
        rectangle.height = 160.0;
        set_exact_geometry_keys(&mut rectangle);
        rectangle.keyframes[1].interpolation = MotionInterpolation::Hold;
        project.scenes[0].nodes[0] = rectangle;
        let options = fixture_options(&project);
        assert_eq!(
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported,
        );
    }

    #[test]
    fn vertical_replan_shape_resize_keeps_native_top_left_across_asymmetric_scales() {
        let mut project = fixture_project();
        let mut profile = project.deliverables[0].clone();
        profile.width = 1080;
        profile.height = 1920;
        profile.framing_strategy = FramingStrategy::Replan;
        project.deliverables[0] = profile.clone();
        let rate = Rate::new(30, 1).unwrap();
        let (output, projection) =
            output_profile(&profile, rate, &[&project.scenes[0], &project.scenes[1]]).unwrap();
        let projection = scene_layout_projection(&project.scenes[0], projection);
        assert_ne!(projection.position_scale_y, projection.object_scale);
        let mut end = node("shape", None);
        end.x = 720.0;
        end.y = 460.0;
        end.width = 480.0;
        end.height = 160.0;
        let mut start = end.clone();
        start.width = 280.0;
        start.height = 90.0;
        let (from, to) = projected_geometry_centers(&end, &start, projection, &output);
        let half = projection.object_scale / 2.0;
        assert!((from.x - start.width * half - (to.x - end.width * half)).abs() < 1e-8);
        assert!((from.y - start.height * half - (to.y - end.height * half)).abs() < 1e-8);
        // The pre-fix approach incorrectly used position_scale_y for the
        // half-height delta, making exported 9:16 resize drift visibly.
        let naive = canonical_position(&start, projection, &output);
        assert!((naive.y - from.y).abs() > 1.0);

        // Verify that the *real source-to-Film-to-Semwright grammar path*
        // preserves the same center compensation on this 9:16 deliverable.
        // A helper-only test could pass even if the Film never emits it.
        let mut animated = end.clone();
        set_exact_geometry_keys(&mut animated);
        let subject = subject_id(animated.id, None);
        project.scenes[0].nodes[0] = animated;
        let options = fixture_options(&project);
        let segments = build_motion_canvas_segments(&project, profile.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        let realized = realize(&segments[0].film).unwrap();
        let position = realized
            .instructions
            .iter()
            .filter(|instruction| {
                matches!(
                    &instruction.operation,
                    semwright_motion_authoring::NativeOp::Tween {
                        target, channel: Channel::Position, ..
                    } if target == &subject
                )
            })
            .collect::<Vec<_>>();
        assert_eq!(position.len(), 1);
        assert_eq!(position[0].start, RationalTime::ZERO);
        assert_eq!(position[0].duration, RationalTime::new(1, 1).unwrap());
        assert_eq!(position[0].easing, MotionEasing::OutCubic);
        match &position[0].operation {
            semwright_motion_authoring::NativeOp::Tween {
                from: native_from,
                to: native_to,
                ..
            } => {
                assert_eq!(native_from, &Some(Operand::Vector(from)));
                assert_eq!(native_to, &Operand::Vector(to));
            }
            _ => unreachable!("matched the native position tween"),
        }
        let size_channels = realized
            .instructions
            .iter()
            .filter_map(|instruction| match &instruction.operation {
                semwright_motion_authoring::NativeOp::Tween {
                    target, channel, ..
                } if target == &subject && matches!(channel, Channel::Width | Channel::Height) => {
                    Some(*channel)
                }
                _ => None,
            })
            .collect::<BTreeSet<_>>();
        assert_eq!(
            size_channels,
            BTreeSet::from([Channel::Width, Channel::Height])
        );
    }

    #[test]
    fn unrepresentable_shape_geometry_never_silently_becomes_a_native_motion() {
        let mut baseline = fixture_project();
        let mut shape = node("shape", None);
        shape.x = 720.0;
        shape.y = 460.0;
        shape.width = 480.0;
        shape.height = 160.0;
        set_exact_geometry_keys(&mut shape);
        baseline.scenes[0].nodes[0] = shape;
        let profile = baseline.deliverables[0].id;
        let options = fixture_options(&baseline);
        for invalid_case in 0..6 {
            let mut candidate = baseline.clone();
            let keys = &mut candidate.scenes[0].nodes[0].keyframes;
            match invalid_case {
                0 => keys[1].interpolation = MotionInterpolation::EaseInOut,
                1 => keys[1].at = RationalTime::new(1, 7).unwrap(),
                2 => keys[3].at = RationalTime::new(1, 2).unwrap(),
                3 => keys[5].value = 5.0,
                4 => keys.push(CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property: MotionProperty::Opacity,
                    value: 0.0,
                    interpolation: MotionInterpolation::Linear,
                }),
                5 => keys[0].value = 1900.0,
                _ => unreachable!(),
            }
            let result = build_motion_canvas_segments(&candidate, profile, &options);
            let rejection = result.unwrap_err();
            assert_eq!(rejection.code, ErrorCode::Unsupported);
            if invalid_case == 5 {
                assert!(rejection.message.contains("safe area"));
            }
        }
        let mut text = baseline;
        let mut source_text = node("text", Some("Rendered source text"));
        source_text.x = 720.0;
        source_text.y = 460.0;
        source_text.width = 480.0;
        source_text.height = 160.0;
        source_text.keyframes = text.scenes[0].nodes[0].keyframes.clone();
        text.scenes[0].nodes[0] = source_text;
        assert_eq!(
            build_motion_canvas_segments(&text, profile, &options)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
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
        let root_id = subject_id(text_node, None);
        let subjects = &segments[0].film.sequences[0].beats[0].shots[0].subjects;
        let subject = subjects
            .iter()
            .find(|subject| subject.id == root_id)
            .unwrap();
        // One authored line remains one native text subject; only saved hard
        // breaks create editable line leaves.
        assert_eq!(subject.parent, None);
        assert!(
            !subjects
                .iter()
                .any(|item| item.id.starts_with(&format!("{root_id}-line-")))
        );
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
    fn native_film_refuses_a_tampered_historical_metric_notice() {
        use motionwright_domain::{HeroConfig, HeroLayout, hero_node_id};

        let mut project = Project::new("Source caption historical import").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Metric study".into(),
                objective: "A statement not yet supported by source measurements".into(),
                duration_seconds: 6,
            })
            .unwrap();
        let id = Uuid::new_v4();
        let scene_id = project.scenes[0].id;
        project
            .apply_change(&Change::UpsertProductHero {
                instance_id: id,
                scene_id,
                config: HeroConfig {
                    layout: HeroLayout::MetricEvidence,
                    headline: "42% growth".into(),
                    body: "This figure still needs independent source checking.".into(),
                    wordmark: "DATA".into(),
                    ..HeroConfig::default()
                },
            })
            .unwrap();

        let notice_id = hero_node_id(id, "disclosure");
        project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == notice_id)
            .unwrap()
            .text = Some("APPROVED FACT".into());
        // Historical reads can return an old authored document unchanged.
        assert!(project.validate().is_ok());
        // A native media export cannot silently legitimize its missing notice.
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: vec![],
        };
        let error = build_motion_canvas_segments(&project, project.deliverables[0].id, &options)
            .unwrap_err();
        assert!(
            error.message.contains("MetricEvidence source disclosure"),
            "native film should refuse a tampered source-not-verified caption: {error:?}"
        );
    }

    #[test]
    fn rational_frame_identity_preserves_partitioned_master_without_accumulated_error() {
        let rate = Rate::new(30_000, 1_001).unwrap();
        let mut project = Project::new("Exact rational master clock").unwrap();
        let mut profile = project.deliverables[0].clone();
        let deliverable_id = profile.id;
        profile.frame_rate = RationalTime::new(30_000, 1_001).unwrap();
        project
            .apply_change(&Change::UpsertDeliverable { profile })
            .unwrap();
        for index in 0..33 {
            project
                .apply_change(&Change::AddScene {
                    name: format!("Rational frame {index:02}"),
                    objective: "Keep the exact authored clock".into(),
                    duration_seconds: 1,
                })
                .unwrap();
            let scene_id = project.scenes.last().unwrap().id;
            project
                .apply_change(&Change::SetSceneDuration {
                    scene_id,
                    duration: rate.at(1).unwrap(),
                })
                .unwrap();
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: node("shape", None),
                })
                .unwrap();
        }
        let original = project.clone();
        let mut options = fixture_options(&project);
        options.frame_rate = rate;
        let segments = build_motion_canvas_segments(&project, deliverable_id, &options).unwrap();
        assert_eq!(segments.len(), 2);
        assert_eq!(segments[0].frame_count, 32);
        assert_eq!(segments[1].frame_count, 1);
        let total_frames = segments
            .iter()
            .map(|segment| segment.frame_count)
            .sum::<u64>();
        let total_duration = segments
            .iter()
            .try_fold(Rational::ZERO, |sum, segment| {
                sum.checked_add(segment.duration)
            })
            .unwrap();
        assert_eq!(total_frames, 33);
        assert_eq!(
            total_duration,
            rate.at(i64::try_from(total_frames).unwrap()).unwrap()
        );
        assert_eq!(
            total_duration
                .checked_sub(rate.at(i64::try_from(total_frames).unwrap()).unwrap())
                .unwrap(),
            Rational::ZERO,
        );
        for (segment, first_scene) in [(&segments[0], 0usize), (&segments[1], 32)] {
            assert_eq!(segment.film.output.frame_rate, rate);
            assert_eq!(
                segment.global_start,
                rate.at(i64::try_from(first_scene).unwrap()).unwrap()
            );
            assert_eq!(
                segment.duration,
                rate.at(i64::try_from(segment.frame_count).unwrap())
                    .unwrap()
            );
            let realized = realize(&segment.film).unwrap();
            assert_eq!(
                realized.schedule.frame_count(&segment.film.output).unwrap(),
                segment.frame_count
            );
            for (offset, beat) in segment.film.sequences[0].beats.iter().enumerate() {
                let interval = realized.schedule.interval(&beat.shots[0].span_id).unwrap();
                let source = &project.scenes[first_scene + offset];
                assert_eq!(
                    interval.start,
                    rate.at(i64::try_from(offset).unwrap()).unwrap()
                );
                assert_eq!(
                    interval.end,
                    rate.at(i64::try_from(offset + 1).unwrap()).unwrap()
                );
                assert_eq!(
                    segment.global_start.checked_add(interval.start).unwrap(),
                    source.start,
                );
                assert_eq!(interval.duration().unwrap(), source.duration);
            }
        }
        let last = segments.last().unwrap();
        assert_eq!(
            last.global_start.checked_add(last.duration).unwrap(),
            rate.at(33).unwrap(),
        );
        assert_eq!(
            project, original,
            "clock admission must not retime the source"
        );
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
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("saved_deliverable_rate=30/1"));
        assert!(error.message.contains("requested_output_rate=24/1"));
        assert!(error.message.contains("preserve-saved-frame-rate-v1"));
    }
    #[test]
    fn selected_brand_source_is_editable_and_its_digest_changes_the_film() {
        use motionwright_domain::{Asset, FontRight, RightsStatus};
        let mut project = fixture_project();
        let asset_id = Uuid::new_v4();
        project.assets.push(Asset {
            id: asset_id,
            name: "Brand source".into(),
            media_type: "font/ttf".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: Some("source-only-rights-fixture".into()),
        });
        project.visual_language.fonts.push(FontRight {
            family: "Brand Family".into(),
            source: format!("asset:{asset_id}"),
            rights_status: RightsStatus::Cleared,
        });
        for scene in &mut project.scenes {
            for node in &mut scene.nodes {
                if node.kind == "text" {
                    node.style.font_family = Some("Brand Family".into());
                }
            }
        }
        let mut options = fixture_options(&project);
        options.font_family = "Brand Family".into();
        let master = project.deliverables[0].id;
        let original = serde_json::to_vec(&project).unwrap();
        let before = build_motion_canvas_segments(&project, master, &options).unwrap();
        assert_eq!(serde_json::to_vec(&project).unwrap(), original);
        assert_eq!(before[0].film.assets.len(), 1);
        assert_eq!(
            before[0]
                .film
                .editorial
                .font
                .asset_digest
                .as_ref()
                .unwrap()
                .as_str(),
            "a".repeat(64)
        );
        assert_eq!(before[0].film.editorial.font.fallback, FontFallback::Deny);
        let old_digest = Digest::of_bytes(&serde_json::to_vec(&before[0].film).unwrap());
        project
            .assets
            .iter_mut()
            .find(|asset| asset.id == asset_id)
            .unwrap()
            .content_sha256 = Some("b".repeat(64));
        let after = build_motion_canvas_segments(&project, master, &options).unwrap();
        assert_ne!(
            old_digest,
            Digest::of_bytes(&serde_json::to_vec(&after[0].film).unwrap())
        );
        assert_eq!(before[0].film.sequences, after[0].film.sequences);
        let reopened: Project = serde_json::from_slice(&original).unwrap();
        assert_eq!(
            reopened.visual_language.fonts,
            project.visual_language.fonts
        );
        assert_eq!(reopened.scenes, project.scenes);
    }

    fn attach_source_guard_graph(project: &mut Project, scene_index: usize, profile_index: usize) {
        let mut selected = project.clone();
        selected.scenes.swap(0, scene_index);
        selected.deliverables.swap(0, profile_index);
        let mut graph = graph_source_guard_fixture::graph_for(&selected, false);
        graph.time_range.start = motionwright_domain::RationalTime::new(0, 1).unwrap();
        graph.time_range.duration = motionwright_domain::RationalTime::new(1, 1).unwrap();
        let change = motionwright_domain::Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        };
        if selected.scenes[0].renderer == motionwright_domain::RendererKind::ManimCommunity {
            project.apply_change(&change).unwrap();
        } else {
            // These consumer guards must also inspect historical snapshots.
            // New Graph writes remain restricted to Manim Community.
            let before = project.clone();
            let error = project.apply_change(&change).unwrap_err();
            assert!(matches!(
                error,
                motionwright_domain::DomainError::Invalid(message)
                    if message == "Semantic Graph source requires the declared Manim Community scene"
            ));
            assert_eq!(*project, before);
            project.production_design.semantic_graphs.push(graph);
        }
        project.validate().unwrap();
    }

    #[test]
    fn motion_canvas_runs_reject_selected_scene_graph() {
        let mut project = fixture_project();
        assert!(
            !motion_canvas_runs(&project, &project.deliverables[0])
                .unwrap()
                .is_empty()
        );
        attach_source_guard_graph(&mut project, 0, 0);
        let before = project.clone();
        let error = motion_canvas_runs(&project, &project.deliverables[0]).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert_eq!(project, before);
    }

    #[test]
    fn motion_canvas_runs_preserve_other_profile_graph_scope() {
        let mut project = fixture_project();
        let mut other_profile = project.deliverables[0].clone();
        other_profile.id = Uuid::new_v4();
        other_profile.name = "Graph-only source guard profile".into();
        project.deliverables.push(other_profile);
        project.validate().unwrap();
        let baseline = motion_canvas_runs(&project, &project.deliverables[0]).unwrap();
        assert!(!baseline.is_empty());
        let other_profile_index = project.deliverables.len() - 1;
        attach_source_guard_graph(&mut project, 0, other_profile_index);
        let before = project.clone();
        let runs = motion_canvas_runs(&project, &project.deliverables[0]).unwrap();
        assert_eq!(runs, baseline);
        assert_eq!(project, before);
    }

    #[test]
    fn motion_canvas_runs_preserve_other_renderer_graph_scope() {
        let mut project = fixture_project();
        project.scenes[1].renderer = RendererKind::ManimCommunity;
        project.scenes[1].nodes.clear();
        project.scenes[1].beats.clear();
        project.validate().unwrap();
        let baseline = motion_canvas_runs(&project, &project.deliverables[0]).unwrap();
        assert!(!baseline.is_empty());
        attach_source_guard_graph(&mut project, 1, 0);
        let before = project.clone();
        let runs = motion_canvas_runs(&project, &project.deliverables[0]).unwrap();
        assert_eq!(runs, baseline);
        assert_eq!(project, before);
    }

    #[test]
    fn native_window_maps_saved_cut_order_without_reauthoring_the_film() {
        let mut project = fixture_project();
        let first = project.scenes[0].id;
        let second = project.scenes[1].id;
        let rate = Rate::new(30, 1).unwrap();
        let first_frames = exact_window_frame(project.scenes[0].duration, rate).unwrap();
        let second_frames = exact_window_frame(project.scenes[1].duration, rate).unwrap();
        let mut profile = project.deliverables[0].clone();
        profile.included_scene_ids = vec![second, first];
        let before_rejected_cut = project.clone();
        assert!(matches!(
            project.apply_change(&Change::UpsertDeliverable {
                profile: profile.clone(),
            }),
            Err(motionwright_domain::DomainError::Invalid(message))
                if message == "deliverable cut must preserve approved project scene order"
        ));
        assert_eq!(project, before_rejected_cut);
        // A saved cut follows the approved project order. Author that order
        // through its canonical change before constructing the valid fixture.
        project
            .apply_change(&Change::MoveScene {
                scene_id: first,
                to_index: 1,
            })
            .unwrap();
        // An omitted source scene keeps this a real source-to-cut offset
        // mapping instead of an identity mapping after the approved reorder.
        project
            .apply_change(&Change::AddScene {
                name: "Omitted source opening".into(),
                objective: "Remain outside the saved cut".into(),
                duration_seconds: 1,
            })
            .unwrap();
        let omitted = project.scenes.last().unwrap().id;
        project
            .apply_change(&Change::MoveScene {
                scene_id: omitted,
                to_index: 0,
            })
            .unwrap();
        assert!(!profile.included_scene_ids.contains(&omitted));
        let source_scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == first)
            .unwrap();
        let source_start = exact_window_frame(source_scene.start, rate).unwrap();
        assert!(source_start > second_frames);
        project
            .apply_change(&Change::UpsertDeliverable { profile })
            .unwrap();
        let before = project.clone();
        let options = fixture_options(&project);
        let original =
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap();
        let mapped = build_motion_canvas_window(
            &project,
            project.deliverables[0].id,
            first,
            motionwright_domain::SourceFrameWindow {
                start: source_start + 1,
                end: source_start + first_frames - 1,
            },
            &options,
        )
        .unwrap();
        assert_eq!(mapped.segment_index, 0);
        assert_eq!(mapped.segment_window.start, second_frames + 1);
        assert_eq!(mapped.segment_window.end, second_frames + first_frames - 1);
        assert_eq!(mapped.segment, original[0]);
        assert_eq!(project, before);
    }

    #[test]
    fn native_window_rejects_cross_scene_excluded_and_fractional_source_frames() {
        let mut project = fixture_project();
        let scene_id = project.scenes[0].id;
        let options = fixture_options(&project);
        let end = exact_window_frame(segment_end(&project.scenes[0]).unwrap(), options.frame_rate)
            .unwrap();
        assert!(
            build_motion_canvas_window(
                &project,
                project.deliverables[0].id,
                scene_id,
                motionwright_domain::SourceFrameWindow {
                    start: end - 1,
                    end: end + 1
                },
                &options
            )
            .is_err()
        );
        let mut profile = project.deliverables[0].clone();
        profile.included_scene_ids = vec![project.scenes[1].id];
        project
            .apply_change(&Change::UpsertDeliverable { profile })
            .unwrap();
        assert!(
            build_motion_canvas_window(
                &project,
                project.deliverables[0].id,
                scene_id,
                motionwright_domain::SourceFrameWindow { start: 0, end: 1 },
                &options
            )
            .is_err()
        );
        assert!(
            exact_window_frame(RationalTime::new(1, 31).unwrap(), Rate::new(30, 1).unwrap())
                .is_err()
        );
        assert!(
            exact_window_frame(RationalTime::new(-1, 1).unwrap(), Rate::new(30, 1).unwrap())
                .is_err()
        );
    }

    #[test]
    fn native_window_uses_exact_rational_frames_and_rejects_a_different_profile_rate() {
        let rate = Rate::new(30_000, 1_001).unwrap();
        for frame in [0, 1, 29, 30_000, 1_000_000] {
            assert_eq!(
                exact_window_frame(rate.at(frame).unwrap(), rate).unwrap(),
                frame as u64
            );
        }
        let project = fixture_project();
        let mut options = fixture_options(&project);
        options.frame_rate = rate;
        assert!(
            build_motion_canvas_window(
                &project,
                project.deliverables[0].id,
                project.scenes[0].id,
                motionwright_domain::SourceFrameWindow { start: 1, end: 2 },
                &options
            )
            .is_err()
        );
    }

    fn native_boundary_test_draft(scene_id: Uuid) -> motionwright_domain::SceneBoundary {
        use motionwright_domain::{SceneBoundary, SceneBoundaryDefinition, SceneBoundaryInput};
        SceneBoundary {
            version: motionwright_domain::SCENE_BOUNDARY_VERSION,
            id: Uuid::now_v7(),
            scene_id,
            input: SceneBoundaryInput::SelfContained,
            definition: SceneBoundaryDefinition::DraftIncomplete {
                source: None,
                entry: None,
                exit: None,
            },
        }
    }

    #[test]
    fn native_boundary_full_rejects_draft_but_window_scope_is_run_local() {
        let mut project = fixture_project();
        let first = project.scenes[0].id;
        project.scenes[1].renderer = RendererKind::Blender;
        project
            .apply_change(&Change::AddScene {
                name: "Second Motion run".into(),
                objective: "Keep selection local to the actual run".into(),
                duration_seconds: 1,
            })
            .unwrap();
        let second = project.scenes[2].id;
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id: second,
                node: node("shape", None),
            })
            .unwrap();
        project
            .apply_change(&Change::UpsertSceneBoundary {
                boundary: Box::new(native_boundary_test_draft(second)),
            })
            .unwrap();
        let before = project.clone();
        let profile = project.deliverables[0].id;
        let options = fixture_options(&project);

        let error = build_motion_canvas_segments(&project, profile, &options).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert!(error.message.contains("SceneBoundary"));

        let first_window = build_motion_canvas_window(
            &project,
            profile,
            first,
            motionwright_domain::SourceFrameWindow { start: 10, end: 20 },
            &options,
        )
        .unwrap();
        assert_eq!(first_window.segment_index, 0);
        assert_eq!(first_window.segment.scene_ids, vec![first]);
        assert!(
            crate::native_scene_boundary::ExecutionScope::motion(
                &project,
                profile,
                &options,
                &first_window.segment.scene_ids,
            )
            .is_ok()
        );

        let second_window = build_motion_canvas_window(
            &project,
            profile,
            second,
            motionwright_domain::SourceFrameWindow {
                start: 90,
                end: 100,
            },
            &options,
        )
        .unwrap();
        assert_eq!(second_window.segment_index, 1);
        assert_eq!(second_window.segment.scene_ids, vec![second]);
        let error = crate::native_scene_boundary::ExecutionScope::motion(
            &project,
            profile,
            &options,
            &second_window.segment.scene_ids,
        )
        .err()
        .expect("the selected Draft must not acquire an execution scope");
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert!(error.message.contains("SceneBoundary"));
        assert_eq!(project, before);
    }

    #[test]
    fn mapped_boundary_checks_real_cut_pose_before_native_projection() {
        use motionwright_domain::{
            SceneBoundary, SceneBoundaryAppearance, SceneBoundaryArchetype,
            SceneBoundaryDefinition, SceneBoundaryEndpoint, SceneBoundaryFilmOptions,
            SceneBoundaryInput, SceneBoundaryNarrativeRole,
        };
        for case in [
            "match",
            "editorial",
            "envelope_visible",
            "constant_visible",
            "envelope_exit_invisible",
            "envelope_entry_invisible",
            "constant_invisible",
            "jump",
            "appearance",
            "invisible",
            "snapshot",
            "reordered",
        ] {
            let mut project = fixture_project();
            let mut from = node("rectangle", None);
            from.x = 650.0;
            from.y = 350.0;
            from.width = 250.0;
            from.height = 180.0;
            let mut to = from.clone();
            to.id = Uuid::now_v7();
            to.x += 80.0;
            for (at, x, y) in [
                (
                    RationalTime::ZERO,
                    from.x + if case == "jump" { 25.0 } else { 0.0 },
                    from.y,
                ),
                (RationalTime::new(1, 2).unwrap(), to.x, to.y),
            ] {
                for (property, value) in [(MotionProperty::X, x), (MotionProperty::Y, y)] {
                    to.keyframes.push(CanvasKeyframe {
                        at,
                        property,
                        value,
                        interpolation: MotionInterpolation::Linear,
                    });
                }
            }
            if case == "appearance" {
                to.style.fill = Some("#f05a32".into());
            }
            if case == "invisible" {
                for (at, value) in [
                    (RationalTime::ZERO, 0.0),
                    (RationalTime::new(1, 2).unwrap(), 1.0),
                ] {
                    to.keyframes.push(CanvasKeyframe {
                        at,
                        property: MotionProperty::Opacity,
                        value,
                        interpolation: MotionInterpolation::Linear,
                    });
                }
            }
            let opacity = |target: &mut CanvasNode, values: [f64; 3]| {
                for (frame, value) in [0, 15, 29].into_iter().zip(values) {
                    target.keyframes.push(CanvasKeyframe {
                        at: RationalTime::new(frame, 30).unwrap(),
                        property: MotionProperty::Opacity,
                        value,
                        interpolation: MotionInterpolation::Linear,
                    });
                }
            };
            match case {
                "envelope_visible" => {
                    opacity(&mut from, [1.0, 0.0, 1.0]);
                    opacity(&mut to, [1.0, 0.0, 1.0]);
                }
                "constant_visible" => {
                    opacity(&mut from, [1.0; 3]);
                    opacity(&mut to, [1.0; 3]);
                }
                "envelope_exit_invisible" => opacity(&mut from, [0.0, 1.0, 0.0]),
                "envelope_entry_invisible" => opacity(&mut to, [0.0, 1.0, 0.0]),
                "constant_invisible" => opacity(&mut from, [0.0; 3]),
                _ => {}
            }
            project.scenes[0].nodes = vec![from.clone()];
            project.scenes[1].nodes = vec![to.clone()];
            if case == "editorial" {
                for scene in &mut project.scenes {
                    let half =
                        RationalTime::new(scene.duration.num, scene.duration.den * 2).unwrap();
                    scene.beats = [RationalTime::ZERO, half]
                        .into_iter()
                        .map(|start| SceneBeat {
                            id: Uuid::now_v7(),
                            label: "Preserved editorial beat".into(),
                            objective: "Keep focal continuity at the actual scene cut".into(),
                            start,
                            duration: half,
                        })
                        .collect();
                }
            }
            let options = fixture_options(&project);
            let profile = project.deliverables[0].id;
            let actual = SceneBoundaryFilmOptions {
                frame_rate: options.frame_rate,
                font_family: options.font_family.clone(),
                mono_font_family: options.mono_font_family.clone(),
                role: SceneBoundaryNarrativeRole::Mechanism,
                archetype: SceneBoundaryArchetype::Statement,
            };
            let make = |project: &Project, index: usize| {
                let scene = &project.scenes[index];
                let endpoint = SceneBoundaryEndpoint {
                    nodes: scene.nodes.clone(),
                    camera: Some(scene.camera.clone()),
                    assets: vec![],
                };
                SceneBoundary {
                    version: motionwright_domain::SCENE_BOUNDARY_VERSION,
                    id: Uuid::now_v7(),
                    scene_id: scene.id,
                    input: SceneBoundaryInput::SelfContained,
                    definition: SceneBoundaryDefinition::Explicit {
                        source: project
                            .capture_scene_boundary_source(scene.id, profile, &actual)
                            .unwrap(),
                        entry: endpoint.clone(),
                        exit: endpoint,
                    },
                }
            };
            let predecessor = make(&project, 0);
            project
                .apply_change(&Change::UpsertSceneBoundary {
                    boundary: Box::new(predecessor.clone()),
                })
                .unwrap();
            let mut successor = make(&project, 1);
            successor.input = SceneBoundaryInput::Predecessor {
                scene_id: Some(predecessor.scene_id),
                boundary_id: Some(predecessor.id),
                exit_sha256: Some(predecessor.bound_exit_sha256().unwrap()),
                appearances: vec![SceneBoundaryAppearance {
                    predecessor_node_id: from.id,
                    successor_node_id: to.id,
                }],
            };
            if case == "snapshot" {
                let SceneBoundaryDefinition::Explicit { entry, .. } = &mut successor.definition
                else {
                    unreachable!()
                };
                entry.nodes[0].x += 10.0;
            }
            project
                .apply_change(&Change::UpsertSceneBoundary {
                    boundary: Box::new(successor),
                })
                .unwrap();
            if case == "reordered" {
                project.deliverables[0].included_scene_ids =
                    vec![project.scenes[1].id, project.scenes[0].id];
            }
            let before = project.clone();
            let result = build_motion_canvas_segments(&project, profile, &options);
            if matches!(
                case,
                "match" | "editorial" | "envelope_visible" | "constant_visible"
            ) {
                let segments = result.unwrap();
                assert!(
                    segments
                        .iter()
                        .all(|segment| realize(&segment.film).is_ok())
                );
            } else {
                let error = result.unwrap_err();
                if case == "reordered" {
                    // Domain rejects a reversed approved cut before Native
                    // continuity projection. Preserve that earlier fence.
                    assert_eq!(error.code, ErrorCode::InvalidArgument);
                    assert!(
                        error
                            .message
                            .contains("deliverable cut must preserve approved project scene order"),
                        "{case}: {error:?}"
                    );
                } else {
                    assert!(
                        error
                            .message
                            .contains("SceneBoundary appearance continuity loss"),
                        "{case}: {error:?}"
                    );
                }
            }
            assert_eq!(project, before);
        }
    }

    #[test]
    fn native_boundary_explicit_compares_actual_film_options() {
        use motionwright_domain::{
            SceneBoundary, SceneBoundaryArchetype, SceneBoundaryDefinition, SceneBoundaryEndpoint,
            SceneBoundaryFilmOptions, SceneBoundaryInput, SceneBoundaryNarrativeRole,
        };
        let mut project = fixture_project();
        let scene_id = project.scenes[0].id;
        let profile = project.deliverables[0].id;
        let options = fixture_options(&project);
        let actual = SceneBoundaryFilmOptions {
            frame_rate: options.frame_rate,
            font_family: options.font_family.clone(),
            mono_font_family: options.mono_font_family.clone(),
            role: SceneBoundaryNarrativeRole::Mechanism,
            archetype: SceneBoundaryArchetype::Statement,
        };
        let source = project
            .capture_scene_boundary_source(scene_id, profile, &actual)
            .unwrap();
        let endpoint = SceneBoundaryEndpoint {
            nodes: project.scenes[0].nodes.clone(),
            camera: Some(project.scenes[0].camera.clone()),
            assets: Vec::new(),
        };
        project
            .apply_change(&Change::UpsertSceneBoundary {
                boundary: Box::new(SceneBoundary {
                    version: motionwright_domain::SCENE_BOUNDARY_VERSION,
                    id: Uuid::now_v7(),
                    scene_id,
                    input: SceneBoundaryInput::SelfContained,
                    definition: SceneBoundaryDefinition::Explicit {
                        source,
                        entry: endpoint.clone(),
                        exit: endpoint,
                    },
                }),
            })
            .unwrap();
        let before = project.clone();
        assert!(build_motion_canvas_segments(&project, profile, &options).is_ok());

        for case in [
            "frame_rate",
            "font_family",
            "mono_font_family",
            "role",
            "archetype",
        ] {
            let mut changed = options.clone();
            match case {
                "frame_rate" => changed.frame_rate = Rate::new(60, 1).unwrap(),
                "font_family" => changed.font_family = "Another primary family".into(),
                "mono_font_family" => changed.mono_font_family = "Another mono family".into(),
                "role" => changed.scene_intents[0].role = NarrativeRole::Evidence,
                "archetype" => changed.scene_intents[0].archetype = Archetype::Comparison,
                _ => unreachable!(),
            }
            let error = crate::native_scene_boundary::guard_motion_scenes(
                &project,
                profile,
                &changed,
                &[scene_id],
            )
            .unwrap_err();
            assert_eq!(error.code, ErrorCode::InvalidArgument, "{case}");
            assert!(error.message.contains("SceneBoundary"), "{case}");
        }
        let mut changed_role = options.clone();
        changed_role.scene_intents[0].role = NarrativeRole::Evidence;
        assert_eq!(
            build_motion_canvas_segments(&project, profile, &changed_role)
                .unwrap_err()
                .code,
            ErrorCode::InvalidArgument,
        );
        assert_eq!(project, before);
    }
}
