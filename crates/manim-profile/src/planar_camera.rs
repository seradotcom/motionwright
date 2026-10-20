//! Versioned, source-only planar camera contribution for pinned Manim 0.21 Cairo.
//! The existing Manim compiler and original production coordinator remain the
//! sole realization and effect authorities; plans here never permit a job start.
use super::{
    MAX_SOURCE_ENTRY_BYTES_V2, ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2,
    ManimProfileError, ManimRenderProfile, ManimSceneBindingV2, ManimSourceShapeKindV2,
    ManimStaticShapeV2,
};
use semwright_media_time::Rational;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

pub const CAMERA_SCHEMA_V1: u16 = 4;
pub const CAMERA_PROFILE_V1: &str = "mw-manim-planar-camera-v1";
pub const CAMERA_VERSION_V2: u32 = 2;
pub const CANVAS_WIDTH: f64 = 1920.0;
pub const CANVAS_HEIGHT: f64 = 1080.0;
pub const PIXELS_PER_UNIT: f64 = 135.0;
pub const FRAME_WIDTH_UNITS: f64 = 128.0 / 9.0;
pub const FRAME_HEIGHT_UNITS: f64 = 8.0;
pub const EXPECTED_RATE: u32 = 30;
const MAX_CAMERA_NODES: usize = 32;
const MAX_CAMERA_FRAMES: u32 = 180;
const MIN_CAMERA_FRAMES: u16 = 12;
const FIXED_GETTER_BYTES: usize = 2048;
const PER_FRAME_GETTER_BYTES: usize = 1024;
const PER_OBJECT_FRAME_BYTES: usize = 1024;

type Checked<T> = std::result::Result<T, ManimProfileError>;

fn refuse(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

fn require(condition: bool, reason: &'static str) -> Checked<()> {
    if condition {
        Ok(())
    } else {
        Err(refuse(reason))
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum ManimCameraProfileIdV1 {
    #[serde(rename = "mw-manim-planar-camera-v1")]
    PlanarCamera,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CameraMoveV1 {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub component_version: u32,
    pub start_center_x: f64,
    pub start_center_y: f64,
    pub end_center_x: f64,
    pub end_center_y: f64,
    pub duration_frames: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimCameraPoseV1 {
    pub center_x: f64,
    pub center_y: f64,
    pub zoom: f64,
    pub rotation_deg: f64,
    pub safe_margin: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimCameraScenePlanV1 {
    pub schema_version: u16,
    pub profile_id: ManimCameraProfileIdV1,
    pub output_role: ManimOutputRoleV2,
    pub binding: ManimSceneBindingV2,
    pub clock: ManimFrameClockV2,
    pub render_profile: ManimRenderProfile,
    pub canvas: ManimCanvasV2,
    pub background: ManimMatteV2,
    pub camera: ManimCameraPoseV1,
    pub reframe: CameraMoveV1,
    pub nodes: Vec<ManimStaticShapeV2>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimCameraFrameV1 {
    pub frame: u32,
    pub pts: Rational,
    pub center_x: f64,
    pub center_y: f64,
    pub world_frame_center: [f64; 3],
    pub world_frame_width: f64,
    pub world_frame_height: f64,
}

fn pose_within_camera(
    node: &ManimStaticShapeV2,
    center_x: f64,
    center_y: f64,
    margin: f64,
) -> bool {
    let x = node.x_px + CANVAS_WIDTH / 2.0 - center_x;
    let y = node.y_px + CANVAS_HEIGHT / 2.0 - center_y;
    let margin_x = CANVAS_WIDTH * margin;
    let margin_y = CANVAS_HEIGHT * margin;
    x >= margin_x
        && y >= margin_y
        && x + node.width_px <= CANVAS_WIDTH - margin_x
        && y + node.height_px <= CANVAS_HEIGHT - margin_y
}

impl ManimCameraScenePlanV1 {
    pub fn validate_source_only(&self) -> Checked<()> {
        require(
            self.schema_version == CAMERA_SCHEMA_V1
                && self.profile_id == ManimCameraProfileIdV1::PlanarCamera,
            "manim_camera_schema_or_profile_mismatch",
        )?;
        require(
            self.output_role == ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
            "manim_camera_output_role_mismatch",
        )?;
        require(
            !self.binding.project_id.is_nil()
                && !self.binding.generation.is_nil()
                && self.binding.revision > 0
                && !self.binding.scene_id.is_nil()
                && !self.binding.deliverable_id.is_nil(),
            "manim_camera_binding_not_current",
        )?;
        self.clock
            .validate()
            .map_err(|_| refuse("manim_camera_clock_binding_invalid"))?;
        require(
            self.clock.frame_rate == Rational::new(30, 1).map_err(|_| refuse("camera_rate"))?
                && self.clock.local_start_frame == 0
                && (1..=MAX_CAMERA_FRAMES).contains(&self.clock.frame_count)
                && self.clock.project_end_frame_exclusive >= self.clock.project_start_frame
                && self.clock.project_end_frame_exclusive - self.clock.project_start_frame
                    == u64::from(self.clock.frame_count),
            "manim_camera_frame_clock_not_admitted",
        )?;
        require(
            self.render_profile.width == 640
                && self.render_profile.height == 360
                && self.render_profile.frame_rate == EXPECTED_RATE
                && self.canvas == ManimCanvasV2::default(),
            "manim_camera_output_geometry_not_admitted",
        )?;
        self.render_profile.validate()?;
        require(
            self.camera.zoom == 1.0
                && self.camera.rotation_deg == 0.0
                && self.camera.center_x == self.reframe.end_center_x
                && self.camera.center_y == self.reframe.end_center_y
                && self.camera.safe_margin.is_finite(),
            "manim_camera_terminal_pose_or_zoom_drift",
        )?;
        require(
            self.background == ManimMatteV2::default(),
            "manim_camera_source_matte_not_admitted",
        )?;
        require(
            !self.reframe.id.is_nil()
                && self.reframe.scene_id == self.binding.scene_id
                && self.reframe.component_version == CAMERA_VERSION_V2
                && (MIN_CAMERA_FRAMES..=MAX_CAMERA_FRAMES as u16)
                    .contains(&self.reframe.duration_frames)
                && u32::from(self.reframe.duration_frames) <= self.clock.frame_count,
            "manim_camera_reframe_binding_or_duration_invalid",
        )?;
        for value in [
            self.reframe.start_center_x,
            self.reframe.start_center_y,
            self.reframe.end_center_x,
            self.reframe.end_center_y,
        ] {
            require(value.is_finite(), "manim_camera_nonfinite_source")?;
        }
        for x in [self.reframe.start_center_x, self.reframe.end_center_x] {
            require(
                (720.0..=1200.0).contains(&x),
                "manim_camera_x_out_of_bounds",
            )?;
        }
        for y in [self.reframe.start_center_y, self.reframe.end_center_y] {
            require((320.0..=760.0).contains(&y), "manim_camera_y_out_of_bounds")?;
        }
        let dx = self.reframe.end_center_x - self.reframe.start_center_x;
        let dy = self.reframe.end_center_y - self.reframe.start_center_y;
        require(
            dx.abs() <= 80.0 && dy.abs() <= 80.0 && (dx.abs() >= 0.001 || dy.abs() >= 0.001),
            "manim_camera_displacement_invalid",
        )?;
        require(
            (1..=MAX_CAMERA_NODES).contains(&self.nodes.len()),
            "manim_camera_object_budget",
        )?;
        let mut ids = BTreeSet::new();
        let margin = self.camera.safe_margin.max(0.05);
        for (ordinal, node) in self.nodes.iter().enumerate() {
            require(
                !node.node_id.is_nil()
                    && ids.insert(node.node_id)
                    && node.canonical_ordinal as usize == ordinal
                    && !node.display_name.trim().is_empty()
                    && node.display_name.len() <= 160
                    && !node.display_name.contains('\0')
                    && matches!(
                        node.source_kind,
                        ManimSourceShapeKindV2::Rectangle | ManimSourceShapeKindV2::Circle
                    )
                    && node.x_px.is_finite()
                    && node.y_px.is_finite()
                    && node.width_px.is_finite()
                    && node.height_px.is_finite()
                    && node.width_px > 0.0
                    && node.height_px > 0.0
                    && node.fill_hex.starts_with('#')
                    && node.fill_hex.len() == 7
                    && node.fill_hex[1..].bytes().all(|b| b.is_ascii_hexdigit()),
                "manim_camera_nonflat_or_unsupported_object",
            )?;
            if matches!(node.source_kind, ManimSourceShapeKindV2::Circle) {
                require(
                    node.width_px == node.height_px,
                    "manim_camera_circle_not_square",
                )?;
            }
            require(
                pose_within_camera(
                    node,
                    self.reframe.start_center_x,
                    self.reframe.start_center_y,
                    margin,
                ) && pose_within_camera(
                    node,
                    self.reframe.end_center_x,
                    self.reframe.end_center_y,
                    margin,
                ),
                "manim_camera_original_object_crop",
            )?;
        }
        // The current driver returns getter rows through a finite inline
        // envelope. Never imply any combination of 180 frames x 32 shapes is
        // transportable solely because each individual count is admissible.
        let n = usize::try_from(self.clock.frame_count)
            .map_err(|_| refuse("manim_camera_frame_overflow"))?;
        let estimated = FIXED_GETTER_BYTES
            .checked_add(
                n.checked_mul(PER_FRAME_GETTER_BYTES)
                    .ok_or(refuse("manim_camera_readback_overflow"))?,
            )
            .and_then(|x| {
                n.checked_mul(self.nodes.len())
                    .and_then(|count| count.checked_mul(PER_OBJECT_FRAME_BYTES))
                    .and_then(|added| x.checked_add(added))
            })
            .ok_or(refuse("manim_camera_readback_overflow"))?;
        require(
            estimated <= MAX_SOURCE_ENTRY_BYTES_V2,
            "manim_camera_inline_getter_budget_exceeded",
        )?;
        Ok(())
    }

    pub fn sample(&self, frame: u32) -> Checked<ManimCameraFrameV1> {
        self.validate_source_only()?;
        require(
            frame < self.clock.frame_count,
            "manim_camera_sample_out_of_range",
        )?;
        let pts = Rational::new(i64::from(frame), 30)
            .map_err(|_| refuse("manim_camera_sample_pts_invalid"))?;
        let u = f64::from(frame.min(u32::from(self.reframe.duration_frames)))
            / f64::from(self.reframe.duration_frames);
        let cx = self.reframe.start_center_x
            + (self.reframe.end_center_x - self.reframe.start_center_x) * u;
        let cy = self.reframe.start_center_y
            + (self.reframe.end_center_y - self.reframe.start_center_y) * u;
        Ok(ManimCameraFrameV1 {
            frame,
            pts,
            center_x: cx,
            center_y: cy,
            world_frame_center: [
                (cx - 960.0) / PIXELS_PER_UNIT,
                (540.0 - cy) / PIXELS_PER_UNIT,
                0.0,
            ],
            world_frame_width: FRAME_WIDTH_UNITS,
            world_frame_height: FRAME_HEIGHT_UNITS,
        })
    }

    pub fn expected_frame_samples(&self) -> Checked<Vec<ManimCameraFrameV1>> {
        self.validate_source_only()?;
        (0..self.clock.frame_count)
            .map(|i| self.sample(i))
            .collect()
    }

    pub fn output_pixel_from_source_point(&self, x: f64, y: f64, frame: u32) -> Checked<[f64; 2]> {
        require(
            x.is_finite() && y.is_finite(),
            "manim_camera_world_point_nonfinite",
        )?;
        let at = self.sample(frame)?;
        Ok([
            (x + 960.0 - at.center_x) / 3.0,
            (y + 540.0 - at.center_y) / 3.0,
        ])
    }
}

/// Compile the versioned camera profile into inert source for the trusted Manim Host.
/// Schema validation grants no execution authority.
/// Never execute this program at import time, trust a ZIP as a grant, or dispatch
/// from this module. The existing coordinator owns every native effect.
pub const CAMERA_COMPILER_V1: &str = "motionwright.manim.rust.planar_camera.v1.0";

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PreparedCameraProgramV1 {
    pub compiler_id: &'static str,
    pub plan_sha256: String,
    pub source_sha256: String,
    pub plan_document: Vec<u8>,
    pub python_source: String,
    pub native_execution_performed: bool,
    pub runtime_sample_pts_verified: bool,
}

/// Exactly the same pretty-printed JSON+LF source-plan byte representation as
/// the existing Manim static-plan source compiler. All executable statements
/// are fixed trusted source; authored data can only enter through JSON strings.
pub fn compile_camera_program_source_only(
    plan: &ManimCameraScenePlanV1,
) -> Checked<PreparedCameraProgramV1> {
    use sha2::{Digest, Sha256};
    plan.validate_source_only()?;
    let mut plan_document =
        serde_json::to_vec_pretty(plan).map_err(|_| refuse("manim_camera_plan_json_encoding"))?;
    plan_document.push(b'\n');
    require(
        plan_document.len() <= MAX_SOURCE_ENTRY_BYTES_V2,
        "manim_camera_plan_source_budget",
    )?;
    let plan_sha256 = hex::encode(Sha256::digest(&plan_document));
    // Calculate expected sample centers from the saved typed source, not
    // from a user-provided Python expression or imported script.
    let frames = plan.expected_frame_samples()?;
    let payload = serde_json::json!({
        "plan_sha256": plan_sha256,
        "binding": plan.binding,
        "source_profile": CAMERA_PROFILE_V1,
        "render_profile": plan.render_profile,
        "nodes": plan.nodes,
        "frame_count": plan.clock.frame_count,
        "duration_frames": plan.reframe.duration_frames,
        "frames": frames,
        "terminal_world_frame_center": [
            (plan.reframe.end_center_x - 960.0) / PIXELS_PER_UNIT,
            (540.0 - plan.reframe.end_center_y) / PIXELS_PER_UNIT,
            0.0
        ],
    });
    let data = serde_json::to_string(&payload)
        .map_err(|_| refuse("manim_camera_source_payload_encoding"))?;
    // JSON literal with only quoted UTF-8 text; it contains no executable
    // expression. A malicious name remains inert data after json.loads.
    let quoted =
        serde_json::to_string(&data).map_err(|_| refuse("manim_camera_source_literal_encoding"))?;
    let python_source = format!(
        "# Generated by {CAMERA_COMPILER_V1}\n\
         # Only the original trusted compiler may construct this source.\n\
         # Imported scene.py is never executable source authority.\n\
         _DATA = {quoted}\n\
         {CAMERA_NATIVE_PROGRAM_V1}"
    );
    require(
        python_source.len() <= MAX_SOURCE_ENTRY_BYTES_V2,
        "manim_camera_program_source_budget",
    )?;
    let source_sha256 = hex::encode(Sha256::digest(python_source.as_bytes()));
    Ok(PreparedCameraProgramV1 {
        compiler_id: CAMERA_COMPILER_V1,
        plan_sha256,
        source_sha256,
        plan_document,
        python_source,
        native_execution_performed: false,
        runtime_sample_pts_verified: false,
    })
}

/// Fixed, bounded Manim Community 0.21.0 Cairo realization.
/// Manim V3's *existing* midpoint play sampler is reused unchanged:
/// one output sample is at i/fps (not at a frame midpoint); the final alpha=1
/// is a terminal editing pose, never an extra decoded frame. The reader must
/// independently verify all native camera/object getters and media PTS.
use super::{MAX_SOURCE_BUNDLE_BYTES_V2, ManimSourceClosureV1, ManimSourceEntryV1};
use std::collections::BTreeMap;

pub const CAMERA_SOURCE_MIME_V1: &str =
    "application/vnd.motionwright.manim-planar-camera-source+zip";

#[derive(Debug, Clone, PartialEq)]
pub struct ManimCameraSourcePackageV1 {
    pub plan: ManimCameraScenePlanV1,
    pub entries: BTreeMap<String, Vec<u8>>,
    pub closure: ManimSourceClosureV1,
}

fn camera_source_rows(entries: &BTreeMap<String, Vec<u8>>) -> Vec<ManimSourceEntryV1> {
    entries
        .iter()
        .map(|(path, bytes)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: bytes.len() as u64,
            sha256: super::source_digest(bytes),
        })
        .collect()
}

/// Preserve the admitted typed camera source and its fixed generated program.
/// This package is inert data, not a grant or evidence of native execution.
pub fn camera_source_package_v1(
    plan: &ManimCameraScenePlanV1,
) -> Checked<ManimCameraSourcePackageV1> {
    let prepared = compile_camera_program_source_only(plan)?;
    let config = super::source_json(&serde_json::json!({
        "schema_version": 1,
        "profile_id": CAMERA_PROFILE_V1,
        "compiler_id": CAMERA_COMPILER_V1,
        "runtime": {"family": "manim-community", "version": "0.21.0", "renderer": "cairo"},
        "output_role": plan.output_role,
        "render_profile": plan.render_profile,
        "clock": plan.clock,
        "canvas": plan.canvas,
        "background": plan.background,
        "native_execution_performed": false,
        "runtime_sample_pts_verified": false
    }))?;
    let assets_lock = super::source_json(&serde_json::json!({
        "schema_version": 1, "profile_id": CAMERA_PROFILE_V1, "assets": [],
        "external_asset_usage": "none", "external_rights_certified": false
    }))?;
    let plan_sha256 = prepared.plan_sha256;
    let source_sha256 = prepared.source_sha256;
    let config_sha256 = super::source_digest(&config);
    let assets_lock_sha256 = super::source_digest(&assets_lock);
    let mut entries = BTreeMap::from([
        ("plan.json".into(), prepared.plan_document),
        ("scene.py".into(), prepared.python_source.into_bytes()),
        ("render-config.json".into(), config),
        ("assets.lock.json".into(), assets_lock),
    ]);
    let manifest = super::source_json(&serde_json::json!({
        "schema_version": 1,
        "bundle_kind": "motionwright.manim.planar_camera.editable_source",
        "mime_type": CAMERA_SOURCE_MIME_V1,
        "profile_id": CAMERA_PROFILE_V1,
        "plan_schema_version": CAMERA_SCHEMA_V1,
        "compiler_id": CAMERA_COMPILER_V1,
        "encoding_id": "serde-json-pretty-rational-strings-zip9-stored-v1",
        "plan_sha256": plan_sha256, "source_sha256": source_sha256,
        "config_sha256": config_sha256, "assets_lock_sha256": assets_lock_sha256,
        "entries": camera_source_rows(&entries),
        "typed_source_controls": [
            "reframe.start_center_x", "reframe.start_center_y",
            "reframe.end_center_x", "reframe.end_center_y",
            "reframe.duration_frames", "camera.safe_margin"
        ],
        "canonical_import_controls": ["reframe.end_center_x"],
        "derived_controls": {
            "camera.center_x": "reframe.end_center_x",
            "camera.center_y": "reframe.end_center_y"
        },
        "limitations": [
            "The admitted camera moves linearly between its typed centers.",
            "Zoom is fixed at 1 and rotation at 0 degrees.",
            "Only the validated flat rectangle and circle subset is admitted.",
            "Output is an opaque scene contribution without audio or captions.",
            "Imported Python is preserved as source bytes, never executed.",
            "Native rendering and reopening require independent receipts."
        ],
        "native_render_verified": false, "native_reopen_verified": false,
        "canonical_import_applied": false
    }))?;
    let manifest_sha256 = super::source_digest(&manifest);
    entries.insert("manifest.json".into(), manifest);
    let closure = ManimSourceClosureV1 {
        plan_schema_version: CAMERA_SCHEMA_V1,
        compiler_contract_id: CAMERA_COMPILER_V1.into(),
        plan_sha256,
        source_sha256,
        config_sha256,
        assets_lock_sha256,
        manifest_sha256,
        entries: camera_source_rows(&entries),
    };
    super::encode_source_entries(&entries)?;
    Ok(ManimCameraSourcePackageV1 {
        plan: plan.clone(),
        entries,
        closure,
    })
}

pub fn camera_source_bundle_v1(plan: &ManimCameraScenePlanV1) -> Checked<Vec<u8>> {
    super::encode_source_entries(&camera_source_package_v1(plan)?.entries)
}

/// Inspect a bounded stored archive without extraction or execution.
/// Only the typed plan is read. Every other byte must equal its trusted,
/// deterministic regeneration, including archive metadata and entry order.
pub fn inspect_camera_source_bundle_v1(bytes: &[u8]) -> Checked<ManimCameraSourcePackageV1> {
    use std::io::{Cursor, Read};
    require(
        !bytes.is_empty() && bytes.len() <= MAX_SOURCE_BUNDLE_BYTES_V2,
        "manim_camera_source_bundle_budget",
    )?;
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| refuse("manim_camera_source_zip_invalid"))?;
    require(
        archive.len() == 5 && archive.comment().is_empty(),
        "manim_camera_source_zip_shape",
    )?;
    let allowed = [
        "assets.lock.json",
        "manifest.json",
        "plan.json",
        "render-config.json",
        "scene.py",
    ];
    let mut names = BTreeSet::new();
    let mut plan_document = None;
    let mut total = 0_usize;
    for index in 0..archive.len() {
        let mut file = archive
            .by_index(index)
            .map_err(|_| refuse("manim_camera_source_entry_invalid"))?;
        let name = file
            .name()
            .map_err(|_| refuse("manim_camera_source_entry_name_invalid"))?
            .into_owned();
        require(
            allowed.contains(&name.as_str()) && names.insert(name.clone()),
            "manim_camera_source_entry_unknown_or_duplicate",
        )?;
        require(
            file.is_file()
                && !file.is_symlink()
                && !file.encrypted()
                && file.compression() == zip::CompressionMethod::Stored
                && file.comment().is_empty()
                && file.size() > 0
                && file.size() <= MAX_SOURCE_ENTRY_BYTES_V2 as u64,
            "manim_camera_source_entry_not_admitted",
        )?;
        total = total
            .checked_add(file.size() as usize)
            .ok_or_else(|| refuse("manim_camera_source_bundle_budget"))?;
        require(
            total <= MAX_SOURCE_BUNDLE_BYTES_V2,
            "manim_camera_source_bundle_budget",
        )?;
        if name == "plan.json" {
            let expected_size = file.size();
            let mut document = Vec::new();
            file.by_ref()
                .take(MAX_SOURCE_ENTRY_BYTES_V2 as u64 + 1)
                .read_to_end(&mut document)
                .map_err(|_| refuse("manim_camera_source_plan_read_failed"))?;
            require(
                document.len() <= MAX_SOURCE_ENTRY_BYTES_V2
                    && document.len() as u64 == expected_size,
                "manim_camera_source_plan_size_mismatch",
            )?;
            plan_document = Some(document);
        }
    }
    let document = plan_document.ok_or_else(|| refuse("manim_camera_source_plan_missing"))?;
    let plan: ManimCameraScenePlanV1 = serde_json::from_slice(&document)
        .map_err(|_| refuse("manim_camera_source_plan_invalid"))?;
    let package = camera_source_package_v1(&plan)?;
    let canonical = super::encode_source_entries(&package.entries)?;
    require(
        canonical.as_slice() == bytes,
        "manim_camera_source_bundle_not_canonical",
    )?;
    Ok(package)
}

/// Rebind preserved source only when the current typed content is identical.
/// The caller independently establishes current Project authority. This
/// function never applies edits or grants permission.
pub fn rebind_camera_source_v1(
    origin: &ManimCameraSourcePackageV1,
    current: &ManimCameraScenePlanV1,
) -> Checked<ManimCameraSourcePackageV1> {
    let canonical_origin = camera_source_package_v1(&origin.plan)?;
    require(
        origin.entries == canonical_origin.entries && origin.closure == canonical_origin.closure,
        "manim_camera_source_origin_not_canonical",
    )?;
    current.validate_source_only()?;
    let mut rebound = origin.plan.clone();
    rebound.binding.generation = current.binding.generation;
    rebound.binding.revision = current.binding.revision;
    // Numeric equality alone collapses signed zero, while the source and its
    // closure retain the exact serialized representation of every control.
    require(
        super::source_json(&rebound)? == super::source_json(current)?,
        "manim_camera_source_rebind_content_or_identity_drift",
    )?;
    camera_source_package_v1(&rebound)
}

const CAMERA_NATIVE_PROGRAM_V1: &str = r###"
from manim import Circle, Rectangle, MovingCameraScene, UpdateFromAlphaFunc, VGroup, config, linear
from manim import __version__ as _manim_version
import json
import math
from pathlib import Path

_D = json.loads(_DATA)
_PROFILE = _D["render_profile"]
if _manim_version != "0.21.0":
    raise ValueError("manim_camera_version_mismatch")
if (config.pixel_width, config.pixel_height, config.frame_rate) != (
    _PROFILE["width"], _PROFILE["height"], _PROFILE["frame_rate"]
):
    raise ValueError("manim_camera_profile_mismatch")
if (_PROFILE["width"], _PROFILE["height"], _PROFILE["frame_rate"]) != (640, 360, 30):
    raise ValueError("manim_camera_unadmitted_output_profile")
config.frame_width = 128.0 / 9.0
config.frame_height = 8.0
config.background_color = "#000000"
config.background_opacity = 1.0

class MotionwrightScene(MovingCameraScene):
    def construct(self):
        if self.camera.frame_rate != 30:
            raise ValueError("manim_camera_native_rate_mismatch")
        self.camera.frame.set(width=128.0 / 9.0, height=8.0)
        self.camera.frame.move_to(_D["frames"][0]["world_frame_center"])
        order = sorted(_D["nodes"], key=lambda n: (n["z_index"], n["canonical_ordinal"]))
        objects = []
        for node in order:
            name = "mw_" + node["node_id"].replace("-", "")
            if node["source_kind"] == "circle":
                item = Circle(radius=node["width_px"] / 270.0, name=name)
            elif node["source_kind"] == "rectangle":
                item = Rectangle(
                    width=node["width_px"] / 135.0,
                    height=node["height_px"] / 135.0,
                    name=name,
                )
            else:
                raise ValueError("manim_camera_unknown_shape")
            item.move_to([
                (node["x_px"] + node["width_px"] / 2.0 - 960.0) / 135.0,
                (540.0 - node["y_px"] - node["height_px"] / 2.0) / 135.0,
                0.0,
            ])
            item.set_fill(node["fill_hex"], opacity=1.0)
            item.set_stroke(width=0.0, opacity=0.0)
            item.set_z_index(node["z_index"])
            objects.append((node, item))
        moving = VGroup(*(item for _, item in objects))
        self.add(moving)
        observed = []
        alphas = []
        frames = _D["frames"]
        count = _D["frame_count"]

        def camera_getters():
            return {
                "frame_center": [float(x) for x in self.camera.frame_center],
                "frame_width": float(self.camera.frame_width),
                "frame_height": float(self.camera.frame_height),
                "pixel_width": int(self.camera.pixel_width),
                "pixel_height": int(self.camera.pixel_height),
                "frame_rate": float(self.camera.frame_rate),
            }

        def object_getters(node, item):
            return {
                "node_id": node["node_id"],
                "kind": node["source_kind"],
                "name": item.name,
                "world_center": [float(x) for x in item.get_center()],
                "width": float(item.width),
                "height": float(item.height),
                "z_index": int(item.z_index),
                "fill_hex": item.get_fill_color().to_hex(),
                "fill_opacity": float(item.get_fill_opacity()),
                "stroke_width": float(item.get_stroke_width()),
                "stroke_opacity": float(item.get_stroke_opacity()),
            }

        def update(_group, alpha):
            if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
                raise ValueError("manim_camera_callback_alpha_invalid")
            if alpha == 1.0:
                self.camera.frame.move_to(_D["terminal_world_frame_center"])
                return
            coordinate = alpha * (count - 0.5)
            index = round(coordinate)
            if abs(coordinate - index) > 0.000001 or not 0 <= index < count:
                raise ValueError("manim_camera_callback_off_frame_grid")
            frame = frames[index]
            self.camera.frame.move_to(frame["world_frame_center"])
            row = {
                "index": index,
                "camera": camera_getters(),
                "objects": [object_getters(node, item) for node, item in objects],
            }
            if len(observed) == index:
                observed.append(row)
                alphas.append(float(alpha))
            elif index == 0 and len(observed) == 1 and observed[0] == row:
                pass  # Manim Animation.begin may observe alpha=0 twice.
            else:
                raise ValueError("manim_camera_native_sample_sequence")

        seconds = (count - 0.5) / 30.0
        self.play(UpdateFromAlphaFunc(moving, update, run_time=seconds, rate_func=linear))
        if len(observed) != count:
            raise ValueError("manim_camera_native_samples_missing")
        record = {
            "schema_version": 1,
            "profile_id": "mw-manim-planar-camera-v1",
            "scene_class": self.__class__.__name__,
            "plan_sha256": _D["plan_sha256"],
            "binding": _D["binding"],
            "sample_count": len(observed),
            "callback_alphas": alphas,
            "frames": observed,
            "decoded_video_verified": False,
            "native_grants_verified_by_this_script": False,
        }
        encoded = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if not 0 < len(encoded) <= 8388608:
            raise ValueError("manim_camera_native_getter_budget")
        with Path(__file__).with_suffix(".camera.json").open("xb") as target:
            target.write(encoded)
"###;

#[cfg(test)]
mod source_contract_tests {
    use super::*;

    fn fixture() -> ManimCameraScenePlanV1 {
        let scene = Uuid::from_u128(0x20000000000040008000000000000001);
        let nodes = [
            ("rectangle", 420.0, 270.0, 270.0, 180.0, "#E53935"),
            ("circle", 1125.0, 540.0, 180.0, 180.0, "#2BBBAD"),
        ]
        .iter()
        .enumerate()
        .map(
            |(index, (kind, x, y, width, height, color))| ManimStaticShapeV2 {
                node_id: Uuid::from_u128(0x40000000000040008000000000000001 + index as u128),
                display_name: format!("test-shape-{index}"),
                source_kind: match *kind {
                    "rectangle" => ManimSourceShapeKindV2::Rectangle,
                    _ => ManimSourceShapeKindV2::Circle,
                },
                canonical_ordinal: index as u32,
                z_index: index as i32,
                x_px: *x,
                y_px: *y,
                width_px: *width,
                height_px: *height,
                fill_hex: (*color).into(),
            },
        )
        .collect();
        ManimCameraScenePlanV1 {
            schema_version: CAMERA_SCHEMA_V1,
            profile_id: ManimCameraProfileIdV1::PlanarCamera,
            output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
            binding: ManimSceneBindingV2 {
                project_id: Uuid::from_u128(0x10000000000040008000000000000001),
                generation: Uuid::from_u128(0x10000000000040008000000000000002),
                revision: 17,
                scene_id: scene,
                deliverable_id: Uuid::from_u128(0x10000000000040008000000000000003),
            },
            clock: ManimFrameClockV2::from_canonical(
                Rational::new(0, 1).unwrap(),
                Rational::new(1, 1).unwrap(),
                Rational::new(30, 1).unwrap(),
            )
            .unwrap(),
            render_profile: ManimRenderProfile {
                width: 640,
                height: 360,
                frame_rate: 30,
            },
            canvas: ManimCanvasV2::default(),
            background: ManimMatteV2::default(),
            camera: ManimCameraPoseV1 {
                center_x: 1000.0,
                center_y: 520.0,
                zoom: 1.0,
                rotation_deg: 0.0,
                safe_margin: 0.05,
            },
            reframe: CameraMoveV1 {
                id: Uuid::from_u128(0x30000000000040008000000000000001),
                scene_id: scene,
                component_version: CAMERA_VERSION_V2,
                start_center_x: 960.0,
                start_center_y: 540.0,
                end_center_x: 1000.0,
                end_center_y: 520.0,
                duration_frames: 24,
            },
            nodes,
        }
    }

    #[test]
    fn source_clock_contains_every_frame_and_exact_24_to_29_hold() {
        let plan = fixture();
        let samples = plan.expected_frame_samples().unwrap();
        assert_eq!(samples.len(), 30);
        assert_eq!(samples[0].pts, Rational::new(0, 30).unwrap());
        assert_eq!(samples[12].pts, Rational::new(12, 30).unwrap());
        assert_eq!((samples[0].center_x, samples[0].center_y), (960.0, 540.0));
        assert_eq!((samples[12].center_x, samples[12].center_y), (980.0, 530.0));
        for (index, row) in samples.iter().enumerate() {
            assert_eq!(row.frame as usize, index);
            if index >= 24 {
                assert_eq!((row.center_x, row.center_y), (1000.0, 520.0));
            }
        }
        assert_eq!(plan.nodes[0].x_px, 420.0);
        assert_eq!(plan.nodes[1].x_px, 1125.0);
        assert!(plan.sample(30).is_err());
    }

    #[test]
    fn source_compiler_is_fixed_and_hash_bound_across_edit_and_untrusted_names() {
        use sha2::{Digest, Sha256};
        let input = fixture();
        let prepared = compile_camera_program_source_only(&input).unwrap();
        assert_eq!(
            prepared,
            compile_camera_program_source_only(&input).unwrap()
        );
        assert_eq!(prepared.compiler_id, CAMERA_COMPILER_V1);
        assert!(!prepared.native_execution_performed);
        assert!(!prepared.runtime_sample_pts_verified);
        assert!(prepared.plan_document.ends_with(b"\n"));
        assert_eq!(
            serde_json::from_slice::<ManimCameraScenePlanV1>(&prepared.plan_document).unwrap(),
            input
        );
        assert_eq!(
            prepared.plan_sha256,
            hex::encode(Sha256::digest(&prepared.plan_document))
        );
        assert_eq!(
            prepared.source_sha256,
            hex::encode(Sha256::digest(prepared.python_source.as_bytes()))
        );
        let trusted_token = "from manim import Circle, Rectangle, MovingCameraScene";
        assert!(prepared.python_source.contains(trusted_token));
        assert!(
            prepared
                .python_source
                .contains("self.play(UpdateFromAlphaFunc(")
        );
        let program_body = prepared.python_source.split_once(trusted_token).unwrap().1;
        let mut injection = input.clone();
        injection.nodes[0].display_name = "__import__('os').system('bad')".into();
        let inert = compile_camera_program_source_only(&injection).unwrap();
        assert_eq!(
            program_body,
            inert.python_source.split_once(trusted_token).unwrap().1,
            "authored bytes must not change executable program body"
        );
        assert_ne!(prepared.plan_sha256, inert.plan_sha256);
        let mut edited = input;
        edited.reframe.end_center_x = 1012.0;
        edited.camera.center_x = 1012.0;
        edited.binding.revision += 1;
        let next = compile_camera_program_source_only(&edited).unwrap();
        assert_ne!(prepared.plan_sha256, next.plan_sha256);
        assert_ne!(prepared.source_sha256, next.source_sha256);
        assert_eq!(
            program_body,
            next.python_source.split_once(trusted_token).unwrap().1
        );
    }

    #[test]
    fn compiler_refuses_unadmitted_controls_without_mutations_or_dispatch() {
        let original = fixture();
        assert!(original.validate_source_only().is_ok());
        let mut bad = original.clone();
        bad.reframe.component_version = 1;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = original.clone();
        bad.render_profile.frame_rate = 60;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = original.clone();
        bad.camera.center_x = 999.0;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = original.clone();
        bad.nodes[0].fill_hex = "#NOTHEX".into();
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = original.clone();
        bad.nodes[0].display_name = "X".repeat(161);
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut bad = original;
        bad.clock.frame_count += 1;
        assert!(compile_camera_program_source_only(&bad).is_err());
        let mut value = serde_json::to_value(&bad).unwrap();
        value["clock"]["frame_count"] = serde_json::json!(true);
        assert!(serde_json::from_value::<ManimCameraScenePlanV1>(value).is_err());
    }

    #[test]
    fn camera_source_bundle_roundtrip_preserves_five_canonical_entries() {
        let plan = fixture();
        let compiled = compile_camera_program_source_only(&plan).unwrap();
        let source = camera_source_package_v1(&plan).unwrap();
        let bytes = camera_source_bundle_v1(&plan).unwrap();
        assert_eq!(
            source
                .entries
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            [
                "assets.lock.json",
                "manifest.json",
                "plan.json",
                "render-config.json",
                "scene.py",
            ]
        );
        assert_eq!(source.closure.entries.len(), 5);
        assert_eq!(source.closure.plan_sha256, compiled.plan_sha256);
        assert_eq!(source.closure.source_sha256, compiled.source_sha256);
        assert_eq!(source.entries["plan.json"], compiled.plan_document);
        assert_eq!(
            source.entries["scene.py"],
            compiled.python_source.as_bytes()
        );
        assert_eq!(inspect_camera_source_bundle_v1(&bytes).unwrap(), source);
        assert_eq!(camera_source_bundle_v1(&plan).unwrap(), bytes);
        let manifest: serde_json::Value =
            serde_json::from_slice(&source.entries["manifest.json"]).unwrap();
        assert_eq!(manifest["native_render_verified"], false);
        assert_eq!(manifest["native_reopen_verified"], false);
        assert_eq!(manifest["canonical_import_applied"], false);
    }

    #[test]
    fn camera_source_bundle_preserves_semantics_and_inert_names() {
        let mut plan = fixture();
        plan.nodes[0].display_name = "\"; __import__('os').system('not-executable') #".into();
        let source = camera_source_package_v1(&plan).unwrap();
        let reopened =
            inspect_camera_source_bundle_v1(&camera_source_bundle_v1(&plan).unwrap()).unwrap();
        assert_eq!(reopened.plan, plan);
        assert_eq!(
            reopened.plan.expected_frame_samples().unwrap(),
            plan.expected_frame_samples().unwrap()
        );
        let generated = String::from_utf8(source.entries["scene.py"].clone()).unwrap();
        assert!(generated.ends_with(CAMERA_NATIVE_PROGRAM_V1));
        assert_eq!(
            generated,
            compile_camera_program_source_only(&plan)
                .unwrap()
                .python_source
        );
    }

    #[test]
    fn camera_source_bundle_rejects_alteration_of_every_entry() {
        let source = camera_source_package_v1(&fixture()).unwrap();
        for name in source.entries.keys() {
            let mut altered = source.entries.clone();
            altered.get_mut(name).unwrap().push(b'\n');
            let bytes = super::super::encode_source_entries(&altered).unwrap();
            assert!(
                inspect_camera_source_bundle_v1(&bytes).is_err(),
                "altered entry: {name}"
            );
        }
        let mut altered = source.entries.clone();
        altered.insert(
            "scene.py".into(),
            b"raise RuntimeError('untrusted imported Python')\n".to_vec(),
        );
        let bytes = super::super::encode_source_entries(&altered).unwrap();
        assert!(inspect_camera_source_bundle_v1(&bytes).is_err());
    }

    fn replace_camera_zip_test_name(bytes: &mut [u8], old: &[u8], new: &[u8]) {
        assert_eq!(old.len(), new.len());
        let mut replaced = 0;
        for index in 0..=bytes.len() - old.len() {
            if &bytes[index..index + old.len()] == old {
                bytes[index..index + old.len()].copy_from_slice(new);
                replaced += 1;
            }
        }
        assert_eq!(replaced, 2);
    }

    #[test]
    fn camera_source_bundle_rejects_duplicate_traversal_extra_and_trailing() {
        let plan = fixture();
        let source = camera_source_package_v1(&plan).unwrap();
        let mut renamed = source.entries.clone();
        renamed.remove("scene.py");
        renamed.insert("copy.json".into(), source.entries["plan.json"].clone());
        let original = super::super::encode_source_entries(&renamed).unwrap();
        let mut duplicate = original.clone();
        replace_camera_zip_test_name(&mut duplicate, b"copy.json", b"plan.json");
        assert!(inspect_camera_source_bundle_v1(&duplicate).is_err());
        let mut traversal = original;
        replace_camera_zip_test_name(&mut traversal, b"copy.json", b"../x.json");
        assert!(inspect_camera_source_bundle_v1(&traversal).is_err());
        let mut extra = source.entries.clone();
        extra.insert("extra.json".into(), b"{}\n".to_vec());
        assert!(
            inspect_camera_source_bundle_v1(&super::super::encode_source_entries(&extra).unwrap())
                .is_err()
        );
        let mut trailing = camera_source_bundle_v1(&plan).unwrap();
        assert!(inspect_camera_source_bundle_v1(&trailing[..trailing.len() - 16]).is_err());
        trailing.extend_from_slice(b"\ntrailing-data");
        assert!(inspect_camera_source_bundle_v1(&trailing).is_err());
    }

    #[test]
    fn camera_source_bundle_rejects_unsupported_schema_and_budget() {
        let source = camera_source_package_v1(&fixture()).unwrap();
        let mut entries = source.entries.clone();
        let mut value: serde_json::Value = serde_json::from_slice(&entries["plan.json"]).unwrap();
        value["schema_version"] = serde_json::json!(CAMERA_SCHEMA_V1 + 1);
        entries.insert(
            "plan.json".into(),
            super::super::source_json(&value).unwrap(),
        );
        let bytes = super::super::encode_source_entries(&entries).unwrap();
        assert!(inspect_camera_source_bundle_v1(&bytes).is_err());
        assert!(inspect_camera_source_bundle_v1(&[]).is_err());
        assert!(inspect_camera_source_bundle_v1(&vec![0; MAX_SOURCE_BUNDLE_BYTES_V2 + 1]).is_err());
    }

    #[test]
    fn camera_source_rebind_only_changes_generation_and_revision() {
        let origin = camera_source_package_v1(&fixture()).unwrap();
        let before = origin.clone();
        let mut current = origin.plan.clone();
        current.binding.generation = Uuid::from_u128(0x90000000000040008000000000000001);
        current.binding.revision += 1;
        let rebound = rebind_camera_source_v1(&origin, &current).unwrap();
        assert_eq!(rebound.plan, current);
        assert_eq!(origin, before);
        assert_ne!(rebound.closure.plan_sha256, origin.closure.plan_sha256);
        assert_eq!(
            inspect_camera_source_bundle_v1(&camera_source_bundle_v1(&current).unwrap()).unwrap(),
            rebound
        );
        let mut foreign = current.clone();
        foreign.binding.project_id = Uuid::from_u128(0x90000000000040008000000000000002);
        assert!(rebind_camera_source_v1(&origin, &foreign).is_err());
        let mut foreign = current.clone();
        foreign.binding.deliverable_id = Uuid::from_u128(0x90000000000040008000000000000003);
        assert!(rebind_camera_source_v1(&origin, &foreign).is_err());
        let mut foreign = current.clone();
        foreign.binding.scene_id = Uuid::from_u128(0x90000000000040008000000000000004);
        foreign.reframe.scene_id = foreign.binding.scene_id;
        assert!(rebind_camera_source_v1(&origin, &foreign).is_err());
        let mut drift = current;
        drift.reframe.end_center_x = 1012.0;
        drift.camera.center_x = 1012.0;
        assert!(drift.validate_source_only().is_ok());
        assert!(rebind_camera_source_v1(&origin, &drift).is_err());
        let mut tampered = origin.clone();
        tampered.entries.get_mut("scene.py").unwrap().push(b'\n');
        assert!(rebind_camera_source_v1(&tampered, &origin.plan).is_err());
        let mut tampered = origin.clone();
        tampered.closure.source_sha256 = "0".repeat(64);
        assert!(rebind_camera_source_v1(&tampered, &origin.plan).is_err());
    }

    #[test]
    fn camera_source_rebind_rejects_signed_zero_representation_drift() {
        for (original_zero, changed_zero) in [(-0.0, 0.0), (0.0, -0.0)] {
            let mut authored = fixture();
            authored.camera.rotation_deg = original_zero;
            let origin = camera_source_package_v1(&authored).unwrap();
            let mut current = authored;
            current.binding.revision += 1;
            let rebound = rebind_camera_source_v1(&origin, &current).unwrap();
            assert_eq!(
                rebound.closure.plan_sha256,
                compile_camera_program_source_only(&current)
                    .unwrap()
                    .plan_sha256
            );

            current.camera.rotation_deg = changed_zero;
            assert!(current.validate_source_only().is_ok());
            assert_eq!(origin.plan.camera.rotation_deg, current.camera.rotation_deg);
            assert_ne!(
                rebound.closure.plan_sha256,
                compile_camera_program_source_only(&current)
                    .unwrap()
                    .plan_sha256
            );
            assert!(rebind_camera_source_v1(&origin, &current).is_err());
        }
    }

    #[test]
    fn camera_source_edit_creates_new_source_and_preserves_original_bytes() {
        let plan = fixture();
        let original_bytes = camera_source_bundle_v1(&plan).unwrap();
        let original = inspect_camera_source_bundle_v1(&original_bytes).unwrap();
        let mut edited = original.plan.clone();
        edited.reframe.end_center_x = 1012.0;
        edited.camera.center_x = 1012.0;
        edited.binding.revision += 1;
        let edited_bytes = camera_source_bundle_v1(&edited).unwrap();
        let reopened = inspect_camera_source_bundle_v1(&edited_bytes).unwrap();
        assert_eq!(reopened.plan, edited);
        assert_eq!(reopened.plan.nodes, plan.nodes);
        assert_ne!(edited_bytes, original_bytes);
        assert_ne!(reopened.closure.plan_sha256, original.closure.plan_sha256);
        assert_ne!(
            reopened.closure.source_sha256,
            original.closure.source_sha256
        );
        assert_eq!(
            inspect_camera_source_bundle_v1(&original_bytes).unwrap(),
            original
        );
        assert_eq!(
            camera_source_bundle_v1(&original.plan).unwrap(),
            original_bytes
        );
    }
}
