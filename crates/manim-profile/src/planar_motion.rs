//! Versioned planar motion source. This module extends the existing Manim
//! compiler and source format; it is not a runtime, grant, or renderer admission.
//! The midpoint play sampler still requires a pinned runtime/all-frame gate.
use super::{
    MAX_PRIMITIVES, MAX_SOURCE_BUNDLE_BYTES_V2, MAX_SOURCE_ENTRY_BYTES_V2, ManimCanvasV2,
    ManimFrameClockV2, ManimMatteV2, ManimNativeCameraReadbackV1, ManimOutputRoleV2,
    ManimRenderProfile, ManimSceneBindingV2, ManimSourceClosureV1, ManimSourceEntryV1,
    ManimSourceShapeKindV2, Result, SCENE_CLASS, color_hex, encode_source_entries, finite_coord,
    positive_dimension, profile_invalid, py_string, source_digest, source_json,
};
use semwright_media_time::Rational;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Cursor, Read},
};
use uuid::Uuid;

pub const PLANAR_MOTION_PROFILE_V3: &str = "mw-manim-planar-motion-v3";
pub const MOTION_COMPILER_V3: &str = "motionwright.manim.rust.planar_motion.v3.1";
pub const MOTION_SAMPLER_V1: &str = "manim-0.21-cairo-midpoint-play-v1";
pub const MAX_MOTION_NODE_FRAMES_V3: usize = 32_768;
pub const MAX_MOTION_READBACK_BYTES_V3: usize = 8 * 1024 * 1024;
// The existing native transport carries the getter inline. Its current source
// readback ceiling applies before any start, below the outer artifact ceiling.
pub const MAX_MOTION_INLINE_READBACK_BYTES_V3: usize = MAX_SOURCE_ENTRY_BYTES_V2;
pub const MAX_MOTION_KEYS_PER_NODE_V3: usize = 128;
// Independent ceilings: serialized source retains the existing 256 KiB entry
// and 2 MiB archive budgets. A node-frame count never bypasses a byte budget.
const READBACK_FIXED_BYTES: usize = 2_048;
const READBACK_FRAME_BYTES: usize = 1_024;
const READBACK_OBJECT_BYTES: usize = 1_024;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum ManimMotionProfileIdV3 {
    #[serde(rename = "mw-manim-planar-motion-v3")]
    PlanarMotion,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum ManimMotionPropertyV3 {
    X,
    Y,
    Width,
    Height,
    RotationDeg,
    Opacity,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ManimMotionInterpolationV3 {
    Hold,
    Linear,
    EaseOutCubic,
    EaseInOut,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimPlanarPoseV3 {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub rotation_deg: f64,
    pub opacity: f64,
}

impl ManimPlanarPoseV3 {
    fn value(self, property: ManimMotionPropertyV3) -> f64 {
        match property {
            ManimMotionPropertyV3::X => self.x,
            ManimMotionPropertyV3::Y => self.y,
            ManimMotionPropertyV3::Width => self.width,
            ManimMotionPropertyV3::Height => self.height,
            ManimMotionPropertyV3::RotationDeg => self.rotation_deg,
            ManimMotionPropertyV3::Opacity => self.opacity,
        }
    }
    fn set(&mut self, property: ManimMotionPropertyV3, value: f64) {
        match property {
            ManimMotionPropertyV3::X => self.x = value,
            ManimMotionPropertyV3::Y => self.y = value,
            ManimMotionPropertyV3::Width => self.width = value,
            ManimMotionPropertyV3::Height => self.height = value,
            ManimMotionPropertyV3::RotationDeg => self.rotation_deg = value,
            ManimMotionPropertyV3::Opacity => self.opacity = value,
        }
    }
    pub fn validate(self) -> Result<()> {
        if [
            self.x,
            self.y,
            self.width,
            self.height,
            self.rotation_deg,
            self.opacity,
        ]
        .iter()
        .any(|x| !x.is_finite())
            || !(0.0..=1.0).contains(&self.opacity)
            || self.rotation_deg.abs() > 36_000.0
        {
            return Err(profile_invalid("motion_pose_out_of_bounds"));
        }
        positive_dimension(self.width / 135.0)?;
        positive_dimension(self.height / 135.0)?;
        finite_coord((self.x + self.width / 2.0 - 960.0) / 135.0)?;
        finite_coord((540.0 - self.y - self.height / 2.0) / 135.0)?;
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimMotionKeyframeV3 {
    pub at: Rational,
    pub property: ManimMotionPropertyV3,
    pub value: f64,
    pub interpolation: ManimMotionInterpolationV3,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimPlanarNodeV3 {
    pub node_id: Uuid,
    pub display_name: String,
    pub source_kind: ManimSourceShapeKindV2,
    pub canonical_ordinal: u32,
    pub z_index: i32,
    pub base: ManimPlanarPoseV3,
    pub fill_hex: String,
    pub keyframes: Vec<ManimMotionKeyframeV3>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimPlanarCameraV3 {
    pub center_x: f64,
    pub center_y: f64,
    pub zoom: f64,
    pub rotation_deg: f64,
    /// Preserved editorial guide; it is not painted into the contribution.
    pub safe_margin: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimScenePlanV3 {
    pub schema_version: u16,
    pub profile_id: ManimMotionProfileIdV3,
    pub output_role: ManimOutputRoleV2,
    pub binding: ManimSceneBindingV2,
    pub clock: ManimFrameClockV2,
    pub render_profile: ManimRenderProfile,
    pub canvas: ManimCanvasV2,
    pub background: ManimMatteV2,
    pub camera: ManimPlanarCameraV3,
    pub nodes: Vec<ManimPlanarNodeV3>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimMotionSamplingV1 {
    pub sampler_id: String,
    pub internal_play_duration: Rational,
    pub seconds_hex: String,
    pub predicted_frame_count: u32,
    pub runtime_all_frames_verified: bool,
}

pub fn motion_sampling_v3(frames: u32, fps: u32) -> Result<ManimMotionSamplingV1> {
    if !(2..=3_600).contains(&frames) || !(1..=120).contains(&fps) {
        return Err(profile_invalid("motion_frame_budget"));
    }
    let internal = Rational::new(i64::from(frames) * 2 - 1, i64::from(fps) * 2)
        .map_err(|_| profile_invalid("motion_clock_overflow"))?;
    let seconds = (f64::from(frames) - 0.5) / f64::from(fps);
    if !(f64::from(frames - 1) / f64::from(fps) < seconds
        && seconds < f64::from(frames) / f64::from(fps))
    {
        return Err(profile_invalid("motion_play_interval_unrepresentable"));
    }
    let bits = seconds.to_bits();
    let exponent = ((bits >> 52) & 0x7ff) as i32 - 1023;
    Ok(ManimMotionSamplingV1 {
        sampler_id: MOTION_SAMPLER_V1.into(),
        internal_play_duration: internal,
        seconds_hex: format!("0x1.{:013x}p{exponent:+}", bits & ((1_u64 << 52) - 1)),
        predicted_frame_count: frames,
        runtime_all_frames_verified: false,
    })
}

fn validate_node(node: &ManimPlanarNodeV3, duration: Rational) -> Result<()> {
    node.base.validate()?;
    color_hex(&node.fill_hex)?;
    if node.node_id.is_nil()
        || node.display_name.trim().is_empty()
        || node.display_name.len() > 160
        || node.display_name.contains('\0')
        || node.keyframes.len() > MAX_MOTION_KEYS_PER_NODE_V3
    {
        return Err(profile_invalid("motion_node_identity_or_key_budget"));
    }
    let mut keys = BTreeSet::new();
    for key in &node.keyframes {
        key.at
            .validate()
            .map_err(|_| profile_invalid("motion_key_time"))?;
        if key.at < Rational::ZERO || key.at >= duration || !keys.insert((key.property, key.at)) {
            return Err(profile_invalid("motion_key_interval_or_duplicate"));
        }
        let mut endpoint = node.base;
        endpoint.set(key.property, key.value);
        endpoint.validate()?;
    }
    if node.source_kind == ManimSourceShapeKindV2::Circle {
        if node.base.width != node.base.height {
            return Err(profile_invalid("motion_circle_is_ellipse"));
        }
        let track = |property| {
            let mut values = node
                .keyframes
                .iter()
                .filter(|k| k.property == property)
                .map(|k| (k.at, k.value, k.interpolation))
                .collect::<Vec<_>>();
            values.sort_by_key(|k| k.0);
            values
        };
        if track(ManimMotionPropertyV3::Width) != track(ManimMotionPropertyV3::Height) {
            return Err(profile_invalid("motion_circle_size_tracks_differ"));
        }
    }
    Ok(())
}

pub fn validate_plan_v3(plan: &ManimScenePlanV3) -> Result<()> {
    plan.clock.validate()?;
    plan.render_profile.validate()?;
    motion_sampling_v3(plan.clock.frame_count, plan.render_profile.frame_rate)?;
    let p = plan.render_profile;
    if plan.schema_version != 3
        || plan.canvas != ManimCanvasV2::default()
        || plan.background != ManimMatteV2::default()
        || u64::from(p.width) * 9 != u64::from(p.height) * 16
        || !p.width.is_multiple_of(2)
        || !p.height.is_multiple_of(2)
        || i64::from(p.frame_rate) != plan.clock.frame_rate.num
    {
        return Err(profile_invalid("motion_render_profile"));
    }
    if [
        plan.binding.project_id,
        plan.binding.generation,
        plan.binding.scene_id,
        plan.binding.deliverable_id,
    ]
    .iter()
    .any(Uuid::is_nil)
    {
        return Err(profile_invalid("motion_source_identity"));
    }
    let c = &plan.camera;
    if [
        c.center_x,
        c.center_y,
        c.zoom,
        c.rotation_deg,
        c.safe_margin,
    ]
    .iter()
    .any(|x| !x.is_finite())
        || c.rotation_deg != 0.0
        || !(0.01..=100.0).contains(&c.zoom)
        || !(0.0..=0.49).contains(&c.safe_margin)
    {
        return Err(profile_invalid("motion_camera_unsupported"));
    }
    finite_coord((c.center_x - 960.0) / 135.0)?;
    finite_coord((540.0 - c.center_y) / 135.0)?;
    let frames = plan.clock.frame_count as usize;
    let node_frames = plan
        .nodes
        .len()
        .checked_mul(frames)
        .ok_or_else(|| profile_invalid("motion_observation_budget"))?;
    let readback_bound = frames
        .checked_mul(READBACK_FRAME_BYTES)
        .and_then(|n| {
            node_frames
                .checked_mul(READBACK_OBJECT_BYTES)
                .and_then(|m| n.checked_add(m))
        })
        .and_then(|n| n.checked_add(READBACK_FIXED_BYTES))
        .ok_or_else(|| profile_invalid("motion_observation_budget"))?;
    if plan.nodes.is_empty()
        || plan.nodes.len() > MAX_PRIMITIVES
        || node_frames > MAX_MOTION_NODE_FRAMES_V3
        || readback_bound > MAX_MOTION_READBACK_BYTES_V3
        || readback_bound > MAX_MOTION_INLINE_READBACK_BYTES_V3
    {
        return Err(profile_invalid("motion_observation_budget"));
    }
    let mut ids = BTreeSet::new();
    for (ordinal, node) in plan.nodes.iter().enumerate() {
        if node.canonical_ordinal as usize != ordinal || !ids.insert(node.node_id) {
            return Err(profile_invalid("motion_node_order_or_duplicate"));
        }
        validate_node(node, plan.clock.duration)?;
    }
    Ok(())
}

fn sample_pose(node: &ManimPlanarNodeV3, at: Rational) -> Result<ManimPlanarPoseV3> {
    let mut output = node.base;
    for property in [
        ManimMotionPropertyV3::X,
        ManimMotionPropertyV3::Y,
        ManimMotionPropertyV3::Width,
        ManimMotionPropertyV3::Height,
        ManimMotionPropertyV3::RotationDeg,
        ManimMotionPropertyV3::Opacity,
    ] {
        let mut keys = node
            .keyframes
            .iter()
            .filter(|k| k.property == property)
            .collect::<Vec<_>>();
        keys.sort_by_key(|k| k.at);
        let mut from_time = Rational::ZERO;
        let mut from_value = output.value(property);
        for key in keys {
            if at < key.at {
                let elapsed = at
                    .checked_sub(from_time)
                    .map_err(|_| profile_invalid("motion_sampler_overflow"))?;
                let span = key
                    .at
                    .checked_sub(from_time)
                    .map_err(|_| profile_invalid("motion_sampler_overflow"))?;
                let ratio = elapsed
                    .checked_div(span)
                    .map_err(|_| profile_invalid("motion_sampler_overflow"))?;
                let progress = ratio.num as f64 / ratio.den as f64;
                if !progress.is_finite() || !(0.0..1.0).contains(&progress) {
                    return Err(profile_invalid("motion_sampler_interval"));
                }
                let eased = match key.interpolation {
                    ManimMotionInterpolationV3::Hold => 0.0,
                    ManimMotionInterpolationV3::Linear => progress,
                    ManimMotionInterpolationV3::EaseOutCubic => 1.0 - (1.0 - progress).powi(3),
                    ManimMotionInterpolationV3::EaseInOut => {
                        progress * progress * (3.0 - 2.0 * progress)
                    }
                };
                output.set(property, from_value + (key.value - from_value) * eased);
                break;
            }
            from_time = key.at;
            from_value = key.value;
            output.set(property, key.value);
        }
    }
    output.validate()?;
    Ok(output)
}

pub fn sample_frame_v3(plan: &ManimScenePlanV3, frame: u32) -> Result<Vec<ManimPlanarPoseV3>> {
    validate_plan_v3(plan)?;
    if frame >= plan.clock.frame_count {
        return Err(profile_invalid("motion_frame_outside_clock"));
    }
    let at = Rational::new(i64::from(frame), i64::from(plan.render_profile.frame_rate))
        .map_err(|_| profile_invalid("motion_sampler_overflow"))?;
    plan.nodes.iter().map(|n| sample_pose(n, at)).collect()
}

pub fn compile_manim_python_v3(plan: &ManimScenePlanV3) -> Result<String> {
    validate_plan_v3(plan)?;
    let sampling = motion_sampling_v3(plan.clock.frame_count, plan.render_profile.frame_rate)?;
    let mut samples = Vec::with_capacity(plan.clock.frame_count as usize);
    for index in 0..plan.clock.frame_count {
        let at = Rational::new(i64::from(index), i64::from(plan.render_profile.frame_rate))
            .map_err(|_| profile_invalid("motion_sampler_overflow"))?;
        samples.push(
            plan.nodes
                .iter()
                .map(|n| sample_pose(n, at))
                .collect::<Result<Vec<_>>>()?,
        );
    }
    let end = plan
        .nodes
        .iter()
        .map(|n| sample_pose(n, plan.clock.duration))
        .collect::<Result<Vec<_>>>()?;
    let data = serde_json::to_string(&serde_json::json!({
        "plan_sha256": source_digest(&source_json(plan)?), "binding": plan.binding,
        "profile": plan.render_profile, "camera": plan.camera, "nodes": plan.nodes,
        "samples": samples, "end": end, "sampling": sampling,
    }))
    .map_err(|_| profile_invalid("motion_source_encoding"))?;
    let mut source = format!(
        "# Generated by {MOTION_COMPILER_V3}\n# Imported source is data; only this compiler emits executable code.\n_DATA = {}\n",
        py_string(&data)?
    );
    source.push_str(MOTION_PROGRAM_V3);
    if source.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(profile_invalid("motion_source_byte_budget"));
    }
    Ok(source)
}

const MOTION_PROGRAM_V3: &str = r##"from manim import Circle, Rectangle, MovingCameraScene, UpdateFromAlphaFunc, VGroup, config, linear
from manim import __version__ as _manim_version
import json
import math
from pathlib import Path

_D = json.loads(_DATA)
_P = _D["profile"]
if _manim_version != "0.21.0":
    raise ValueError("motion_manim_version_mismatch")
if (config.pixel_width, config.pixel_height, config.frame_rate) != (_P["width"], _P["height"], _P["frame_rate"]):
    raise ValueError("motion_render_profile_mismatch")
config.frame_width = 128.0 / 9.0
config.frame_height = 8.0
config.background_color = "#000000"
config.background_opacity = 1.0

class MotionwrightScene(MovingCameraScene):
    def construct(self):
        if self.camera.frame_rate != _P["frame_rate"]:
            raise ValueError("motion_camera_rate_mismatch")
        camera = _D["camera"]
        self.camera.frame.set(width=128.0 / (9.0 * camera["zoom"]), height=8.0 / camera["zoom"])
        self.camera.frame.move_to([(camera["center_x"] - 960.0) / 135.0, (540.0 - camera["center_y"]) / 135.0, 0.0])
        order = sorted(range(len(_D["nodes"])), key=lambda i: (_D["nodes"][i]["z_index"], _D["nodes"][i]["canonical_ordinal"]))
        objects = []
        def make(index, pose):
            node = _D["nodes"][index]
            name = "mw_" + node["node_id"].replace("-", "")
            if node["source_kind"] == "circle":
                item = Circle(radius=pose["width"] / 270.0, name=name)
            else:
                item = Rectangle(width=pose["width"] / 135.0, height=pose["height"] / 135.0, name=name)
            item.rotate(-pose["rotation_deg"] * math.pi / 180.0)
            item.move_to([(pose["x"] + pose["width"] / 2.0 - 960.0) / 135.0, (540.0 - pose["y"] - pose["height"] / 2.0) / 135.0, 0.0])
            item.set_fill(node["fill_hex"], opacity=pose["opacity"])
            item.set_stroke(width=0.0, opacity=0.0)
            item.set_z_index(node["z_index"])
            return item
        for index in order:
            objects.append(make(index, _D["samples"][0][index]))
        group = VGroup(*objects)
        self.add(group)
        samples = []
        observed_alphas = []
        def observe_camera():
            return {"pixel_width": self.camera.pixel_width, "pixel_height": self.camera.pixel_height,
                "frame_rate": float(self.camera.frame_rate), "frame_width": float(self.camera.frame_width),
                "frame_height": float(self.camera.frame_height), "frame_center": [float(x) for x in self.camera.frame_center],
                "background_hex": self.camera.background_color.to_hex(), "background_opacity": float(self.camera.background_opacity)}
        def observe(item):
            return {"name": item.name, "primitive": item.__class__.__name__,
                "center": [float(x) for x in item.get_center()],
                "width": float(item.width), "height": float(item.height), "z_index": float(item.z_index),
                "fill_hex": item.get_fill_color().to_hex(), "fill_opacity": float(item.get_fill_opacity()),
                "stroke_width": float(item.get_stroke_width()), "stroke_opacity": float(item.get_stroke_opacity()),
                "vertices": [[float(x) for x in p] for p in item.get_vertices()] if isinstance(item, Rectangle) else [],
                "curve_start": [float(x) for x in item.get_start()],
                "curve_mid": [float(x) for x in item.point_from_proportion(0.5)]}
        def update(_moving_group, alpha):
            if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
                raise ValueError("motion_callback_alpha_invalid")
            terminal = alpha == 1.0
            coordinate = alpha * (len(_D["samples"]) - 0.5)
            index = round(coordinate)
            if not terminal and (abs(coordinate - index) > 1e-6 or not 0 <= index < len(_D["samples"])):
                raise ValueError("motion_callback_not_on_frame_grid")
            poses = _D["end"] if terminal else _D["samples"][index]
            for item, node_index in zip(objects, order):
                target = make(node_index, poses[node_index])
                item.become(target)
                item.name = target.name
                item.set_z_index(target.z_index)
            if terminal:
                return
            row = {"index": index, "camera": observe_camera(), "objects": [observe(item) for item in objects]}
            if len(samples) == index:
                samples.append(row)
                observed_alphas.append(float(alpha))
            elif index == 0 and len(samples) == 1 and samples[0] == row:
                pass  # Animation.begin and the first rendered sample may both interpolate(0).
            else:
                raise ValueError("motion_callback_sequence_not_complete")
        seconds = float.fromhex(_D["sampling"]["seconds_hex"])
        self.play(UpdateFromAlphaFunc(group, update, run_time=seconds, rate_func=linear))
        if len(samples) != len(_D["samples"]):
            raise ValueError("motion_native_samples_missing")
        record = {"schema_version": 1, "profile_id": "mw-manim-planar-motion-v3",
            "scene_class": self.__class__.__name__, "plan_sha256": _D["plan_sha256"],
            "binding": _D["binding"], "sampler_id": _D["sampling"]["sampler_id"],
            "sample_count": len(samples), "callback_alphas": observed_alphas, "samples": samples,
            "decoded_video_verified": False, "canonical_import_applied": False}
        encoded = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if not 0 < len(encoded) <= 8388608:
            raise ValueError("motion_native_readback_byte_budget")
        with Path(__file__).with_suffix(".motion.json").open("xb") as output:
            output.write(encoded)
"##;

#[derive(Debug, Clone, PartialEq)]
pub struct ManimSourcePackageV3 {
    pub plan: ManimScenePlanV3,
    pub entries: BTreeMap<String, Vec<u8>>,
    pub closure: ManimSourceClosureV1,
}

pub fn source_package_v3(plan: &ManimScenePlanV3) -> Result<ManimSourcePackageV3> {
    validate_plan_v3(plan)?;
    let mut entries: BTreeMap<String, Vec<u8>> = BTreeMap::from([
        ("plan.json".into(), source_json(plan)?),
        (
            "scene.py".into(),
            compile_manim_python_v3(plan)?.into_bytes(),
        ),
        (
            "render-config.json".into(),
            source_json(&serde_json::json!({
                "schema_version": 1, "profile_id": PLANAR_MOTION_PROFILE_V3,
                "compiler_id": MOTION_COMPILER_V3, "manim_version_requirement": "0.21.0",
                "renderer": "cairo", "scene_class": SCENE_CLASS, "render_profile": plan.render_profile,
                "format": "mp4", "seed": 0, "disable_caching": true, "background": plan.background,
                "camera": plan.camera, "sampler": motion_sampling_v3(plan.clock.frame_count, plan.render_profile.frame_rate)?,
                "source_entry_byte_limit": MAX_SOURCE_ENTRY_BYTES_V2,
                "readback_byte_limit_hypothesis": MAX_MOTION_READBACK_BYTES_V3,
                "inline_readback_byte_limit": MAX_MOTION_INLINE_READBACK_BYTES_V3,
                "native_frame_readback_required": true, "runtime_binary_attested": false
            }))?,
        ),
        (
            "assets.lock.json".into(),
            source_json(&serde_json::json!({
                "schema_version": 1, "profile_id": PLANAR_MOTION_PROFILE_V3, "assets": [],
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
        "schema_version": 1, "bundle_kind": "motionwright.manim.editable_planar_motion_source",
        "compiler_id": MOTION_COMPILER_V3, "encoding_id": "serde-json-pretty-rational-strings-zip9-stored-v1",
        "plan_sha256": plan_sha256, "source_sha256": source_sha256, "config_sha256": config_sha256,
        "entries": rows, "losses": ["binary64_geometry_after_rational_interval_selection",
            "opaque_black_matte_no_output_alpha", "no_text_graph_masks_stroke_or_dynamic_camera",
            "no_audio_or_captions", "safe_margin_preserved_editorial_guide",
            "requested_rec709_master_requires_independent_colorimetry_gate",
            "generated_python_edits_outside_plan_not_importable", "midpoint_play_sampler_requires_all_frame_runtime_gate"],
        "native_render_verified": false, "native_reopen_verified": false, "canonical_import_applied": false
    }))?;
    let manifest_sha256 = source_digest(&manifest);
    entries.insert("manifest.json".into(), manifest);
    let closure = ManimSourceClosureV1 {
        plan_schema_version: 3,
        compiler_contract_id: MOTION_COMPILER_V3.into(),
        plan_sha256,
        source_sha256,
        config_sha256,
        assets_lock_sha256,
        manifest_sha256,
        entries: entries
            .iter()
            .map(|(path, bytes)| ManimSourceEntryV1 {
                path: path.clone(),
                bytes: bytes.len() as u64,
                sha256: source_digest(bytes),
            })
            .collect(),
    };
    encode_source_entries(&entries)?;
    Ok(ManimSourcePackageV3 {
        plan: plan.clone(),
        entries,
        closure,
    })
}

pub fn source_bundle_v3(plan: &ManimScenePlanV3) -> Result<Vec<u8>> {
    encode_source_entries(&source_package_v3(plan)?.entries)
}

/// Memory-only, canonical ZIP inspection. It never extracts or imports Python.
pub fn inspect_source_bundle_v3(bytes: &[u8]) -> Result<ManimSourcePackageV3> {
    const NAMES: [&str; 5] = [
        "assets.lock.json",
        "manifest.json",
        "plan.json",
        "render-config.json",
        "scene.py",
    ];
    if bytes.is_empty() || bytes.len() > MAX_SOURCE_BUNDLE_BYTES_V2 {
        return Err(profile_invalid("motion_archive_budget"));
    }
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| profile_invalid("motion_archive_invalid"))?;
    if zip.len() != NAMES.len() || !zip.comment().is_empty() {
        return Err(profile_invalid("motion_archive_entry_set"));
    }
    let mut entries = BTreeMap::new();
    for index in 0..NAMES.len() {
        let mut file = zip
            .by_index(index)
            .map_err(|_| profile_invalid("motion_archive_invalid"))?;
        let name = file
            .name()
            .map_err(|_| profile_invalid("motion_archive_name"))?
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
            return Err(profile_invalid("motion_archive_entry_invalid"));
        }
        let expected = file.size() as usize;
        let mut entry = Vec::with_capacity(expected);
        file.by_ref()
            .take(MAX_SOURCE_ENTRY_BYTES_V2 as u64 + 1)
            .read_to_end(&mut entry)
            .map_err(|_| profile_invalid("motion_archive_checksum"))?;
        if entry.len() != expected {
            return Err(profile_invalid("motion_archive_size"));
        }
        entries.insert(name, entry);
    }
    let plan: ManimScenePlanV3 = serde_json::from_slice(&entries["plan.json"])
        .map_err(|_| profile_invalid("motion_source_plan_invalid"))?;
    let expected = source_package_v3(&plan)?;
    if entries["scene.py"] != expected.entries["scene.py"] {
        return Err(profile_invalid("motion_source_projection_mismatch"));
    }
    if entries != expected.entries || encode_source_entries(&entries)? != bytes {
        return Err(profile_invalid("motion_source_archive_not_canonical"));
    }
    Ok(expected)
}

pub fn rebind_source_v3(
    origin: &ManimScenePlanV3,
    current: &ManimScenePlanV3,
) -> Result<ManimSourcePackageV3> {
    validate_plan_v3(origin)?;
    validate_plan_v3(current)?;
    let mut rebound = origin.clone();
    rebound.binding.generation = current.binding.generation;
    rebound.binding.revision = current.binding.revision;
    if rebound != *current {
        return Err(profile_invalid("motion_semantic_rebind_mismatch"));
    }
    source_package_v3(current)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimMotionObjectReadbackV3 {
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
    pub vertices: Vec<[f64; 3]>,
    pub curve_start: [f64; 3],
    pub curve_mid: [f64; 3],
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimMotionFrameReadbackV3 {
    pub index: u32,
    pub camera: ManimNativeCameraReadbackV1,
    pub objects: Vec<ManimMotionObjectReadbackV3>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimMotionReadbackV3 {
    pub schema_version: u16,
    pub profile_id: ManimMotionProfileIdV3,
    pub scene_class: String,
    pub plan_sha256: String,
    pub binding: ManimSceneBindingV2,
    pub sampler_id: String,
    pub sample_count: u32,
    pub callback_alphas: Vec<f64>,
    pub samples: Vec<ManimMotionFrameReadbackV3>,
    pub decoded_video_verified: bool,
    pub canonical_import_applied: bool,
}

fn near(actual: f64, expected: f64) -> bool {
    actual.is_finite() && (actual - expected).abs() <= 1.0e-7
}
fn point_near(actual: &[f64; 3], expected: [f64; 3]) -> bool {
    actual.iter().zip(expected).all(|(a, e)| near(*a, e))
}

/// Expected native vertices, in Rectangle's public UR, UL, DL, DR order.
pub fn projected_rectangle_vertices_v3(pose: ManimPlanarPoseV3) -> [[f64; 3]; 4] {
    let center = [
        (pose.x + pose.width / 2.0 - 960.0) / 135.0,
        (540.0 - pose.y - pose.height / 2.0) / 135.0,
    ];
    let (s, c) = (-pose.rotation_deg.to_radians()).sin_cos();
    [(1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0), (1.0, -1.0)].map(|(x, y)| {
        let x = x * pose.width / 270.0;
        let y = y * pose.height / 270.0;
        [center[0] + c * x - s * y, center[1] + s * x + c * y, 0.0]
    })
}

/// Validates native getter observations, not the video or the receipt journal.
pub fn inspect_motion_readback_v3(
    plan: &ManimScenePlanV3,
    bytes: &[u8],
) -> Result<ManimMotionReadbackV3> {
    validate_plan_v3(plan)?;
    if bytes.is_empty() || bytes.len() > MAX_MOTION_READBACK_BYTES_V3 {
        return Err(profile_invalid("motion_native_readback_budget"));
    }
    let r: ManimMotionReadbackV3 = serde_json::from_slice(bytes)
        .map_err(|_| profile_invalid("motion_native_readback_invalid"))?;
    if r.schema_version != 1
        || r.profile_id != plan.profile_id
        || r.scene_class != SCENE_CLASS
        || r.binding != plan.binding
        || r.plan_sha256 != source_digest(&source_json(plan)?)
        || r.sampler_id != MOTION_SAMPLER_V1
        || r.sample_count != plan.clock.frame_count
        || r.samples.len() != plan.clock.frame_count as usize
        || r.callback_alphas.len() != r.samples.len()
        || r.decoded_video_verified
        || r.canonical_import_applied
    {
        return Err(profile_invalid("motion_native_readback_identity_or_count"));
    }
    let mut order = (0..plan.nodes.len()).collect::<Vec<_>>();
    order.sort_by_key(|i| (plan.nodes[*i].z_index, plan.nodes[*i].canonical_ordinal));
    for (index, frame) in r.samples.iter().enumerate() {
        let alpha = r.callback_alphas[index];
        if frame.index as usize != index
            || !alpha.is_finite()
            || !(0.0..1.0).contains(&alpha)
            || (alpha * (f64::from(plan.clock.frame_count) - 0.5) - index as f64).abs() > 1.0e-6
            || frame.objects.len() != order.len()
        {
            return Err(profile_invalid("motion_native_frame_sequence"));
        }
        let c = &frame.camera;
        if c.pixel_width != plan.render_profile.width
            || c.pixel_height != plan.render_profile.height
            || !near(c.frame_rate, f64::from(plan.render_profile.frame_rate))
            || !near(c.frame_width, 128.0 / (9.0 * plan.camera.zoom))
            || !near(c.frame_height, 8.0 / plan.camera.zoom)
            || !point_near(
                &c.frame_center,
                [
                    (plan.camera.center_x - 960.0) / 135.0,
                    (540.0 - plan.camera.center_y) / 135.0,
                    0.0,
                ],
            )
            || !c.background_hex.eq_ignore_ascii_case("#000000")
            || !near(c.background_opacity, 1.0)
        {
            return Err(profile_invalid("motion_native_camera_mismatch"));
        }
        let poses = sample_frame_v3(plan, index as u32)?;
        for (actual, ordinal) in frame.objects.iter().zip(&order) {
            let n = &plan.nodes[*ordinal];
            let p = poses[*ordinal];
            let center = [
                (p.x + p.width / 2.0 - 960.0) / 135.0,
                (540.0 - p.y - p.height / 2.0) / 135.0,
                0.0,
            ];
            let primitive = if n.source_kind == ManimSourceShapeKindV2::Circle {
                "Circle"
            } else {
                "Rectangle"
            };
            if actual.name != format!("mw_{}", n.node_id.simple())
                || actual.primitive != primitive
                || !point_near(&actual.center, center)
                || !near(actual.z_index, f64::from(n.z_index))
                || !actual.fill_hex.eq_ignore_ascii_case(&n.fill_hex)
                || !near(actual.fill_opacity, p.opacity)
                || !near(actual.stroke_width, 0.0)
                || !near(actual.stroke_opacity, 0.0)
                || !actual.width.is_finite()
                || !actual.height.is_finite()
            {
                return Err(profile_invalid("motion_native_object_mismatch"));
            }
            if primitive == "Rectangle" {
                let expected = projected_rectangle_vertices_v3(p);
                if actual.vertices.len() != 4
                    || !actual
                        .vertices
                        .iter()
                        .zip(expected)
                        .all(|(a, e)| point_near(a, e))
                    || !point_near(&actual.curve_start, expected[0])
                    || !point_near(&actual.curve_mid, expected[2])
                {
                    return Err(profile_invalid("motion_native_vertices_mismatch"));
                }
                let (s, c) = p.rotation_deg.to_radians().sin_cos();
                if !near(
                    actual.width,
                    (p.width * c.abs() + p.height * s.abs()) / 135.0,
                ) || !near(
                    actual.height,
                    (p.width * s.abs() + p.height * c.abs()) / 135.0,
                ) {
                    return Err(profile_invalid("motion_native_extent_mismatch"));
                }
            } else {
                let (s, c) = (-p.rotation_deg.to_radians()).sin_cos();
                let radius = p.width / 270.0;
                if !actual.vertices.is_empty()
                    || !point_near(
                        &actual.curve_start,
                        [center[0] + radius * c, center[1] + radius * s, 0.0],
                    )
                    || !point_near(
                        &actual.curve_mid,
                        [center[0] - radius * c, center[1] - radius * s, 0.0],
                    )
                    || !(1.99 * radius..=2.1 * radius).contains(&actual.width)
                    || !(1.99 * radius..=2.1 * radius).contains(&actual.height)
                {
                    return Err(profile_invalid("motion_native_circle_curve_mismatch"));
                }
            }
        }
    }
    Ok(r)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> ManimScenePlanV3 {
        ManimScenePlanV3 {
            schema_version: 3,
            profile_id: ManimMotionProfileIdV3::PlanarMotion,
            output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
            binding: ManimSceneBindingV2 {
                project_id: Uuid::from_u128(1),
                generation: Uuid::from_u128(2),
                revision: 7,
                scene_id: Uuid::from_u128(3),
                deliverable_id: Uuid::from_u128(4),
            },
            clock: ManimFrameClockV2::from_canonical(
                Rational::ZERO,
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
            camera: ManimPlanarCameraV3 {
                center_x: 960.0,
                center_y: 540.0,
                zoom: 1.0,
                rotation_deg: 0.0,
                safe_margin: 0.05,
            },
            nodes: vec![ManimPlanarNodeV3 {
                node_id: Uuid::from_u128(5),
                display_name: "Editable rectangle".into(),
                source_kind: ManimSourceShapeKindV2::Rectangle,
                canonical_ordinal: 0,
                z_index: 10,
                base: ManimPlanarPoseV3 {
                    x: 240.0,
                    y: 180.0,
                    width: 600.0,
                    height: 360.0,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                },
                fill_hex: "#E02040".into(),
                keyframes: vec![ManimMotionKeyframeV3 {
                    at: Rational::new(1, 1).unwrap(),
                    property: ManimMotionPropertyV3::X,
                    value: 600.0,
                    interpolation: ManimMotionInterpolationV3::Linear,
                }],
            }],
        }
    }

    #[test]
    fn rehashed_executable_source_is_refused_before_any_extraction() {
        let plan = fixture();
        let original = source_bundle_v3(&plan).unwrap();
        assert_eq!(inspect_source_bundle_v3(&original).unwrap().plan, plan);
        let mut package = source_package_v3(&plan).unwrap();
        package
            .entries
            .get_mut("scene.py")
            .unwrap()
            .extend_from_slice(b"\nraise RuntimeError('arbitrary imported code')\n");
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&package.entries["manifest.json"]).unwrap();
        manifest["source_sha256"] = serde_json::json!(source_digest(&package.entries["scene.py"]));
        for row in manifest["entries"].as_array_mut().unwrap() {
            let path = row["path"].as_str().unwrap().to_owned();
            row["bytes"] = serde_json::json!(package.entries[&path].len());
            row["sha256"] = serde_json::json!(source_digest(&package.entries[&path]));
        }
        package
            .entries
            .insert("manifest.json".into(), source_json(&manifest).unwrap());
        let mutant = encode_source_entries(&package.entries).unwrap();
        let error = inspect_source_bundle_v3(&mutant).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("motion_source_projection_mismatch")
        );
        assert_ne!(mutant, original);
    }

    #[test]
    fn closed_archive_rejects_path_traversal_unknown_plan_and_extra_entry() {
        for case in 0..3 {
            let mut package = source_package_v3(&fixture()).unwrap();
            match case {
                0 => {
                    let bytes = package.entries.remove("scene.py").unwrap();
                    package.entries.insert("../scene.py".into(), bytes);
                }
                1 => {
                    let mut plan: serde_json::Value =
                        serde_json::from_slice(&package.entries["plan.json"]).unwrap();
                    plan["python"] = serde_json::json!("arbitrary");
                    package
                        .entries
                        .insert("plan.json".into(), source_json(&plan).unwrap());
                }
                _ => {
                    package.entries.insert("unknown-asset.bin".into(), vec![1]);
                }
            }
            assert!(
                inspect_source_bundle_v3(&encode_source_entries(&package.entries).unwrap())
                    .is_err(),
                "case {case}"
            );
        }
    }

    #[test]
    fn source_generation_cannot_round_an_unrepresentable_key_interval_to_arrival() {
        let mut plan = fixture();
        // A key strictly after frame 1/2 can be so close that binary64 ratio
        // rounds to 1.0. Reject it; never report early arrival as exact easing.
        plan.nodes[0].keyframes[0].at =
            Rational::new(4_500_000_000_000_000_001, 9_000_000_000_000_000_000).unwrap();
        assert!(Rational::new(1, 2).unwrap() < plan.nodes[0].keyframes[0].at);
        let error = sample_frame_v3(&plan, 6).unwrap_err();
        assert!(error.to_string().contains("motion_sampler_interval"));
        assert!(source_package_v3(&plan).is_err());
    }

    #[test]
    fn inline_getter_budget_rejects_before_the_existing_transport_can_overflow() {
        let mut plan = fixture();
        plan.clock = ManimFrameClockV2::from_canonical(
            Rational::ZERO,
            Rational::new(127, 12).unwrap(),
            Rational::new(12, 1).unwrap(),
        )
        .unwrap();
        source_package_v3(&plan).unwrap();
        plan.clock = ManimFrameClockV2::from_canonical(
            Rational::ZERO,
            Rational::new(128, 12).unwrap(),
            Rational::new(12, 1).unwrap(),
        )
        .unwrap();
        assert!(
            validate_plan_v3(&plan)
                .unwrap_err()
                .to_string()
                .contains("motion_observation_budget")
        );
    }
}
