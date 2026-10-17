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
        original_motion.validate(&manim.scenes[0]).unwrap();
    }

    #[test]
    fn manim_v2_rejects_unrepresented_visual_state_without_mutation() {
        let (project, scene_id) = reference_project();
        let cases: Vec<Box<dyn Fn(&mut Project)>> = vec![
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
