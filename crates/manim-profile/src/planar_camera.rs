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
