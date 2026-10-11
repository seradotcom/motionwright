//! Projection gate: a bound source value creates ordinary native semantic
//! Canvas text and a rectangle, never an opaque "chart" screenshot.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole, realize};
use std::collections::BTreeSet;
use uuid::Uuid;
#[test]
fn source_bound_metrics_project_as_editable_native_text_and_rectangle() {
    let mut p = Project::new("Measured Film projection").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Metric".into(),
        objective: "Only explicitly source-bound numeric text".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scene_id = p.scenes[0].id;
    let asset = Asset {
        id: Uuid::new_v4(),
        name: "Source JSON".into(),
        media_type: "application/json".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: None,
    };
    p.apply_change(&Change::AddAsset {
        asset: asset.clone(),
    })
    .unwrap();
    let table = MeasuredTable {
        schema: MEASURED_DATA_SCHEMA.into(),
        id: Uuid::new_v4(),
        label: "Source-backed test table".into(),
        version: 1,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        rows: vec![MeasuredDatum {
            key: "rate".into(),
            label: "Measured rate".into(),
            milli: 25000,
            unit: MeasuredUnit::Percent,
        }],
    };
    p.apply_change(&Change::UpsertMeasuredTable {
        table: table.clone(),
    })
    .unwrap();
    let text = CanvasNode {
        id: Uuid::new_v4(),
        name: "Measured label".into(),
        kind: "text".into(),
        parent_id: None,
        x: 280.0,
        y: 250.0,
        width: 830.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Before binding".into()),
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
    let bar = CanvasNode {
        id: Uuid::new_v4(),
        name: "Explicit 100%-range bar".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 290.0,
        y: 420.0,
        width: 500.0,
        height: 40.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle {
            fill: Some("#A5C8DF".into()),
            ..Default::default()
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    };
    for node in [text.clone(), bar.clone()] {
        p.apply_change(&Change::AddCanvasNode { scene_id, node })
            .unwrap();
    }
    p.apply_change(&Change::UpsertMeasuredBinding {
        binding_id: Uuid::new_v4(),
        scene_id,
        node_id: text.id,
        table_id: table.id,
        datum_key: "rate".into(),
        view: MeasuredView::Label { decimals: 1 },
    })
    .unwrap();
    p.apply_change(&Change::UpsertMeasuredBinding {
        binding_id: Uuid::new_v4(),
        scene_id,
        node_id: bar.id,
        table_id: table.id,
        datum_key: "rate".into(),
        view: MeasuredView::Bar {
            axis_min_milli: 0,
            axis_max_milli: 100000,
            max_width_px: 500,
        },
    })
    .unwrap();
    assert_eq!(
        p.scenes[0].nodes[0].text.as_deref(),
        Some("Measured rate · 25.0%")
    );
    assert_eq!(p.scenes[0].nodes[1].width, 125.0);
    let options = FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    };
    let segments = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    let shot = &segments[0].film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.subjects.len(), 2);
    assert_eq!(shot.layers.len(), 1);
    assert_eq!(segments[0].frame_count, 180);
    assert!(realize(&segments[0].film).is_ok());
}
