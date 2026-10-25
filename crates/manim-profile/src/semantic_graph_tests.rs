use super::*;
use motionwright_domain::{
    GraphAuthorship, GraphCausalBasis, GraphCausalEdge, GraphCausalInfluence, GraphFlowChannel,
    GraphFlowEdge, GraphFlowEvent, GraphReferenceFrame, GraphStyle, GraphSubjectRef,
    GraphSyntheticFlowSource, GraphTimeRange, GraphUnit,
};

fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}
fn time(num: i64, den: i64) -> RationalTime {
    RationalTime { num, den }
}
fn rect(x: f64, y: f64, width: f64, height: f64) -> GraphBox {
    GraphBox {
        x,
        y,
        width,
        height,
    }
}

fn fixture() -> ManimSemanticGraphPlan {
    let authored = GraphSourceSnapshot {
        project_id: id(1),
        generation: id(2),
        branch_id: id(3),
        revision: 7,
    };
    let mut current = authored.clone();
    current.revision = 8;
    let a = GraphVertex {
        id: id(10),
        label: "Origin\nA".into(),
        meaning: "Authored causal meaning".into(),
        unit: GraphUnit::Qualitative,
        r#box: rect(10.25, 20.5, 60.5, 40.25),
        shape: GraphVertexShape::Rectangle,
        target: None,
        appearance: GraphAppearance {
            at: time(0, 1),
            duration: time(1, 1),
        },
    };
    let b = GraphVertex {
        id: id(11),
        label: "Destination B".into(),
        meaning: "Authored destination".into(),
        unit: GraphUnit::Symbol {
            symbol: "count".into(),
        },
        r#box: rect(300.75, 20.5, 50.5, 50.5),
        shape: GraphVertexShape::Circle,
        target: None,
        appearance: GraphAppearance {
            at: time(0, 1),
            duration: time(1, 1),
        },
    };
    let edge = GraphFlowEdge {
        id: id(20),
        label: "Payload".into(),
        from_vertex_id: a.id,
        to_vertex_id: b.id,
        from_port: GraphPort::Right,
        to_port: GraphPort::Left,
        waypoints: vec![
            GraphPoint {
                x: 100.125,
                y: 40.625,
            },
            GraphPoint {
                x: 100.125,
                y: 80.25,
            },
        ],
        label_box: rect(150.0, 120.0, 150.0, 40.0),
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
            vertices: vec![a, b],
            edges: vec![edge],
            events: vec![GraphFlowEvent {
                id: id(40),
                edge_id: id(20),
                label: "One authored event".into(),
                at: time(2, 1),
                duration: time(1, 1),
            }],
        },
        authorship: GraphAuthorship {
            author_label: "Fixture author".into(),
            rationale: "Explicit source fixture".into(),
            source_snapshot: authored,
        },
    };
    ManimSemanticGraphPlan::new(
        current,
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

#[test]
fn canonical_source_and_historical_authorship_roundtrip_unchanged() {
    let plan = fixture();
    let original = serde_json::to_vec(&plan.source).unwrap();
    let bytes = plan.source_document().unwrap();
    let reopened = ManimSemanticGraphPlan::inspect_source_document(&bytes).unwrap();
    assert_eq!(serde_json::to_vec(&reopened.source).unwrap(), original);
    assert_eq!(reopened.source.authorship.source_snapshot.revision, 7);
    assert_eq!(reopened.execution_snapshot.revision, 8);
    assert_eq!(reopened, plan);
}

#[test]
fn graph_interval_and_scene_matte_are_half_open() {
    let plan = fixture();
    assert_eq!(plan.sample(19).unwrap().graph_local_frame, None);
    assert_eq!(plan.sample(19).unwrap().background_hex, "#000000");
    assert_eq!(plan.sample(20).unwrap().graph_local_frame, Some(0));
    assert_eq!(plan.sample(20).unwrap().background_hex, "#102030");
    assert_eq!(plan.sample(79).unwrap().graph_local_frame, Some(59));
    assert_eq!(plan.sample(80).unwrap().graph_local_frame, None);
    assert_eq!(plan.sample(80).unwrap().background_hex, "#000000");
}

#[test]
fn full_appearance_uses_exact_frame_progress() {
    let plan = fixture();
    assert_eq!(
        plan.sample(20).unwrap().vertices[0].opacity,
        GraphOpacity {
            numerator: 0,
            denominator: 10
        }
    );
    assert_eq!(
        plan.sample(25).unwrap().vertices[0].opacity,
        GraphOpacity {
            numerator: 5,
            denominator: 10
        }
    );
    assert_eq!(
        plan.sample(30).unwrap().vertices[0].opacity,
        GraphOpacity::visible()
    );
    assert_eq!(
        plan.sample(35).unwrap().edges[0].opacity,
        GraphOpacity {
            numerator: 5,
            denominator: 10
        }
    );
}

#[test]
fn reduced_motion_does_not_move_authored_events_or_edge_start() {
    let mut plan = fixture();
    plan.source.motion = GraphMotionPreference::Off;
    assert_eq!(
        plan.sample(19).unwrap().vertices[0].opacity,
        GraphOpacity::hidden()
    );
    assert_eq!(
        plan.sample(20).unwrap().vertices[0].opacity,
        GraphOpacity::visible()
    );
    assert_eq!(
        plan.sample(29).unwrap().edges[0].opacity,
        GraphOpacity::hidden()
    );
    assert_eq!(
        plan.sample(30).unwrap().edges[0].opacity,
        GraphOpacity::visible()
    );
    assert!(plan.sample(39).unwrap().active_events.is_empty());
    assert_eq!(plan.sample(40).unwrap().active_events, vec![id(40)]);
}

#[test]
fn authored_geometry_and_waypoints_are_not_rounded_or_scaled() {
    let plan = fixture();
    let frame = plan.sample(50).unwrap();
    assert_eq!(
        frame.vertices[0].authored_box,
        rect(10.25, 20.5, 60.5, 40.25)
    );
    assert_eq!(
        frame.edges[0].points,
        vec![
            GraphPoint {
                x: 70.75,
                y: 40.625
            },
            GraphPoint {
                x: 100.125,
                y: 40.625
            },
            GraphPoint {
                x: 100.125,
                y: 80.25
            },
            GraphPoint {
                x: 300.75,
                y: 45.75
            },
        ]
    );
}

#[test]
fn each_port_uses_the_authored_box() {
    let plan = fixture();
    let vertex = &plan.source.content.vertices()[0];
    assert_eq!(
        port_point(vertex, GraphPort::Top),
        GraphPoint { x: 40.5, y: 20.5 }
    );
    assert_eq!(
        port_point(vertex, GraphPort::Right),
        GraphPoint {
            x: 70.75,
            y: 40.625
        }
    );
    assert_eq!(
        port_point(vertex, GraphPort::Bottom),
        GraphPoint { x: 40.5, y: 60.75 }
    );
    assert_eq!(
        port_point(vertex, GraphPort::Left),
        GraphPoint {
            x: 10.25,
            y: 40.625
        }
    );
}

#[test]
fn flow_event_end_is_exclusive() {
    let plan = fixture();
    assert!(plan.sample(39).unwrap().active_events.is_empty());
    assert_eq!(plan.sample(40).unwrap().active_events, vec![id(40)]);
    assert_eq!(plan.sample(49).unwrap().active_events, vec![id(40)]);
    assert!(plan.sample(50).unwrap().active_events.is_empty());
}

#[test]
fn causal_edges_use_the_same_geometry_without_becoming_flow_events() {
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
            appearance: edge.appearance.clone(),
            influence: GraphCausalInfluence::Positive,
            basis: GraphCausalBasis::AuthorHypothesis {
                disclosure: "An authored hypothesis.".into(),
            },
        }],
    };
    let frame = plan.sample(50).unwrap();
    assert_eq!(frame.edges.len(), 1);
    assert!(frame.active_events.is_empty());
    assert_eq!(
        frame.edges[0].points[0],
        GraphPoint {
            x: 70.75,
            y: 40.625
        }
    );
}

#[test]
fn current_execution_can_preserve_a_different_historical_branch() {
    let mut plan = fixture();
    plan.execution_snapshot.branch_id = id(90);
    plan.execution_snapshot.generation = id(91);
    plan.validate().unwrap();
    let reopened =
        ManimSemanticGraphPlan::inspect_source_document(&plan.source_document().unwrap()).unwrap();
    assert_eq!(reopened.source.authorship.source_snapshot.branch_id, id(3));
    assert_eq!(reopened.execution_snapshot.branch_id, id(90));
    // This is source history preservation; the owner must still validate the
    // execution stamp and current dependency descriptors against its Project.
}

#[test]
fn edge_cannot_precede_full_vertex_visibility_even_with_motion_off() {
    let mut plan = fixture();
    plan.source.motion = GraphMotionPreference::Off;
    let GraphContent::SystemFlow { edges, .. } = &mut plan.source.content else {
        unreachable!()
    };
    edges[0].appearance.at = time(1, 2);
    assert!(plan.validate().is_err());
}

#[test]
fn event_cannot_precede_full_edge_visibility() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { events, .. } = &mut plan.source.content else {
        unreachable!()
    };
    events[0].at = time(3, 2);
    assert!(plan.validate().is_err());
}

#[test]
fn non_integral_frames_are_rejected_without_rounding() {
    let mut plan = fixture();
    plan.source.time_range.start = time(1, 3);
    assert!(plan.validate().is_err());
    assert!(exact_frame(time(1, 3), 10).is_err());
}

#[test]
fn fractional_fps_is_rejected_by_the_existing_native_clock() {
    assert!(
        ManimFrameClockV2::from_canonical(time(0, 1), time(10, 1), time(30_000, 1001),).is_err()
    );
}

#[test]
fn resized_reference_frame_requires_authored_review() {
    let mut plan = fixture();
    plan.source.reference_frame.width = 1280;
    assert!(plan.validate().is_err());
}

#[test]
fn out_of_frame_and_nonsquare_circle_are_rejected() {
    let mut plan = fixture();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[1].r#box.width = 50.25;
    assert!(plan.validate().is_err());
    let mut plan = fixture();
    let GraphContent::SystemFlow { vertices, .. } = &mut plan.source.content else {
        unreachable!()
    };
    vertices[0].r#box.x = 630.0;
    assert!(plan.validate().is_err());
}

#[test]
fn foreign_profile_and_unknown_fields_are_rejected_on_reopen() {
    let mut value = serde_json::to_value(fixture()).unwrap();
    value["profile_id"] = serde_json::json!("caller-invented-profile");
    assert!(
        ManimSemanticGraphPlan::inspect_source_document(&serde_json::to_vec(&value).unwrap())
            .is_err()
    );
    let mut value = serde_json::to_value(fixture()).unwrap();
    value["ready"] = serde_json::json!(true);
    assert!(
        ManimSemanticGraphPlan::inspect_source_document(&serde_json::to_vec(&value).unwrap())
            .is_err()
    );
}

#[test]
fn forged_clock_and_rate_are_rejected() {
    let mut plan = fixture();
    plan.scene_clock.frame_count += 1;
    assert!(plan.validate().is_err());
    let mut plan = fixture();
    plan.render_profile.frame_rate = 20;
    assert!(plan.validate().is_err());
}

#[test]
fn sample_bounds_and_project_offset_are_exact() {
    let plan = fixture();
    assert_eq!(plan.sample(0).unwrap().project_frame, 200);
    assert_eq!(plan.sample(99).unwrap().project_frame, 299);
    assert!(plan.sample(100).is_err());
}

#[test]
fn typography_requirement_does_not_substitute_the_css_alias() {
    let plan = fixture();
    let requirement = plan.typography_requirement();
    assert_eq!(requirement.editorial_family, "Instrument Sans Variable");
    assert_eq!(requirement.native_family, "Instrument Sans");
    assert_eq!(requirement.weight, 400);
    assert!(!requirement.fallback_allowed);
    let mut plan = plan;
    plan.source.style.font_family = GraphFontFamily::IbmPlexMono;
    assert_eq!(plan.typography_requirement().native_family, "IBM Plex Mono");
}

#[test]
fn edited_source_roundtrips_without_modifying_the_original() {
    let original = fixture();
    let mut edited = original.clone();
    let GraphContent::SystemFlow { vertices, .. } = &mut edited.source.content else {
        unreachable!()
    };
    vertices[0].label = "Edited label\nUnicode: \u{00e9}".into();
    let reopened =
        ManimSemanticGraphPlan::inspect_source_document(&edited.source_document().unwrap())
            .unwrap();
    assert_eq!(reopened, edited);
    assert_eq!(original.source.content.vertices()[0].label, "Origin\nA");
}

#[test]
fn oversized_source_input_is_rejected_before_deserialization() {
    assert!(ManimSemanticGraphPlan::inspect_source_document(
        &vec![b' '; MAX_SOURCE_ENTRY_BYTES_V2 + 1],
    ).is_err());
}

#[test]
fn graph_may_not_extend_past_the_scene() {
    let mut plan = fixture();
    plan.source.time_range.start = time(5, 1);
    assert!(plan.validate().is_err());
}

#[test]
fn native_font_size_bounds_are_explicit_without_changing_canonical_source() {
    for font_size in [3.999, 512.001] {
        let mut plan = fixture();
        plan.source.style.font_size = font_size;
        plan.source
            .validate_shape(Some(plan.scene_clock.frame_rate))
            .unwrap();
        let original_source = plan.source.clone();
        assert!(plan.validate().is_err());
        assert_eq!(plan.source, original_source);
    }
    for font_size in [4.0, 512.0] {
        let mut plan = fixture();
        plan.source.style.font_size = font_size;
        plan.validate().unwrap();
        assert_eq!(plan.source.style.font_size, font_size);
    }
}

#[test]
fn graph_projection_respects_the_existing_native5_writer_pixel_budget() {
    for (width, height) in [(4098, 360), (640, 4098), (4096, 2162)] {
        let mut plan = fixture();
        plan.render_profile.width = width;
        plan.render_profile.height = height;
        plan.source.reference_frame.width = u64::from(width);
        plan.source.reference_frame.height = u64::from(height);
        plan.render_profile.validate().unwrap();
        plan.source
            .validate_shape(Some(plan.scene_clock.frame_rate))
            .unwrap();
        assert!(plan.validate().is_err());
    }
    let mut plan = fixture();
    plan.render_profile.width = 4096;
    plan.render_profile.height = 2160;
    plan.source.reference_frame.width = 4096;
    plan.source.reference_frame.height = 2160;
    plan.validate().unwrap();
    assert_eq!(
        u64::from(plan.render_profile.width) * u64::from(plan.render_profile.height),
        8_847_360
    );
}
