use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

// Camera source is admitted only by its typed closed profile and the original Host.
pub mod editable_scene;
pub mod planar_camera;
pub mod semantic_graph;
pub mod semantic_graph_profile_probe;
pub mod semantic_graph_readback;
pub mod semantic_graph_source;
pub mod semantic_graph_text;
pub mod semantic_graph_text_probe;

pub const SCENE_CLASS: &str = "MotionwrightScene";
pub const MAX_PRIMITIVES: usize = 512;
const MAX_TEXT_BYTES: usize = 16 * 1024;
const MAX_ABS_COORD: f64 = 128.0;
const MAX_DIMENSION: f64 = 128.0;

#[derive(Debug, Error, Clone, PartialEq)]
pub enum ManimProfileError {
    #[error("{0}")]
    Invalid(String),
}

pub type Result<T> = std::result::Result<T, ManimProfileError>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ManimPrimitive {
    Text {
        node_id: Uuid,
        text: String,
        x: f64,
        y: f64,
        font_size: f64,
        color: String,
    },
    Rectangle {
        node_id: Uuid,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        color: String,
    },
    Circle {
        node_id: Uuid,
        x: f64,
        y: f64,
        radius: f64,
        color: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimScenePlan {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub primitives: Vec<ManimPrimitive>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimRenderProfile {
    pub width: u32,
    pub height: u32,
    pub frame_rate: u32,
}

impl Default for ManimRenderProfile {
    fn default() -> Self {
        Self {
            width: 1920,
            height: 1080,
            frame_rate: 30,
        }
    }
}

impl ManimRenderProfile {
    pub fn validate(self) -> Result<()> {
        if !(320..=7680).contains(&self.width)
            || !(240..=4320).contains(&self.height)
            || !(1..=120).contains(&self.frame_rate)
        {
            return Err(ManimProfileError::Invalid(
                "Manim render profile is outside certified bounds".into(),
            ));
        }
        let pixels = u64::from(self.width) * u64::from(self.height);
        if pixels > 33_177_600 {
            return Err(ManimProfileError::Invalid(
                "Manim render profile exceeds the bounded pixel budget".into(),
            ));
        }
        Ok(())
    }

    pub fn quality_directory(self) -> String {
        format!("{}p{}", self.height, self.frame_rate)
    }
}

pub fn validate_plan(plan: &ManimScenePlan) -> Result<()> {
    if plan.primitives.is_empty() || plan.primitives.len() > MAX_PRIMITIVES {
        return Err(ManimProfileError::Invalid(
            "Manim plan has an invalid primitive count".into(),
        ));
    }
    for primitive in &plan.primitives {
        match primitive {
            ManimPrimitive::Text {
                text,
                x,
                y,
                font_size,
                color,
                ..
            } => {
                finite_coord(*x)?;
                finite_coord(*y)?;
                if text.is_empty()
                    || text.len() > MAX_TEXT_BYTES
                    || text.contains('\0')
                    || !font_size.is_finite()
                    || !(4.0..=512.0).contains(font_size)
                {
                    return Err(ManimProfileError::Invalid(
                        "Manim text primitive is outside certified bounds".into(),
                    ));
                }
                color_hex(color)?;
            }
            ManimPrimitive::Rectangle {
                x,
                y,
                width,
                height,
                color,
                ..
            } => {
                finite_coord(*x)?;
                finite_coord(*y)?;
                positive_dimension(*width)?;
                positive_dimension(*height)?;
                color_hex(color)?;
            }
            ManimPrimitive::Circle {
                x,
                y,
                radius,
                color,
                ..
            } => {
                finite_coord(*x)?;
                finite_coord(*y)?;
                positive_dimension(*radius)?;
                color_hex(color)?;
            }
        }
    }
    Ok(())
}

fn finite_coord(value: f64) -> Result<()> {
    if !value.is_finite() || value.abs() > MAX_ABS_COORD {
        return Err(ManimProfileError::Invalid(
            "Manim coordinate is outside certified bounds".into(),
        ));
    }
    Ok(())
}

fn positive_dimension(value: f64) -> Result<()> {
    if !value.is_finite() || value <= 0.0 || value > MAX_DIMENSION {
        return Err(ManimProfileError::Invalid(
            "Manim geometry dimension is outside certified bounds".into(),
        ));
    }
    Ok(())
}

fn color_hex(value: &str) -> Result<()> {
    let bytes = value.as_bytes();
    if bytes.len() != 7 || bytes[0] != b'#' || !bytes[1..].iter().all(u8::is_ascii_hexdigit) {
        return Err(ManimProfileError::Invalid(
            "Manim color must be explicit #RRGGBB".into(),
        ));
    }
    Ok(())
}

fn py_string(value: &str) -> Result<String> {
    serde_json::to_string(value)
        .map_err(|_| ManimProfileError::Invalid("Manim string could not be encoded".into()))
}

pub fn compile_manim_python(plan: &ManimScenePlan) -> Result<String> {
    validate_plan(plan)?;
    let mut body = String::from(
        "from manim import Circle, Create, Rectangle, Scene, Text\n\n\nclass MotionwrightScene(Scene):\n    def construct(self):\n",
    );
    for (index, primitive) in plan.primitives.iter().enumerate() {
        match primitive {
            ManimPrimitive::Text {
                text,
                x,
                y,
                font_size,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Text({}, font_size={font_size:.6}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    py_string(text)?,
                    py_string(color)?
                ));
            }
            ManimPrimitive::Rectangle {
                x,
                y,
                width,
                height,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Rectangle(width={width:.9}, height={height:.9}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    py_string(color)?
                ));
            }
            ManimPrimitive::Circle {
                x,
                y,
                radius,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Circle(radius={radius:.9}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    py_string(color)?
                ));
            }
        }
        body.push_str(&format!("        self.play(Create(item_{index}))\n"));
    }
    Ok(body)
}

/// A bounded, editable source contribution. Audio and captions belong to the
/// canonical audiovisual master; this profile never claims to supply them.
pub const STATIC_SHAPES_PROFILE_V2: &str = "mw-manim-static-shapes-v2";
pub const SOURCE_COMPILER_V2: &str = "motionwright.manim.rust.static_shapes.v2.1";
pub const SOURCE_BUNDLE_MIME_V2: &str = "application/vnd.motionwright.manim-source+zip";
pub const MAX_STATIC_FRAMES_V2: u32 = 3_600;
pub const MAX_SOURCE_ENTRY_BYTES_V2: usize = 256 * 1024;
pub const MAX_SOURCE_BUNDLE_BYTES_V2: usize = 2 * 1024 * 1024;

use semwright_media_time::Rational;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Cursor, Read, Write},
};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum ManimProfileIdV2 {
    #[serde(rename = "mw-manim-static-shapes-v2")]
    StaticShapes,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ManimOutputRoleV2 {
    SceneVideoContributionWithoutAudioOrCaptions,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ManimSourceShapeKindV2 {
    Shape,
    Rectangle,
    Circle,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimSceneBindingV2 {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub deliverable_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimFrameClockV2 {
    pub timeline_start: Rational,
    pub duration: Rational,
    pub frame_rate: Rational,
    pub project_start_frame: u64,
    pub project_end_frame_exclusive: u64,
    pub local_start_frame: u64,
    pub frame_count: u32,
}

fn profile_invalid(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

impl ManimFrameClockV2 {
    pub fn from_canonical(start: Rational, duration: Rational, rate: Rational) -> Result<Self> {
        for time in [start, duration, rate] {
            time.validate()
                .map_err(|_| profile_invalid("invalid_canonical_clock"))?;
        }
        if start.num < 0 || duration.num <= 0 || rate.den != 1 || !(1..=120).contains(&rate.num) {
            return Err(profile_invalid("unsupported_canonical_clock"));
        }
        start
            .checked_add(duration)
            .map_err(|_| profile_invalid("clock_overflow"))?;
        let first = start
            .checked_mul(rate)
            .map_err(|_| profile_invalid("clock_overflow"))?;
        let count = duration
            .checked_mul(rate)
            .map_err(|_| profile_invalid("clock_overflow"))?;
        if first.den != 1 || count.den != 1 || count.num <= 0 {
            return Err(profile_invalid("non_integral_frame_boundary"));
        }
        let project_start_frame =
            u64::try_from(first.num).map_err(|_| profile_invalid("clock_overflow"))?;
        let frame_count = u32::try_from(count.num).map_err(|_| profile_invalid("frame_budget"))?;
        if !(1..=MAX_STATIC_FRAMES_V2).contains(&frame_count) {
            return Err(profile_invalid("frame_budget"));
        }
        let project_end_frame_exclusive = project_start_frame
            .checked_add(u64::from(frame_count))
            .ok_or_else(|| profile_invalid("clock_overflow"))?;
        Ok(Self {
            timeline_start: start,
            duration,
            frame_rate: rate,
            project_start_frame,
            project_end_frame_exclusive,
            local_start_frame: 0,
            frame_count,
        })
    }

    pub fn validate(&self) -> Result<()> {
        if *self != Self::from_canonical(self.timeline_start, self.duration, self.frame_rate)? {
            return Err(profile_invalid("clock_binding_mismatch"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ManimCoordinateSpaceV2 {
    ProjectPixels,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimCanvasV2 {
    pub width: u32,
    pub height: u32,
    pub coordinate_space: ManimCoordinateSpaceV2,
}

impl Default for ManimCanvasV2 {
    fn default() -> Self {
        Self {
            width: 1920,
            height: 1080,
            coordinate_space: ManimCoordinateSpaceV2::ProjectPixels,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ManimMatteKindV2 {
    Solid,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimMatteV2 {
    pub kind: ManimMatteKindV2,
    pub color: String,
    pub opacity: u8,
}

impl Default for ManimMatteV2 {
    fn default() -> Self {
        Self {
            kind: ManimMatteKindV2::Solid,
            color: "#000000".into(),
            opacity: 1,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimStaticShapeV2 {
    pub node_id: Uuid,
    pub display_name: String,
    pub source_kind: ManimSourceShapeKindV2,
    pub canonical_ordinal: u32,
    pub z_index: i32,
    pub x_px: f64,
    pub y_px: f64,
    pub width_px: f64,
    pub height_px: f64,
    pub fill_hex: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimScenePlanV2 {
    pub schema_version: u16,
    pub profile_id: ManimProfileIdV2,
    pub output_role: ManimOutputRoleV2,
    pub binding: ManimSceneBindingV2,
    pub clock: ManimFrameClockV2,
    pub render_profile: ManimRenderProfile,
    pub canvas: ManimCanvasV2,
    pub background: ManimMatteV2,
    pub nodes: Vec<ManimStaticShapeV2>,
}

/// Version discrimination is structural and strict. A legacy plan never receives
/// invented clock fields; every v2 plan must carry its complete source contract.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum ManimStartPlan {
    V1(ManimScenePlan),
    V2(Box<ManimScenePlanV2>),
}

impl ManimStartPlan {
    pub fn validate_for_profile(&self, profile: ManimRenderProfile) -> Result<()> {
        profile.validate()?;
        match self {
            Self::V1(plan) => validate_plan(plan),
            Self::V2(plan) => {
                validate_plan_v2(plan)?;
                if profile != plan.render_profile {
                    return Err(profile_invalid("source_render_profile_mismatch"));
                }
                Ok(())
            }
        }
    }

    /// V1 keeps its original compact digest. V2 names the exact pretty JSON+LF
    /// source document whose encoding is recorded in the source manifest.
    pub fn plan_document(&self) -> Result<Vec<u8>> {
        match self {
            Self::V1(plan) => {
                validate_plan(plan)?;
                serde_json::to_vec(plan).map_err(|_| profile_invalid("source_plan_invalid"))
            }
            Self::V2(plan) => {
                validate_plan_v2(plan)?;
                source_json(plan)
            }
        }
    }

    pub fn compile(&self) -> Result<String> {
        match self {
            Self::V1(plan) => compile_manim_python(plan),
            Self::V2(plan) => compile_manim_python_v2(plan),
        }
    }

    pub fn plan_sha256(&self) -> Result<String> {
        Ok(source_digest(&self.plan_document()?))
    }
}

pub fn validate_plan_v2(plan: &ManimScenePlanV2) -> Result<()> {
    plan.clock.validate()?;
    plan.render_profile.validate()?;
    let profile = plan.render_profile;
    if plan.schema_version != 2
        || plan.canvas != ManimCanvasV2::default()
        || plan.background != ManimMatteV2::default()
        || u64::from(profile.width) * 9 != u64::from(profile.height) * 16
        || !profile.width.is_multiple_of(2)
        || !profile.height.is_multiple_of(2)
        || i64::from(profile.frame_rate) != plan.clock.frame_rate.num
    {
        return Err(profile_invalid("unsupported_static_render_profile"));
    }
    let binding = &plan.binding;
    if [
        binding.project_id,
        binding.generation,
        binding.scene_id,
        binding.deliverable_id,
    ]
    .iter()
    .any(Uuid::is_nil)
    {
        return Err(profile_invalid("invalid_source_identity"));
    }
    if plan.nodes.is_empty() || plan.nodes.len() > MAX_PRIMITIVES {
        return Err(profile_invalid("node_budget"));
    }
    let mut ids = BTreeSet::new();
    for (ordinal, node) in plan.nodes.iter().enumerate() {
        if node.node_id.is_nil()
            || !ids.insert(node.node_id)
            || node.canonical_ordinal as usize != ordinal
            || node.display_name.is_empty()
            || node.display_name.len() > 160
            || node.display_name.contains('\0')
        {
            return Err(profile_invalid("invalid_source_node_identity"));
        }
        color_hex(&node.fill_hex)?;
        let width = node.width_px / 135.0;
        let height = node.height_px / 135.0;
        positive_dimension(width)?;
        positive_dimension(height)?;
        finite_coord((node.x_px + node.width_px / 2.0 - 960.0) / 135.0)?;
        finite_coord((540.0 - node.y_px - node.height_px / 2.0) / 135.0)?;
        if node.source_kind == ManimSourceShapeKindV2::Circle && node.width_px != node.height_px {
            return Err(profile_invalid("unsupported_ellipse"));
        }
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimFrozenWaitV1 {
    pub seconds: f64,
    pub seconds_hex: String,
    pub next_up_steps: u8,
    pub predicted_frame_count: u32,
}

fn positive_binary_fraction(value: f64) -> Option<(u128, u128)> {
    if !value.is_finite() || value <= 0.0 {
        return None;
    }
    let bits = value.to_bits();
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    if exponent == 0 {
        return None;
    }
    let mut numerator = u128::from((bits & ((1_u64 << 52) - 1)) | (1_u64 << 52));
    let shift = exponent - 1023 - 52;
    let mut denominator = 1_u128;
    if shift >= 0 {
        numerator = numerator.checked_shl(shift as u32)?;
    } else {
        denominator = denominator.checked_shl((-shift) as u32)?;
    }
    while denominator > 1 && numerator.is_multiple_of(2) {
        numerator /= 2;
        denominator /= 2;
    }
    Some((numerator, denominator))
}

fn within_two_ulps(value: f64, initial: f64, frames: u32, fps: u32) -> Option<bool> {
    let (n, d) = positive_binary_fraction(value)?;
    let (un, ud) = positive_binary_fraction(initial.next_up() - initial)?;
    let difference = n
        .checked_mul(u128::from(fps))?
        .abs_diff(u128::from(frames).checked_mul(d)?);
    let actual = difference.checked_mul(ud)?;
    let bound = 2_u128
        .checked_mul(un)?
        .checked_mul(d)?
        .checked_mul(u128::from(fps))?;
    Some(actual <= bound)
}

/// Realization of Manim 0.21.0's public frozen-frame wait. The exact canonical
/// rational stays unchanged. A media decoder still must observe every frame.
pub fn frozen_wait_v2(frames: u32, fps: u32) -> Result<ManimFrozenWaitV1> {
    if !(1..=MAX_STATIC_FRAMES_V2).contains(&frames) || !(1..=120).contains(&fps) {
        return Err(profile_invalid("frame_budget"));
    }
    let initial = f64::from(frames) / f64::from(fps);
    let interval = 1.0 / f64::from(fps);
    let mut seconds = initial;
    let mut steps = 0;
    while (seconds / interval).trunc() < f64::from(frames) && steps < 2 {
        seconds = seconds.next_up();
        steps += 1;
    }
    if seconds < interval
        || (seconds / interval).trunc() != f64::from(frames)
        || within_two_ulps(seconds, initial, frames, fps) != Some(true)
    {
        return Err(profile_invalid("frame_realization_unrepresentable"));
    }
    let bits = seconds.to_bits();
    let exponent = ((bits >> 52) & 0x7ff) as i32 - 1023;
    let seconds_hex = format!("0x1.{:013x}p{exponent:+}", bits & ((1_u64 << 52) - 1));
    Ok(ManimFrozenWaitV1 {
        seconds,
        seconds_hex,
        next_up_steps: steps,
        predicted_frame_count: frames,
    })
}

pub fn compile_manim_python_v2(plan: &ManimScenePlanV2) -> Result<String> {
    validate_plan_v2(plan)?;
    let profile = plan.render_profile;
    let wait = frozen_wait_v2(plan.clock.frame_count, profile.frame_rate)?;
    let mut source = format!(
        "# Generated by {SOURCE_COMPILER_V2}\n# Edit plan.json; imported Python is inspected as data and never executed.\nfrom manim import Circle, Rectangle, Scene, config\nimport json\nfrom pathlib import Path\n\nif (config.pixel_width, config.pixel_height, config.frame_rate) != ({}, {}, {}):\n    raise ValueError(\"render_profile_mismatch\")\nconfig.frame_width = 128.0 / 9.0\nconfig.frame_height = 8.0\nconfig.background_color = \"#000000\"\nconfig.background_opacity = 1.0\n\nclass MotionwrightScene(Scene):\n    def construct(self):\n        if self.camera.frame_rate != {}:\n            raise ValueError(\"camera_frame_rate_mismatch\")\n",
        profile.width, profile.height, profile.frame_rate, profile.frame_rate
    );
    let mut nodes = plan.nodes.iter().collect::<Vec<_>>();
    nodes.sort_by_key(|node| (node.z_index, node.canonical_ordinal));
    for node in nodes {
        let name = format!("mw_{}", node.node_id.simple());
        let width = node.width_px / 135.0;
        let height = node.height_px / 135.0;
        let x = (node.x_px + node.width_px / 2.0 - 960.0) / 135.0;
        let y = (540.0 - node.y_px - node.height_px / 2.0) / 135.0;
        let constructor = match node.source_kind {
            ManimSourceShapeKindV2::Shape | ManimSourceShapeKindV2::Rectangle => format!(
                "Rectangle(width={width:?}, height={height:?}, name={})",
                py_string(&name)?
            ),
            ManimSourceShapeKindV2::Circle => format!(
                "Circle(radius={:?}, name={})",
                width / 2.0,
                py_string(&name)?
            ),
        };
        source.push_str(&format!(
            "        {name} = {constructor}\n        {name}.move_to([{x:?}, {y:?}, 0.0])\n        {name}.set_fill({}, opacity=1.0)\n        {name}.set_stroke(width=0.0, opacity=0.0)\n        {name}.set_z_index({})\n        self.add({name})\n",
            py_string(&node.fill_hex)?, node.z_index
        ));
    }
    // This record is made from real native getters, not copied plan properties.
    // The separate media reader still has to check decoded pixels and timing.
    source.push_str(r#"        native_objects = []
        for item in self.mobjects:
            native_objects.append({
                "name": item.name,
                "primitive": item.__class__.__name__,
                "center": [float(value) for value in item.get_center()],
                "width": float(item.width),
                "height": float(item.height),
                "z_index": float(item.z_index),
                "fill_hex": item.get_fill_color().to_hex(),
                "fill_opacity": float(item.get_fill_opacity()),
                "stroke_width": float(item.get_stroke_width()),
                "stroke_opacity": float(item.get_stroke_opacity()),
            })
        native_readback = {
            "schema_version": 1,
            "profile_id": "mw-manim-static-shapes-v2",
            "scene_class": self.__class__.__name__,
            "camera": {
                "pixel_width": self.camera.pixel_width,
                "pixel_height": self.camera.pixel_height,
                "frame_rate": float(self.camera.frame_rate),
                "frame_width": float(self.camera.frame_width),
                "frame_height": float(self.camera.frame_height),
                "frame_center": [float(value) for value in self.camera.frame_center],
                "background_hex": self.camera.background_color.to_hex(),
                "background_opacity": float(self.camera.background_opacity),
            },
            "objects": native_objects,
        }
        with Path(__file__).with_suffix(".objects.json").open("x", encoding="utf-8") as record:
            record.write(json.dumps(native_readback, sort_keys=True, indent=2, allow_nan=False) + "\n")
"#);
    source.push_str(&format!(
        "        realized_wait = float.fromhex({})\n        if int(realized_wait / (1.0 / self.camera.frame_rate)) != {}:\n            raise ValueError(\"frame_count_realization_mismatch\")\n        self.wait(realized_wait, frozen_frame=True)\n",
        py_string(&wait.seconds_hex)?, plan.clock.frame_count
    ));
    if source.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(profile_invalid("source_budget"));
    }
    Ok(source)
}

fn source_digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn source_json<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let mut bytes =
        serde_json::to_vec_pretty(value).map_err(|_| profile_invalid("source_json_invalid"))?;
    bytes.push(b'\n');
    if bytes.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(profile_invalid("source_budget"));
    }
    Ok(bytes)
}

pub fn source_render_config_v2(plan: &ManimScenePlanV2) -> Result<Vec<u8>> {
    validate_plan_v2(plan)?;
    source_json(&serde_json::json!({
        "schema_version": 1, "compiler_id": SOURCE_COMPILER_V2, "profile_id": STATIC_SHAPES_PROFILE_V2,
        "manim_version_requirement": "0.21.0", "renderer": "cairo", "scene_class": SCENE_CLASS,
        "render_profile": plan.render_profile, "format": "mp4",
        "frame_width": Rational::new(128, 9).map_err(|_| profile_invalid("source_json_invalid"))?,
        "frame_height": Rational::new(8, 1).map_err(|_| profile_invalid("source_json_invalid"))?,
        "background": plan.background, "seed": 0, "disable_caching": true,
        "frozen_wait": frozen_wait_v2(plan.clock.frame_count, plan.render_profile.frame_rate)?,
        "runtime_binary_attested": false, "codec_observation_required": true
    }))
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimSourceEntryV1 {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

/// These hashes describe source bytes, not consent or evidence of execution.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimSourceClosureV1 {
    pub plan_schema_version: u16,
    pub compiler_contract_id: String,
    pub plan_sha256: String,
    pub source_sha256: String,
    pub config_sha256: String,
    pub assets_lock_sha256: String,
    pub manifest_sha256: String,
    pub entries: Vec<ManimSourceEntryV1>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ManimSourcePackageV2 {
    pub plan: ManimScenePlanV2,
    pub entries: BTreeMap<String, Vec<u8>>,
    pub closure: ManimSourceClosureV1,
}

pub fn source_package_v2(plan: &ManimScenePlanV2) -> Result<ManimSourcePackageV2> {
    validate_plan_v2(plan)?;
    let mut entries = BTreeMap::from([
        ("plan.json".to_owned(), source_json(plan)?),
        (
            "scene.py".to_owned(),
            compile_manim_python_v2(plan)?.into_bytes(),
        ),
        (
            "render-config.json".to_owned(),
            source_render_config_v2(plan)?,
        ),
        (
            "assets.lock.json".to_owned(),
            source_json(&serde_json::json!({
                "schema_version": 1, "profile_id": STATIC_SHAPES_PROFILE_V2, "assets": [],
                "external_asset_usage": "none", "external_rights_certified": false
            }))?,
        ),
    ]);
    let rows = entries
        .iter()
        .map(|(path, bytes)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: bytes.len() as u64,
            sha256: source_digest(bytes),
        })
        .collect::<Vec<_>>();
    let plan_sha256 = source_digest(&entries["plan.json"]);
    let source_sha256 = source_digest(&entries["scene.py"]);
    let config_sha256 = source_digest(&entries["render-config.json"]);
    let assets_lock_sha256 = source_digest(&entries["assets.lock.json"]);
    let manifest = source_json(&serde_json::json!({
        "schema_version": 1, "bundle_kind": "motionwright.manim.editable_source",
        "compiler_id": SOURCE_COMPILER_V2, "encoding_id": "serde-json-pretty-rational-strings-zip9-stored-v1",
        "plan_sha256": plan_sha256, "source_sha256": source_sha256, "config_sha256": config_sha256,
        "entries": rows,
        "losses": ["generated_python_edits_outside_plan_not_importable", "binary64_geometry_projection",
            "opaque_black_matte_no_alpha", "static_shapes_only", "no_audio_or_captions"],
        "native_render_verified": false, "native_reopen_verified": false, "canonical_import_applied": false
    }))?;
    let manifest_sha256 = source_digest(&manifest);
    entries.insert("manifest.json".into(), manifest);
    let all_rows = entries
        .iter()
        .map(|(path, bytes)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: bytes.len() as u64,
            sha256: source_digest(bytes),
        })
        .collect();
    let closure = ManimSourceClosureV1 {
        plan_schema_version: 2,
        compiler_contract_id: SOURCE_COMPILER_V2.into(),
        plan_sha256,
        source_sha256,
        config_sha256,
        assets_lock_sha256,
        manifest_sha256,
        entries: all_rows,
    };
    Ok(ManimSourcePackageV2 {
        plan: plan.clone(),
        entries,
        closure,
    })
}

fn encode_source_entries(entries: &BTreeMap<String, Vec<u8>>) -> Result<Vec<u8>> {
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Stored)
        .last_modified_time(zip::DateTime::default())
        .unix_permissions(0o600);
    for (path, bytes) in entries {
        zip.start_file(path, options)
            .map_err(|_| profile_invalid("source_archive_invalid"))?;
        zip.write_all(bytes)
            .map_err(|_| profile_invalid("source_archive_invalid"))?;
    }
    let bytes = zip
        .finish()
        .map_err(|_| profile_invalid("source_archive_invalid"))?
        .into_inner();
    if bytes.len() > MAX_SOURCE_BUNDLE_BYTES_V2 {
        return Err(profile_invalid("source_archive_budget"));
    }
    Ok(bytes)
}

pub fn source_bundle_v2(plan: &ManimScenePlanV2) -> Result<Vec<u8>> {
    encode_source_entries(&source_package_v2(plan)?.entries)
}

/// Memory-only import of a closed source format. No extraction, Python import,
/// process, filesystem grant, current Project, or native receipt is constructed.
pub fn inspect_source_bundle_v2(bytes: &[u8]) -> Result<ManimSourcePackageV2> {
    const NAMES: [&str; 5] = [
        "assets.lock.json",
        "manifest.json",
        "plan.json",
        "render-config.json",
        "scene.py",
    ];
    if bytes.is_empty() || bytes.len() > MAX_SOURCE_BUNDLE_BYTES_V2 {
        return Err(profile_invalid("source_archive_budget"));
    }
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| profile_invalid("source_archive_invalid"))?;
    if zip.len() != NAMES.len() || !zip.comment().is_empty() {
        return Err(profile_invalid("source_archive_entry_set"));
    }
    let mut entries = BTreeMap::new();
    let mut total = 0_usize;
    for index in 0..NAMES.len() {
        let mut file = zip
            .by_index(index)
            .map_err(|_| profile_invalid("source_archive_invalid"))?;
        let name = file
            .name()
            .map_err(|_| profile_invalid("source_archive_name_invalid"))?
            .into_owned();
        if !NAMES.contains(&name.as_str())
            || entries.contains_key(&name)
            || !file.is_file()
            || file.is_symlink()
            || file.encrypted()
            || file.compression() != zip::CompressionMethod::Stored
            || !file.comment().is_empty()
            || file.size() == 0
            || file.size() > MAX_SOURCE_ENTRY_BYTES_V2 as u64
        {
            return Err(profile_invalid("source_archive_entry_invalid"));
        }
        let expected = file.size() as usize;
        total = total
            .checked_add(expected)
            .ok_or_else(|| profile_invalid("source_archive_budget"))?;
        if total > MAX_SOURCE_BUNDLE_BYTES_V2 {
            return Err(profile_invalid("source_archive_budget"));
        }
        let mut entry = Vec::with_capacity(expected);
        file.by_ref()
            .take(MAX_SOURCE_ENTRY_BYTES_V2 as u64 + 1)
            .read_to_end(&mut entry)
            .map_err(|_| profile_invalid("source_archive_checksum"))?;
        if entry.len() != expected {
            return Err(profile_invalid("source_archive_size"));
        }
        entries.insert(name, entry);
    }
    let plan: ManimScenePlanV2 = serde_json::from_slice(&entries["plan.json"])
        .map_err(|_| profile_invalid("source_plan_invalid"))?;
    let expected = source_package_v2(&plan)?;
    if entries["scene.py"] != expected.entries["scene.py"] {
        return Err(profile_invalid("source_projection_mismatch"));
    }
    if entries != expected.entries {
        return Err(profile_invalid("source_manifest_mismatch"));
    }
    if encode_source_entries(&entries)? != bytes {
        return Err(profile_invalid("source_archive_not_canonical"));
    }
    Ok(expected)
}

/// Rebinding a portable source may change only invocation context. The caller
/// must derive `current` from the current saved Project and recheck its stamp.
pub fn rebind_source_v2(
    origin: &ManimScenePlanV2,
    current: &ManimScenePlanV2,
) -> Result<ManimSourcePackageV2> {
    validate_plan_v2(origin)?;
    validate_plan_v2(current)?;
    let mut rebound = origin.clone();
    rebound.binding.generation = current.binding.generation;
    rebound.binding.revision = current.binding.revision;
    if rebound != *current {
        return Err(profile_invalid("source_semantic_rebind_mismatch"));
    }
    source_package_v2(current)
}

/// Data-only observation from native Mobject/Camera getters. This record neither
/// grants execution nor proves that any video frame contains those properties.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimNativeCameraReadbackV1 {
    pub pixel_width: u32,
    pub pixel_height: u32,
    pub frame_rate: f64,
    pub frame_width: f64,
    pub frame_height: f64,
    pub frame_center: [f64; 3],
    pub background_hex: String,
    pub background_opacity: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimNativeObjectReadbackV1 {
    pub name: String,
    pub primitive: String,
    pub center: [f64; 3],
    pub width: f64,
    pub height: f64,
    pub z_index: f64,
    pub fill_hex: String,
    pub fill_opacity: f64,
    pub stroke_width: f64,
    pub stroke_opacity: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimNativeSourceReadbackV1 {
    pub schema_version: u16,
    pub profile_id: ManimProfileIdV2,
    pub scene_class: String,
    pub camera: ManimNativeCameraReadbackV1,
    pub objects: Vec<ManimNativeObjectReadbackV1>,
}

pub fn inspect_native_source_readback_v2(
    plan: &ManimScenePlanV2,
    bytes: &[u8],
) -> Result<ManimNativeSourceReadbackV1> {
    validate_plan_v2(plan)?;
    if bytes.is_empty() || bytes.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(profile_invalid("native_readback_budget"));
    }
    let observed: ManimNativeSourceReadbackV1 =
        serde_json::from_slice(bytes).map_err(|_| profile_invalid("native_readback_invalid"))?;
    let near =
        |actual: f64, expected: f64| actual.is_finite() && (actual - expected).abs() <= 1.0e-9;
    let camera = &observed.camera;
    if observed.schema_version != 1
        || observed.scene_class != SCENE_CLASS
        || observed.profile_id != plan.profile_id
        || camera.pixel_width != plan.render_profile.width
        || camera.pixel_height != plan.render_profile.height
        || !near(camera.frame_rate, f64::from(plan.render_profile.frame_rate))
        || !near(camera.frame_width, 128.0 / 9.0)
        || !near(camera.frame_height, 8.0)
        || camera.frame_center.iter().any(|value| !near(*value, 0.0))
        || !camera
            .background_hex
            .eq_ignore_ascii_case(&plan.background.color)
        || !near(camera.background_opacity, 1.0)
        || observed.objects.len() != plan.nodes.len()
    {
        return Err(profile_invalid("native_camera_or_inventory_mismatch"));
    }
    let mut nodes = plan.nodes.iter().collect::<Vec<_>>();
    nodes.sort_by_key(|node| (node.z_index, node.canonical_ordinal));
    for (actual, expected) in observed.objects.iter().zip(nodes) {
        let primitive = match expected.source_kind {
            ManimSourceShapeKindV2::Circle => "Circle",
            ManimSourceShapeKindV2::Shape | ManimSourceShapeKindV2::Rectangle => "Rectangle",
        };
        if actual.name != format!("mw_{}", expected.node_id.simple())
            || actual.primitive != primitive
            || !near(
                actual.center[0],
                (expected.x_px + expected.width_px / 2.0 - 960.0) / 135.0,
            )
            || !near(
                actual.center[1],
                (540.0 - expected.y_px - expected.height_px / 2.0) / 135.0,
            )
            || !near(actual.center[2], 0.0)
            || !near(actual.width, expected.width_px / 135.0)
            || !near(actual.height, expected.height_px / 135.0)
            || !near(actual.z_index, f64::from(expected.z_index))
            || !actual.fill_hex.eq_ignore_ascii_case(&expected.fill_hex)
            || !near(actual.fill_opacity, 1.0)
            || !near(actual.stroke_width, 0.0)
            || !near(actual.stroke_opacity, 0.0)
        {
            return Err(profile_invalid("native_object_property_mismatch"));
        }
    }
    Ok(observed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(text: &str) -> ManimScenePlan {
        ManimScenePlan {
            project_id: Uuid::nil(),
            generation: Uuid::from_u128(1),
            revision: 7,
            scene_id: Uuid::from_u128(2),
            primitives: vec![ManimPrimitive::Text {
                node_id: Uuid::from_u128(3),
                text: text.into(),
                x: 0.0,
                y: 0.0,
                font_size: 48.0,
                color: "#F5B84A".into(),
            }],
        }
    }

    #[test]
    fn compiler_is_fixed_vocabulary_and_escapes_text() {
        let source = compile_manim_python(&plan("x = y + 1\n\"quoted\"")).unwrap();
        assert!(source.contains("class MotionwrightScene(Scene):"));
        assert!(source.contains("Text("));
        for forbidden in ["subprocess", "eval(", "exec(", "__import__", "open("] {
            assert!(!source.contains(forbidden));
        }
        assert!(source.contains("\\n\\\"quoted\\\""));
    }

    #[test]
    fn rejects_non_finite_geometry_and_non_hex_colors() {
        let mut value = plan("safe");
        if let ManimPrimitive::Text { x, color, .. } = &mut value.primitives[0] {
            *x = f64::NAN;
            *color = "WHITE".into();
        }
        assert!(validate_plan(&value).is_err());
    }

    #[test]
    fn render_profile_is_bounded() {
        assert!(ManimRenderProfile::default().validate().is_ok());
        assert!(
            ManimRenderProfile {
                width: 9000,
                height: 1080,
                frame_rate: 30
            }
            .validate()
            .is_err()
        );
        assert_eq!(ManimRenderProfile::default().quality_directory(), "1080p30");
    }

    #[cfg(test)]
    mod tests_v2 {
        use super::*;

        fn plan() -> ManimScenePlanV2 {
            ManimScenePlanV2 {
                schema_version: 2,
                profile_id: ManimProfileIdV2::StaticShapes,
                output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
                binding: ManimSceneBindingV2 {
                    project_id: Uuid::from_u128(1),
                    generation: Uuid::from_u128(2),
                    revision: 7,
                    scene_id: Uuid::from_u128(3),
                    deliverable_id: Uuid::from_u128(4),
                },
                clock: ManimFrameClockV2::from_canonical(
                    Rational::new(3, 1).unwrap(),
                    Rational::new(2, 1).unwrap(),
                    Rational::new(12, 1).unwrap(),
                )
                .unwrap(),
                render_profile: ManimRenderProfile {
                    width: 640,
                    height: 360,
                    frame_rate: 12,
                },
                canvas: ManimCanvasV2::default(),
                background: ManimMatteV2::default(),
                nodes: vec![ManimStaticShapeV2 {
                    node_id: Uuid::from_u128(5),
                    display_name: "Original rectangle".into(),
                    source_kind: ManimSourceShapeKindV2::Rectangle,
                    canonical_ordinal: 0,
                    z_index: 10,
                    x_px: 240.0,
                    y_px: 180.0,
                    width_px: 600.0,
                    height_px: 360.0,
                    fill_hex: "#E02040".into(),
                }],
            }
        }

        #[test]
        fn malformed_v2_never_falls_back_to_legacy_or_invents_clock() {
            let p = plan();
            let wire = serde_json::to_value(&p).unwrap();
            assert!(matches!(
                serde_json::from_value::<ManimStartPlan>(wire.clone()).unwrap(),
                ManimStartPlan::V2(_)
            ));
            for case in 0..6 {
                let mut bad = wire.clone();
                match case {
                    0 => {
                        bad.as_object_mut().unwrap().remove("clock");
                    }
                    1 => bad["schema_version"] = serde_json::json!(1),
                    2 => bad["clock"]["duration"] = serde_json::json!({"num":2,"den":1}),
                    3 => bad["arbitrary_program"] = serde_json::json!("forbidden"),
                    4 => bad["profile_id"] = serde_json::json!("legacy"),
                    _ => bad["nodes"][0]["opacity"] = serde_json::json!(0.5),
                }
                if let Ok(parsed) = serde_json::from_value::<ManimStartPlan>(bad) {
                    assert!(matches!(parsed, ManimStartPlan::V2(_)));
                    assert!(parsed.validate_for_profile(p.render_profile).is_err());
                }
            }
        }

        #[test]
        fn static_clock_preserves_half_open_origin_and_rejects_fractional_boundaries() {
            let p = plan();
            assert_eq!(
                (
                    p.clock.project_start_frame,
                    p.clock.project_end_frame_exclusive,
                    p.clock.frame_count
                ),
                (36, 60, 24)
            );
            assert!(
                ManimFrameClockV2::from_canonical(
                    Rational::ZERO,
                    Rational::ONE,
                    Rational::new(30000, 1001).unwrap()
                )
                .is_err()
            );
            assert!(
                ManimFrameClockV2::from_canonical(
                    Rational::new(1, 25).unwrap(),
                    Rational::ONE,
                    Rational::new(12, 1).unwrap()
                )
                .is_err()
            );
            let mut forged = p.clock;
            forged.frame_count += 1;
            assert!(forged.validate().is_err());
        }

        #[test]
        fn static_clock_rejects_unrepresentable_canonical_end() {
            assert!(
                ManimFrameClockV2::from_canonical(
                    Rational::new(i64::MAX, 1).unwrap(),
                    Rational::ONE,
                    Rational::ONE,
                )
                .is_err()
            );
        }

        #[test]
        fn static_wait_fixes_29_at_25_without_changing_canonical_time() {
            let before = 29.0_f64 / 25.0_f64;
            assert_eq!((before / (1.0 / 25.0)).trunc(), 28.0);
            let wait = frozen_wait_v2(29, 25).unwrap();
            assert_eq!(wait.next_up_steps, 1);
            assert_eq!((wait.seconds / (1.0 / 25.0)).trunc(), 29.0);
            assert_eq!(within_two_ulps(wait.seconds, before, 29, 25), Some(true));
        }

        #[test]
        fn static_wait_admitted_clock_domain_is_representable_with_bounded_error() {
            for fps in 1..=120 {
                for count in 1..=MAX_STATIC_FRAMES_V2 {
                    let wait = frozen_wait_v2(count, fps).unwrap();
                    assert_eq!(
                        (wait.seconds / (1.0 / f64::from(fps))).trunc(),
                        f64::from(count)
                    );
                }
            }
        }

        #[test]
        fn source_wire_uses_the_pinned_sdk_rational_string_contract() {
            let p = plan();
            let wire = serde_json::to_value(&p).unwrap();
            assert_eq!(
                wire["clock"]["duration"],
                serde_json::json!({"num":"2","den":"1"})
            );
            let mut bad = wire;
            bad["clock"]["duration"] = serde_json::json!({"num":2,"den":1});
            assert!(serde_json::from_value::<ManimScenePlanV2>(bad).is_err());
            let data = source_json(&p).unwrap();
            assert_eq!(
                serde_json::from_slice::<ManimScenePlanV2>(&data).unwrap(),
                p
            );
        }

        #[test]
        fn source_bundle_roundtrip_preserves_all_five_actual_entries() {
            let p = plan();
            let first = source_bundle_v2(&p).unwrap();
            let observed = inspect_source_bundle_v2(&first).unwrap();
            assert_eq!(observed.plan, p);
            assert_eq!(observed.entries.len(), 5);
            assert_eq!(source_bundle_v2(&observed.plan).unwrap(), first);
            assert_eq!(
                observed.closure.source_sha256,
                source_digest(&observed.entries["scene.py"])
            );
        }

        #[test]
        fn source_import_rejects_rehashed_python_without_executing_it() {
            let p = plan();
            let mut package = source_package_v2(&p).unwrap();
            package
                .entries
                .get_mut("scene.py")
                .unwrap()
                .extend_from_slice(b"\nraise RuntimeError('untrusted edit')\n");
            let mut manifest: serde_json::Value =
                serde_json::from_slice(&package.entries["manifest.json"]).unwrap();
            manifest["source_sha256"] =
                serde_json::json!(source_digest(&package.entries["scene.py"]));
            for row in manifest["entries"].as_array_mut().unwrap() {
                if row["path"] == "scene.py" {
                    row["sha256"] = serde_json::json!(source_digest(&package.entries["scene.py"]));
                    row["bytes"] = serde_json::json!(package.entries["scene.py"].len());
                }
            }
            package
                .entries
                .insert("manifest.json".into(), source_json(&manifest).unwrap());
            let error = inspect_source_bundle_v2(&encode_source_entries(&package.entries).unwrap())
                .unwrap_err();
            assert_eq!(error.to_string(), "source_projection_mismatch");
        }

        #[test]
        fn source_import_rejects_traversal_extra_entry_tail_corruption_and_duplicate_json() {
            let p = plan();
            let original = source_bundle_v2(&p).unwrap();
            let mut tail = original.clone();
            tail.extend_from_slice(b"undeclared trailer");
            assert!(inspect_source_bundle_v2(&tail).is_err());
            let mut corrupt = original;
            corrupt[80] ^= 1;
            assert!(inspect_source_bundle_v2(&corrupt).is_err());
            for name in ["../scene.py", "unlisted.txt"] {
                let mut entries = source_package_v2(&p).unwrap().entries;
                entries.insert(name.into(), b"test data".to_vec());
                assert!(
                    inspect_source_bundle_v2(&encode_source_entries(&entries).unwrap()).is_err()
                );
            }
            let mut entries = source_package_v2(&p).unwrap().entries;
            let wire = entries.get_mut("plan.json").unwrap();
            wire.splice(1..1, b"\"schema_version\":2,".iter().copied());
            assert_eq!(
                inspect_source_bundle_v2(&encode_source_entries(&entries).unwrap())
                    .unwrap_err()
                    .to_string(),
                "source_plan_invalid"
            );
        }

        #[test]
        fn portable_rebind_changes_only_generation_revision_and_keeps_origin_immutable() {
            let original = plan();
            let carrier = source_bundle_v2(&original).unwrap();
            let mut current = original.clone();
            current.binding.generation = Uuid::from_u128(9);
            current.binding.revision += 2;
            let rebound = rebind_source_v2(&original, &current).unwrap();
            assert_eq!(
                rebound.entries["scene.py"],
                source_package_v2(&original).unwrap().entries["scene.py"]
            );
            assert_ne!(
                rebound.entries["plan.json"],
                source_package_v2(&original).unwrap().entries["plan.json"]
            );
            assert_eq!(carrier, source_bundle_v2(&original).unwrap());
            current.nodes[0].x_px += 90.0;
            assert!(rebind_source_v2(&original, &current).is_err());
            assert_ne!(source_bundle_v2(&current).unwrap(), carrier);
        }

        #[test]
        fn static_shapes_reject_false_identity_ellipse_profile_and_unknown_controls() {
            let p = plan();
            for case in 0..6 {
                let mut bad = p.clone();
                match case {
                    0 => bad.binding.project_id = Uuid::nil(),
                    1 => {
                        bad.nodes[0].source_kind = ManimSourceShapeKindV2::Circle;
                    }
                    2 => bad.render_profile.frame_rate = 25,
                    3 => bad.nodes[0].x_px = f64::NAN,
                    4 => bad.nodes[0].fill_hex = "#E0204080".into(),
                    _ => bad.nodes.push(bad.nodes[0].clone()),
                }
                assert!(validate_plan_v2(&bad).is_err());
            }
            let mut wire = serde_json::to_value(&p).unwrap();
            wire["nodes"][0]["opacity"] = serde_json::json!(0.5);
            assert!(serde_json::from_value::<ManimScenePlanV2>(wire).is_err());
        }
    }
}

/// Complete decoded-video data validation; source authority remains with the original Host job.
pub mod video_observation;
