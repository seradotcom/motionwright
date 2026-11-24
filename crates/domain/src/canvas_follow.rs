//! Exact, non-persisting projection of a saved Follow relation onto the
//! target's paired translation. The follower's terminal relative offset is
//! authored by its ordinary saved pose; no new source keys are saved.
use crate::{
    BlendMode, CanvasKeyframe, CanvasNode, CoordinateSpace, DomainError, MotionInterpolation,
    MotionProperty, NodeProperty, RationalTime, RelationKind, Result, Scene,
};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

const MAX_NODES: usize = 512;

fn invalid(message: &str) -> DomainError {
    DomainError::Invalid(format!("Canvas Follow: {message}"))
}

#[derive(Clone, Copy)]
struct Translation {
    start_x: f64,
    start_y: f64,
    end: RationalTime,
    curve: MotionInterpolation,
}

fn admitted(node: &CanvasNode, parents: &BTreeSet<Uuid>) -> bool {
    matches!(
        node.kind.as_str(),
        "shape" | "rectangle" | "circle" | "text"
    ) && node.parent_id.is_none()
        && !parents.contains(&node.id)
        && node.coordinate_space == CoordinateSpace::ProjectPixels
        && node.rotation_deg == 0.0
        && node.opacity == 1.0
        && node.style.blend_mode == BlendMode::Normal
        && node.width > 0.0
        && node.height > 0.0
        && [node.x, node.y, node.width, node.height]
            .iter()
            .all(|value| value.is_finite())
}

fn root_translation(node: &CanvasNode, scene: &Scene, fps: RationalTime) -> Result<Translation> {
    let fail = || {
        invalid("the root target needs one exact paired linear or ease-out-cubic X/Y translation")
    };
    if !node.relations.is_empty() || node.keyframes.len() != 4 || !scene.beats.is_empty() {
        return Err(fail());
    }
    let mut x = Vec::new();
    let mut y = Vec::new();
    for key in &node.keyframes {
        key.validate()?;
        match key.property {
            MotionProperty::X => x.push(key),
            MotionProperty::Y => y.push(key),
            _ => return Err(fail()),
        }
    }
    x.sort_by_key(|key| key.at);
    y.sort_by_key(|key| key.at);
    if x.len() != 2 || y.len() != 2 {
        return Err(fail());
    }
    let end = x[1].at;
    let curve = x[1].interpolation;
    let numerator = i128::from(end.num) * i128::from(fps.num);
    let denominator = i128::from(end.den) * i128::from(fps.den);
    if x[0].at != RationalTime::ZERO
        || y[0].at != RationalTime::ZERO
        || end <= RationalTime::ZERO
        || end >= scene.duration
        || y[1].at != end
        || x[0].interpolation != MotionInterpolation::Linear
        || y[0].interpolation != MotionInterpolation::Linear
        || curve != y[1].interpolation
        || !matches!(
            curve,
            MotionInterpolation::Linear | MotionInterpolation::EaseOutCubic
        )
        || x[1].value != node.x
        || y[1].value != node.y
        || (x[0].value == node.x && y[0].value == node.y)
        || numerator <= 0
        || denominator <= 0
        || numerator % denominator != 0
        || numerator / denominator > 36_000
    {
        return Err(fail());
    }
    Ok(Translation {
        start_x: x[0].value,
        start_y: y[0].value,
        end,
        curve,
    })
}

/// The exact source subset: one Follow per independent static follower,
/// a nonzero original paired translation at its root, and a DAG of followers.
/// Static alignments may coexist only on disjoint participants. No guessed
/// Attach semantics, parent transforms, mixed clocks, or independent keys.
pub fn resolve_canvas_follow_tracks(
    scene: &Scene,
    fps: RationalTime,
    position_locked: bool,
) -> Result<BTreeMap<Uuid, Vec<CanvasKeyframe>>> {
    if !scene.nodes.iter().any(|node| {
        node.relations
            .iter()
            .any(|relation| relation.kind == RelationKind::Follow)
    }) {
        return Ok(BTreeMap::new());
    }
    if scene.nodes.len() > MAX_NODES
        || !scene.beats.is_empty()
        || fps.validate().is_err()
        || fps.num <= 0
        || fps.den <= 0
    {
        return Err(invalid(
            "a bounded continuous scene and exact positive output clock are required",
        ));
    }
    let all: BTreeMap<_, _> = scene.nodes.iter().map(|node| (node.id, node)).collect();
    if all.len() != scene.nodes.len() {
        return Err(invalid("duplicate object identity"));
    }
    let parents: BTreeSet<_> = scene
        .nodes
        .iter()
        .filter_map(|node| node.parent_id)
        .collect();
    let mut participants = BTreeSet::new();
    let mut static_participants = BTreeSet::new();
    let mut remaining = BTreeMap::new();
    for node in &scene.nodes {
        if node
            .relations
            .iter()
            .any(|relation| relation.kind == RelationKind::Follow)
        {
            if node.relations.len() != 1
                || !node.keyframes.is_empty()
                || !admitted(node, &parents)
                || position_locked
                || node.property_locks.contains(&NodeProperty::Position)
            {
                return Err(invalid(
                    "each follower needs one Follow, an unlocked independent static pose and no authored keys",
                ));
            }
            let target = node.relations[0].target_id;
            if target == node.id {
                return Err(invalid("a follower cannot target itself"));
            }
            let target_node = all
                .get(&target)
                .copied()
                .ok_or_else(|| invalid("target object is absent"))?;
            if !admitted(target_node, &parents) {
                return Err(invalid(
                    "Follow target must be an independent planar drawable",
                ));
            }
            participants.extend([node.id, target]);
            remaining.insert(node.id, target);
        } else {
            for relation in &node.relations {
                static_participants.extend([node.id, relation.target_id]);
            }
        }
    }
    if !participants.is_disjoint(&static_participants) {
        return Err(invalid(
            "Follow and other relations share a participant; their combined semantics are not admitted",
        ));
    }
    let mut tracks: BTreeMap<Uuid, Translation> = BTreeMap::new();
    for id in &participants {
        if !remaining.contains_key(id) {
            tracks.insert(*id, root_translation(all[id], scene, fps)?);
        }
    }
    let mut result = BTreeMap::new();
    while !remaining.is_empty() {
        let ready: Vec<_> = remaining
            .iter()
            .filter(|(_, target)| tracks.contains_key(*target))
            .map(|(id, _)| *id)
            .collect();
        if ready.is_empty() {
            return Err(invalid("cyclic Follow dependencies or no translating root"));
        }
        for id in ready {
            let target_id = remaining.remove(&id).expect("ready target");
            let target = all[&target_id];
            let node = all[&id];
            let inherited = tracks[&target_id];
            let translation = Translation {
                start_x: node.x + (inherited.start_x - target.x),
                start_y: node.y + (inherited.start_y - target.y),
                end: inherited.end,
                curve: inherited.curve,
            };
            if !translation.start_x.is_finite()
                || !translation.start_y.is_finite()
                || (translation.start_x == node.x && translation.start_y == node.y)
            {
                return Err(invalid(
                    "derived translation is not representable at the follower's saved coordinates",
                ));
            }
            let keys = [
                (
                    RationalTime::ZERO,
                    MotionProperty::X,
                    translation.start_x,
                    MotionInterpolation::Linear,
                ),
                (
                    RationalTime::ZERO,
                    MotionProperty::Y,
                    translation.start_y,
                    MotionInterpolation::Linear,
                ),
                (
                    translation.end,
                    MotionProperty::X,
                    node.x,
                    translation.curve,
                ),
                (
                    translation.end,
                    MotionProperty::Y,
                    node.y,
                    translation.curve,
                ),
            ]
            .into_iter()
            .map(|(at, property, value, interpolation)| CanvasKeyframe {
                at,
                property,
                value,
                interpolation,
            })
            .collect::<Vec<_>>();
            for key in &keys {
                key.validate()?;
            }
            tracks.insert(id, translation);
            result.insert(id, keys);
        }
    }
    Ok(result)
}
