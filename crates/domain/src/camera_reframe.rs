//! MW05-E07-03: bounded authored planar camera reframe on canonical Canvas.
//!
//! This is a semantic 2D camera translation projected through the existing
//! Semwright Film Settle primitive. It is NOT a 3D orbit, focal-length change,
//! dolly, optical zoom, perspective reconstruction, or simulated device shot.
//! Both source and destination camera boxes must preserve every authored
//! native Canvas object inside the output-safe 1920x1080 source bounds.
use crate::*;
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

pub const CAMERA_REFRAME_VERSION: u32 = 1;
/// Closed Manim Community source contract; legacy Motion Canvas records stay v1.
pub const CAMERA_REFRAME_MANIM_VERSION: u32 = 2;
/// Explicit bounded detail-and-return on the original planar Motion Canvas.
pub const CAMERA_DETAIL_RETURN_VERSION: u32 = 3;
pub const CAMERA_REFRAME_FPS: i64 = 30;
pub const CAMERA_REFRAME_MAX: usize = 16;
pub const CAMERA_REF_CANVAS_WIDTH: f64 = 1920.0;
pub const CAMERA_REF_CANVAS_HEIGHT: f64 = 1080.0;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CameraReframe {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub component_version: u32,
    pub start_center_x: f64,
    pub start_center_y: f64,
    pub end_center_x: f64,
    pub end_center_y: f64,
    /// Exact 30fps duration; no silent shot/frame interpolation inference.
    pub duration_frames: u16,
    /// Absent preserves the exact v1/v2 wire. Required only for v3.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_detail_return"
    )]
    pub detail_return: Option<CameraDetailReturn>,
}

/// The start/end centers are the same saved context. Approach and return
/// use matching linear position/scale interpolation on one Native group.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CameraDetailReturn {
    pub center_x: f64,
    pub center_y: f64,
    pub zoom: f64,
    pub approach_frames: u16,
    pub hold_frames: u16,
}

fn deserialize_detail_return<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<CameraDetailReturn>, D::Error> {
    CameraDetailReturn::deserialize(deserializer).map(Some)
}

/// A common native transform frame keeps camera translation on a separate
/// hierarchy target from every editable object motion channel.
pub fn camera_uses_composition(scene: &Scene) -> bool {
    scene.nodes.iter().any(|node| {
        !node.keyframes.is_empty()
            || node.rotation_deg != 0.0
            || node.parent_id.is_some()
            || matches!(node.kind.as_str(), "group" | "clip_group")
    })
}

/// One literal source pose and its incoming curve. This is an interpreted
/// read-only view of existing Canvas keyframes, never new persisted state.
#[derive(Debug, Clone)]
pub struct CanvasPosePoint {
    pub at: RationalTime,
    pub pose: CanvasNode,
    pub curve: MotionInterpolation,
}

/// Admit the bounded multi-pose grammar only when every geometry channel
/// has the exact same time slots and incoming interpolation. Alpha remains
/// a separate original channel, validated by its own consumer. Two-pose
/// and single-channel legacy routes continue through their existing code.
pub fn exact_canvas_pose_waypoints(
    node: &CanvasNode,
    scene: &Scene,
    fps: RationalTime,
) -> Result<Option<Vec<CanvasPosePoint>>> {
    let mut slots = BTreeMap::<RationalTime, Vec<&CanvasKeyframe>>::new();
    for key in node
        .keyframes
        .iter()
        .filter(|key| key.property != MotionProperty::Opacity)
    {
        key.validate()?;
        slots.entry(key.at).or_default().push(key);
    }
    let text_pose = node.kind == "text"
        && slots.len() == 2
        && (slots.values().all(|keys| keys.len() == 5)
            || slots
                .values()
                .flatten()
                .any(|key| key.interpolation == MotionInterpolation::Hold));
    let paired_text = text_pose && slots.values().all(|keys| keys.len() == 2);
    if slots.len() <= 2 && !text_pose {
        return Ok(None);
    }
    let fail = || {
        DomainError::Invalid(format!(
            "Canvas node {} requires 3 to 6 complete frame-aligned geometry poses with exact curves and unchanged Clip or Group ancestors",
            node.id
        ))
    };
    if (!(3..=6).contains(&slots.len()) && !text_pose)
        || fps.validate().is_err()
        || fps.num <= 0
        || fps.den <= 0
        || !matches!(
            node.kind.as_str(),
            "shape" | "rectangle" | "circle" | "text"
        )
        || (node.kind == "text" && node.parent_id.is_some())
        || node.coordinate_space != CoordinateSpace::ProjectPixels
        || node.style.blend_mode != BlendMode::Normal
        || !node.relations.is_empty()
        || node.opacity != 1.0
        || scene
            .nodes
            .iter()
            .any(|child| child.parent_id == Some(node.id))
    {
        return Err(fail());
    }
    if node.parent_id.is_some() && static_native_group_parent(node, scene).is_none() {
        return Err(fail());
    }
    let frame_duration = RationalTime::new(fps.den, fps.num).map_err(|_| fail())?;
    let mut points = Vec::with_capacity(slots.len());
    for (at, keys) in slots {
        let numerator = i128::from(at.num) * i128::from(fps.num);
        let denominator = i128::from(at.den) * i128::from(fps.den);
        if at >= scene.duration
            || denominator <= 0
            || numerator < 0
            || numerator % denominator != 0
            || numerator / denominator > 36000
            || keys.len() != if paired_text { 2 } else { 5 }
        {
            return Err(fail());
        }
        let curve = keys[0].interpolation;
        if !matches!(
            curve,
            MotionInterpolation::Linear
                | MotionInterpolation::EaseOutCubic
                | MotionInterpolation::Hold
        ) || (points.is_empty()
            && (at != RationalTime::ZERO || curve != MotionInterpolation::Linear))
            || (curve == MotionInterpolation::Hold
                && at.checked_add(frame_duration).map_err(|_| fail())? > scene.duration)
        {
            return Err(fail());
        }
        let mut seen = BTreeSet::new();
        let mut pose = node.clone();
        pose.keyframes.clear();
        for key in keys {
            if key.interpolation != curve || !seen.insert(key.property) {
                return Err(fail());
            }
            match key.property {
                MotionProperty::X => pose.x = key.value,
                MotionProperty::Y => pose.y = key.value,
                MotionProperty::Width => pose.width = key.value,
                MotionProperty::Height => pose.height = key.value,
                MotionProperty::RotationDeg => pose.rotation_deg = key.value,
                MotionProperty::Opacity => return Err(fail()),
            }
        }
        if paired_text && (!seen.contains(&MotionProperty::X) || !seen.contains(&MotionProperty::Y))
        {
            return Err(fail());
        }
        if pose.width <= 0.0
            || pose.height <= 0.0
            || pose.width > 16384.0
            || pose.height > 16384.0
            || pose.rotation_deg.abs() > 360.0
            || (node.kind == "text"
                && (node.rotation_deg != 0.0
                    || pose.rotation_deg != 0.0
                    || pose.width != node.width
                    || pose.height != node.height))
        {
            return Err(fail());
        }
        points.push(CanvasPosePoint { at, pose, curve });
    }
    let end = &points.last().expect("bounded nonempty poses").pose;
    if end.x != node.x
        || end.y != node.y
        || end.width != node.width
        || end.height != node.height
        || end.rotation_deg != node.rotation_deg
    {
        return Err(fail());
    }
    Ok(Some(points))
}

fn camera_motion_start(node: &CanvasNode, scene: &Scene) -> Result<CanvasNode> {
    let fail = || {
        DomainError::Invalid(format!(
            "Camera composition requires exact paired motion and unchanged Clip or Group ancestors for node {}",
            node.id
        ))
    };
    if node.coordinate_space != CoordinateSpace::ProjectPixels
        || !node.relations.is_empty()
        || node.style.blend_mode != BlendMode::Normal
    {
        return Err(fail());
    }
    if (node.parent_id.is_some() && static_native_group_parent(node, scene).is_none())
        || (matches!(node.kind.as_str(), "group" | "clip_group") && !is_static_native_group(node))
    {
        return Err(fail());
    }
    let mut initial = node.clone();
    if node.keyframes.is_empty() {
        if node.rotation_deg != 0.0 && !native_static_shape_rotation(node) {
            return Err(fail());
        }
        return Ok(initial);
    }
    if (node.rotation_deg != 0.0 && !native_shape_rotation(node))
        || node.opacity != 1.0
        || scene
            .nodes
            .iter()
            .any(|child| child.parent_id == Some(node.id))
    {
        return Err(fail());
    }
    for key in &node.keyframes {
        key.validate()?;
    }
    let geometry = node
        .keyframes
        .iter()
        .any(|key| key.property != MotionProperty::Opacity);
    if geometry
        && !matches!(
            node.kind.as_str(),
            "shape" | "rectangle" | "circle" | "text"
        )
    {
        return Err(fail());
    }
    if node.kind == "text"
        && (node.parent_id.is_some()
            || node.rotation_deg != 0.0
            || node.keyframes.iter().any(|key| match key.property {
                MotionProperty::Width => key.value != node.width,
                MotionProperty::Height => key.value != node.height,
                MotionProperty::RotationDeg => key.value != 0.0,
                _ => false,
            }))
    {
        return Err(fail());
    }
    let x = node
        .keyframes
        .iter()
        .any(|key| key.property == MotionProperty::X);
    let y = node
        .keyframes
        .iter()
        .any(|key| key.property == MotionProperty::Y);
    if x != y {
        return Err(fail());
    }
    let _ = crate::exact_canvas_opacity_spans(
        node,
        scene,
        RationalTime {
            num: CAMERA_REFRAME_FPS,
            den: 1,
        },
    )?;
    let mut geometry_end = None;
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
        keys.sort_by_key(|key| key.at);
        if keys.len() != 2
            || keys[0].at != RationalTime::ZERO
            || keys[1].at <= RationalTime::ZERO
            || keys[1].at >= scene.duration
            || keys[0].interpolation != MotionInterpolation::Linear
            || (i128::from(keys[1].at.num) * 30) % i128::from(keys[1].at.den) != 0
        {
            return Err(fail());
        }
        if !matches!(
            keys[1].interpolation,
            MotionInterpolation::Linear
                | MotionInterpolation::EaseOutCubic
                | MotionInterpolation::Hold
        ) {
            return Err(fail());
        }
        let end = (keys[1].at, keys[1].interpolation);
        if geometry_end.is_some_and(|prior| prior != end) {
            return Err(fail());
        }
        geometry_end = Some(end);
        let saved = match property {
            MotionProperty::X => node.x,
            MotionProperty::Y => node.y,
            MotionProperty::Width => node.width,
            MotionProperty::Height => node.height,
            MotionProperty::RotationDeg => node.rotation_deg,
            MotionProperty::Opacity => unreachable!(),
        };
        if keys[1].value != saved {
            return Err(fail());
        }
        match property {
            MotionProperty::X => initial.x = keys[0].value,
            MotionProperty::Y => initial.y = keys[0].value,
            MotionProperty::Width => initial.width = keys[0].value,
            MotionProperty::Height => initial.height = keys[0].value,
            MotionProperty::RotationDeg => initial.rotation_deg = keys[0].value,
            MotionProperty::Opacity => unreachable!(),
        }
    }
    if initial.width <= 0.0 || initial.height <= 0.0 {
        return Err(fail());
    }
    Ok(initial)
}

impl CameraReframe {
    fn validate_detail_return(&self, scene: &Scene) -> Result<()> {
        let fail = |message: &str| DomainError::Invalid(format!("Camera detail return: {message}"));
        let detail = self
            .detail_return
            .ok_or_else(|| fail("version 3 requires the complete detail parameters"))?;
        if scene.id != self.scene_id
            || scene.renderer != RendererKind::MotionCanvas
            || scene.nodes.is_empty()
            || scene.nodes.len() > 32
        {
            return Err(fail(
                "requires a Motion Canvas scene with 1 to 32 original objects",
            ));
        }
        // The terminal context is immutable during the move. Do not pretend
        // arbitrary source rotation, hierarchy, blend or object animation can
        // be coupled to this exact bounded planar zoom.
        if scene.camera.zoom != 1.0
            || scene.camera.rotation_deg != 0.0
            || self.start_center_x != self.end_center_x
            || self.start_center_y != self.end_center_y
            || scene.camera.center_x != self.end_center_x
            || scene.camera.center_y != self.end_center_y
        {
            return Err(fail(
                "start and return must share the exact saved unit-zoom context",
            ));
        }
        for (value, min, max) in [
            (self.start_center_x, 720.0, 1200.0),
            (self.start_center_y, 320.0, 760.0),
            (detail.center_x, 720.0, 1200.0),
            (detail.center_y, 320.0, 760.0),
            (detail.zoom, 1.05, 1.25),
            (scene.camera.safe_margin, 0.0, 0.49),
        ] {
            if !value.is_finite() || !(min..=max).contains(&value) {
                return Err(fail("invalid bounded center, zoom or safe margin"));
            }
        }
        if (detail.center_x - self.start_center_x).abs() > 80.0
            || (detail.center_y - self.start_center_y).abs() > 80.0
        {
            return Err(fail(
                "detail center may move at most 80 original pixels per axis",
            ));
        }
        let used = u32::from(detail.approach_frames) + u32::from(detail.hold_frames);
        if detail.approach_frames < 12
            || detail.hold_frames < 1
            || self.duration_frames > 180
            || used + 12 > u32::from(self.duration_frames)
            || RationalTime::new(i64::from(self.duration_frames) + 1, 30)
                .map_err(|_| fail("invalid exact frame duration"))?
                > scene.duration
        {
            return Err(fail(
                "approach and return each need 12 frames, a positive hold, total at most 180 frames, and at least one final context frame",
            ));
        }
        for node in &scene.nodes {
            if !matches!(
                node.kind.as_str(),
                "shape" | "rectangle" | "circle" | "text"
            ) || node.parent_id.is_some()
                || !node.relations.is_empty()
                || !node.keyframes.is_empty()
                || node.coordinate_space != CoordinateSpace::ProjectPixels
                || node.opacity != 1.0
                || node.rotation_deg != 0.0
                || node.style.blend_mode != BlendMode::Normal
                || node.style.stroke.is_some()
                || node.style.stroke_width != 0.0
                || ![node.x, node.y, node.width, node.height]
                    .iter()
                    .all(|value| value.is_finite())
                || node.width <= 0.0
                || node.height <= 0.0
            {
                return Err(fail(
                    "requires independent static original shapes/text without rotation, stroke, relation or blend writers",
                ));
            }
        }
        self.validate_detail_return_bounds(
            scene,
            1920.0,
            1080.0,
            scene.camera.safe_margin.max(0.05),
        )
    }

    /// Match the native Replan geometry. With constant source frames, linear
    /// scale and translation give every corner a convex path between these
    /// two endpoints. This is geometry admission, not measured glyph pixels.
    pub fn validate_detail_return_bounds(
        &self,
        scene: &Scene,
        width: f64,
        height: f64,
        margin: f64,
    ) -> Result<()> {
        let detail = self
            .detail_return
            .ok_or_else(|| DomainError::Invalid("Missing camera detail parameters".into()))?;
        let sx = width / 1920.0;
        let sy = height / 1080.0;
        let scale = sx.min(sy);
        if ![width, height, margin].iter().all(|v| v.is_finite())
            || width <= 0.0
            || height <= 0.0
            || !(0.0..0.5).contains(&margin)
        {
            return Err(DomainError::Invalid(
                "Invalid camera detail output bounds".into(),
            ));
        }
        for node in &scene.nodes {
            for (cx, cy, zoom) in [
                (self.start_center_x, self.start_center_y, 1.0),
                (detail.center_x, detail.center_y, detail.zoom),
            ] {
                let x = width / 2.0 + (node.x + node.width / 2.0 - cx) * sx * zoom;
                let y = height / 2.0 + (node.y + node.height / 2.0 - cy) * sy * zoom;
                let hw = node.width * scale * zoom / 2.0;
                let hh = node.height * scale * zoom / 2.0;
                if x - hw + 1e-6 < width * margin
                    || y - hh + 1e-6 < height * margin
                    || x + hw - 1e-6 > width * (1.0 - margin)
                    || y + hh - 1e-6 > height * (1.0 - margin)
                {
                    return Err(DomainError::Invalid(format!(
                        "Camera detail return would crop original Canvas node {} in the selected safe area",
                        node.id
                    )));
                }
            }
        }
        Ok(())
    }

    pub fn validate(&self, scene: &Scene) -> Result<()> {
        if self.component_version == CAMERA_DETAIL_RETURN_VERSION {
            return self.validate_detail_return(scene);
        }
        if self.detail_return.is_some() {
            return Err(DomainError::Invalid(
                "Only camera detail-return version 3 may carry detail parameters".into(),
            ));
        }
        // Resolve the existing source relations once for the source camera
        // envelope. Native repeats projection with the selected output scale;
        // neither consumer mutates the original relation or keyframe list.
        let mut resolved;
        let scene = if self.component_version == CAMERA_REFRAME_VERSION
            && scene.nodes.iter().any(|node| !node.relations.is_empty())
        {
            resolved = scene.clone();
            let tracks = crate::resolve_canvas_follow_tracks(
                scene,
                RationalTime {
                    num: CAMERA_REFRAME_FPS,
                    den: 1,
                },
                false,
            )?;
            for node in &mut resolved.nodes {
                if let Some(keys) = tracks.get(&node.id) {
                    node.keyframes = keys.clone();
                    node.relations.clear();
                }
            }
            let positions = crate::resolve_canvas_alignments(
                &resolved,
                crate::AlignmentScale {
                    x: 1.0,
                    y: 1.0,
                    object: 1.0,
                },
                false,
            )?;
            for node in &mut resolved.nodes {
                if let Some(position) = positions.get(&node.id) {
                    node.x = position.x;
                    node.y = position.y;
                    node.relations.clear();
                }
            }
            &resolved
        } else {
            scene
        };
        let admitted_pair = (self.component_version == CAMERA_REFRAME_VERSION
            && scene.renderer == RendererKind::MotionCanvas)
            || (self.component_version == CAMERA_REFRAME_MANIM_VERSION
                && scene.renderer == RendererKind::ManimCommunity);
        if !admitted_pair
            || scene.id != self.scene_id
            || scene.nodes.is_empty()
            || scene.nodes.len() > 32
            || (self.component_version == CAMERA_REFRAME_MANIM_VERSION
                && (!scene.beats.is_empty()
                    || scene.nodes.iter().any(|node| {
                        matches!(node.kind.as_str(), "group" | "clip_group")
                            || !node.keyframes.is_empty()
                            || node.parent_id.is_some()
                    })))
        {
            return Err(DomainError::Invalid(
                "CameraReframe requires a supported source scene with 1 to 32 objects; the Manim profile also requires an ungrouped, motion-free scene without editorial beats".into(),
            ));
        }
        if scene.camera.zoom != 1.0 || scene.camera.rotation_deg != 0.0 {
            return Err(DomainError::Invalid(
                "Planar camera reframe cannot replace unsupported optical zoom or rotation".into(),
            ));
        }
        // Manim Community v2 uses the existing bounded static shape source.
        // A domain-level save must not admit a feature that this native
        // contributor silently drops (text, opacity, stroke, rotation, font,
        // hierarchy, non-pixel coordinates or unsupported fill).
        if self.component_version == CAMERA_REFRAME_MANIM_VERSION {
            if scene.camera.safe_margin < 0.05 {
                return Err(DomainError::Invalid(
                    "Manim camera requires an authored safe margin of at least 0.05".into(),
                ));
            }
            for node in &scene.nodes {
                if !matches!(node.kind.as_str(), "rectangle" | "circle")
                    || node.coordinate_space != CoordinateSpace::ProjectPixels
                    || !node.relations.is_empty()
                    || node.opacity != 1.0
                    || node.rotation_deg != 0.0
                    || node.text.is_some()
                    || node.style.stroke.is_some()
                    || node.style.stroke_width != 0.0
                    || node.style.blend_mode != BlendMode::Normal
                    || node.style.font_family.is_some()
                    || node.style.font_size.is_some()
                    || node.style.font_weight.is_some()
                    || node.style.line_height.is_some()
                    || (node.kind == "circle" && node.width != node.height)
                {
                    return Err(DomainError::Invalid(format!(
                        "Manim camera source cannot preserve the authored shape semantics of node {}",
                        node.id
                    )));
                }
                let fill = node.style.fill.as_deref().ok_or_else(|| {
                    DomainError::Invalid(format!(
                        "Manim camera requires explicit opaque hex fill for node {}",
                        node.id
                    ))
                })?;
                if !fill.starts_with('#')
                    || fill.len() != 7
                    || !fill[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
                {
                    return Err(DomainError::Invalid(format!(
                        "Manim camera requires #RRGGBB fill without inferred alpha on node {}",
                        node.id
                    )));
                }
            }
        }
        for (label, value, min, max) in [
            ("start x", self.start_center_x, 720.0, 1200.0),
            ("end x", self.end_center_x, 720.0, 1200.0),
            ("start y", self.start_center_y, 320.0, 760.0),
            ("end y", self.end_center_y, 320.0, 760.0),
        ] {
            if !value.is_finite() || !(min..=max).contains(&value) {
                return Err(DomainError::Invalid(format!(
                    "CameraReframe {label} center is outside the bounded source frame"
                )));
            }
        }
        let shift_x = self.end_center_x - self.start_center_x;
        let shift_y = self.end_center_y - self.start_center_y;
        if shift_x.abs() > 80.0
            || shift_y.abs() > 80.0
            || (shift_x.abs() < 0.001 && shift_y.abs() < 0.001)
        {
            return Err(DomainError::Invalid(
                "CameraReframe needs a real, bounded center displacement of at most 80 source pixels per axis".into(),
            ));
        }
        let duration = RationalTime::new(i64::from(self.duration_frames), CAMERA_REFRAME_FPS)
            .expect("fixed camera frame-rate has a nonzero denominator");
        if !(12..=180).contains(&self.duration_frames) || duration > scene.duration {
            return Err(DomainError::Invalid(
                "CameraReframe frames must be 12..=180 and fit inside the authored scene".into(),
            ));
        }
        if scene.camera.center_x != self.end_center_x || scene.camera.center_y != self.end_center_y
        {
            return Err(DomainError::Invalid(
                "CameraReframe terminal center must equal the current authored scene camera".into(),
            ));
        }
        let margin_x = CAMERA_REF_CANVAS_WIDTH * scene.camera.safe_margin.max(0.05);
        let margin_y = CAMERA_REF_CANVAS_HEIGHT * scene.camera.safe_margin.max(0.05);
        let composed =
            self.component_version == CAMERA_REFRAME_VERSION && camera_uses_composition(scene);
        if composed {
            validate_static_native_group_order(scene)?;
        }
        for node in &scene.nodes {
            let poses = if composed {
                if let Some(points) = exact_canvas_pose_waypoints(
                    node,
                    scene,
                    RationalTime::new(30, 1).expect("camera fps"),
                )? {
                    // Reuse the existing exact opacity check, independently
                    // of the multi-pose geometry timestamps.
                    let mut alpha = node.clone();
                    alpha
                        .keyframes
                        .retain(|key| key.property == MotionProperty::Opacity);
                    camera_motion_start(&alpha, scene)?;
                    points
                        .into_iter()
                        .map(|point| point.pose)
                        .collect::<Vec<_>>()
                } else {
                    vec![camera_motion_start(node, scene)?, node.clone()]
                }
            } else {
                vec![node.clone()]
            };
            for (center_x, center_y) in [
                (self.start_center_x, self.start_center_y),
                (self.end_center_x, self.end_center_y),
            ] {
                if composed {
                    let width = poses.iter().map(|pose| pose.width).fold(0.0_f64, f64::max);
                    let height = poses.iter().map(|pose| pose.height).fold(0.0_f64, f64::max);
                    let rotated = poses.iter().any(|pose| pose.rotation_deg != 0.0);
                    let half_width = if rotated {
                        width.hypot(height) / 2.0
                    } else {
                        width / 2.0
                    };
                    let half_height = if rotated { half_width } else { height / 2.0 };
                    // Camera and object may use different durations/curves.
                    // Their Cartesian envelope bounds every frame and Hold cut.
                    for pose in &poses {
                        let x =
                            pose.x + pose.width / 2.0 + CAMERA_REF_CANVAS_WIDTH / 2.0 - center_x;
                        let y =
                            pose.y + pose.height / 2.0 + CAMERA_REF_CANVAS_HEIGHT / 2.0 - center_y;
                        if x - half_width < margin_x
                            || y - half_height < margin_y
                            || x + half_width > CAMERA_REF_CANVAS_WIDTH - margin_x
                            || y + half_height > CAMERA_REF_CANVAS_HEIGHT - margin_y
                        {
                            return Err(DomainError::Invalid(format!(
                                "Camera composition would crop moving Canvas node {}; adjust its complete path",
                                node.id
                            )));
                        }
                    }
                    continue;
                }
                let x = node.x + CAMERA_REF_CANVAS_WIDTH / 2.0 - center_x;
                let y = node.y + CAMERA_REF_CANVAS_HEIGHT / 2.0 - center_y;
                if x < margin_x
                    || y < margin_y
                    || x + node.width > CAMERA_REF_CANVAS_WIDTH - margin_x
                    || y + node.height > CAMERA_REF_CANVAS_HEIGHT - margin_y
                {
                    return Err(DomainError::Invalid(format!(
                        "CameraReframe would crop original Canvas node {}; adjust the camera or source positions",
                        node.id
                    )));
                }
            }
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn upsert_camera_reframe(&mut self, reframe: &CameraReframe) -> Result<()> {
        self.ensure_unlocked(
            &self.resource_key(),
            &[
                LockKind::Content,
                LockKind::Timing,
                LockKind::Position,
                LockKind::Renderer,
            ],
        )?;
        self.ensure_unlocked(
            &format!("scene:{}", reframe.scene_id),
            &[
                LockKind::Content,
                LockKind::Timing,
                LockKind::Position,
                LockKind::Renderer,
            ],
        )?;
        let mut next = self.clone();
        let scene = next
            .scenes
            .iter_mut()
            .find(|scene| scene.id == reframe.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", reframe.scene_id)))?;
        scene.camera.center_x = reframe.end_center_x;
        scene.camera.center_y = reframe.end_center_y;
        scene.status = SceneStatus::Draft;
        if let Some(existing) = next
            .production_design
            .camera_reframes
            .iter_mut()
            .find(|existing| existing.id == reframe.id)
        {
            if existing.scene_id != reframe.scene_id {
                return Err(DomainError::Invalid(
                    "CameraReframe identity cannot migrate to a different scene".into(),
                ));
            }
            *existing = reframe.clone();
        } else {
            if next.production_design.camera_reframes.len() >= CAMERA_REFRAME_MAX
                || next
                    .production_design
                    .camera_reframes
                    .iter()
                    .any(|existing| existing.scene_id == reframe.scene_id)
            {
                return Err(DomainError::Invalid(
                    "Only one bounded camera move is admitted per scene and at most 16 per project"
                        .into(),
                ));
            }
            next.production_design.camera_reframes.push(reframe.clone());
        }
        next.validate()?;
        *self = next;
        Ok(())
    }
    pub(crate) fn detach_camera_reframe(&mut self, id: Uuid) -> Result<()> {
        let reframe = self
            .production_design
            .camera_reframes
            .iter()
            .find(|reframe| reframe.id == id)
            .ok_or_else(|| DomainError::NotFound(format!("CameraReframe:{id}")))?;
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        self.ensure_unlocked(
            &format!("scene:{}", reframe.scene_id),
            &[LockKind::Content, LockKind::Timing],
        )?;
        self.production_design
            .camera_reframes
            .retain(|reframe| reframe.id != id);
        // Preserve the existing camera's last pose, every Canvas subject,
        // and all human-authored project objects when releasing the rig.
        Ok(())
    }
}
impl ProductionDesign {
    pub(crate) fn validate_camera_reframes(
        &self,
        scenes: &[Scene],
        profiles: &[DeliverableProfile],
    ) -> Result<()> {
        if self.camera_reframes.len() > CAMERA_REFRAME_MAX {
            return Err(DomainError::Invalid(
                "CameraReframe project capacity exceeded".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        let mut scene_ids = BTreeSet::new();
        for reframe in &self.camera_reframes {
            if !ids.insert(reframe.id) || !scene_ids.insert(reframe.scene_id) {
                return Err(DomainError::Invalid(
                    "Duplicate CameraReframe identity or scene".into(),
                ));
            }
            let scene = scenes
                .iter()
                .find(|scene| scene.id == reframe.scene_id)
                .ok_or_else(|| {
                    DomainError::Invalid("CameraReframe references a removed scene".into())
                })?;
            reframe.validate(scene)?;
            if reframe.component_version == CAMERA_DETAIL_RETURN_VERSION {
                // Every selected delivery must consume the same complete
                // source camera clock. No implicit crop or rate conversion.
                for profile in profiles.iter().filter(|profile| {
                    profile.included_scene_ids.is_empty()
                        || profile.included_scene_ids.contains(&scene.id)
                }) {
                    if profile.frame_rate != RationalTime::new(30, 1).expect("camera fps")
                        || profile.framing_strategy != FramingStrategy::Replan
                    {
                        return Err(DomainError::Invalid(format!(
                            "Camera detail return requires exact 30fps Replan output: {}",
                            profile.name
                        )));
                    }
                    reframe.validate_detail_return_bounds(
                        scene,
                        f64::from(profile.width),
                        f64::from(profile.height),
                        scene.camera.safe_margin.max(0.05),
                    )?;
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod manim_camera_closed_pair_tests {
    use super::*;

    fn reference_project() -> (Project, Uuid) {
        let mut project = Project::new("Original editable Manim camera").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Bounded planar scene".into(),
                objective: "Preserve two original vector shapes".into(),
                duration_seconds: 3,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        for (index, kind) in ["rectangle", "circle"].iter().enumerate() {
            let circle = *kind == "circle";
            let node = CanvasNode {
                id: Uuid::now_v7(),
                name: format!("Vector object {index}"),
                kind: (*kind).into(),
                parent_id: None,
                x: if circle { 1050.0 } else { 400.0 },
                y: if circle { 430.0 } else { 400.0 },
                width: if circle { 160.0 } else { 220.0 },
                height: if circle { 160.0 } else { 120.0 },
                rotation_deg: 0.0,
                opacity: 1.0,
                text: None,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: index as i32,
                style: NodeStyle {
                    fill: Some("#86C8D1".into()),
                    ..NodeStyle::default()
                },
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            };
            project
                .apply_change(&Change::AddCanvasNode { scene_id, node })
                .unwrap();
        }
        project.scenes[0].renderer = RendererKind::ManimCommunity;
        (project, scene_id)
    }

    fn v2(scene_id: Uuid) -> CameraReframe {
        CameraReframe {
            id: Uuid::now_v7(),
            scene_id,
            component_version: 2,
            start_center_x: 960.0,
            start_center_y: 540.0,
            end_center_x: 1000.0,
            end_center_y: 544.0,
            duration_frames: 24,
            detail_return: None,
        }
    }

    #[test]
    fn camera_composition_preserves_pose_clip_identity_and_rejects_unsafe_paths() {
        for curve in [
            MotionInterpolation::Linear,
            MotionInterpolation::EaseOutCubic,
            MotionInterpolation::Hold,
        ] {
            let (mut project, scene_id) = reference_project();
            project.scenes[0].renderer = RendererKind::MotionCanvas;
            let mut clip = project.scenes[0].nodes[0].clone();
            clip.id = Uuid::now_v7();
            clip.kind = "clip_group".into();
            clip.name = "Camera source Clip".into();
            clip.x = 300.0;
            clip.y = 300.0;
            clip.width = 520.0;
            clip.height = 400.0;
            clip.style = NodeStyle::default();
            clip.z_index = 2;
            let clip_id = clip.id;
            project.scenes[0].nodes.push(clip);
            let child = &mut project.scenes[0].nodes[0];
            child.parent_id = Some(clip_id);
            child.z_index = 2;
            for (property, from, to) in [
                (MotionProperty::X, 360.0, child.x),
                (MotionProperty::Y, 380.0, child.y),
                (MotionProperty::Width, 180.0, child.width),
                (MotionProperty::Height, 90.0, child.height),
                (MotionProperty::RotationDeg, -12.0, 0.0),
                (MotionProperty::Opacity, 1.0, 0.0),
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
                        MotionInterpolation::Linear
                    } else {
                        curve
                    },
                });
            }
            let source = project.clone();
            let mut camera = v2(scene_id);
            camera.component_version = CAMERA_REFRAME_VERSION;
            project
                .apply_change(&Change::UpsertCameraReframe {
                    reframe: camera.clone(),
                })
                .unwrap();
            assert_eq!(project.scenes[0].nodes, source.scenes[0].nodes);
            let reopened: Project =
                serde_json::from_slice(&serde_json::to_vec(&project).unwrap()).unwrap();
            assert_eq!(reopened, project);
            reopened.validate().unwrap();
            for negative in 0..5 {
                let mut rejected = source.clone();
                match negative {
                    0 => rejected.scenes[0].nodes[0].keyframes[0].value = 0.0,
                    1 => rejected.scenes[0].nodes[0].keyframes[11].value = 0.25,
                    2 => {
                        rejected.scenes[0]
                            .nodes
                            .iter_mut()
                            .find(|node| node.id == clip_id)
                            .unwrap()
                            .rotation_deg = 5.0
                    }
                    3 => {
                        rejected.scenes[0].nodes[0].keyframes[1].interpolation =
                            MotionInterpolation::EaseInOut
                    }
                    4 => {
                        // The root lies below an unrelated drawable but its
                        // painted child lies above it. Native cannot interleave.
                        rejected.scenes[0]
                            .nodes
                            .iter_mut()
                            .find(|node| node.id == clip_id)
                            .unwrap()
                            .z_index = 0;
                    }
                    _ => unreachable!(),
                }
                let before = rejected.clone();
                assert!(
                    rejected
                        .apply_change(&Change::UpsertCameraReframe {
                            reframe: camera.clone()
                        })
                        .is_err()
                );
                assert_eq!(rejected, before);
            }
        }
    }

    #[test]
    fn manim_v2_closed_pair_preserves_original_shapes_and_json_after_edit() {
        let (mut project, scene_id) = reference_project();
        let original_objects = project.scenes[0].nodes.clone();
        let original_branch = project.active_branch;
        let original_id = project.id;
        let reframe = v2(scene_id);
        project
            .apply_change(&Change::UpsertCameraReframe {
                reframe: reframe.clone(),
            })
            .unwrap();
        assert_eq!(project.scenes[0].nodes, original_objects);
        assert_eq!(project.id, original_id);
        assert_eq!(project.active_branch, original_branch);
        assert_eq!(project.scenes[0].camera.center_x, 1000.0);
        assert_eq!(project.scenes[0].camera.center_y, 544.0);
        assert_eq!(
            project.production_design.camera_reframes,
            vec![reframe.clone()]
        );
        let saved = serde_json::to_vec(&project).unwrap();
        let mut reopened: Project = serde_json::from_slice(&saved).unwrap();
        assert_eq!(reopened, project);
        let new_end = CameraReframe {
            end_center_x: 1012.0,
            ..reframe
        };

        reopened
            .apply_change(&Change::UpsertCameraReframe { reframe: new_end })
            .unwrap();
        assert_eq!(reopened.scenes[0].nodes, original_objects);
        assert_eq!(reopened.production_design.camera_reframes.len(), 1);
    }

    #[test]
    fn version_renderer_cross_pairs_are_not_migrated_or_inferred() {
        let (mut manim, scene_id) = reference_project();
        let original = manim.clone();
        let mut legacy = v2(scene_id);
        legacy.component_version = CAMERA_REFRAME_VERSION;
        assert!(
            manim
                .apply_change(&Change::UpsertCameraReframe { reframe: legacy })
                .is_err()
        );
        assert_eq!(manim, original);

        manim.scenes[0].renderer = RendererKind::MotionCanvas;
        let motion_original = manim.clone();
        let new_manim = v2(scene_id);
        assert!(
            manim
                .apply_change(&Change::UpsertCameraReframe { reframe: new_manim })
                .is_err()
        );
        assert_eq!(manim, motion_original);

        let mut original_motion = v2(scene_id);
        original_motion.component_version = CAMERA_REFRAME_VERSION;
        manim
            .apply_change(&Change::UpsertCameraReframe {
                reframe: original_motion,
            })
            .unwrap();
    }

    #[test]
    fn manim_v2_rejects_unrepresented_visual_state_without_mutation() {
        let (project, scene_id) = reference_project();
        type ProjectMutation = Box<dyn Fn(&mut Project)>;
        let cases: Vec<ProjectMutation> = vec![
            Box::new(|p| p.scenes[0].nodes[0].kind = "text".into()),
            Box::new(|p| p.scenes[0].nodes[1].height = 159.0),
            Box::new(|p| p.scenes[0].nodes[0].opacity = 0.7),
            Box::new(|p| p.scenes[0].nodes[0].rotation_deg = 5.0),
            Box::new(|p| p.scenes[0].nodes[0].style.stroke = Some("#101010".into())),
            Box::new(|p| p.scenes[0].nodes[0].style.fill = None),
            Box::new(|p| p.scenes[0].nodes[0].style.fill = Some("red".into())),
            Box::new(|p| p.scenes[0].nodes[0].style.font_family = Some("system".into())),
            Box::new(|p| {
                let target_id = p.scenes[0].nodes[1].id;
                p.scenes[0].nodes[0].relations.push(NodeRelation {
                    id: Uuid::now_v7(),
                    target_id,
                    kind: RelationKind::AlignCenterX,
                });
            }),
        ];
        for mutate in cases {
            let mut changed = project.clone();
            mutate(&mut changed);
            let original = changed.clone();
            assert!(
                changed
                    .apply_change(&Change::UpsertCameraReframe {
                        reframe: v2(scene_id),
                    })
                    .is_err()
            );
            assert_eq!(
                changed, original,
                "rejected camera must not alter human content"
            );
        }
    }
}
