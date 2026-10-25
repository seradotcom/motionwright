//! Pure original-source wire/shape oracles. These do not exercise native render,
//! grants, Project mutations, or SQLite, and grant none of those acceptance claims.
use motionwright_domain::*;
use serde_json::{Value, json};

#[path = "support/semantic_graph_source.rs"]
mod source;
use source::{id, raw_graph};

fn graph(flow: bool) -> SemanticGraph {
    serde_json::from_value(raw_graph(flow)).unwrap()
}
fn rate() -> Option<RationalTime> {
    Some(RationalTime { num: 30, den: 1 })
}

#[test]
fn original_causal_wire_roundtrips_without_nested_geometry_or_lost_null_intent() {
    let value = graph(false);
    value.validate_shape(rate()).unwrap();
    let saved = serde_json::to_value(&value).unwrap();
    assert!(saved["content"]["vertices"][0].get("box").is_some());
    assert!(saved["content"]["edges"][0].get("from_vertex_id").is_some());
    assert!(saved["content"]["edges"][0].get("geometry").is_none());
    assert_eq!(saved["brand_profile_ref"], Value::Null);
    assert_eq!(saved["content"]["vertices"][0]["target"], Value::Null);
    assert_eq!(
        serde_json::from_value::<SemanticGraph>(saved).unwrap(),
        value
    );
}

#[test]
fn unknown_members_and_missing_nullable_author_choices_are_not_silently_normalized() {
    let mut unknown = raw_graph(false);
    unknown["content"]["edges"][0]["native_approved"] = json!(true);
    assert!(serde_json::from_value::<SemanticGraph>(unknown).is_err());
    let mut no_brand = raw_graph(false);
    no_brand
        .as_object_mut()
        .unwrap()
        .remove("brand_profile_ref");
    assert!(serde_json::from_value::<SemanticGraph>(no_brand).is_err());
    let mut no_target = raw_graph(false);
    no_target["content"]["vertices"][0]
        .as_object_mut()
        .unwrap()
        .remove("target");
    assert!(serde_json::from_value::<SemanticGraph>(no_target).is_err());
}

#[test]
fn switching_motion_off_preserves_and_still_enforces_exact_reveal_dependencies() {
    let mut value = graph(false);
    value.motion = GraphMotionPreference::Off;
    value.validate_shape(rate()).unwrap();
    let original = value.clone();
    if let GraphContent::CausalDiagram { edges, .. } = &mut value.content {
        edges[0].appearance.at = RationalTime { num: 0, den: 1 };
    }
    assert!(value.validate_shape(rate()).is_err());
    assert_eq!(
        original.content.vertices()[0].appearance.duration,
        RationalTime { num: 1, den: 30 }
    );
}

#[test]
fn exact_half_open_flow_events_allow_adjacency_but_reject_overlap() {
    let mut value = graph(true);
    if let GraphContent::SystemFlow { events, .. } = &mut value.content {
        let mut second = events[0].clone();
        second.id = uuid::Uuid::parse_str(&id(14)).unwrap();
        second.at = RationalTime { num: 1, den: 10 };
        events.push(second);
    }
    value
        .validate_shape(Some(RationalTime { num: 60, den: 1 }))
        .unwrap();
    if let GraphContent::SystemFlow { events, .. } = &mut value.content {
        events[1].at = RationalTime { num: 1, den: 12 };
    }
    assert!(
        value
            .validate_shape(Some(RationalTime { num: 60, den: 1 }))
            .is_err()
    );
}

#[test]
fn synthetic_flow_cannot_claim_recorded_execution_or_bind_events_to_missing_edges() {
    let mut unknown = raw_graph(true);
    unknown["content"]["source"]["kind"] = json!("recorded_execution");
    assert!(serde_json::from_value::<SemanticGraph>(unknown).is_err());
    let mut value = graph(true);
    if let GraphContent::SystemFlow { events, .. } = &mut value.content {
        events[0].edge_id = uuid::Uuid::parse_str(&id(999)).unwrap();
    }
    assert!(value.validate_shape(rate()).is_err());
}

#[test]
fn a_deleted_or_reused_vertex_identity_cannot_silently_retarget_an_edge() {
    let mut deleted = graph(false);
    if let GraphContent::CausalDiagram { vertices, .. } = &mut deleted.content {
        vertices.pop();
    }
    assert!(deleted.validate_shape(rate()).is_err());
    let mut reused = graph(false);
    if let GraphContent::CausalDiagram { vertices, edges } = &mut reused.content {
        edges[0].id = vertices[0].id;
    }
    assert!(reused.validate_shape(rate()).is_err());
    let mut looped = graph(false);
    if let GraphContent::CausalDiagram { edges, .. } = &mut looped.content {
        edges[0].to_vertex_id = edges[0].from_vertex_id;
    }
    assert!(looped.validate_shape(rate()).is_err());
}

#[test]
fn invalid_geometry_is_rejected_without_clipping_or_moving_authored_points() {
    let mut value = graph(false);
    if let GraphContent::CausalDiagram { vertices, .. } = &mut value.content {
        vertices[0].r#box.width = 800.0;
    }
    let original = value.clone();
    assert!(value.validate_shape(rate()).is_err());
    assert_eq!(value, original);
    if let GraphContent::CausalDiagram { vertices, .. } = &mut value.content {
        vertices[0].r#box.width = f64::NAN;
    }
    assert!(value.validate_shape(rate()).is_err());
}

#[test]
fn subframe_times_and_unsafe_revision_numbers_remain_errors_instead_of_rounded_values() {
    let mut value = graph(false);
    value.time_range.start = RationalTime { num: 1, den: 100 };
    assert!(value.validate_shape(rate()).is_err());
    assert_eq!(value.time_range.start, RationalTime { num: 1, den: 100 });
    let mut value = graph(false);
    value.authorship.source_snapshot.revision = 9_007_199_254_740_992;
    assert!(value.validate_shape(rate()).is_err());
}

#[test]
fn review_resources_resolve_only_existing_parent_and_child_identities() {
    let graphs = vec![graph(false)];
    assert!(semantic_graph_resource_exists(
        &graphs,
        &format!("graph:{}", id(1))
    ));
    assert!(semantic_graph_resource_exists(
        &graphs,
        &format!("graph-vertex:{}:{}", id(1), id(10))
    ));
    assert!(semantic_graph_resource_exists(
        &graphs,
        &format!("graph-edge:{}:{}", id(1), id(12))
    ));
    assert!(!semantic_graph_resource_exists(
        &graphs,
        &format!("graph-edge:{}:{}", id(1), id(10))
    ));
    assert!(!semantic_graph_resource_exists(
        &graphs,
        &format!("graph-vertex:{}:{}", id(999), id(10))
    ));
    assert!(!semantic_graph_resource_exists(
        &graphs,
        &format!("graph:{}:extra", id(1))
    ));
    assert!(!semantic_graph_resource_exists(
        &graphs,
        &format!("graph:{}", id(1).replace('-', ""))
    ));
    assert!(!semantic_graph_resource_exists(
        &[],
        &format!("graph:{}", id(1))
    ));
}
