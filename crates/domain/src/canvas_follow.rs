//! Exact, non-persisting projection of a saved Follow relation onto the
//! target's exact translation poses. The follower's terminal relative offset is
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
struct TranslationPoint {
    x: f64,
    y: f64,
    at: RationalTime,
    curve: MotionInterpolation,
}
type Translation = Vec<TranslationPoint>;

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
        && node.width <= 16384.0
        && node.height <= 16384.0
        && [node.x, node.y, node.width, node.height]
            .iter()
            .all(|value| value.is_finite())
}

fn root_translation(node: &CanvasNode, scene: &Scene, fps: RationalTime) -> Result<Translation> {
    let fail = || {
        invalid(
            "the root needs 2 to 6 exact translation poses with common linear, ease-out-cubic or Hold curves and unchanged size and orientation",
        )
    };
    if !node.relations.is_empty() || !scene.beats.is_empty() {
        return Err(fail());
    }
    let _ = crate::exact_canvas_opacity_spans(node, scene, fps)?;
    let mut slots = BTreeMap::<RationalTime, Vec<&CanvasKeyframe>>::new();
    for key in &node.keyframes {
        key.validate()?;
        if key.property != MotionProperty::Opacity {
            slots.entry(key.at).or_default().push(key);
        }
    }
    if !(2..=6).contains(&slots.len()) {
        return Err(fail());
    }
    let multiple = slots.len() > 2;
    let mut points = Vec::with_capacity(slots.len());
    let mut grammar = None;
    let frame = RationalTime::new(fps.den, fps.num)?;
    for (at, keys) in slots {
        let count = keys.len();
        let curve = keys[0].interpolation;
        let numerator = i128::from(at.num) * i128::from(fps.num);
        let denominator = i128::from(at.den) * i128::from(fps.den);
        if !matches!(count, 2 | 5)
            || (multiple && count != 5)
            || grammar.is_some_and(|previous| previous != count)
            || at >= scene.duration
            || numerator < 0
            || denominator <= 0
            || numerator % denominator != 0
            || numerator / denominator > 36_000
            || !matches!(
                curve,
                MotionInterpolation::Linear
                    | MotionInterpolation::EaseOutCubic
                    | MotionInterpolation::Hold
            )
            || (points.is_empty()
                && (at != RationalTime::ZERO || curve != MotionInterpolation::Linear))
            || (curve == MotionInterpolation::Hold && at.checked_add(frame)? > scene.duration)
        {
            return Err(fail());
        }
        grammar = Some(count);
        let mut properties = BTreeSet::new();
        let mut x = None;
        let mut y = None;
        for key in keys {
            if !properties.insert(key.property) || key.interpolation != curve {
                return Err(fail());
            }
            match key.property {
                MotionProperty::X => x = Some(key.value),
                MotionProperty::Y => y = Some(key.value),
                MotionProperty::Width if key.value == node.width => {}
                MotionProperty::Height if key.value == node.height => {}
                MotionProperty::RotationDeg if key.value == node.rotation_deg => {}
                _ => return Err(fail()),
            }
        }
        points.push(TranslationPoint {
            at,
            curve,
            x: x.ok_or_else(&fail)?,
            y: y.ok_or_else(&fail)?,
        });
    }
    let end = points.last().expect("bounded nonempty translation");
    if end.x != node.x
        || end.y != node.y
        || !points
            .iter()
            .any(|point| point.x != node.x || point.y != node.y)
    {
        return Err(fail());
    }
    Ok(points)
}

/// One Follow per independent follower, a nonzero exact translation at its
/// root, and a DAG of followers. Independent opacity stays on each original
/// object. Static alignments may coexist only on disjoint participants.
/// No Attach, inherited scale/rotation, parent transforms or mixed clocks.
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
                || node
                    .keyframes
                    .iter()
                    .any(|key| key.property != MotionProperty::Opacity)
                || !admitted(node, &parents)
                || position_locked
                || node.property_locks.contains(&NodeProperty::Position)
            {
                return Err(invalid(
                    "each follower needs one Follow, an unlocked independent pose and no authored geometry keys",
                ));
            }
            let _ = crate::exact_canvas_opacity_spans(node, scene, fps)?;
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
            let translation: Translation = tracks[&target_id]
                .iter()
                .map(|point| TranslationPoint {
                    x: node.x + (point.x - target.x),
                    y: node.y + (point.y - target.y),
                    at: point.at,
                    curve: point.curve,
                })
                .collect();
            if translation
                .iter()
                .any(|point| !point.x.is_finite() || !point.y.is_finite())
                || !translation
                    .iter()
                    .any(|point| point.x != node.x || point.y != node.y)
            {
                return Err(invalid(
                    "derived translation is not representable at the follower's saved coordinates",
                ));
            }
            let mut keys = Vec::new();
            for point in &translation {
                let values = [
                    (MotionProperty::X, point.x),
                    (MotionProperty::Y, point.y),
                    (MotionProperty::Width, node.width),
                    (MotionProperty::Height, node.height),
                    (MotionProperty::RotationDeg, node.rotation_deg),
                ];
                // Preserve the legacy paired two-pose route; full waypoint
                // geometry exists only in this read-only projection.
                for (property, value) in
                    values
                        .into_iter()
                        .take(if translation.len() > 2 { 5 } else { 2 })
                {
                    keys.push(CanvasKeyframe {
                        at: point.at,
                        property,
                        value,
                        interpolation: point.curve,
                    });
                }
            }
            keys.extend(
                node.keyframes
                    .iter()
                    .filter(|key| key.property == MotionProperty::Opacity)
                    .cloned(),
            );
            for key in &keys {
                key.validate()?;
            }
            tracks.insert(id, translation);
            result.insert(id, keys);
        }
    }
    Ok(result)
}
