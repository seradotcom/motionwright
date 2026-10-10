//! Conservative first-party HyperFrames **visual-only** frame invalidation.
//!
//! A source-level proposal NEVER proves full video/audio reuse. The direct
//! native frame oracle must compare every reused PNG with a freshly rendered
//! full-after source before emitting a verified incremental transfer artifact.
//! Unadmitted springs, shutter blur, transition dependencies and audio tails
//! are not inferred from names or invented as empty dependencies.
use crate::*;
use native::{Curve, HyperframesDocument, Node, Property};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrameInterval {
    pub start: u32,
    pub end_exclusive: u32,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct NativeFrameInvalidation {
    pub schema: String,
    pub before_source_sha256: String,
    pub after_source_sha256: String,
    pub total_frames: u32,
    pub dirty_intervals: Vec<FrameInterval>,
    pub reusable_intervals: Vec<FrameInterval>,
    pub dirty_frame_count: u32,
    pub reusable_frame_count: u32,
    pub source_fidelity_class: String,
    pub invalidation_reason: String,
    pub observation_readback_reusable: bool,
    pub audio_samples_reusable: bool,
    pub encoder_output_reusable: bool,
    pub rendered_pixel_equivalence_verified: bool,
    pub actual_native_frames_avoided: u32,
    pub owner_granted_execution: bool,
}
fn intervals(mask: &[bool], kind: bool) -> Vec<FrameInterval> {
    let mut out = Vec::new();
    let mut begin = None;
    for (index, value) in mask.iter().copied().enumerate() {
        if value == kind && begin.is_none() {
            begin = Some(index as u32);
        } else if value != kind
            && let Some(start) = begin.take()
        {
            out.push(FrameInterval {
                start,
                end_exclusive: index as u32,
            });
        }
    }
    if let Some(start) = begin {
        out.push(FrameInterval {
            start,
            end_exclusive: mask.len() as u32,
        });
    }
    out
}
/// The exact HTML/GSAP profile uses `timeline.set()` for HOLD keys and a
/// from-to tween otherwise. An animated opacity with a non-HOLD key cannot
/// safely be treated as zero even if its two authored endpoints are zero,
/// since frame seeking and interpolation semantics require renderer proof.
fn author_proved_hidden(node: &Node, frame: u32) -> bool {
    let mut state = node.pose.opacity;
    let mut keys = node
        .keyframes
        .iter()
        .filter(|key| key.property == Property::Opacity)
        .collect::<Vec<_>>();
    if keys.iter().any(|key| key.curve != Curve::Hold) {
        return false;
    }
    keys.sort_by(|a, b| {
        let a_time = (
            a.frame,
            a.subframe.map_or(0_u32, |s| s.num),
            a.subframe.map_or(1_u32, |s| s.den),
        );
        let b_time = (
            b.frame,
            b.subframe.map_or(0_u32, |s| s.num),
            b.subframe.map_or(1_u32, |s| s.den),
        );
        a_time.0.cmp(&b_time.0).then_with(|| {
            (u128::from(a_time.1) * u128::from(b_time.2))
                .cmp(&(u128::from(b_time.1) * u128::from(a_time.2)))
        })
    });
    for key in keys {
        // Integer screenshot samples only: a fractional knot applies after
        // the current whole frame, never before its exact rational position.
        if key.frame < frame || (key.frame == frame && key.subframe.is_none()) {
            state = key.value;
        }
    }
    state == 0.0
}
fn invisible_with_ancestors(node: &Node, nodes: &BTreeMap<Uuid, &Node>, frame: u32) -> bool {
    let mut cursor = Some(node);
    let mut depth = 0_usize;
    while let Some(item) = cursor {
        if author_proved_hidden(item, frame) {
            return true;
        }
        depth += 1;
        if depth > native::MAX_NODES {
            return false;
        }
        cursor = item.parent_id.and_then(|id| nodes.get(&id).copied());
    }
    false
}
fn visual_parts_identical(before: &Node, after: &Node) -> bool {
    before.parent_id == after.parent_id
        && before.pose == after.pose
        && before.content == after.content
        && before.blend == after.blend
        && before.clip == after.clip
        && before.effects == after.effects
        && before.keyframes == after.keyframes
}
/// Source-only preview invalidation. Every zero-opacity reuse is a proposal;
/// it is not a permission to skip a render or keep previous observations.
/// The caller's full-after screenshot oracle must compare bytes in every
/// proposed reusable frame and fail closed on any mismatch.
pub fn propose_native_frame_invalidation(
    before: &HyperframesDocument,
    after: &HyperframesDocument,
) -> Result<NativeFrameInvalidation> {
    native::validate_document(before).map_err(|e| CraftError(e.to_string()))?;
    native::validate_document(after).map_err(|e| CraftError(e.to_string()))?;
    let before_sha = native::source_digest(before).map_err(|e| CraftError(e.to_string()))?;
    let after_sha = native::source_digest(after).map_err(|e| CraftError(e.to_string()))?;
    let n = after.canvas.frames;
    let mut dirty = vec![true; n as usize];
    let reason = if before.canvas != after.canvas
        || before.camera != after.camera
        || before.assets != after.assets
        || before.nodes.len() != after.nodes.len()
        || before
            .nodes
            .iter()
            .zip(after.nodes.iter())
            .any(|(old, new)| old.id != new.id || old.parent_id != new.parent_id)
    {
        // A changed camera, source-media digest, hierarchy, composition
        // order or dimension can have cross-scene/per-frame consequences.
        "global_camera_asset_canvas_or_graph_dependency"
    } else {
        dirty.fill(false);
        let prev = before
            .nodes
            .iter()
            .map(|node| (node.id, node))
            .collect::<BTreeMap<_, _>>();
        let next = after
            .nodes
            .iter()
            .map(|node| (node.id, node))
            .collect::<BTreeMap<_, _>>();
        let mut visual_changes = 0;
        for (old, new) in before.nodes.iter().zip(after.nodes.iter()) {
            if visual_parts_identical(old, new) {
                continue;
            }
            visual_changes += 1;
            for frame in 0..n {
                let index = frame as usize;
                if !invisible_with_ancestors(old, &prev, frame)
                    || !invisible_with_ancestors(new, &next, frame)
                {
                    dirty[index] = true;
                }
            }
        }
        if visual_changes == 0 {
            "metadata_or_human_locks_only_pixels_unchanged_unverified"
        } else if dirty.iter().any(|dirty| !*dirty) {
            "source_proved_zero_opacity_hold_frames_require_full_after_oracle"
        } else {
            "conservative_all_frames_for_visible_or_unverified_dependencies"
        }
    };
    let dirty_count = dirty.iter().filter(|d| **d).count() as u32;
    Ok(NativeFrameInvalidation {
        schema: "motionwright.native-source-invalidation/1".into(),
        before_source_sha256: before_sha,
        after_source_sha256: after_sha,
        total_frames: n,
        dirty_intervals: intervals(&dirty, true),
        reusable_intervals: intervals(&dirty, false),
        dirty_frame_count: dirty_count,
        reusable_frame_count: n - dirty_count,
        source_fidelity_class: "native_html_visual_only".into(),
        invalidation_reason: reason.into(),
        observation_readback_reusable: false,
        audio_samples_reusable: false,
        encoder_output_reusable: false,
        rendered_pixel_equivalence_verified: false,
        actual_native_frames_avoided: 0,
        owner_granted_execution: false,
    })
}
