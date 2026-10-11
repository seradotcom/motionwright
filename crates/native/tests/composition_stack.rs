//! Cross-workstream native Film acceptance: Stage, procedural original geometry
//! and source-labeled measurements occupy one timeline and one deliverable.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole, realize};
use std::collections::BTreeSet;
use uuid::Uuid;

fn add_scene(project: &mut Project, label: &str) -> Uuid {
    project
        .apply_change(&Change::AddScene {
            name: label.into(),
            objective: format!("Original independent {label}"),
            duration_seconds: 6,
        })
        .unwrap();
    project.scenes.last().unwrap().id
}
#[test]
fn one_edited_film_projects_three_original_source_families_without_flattening() {
    let mut p = Project::new("Source-bound compositional Film").unwrap();
    let stage_scene = add_scene(&mut p, "Generic product");
    let procedural_scene = add_scene(&mut p, "Deterministic geometry");
    let measured_scene = add_scene(&mut p, "Declared measurement");
    let stage_id = Uuid::parse_str("00000000-0000-4000-8000-000000000005").unwrap();
    p.apply_change(&Change::UpsertProductStage {
        instance_id: stage_id,
        scene_id: stage_scene,
        config: ProductStageConfig {
            author_attestation: "I authored the original test-only generic screen.".into(),
            animate_focus: true,
            ..Default::default()
        },
    })
    .unwrap();
    let procedural_id = Uuid::parse_str("00000000-0000-4000-8000-000000000095").unwrap();
    p.apply_change(&Change::UpsertProceduralField {
        instance_id: procedural_id,
        scene_id: procedural_scene,
        config: ProceduralConfig {
            count: 8,
            reveal_step_frames: 3,
            reveal_duration_frames: 12,
            ..Default::default()
        },
    })
    .unwrap();
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "Declared synthetic unit fixture".into(),
        media_type: "application/json".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: None,
    };
    p.apply_change(&Change::AddAsset {
        asset: asset.clone(),
    })
    .unwrap();
    let table_id = Uuid::new_v4();
    p.apply_change(&Change::UpsertMeasuredTable {
        table: MeasuredTable {
            schema: MEASURED_DATA_SCHEMA.into(),
            id: table_id,
            label: "Original source table".into(),
            version: 1,
            source_asset_id: asset.id,
            source_sha256: asset.content_sha256.unwrap(),
            rows: vec![MeasuredDatum {
                key: "rate".into(),
                label: "Synthetic rate".into(),
                milli: 37500,
                unit: MeasuredUnit::Percent,
            }],
        },
    })
    .unwrap();
    let metric = CanvasNode {
        id: Uuid::new_v4(),
        name: "Author-sourced text label".into(),
        kind: "text".into(),
        parent_id: None,
        x: 270.0,
        y: 260.0,
        width: 840.0,
        height: 108.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Source placeholder".into()),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle {
            fill: Some("#F2F4F3".into()),
            font_family: Some(MOTION_CANVAS_FONT_FAMILY.into()),
            font_size: Some(48.0),
            font_weight: Some(400),
            ..Default::default()
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    };
    p.apply_change(&Change::AddCanvasNode {
        scene_id: measured_scene,
        node: metric.clone(),
    })
    .unwrap();
    p.apply_change(&Change::UpsertMeasuredBinding {
        binding_id: Uuid::new_v4(),
        scene_id: measured_scene,
        node_id: metric.id,
        table_id,
        datum_key: "rate".into(),
        view: MeasuredView::Label { decimals: 1 },
    })
    .unwrap();
    let scenes = p.scenes.iter().map(|scene| scene.id).collect::<Vec<_>>();
    let options = FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: scenes
            .iter()
            .map(|id| SceneFilmIntent {
                scene_id: *id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::ObjectSpotlight,
            })
            .collect(),
    };
    let segments = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    assert_eq!(
        segments.len(),
        1,
        "one contiguous native Motion Canvas segment"
    );
    let film = &segments[0].film;
    assert_eq!(segments[0].scene_ids, scenes);
    assert_eq!(segments[0].frame_count, 540, "18 seconds / 30 fps");
    let shots = film
        .sequences
        .iter()
        .flat_map(|sequence| sequence.beats.iter().flat_map(|beat| beat.shots.iter()))
        .collect::<Vec<_>>();
    assert_eq!(shots.len(), 3, "three source editable Film shots");
    assert_eq!(shots[0].layers.len(), STAGE_ROLES.len());
    assert_eq!(shots[1].layers.len(), 8);
    assert!(shots[0].subjects.iter().any(|item| {
        item.id
            .contains(&stage_node_id(stage_id, "disclosure").simple().to_string())
    }));
    assert!(shots[1].subjects.iter().any(|item| {
        item.id
            .contains(&procedural_node_id(procedural_id, 0).simple().to_string())
    }));
    assert!(
        shots[2]
            .subjects
            .iter()
            .any(|item| item.role == "component-text-line")
    );
    assert!(realize(film).is_ok());
    // Source project remains editable; projection may never mutate source.
    assert_eq!(p.production_design.stages.len(), 1);
    assert_eq!(p.production_design.procedural_fields.len(), 1);
    assert_eq!(p.production_design.measured_bindings.len(), 1);
    assert_eq!(p.scenes[2].nodes[0].id, metric.id);
}
