use motionwright_domain::*;
use serde_json::{Value, json};
use uuid::Uuid;

fn id() -> Uuid {
    Uuid::parse_str("00000000-0000-4000-8000-000000000095").unwrap()
}
fn config() -> ProceduralConfig {
    ProceduralConfig {
        distribution: FieldDistribution::Grid,
        count: 3,
        columns: 3,
        origin_x: 300,
        origin_y: 300,
        area_width: 600,
        area_height: 200,
        ..Default::default()
    }
}
fn influenced() -> ProceduralConfig {
    ProceduralConfig {
        influence: Some(ProceduralInfluence {
            center_x: 600,
            center_y: 400,
            radius: 150,
            target_size: 64,
        }),
        ..config()
    }
}
fn project() -> Project {
    let mut p = Project::new("Editable bounded influence").unwrap();
    for name in ["Field", "Unrelated context"] {
        p.apply_change(&Change::AddScene {
            name: name.into(),
            objective: "Preserve authored meaning and identity".into(),
            duration_seconds: 6,
        })
        .unwrap();
    }
    p
}
fn upsert(p: &mut Project, config: ProceduralConfig) -> Result<()> {
    p.apply_change(&Change::UpsertProceduralField {
        instance_id: id(),
        scene_id: p.scenes[0].id,
        config,
    })
}
fn golden() -> Value {
    serde_json::from_str(include_str!(
        "../../../fixtures/procedural-influence-v1.json"
    ))
    .unwrap()
}

#[test]
fn absent_influence_preserves_complete_historical_config_and_node_bytes() {
    let fixture = golden();
    let wire = &fixture["legacy"]["rust"]["wire"];
    assert_eq!(
        serde_json::to_string(&ProceduralConfig::default()).unwrap(),
        wire["config_wire"]
            .as_str()
            .expect("actual6a legacy config capture")
    );
    assert_eq!(
        serde_json::to_string(
            &realize_procedural_field(id(), &ProceduralConfig::default()).unwrap()
        )
        .unwrap(),
        wire["nodes_wire"]
            .as_str()
            .expect("actual6a legacy node capture")
    );
    let decoded: ProceduralConfig =
        serde_json::from_str(wire["config_wire"].as_str().unwrap()).unwrap();
    assert_eq!(decoded.influence, None);
    assert_eq!(procedural_generator_version(&decoded), 1);
    assert!(
        serde_json::to_value(decoded)
            .unwrap()
            .get("influence")
            .is_none()
    );
}

#[test]
fn eight_shared_distance_vectors_preserve_original_centers_and_ids() {
    let fixture = golden();
    let cases = fixture["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 8);
    for vector in cases {
        let config: ProceduralConfig = serde_json::from_value(vector["config"].clone()).unwrap();
        let expected: Vec<[f64; 3]> = serde_json::from_value(vector["expected"].clone()).unwrap();
        let after = realize_procedural_field(id(), &config).unwrap();
        assert_eq!(
            after
                .iter()
                .map(|n| [n.x, n.y, n.width])
                .collect::<Vec<_>>(),
            expected,
            "{}",
            vector["name"]
        );
        let mut legacy = config;
        legacy.influence = None;
        let before = realize_procedural_field(id(), &legacy).unwrap();
        for (old, new) in before.iter().zip(&after) {
            assert_eq!(old.id, new.id);
            assert_eq!(new.width, new.height);
            assert_eq!(old.x + old.width / 2.0, new.x + new.width / 2.0);
            assert_eq!(old.y + old.height / 2.0, new.y + new.height / 2.0);
        }
    }
}

#[test]
fn malformed_or_out_of_bounds_influence_never_mutates_the_project() {
    let valid = json!({"center_x":600,"center_y":400,"radius":150,"target_size":64});
    let mut malformed = vec![
        Value::Null,
        json!({}),
        json!({"center_x":600}),
        json!({"center_x":600,"center_y":400,"radius":150,"target_size":64,"extra":1}),
    ];
    for (key, value) in [
        ("radius", json!(0)),
        ("radius", json!(1921)),
        ("radius", json!(2.5)),
        ("radius", json!("1e")),
        ("center_x", json!(-1)),
        ("center_x", json!(299)),
        ("center_x", json!(901)),
        ("center_y", json!(299)),
        ("center_y", json!(501)),
        ("target_size", json!(3)),
        ("target_size", json!(129)),
    ] {
        let mut candidate = valid.clone();
        candidate[key] = value;
        malformed.push(candidate);
    }
    let mut p = project();
    let before = p.clone();
    for influence in malformed {
        let mut wire = serde_json::to_value(config()).unwrap();
        wire["influence"] = influence;
        if let Ok(config) = serde_json::from_value::<ProceduralConfig>(wire) {
            assert!(upsert(&mut p, config).is_err());
        }
        assert_eq!(p, before);
    }
}

#[test]
fn oversized_influenced_geometry_is_rejected_atomically_without_clamping() {
    let mut p = project();
    let before = p.clone();
    let config = ProceduralConfig {
        count: 1,
        columns: 1,
        origin_x: 0,
        origin_y: 0,
        area_width: 32,
        area_height: 32,
        influence: Some(ProceduralInfluence {
            center_x: 16,
            center_y: 16,
            radius: 1,
            target_size: 128,
        }),
        ..config()
    };
    let error = upsert(&mut p, config).unwrap_err().to_string();
    assert!(
        error.contains("influenced object exceeds the authored field"),
        "{error}"
    );
    assert_eq!(p, before);
}

#[test]
fn influence_updates_and_explicit_disable_preserve_compatible_human_style_and_ids() {
    let mut p = project();
    upsert(&mut p, config()).unwrap();
    let original_ids = p.scenes[0].nodes.iter().map(|n| n.id).collect::<Vec<_>>();
    let unrelated = p.scenes[1].clone();
    let node = &p.scenes[0].nodes[0];
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id: node.id,
        style: NodeStyle {
            fill: Some("#EECCAA".into()),
            ..node.style.clone()
        },
    })
    .unwrap();
    for target_size in [64, 80, 48] {
        let mut next = influenced();
        next.influence.as_mut().unwrap().target_size = target_size;
        upsert(&mut p, next).unwrap();
        assert_eq!(p.scenes[0].nodes[1].width, f64::from(target_size));
        assert_eq!(p.scenes[0].nodes[0].style.fill.as_deref(), Some("#EECCAA"));
        assert_eq!(
            p.scenes[0].nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
            original_ids
        );
        assert_eq!(p.scenes[1], unrelated);
        assert_eq!(
            p.production_design.procedural_fields[0].generator_version,
            2
        );
        p.validate().unwrap();
    }
    upsert(&mut p, config()).unwrap();
    assert_eq!(
        p.production_design.procedural_fields[0].generator_version,
        1
    );
    assert_eq!(
        p.production_design.procedural_fields[0].config.influence,
        None
    );
    assert!(p.scenes[0].nodes.iter().all(|n| n.width == 32.0));
    assert_eq!(p.scenes[0].nodes[0].style.fill.as_deref(), Some("#EECCAA"));
    let before_detach = p.scenes[0].nodes.clone();
    p.apply_change(&Change::DetachProceduralField { instance_id: id() })
        .unwrap();
    assert_eq!(p.scenes[0].nodes, before_detach);
    assert!(p.production_design.procedural_fields.is_empty());
}

#[test]
fn human_size_conflicts_and_node_or_scope_locks_preserve_the_full_project() {
    for fault in ["override", "size", "position", "scope"] {
        let mut p = project();
        upsert(&mut p, config()).unwrap();
        match fault {
            "override" => p.scenes[0].nodes[1].width = 41.0,
            "size" => {
                p.scenes[0].nodes[1]
                    .property_locks
                    .insert(NodeProperty::Size);
            }
            "position" => {
                p.scenes[0].nodes[1]
                    .property_locks
                    .insert(NodeProperty::Position);
            }
            _ => {
                p.apply_change(&Change::SetLock {
                    resource: format!("scene:{}", p.scenes[0].id),
                    kind: LockKind::Position,
                    note: "Approved spatial arrangement".into(),
                })
                .unwrap();
            }
        }
        let before = p.clone();
        let error = upsert(&mut p, influenced()).unwrap_err().to_string();
        assert!(
            error.contains(if fault == "override" {
                "override conflict"
            } else {
                "locked"
            }),
            "{fault}: {error}"
        );
        assert_eq!(p, before, "{fault}");
    }
}

#[test]
fn all_distributions_seed_extremes_and_shapes_keep64_bounded_editable_nodes() {
    for distribution in [
        FieldDistribution::Grid,
        FieldDistribution::Staggered,
        FieldDistribution::Scatter,
        FieldDistribution::Radial,
        FieldDistribution::Spiral,
    ] {
        for seed in [0, u32::MAX] {
            for shape in [ProceduralShape::Square, ProceduralShape::Circle] {
                let config = ProceduralConfig {
                    distribution,
                    seed,
                    shape,
                    count: 64,
                    columns: 16,
                    influence: Some(ProceduralInfluence {
                        center_x: 630,
                        center_y: 450,
                        radius: 1920,
                        target_size: 4,
                    }),
                    ..Default::default()
                };
                let nodes = realize_procedural_field(id(), &config).unwrap();
                assert_eq!(nodes.len(), 64);
                assert_eq!(
                    nodes
                        .iter()
                        .map(|n| n.id)
                        .collect::<std::collections::BTreeSet<_>>()
                        .len(),
                    64
                );
                assert_eq!(nodes, realize_procedural_field(id(), &config).unwrap());
                assert!(nodes.iter().all(|n| n.width >= 4.0
                    && n.width <= 32.0
                    && n.width == n.height
                    && n.x >= f64::from(config.origin_x)
                    && n.y >= f64::from(config.origin_y)
                    && n.x + n.width <= f64::from(config.origin_x + config.area_width)
                    && n.y + n.height <= f64::from(config.origin_y + config.area_height)));
            }
        }
    }
}

#[test]
fn influence_retains_entrance_clock_and_existing_y_opacity_grammar() {
    let original = ProceduralConfig {
        reveal_step_frames: 4,
        reveal_duration_frames: 12,
        ..config()
    };
    let before = realize_procedural_field(id(), &original).unwrap();
    let next = ProceduralConfig {
        influence: influenced().influence,
        ..original
    };
    let after = realize_procedural_field(id(), &next).unwrap();
    for (old, new) in before.iter().zip(&after) {
        assert_eq!(old.keyframes.len(), new.keyframes.len());
        for (old_key, new_key) in old.keyframes.iter().zip(&new.keyframes) {
            assert_eq!(old_key.at, new_key.at);
            assert_eq!(old_key.property, new_key.property);
            assert_eq!(old_key.interpolation, new_key.interpolation);
            if old_key.property == MotionProperty::Opacity {
                assert_eq!(old_key, new_key);
            }
        }
        let y = new
            .keyframes
            .iter()
            .filter(|k| k.property == MotionProperty::Y)
            .collect::<Vec<_>>();
        assert_eq!(y.first().unwrap().value, new.y + 12.0);
        assert_eq!(y.last().unwrap().value, new.y);
    }
}

#[test]
fn versioned_baseline_roundtrip_refuses_forged_version_or_geometry() {
    let mut p = project();
    upsert(&mut p, influenced()).unwrap();
    let bytes = serde_json::to_vec(&p).unwrap();
    let reopened: Project = serde_json::from_slice(&bytes).unwrap();
    reopened.validate().unwrap();
    assert_eq!(reopened, p);
    for tamper in ["version", "baseline"] {
        let mut bad = reopened.clone();
        if tamper == "version" {
            bad.production_design.procedural_fields[0].generator_version = 1;
        } else {
            bad.production_design.procedural_fields[0].baseline[1].width = 33.0;
        }
        let before = bad.clone();
        assert!(upsert(&mut bad, config()).is_err());
        assert_eq!(bad, before);
    }
}
