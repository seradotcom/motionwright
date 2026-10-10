//! Bounded source-level repair proposals for a persisted HyperFrames document.
//! No runtime/tool execution and no automatic overwrite of human authoring.
use crate::*;
use native::{Content, HyperframesDocument, Node, NodeField, Property, Subframe};
use std::collections::BTreeSet;
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum PoseRepairProperty {
    X,
    Y,
    Width,
    Height,
    ScaleX,
    ScaleY,
    Rotation,
    Opacity,
}
impl PoseRepairProperty {
    fn native(self) -> Property {
        match self {
            Self::X => Property::X,
            Self::Y => Property::Y,
            Self::Width => Property::Width,
            Self::Height => Property::Height,
            Self::ScaleX => Property::ScaleX,
            Self::ScaleY => Property::ScaleY,
            Self::Rotation => Property::Rotation,
            Self::Opacity => Property::Opacity,
        }
    }
    fn value(self, node: &Node) -> f64 {
        match self {
            Self::X => node.pose.x,
            Self::Y => node.pose.y,
            Self::Width => node.pose.width,
            Self::Height => node.pose.height,
            Self::ScaleX => node.pose.scale_x,
            Self::ScaleY => node.pose.scale_y,
            Self::Rotation => node.pose.rotation,
            Self::Opacity => node.pose.opacity,
        }
    }
    fn set(self, node: &mut Node, value: f64) {
        match self {
            Self::X => node.pose.x = value,
            Self::Y => node.pose.y = value,
            Self::Width => node.pose.width = value,
            Self::Height => node.pose.height = value,
            Self::ScaleX => node.pose.scale_x = value,
            Self::ScaleY => node.pose.scale_y = value,
            Self::Rotation => node.pose.rotation = value,
            Self::Opacity => node.pose.opacity = value,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum NativeRepairOperation {
    SetPose {
        node_id: Uuid,
        property: PoseRepairProperty,
        expected: f64,
        next: f64,
    },
    SetBlur {
        node_id: Uuid,
        expected: f64,
        next: f64,
    },
    ReplaceTextRun {
        node_id: Uuid,
        run_index: usize,
        expected_text: String,
        next_text: String,
    },
    SetKeyframeValue {
        node_id: Uuid,
        property: Property,
        frame: u32,
        subframe: Option<Subframe>,
        expected: f64,
        next: f64,
    },
}
impl NativeRepairOperation {
    fn identity(&self) -> (Uuid, String) {
        match self {
            Self::SetPose {
                node_id, property, ..
            } => (*node_id, format!("pose/{property:?}")),
            Self::SetBlur { node_id, .. } => (*node_id, "effects/blur".into()),
            Self::ReplaceTextRun {
                node_id, run_index, ..
            } => (*node_id, format!("text/run/{run_index}")),
            Self::SetKeyframeValue {
                node_id,
                property,
                frame,
                subframe,
                ..
            } => (
                *node_id,
                format!("motion/{property:?}/{frame}/{subframe:?}"),
            ),
        }
    }
    fn node_id(&self) -> Uuid {
        self.identity().0
    }
    fn apply(&self, node: &mut Node) -> Result<()> {
        let guard = |expected: f64, next: f64| -> Result<()> {
            check(
                expected.is_finite() && next.is_finite() && next.abs() <= 32768.0,
                "Repair values must be bounded and finite",
            )?;
            check(
                expected != next,
                "Repair must actually change an authored value",
            )
        };
        match self {
            Self::SetPose {
                property,
                expected,
                next,
                ..
            } => {
                guard(*expected, *next)?;
                check(
                    !node.locked_properties.contains(&property.native()),
                    "The human owner has protected this native transform",
                )?;
                check(
                    property.value(node) == *expected,
                    "Repair pose precondition is stale; reobserve the exact source",
                )?;
                property.set(node, *next);
            }
            Self::SetBlur { expected, next, .. } => {
                guard(*expected, *next)?;
                check(
                    !node.locked_fields.contains(&NodeField::Appearance)
                        && !node.locked_properties.contains(&Property::Blur),
                    "The human owner has protected this effect",
                )?;
                check(
                    node.effects.blur == *expected,
                    "Repair blur precondition is stale",
                )?;
                node.effects.blur = *next;
            }
            Self::ReplaceTextRun {
                run_index,
                expected_text,
                next_text,
                ..
            } => {
                check(
                    !node.locked_fields.contains(&NodeField::Content),
                    "The human owner has protected this text",
                )?;
                text(
                    next_text,
                    8000,
                    "Repair copy is empty or outside its source budget",
                )?;
                let Content::Text { runs, .. } = &mut node.content else {
                    return Err(CraftError(
                        "Cannot repair text on a non-text native object".into(),
                    ));
                };
                let run = runs
                    .get_mut(*run_index)
                    .ok_or_else(|| CraftError("Repair text-run index is absent".into()))?;
                check(
                    run.text == *expected_text && run.text != *next_text,
                    "Repair text precondition is stale or unchanged",
                )?;
                run.text = next_text.clone();
            }
            Self::SetKeyframeValue {
                property,
                frame,
                subframe,
                expected,
                next,
                ..
            } => {
                guard(*expected, *next)?;
                check(
                    !node.locked_fields.contains(&NodeField::Keyframes)
                        && !node.locked_properties.contains(property),
                    "Human-owned keyframe channel cannot be modified",
                )?;
                let knot = node
                    .keyframes
                    .iter_mut()
                    .find(|k| {
                        k.property == *property && k.frame == *frame && k.subframe == *subframe
                    })
                    .ok_or_else(|| CraftError("Exact rational keyframe does not exist".into()))?;
                check(
                    knot.value == *expected,
                    "Repair keyframe precondition is stale",
                )?;
                knot.value = *next;
            }
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeRepairProposal {
    pub schema: String,
    pub expected_source_sha256: String,
    pub proposed_source_sha256: String,
    pub description: String,
    pub changed_nodes: Vec<Uuid>,
    pub changed_properties: Vec<String>,
    pub dirty_first_frame: u32,
    pub dirty_end_frame_exclusive: u32,
    pub dirty_reason: String,
    pub document: HyperframesDocument,
    pub committed: bool,
    pub runtime_executed: bool,
    pub renderer_equivalence_checked: bool,
    pub creative_approval: String,
}
pub fn propose_native_repair(
    source: &HyperframesDocument,
    expected_source_sha256: &str,
    rationale: &str,
    edits: &[NativeRepairOperation],
) -> Result<NativeRepairProposal> {
    text(
        rationale,
        2000,
        "A localized repair requires an explicit rationale",
    )?;
    check(
        (1..=3).contains(&edits.len()),
        "A localized creative repair may contain at most three explicit edits",
    )?;
    native::validate_document(source).map_err(|e| CraftError(e.to_string()))?;
    let sha = native::source_digest(source).map_err(|e| CraftError(e.to_string()))?;
    check(
        valid_sha(expected_source_sha256) && sha == expected_source_sha256,
        "Source changed since the repair was proposed. Request a new exact revision",
    )?;
    let mut proposal = source.clone();
    let mut keys = BTreeSet::new();
    let mut nodes = BTreeSet::new();
    let mut changed = Vec::new();
    for edit in edits {
        let (id, key) = edit.identity();
        check(
            keys.insert((id, key.clone())),
            "An atomic repair cannot change the same property twice",
        )?;
        let node = proposal
            .nodes
            .iter_mut()
            .find(|node| node.id == edit.node_id())
            .ok_or_else(|| CraftError("Target repair object does not exist".into()))?;
        edit.apply(node)?;
        nodes.insert(id);
        changed.push(format!("{id}/{key}"));
    }
    native::validate_document(&proposal).map_err(|e| CraftError(e.to_string()))?;
    let new_sha = native::source_digest(&proposal).map_err(|e| CraftError(e.to_string()))?;
    check(
        new_sha != sha,
        "A repair cannot claim a source change without altering native bytes",
    )?;
    Ok(NativeRepairProposal {
        schema: "motionwright.native-repair-proposal/1".into(),
        expected_source_sha256: sha,
        proposed_source_sha256: new_sha,
        description: rationale.into(),
        changed_nodes: nodes.into_iter().collect(),
        changed_properties: changed,
        dirty_first_frame: 0,
        dirty_end_frame_exclusive: source.canvas.frames,
        dirty_reason: "conservative_full_scene_until_renderer_temporal_dependency_proof".into(),
        document: proposal,
        committed: false,
        runtime_executed: false,
        renderer_equivalence_checked: false,
        creative_approval: "human_approval_required".into(),
    })
}
