//! Read-only, exact static box alignment for Motion Canvas. Source transforms,
//! semantic relation IDs, locks and the original Project remain untouched.
use crate::{
    BlendMode, CanvasNode, CoordinateSpace, DomainError, NodeProperty, RelationKind, Result, Scene,
};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

const MAX_NODES: usize = 512;
const EPSILON: f64 = 1e-6;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AlignedPosition {
    pub x: f64,
    pub y: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct AlignmentScale {
    pub x: f64,
    pub y: f64,
    pub object: f64,
}

fn invalid(message: &str) -> DomainError {
    DomainError::Invalid(format!("Canvas alignment: {message}"))
}

fn admitted(node: &CanvasNode, children: &BTreeSet<Uuid>) -> bool {
    matches!(
        node.kind.as_str(),
        "shape" | "rectangle" | "circle" | "text"
    ) && node.parent_id.is_none()
        && !children.contains(&node.id)
        && node.coordinate_space == CoordinateSpace::ProjectPixels
        && node.keyframes.is_empty()
        && node.rotation_deg == 0.0
        && node.opacity == 1.0
        && node.style.blend_mode == BlendMode::Normal
        && node.width > 0.0
        && node.height > 0.0
        && [node.x, node.y, node.width, node.height]
            .iter()
            .all(|value| value.is_finite())
}

#[derive(Debug, Clone, Copy)]
struct Equation {
    target: Uuid,
    edge: f64,
}

/// For an axis, the output box center is (world_start + size/2)*position_scale,
/// but the output box half-size is size*object_scale/2. Therefore matching
/// edges must compensate for the difference, particularly in portrait replan.
fn resolve_axis(
    nodes: &BTreeMap<Uuid, &CanvasNode>,
    equations: &BTreeMap<Uuid, Equation>,
    position_scale: f64,
    object_scale: f64,
    horizontal: bool,
    position_locked: bool,
) -> Result<BTreeMap<Uuid, f64>> {
    let start = |node: &CanvasNode| if horizontal { node.x } else { node.y };
    let size = |node: &CanvasNode| if horizontal { node.width } else { node.height };
    let mut values: BTreeMap<Uuid, f64> = nodes
        .iter()
        .filter(|(id, _)| !equations.contains_key(*id))
        .map(|(id, node)| (*id, start(node)))
        .collect();
    let mut remaining = equations.clone();
    while !remaining.is_empty() {
        let ready: Vec<Uuid> = remaining
            .iter()
            .filter(|(_, row)| values.contains_key(&row.target))
            .map(|(id, _)| *id)
            .collect();
        if ready.is_empty() {
            return Err(invalid("cyclic dependencies on the same axis"));
        }
        for id in ready {
            let equation = remaining.remove(&id).expect("ready equation");
            let node = nodes[&id];
            let target = nodes[&equation.target];
            let own_size = size(node);
            let target_size = size(target);
            let target_center = values[&equation.target] + target_size / 2.0;
            // edge=-1 (left/top), 0 (center), +1 (right/bottom).
            let desired = target_center - own_size / 2.0
                + equation.edge * (target_size - own_size) * object_scale / (2.0 * position_scale);
            if !desired.is_finite() {
                return Err(invalid("resolved coordinate is not finite"));
            }
            let locked = position_locked || node.property_locks.contains(&NodeProperty::Position);
            if locked && ((desired - start(node)) * position_scale).abs() > EPSILON {
                return Err(invalid("an alignment would move a locked object"));
            }
            values.insert(id, if locked { start(node) } else { desired });
        }
    }
    Ok(values)
}

/// Solve only the six literal axis alignments. Follow/Attach, animated targets,
/// parent transformations, multiple constraints on one axis and cycles remain
/// explicit errors. Output values are equivalent world coordinates for this
/// exact projection, not a mutation or proof of raster/glyph bounds.
pub fn resolve_canvas_alignments(
    scene: &Scene,
    scale: AlignmentScale,
    position_locked: bool,
) -> Result<BTreeMap<Uuid, AlignedPosition>> {
    if !scene.nodes.iter().any(|node| !node.relations.is_empty()) {
        return Ok(BTreeMap::new());
    }
    if scene.nodes.len() > MAX_NODES
        || [scale.x, scale.y, scale.object]
            .iter()
            .any(|v| !v.is_finite() || *v <= 0.0)
        || scale.object > scale.x.max(scale.y)
    {
        return Err(invalid(
            "projection exceeds its bounded static layout contract",
        ));
    }
    let all: BTreeMap<Uuid, &CanvasNode> = scene.nodes.iter().map(|node| (node.id, node)).collect();
    if all.len() != scene.nodes.len() {
        return Err(invalid("duplicate object identity"));
    }
    let children: BTreeSet<Uuid> = scene
        .nodes
        .iter()
        .filter_map(|node| node.parent_id)
        .collect();
    let mut nodes = BTreeMap::new();
    let mut xs = BTreeMap::new();
    let mut ys = BTreeMap::new();
    let mut constrained = BTreeSet::new();
    for node in scene.nodes.iter().filter(|node| !node.relations.is_empty()) {
        if !admitted(node, &children) {
            return Err(invalid(
                "only independent static drawable boxes may be aligned",
            ));
        }
        nodes.insert(node.id, node);
        constrained.insert(node.id);
        for relation in &node.relations {
            let target = all
                .get(&relation.target_id)
                .copied()
                .ok_or_else(|| invalid("target object is absent"))?;
            if target.id == node.id || !admitted(target, &children) {
                return Err(invalid(
                    "alignment target is not an independent static drawable box",
                ));
            }
            nodes.insert(target.id, target);
            let (axis, edge) = match relation.kind {
                RelationKind::AlignLeft => (&mut xs, -1.0),
                RelationKind::AlignCenterX => (&mut xs, 0.0),
                RelationKind::AlignRight => (&mut xs, 1.0),
                RelationKind::AlignTop => (&mut ys, -1.0),
                RelationKind::AlignCenterY => (&mut ys, 0.0),
                RelationKind::AlignBottom => (&mut ys, 1.0),
                RelationKind::Follow | RelationKind::Attach => {
                    return Err(invalid(
                        "Follow and Attach require a dedicated dynamic relation projection",
                    ));
                }
            };
            if axis
                .insert(
                    node.id,
                    Equation {
                        target: target.id,
                        edge,
                    },
                )
                .is_some()
            {
                return Err(invalid(
                    "more than one alignment is authored on the same axis",
                ));
            }
        }
    }
    let x = resolve_axis(&nodes, &xs, scale.x, scale.object, true, position_locked)?;
    let y = resolve_axis(&nodes, &ys, scale.y, scale.object, false, position_locked)?;
    Ok(constrained
        .into_iter()
        .map(|id| {
            (
                id,
                AlignedPosition {
                    x: x[&id],
                    y: y[&id],
                },
            )
        })
        .collect())
}
