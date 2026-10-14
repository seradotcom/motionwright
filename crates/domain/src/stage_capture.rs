//! Bind a genuinely imported, content-addressed first-party screenshot into
//! the editable original ProductStage card region. This is not an arbitrary
//! image importer, a real customer-app capture, or permission to render private
//! assets. An attached screenshot is one **source tile**, never a flattened
//! replacement for the rest of the authored stage or a legal-rights guarantee.
use crate::*;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const STAGE_CAPTURE_CARD_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StageCaptureCard {
    pub stage_id: Uuid,
    pub capture_id: Uuid,
    pub version: u32,
}

impl StageCaptureCard {
    pub fn validate(&self, project: &Project) -> Result<()> {
        if self.version != STAGE_CAPTURE_CARD_VERSION {
            return Err(DomainError::Invalid(
                "Unsupported source capture card version".into(),
            ));
        }
        let stage = project
            .production_design
            .stages
            .iter()
            .find(|stage| stage.id == self.stage_id)
            .ok_or_else(|| {
                DomainError::Invalid(
                    "Source card requires an attached original ProductStage".into(),
                )
            })?;
        let capture = project
            .production_design
            .captures
            .iter()
            .find(|capture| capture.id == self.capture_id)
            .ok_or_else(|| {
                DomainError::Invalid(
                    "Source card requires an immutable, previously registered first-party capture"
                        .into(),
                )
            })?;
        capture.validate(project)?;
        if capture.source_project_id != project.id
            || capture.declared_scope != "first-party-synthetic-fixture-only"
            || capture.origin != "http://127.0.0.1:1420"
            || capture.viewport_width != 1440
            || capture.viewport_height != 940
            || capture.screenshot_sha256 != capture.screenshot_repeat_sha256
            || !project.assets.iter().any(|asset| {
                asset.id == capture.screenshot_asset_id
                    && asset.media_type == "image/png"
                    && asset.content_sha256.as_deref() == Some(capture.screenshot_sha256.as_str())
            })
        {
            return Err(DomainError::Invalid(
                "Source card cannot reference an unverified or external screenshot asset".into(),
            ));
        }
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == stage.scene_id)
            .ok_or_else(|| DomainError::Invalid("Source card stage scene is missing".into()))?;
        if scene.renderer != RendererKind::MotionCanvas
            || !scene.nodes.iter().any(|node| {
                node.id == stage_node_id(stage.id, "card")
                    && node.kind == "rectangle"
                    && node.width > 0.0
                    && node.height > 0.0
            })
        {
            return Err(DomainError::Invalid(
                "Source card requires its editable original Canvas card region".into(),
            ));
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn bind_stage_capture(&mut self, stage_id: Uuid, capture_id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let scene_id = self
            .production_design
            .stages
            .iter()
            .find(|stage| stage.id == stage_id)
            .ok_or_else(|| DomainError::NotFound(format!("ProductStage:{stage_id}")))?
            .scene_id;
        self.ensure_unlocked(
            &format!("scene:{scene_id}"),
            &[
                LockKind::Content,
                LockKind::Position,
                LockKind::Style,
                LockKind::Renderer,
            ],
        )?;
        let proposed = StageCaptureCard {
            stage_id,
            capture_id,
            version: STAGE_CAPTURE_CARD_VERSION,
        };
        proposed.validate(self)?;
        let mut next = self.clone();
        if let Some(existing) = next
            .production_design
            .stage_capture_cards
            .iter_mut()
            .find(|binding| binding.stage_id == stage_id)
        {
            *existing = proposed;
        } else {
            if next.production_design.stage_capture_cards.len() >= 16 {
                return Err(DomainError::Invalid(
                    "ProductStage source tile limit exceeded".into(),
                ));
            }
            next.production_design.stage_capture_cards.push(proposed);
        }
        next.scenes
            .iter_mut()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?
            .status = SceneStatus::Draft;
        next.validate()?;
        *self = next;
        Ok(())
    }
    pub(crate) fn detach_stage_capture(&mut self, stage_id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let scene_id = self
            .production_design
            .stages
            .iter()
            .find(|stage| stage.id == stage_id)
            .ok_or_else(|| DomainError::NotFound(format!("ProductStage:{stage_id}")))?
            .scene_id;
        self.ensure_unlocked(&format!("scene:{scene_id}"), &[LockKind::Content])?;
        let old = self.production_design.stage_capture_cards.len();
        self.production_design
            .stage_capture_cards
            .retain(|item| item.stage_id != stage_id);
        if old == self.production_design.stage_capture_cards.len() {
            return Err(DomainError::NotFound(format!(
                "StageCaptureCard:{stage_id}"
            )));
        }
        self.scenes
            .iter_mut()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?
            .status = SceneStatus::Draft;
        Ok(()) // Retain the 14 original editable Canvas nodes and source capture record.
    }
}

impl Project {
    pub(crate) fn validate_stage_capture_cards(&self) -> Result<()> {
        if self.production_design.stage_capture_cards.len() > 16 {
            return Err(DomainError::Invalid(
                "Too many first-party stage capture tiles".into(),
            ));
        }
        let mut ids = std::collections::BTreeSet::new();
        for card in &self.production_design.stage_capture_cards {
            if !ids.insert(card.stage_id) {
                return Err(DomainError::Invalid(
                    "Only one first-party source tile per ProductStage".into(),
                ));
            }
            card.validate(self)?;
        }
        Ok(())
    }
}
