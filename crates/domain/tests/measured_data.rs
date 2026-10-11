use motionwright_domain::*;
use serde_json::json;
use std::collections::BTreeSet;
use uuid::Uuid;

const SHA_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const SHA_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const SHA_C: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

fn node(name: &str, kind: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::new_v4(),
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x: 200.0,
        y: 300.0,
        width: 800.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: if kind == "text" {
            Some("Original editorial placeholder".into())
        } else {
            None
        },
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn source_asset(sha: &str) -> Asset {
    Asset {
        id: Uuid::new_v4(),
        name: format!("Measured source {sha}"),
        media_type: "application/json".into(),
        content_sha256: Some(sha.into()),
        source_revision: Some("owner-imported-unreviewed".into()),
    }
}
fn table(asset: &Asset, id: Uuid, version: u32, percent: i64) -> MeasuredTable {
    MeasuredTable {
        schema: MEASURED_DATA_SCHEMA.into(),
        id,
        label: "Imported campaign report".into(),
        version,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        rows: vec![
            MeasuredDatum {
                key: "conversion".into(),
                label: "Conversion".into(),
                milli: percent,
                unit: MeasuredUnit::Percent,
            },
            MeasuredDatum {
                key: "orders".into(),
                label: "Orders".into(),
                milli: 128000,
                unit: MeasuredUnit::Count,
            },
        ],
    }
}
fn seed() -> (Project, Uuid, [Asset; 3], [Uuid; 2], [Uuid; 2]) {
    let mut p = Project::new("Measured data project").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Dependent".into(),
        objective: "Measured counters".into(),
        duration_seconds: 6,
    })
    .unwrap();
    p.apply_change(&Change::AddScene {
        name: "Independent".into(),
        objective: "Unchanged graphic".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scenes = [p.scenes[0].id, p.scenes[1].id];
    let table_id = Uuid::new_v4();
    let assets = [
        source_asset(SHA_A),
        source_asset(SHA_B),
        source_asset(SHA_C),
    ];
    for asset in &assets {
        p.apply_change(&Change::AddAsset {
            asset: asset.clone(),
        })
        .unwrap();
    }
    let nodes = [
        node("Measured counter", "text"),
        node("Explicit bounded bar", "rectangle"),
    ];
    for (scene, node) in [(scenes[0], &nodes[0]), (scenes[0], &nodes[1])] {
        p.apply_change(&Change::AddCanvasNode {
            scene_id: scene,
            node: node.clone(),
        })
        .unwrap();
    }
    // Default delivery profiles include every scene when the selection
    // is empty. Explicitly scope the other default profiles to B so this
    // fixture proves *only* the affected profile is invalidated.
    for mut independent in p.deliverables.clone().into_iter().skip(1) {
        independent.included_scene_ids = vec![scenes[1]];
        p.apply_change(&Change::UpsertDeliverable {
            profile: independent,
        })
        .unwrap();
    }
    let profile = p.deliverables[0].clone();
    let mut a = profile.clone();
    a.included_scene_ids = vec![scenes[0]];
    p.apply_change(&Change::UpsertDeliverable { profile: a })
        .unwrap();
    let mut b = profile;
    b.id = Uuid::new_v4();
    b.name = "Only independent".into();
    b.included_scene_ids = vec![scenes[1]];
    b.parent_profile_id = None;
    p.apply_change(&Change::UpsertDeliverable { profile: b })
        .unwrap();
    let nodeids = [nodes[0].id, nodes[1].id];
    (p, table_id, assets, scenes, nodeids)
}
fn bind(
    p: &mut Project,
    id: Uuid,
    scene: Uuid,
    node: Uuid,
    table: Uuid,
    key: &str,
    view: MeasuredView,
) -> Result<()> {
    p.apply_change(&Change::UpsertMeasuredBinding {
        binding_id: id,
        scene_id: scene,
        node_id: node,
        table_id: table,
        datum_key: key.into(),
        view,
    })
}
#[test]
fn exact_integer_units_and_explicit_axes_do_not_infer_metrics() {
    let row = MeasuredDatum {
        key: "growth".into(),
        label: "Measured growth".into(),
        milli: -12505,
        unit: MeasuredUnit::Percent,
    };
    assert_eq!(
        measured_output(&row, &MeasuredView::Counter { decimals: 2 }).unwrap(),
        MeasuredOutput::Text {
            value: "-12.51%".into()
        }
    );
    assert_eq!(
        measured_output(&row, &MeasuredView::Label { decimals: 1 }).unwrap(),
        MeasuredOutput::Text {
            value: "Measured growth · -12.5%".into()
        }
    );
    assert_eq!(
        measured_output(&row, &MeasuredView::Counter { decimals: 0 }).unwrap(),
        MeasuredOutput::Text {
            value: "-13%".into()
        }
    );
    let positive = MeasuredDatum {
        milli: 25000,
        ..row
    };
    assert_eq!(
        measured_output(
            &positive,
            &MeasuredView::Bar {
                axis_min_milli: 0,
                axis_max_milli: 100000,
                max_width_px: 400,
            }
        )
        .unwrap(),
        MeasuredOutput::Bar { width_px: 100 }
    );
    assert!(
        measured_output(
            &positive,
            &MeasuredView::Bar {
                axis_min_milli: 30000,
                axis_max_milli: 100000,
                max_width_px: 400,
            }
        )
        .unwrap_err()
        .to_string()
        .contains("axis")
    );
}
#[test]
fn one_changed_measurement_invalidates_only_its_dependent_scene_and_deliverable() {
    let (mut p, id, assets, scenes, nodes) = seed();
    p.apply_change(&Change::UpsertMeasuredTable {
        table: table(&assets[0], id, 1, 12500),
    })
    .unwrap();
    let label = Uuid::new_v4();
    let bar = Uuid::new_v4();
    bind(
        &mut p,
        label,
        scenes[0],
        nodes[0],
        id,
        "conversion",
        MeasuredView::Label { decimals: 2 },
    )
    .unwrap();
    bind(
        &mut p,
        bar,
        scenes[0],
        nodes[1],
        id,
        "conversion",
        MeasuredView::Bar {
            axis_min_milli: 0,
            axis_max_milli: 100000,
            max_width_px: 800,
        },
    )
    .unwrap();
    assert_eq!(
        p.scenes[0].nodes[0].text.as_deref(),
        Some("Conversion · 12.50%")
    );
    assert_eq!(p.scenes[0].nodes[1].width, 100.0);
    p.scenes[0].status = SceneStatus::Approved;
    p.scenes[1].status = SceneStatus::Approved;
    let before_other = p.scenes[1].clone();
    let before_style = p.scenes[0].nodes[0].style.clone();
    let before_height = p.scenes[0].nodes[1].height;
    p.apply_change(&Change::UpsertMeasuredTable {
        table: table(&assets[1], id, 2, 37500),
    })
    .unwrap();
    assert_eq!(
        p.scenes[0].nodes[0].text.as_deref(),
        Some("Conversion · 37.50%")
    );
    assert_eq!(p.scenes[0].nodes[1].width, 300.0);
    assert_eq!(p.scenes[0].nodes[0].style, before_style);
    assert_eq!(p.scenes[0].nodes[1].height, before_height);
    assert_eq!(p.scenes[0].status, SceneStatus::Draft);
    assert_eq!(p.scenes[1], before_other);
    let impact = p.production_design.last_measured_impact.as_ref().unwrap();
    assert_eq!(impact.changed_keys, vec!["conversion"]);
    assert_eq!(impact.scene_ids, vec![scenes[0]]);
    assert_eq!(impact.deliverable_ids, vec![p.deliverables[0].id]);
    assert_eq!(p.production_design.measured_bindings.len(), 2);
    p.validate().unwrap();

    p.scenes[0].status = SceneStatus::Approved;
    p.apply_change(&Change::UpsertMeasuredTable {
        table: table(&assets[2], id, 3, 37500),
    })
    .unwrap();
    assert_eq!(p.scenes[0].status, SceneStatus::Approved);
    assert!(
        p.production_design
            .last_measured_impact
            .as_ref()
            .unwrap()
            .scene_ids
            .is_empty()
    );
}
#[test]
fn source_sha_versions_missing_rows_edits_and_locks_fail_closed() {
    let (mut p, id, assets, scenes, nodes) = seed();
    let original = table(&assets[0], id, 1, 12500);
    let mut forged = original.clone();
    forged.source_sha256 = SHA_B.into();
    assert!(
        p.apply_change(&Change::UpsertMeasuredTable { table: forged })
            .unwrap_err()
            .to_string()
            .contains("SHA-256")
    );
    p.apply_change(&Change::UpsertMeasuredTable { table: original })
        .unwrap();
    bind(
        &mut p,
        Uuid::new_v4(),
        scenes[0],
        nodes[0],
        id,
        "conversion",
        MeasuredView::Counter { decimals: 1 },
    )
    .unwrap();
    let mut wrong = table(&assets[1], id, 2, 22000);
    wrong.rows.retain(|r| r.key != "conversion");
    let mut candidate = p.clone();
    assert!(
        candidate
            .apply_change(&Change::UpsertMeasuredTable { table: wrong })
            .unwrap_err()
            .to_string()
            .contains("key")
    );
    let mut wrong_version = table(&assets[1], id, 1, 22000);
    assert!(
        p.clone()
            .apply_change(&Change::UpsertMeasuredTable {
                table: wrong_version.clone()
            })
            .unwrap_err()
            .to_string()
            .contains("version")
    );
    wrong_version.version = 2;
    let mut edited = p.clone();
    edited.scenes[0].nodes[0].text = Some("Human replaced the value".into());
    assert!(
        edited
            .apply_change(&Change::UpsertMeasuredTable {
                table: wrong_version.clone()
            })
            .unwrap_err()
            .to_string()
            .contains("detach")
    );
    let mut locked = p.clone();
    locked.scenes[0].nodes[0]
        .property_locks
        .insert(NodeProperty::Text);
    assert!(
        locked
            .apply_change(&Change::UpsertMeasuredTable {
                table: wrong_version
            })
            .unwrap_err()
            .to_string()
            .contains("locked")
    );
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: assets[0].id
        })
        .unwrap_err()
        .to_string()
        .contains("measured table")
    );
    let before = p.clone();
    assert!(
        bind(
            &mut p,
            Uuid::new_v4(),
            scenes[0],
            nodes[1],
            id,
            "conversion",
            MeasuredView::Bar {
                axis_min_milli: 30000,
                axis_max_milli: 100000,
                max_width_px: 800,
            }
        )
        .is_err()
    );
    // Unlike an unfenced raw canvas mutation, measured attach is staged.
    assert_eq!(p, before);
}
#[test]
fn detach_retains_editable_canvas_and_legacy_serialization_keeps_empty_state() {
    let (mut p, id, assets, scenes, nodes) = seed();
    p.apply_change(&Change::UpsertMeasuredTable {
        table: table(&assets[0], id, 1, 55000),
    })
    .unwrap();
    let binding_id = Uuid::new_v4();
    bind(
        &mut p,
        binding_id,
        scenes[0],
        nodes[0],
        id,
        "conversion",
        MeasuredView::Counter { decimals: 1 },
    )
    .unwrap();
    p.apply_change(&Change::DetachMeasuredBinding { binding_id })
        .unwrap();
    assert!(p.production_design.measured_bindings.is_empty());
    assert_eq!(p.scenes[0].nodes[0].text.as_deref(), Some("55.0%"));
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: scenes[0],
        node_id: nodes[0],
        text: Some("An independently edited caption".into()),
    })
    .unwrap();
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(p.production_design, restored.production_design);
    let legacy = json!({
        "plan":null,"heroes":[],"capsules":[],"patches":[],
        "brand_profile":null,"taste_profile":null,"brand_exceptions":[],"project_decisions":[]
    });
    let design: ProductionDesign = serde_json::from_value(legacy).unwrap();
    assert!(design.measured_tables.is_empty() && design.measured_bindings.is_empty());
}

#[test]
fn changed_metric_meaning_invalidates_dependents_even_if_bar_and_counter_pixels_are_unchanged() {
    let (mut p, id, assets, scenes, nodes) = seed();
    p.apply_change(&Change::UpsertMeasuredTable {
        table: table(&assets[0], id, 1, 12500),
    })
    .unwrap();
    bind(
        &mut p,
        Uuid::new_v4(),
        scenes[0],
        nodes[0],
        id,
        "conversion",
        MeasuredView::Counter { decimals: 1 },
    )
    .unwrap();
    bind(
        &mut p,
        Uuid::new_v4(),
        scenes[0],
        nodes[1],
        id,
        "conversion",
        MeasuredView::Bar {
            axis_min_milli: 0,
            axis_max_milli: 100000,
            max_width_px: 800,
        },
    )
    .unwrap();
    let same_pixels = p.scenes[0].nodes.clone();
    p.scenes[0].status = SceneStatus::Approved;
    p.scenes[1].status = SceneStatus::Approved;
    let independent = p.scenes[1].clone();
    let mut changed = table(&assets[1], id, 2, 12500);
    changed.rows[0].label = "The underlying measurement definition changed".into();
    p.apply_change(&Change::UpsertMeasuredTable { table: changed })
        .unwrap();
    assert_eq!(
        p.scenes[0].nodes, same_pixels,
        "a semantic-only change must not fabricate a new visual rendering"
    );
    assert_eq!(p.scenes[0].status, SceneStatus::Draft);
    assert_eq!(p.scenes[1], independent);
    let impact = p.production_design.last_measured_impact.as_ref().unwrap();
    assert_eq!(impact.changed_keys, vec!["conversion"]);
    assert_eq!(impact.scene_ids, vec![scenes[0]]);
    assert_eq!(impact.deliverable_ids, vec![p.deliverables[0].id]);

    // Preserve a separately reviewed unit-only change, even though the
    // explicit bar's normalized width cannot convey unit semantics visually.
    let (mut q, id, assets, scenes, nodes) = seed();
    q.apply_change(&Change::UpsertMeasuredTable {
        table: table(&assets[0], id, 1, 12500),
    })
    .unwrap();
    bind(
        &mut q,
        Uuid::new_v4(),
        scenes[0],
        nodes[1],
        id,
        "conversion",
        MeasuredView::Bar {
            axis_min_milli: 0,
            axis_max_milli: 100000,
            max_width_px: 800,
        },
    )
    .unwrap();
    q.scenes[0].status = SceneStatus::Approved;
    let original_width = q.scenes[0].nodes[1].width;
    let mut changed_unit = table(&assets[1], id, 2, 12500);
    changed_unit.rows[0].unit = MeasuredUnit::Seconds;
    q.apply_change(&Change::UpsertMeasuredTable {
        table: changed_unit,
    })
    .unwrap();
    assert_eq!(q.scenes[0].nodes[1].width, original_width);
    assert_eq!(q.scenes[0].status, SceneStatus::Draft);
    assert_eq!(
        q.production_design
            .last_measured_impact
            .as_ref()
            .unwrap()
            .changed_keys,
        vec!["conversion"]
    );
}
