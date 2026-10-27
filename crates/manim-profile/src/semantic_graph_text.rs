//! Fixed presentation lowering for Graph-v1; canonical source is unchanged.
//! These derived rows are not a new domain model, runtime grant or Ready value.
use crate::semantic_graph::{ManimSemanticGraphPlan, exact_frame};
use crate::{ManimProfileError, Result};
use motionwright_domain::{
    GraphAppearance, GraphBox, GraphCausalBasis, GraphCausalInfluence, GraphContent,
    GraphFlowChannel, GraphPoint, GraphSyntheticFlowSource, GraphUnit,
};
use serde::Serialize;
use std::collections::BTreeSet;
use uuid::Uuid;

pub const GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256: &str =
    "f0c7b5a58efed42a32461df4a8cb92bc4cf187f644adad2b76423496dc0b258a";

fn reject(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum GraphTextOwner {
    Vertex { id: Uuid },
    Edge { id: Uuid },
    Disclosure,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphTextBlock {
    pub ordinal: u16,
    pub resource: String,
    pub owner: GraphTextOwner,
    pub text: String,
    pub authored_box: GraphBox,
    pub active_start_frame: u32,
    pub active_end_frame_exclusive: u32,
    pub active_event_ids: Vec<Uuid>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphAppearanceFrames {
    pub start_frame: u32,
    pub duration_frames: u32,
}

pub fn appearance_frames(appearance: &GraphAppearance, fps: u32) -> Result<GraphAppearanceFrames> {
    if !(1..=120).contains(&fps) {
        return Err(reject("graph_appearance_frame_rate"));
    }
    let result = GraphAppearanceFrames {
        start_frame: exact_frame(appearance.at, fps)?,
        duration_frames: exact_frame(appearance.duration, fps)?,
    };
    if result.duration_frames == 0 {
        return Err(reject("graph_appearance_duration"));
    }
    Ok(result)
}

fn append_block(
    blocks: &mut Vec<GraphTextBlock>,
    owner: GraphTextOwner,
    resource: String,
    text: String,
    authored_box: &GraphBox,
    interval: (u32, u32),
    active_event_ids: Vec<Uuid>,
) -> Result<()> {
    if interval.0 >= interval.1 || text.len() > 16 * 1024 {
        return Err(reject("graph_derived_text_or_interval_budget"));
    }
    let ordinal =
        u16::try_from(blocks.len()).map_err(|_| reject("graph_text_block_count_overflow"))?;
    blocks.push(GraphTextBlock {
        ordinal,
        owner,
        resource,
        text,
        authored_box: authored_box.clone(),
        active_start_frame: interval.0,
        active_end_frame_exclusive: interval.1,
        active_event_ids,
    });
    Ok(())
}

/// Every distinct event interval is validated, including the pre/post-event
/// labels. Coincident boundaries are coalesced, never rounded or sampled.
pub fn text_blocks(plan: &ManimSemanticGraphPlan) -> Result<Vec<GraphTextBlock>> {
    plan.validate()?;
    let graph = &plan.source;
    let fps = plan.render_profile.frame_rate;
    let count = exact_frame(graph.time_range.duration, fps)?;
    let mut blocks = Vec::new();
    for vertex in graph.content.vertices() {
        let text = match &vertex.unit {
            GraphUnit::Qualitative => vertex.label.clone(),
            GraphUnit::Symbol { symbol } => format!("{} ({symbol})", vertex.label),
        };
        append_block(
            &mut blocks,
            GraphTextOwner::Vertex { id: vertex.id },
            format!("graph-vertex:{}:{}", graph.id, vertex.id),
            text,
            &vertex.r#box,
            (0, count),
            Vec::new(),
        )?;
    }
    let disclosure = match &graph.content {
        GraphContent::CausalDiagram { edges, .. } => {
            let mut disclosures = Vec::with_capacity(edges.len());
            for edge in edges {
                let label_box = edge
                    .resolved_label_box(graph)
                    .map_err(|_| reject("canonical_graph_label_placement_rejected"))?;
                let sign = match edge.influence {
                    GraphCausalInfluence::Positive => "+",
                    GraphCausalInfluence::Negative => "\u{2212}",
                    GraphCausalInfluence::Unspecified => "?",
                };
                append_block(
                    &mut blocks,
                    GraphTextOwner::Edge { id: edge.id },
                    format!("graph-edge:{}:{}", graph.id, edge.id),
                    format!("{} \u{00b7} {sign}", edge.label),
                    &label_box,
                    (0, count),
                    Vec::new(),
                )?;
                disclosures.push(match &edge.basis {
                    GraphCausalBasis::AuthorHypothesis { disclosure } => {
                        format!("Hypothesis: {disclosure}")
                    }
                    GraphCausalBasis::ClaimReference { claim_id, .. } => {
                        format!("Declared claim: {claim_id}")
                    }
                });
            }
            disclosures.join("\n")
        }
        GraphContent::SystemFlow {
            source,
            edges,
            events,
            ..
        } => {
            for edge in edges {
                let label_box = edge
                    .resolved_label_box(graph)
                    .map_err(|_| reject("canonical_graph_label_placement_rejected"))?;
                let channel = match edge.channel {
                    GraphFlowChannel::Data => "data",
                    GraphFlowChannel::Control => "control",
                    GraphFlowChannel::Event => "event",
                };
                let mut boundaries = BTreeSet::from([0, count]);
                let mut events_on_edge = Vec::new();
                for event in events.iter().filter(|event| event.edge_id == edge.id) {
                    let first = exact_frame(event.at, fps)?;
                    let duration = exact_frame(event.duration, fps)?;
                    let end = first
                        .checked_add(duration)
                        .ok_or_else(|| reject("graph_event_frame_overflow"))?;
                    if first >= end || end > count {
                        return Err(reject("graph_event_outside_graph"));
                    }
                    boundaries.insert(first);
                    boundaries.insert(end);
                    events_on_edge.push((event, first, end));
                }
                let boundaries = boundaries.into_iter().collect::<Vec<_>>();
                for (&first, &end) in boundaries.iter().zip(boundaries.iter().skip(1)) {
                    let mut text = format!("{} \u{00b7} {channel}", edge.label);
                    let mut active = Vec::new();
                    for (event, event_start, event_end) in &events_on_edge {
                        if first >= *event_start && first < *event_end {
                            text.push('\n');
                            text.push_str(&event.label);
                            active.push(event.id);
                        }
                    }
                    append_block(
                        &mut blocks,
                        GraphTextOwner::Edge { id: edge.id },
                        format!("graph-edge:{}:{}", graph.id, edge.id),
                        text,
                        &label_box,
                        (first, end),
                        active,
                    )?;
                }
            }
            match source {
                GraphSyntheticFlowSource::Synthetic { disclosure } => disclosure.clone(),
            }
        }
    };
    append_block(
        &mut blocks,
        GraphTextOwner::Disclosure,
        format!("graph:{}", graph.id),
        disclosure,
        &graph.style.disclosure_box,
        (0, count),
        Vec::new(),
    )?;
    // At most 32 vertices +64 edges +2*64 event boundaries +1 disclosure.
    // Source validation is the owner of those canonical budgets.
    if blocks.len() > 225 {
        return Err(reject("graph_text_block_count_exceeds_canonical_budget"));
    }
    Ok(blocks)
}

pub fn rounded_rectangle_radius(box_: &GraphBox) -> f64 {
    0.04 * box_.width.min(box_.height)
}

/// Exact Graph-v1 marker geometry, including the one-stroke-width overshoot.
/// Do not shorten a marker merely because the last segment is short.
pub fn arrowhead(points: &[GraphPoint], edge_width: f64) -> Result<[GraphPoint; 3]> {
    if points.len() < 2 || !edge_width.is_finite() || edge_width <= 0.0 {
        return Err(reject("graph_arrow_invalid_input"));
    }
    let end = &points[points.len() - 1];
    let previous = &points[points.len() - 2];
    let dx = end.x - previous.x;
    let dy = end.y - previous.y;
    let length = dx.hypot(dy);
    if !length.is_finite() || length <= 0.0 {
        return Err(reject("graph_arrow_degenerate_segment"));
    }
    let ux = dx / length;
    let uy = dy / length;
    let nx = -uy;
    let ny = ux;
    let tip = GraphPoint {
        x: end.x + edge_width * ux,
        y: end.y + edge_width * uy,
    };
    let left = GraphPoint {
        x: end.x - 9.0 * edge_width * ux + 5.0 * edge_width * nx,
        y: end.y - 9.0 * edge_width * uy + 5.0 * edge_width * ny,
    };
    let right = GraphPoint {
        x: end.x - 9.0 * edge_width * ux - 5.0 * edge_width * nx,
        y: end.y - 9.0 * edge_width * uy - 5.0 * edge_width * ny,
    };
    if [&tip, &left, &right]
        .iter()
        .any(|point| !point.x.is_finite() || !point.y.is_finite())
    {
        return Err(reject("graph_arrow_nonfinite_output"));
    }
    Ok([tip, left, right])
}

#[cfg(test)]
#[path = "semantic_graph_text_tests.rs"]
mod tests;
