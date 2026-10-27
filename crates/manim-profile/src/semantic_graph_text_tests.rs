use super::*;
use crate::{ManimFrameClockV2, ManimMatteV2, ManimRenderProfile};
use motionwright_domain::{
    GraphAuthorship, GraphCausalEdge, GraphFlowEdge, GraphFlowEvent, GraphFontFamily,
    GraphMotionPreference, GraphPort, GraphReferenceFrame, GraphSourceSnapshot, GraphStyle,
    GraphSubjectRef, GraphTimeRange, GraphVertex, GraphVertexShape, RationalTime, SemanticGraph,
};

fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}
fn time(num: i64, den: i64) -> RationalTime {
    RationalTime::new(num, den).unwrap()
}
fn rect(x: f64, y: f64, width: f64, height: f64) -> GraphBox {
    GraphBox {
        x,
        y,
        width,
        height,
    }
}
fn event(value: u128, at: i64, duration: i64, label: &str) -> GraphFlowEvent {
    GraphFlowEvent {
        id: id(value),
        edge_id: id(20),
        label: label.into(),
        at: time(at, 10),
        duration: time(duration, 10),
    }
}
fn fixture() -> ManimSemanticGraphPlan {
    let snapshot = GraphSourceSnapshot {
        project_id: id(1),
        generation: id(2),
        branch_id: id(3),
        revision: 7,
    };
    let vertices = vec![
        GraphVertex {
            id: id(10),
            label: " Origin\nA ".into(),
            meaning: "Authored origin".into(),
            unit: GraphUnit::Qualitative,
            r#box: rect(10.0, 20.0, 60.0, 40.0),
            shape: GraphVertexShape::Rectangle,
            target: None,
            appearance: GraphAppearance {
                at: time(0, 1),
                duration: time(1, 1),
            },
        },
        GraphVertex {
            id: id(11),
            label: "Destination B".into(),
            meaning: "Authored destination".into(),
            unit: GraphUnit::Symbol {
                symbol: "count".into(),
            },
            r#box: rect(300.0, 20.0, 50.0, 50.0),
            shape: GraphVertexShape::Circle,
            target: None,
            appearance: GraphAppearance {
                at: time(0, 1),
                duration: time(1, 1),
            },
        },
    ];
    let edge = GraphFlowEdge {
        id: id(20),
        label: "Payload".into(),
        from_vertex_id: id(10),
        to_vertex_id: id(11),
        from_port: GraphPort::Right,
        to_port: GraphPort::Left,
        waypoints: vec![GraphPoint { x: 100.0, y: 40.0 }],
        label_box: rect(100.0, 100.0, 240.0, 100.0),
        label_placement: None,
        appearance: GraphAppearance {
            at: time(1, 1),
            duration: time(1, 1),
        },
        channel: GraphFlowChannel::Data,
    };
    let source = SemanticGraph {
        id: id(30),
        scene_id: id(4),
        component_version: 1,
        subject_ref: GraphSubjectRef::Scene { scene_id: id(4) },
        time_range: GraphTimeRange {
            start: time(2, 1),
            duration: time(6, 1),
        },
        brand_profile_ref: None,
        variant_profile_ref: id(5),
        reference_frame: GraphReferenceFrame {
            width: 640,
            height: 360,
        },
        motion: GraphMotionPreference::Full,
        style: GraphStyle {
            background: "#102030".into(),
            vertex_fill: "#405060".into(),
            text_color: "#ffffff".into(),
            edge_color: "#abcdef".into(),
            accent_color: "#fedcba".into(),
            font_family: GraphFontFamily::InstrumentSansVariable,
            font_size: 20.0,
            edge_width: 2.0,
            disclosure_box: rect(20.0, 280.0, 600.0, 60.0),
        },
        content: GraphContent::SystemFlow {
            source: GraphSyntheticFlowSource::Synthetic {
                disclosure: "Synthetic flow; no recorded execution.".into(),
            },
            vertices,
            edges: vec![edge],
            events: vec![event(40, 20, 10, "First event")],
        },
        authorship: GraphAuthorship {
            author_label: "Source fixture".into(),
            rationale: "Explicit canonical DTO".into(),
            source_snapshot: snapshot.clone(),
        },
    };
    let mut execution = snapshot;
    execution.revision = 8;
    ManimSemanticGraphPlan::new(
        execution,
        ManimFrameClockV2::from_canonical(time(20, 1), time(10, 1), time(10, 1)).unwrap(),
        ManimRenderProfile {
            width: 640,
            height: 360,
            frame_rate: 10,
        },
        ManimMatteV2::default(),
        source,
    )
    .unwrap()
}
fn edge_blocks(blocks: &[GraphTextBlock]) -> Vec<&GraphTextBlock> {
    blocks
        .iter()
        .filter(|block| matches!(block.owner, GraphTextOwner::Edge { .. }))
        .collect()
}

#[test]
fn vertex_unit_and_literal_whitespace_are_preserved() {
    let blocks = text_blocks(&fixture()).unwrap();
    assert_eq!(blocks[0].text, " Origin\nA ");
    assert_eq!(blocks[1].text, "Destination B (count)");
    assert_eq!(blocks[0].authored_box, rect(10.0, 20.0, 60.0, 40.0));
    assert_eq!(blocks[0].active_start_frame, 0);
    assert_eq!(blocks[0].active_end_frame_exclusive, 60);
}

#[test]
fn all_flow_intervals_include_pre_event_and_post_event_text() {
    let blocks = text_blocks(&fixture()).unwrap();
    let edges = edge_blocks(&blocks);
    assert_eq!(
        edges
            .iter()
            .map(|b| (b.active_start_frame, b.active_end_frame_exclusive))
            .collect::<Vec<_>>(),
        vec![(0, 20), (20, 30), (30, 60)]
    );
    assert_eq!(edges[0].text, "Payload \u{00b7} data");
    assert_eq!(edges[1].text, "Payload \u{00b7} data\nFirst event");
    assert_eq!(edges[2].text, edges[0].text);
    assert_eq!(edges[1].active_event_ids, vec![id(40)]);
    assert!(edges[0].active_event_ids.is_empty());
    assert!(edges[2].active_event_ids.is_empty());
}

#[test]
fn overlapping_events_on_one_edge_are_rejected_by_canonical_source() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { events, .. } = &mut plan.source.content else {
        unreachable!()
    };
    *events = vec![
        event(90, 25, 10, "Second arrival first in source"),
        event(40, 20, 20, "First arrival second in source"),
    ];
    let original_source = plan.source.clone();
    assert!(plan.validate().is_err());
    assert!(text_blocks(&plan).is_err());
    assert_eq!(plan.source, original_source);
}

#[test]
fn coincident_boundaries_are_unique_and_intervals_are_half_open() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { events, .. } = &mut plan.source.content else {
        unreachable!()
    };
    *events = vec![event(40, 20, 10, "A"), event(41, 30, 10, "B")];
    let blocks = text_blocks(&plan).unwrap();
    let edges = edge_blocks(&blocks);
    assert_eq!(edges.len(), 4);
    assert_eq!(edges[2].active_start_frame, 30);
    assert_eq!(edges[2].active_event_ids, vec![id(41)]);
    assert!(
        edges
            .iter()
            .all(|b| b.active_start_frame < b.active_end_frame_exclusive)
    );
}

#[test]
fn final_event_does_not_create_a_label_outside_the_authored_graph() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { events, .. } = &mut plan.source.content else {
        unreachable!()
    };
    *events = vec![event(40, 50, 10, "At tail")];
    let blocks = text_blocks(&plan).unwrap();
    let edges = edge_blocks(&blocks);
    assert_eq!(edges.len(), 2);
    assert_eq!(edges[1].active_end_frame_exclusive, 60);
    assert_eq!(edges[1].active_event_ids, vec![id(40)]);
}

#[test]
fn every_flow_channel_uses_its_exact_source_label() {
    for (channel, expected) in [
        (GraphFlowChannel::Data, "data"),
        (GraphFlowChannel::Control, "control"),
        (GraphFlowChannel::Event, "event"),
    ] {
        let mut plan = fixture();
        let GraphContent::SystemFlow { edges, events, .. } = &mut plan.source.content else {
            unreachable!()
        };
        edges[0].channel = channel;
        events.clear();
        let blocks = text_blocks(&plan).unwrap();
        assert_eq!(
            edge_blocks(&blocks)[0].text,
            format!("Payload \u{00b7} {expected}")
        );
    }
}

#[test]
fn causal_minus_and_disclosures_use_the_frozen_presentation() {
    let mut plan = fixture();
    let GraphContent::SystemFlow {
        vertices, edges, ..
    } = plan.source.content.clone()
    else {
        unreachable!()
    };
    let edge = &edges[0];
    plan.source.content = GraphContent::CausalDiagram {
        vertices,
        edges: vec![GraphCausalEdge {
            id: edge.id,
            label: edge.label.clone(),
            from_vertex_id: edge.from_vertex_id,
            to_vertex_id: edge.to_vertex_id,
            from_port: edge.from_port,
            to_port: edge.to_port,
            waypoints: edge.waypoints.clone(),
            label_box: edge.label_box.clone(),
            label_placement: None,
            appearance: edge.appearance.clone(),
            influence: GraphCausalInfluence::Negative,
            basis: GraphCausalBasis::AuthorHypothesis {
                disclosure: "Explicit hypothesis\nSecond line".into(),
            },
        }],
    };
    let blocks = text_blocks(&plan).unwrap();
    assert_eq!(edge_blocks(&blocks)[0].text, "Payload \u{00b7} \u{2212}");
    assert_eq!(
        blocks.last().unwrap().text,
        "Hypothesis: Explicit hypothesis\nSecond line"
    );
}

#[test]
fn event_lines_and_final_lf_are_not_trimmed() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { events, .. } = &mut plan.source.content else {
        unreachable!()
    };
    events[0].label = "  Event \n\n".into();
    let blocks = text_blocks(&plan).unwrap();
    assert_eq!(
        edge_blocks(&blocks)[1].text,
        "Payload \u{00b7} data\n  Event \n\n"
    );
}

#[test]
fn lowering_does_not_rewrite_source_history_or_execution_stamp() {
    let plan = fixture();
    let before = serde_json::to_vec(&plan).unwrap();
    let blocks = text_blocks(&plan).unwrap();
    assert_eq!(serde_json::to_vec(&plan).unwrap(), before);
    assert_eq!(plan.source.authorship.source_snapshot.revision, 7);
    assert_eq!(plan.execution_snapshot.revision, 8);
    assert_eq!(blocks.last().unwrap().resource, format!("graph:{}", id(30)));
    for (index, block) in blocks.iter().enumerate() {
        assert_eq!(usize::from(block.ordinal), index);
    }
}

#[test]
fn derived_text_limit_rejects_without_truncation_or_partial_row() {
    let mut blocks = Vec::new();
    let text = "x".repeat(16 * 1024 + 1);
    assert!(
        append_block(
            &mut blocks,
            GraphTextOwner::Disclosure,
            "resource".into(),
            text,
            &rect(0.0, 0.0, 1.0, 1.0),
            (0, 1),
            Vec::new()
        )
        .is_err()
    );
    assert!(blocks.is_empty());
}

#[test]
fn appearance_lowering_rejects_zero_duration_and_fractional_frame() {
    assert!(
        appearance_frames(
            &GraphAppearance {
                at: time(0, 1),
                duration: time(0, 1)
            },
            30
        )
        .is_err()
    );
    assert!(
        appearance_frames(
            &GraphAppearance {
                at: time(1, 3),
                duration: time(1, 1)
            },
            10
        )
        .is_err()
    );
    assert!(
        appearance_frames(
            &GraphAppearance {
                at: time(0, 1),
                duration: time(1, 1)
            },
            0
        )
        .is_err()
    );
    assert_eq!(
        appearance_frames(
            &GraphAppearance {
                at: time(1, 2),
                duration: time(3, 2)
            },
            30
        )
        .unwrap(),
        GraphAppearanceFrames {
            start_frame: 15,
            duration_frames: 45
        }
    );
}

#[test]
fn horizontal_arrow_preserves_reference_point_and_overshoot() {
    let points = [
        GraphPoint { x: 10.0, y: 20.0 },
        GraphPoint { x: 30.0, y: 20.0 },
    ];
    assert_eq!(
        arrowhead(&points, 2.0).unwrap(),
        [
            GraphPoint { x: 32.0, y: 20.0 },
            GraphPoint { x: 12.0, y: 30.0 },
            GraphPoint { x: 12.0, y: 10.0 },
        ]
    );
}

#[test]
fn vertical_arrow_is_rotated_without_changing_scale() {
    let points = [
        GraphPoint { x: 10.0, y: 10.0 },
        GraphPoint { x: 10.0, y: 30.0 },
    ];
    assert_eq!(
        arrowhead(&points, 2.0).unwrap(),
        [
            GraphPoint { x: 10.0, y: 32.0 },
            GraphPoint { x: 0.0, y: 12.0 },
            GraphPoint { x: 20.0, y: 12.0 },
        ]
    );
}

#[test]
fn short_final_segment_does_not_shrink_the_arrow() {
    let points = [
        GraphPoint { x: 9.75, y: 20.0 },
        GraphPoint { x: 10.0, y: 20.0 },
    ];
    assert_eq!(
        arrowhead(&points, 2.0).unwrap()[1],
        GraphPoint { x: -8.0, y: 30.0 }
    );
}

#[test]
fn degenerate_and_nonfinite_arrow_geometry_is_rejected() {
    let point = GraphPoint { x: 1.0, y: 2.0 };
    assert!(arrowhead(&[], 2.0).is_err());
    assert!(arrowhead(&[point.clone(), point.clone()], 2.0).is_err());
    assert!(
        arrowhead(
            &[
                point.clone(),
                GraphPoint {
                    x: f64::INFINITY,
                    y: 2.0
                }
            ],
            2.0
        )
        .is_err()
    );
    assert!(arrowhead(&[point, GraphPoint { x: 3.0, y: 2.0 }], 0.0).is_err());
}

#[test]
fn rounded_rectangle_radius_uses_the_smaller_authored_dimension() {
    assert_eq!(
        rounded_rectangle_radius(&rect(10.25, 20.5, 60.5, 40.25)),
        1.61
    );
    assert_eq!(rounded_rectangle_radius(&rect(0.0, 0.0, 40.25, 60.5)), 1.61);
}
