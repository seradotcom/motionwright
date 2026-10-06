use motionwright_domain::{
    BlendMode, CanvasNode, CoordinateSpace, Project, RationalTime, RendererKind, Scene,
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

fn flush_run<'a>(runs: &mut Vec<Vec<&'a Scene>>, current: &mut Vec<&'a Scene>) {
    if !current.is_empty() {
        runs.push(std::mem::take(current));
    }
}

fn motion_canvas_runs(project: &Project) -> NativeResult<Vec<Vec<&Scene>>> {
    let mut runs = Vec::new();
    let mut current = Vec::new();
    let mut previous_end: Option<RationalTime> = None;
    let max_duration = max_film_duration()?;

    for scene in &project.scenes {
        let end = segment_end(scene)?;
        if let Some(previous) = previous_end
            && scene.start < previous
        {
            return Err(invalid("Project timeline contains overlapping scenes"));
        }
        previous_end = Some(end);

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
            scene.start != last_end
                || candidate_duration > max_duration
                || current.len() >= MAX_SEQUENCES
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

fn canonical_position(node: &CanvasNode, scale: f64, output: &OutputProfile) -> Point {
    let width = node.width * scale;
    let height = node.height * scale;
    Point {
        x: node.x * scale + width / 2.0 - f64::from(output.width) / 2.0,
        y: node.y * scale + height / 2.0 - f64::from(output.height) / 2.0,
    }
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
        type_scale.insert(id, size);
    }
    if type_scale.is_empty() {
        type_scale.insert("body".into(), 16.0);
    }
    Ok((by_bits, type_scale))
}

fn global_stroke(scenes: &[&Scene]) -> NativeResult<f64> {
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
    Ok(stroke.unwrap_or(0.0))
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

fn subject(
    node: &CanvasNode,
    layer_names: &BTreeMap<i32, String>,
    text_style: &BTreeMap<u64, String>,
    profile_language: &str,
    scale: f64,
    output: &OutputProfile,
    font_family: &str,
) -> NativeResult<(Subject, Vec<VisualConstraint>)> {
    validate_node_projection(node, font_family)?;
    let id = uid("node", node.id);
    let layer = layer_names
        .get(&node.z_index)
        .cloned()
        .ok_or_else(|| invalid("Canvas node layer projection is missing"))?;
    let content = match node.kind.as_str() {
        "text" => {
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
    let mut constraints = vec![VisualConstraint::SafeArea {
        subject: id.clone(),
        tolerance: 0.0,
    }];
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
            parent: node.parent_id.map(|id| uid("node", id)),
            layer,
            content,
            layout: SpatialIntent::Fixed {
                position: canonical_position(node, scale, output),
                size: Size {
                    width: node.width * scale,
                    height: node.height * scale,
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
    width: u32,
    height: u32,
    frame_rate: Rate,
    scenes: &[&Scene],
) -> NativeResult<(OutputProfile, f64)> {
    if u64::from(width) * 1080 != u64::from(height) * 1920 {
        return Err(unsupported(
            "Canonical Motion Canvas projection currently requires a 16:9 deliverable; use a reframed branch for vertical or square output",
        ));
    }
    let scale = f64::from(width) / CANVAS_WIDTH;
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
            width,
            height,
            frame_rate,
            aspect: aspect(width, height),
            safe_area: Insets {
                top: f64::from(height) * safe_margin,
                right: f64::from(width) * safe_margin,
                bottom: f64::from(height) * safe_margin,
                left: f64::from(width) * safe_margin,
            },
        },
        scale,
    ))
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

fn build_segment(
    project: &Project,
    scenes: &[&Scene],
    segment_index: usize,
    width: u32,
    height: u32,
    options: &FilmBuildOptions,
    intents: &HashMap<Uuid, &SceneFilmIntent>,
) -> NativeResult<MotionCanvasSegment> {
    let first = scenes
        .first()
        .copied()
        .ok_or_else(|| invalid("Cannot build an empty Motion Canvas segment"))?;
    let last = scenes.last().copied().expect("segment is nonempty");
    let start = first.start;
    let end = segment_end(last)?;
    let duration = sub(end, start, "Film segment duration underflow")?;
    let (output, scale) = output_profile(width, height, options.frame_rate, scenes)?;
    let (text_style, type_scale) = text_styles(scenes, &options.font_family)?;
    let stroke = global_stroke(scenes)?;

    let mut spans = Vec::with_capacity(scenes.len() * 2);
    let mut constraints = Vec::with_capacity(scenes.len());
    let mut sequences = Vec::with_capacity(scenes.len());

    for scene in scenes {
        if !scene.beats.is_empty() {
            return Err(unsupported(format!(
                "Scene {} has authored beat timing that requires explicit Film beat projection",
                scene.id
            )));
        }
        if scene.nodes.is_empty() {
            return Err(unsupported(format!(
                "Scene {} has no semantic canvas objects to render",
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
        let shot_span = uid("shotspan", scene.id);
        for (span_id, parent) in [(sequence_span.clone(), false), (shot_span.clone(), true)] {
            spans.push(TemporalSpan {
                id: span_id.clone(),
                minimum: scene.duration,
                preferred: scene.duration,
                maximum: scene.duration,
                anchor: StartAnchor::Absolute { time: local_start },
                preference_priority: 0,
            });
            if parent {
                constraints.push(TemporalConstraint::Contains {
                    id: uid("contains", scene.id),
                    parent: sequence_span.clone(),
                    child: span_id,
                });
            }
        }

        let (layers, layer_names) = layers(scene)?;
        let mut subjects = Vec::with_capacity(scene.nodes.len());
        let mut visual_constraints = Vec::new();
        for node in &scene.nodes {
            let (mapped, mut node_constraints) = subject(
                node,
                &layer_names,
                &text_style,
                project
                    .deliverables
                    .iter()
                    .find(|profile| profile.width == width && profile.height == height)
                    .map(|profile| profile.language.as_str())
                    .unwrap_or("und"),
                scale,
                &output,
                &options.font_family,
            )?;
            subjects.push(mapped);
            visual_constraints.append(&mut node_constraints);
        }

        sequences.push(Sequence {
            id: uid("scene", scene.id),
            span_id: sequence_span,
            beats: vec![AuthoringBeat {
                id: uid("beat", scene.id),
                role: intent.role,
                shots: vec![Shot {
                    id: uid("shot", scene.id),
                    span_id: shot_span,
                    archetype: intent.archetype,
                    subjects,
                    layers,
                    annotations: vec![],
                    captions: vec![],
                    motion: vec![],
                    constraints: visual_constraints,
                }],
            }],
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
            colors: BTreeMap::new(),
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
    let runs = motion_canvas_runs(project)?;
    runs.iter()
        .enumerate()
        .map(|(index, scenes)| {
            build_segment(
                project,
                scenes,
                index,
                deliverable.width,
                deliverable.height,
                options,
                &intents,
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        CanvasKeyframe, CanvasNode, Change, MotionInterpolation, MotionProperty, NodeStyle,
        RationalTime, RendererKind,
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
                font_family: (kind == "text").then(|| "system-ui".into()),
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
        let segments = build_motion_canvas_segments(&project, master.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].scene_ids.len(), 2);
        assert_eq!(segments[0].frame_count, 90);
        assert_eq!(segments[0].film.sequences.len(), 2);
        assert_eq!(
            segments[0].film.timing.duration,
            Rational::new(3, 1).unwrap()
        );
        assert!(realize(&segments[0].film).is_ok());
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
            font_family: "system-ui".into(),
            mono_font_family: "monospace".into(),
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
        assert!(error.message.contains("motion keyframes"));
    }

    #[test]
    fn unsupported_canvas_semantics_fail_closed() {
        let mut project = fixture_project();
        project.scenes[0].nodes[0].opacity = 0.5;
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
    }
}
