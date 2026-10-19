//! Authored hard lines are lowered to native editable text leaves without
//! silently changing unchanged single-line projects or claiming pixel equivalence.
use motionwright_domain::{BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, Project};
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{
    Archetype, NarrativeRole, Shot, SpatialIntent, SubjectContent, VisualConstraint,
};
use semwright_native_sdk::ErrorCode;
use std::collections::BTreeSet;
use uuid::Uuid;

fn fixture(text: &str, height: f64) -> (Project, Uuid, Uuid, FilmBuildOptions) {
    let mut project = Project::new("Attributed authored text projection").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Editable typography".into(),
            objective: "Preserve saved line advance".into(),
            duration_seconds: 2,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    let node_id = Uuid::new_v4();
    project
        .apply_change(&Change::AddCanvasNode {
            scene_id,
            node: CanvasNode {
                id: node_id,
                name: "Text with authored breaks".into(),
                kind: "text".into(),
                parent_id: None,
                x: 160.0,
                y: 180.0,
                width: 640.0,
                height,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: Some(text.into()),
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 1,
                style: NodeStyle {
                    fill: Some("#F5F5F2".into()),
                    stroke: None,
                    stroke_width: 0.0,
                    font_family: Some(MOTION_CANVAS_FONT_FAMILY.into()),
                    font_size: Some(44.0),
                    font_weight: Some(700),
                    line_height: Some(1.1),
                    blend_mode: BlendMode::Normal,
                },
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            },
        })
        .unwrap();
    let profile_id = project.deliverables[0].id;
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1).unwrap(),
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
        }],
    };
    (project, node_id, profile_id, options)
}

fn projected_shot(project: &Project, profile_id: Uuid, options: &FilmBuildOptions) -> Shot {
    let segments = build_motion_canvas_segments(project, profile_id, options).unwrap();
    assert_eq!(segments.len(), 1);
    assert_eq!(segments[0].frame_count, 60);
    segments[0].film.sequences[0].beats[0].shots[0].clone()
}

fn line<'a>(shot: &'a Shot, index: usize) -> &'a semwright_motion_authoring::Subject {
    shot.subjects
        .iter()
        .find(|item| item.id.ends_with(&format!("-line-{index}")))
        .expect("authored nonempty line is retained")
}

fn text_content(content: &SubjectContent) -> &str {
    match content {
        SubjectContent::Text { runs, .. } => runs[0].text.as_str(),
        other => panic!("Expected native text, found {other:?}"),
    }
}

fn fixed_y(subject: &semwright_motion_authoring::Subject) -> f64 {
    match &subject.layout {
        SpatialIntent::Fixed { position, .. } => position.y,
        other => panic!("Expected fixed layout, found {other:?}"),
    }
}

#[test]
fn multiline_preserves_saved_44_by_1_1_advance_and_editable_identity() {
    let (project, node_id, profile_id, options) = fixture("Primera\nSegunda", 180.0);
    let original = serde_json::to_value(&project).unwrap();
    let shot = projected_shot(&project, profile_id, &options);
    assert_eq!(shot.subjects.len(), 3);
    let root = &shot.subjects[0];
    assert_eq!(root.role, "text");
    assert!(matches!(root.content, SubjectContent::Group));
    let first = line(&shot, 0);
    let second = line(&shot, 1);
    assert_eq!(text_content(&first.content), "Primera");
    assert_eq!(text_content(&second.content), "Segunda");
    assert_eq!(first.parent.as_deref(), Some(root.id.as_str()));
    assert_eq!(second.parent.as_deref(), Some(root.id.as_str()));
    assert_eq!(first.layer, root.layer);
    assert_eq!(second.layer, root.layer);
    assert_eq!(root.id, format!("node-{}", node_id.simple()));
    assert!((fixed_y(second) - fixed_y(first) - 48.4).abs() < 1e-8);
    assert!(shot.constraints.iter().any(|constraint| {
        matches!(constraint, VisualConstraint::FontLoaded { subject } if subject == &first.id)
    }));
    assert_eq!(serde_json::to_value(&project).unwrap(), original);
}

#[test]
fn profile_override_is_resolved_before_hard_line_lowering() {
    let (mut project, node_id, profile_id, options) = fixture("Origen", 180.0);
    let before = projected_shot(&project, profile_id, &options);
    assert_eq!(before.subjects.len(), 1);
    assert_eq!(text_content(&before.subjects[0].content), "Origen");
    project.deliverables[0]
        .text_overrides
        .insert(node_id, "Uno\nDos".into());
    let override_shot = projected_shot(&project, profile_id, &options);
    assert_eq!(override_shot.subjects.len(), 3);
    assert_eq!(text_content(&line(&override_shot, 0).content), "Uno");
    assert_eq!(text_content(&line(&override_shot, 1).content), "Dos");
    assert_eq!(project.scenes[0].nodes[0].text.as_deref(), Some("Origen"));
}

#[test]
fn blank_authored_line_preserves_its_original_index_and_advance() {
    let (project, _, profile_id, options) = fixture("Uno\n\nTres", 180.0);
    let shot = projected_shot(&project, profile_id, &options);
    assert_eq!(shot.subjects.len(), 3);
    assert!(
        shot.subjects
            .iter()
            .all(|subject| !subject.id.ends_with("-line-1"))
    );
    assert_eq!(text_content(&line(&shot, 2).content), "Tres");
    assert!((fixed_y(line(&shot, 2)) - fixed_y(line(&shot, 0)) - 96.8).abs() < 1e-8);
}

#[test]
fn authored_lines_do_not_silently_shrink_native_glyph_boxes() {
    // Two baselines need 96.8 logical units but actual retained line boxes
    // require 48.4 + (44 * 1.4) = 110 native units.
    let (project, _, profile_id, options) = fixture("Uno\nDos", 104.0);
    let failure = build_motion_canvas_segments(&project, profile_id, &options).unwrap_err();
    assert_eq!(failure.code, ErrorCode::Unsupported);
    assert!(failure.message.contains("glyph boxes"));
}

#[test]
fn unsupported_carriage_return_does_not_become_a_different_line() {
    let (project, _, profile_id, options) = fixture("Uno\r\nDos", 180.0);
    let failure = build_motion_canvas_segments(&project, profile_id, &options).unwrap_err();
    assert_eq!(failure.code, ErrorCode::Unsupported);
    assert!(failure.message.contains("carriage returns"));
}

#[test]
fn unchanged_single_line_retains_original_native_text_hierarchy() {
    let (project, _, profile_id, options) = fixture("Texto simple", 180.0);
    let shot = projected_shot(&project, profile_id, &options);
    assert_eq!(shot.subjects.len(), 1);
    assert_eq!(shot.subjects[0].role, "text");
    assert_eq!(text_content(&shot.subjects[0].content), "Texto simple");
    assert!(
        shot.subjects
            .iter()
            .all(|subject| !subject.id.contains("-line-"))
    );
}

#[test]
fn authored_line_advance_scales_with_the_selected_output_profile() {
    let (mut project, _, profile_id, options) = fixture("Uno\nDos", 180.0);
    project.deliverables[0].width = 960;
    project.deliverables[0].height = 540;
    let shot = projected_shot(&project, profile_id, &options);
    assert_eq!(shot.subjects.len(), 3);
    assert!((fixed_y(line(&shot, 1)) - fixed_y(line(&shot, 0)) - 24.2).abs() < 1e-8);
}
