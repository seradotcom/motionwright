//! Bounded owner-reviewed OTIO cut acceptance into the existing project CAS.
//! A Python bridge verifies media and produces a *proposal*; this domain
//! gate never asserts NLE authenticity, re-links media or executes an editor.
use crate::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

pub const OTIO_PREVIEW_SCHEMA: &str = "motionwright.otio-edit-preview/1";
pub const OTIO_MAX_SCENES: usize = 32;

const FIDELITY: [(&str, &str, &str); 12] = [
    ("scene_order", "editable_otio", "bounded_proposal_only"),
    (
        "scene_duration",
        "editable_otio",
        "bounded_frame_exact_end_trim_only",
    ),
    (
        "source_video_media",
        "verified_external_reference",
        "source_sha_revalidated_no_new_media",
    ),
    (
        "media_source_in_point",
        "represented_in_otio",
        "not_supported_native_scene_offset",
    ),
    (
        "unbound_scene_media",
        "missing_reference",
        "cannot_invent_media",
    ),
    (
        "native_object_geometry_hierarchy_text_masks",
        "source_project_only",
        "not_supported",
    ),
    (
        "camera_keyframes_effects_and_procedural_controls",
        "source_project_only",
        "not_supported",
    ),
    (
        "source_audio_and_voice_cues",
        "source_project_only",
        "not_roundtripped_as_nle_audio",
    ),
    (
        "captions_transcripts_alignment",
        "source_project_only",
        "not_roundtripped",
    ),
    (
        "human_locks_creative_reviews_branches",
        "source_project_only",
        "not_roundtripped",
    ),
    (
        "runtime_render_authority",
        "not_exported",
        "requires_separate_owner_grant",
    ),
    (
        "publication_rights",
        "not_granted",
        "requires_separate_owner_approval",
    ),
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioPropertyFidelity {
    pub property: String,
    pub status: String,
    pub import_state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub missing_count: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum OtioNativeCut {
    MoveScene {
        scene_id: Uuid,
        to_index: usize,
    },
    SetSceneDuration {
        scene_id: Uuid,
        duration: RationalTime,
    },
}

/// The exact shape of the existing tooling/otio-media/bridge.py preview output.
/// The preview is a declaration, not an authenticated authorization receipt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioEditPreview {
    pub schema: String,
    pub source_project_sha256: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub expected_project_revision: u64,
    pub original_scene_order: Vec<Uuid>,
    pub proposed_scene_order: Vec<Uuid>,
    pub native_change_proposals: Vec<OtioNativeCut>,
    pub media_refs_verified: usize,
    pub missing_references_retained: usize,
    pub loss_report: Vec<String>,
    pub property_fidelity: Vec<OtioPropertyFidelity>,
    pub edit_imported: bool,
    pub source_media_relinked: bool,
    pub owner_revision_commit_required: bool,
    pub human_review_required: bool,
    pub rendering_performed: bool,
    pub semantic_native_roundtrip: String,
    pub nle_receiver_acceptance: String,
    pub publication_approved: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OtioOwnerCutReview {
    pub preview: Box<OtioEditPreview>,
    pub reviewer_declaration: String,
    pub rationale: String,
    pub acknowledges_property_losses: bool,
    pub acknowledges_absolute_audio_marker_positions: bool,
    pub confirms_single_revision_change: bool,
}

fn bounded(value: &str, max: usize) -> bool {
    !value.trim().is_empty() && value.len() <= max && !value.chars().any(char::is_control)
}

fn sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

impl OtioEditPreview {
    /// Independently reconstruct the *minimal* exact supported native change
    /// sequence. Foreign NLE effects, tracks, media offsets and files are not
    /// expressible by this contract.
    pub fn bounded_native_changes(&self, project: &Project) -> Result<Vec<Change>> {
        let current: Vec<Uuid> = project.scenes.iter().map(|scene| scene.id).collect();
        if self.schema != OTIO_PREVIEW_SCHEMA
            || self.project_id != project.id
            || self.generation != project.generation
            || self.expected_project_revision != project.revision
            || !sha256(&self.source_project_sha256)
            || current.is_empty()
            || current.len() > OTIO_MAX_SCENES
            || self.original_scene_order != current
            || self.proposed_scene_order.len() != current.len()
            || self.native_change_proposals.is_empty()
            || self.native_change_proposals.len() > OTIO_MAX_SCENES * 2
            || !self.owner_revision_commit_required
            || !self.human_review_required
            || self.edit_imported
            || self.source_media_relinked
            || self.rendering_performed
            || self.publication_approved
            || self.semantic_native_roundtrip != "NOT_SUPPORTED"
            || self.nle_receiver_acceptance != "NOT_VERIFIED"
        {
            return Err(DomainError::Invalid(
                "OTIO cut preview must match an exact current project and remain a bounded, uncommitted proposal".into(),
            ));
        }
        if self
            .media_refs_verified
            .checked_add(self.missing_references_retained)
            != Some(current.len())
        {
            return Err(DomainError::Invalid(
                "OTIO source scene/media cardinality changed".into(),
            ));
        }
        let original_ids: BTreeSet<_> = current.iter().copied().collect();
        let proposed_ids: BTreeSet<_> = self.proposed_scene_order.iter().copied().collect();
        if proposed_ids != original_ids || proposed_ids.len() != current.len() {
            return Err(DomainError::Invalid(
                "OTIO proposal omitted, duplicated or invented a scene".into(),
            ));
        }
        if self.loss_report.is_empty()
            || self.loss_report.len() > 64
            || self.loss_report.iter().any(|line| !bounded(line, 1600))
            || self.property_fidelity.len() != FIDELITY.len()
        {
            return Err(DomainError::Invalid(
                "OTIO property-loss review evidence is incomplete".into(),
            ));
        }
        for (item, (property, status, import_state)) in self.property_fidelity.iter().zip(FIDELITY)
        {
            if item.property != property
                || item.status != status
                || item.import_state != import_state
                || if property == "unbound_scene_media" {
                    item.missing_count != Some(self.missing_references_retained)
                } else {
                    item.missing_count.is_some()
                }
            {
                return Err(DomainError::Invalid(
                    "OTIO property-fidelity contract was changed or stripped".into(),
                ));
            }
        }
        let mut duration_edits = BTreeMap::new();
        for operation in &self.native_change_proposals {
            if let OtioNativeCut::SetSceneDuration { scene_id, duration } = operation {
                let scene = project
                    .scenes
                    .iter()
                    .find(|s| s.id == *scene_id)
                    .ok_or_else(|| {
                        DomainError::Invalid("OTIO duration targets an unknown scene".into())
                    })?;
                if duration.validate().is_err()
                    || *duration <= RationalTime::ZERO
                    || *duration >= scene.duration
                    || *duration > whole_seconds(600)
                    || duration_edits.insert(*scene_id, *duration).is_some()
                {
                    return Err(DomainError::Invalid(
                        "OTIO cut accepts only one strict bounded end trim per original scene"
                            .into(),
                    ));
                }
            }
        }
        let mut working = current.clone();
        let mut canonical = Vec::new();
        for (target_index, &scene_id) in self.proposed_scene_order.iter().enumerate() {
            if working[target_index] == scene_id {
                continue;
            }
            let from = working
                .iter()
                .position(|item| *item == scene_id)
                .ok_or_else(|| {
                    DomainError::Invalid("OTIO proposed scene identity was lost".into())
                })?;
            working.remove(from);
            working.insert(target_index, scene_id);
            canonical.push(OtioNativeCut::MoveScene {
                scene_id,
                to_index: target_index,
            });
        }
        for scene_id in &self.proposed_scene_order {
            if let Some(&duration) = duration_edits.get(scene_id) {
                canonical.push(OtioNativeCut::SetSceneDuration {
                    scene_id: *scene_id,
                    duration,
                });
            }
        }
        if canonical != self.native_change_proposals {
            return Err(DomainError::Invalid(
                "OTIO cut proposals are reordered, nonminimal, duplicated or incompatible".into(),
            ));
        }
        Ok(canonical
            .into_iter()
            .map(|operation| match operation {
                OtioNativeCut::MoveScene { scene_id, to_index } => {
                    Change::MoveScene { scene_id, to_index }
                }
                OtioNativeCut::SetSceneDuration { scene_id, duration } => {
                    Change::SetSceneDuration { scene_id, duration }
                }
            })
            .collect())
    }
}

impl Project {
    pub fn apply_otio_owner_cut_review(&mut self, review: &OtioOwnerCutReview) -> Result<()> {
        if !bounded(&review.reviewer_declaration, 160)
            || !bounded(&review.rationale, 1600)
            || !review.acknowledges_property_losses
            || !review.confirms_single_revision_change
        {
            return Err(DomainError::Invalid(
                "Explicit owner-declared review and property-loss acceptance are required".into(),
            ));
        }
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        if self
            .scenes
            .iter()
            .any(|scene| matches!(scene.status, SceneStatus::Approved | SceneStatus::Review))
            || self.reviews.iter().any(|review| {
                matches!(
                    review.status,
                    ReviewStatus::Open | ReviewStatus::NeedsRecheck
                )
            })
        {
            return Err(DomainError::Invalid(
                "A reviewed/approved scene or unresolved project review cannot be overwritten by OTIO".into(),
            ));
        }
        if (!self.markers.is_empty()
            || !self.audio.voice_tracks.is_empty()
            || !self.audio.transcript.is_empty()
            || !self.audio.cues.is_empty()
            || !self.audio.sfx_cues.is_empty())
            && !review.acknowledges_absolute_audio_marker_positions
        {
            return Err(DomainError::Invalid(
                "Original marker/transcript/audio positions remain absolute: acknowledge the possible misalignment".into(),
            ));
        }
        let changes = review.preview.bounded_native_changes(self)?;
        let mut candidate = self.clone();
        for change in changes {
            candidate.apply_change(&change)?;
        }
        // No partial commit: only publish the validated candidate after all
        // canonical resource locks and temporal constraints have passed.
        *self = candidate;
        Ok(())
    }
}
