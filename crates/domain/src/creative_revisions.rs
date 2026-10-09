//! Bounded reversible creative edits. Undo is a new ordinary CAS revision, not a rewind.
use crate::*;

pub const MAX_CREATIVE_PATCH_RECORDS: usize = 64;
const MAX_PATCH_RECORD_BYTES: usize = 256 * 1024;
const MAX_PATCH_HISTORY_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativePatchRecord {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub source_revision: u64,
    pub rationale: String,
    pub before: Vec<CanvasNode>,
    pub after: Vec<CanvasNode>,
    /// Audit link only. The predecessor may have aged out of the bounded undo window.
    pub reverts: Option<Uuid>,
}
impl CreativePatchRecord {
    pub fn validate(&self) -> Result<()> {
        crate::production_design::design_text(&self.rationale, 4000, "patch record rationale")?;
        if self.before.is_empty()
            || self.before.len() > 128
            || self.before.len() != self.after.len()
            || self.before == self.after
            || self.reverts == Some(self.id)
        {
            return Err(DomainError::Invalid(
                "invalid creative patch record shape".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for (before, after) in self.before.iter().zip(&self.after) {
            before.validate()?;
            after.validate()?;
            if before.id != after.id || !ids.insert(before.id) {
                return Err(DomainError::Invalid(
                    "patch record identities differ or repeat".into(),
                ));
            }
            let mut editable = before.clone();
            editable.x = after.x;
            editable.y = after.y;
            editable.width = after.width;
            editable.height = after.height;
            editable.rotation_deg = after.rotation_deg;
            editable.opacity = after.opacity;
            editable.text = after.text.clone();
            editable.style = after.style.clone();
            editable.keyframes = after.keyframes.clone();
            if editable != *after {
                return Err(DomainError::Invalid(
                    "patch record changes fields outside the creative edit contract".into(),
                ));
            }
        }
        if serde_json::to_vec(self)
            .map_err(|error| DomainError::Invalid(error.to_string()))?
            .len()
            > MAX_PATCH_RECORD_BYTES
        {
            return Err(DomainError::Invalid(
                "creative patch record exceeds its byte budget".into(),
            ));
        }
        Ok(())
    }
}

pub(crate) fn validate_creative_patch_records(records: &[CreativePatchRecord]) -> Result<()> {
    if records.len() > MAX_CREATIVE_PATCH_RECORDS {
        return Err(DomainError::Invalid(
            "creative undo window exceeds 64 records".into(),
        ));
    }
    let mut ids = BTreeSet::new();
    for record in records {
        record.validate()?;
        if !ids.insert(record.id) {
            return Err(DomainError::Invalid(
                "duplicate creative patch record identity".into(),
            ));
        }
    }
    if serde_json::to_vec(records)
        .map_err(|error| DomainError::Invalid(error.to_string()))?
        .len()
        > MAX_PATCH_HISTORY_BYTES
    {
        return Err(DomainError::Invalid(
            "creative undo window exceeds its byte budget".into(),
        ));
    }
    Ok(())
}

impl Project {
    pub fn preview_creative_patch_undo(&self, patch_id: Uuid) -> Result<CreativePatchPreview> {
        let record = self
            .production_design
            .patches
            .iter()
            .find(|record| record.id == patch_id)
            .ok_or_else(|| DomainError::NotFound(format!("creative-patch:{patch_id}")))?;
        record.validate()?;
        if self
            .production_design
            .patches
            .iter()
            .any(|record| record.reverts == Some(patch_id))
        {
            return Err(DomainError::Invalid(
                "this patch has already been reverted; undo its inverse to redo".into(),
            ));
        }
        self.ensure_unlocked(
            &self.resource_key(),
            &[
                LockKind::Content,
                LockKind::Style,
                LockKind::Position,
                LockKind::Timing,
            ],
        )?;
        self.ensure_unlocked(
            &format!("scene:{}", record.scene_id),
            &[
                LockKind::Content,
                LockKind::Style,
                LockKind::Position,
                LockKind::Timing,
            ],
        )?;
        let scene = self
            .scenes
            .iter()
            .find(|scene| scene.id == record.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", record.scene_id)))?;
        // The original postimage is the merge base: only fields changed by the
        // selected patch are inverted. Conflicting later edits fail atomically.
        let merged = merge_component_nodes(&record.after, &scene.nodes, &record.before)?;
        let scope = record
            .before
            .iter()
            .map(|node| node.id)
            .collect::<BTreeSet<_>>();
        let preview = CreativePatchPreview {
            source_revision: self.revision,
            scene_id: scene.id,
            before: scene
                .nodes
                .iter()
                .filter(|node| scope.contains(&node.id))
                .cloned()
                .collect(),
            after: merged
                .into_iter()
                .filter(|node| scope.contains(&node.id))
                .collect(),
            dirty_start: scene.start,
            dirty_end: scene
                .start
                .checked_add(scene.duration)
                .map_err(|error| DomainError::Invalid(error.to_string()))?,
            kind: "semantic_diff_full_scene_invalidation_not_pixel_verification".into(),
        };
        let mut candidate = self.clone();
        candidate.replace_creative_preview(&preview)?;
        candidate.validate()?;
        if preview.before == preview.after {
            return Err(DomainError::Invalid(
                "undo would not change the current project".into(),
            ));
        }
        Ok(preview)
    }

    pub(crate) fn replace_creative_preview(
        &mut self,
        preview: &CreativePatchPreview,
    ) -> Result<()> {
        let scene = self
            .scenes
            .iter_mut()
            .find(|scene| scene.id == preview.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", preview.scene_id)))?;
        for updated in &preview.after {
            let node = scene
                .nodes
                .iter_mut()
                .find(|node| node.id == updated.id)
                .ok_or_else(|| DomainError::NotFound(format!("node:{}", updated.id)))?;
            *node = updated.clone();
        }
        scene.status = SceneStatus::Draft;
        Ok(())
    }

    pub(crate) fn commit_creative_preview(
        &mut self,
        preview: CreativePatchPreview,
        rationale: String,
        reverts: Option<Uuid>,
    ) -> Result<()> {
        if preview.source_revision != self.revision {
            return Err(DomainError::Invalid("creative preview is stale".into()));
        }
        let record = CreativePatchRecord {
            id: Uuid::now_v7(),
            scene_id: preview.scene_id,
            source_revision: self.revision,
            rationale,
            before: preview.before.clone(),
            after: preview.after.clone(),
            reverts,
        };
        record.validate()?;
        let mut candidate = self.clone();
        candidate.replace_creative_preview(&preview)?;
        candidate.production_design.patches.push(record);
        if candidate.production_design.patches.len() > MAX_CREATIVE_PATCH_RECORDS {
            candidate.production_design.patches.remove(0);
        }
        candidate.validate()?;
        *self = candidate;
        Ok(())
    }

    pub(crate) fn undo_creative_patch(&mut self, patch_id: Uuid) -> Result<()> {
        let preview = self.preview_creative_patch_undo(patch_id)?;
        self.commit_creative_preview(
            preview,
            format!("Revert creative patch {patch_id}"),
            Some(patch_id),
        )
    }
}
