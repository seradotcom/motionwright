//! MW05-E07-03: bounded authored planar camera reframe on canonical Canvas.
//!
//! This is a semantic 2D camera translation projected through the existing
//! Semwright Film Settle primitive. It is NOT a 3D orbit, focal-length change,
//! dolly, optical zoom, perspective reconstruction, or simulated device shot.
//! Both source and destination camera boxes must preserve every authored
//! native Canvas object inside the output-safe 1920x1080 source bounds.
use crate::*;
use std::collections::BTreeSet;
use uuid::Uuid;

pub const CAMERA_REFRAME_VERSION: u32 = 1;
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
}
impl CameraReframe {
    pub fn validate(&self, scene: &Scene) -> Result<()> {
        if self.component_version != CAMERA_REFRAME_VERSION
            || scene.id != self.scene_id
            || scene.renderer != RendererKind::MotionCanvas
            || scene.nodes.is_empty()
            || scene.nodes.len() > 32
            || !scene.beats.is_empty()
            || scene.nodes.iter().any(|node| {
                !node.keyframes.is_empty() || node.parent_id.is_some() || node.kind == "group"
            })
        {
            return Err(DomainError::Invalid(
                "CameraReframe requires one ungrouped, motion-free original Motion Canvas shot of at most 32 objects".into(),
            ));
        }
        if scene.camera.zoom != 1.0 || scene.camera.rotation_deg != 0.0 {
            return Err(DomainError::Invalid(
                "Planar camera reframe cannot replace unsupported optical zoom or rotation".into(),
            ));
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
        for node in &scene.nodes {
            for (center_x, center_y) in [
                (self.start_center_x, self.start_center_y),
                (self.end_center_x, self.end_center_y),
            ] {
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
    pub(crate) fn validate_camera_reframes(&self, scenes: &[Scene]) -> Result<()> {
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
        }
        Ok(())
    }
}
