//! Original measured labels and bars survive one comparison layout patch,
//! SQLite reopen and canonical Film construction. No physical provider claim.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{
    Archetype, MotionEasing, NarrativeRole, Primitive, SpatialIntent, StartAnchor, SubjectContent,
    realize,
};
use uuid::Uuid;

#[test]
fn measured_state_comparison_retains_bindings_and_axis_through_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("measured-comparison.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service
        .create_project("Source-bound state comparison")
        .unwrap();
    let save = |p: &Project, change: Change| {
        service
            .apply(p.id, &p.stamp(), &Uuid::now_v7().to_string(), &change)
            .unwrap()
            .project
    };
    p = save(
        &p,
        Change::AddScene {
            name: "Before and after".into(),
            objective: "Compare two original measurements".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let file = temp.path().join("source.csv");
    std::fs::write(
        &file,
        b"key,label,milli,unit\nbefore,Before,25000,percent\nafter,After,60000,percent\n",
    )
    .unwrap();
    let bytes = service.ingest_blob_file(&file).unwrap();
    let asset = Asset {
        id: Uuid::now_v7(),
        name: "Owner imported measurements".into(),
        media_type: "text/csv".into(),
        content_sha256: Some(bytes.sha256.clone()),
        source_revision: None,
    };
    p = save(
        &p,
        Change::AddAsset {
            asset: asset.clone(),
        },
    );
    let table = MeasuredTable {
        schema: MEASURED_DATA_SCHEMA.into(),
        id: Uuid::now_v7(),
        label: "Imported states".into(),
        version: 1,
        source_asset_id: asset.id,
        source_sha256: bytes.sha256,
        rows: vec![
            MeasuredDatum {
                key: "before".into(),
                label: "Before".into(),
                milli: 25000,
                unit: MeasuredUnit::Percent,
            },
            MeasuredDatum {
                key: "after".into(),
                label: "After".into(),
                milli: 60000,
                unit: MeasuredUnit::Percent,
            },
        ],
    };
    p = save(
        &p,
        Change::UpsertMeasuredTable {
            table: table.clone(),
        },
    );
    let mut ids = Vec::new();
    for i in 0..4 {
        let is_text = i % 2 == 0;
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: format!("Original bound state {i}"),
            kind: if is_text { "text" } else { "rectangle" }.into(),
            parent_id: None,
            x: 320.0 + f64::from(i) * 20.0,
            y: 300.0 + f64::from(i) * 20.0,
            width: 800.0,
            height: 100.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: is_text.then(|| "Placeholder".into()),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: i,
            style: NodeStyle {
                fill: Some(if is_text { "#F2F4F3" } else { "#86C8D1" }.into()),
                stroke: None,
                stroke_width: 0.0,
                font_family: is_text.then(|| MOTION_CANVAS_FONT_FAMILY.into()),
                font_size: is_text.then_some(48.0),
                font_weight: is_text.then_some(400),
                line_height: is_text.then_some(1.12),
                ..Default::default()
            },
            relations: vec![],
            property_locks: Default::default(),
            keyframes: vec![],
        };
        ids.push(node.id);
        p = save(
            &p,
            Change::AddCanvasNode {
                scene_id,
                node: node.clone(),
            },
        );
        p = save(
            &p,
            Change::UpsertMeasuredBinding {
                binding_id: Uuid::now_v7(),
                scene_id,
                node_id: node.id,
                table_id: table.id,
                datum_key: if i < 2 { "before" } else { "after" }.into(),
                view: if is_text {
                    MeasuredView::Label { decimals: 1 }
                } else {
                    MeasuredView::Bar {
                        axis_min_milli: 0,
                        axis_max_milli: 100000,
                        max_width_px: 800,
                    }
                },
            },
        );
    }
    let original = p.clone();
    let poses = [
        (180.0, 800.0, 100.0),
        (300.0, 200.0, 48.0),
        (460.0, 800.0, 100.0),
        (580.0, 480.0, 48.0),
    ];
    let patch = CreativePatch {
        scene_id,
        base_revision: p.revision,
        rationale: "motionwright.data.state-comparison/v1".into(),
        edits: ids
            .iter()
            .zip(poses)
            .map(|(id, (y, width, height))| ScopedCanvasEdit::Transform {
                node_id: *id,
                transform: CanvasTransform {
                    x: 240.0,
                    y,
                    width,
                    height,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                },
            })
            .collect(),
    };
    assert_eq!(p.preview_creative_patch(&patch).unwrap().after.len(), 4);
    assert_eq!(service.project(p.id).unwrap(), p);
    p = save(&p, Change::ApplyCreativePatch { patch });
    assert_eq!(p.revision, original.revision + 1);
    assert_eq!(
        p.production_design.measured_bindings,
        original.production_design.measured_bindings
    );
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
    let films = p
        .deliverables
        .iter()
        .map(|profile| {
            let film = build_motion_canvas_segments(&p, profile.id, &options)
                .unwrap()
                .remove(0)
                .film;
            assert!(realize(&film).is_ok());
            let shot = &film.sequences[0].beats[0].shots[0];
            assert_eq!(
                shot.subjects
                    .iter()
                    .filter(|subject| subject.parent.is_none())
                    .count(),
                4
            );
            // Root identity, exact common zero origin, widths and text in the
            // actual typed Film, not just a count of four projected subjects.
            let scale = (f64::from(profile.width) / 1920.0).min(f64::from(profile.height) / 1080.0);
            let left = 240.0 * f64::from(profile.width) / 1920.0 - f64::from(profile.width) / 2.0;
            for (index, expected_width) in [(1, 200.0), (3, 480.0)] {
                let id = format!("node-{}", ids[index].simple());
                let bar = shot
                    .subjects
                    .iter()
                    .find(|subject| subject.id == id)
                    .unwrap();
                assert!(matches!(bar.content, SubjectContent::Rectangle { .. }));
                match &bar.layout {
                    SpatialIntent::Fixed { position, size } => {
                        assert!((size.width - expected_width * scale).abs() < 1e-6);
                        assert!((size.height - 48.0 * scale).abs() < 1e-6);
                        assert!((position.x - size.width / 2.0 - left).abs() < 1e-6);
                    }
                    _ => panic!("Measured bar must retain a fixed original layout"),
                }
            }
            for (index, expected) in [(0, "Before \u{b7} 25.0%"), (2, "After \u{b7} 60.0%")] {
                let root = format!("node-{}", ids[index].simple());
                let lines = shot
                    .subjects
                    .iter()
                    .filter_map(|subject| {
                        if subject.parent.as_deref() != Some(root.as_str()) {
                            return None;
                        }
                        match &subject.content {
                            SubjectContent::Text { runs, .. } => {
                                Some(runs.iter().map(|run| run.text.as_str()).collect::<String>())
                            }
                            _ => None,
                        }
                    })
                    .collect::<Vec<_>>();
                assert_eq!(lines, vec![expected.to_owned()]);
            }
            film
        })
        .collect::<Vec<_>>();
    let patch_id = p.production_design.patches.last().unwrap().id;
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let saved = reopened.project(p.id).unwrap();
    assert_eq!(saved, p);
    for (profile, film) in saved.deliverables.iter().zip(films) {
        assert_eq!(
            build_motion_canvas_segments(&saved, profile.id, &options)
                .unwrap()
                .remove(0)
                .film,
            film
        );
    }
    let undone = reopened
        .apply(
            saved.id,
            &saved.stamp(),
            "undo-comparison",
            &Change::UndoCreativePatch { patch_id },
        )
        .unwrap()
        .project;
    assert_eq!(undone.scenes[0].nodes, original.scenes[0].nodes);
    assert_eq!(
        undone.production_design.measured_bindings,
        original.production_design.measured_bindings
    );
}

#[test]
fn measured_series_reveal_preserves_original_date_values_and_exact_film_visibility_through_reopen()
{
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("measured-series.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service.create_project("Source-bound dated series").unwrap();
    let save = |p: &Project, change: Change| {
        service
            .apply(p.id, &p.stamp(), &Uuid::now_v7().to_string(), &change)
            .unwrap()
            .project
    };
    p = save(
        &p,
        Change::AddScene {
            name: "Dated series reveal".into(),
            objective: "Reveal original dated measurements".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let file = temp.path().join("source.csv");
    std::fs::write(
        &file,
        b"key,label,milli,unit\nbefore,2026-10-01,25000,percent\nafter,2026-11-19,60000,percent\n",
    )
    .unwrap();
    let bytes = service.ingest_blob_file(&file).unwrap();
    let asset = Asset {
        id: Uuid::now_v7(),
        name: "Owner imported measurements".into(),
        media_type: "text/csv".into(),
        content_sha256: Some(bytes.sha256.clone()),
        source_revision: None,
    };
    p = save(
        &p,
        Change::AddAsset {
            asset: asset.clone(),
        },
    );
    let table = MeasuredTable {
        schema: MEASURED_DATA_SCHEMA.into(),
        id: Uuid::now_v7(),
        label: "Imported states".into(),
        version: 1,
        source_asset_id: asset.id,
        source_sha256: bytes.sha256,
        rows: vec![
            MeasuredDatum {
                key: "before".into(),
                label: "2026-10-01".into(),
                milli: 25000,
                unit: MeasuredUnit::Percent,
            },
            MeasuredDatum {
                key: "after".into(),
                label: "2026-11-19".into(),
                milli: 60000,
                unit: MeasuredUnit::Percent,
            },
        ],
    };
    p = save(
        &p,
        Change::UpsertMeasuredTable {
            table: table.clone(),
        },
    );
    let mut ids = Vec::new();
    for i in 0..4 {
        let is_text = i % 2 == 0;
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: format!("Original bound state {i}"),
            kind: if is_text { "text" } else { "rectangle" }.into(),
            parent_id: None,
            x: 320.0 + f64::from(i) * 20.0,
            y: 300.0 + f64::from(i) * 20.0,
            width: 800.0,
            height: 100.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: is_text.then(|| "Placeholder".into()),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: i,
            style: NodeStyle {
                fill: Some(if is_text { "#F2F4F3" } else { "#86C8D1" }.into()),
                stroke: None,
                stroke_width: 0.0,
                font_family: is_text.then(|| MOTION_CANVAS_FONT_FAMILY.into()),
                font_size: is_text.then_some(48.0),
                font_weight: is_text.then_some(400),
                line_height: is_text.then_some(1.12),
                ..Default::default()
            },
            relations: vec![],
            property_locks: Default::default(),
            keyframes: vec![],
        };
        ids.push(node.id);
        p = save(
            &p,
            Change::AddCanvasNode {
                scene_id,
                node: node.clone(),
            },
        );
        p = save(
            &p,
            Change::UpsertMeasuredBinding {
                binding_id: Uuid::now_v7(),
                scene_id,
                node_id: node.id,
                table_id: table.id,
                datum_key: if i < 2 { "before" } else { "after" }.into(),
                view: if is_text {
                    MeasuredView::Label { decimals: 1 }
                } else {
                    MeasuredView::Bar {
                        axis_min_milli: 0,
                        axis_max_milli: 100000,
                        max_width_px: 800,
                    }
                },
            },
        );
    }

    let original = p.clone();
    let mut edits = Vec::new();
    for (index, id) in ids.iter().copied().enumerate() {
        let node = original.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == id)
            .unwrap();
        let start = RationalTime::new((index / 2) as i64, 1).unwrap();
        let end = RationalTime::new(2 * (index / 2) as i64 + 1, 2).unwrap();
        let y = 120.0 + 210.0 * (index / 2) as f64 + if index % 2 == 0 { 0.0 } else { 116.0 };
        edits.push(ScopedCanvasEdit::Transform {
            node_id: id,
            transform: CanvasTransform {
                x: 240.0,
                y,
                width: node.width,
                height: if index % 2 == 0 { 100.0 } else { 40.0 },
                opacity: 1.0,
                rotation_deg: 0.0,
            },
        });
        let mut keys = vec![CanvasKeyframe {
            at: RationalTime::ZERO,
            property: MotionProperty::Opacity,
            value: 0.0,
            interpolation: MotionInterpolation::Linear,
        }];
        if start != RationalTime::ZERO {
            keys.push(CanvasKeyframe {
                at: start,
                property: MotionProperty::Opacity,
                value: 0.0,
                interpolation: MotionInterpolation::Hold,
            });
        }
        keys.push(CanvasKeyframe {
            at: end,
            property: MotionProperty::Opacity,
            value: 1.0,
            interpolation: MotionInterpolation::Linear,
        });
        edits.push(ScopedCanvasEdit::Motion {
            node_id: id,
            keyframes: keys,
        });
    }
    let patch = CreativePatch {
        scene_id, base_revision:p.revision,
        rationale:"motionwright.data.series-reveal/v1: two original dated measurements; editorial visibility, not elapsed-time axis".into(),
        edits,
    };
    assert_eq!(p.preview_creative_patch(&patch).unwrap().after.len(), 4);
    assert_eq!(service.project(p.id).unwrap(), original);
    p = save(&p, Change::ApplyCreativePatch { patch });
    assert_eq!(p.revision, original.revision + 1);
    assert_eq!(
        p.production_design.measured_bindings,
        original.production_design.measured_bindings
    );
    assert_eq!(
        p.production_design.measured_tables,
        original.production_design.measured_tables
    );
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
    let films = p
        .deliverables
        .iter()
        .map(|profile| {
            let film = build_motion_canvas_segments(&p, profile.id, &options)
                .unwrap()
                .remove(0)
                .film;
            let shot = film.shots().next().unwrap();
            assert_eq!(shot.motion.len(), 4);
            for (index, id) in ids.iter().enumerate() {
                let root = format!("node-{}", id.simple());
                let subject = shot.subjects.iter().find(|s| s.id == root).unwrap();
                assert!(!subject.initially_visible);
                let motion = shot
                    .motion
                    .iter()
                    .find(|m| matches!(&m.primitive,Primitive::FadeIn {target} if target==&root))
                    .unwrap();
                assert_eq!(motion.easing, MotionEasing::Linear);
                let span = film
                    .timing
                    .spans
                    .iter()
                    .find(|s| s.id == motion.span_id)
                    .unwrap();
                assert_eq!(span.minimum, RationalTime::new(1, 2).unwrap());
                assert_eq!(span.minimum, span.maximum);
                assert_eq!(
                    span.anchor,
                    StartAnchor::Absolute {
                        time: RationalTime::new((index / 2) as i64, 1).unwrap()
                    }
                );
                if index % 2 == 0 {
                    let expected = original.scenes[0].nodes[index].text.as_deref().unwrap();
                    let leaves = shot
                        .subjects
                        .iter()
                        .filter_map(|s| {
                            if s.parent.as_deref() != Some(root.as_str()) {
                                return None;
                            }
                            match &s.content {
                                SubjectContent::Text { runs, .. } => {
                                    Some(runs.iter().map(|r| r.text.as_str()).collect::<String>())
                                }
                                _ => None,
                            }
                        })
                        .collect::<Vec<_>>();
                    assert_eq!(leaves, vec![expected.to_owned()]);
                } else {
                    let scale =
                        (f64::from(profile.width) / 1920.0).min(f64::from(profile.height) / 1080.0);
                    let width = original.scenes[0].nodes[index].width * scale;
                    match &subject.layout {
                        SpatialIntent::Fixed { position, size } => {
                            assert!((size.width - width).abs() < 1e-6);
                            let left = 240.0 * f64::from(profile.width) / 1920.0
                                - f64::from(profile.width) / 2.0;
                            assert!((position.x - size.width / 2.0 - left).abs() < 1e-6);
                        }
                        _ => panic!("Measured series bar requires fixed geometry"),
                    }
                }
            }
            assert!(realize(&film).is_ok());
            film
        })
        .collect::<Vec<_>>();
    let patch_id = p.production_design.patches.last().unwrap().id;
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let saved = reopened.project(p.id).unwrap();
    assert_eq!(saved, p);
    for (profile, film) in saved.deliverables.iter().zip(films) {
        assert_eq!(
            build_motion_canvas_segments(&saved, profile.id, &options)
                .unwrap()
                .remove(0)
                .film,
            film
        );
    }
    let undone = reopened
        .apply(
            saved.id,
            &saved.stamp(),
            "undo-measured-series",
            &Change::UndoCreativePatch { patch_id },
        )
        .unwrap()
        .project;
    assert_eq!(undone.scenes[0].nodes, original.scenes[0].nodes);
    assert_eq!(
        undone.production_design.measured_bindings,
        original.production_design.measured_bindings
    );
}
