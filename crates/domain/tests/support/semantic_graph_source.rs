//! Authored scenario source only, shared by shape/domain/SQLite oracles.
//! No capability, ready context, persisted Project or native result is mocked.
use motionwright_domain::*;
use serde_json::{Value, json};
use uuid::Uuid;

pub fn id(value: u32) -> String {
    format!("00000000-0000-0000-0000-{value:012x}")
}

pub fn raw_graph(flow: bool) -> Value {
    let vertex = |value, x| {
        json!({
            "id": id(value), "label": "Authored concept", "meaning": "Explanation fixture",
            "unit": {"kind": "qualitative"}, "box": {"x": x, "y": 80.0, "width": 160.0, "height": 80.0},
            "shape": "rectangle", "target": null,
            "appearance": {"at": {"num": "0", "den": "1"}, "duration": {"num": "1", "den": "30"}}
        })
    };
    let mut edge = json!({
        "id": id(12), "label": "Declared relationship", "from_vertex_id": id(10), "to_vertex_id": id(11),
        "from_port": "right", "to_port": "left", "waypoints": [],
        "label_box": {"x": 250.0, "y": 180.0, "width": 120.0, "height": 40.0},
        "appearance": {"at": {"num": "1", "den": "30"}, "duration": {"num": "1", "den": "30"}}
    });
    let content = if flow {
        edge["channel"] = json!("data");
        json!({"kind": "system_flow", "source": {"kind": "synthetic", "disclosure": "Synthetic explanatory flow, not a recorded execution."},
            "vertices": [vertex(10, 40.0), vertex(11, 440.0)], "edges": [edge],
            "events": [{"id": id(13), "edge_id": id(12), "label": "Illustrated transfer",
                "at": {"num": "1", "den": "15"}, "duration": {"num": "1", "den": "30"}}]})
    } else {
        edge["influence"] = json!("unspecified");
        edge["basis"] = json!({"kind": "author_hypothesis", "disclosure": "Authored hypothesis, not established causation."});
        json!({"kind": "causal_diagram", "vertices": [vertex(10, 40.0), vertex(11, 440.0)], "edges": [edge]})
    };
    json!({"id": id(1), "scene_id": id(2), "component_version": 1,
        "subject_ref": {"kind": "scene", "scene_id": id(2)},
        "time_range": {"start": {"num": "0", "den": "1"}, "duration": {"num": "5", "den": "1"}},
        "brand_profile_ref": null, "variant_profile_ref": id(3), "reference_frame": {"width": 640, "height": 360},
        "motion": "full", "style": {"background": "#101820", "vertex_fill": "#263B4F", "text_color": "#FFFFFF",
            "edge_color": "#77C9D4", "accent_color": "#E9B872", "font_family": "Instrument Sans Variable",
            "font_size": 16.0, "edge_width": 2.0, "disclosure_box": {"x": 20.0, "y": 290.0, "width": 600.0, "height": 50.0}},
        "content": content, "authorship": {"author_label": "Source fixture author", "rationale": "Pure typed-shape verification",
            "source_snapshot": {"project_id": id(4), "generation": id(5), "branch_id": id(6), "revision": 7}}})
}

#[allow(dead_code)]
pub fn graph_for(project: &Project, flow: bool) -> SemanticGraph {
    let mut graph: SemanticGraph = serde_json::from_value(raw_graph(flow)).unwrap();
    let scene = &project.scenes[0];
    let profile = &project.deliverables[0];
    graph.id = Uuid::new_v4();
    graph.scene_id = scene.id;
    graph.subject_ref = GraphSubjectRef::Scene { scene_id: scene.id };
    graph.variant_profile_ref = profile.id;
    graph.reference_frame = GraphReferenceFrame {
        width: u64::from(profile.width),
        height: u64::from(profile.height),
    };
    graph.authorship.source_snapshot = GraphSourceSnapshot::from_project(project);
    graph
}

#[allow(dead_code)]
pub fn seed_domain() -> Project {
    let mut project = Project::new("Authored Graph domain scenario").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Explanatory relationships".into(),
            objective: "Persist exact source, without claiming native realization".into(),
            duration_seconds: 6,
        })
        .unwrap();
    project
        .apply_change(&Change::SetSceneRenderer {
            scene_id: project.scenes[0].id,
            renderer: RendererKind::ManimCommunity,
        })
        .unwrap();
    project
}

#[allow(dead_code)]
pub fn graph_with_alpha_identity(project: &Project) -> SemanticGraph {
    let mut graph = graph_for(project, false);
    // Deterministic alphabetic digits make each uppercase-alias oracle exact.
    graph.id = Uuid::parse_str("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap();
    graph
}

#[allow(dead_code)]
pub fn resource_spelling_cases(graph: &SemanticGraph) -> Vec<(String, Vec<String>)> {
    let parent = graph.id.to_string();
    let mut result = vec![(
        format!("graph:{parent}"),
        vec![format!("graph:{}", parent.to_uppercase())],
    )];
    for (kind, id) in [
        ("graph-vertex", graph.content.vertices()[0].id),
        ("graph-edge", graph.content.edge_ids()[0]),
    ] {
        let child = id.to_string();
        result.push((
            format!("{kind}:{parent}:{child}"),
            vec![
                format!("{kind}:{}:{child}", parent.to_uppercase()),
                format!("{kind}:{parent}:{}", child.to_uppercase()),
                format!("{kind}:{}:{}", parent.to_uppercase(), child.to_uppercase()),
            ],
        ));
    }
    result
}
