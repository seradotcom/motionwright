use motionwright_domain::*;
use uuid::Uuid;

const ID: &str = "00000000-0000-4000-8000-000000000095";
fn instance_id() -> Uuid {
    Uuid::parse_str(ID).unwrap()
}
fn project() -> Project {
    let mut p = Project::new("Procedural native Canvas study").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Repetition".into(),
        objective: "Original geometric pattern".into(),
        duration_seconds: 6,
    })
    .unwrap();
    p
}
fn upsert(p: &mut Project, config: ProceduralConfig) -> Result<()> {
    p.apply_change(&Change::UpsertProceduralField {
        instance_id: instance_id(),
        scene_id: p.scenes[0].id,
        config,
    })
}
#[test]
fn stable_identity_seeded_coordinates_and_canvas_wire_shape() {
    let config = ProceduralConfig::default();
    let nodes = realize_procedural_field(instance_id(), &config).unwrap();
    assert_eq!(nodes.len(), 12);
    assert_eq!(
        procedural_node_id(instance_id(), 0).to_string(),
        "1b1d1609-6443-84ef-ae30-15b66241f589"
    );
    assert_eq!(
        procedural_node_id(instance_id(), 1).to_string(),
        "47e4e8df-db4c-81f2-8118-f1e2da44e052"
    );
    assert_eq!(
        procedural_node_id(instance_id(), 11).to_string(),
        "3bea5c37-80c3-819f-8bf9-b6d8a54f88c2"
    );
    let positions: Vec<_> = nodes
        .iter()
        .map(|node| (node.x as i32, node.y as i32))
        .collect();
    assert_eq!(
        positions,
        vec![
            (200, 211),
            (440, 302),
            (792, 193),
            (993, 189),
            (343, 474),
            (594, 371),
            (798, 462),
            (1023, 420),
            (349, 642),
            (449, 678),
            (652, 650),
            (862, 660),
        ]
    );
    assert_eq!(
        nodes,
        realize_procedural_field(instance_id(), &config).unwrap()
    );
    assert!(nodes.iter().all(|node| node.kind == "rectangle"
        && node.opacity == 1.0
        && node.coordinate_space == CoordinateSpace::ProjectPixels
        && node.style.fill.as_deref() == Some("#A5C8DF")
        && node.keyframes.is_empty()));
    let mut grid = config.clone();
    grid.distribution = FieldDistribution::Grid;
    let grid_nodes = realize_procedural_field(instance_id(), &grid).unwrap();
    assert_eq!(
        grid_nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
        nodes.iter().map(|n| n.id).collect::<Vec<_>>()
    );
    assert_ne!(grid_nodes[0].x, nodes[0].x);
}
#[test]
fn rejects_invalid_budgets_before_mutation() {
    let mut p = project();
    let initial = p.clone();
    for bad in [
        ProceduralConfig {
            count: 65,
            ..Default::default()
        },
        ProceduralConfig {
            size: 129,
            ..Default::default()
        },
        ProceduralConfig {
            area_width: 1800,
            ..Default::default()
        },
        ProceduralConfig {
            opacity_percent: 0,
            ..Default::default()
        },
        ProceduralConfig {
            columns: 0,
            ..Default::default()
        },
        ProceduralConfig {
            count: 64,
            area_height: 40,
            ..Default::default()
        },
    ] {
        assert!(upsert(&mut p, bad.clone()).is_err(), "{bad:?}");
        assert_eq!(p, initial, "budget failure must never mutate project");
    }
}
#[test]
fn variation_and_growth_keep_editable_override_and_node_identifiers() {
    let mut p = project();
    upsert(&mut p, ProceduralConfig::default()).unwrap();
    let first = p.scenes[0].nodes.clone();
    let node_id = procedural_node_id(instance_id(), 1);
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id,
        style: NodeStyle {
            fill: Some("#FFAA22".into()),
            ..Default::default()
        },
    })
    .unwrap();
    let config = ProceduralConfig {
        seed: 123,
        count: 18,
        distribution: FieldDistribution::Scatter,
        ..Default::default()
    };
    upsert(&mut p, config).unwrap();
    assert_eq!(p.scenes[0].nodes.len(), 18);
    assert_eq!(p.scenes[0].nodes[0].id, first[0].id);
    assert_ne!(p.scenes[0].nodes[0].x, first[0].x);
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == node_id)
            .unwrap()
            .style
            .fill
            .as_deref(),
        Some("#FFAA22"),
        "human style remains authoritative"
    );
    assert_eq!(
        p.production_design.procedural_fields[0].baseline[1]
            .style
            .fill
            .as_deref(),
        Some("#A5C8DF"),
        "future revisions retain the generated merge base"
    );
    let json = serde_json::to_string(&p).unwrap();
    let reopened: Project = serde_json::from_str(&json).unwrap();
    reopened.validate().unwrap();
    assert_eq!(p, reopened);
}
#[test]
fn shrink_refuses_to_remove_modified_or_deleted_nodes() {
    let mut p = project();
    upsert(&mut p, ProceduralConfig::default()).unwrap();
    let node_id = procedural_node_id(instance_id(), 11);
    p.apply_change(&Change::TransformCanvasNode {
        scene_id: p.scenes[0].id,
        node_id,
        transform: CanvasTransform {
            x: 840.0,
            y: 620.0,
            width: 32.0,
            height: 32.0,
            rotation_deg: 0.0,
            opacity: 1.0,
        },
    })
    .unwrap();
    let prior = p.clone();
    let smaller = ProceduralConfig {
        count: 8,
        ..Default::default()
    };
    assert!(upsert(&mut p, smaller.clone()).is_err());
    assert_eq!(p, prior);
    // Detach preserves all artwork and lets humans control it independent of the field.
    p.apply_change(&Change::DetachProceduralField {
        instance_id: instance_id(),
    })
    .unwrap();
    assert!(p.production_design.procedural_fields.is_empty());
    assert_eq!(p.scenes[0].nodes, prior.scenes[0].nodes);
    assert!(
        p.apply_change(&Change::DetachProceduralField {
            instance_id: instance_id()
        })
        .is_err()
    );
}
#[test]
fn unmodified_shrink_removes_only_generated_suffix() {
    let mut p = project();
    upsert(&mut p, ProceduralConfig::default()).unwrap();
    let original = p.scenes[0].nodes.clone();
    let config = ProceduralConfig {
        count: 8,
        ..Default::default()
    };
    upsert(&mut p, config).unwrap();
    assert_eq!(p.scenes[0].nodes.len(), 8);
    assert_eq!(
        p.scenes[0].nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
        original[..8].iter().map(|n| n.id).collect::<Vec<_>>(),
    );
    assert_ne!(
        p.scenes[0].nodes[0].y, original[0].y,
        "layout reflows to two rows"
    );
    p.validate().unwrap();
}
#[test]
fn ordered_frame_reveals_are_editable_and_half_open_scene_bounded() {
    let mut p = project();
    let sequence = ProceduralConfig {
        count: 5,
        reveal_step_frames: 3,
        reveal_duration_frames: 12,
        ..Default::default()
    };
    let nodes = realize_procedural_field(instance_id(), &sequence).unwrap();
    assert_eq!(nodes.len(), 5);
    assert_eq!(nodes[0].keyframes.len(), 4);
    assert_eq!(nodes[0].keyframes[0].at, RationalTime::ZERO);
    assert_eq!(nodes[0].keyframes[1].at, RationalTime::new(2, 5).unwrap());
    assert_eq!(nodes[0].keyframes[2].at, RationalTime::ZERO);
    assert_eq!(nodes[0].keyframes[3].at, RationalTime::new(2, 5).unwrap());
    assert_eq!(nodes[1].keyframes.len(), 6);
    assert_eq!(nodes[1].keyframes[1].at, RationalTime::new(1, 10).unwrap());
    assert_eq!(nodes[1].keyframes[2].at, RationalTime::new(1, 2).unwrap());
    assert_eq!(nodes[1].keyframes[4].at, RationalTime::new(1, 10).unwrap());
    assert_eq!(nodes[1].keyframes[5].value, 1.0);
    assert_eq!(
        nodes[1].keyframes[5].interpolation,
        MotionInterpolation::EaseOutCubic
    );
    upsert(&mut p, sequence).unwrap();
    assert_eq!(p.scenes[0].nodes.len(), 5);
    p.validate().unwrap();
    let before = p.clone();
    let invalid = ProceduralConfig {
        count: 30,
        reveal_step_frames: 10,
        reveal_duration_frames: 60,
        ..Default::default()
    };
    assert!(
        upsert(&mut p, invalid)
            .unwrap_err()
            .to_string()
            .contains("sequence exceeds scene duration")
    );
    assert_eq!(p, before);
}

#[test]
fn brand_palette_applies_to_procedural_configuration_and_human_nodes() {
    let mut p = project();
    let rule = Uuid::new_v4();
    let policy = BrandProfile {
        id: Uuid::new_v4(),
        label: "Procedural design authority".into(),
        version: 1,
        rules: vec![BrandRule::AllowedAccents {
            id: rule,
            colors: vec!["#A5C8DF".into()],
        }],
    };
    p.apply_change(&Change::SetBrandGovernance {
        profile: Some(policy.clone()),
        exceptions: vec![],
    })
    .unwrap();
    upsert(&mut p, ProceduralConfig::default()).unwrap();
    let before = p.clone();

    let mut forbidden_config = p.clone();
    let wrong = ProceduralConfig {
        fill: "#F00000".into(),
        ..Default::default()
    };
    assert!(
        upsert(&mut forbidden_config, wrong.clone())
            .unwrap_err()
            .to_string()
            .contains("brand rule")
    );
    assert_eq!(p, before, "invalid authored config must not persist");

    let mut forbidden_node = p.clone();
    let original = forbidden_node.scenes[0].nodes[0].style.clone();
    let mut changed_style = original;
    changed_style.fill = Some("#F00000".into());
    assert!(
        forbidden_node
            .apply_change(&Change::UpdateCanvasStyle {
                scene_id: p.scenes[0].id,
                node_id: procedural_node_id(instance_id(), 0),
                style: changed_style,
            })
            .unwrap_err()
            .to_string()
            .contains("brand rule")
    );

    let waiver = BrandException {
        id: Uuid::new_v4(),
        rule_id: rule,
        scene_id: p.scenes[0].id,
        brand_sha256: policy.content_digest().unwrap(),
        campaign: "One-shot palette departure".into(),
        author: "Design owner / declared".into(),
        rationale: "An explicitly reviewed exception on only the first scene".into(),
    };
    p.apply_change(&Change::SetBrandGovernance {
        profile: Some(policy),
        exceptions: vec![waiver],
    })
    .unwrap();
    upsert(&mut p, wrong.clone()).unwrap();
    p.apply_change(&Change::AddScene {
        name: "Second, non-waived scene".into(),
        objective: "Policy scope cannot escape an approved scene".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let second = p.scenes[1].id;
    let mut bad_other = p.clone();
    assert!(
        bad_other
            .apply_change(&Change::UpsertProceduralField {
                instance_id: Uuid::new_v4(),
                scene_id: second,
                config: wrong,
            })
            .unwrap_err()
            .to_string()
            .contains("brand rule")
    );
    p.validate().unwrap();
}

#[test]
fn orbit_and_spiral_share_identity_and_match_fixed_point_golden_layout() {
    let config = ProceduralConfig {
        distribution: FieldDistribution::Radial,
        shape: ProceduralShape::Circle,
        ..Default::default()
    };
    let nodes = realize_procedural_field(instance_id(), &config).unwrap();
    let golden = vec![
        (889, 638),
        (698, 692),
        (489, 686),
        (279, 601),
        (189, 485),
        (199, 358),
        (339, 230),
        (530, 176),
        (739, 182),
        (949, 267),
        (1039, 383),
        (1029, 510),
    ];
    assert_eq!(
        nodes
            .iter()
            .map(|n| (n.x as u32, n.y as u32))
            .collect::<Vec<_>>(),
        golden
    );
    assert!(nodes.iter().all(|n| n.kind == "circle"
        && n.x >= 180.0
        && n.y >= 170.0
        && n.x + n.width <= 1080.0
        && n.y + n.height <= 730.0));
    let spiral = ProceduralConfig {
        distribution: FieldDistribution::Spiral,
        ..config.clone()
    };
    let spiral_nodes = realize_procedural_field(instance_id(), &spiral).unwrap();
    let spiral_golden = vec![
        (636, 451),
        (628, 477),
        (583, 497),
        (503, 489),
        (437, 455),
        (407, 396),
        (454, 315),
        (558, 262),
        (708, 245),
        (893, 295),
        (1004, 387),
        (1029, 510),
    ];
    assert_eq!(
        spiral_nodes
            .iter()
            .map(|n| (n.x as u32, n.y as u32))
            .collect::<Vec<_>>(),
        spiral_golden
    );
    assert_eq!(
        nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
        spiral_nodes.iter().map(|n| n.id).collect::<Vec<_>>()
    );
}

#[test]
fn orbit_budget_and_legacy_square_decoding_fail_closed() {
    for distribution in [FieldDistribution::Radial, FieldDistribution::Spiral] {
        let invalid = ProceduralConfig {
            distribution,
            count: 32,
            area_width: 80,
            size: 60,
            ..Default::default()
        };
        assert!(
            realize_procedural_field(instance_id(), &invalid)
                .unwrap_err()
                .to_string()
                .contains("orbit")
        );
    }
    let old_json = serde_json::json!({
        "seed":41,"count":12,"columns":4,"distribution":"scatter",
        "origin_x":180,"origin_y":170,"area_width":900,"area_height":560,
        "size":32,"opacity_percent":100,"fill":"#A5C8DF",
        "reveal_step_frames":0,"reveal_duration_frames":12,
    });
    let restored: ProceduralConfig = serde_json::from_value(old_json).unwrap();
    assert_eq!(restored.shape, ProceduralShape::Square);
    assert_eq!(
        realize_procedural_field(instance_id(), &restored).unwrap(),
        realize_procedural_field(instance_id(), &ProceduralConfig::default()).unwrap()
    );
}
#[test]
fn circle_orbit_mutation_keeps_human_override_and_updates_kind() {
    let mut p = project();
    upsert(&mut p, ProceduralConfig::default()).unwrap();
    let id = procedural_node_id(instance_id(), 1);
    let mut style = p.scenes[0].nodes[1].style.clone();
    style.fill = Some("#E4A621".into());
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id: id,
        style,
    })
    .unwrap();
    let next = ProceduralConfig {
        distribution: FieldDistribution::Radial,
        shape: ProceduralShape::Circle,
        ..Default::default()
    };
    upsert(&mut p, next).unwrap();
    assert_eq!(p.scenes[0].nodes.len(), 12);
    assert!(p.scenes[0].nodes.iter().all(|n| n.kind == "circle"));
    assert_eq!(p.scenes[0].nodes[1].style.fill.as_deref(), Some("#E4A621"));
    p.validate().unwrap();
}

#[test]
fn bounded_orbits_retain_all_nodes_and_stage_boundaries_across_seed_extremes() {
    let id = instance_id();
    for distribution in [FieldDistribution::Radial, FieldDistribution::Spiral] {
        for seed in [0, 1, 41, 0xffff_ffff] {
            for count in [1, 2, 3, 12, 31, 63, 64] {
                let config = ProceduralConfig {
                    seed,
                    distribution,
                    count,
                    columns: 16,
                    shape: ProceduralShape::Circle,
                    ..Default::default()
                };
                let nodes = realize_procedural_field(id, &config).unwrap();
                let repeat = realize_procedural_field(id, &config).unwrap();
                assert_eq!(nodes, repeat);
                assert_eq!(nodes.len(), usize::from(count));
                let all_ids: std::collections::BTreeSet<_> = nodes.iter().map(|n| n.id).collect();
                assert_eq!(all_ids.len(), usize::from(count));
                assert!(nodes.iter().all(|node| node.x >= f64::from(config.origin_x)
                    && node.y >= f64::from(config.origin_y)
                    && node.x + node.width
                        <= f64::from(config.origin_x) + f64::from(config.area_width)
                    && node.y + node.height
                        <= f64::from(config.origin_y) + f64::from(config.area_height)
                    && node.kind == "circle"));
            }
        }
    }
}

fn edge_config(origin_y: u16, reveal_step_frames: u8) -> ProceduralConfig {
    ProceduralConfig {
        count: 1,
        columns: 1,
        distribution: FieldDistribution::Grid,
        origin_x: 0,
        origin_y,
        area_width: 8,
        area_height: 8,
        size: 8,
        reveal_step_frames,
        reveal_duration_frames: 12,
        ..Default::default()
    }
}

#[test]
fn clipped_new_entrances_reject_without_mutating_empty_or_human_edited_projects() {
    let mut saved = project();
    upsert(&mut saved, ProceduralConfig::default()).unwrap();
    let first = saved.scenes[0].nodes[0].clone();
    saved
        .apply_change(&Change::UpdateCanvasStyle {
            scene_id: saved.scenes[0].id,
            node_id: first.id,
            style: NodeStyle {
                fill: Some("#FFAA22".into()),
                ..first.style
            },
        })
        .unwrap();
    for distribution in [
        FieldDistribution::Grid,
        FieldDistribution::Staggered,
        FieldDistribution::Scatter,
        FieldDistribution::Radial,
        FieldDistribution::Spiral,
    ] {
        let config = ProceduralConfig {
            distribution,
            area_width: 16,
            area_height: 16,
            ..edge_config(1064, 1)
        };
        let legacy_nodes = realize_procedural_field(instance_id(), &config).unwrap();
        assert!(
            legacy_nodes
                .iter()
                .all(|node| node.y + node.height <= 1080.0)
        );
        assert!(legacy_nodes.iter().any(|node| {
            node.keyframes
                .iter()
                .any(|key| key.property == MotionProperty::Y && key.value + node.height > 1080.0)
        }));
        for mut current in [project(), saved.clone()] {
            let before = current.clone();
            let error = upsert(&mut current, config.clone()).unwrap_err();
            assert!(error.to_string().contains("entrance exceeds"));
            assert_eq!(current, before);
        }
    }
}

#[test]
fn static_edge_and_exact_animated_edge_keep_authored_positions_and_times() {
    let mut still = project();
    upsert(&mut still, edge_config(1072, 0)).unwrap();
    assert_eq!(
        still.scenes[0].nodes[0].y + still.scenes[0].nodes[0].height,
        1080.0
    );
    assert!(still.scenes[0].nodes[0].keyframes.is_empty());
    let exact = edge_config(1060, 1);
    let mut fitting = project();
    upsert(&mut fitting, exact.clone()).unwrap();
    assert_eq!(fitting.production_design.procedural_fields[0].config, exact);
    let node = &fitting.scenes[0].nodes[0];
    assert_eq!((node.y, node.height), (1060.0, 8.0));
    assert_eq!(
        node.keyframes
            .iter()
            .filter(|key| key.property == MotionProperty::Y)
            .map(|key| (key.at, key.value))
            .collect::<Vec<_>>(),
        vec![
            (RationalTime::ZERO, 1072.0),
            (RationalTime::new(2, 5).unwrap(), 1060.0)
        ]
    );
    let before = fitting.clone();
    assert!(
        upsert(&mut fitting, edge_config(1061, 1))
            .unwrap_err()
            .to_string()
            .contains("entrance exceeds")
    );
    assert_eq!(fitting, before);
}

#[test]
fn historical_clipped_v1_baseline_reopens_validates_and_detaches_without_migration() {
    let config = edge_config(1072, 1);
    let baseline = realize_procedural_field(instance_id(), &config).unwrap();
    let mut legacy = project();
    legacy.scenes[0].nodes = baseline.clone();
    legacy.production_design.procedural_fields.push(
        serde_json::from_value(serde_json::json!({
            "id": instance_id(),
            "scene_id": legacy.scenes[0].id,
            "generator_version": 1,
            "config": config.clone(),
            "baseline": baseline.clone(),
        }))
        .unwrap(),
    );
    legacy.validate().unwrap();
    let bytes = serde_json::to_string(&legacy).unwrap();
    let mut reopened: Project = serde_json::from_str(&bytes).unwrap();
    reopened.validate().unwrap();
    assert_eq!(reopened, legacy);
    assert_eq!(serde_json::to_string(&reopened).unwrap(), bytes);
    assert_eq!(
        realize_procedural_field(
            instance_id(),
            &reopened.production_design.procedural_fields[0].config
        )
        .unwrap(),
        baseline
    );
    assert_eq!(
        baseline[0]
            .keyframes
            .iter()
            .find(|key| key.property == MotionProperty::Y)
            .unwrap()
            .value,
        1084.0
    );
    let before = reopened.clone();
    assert!(
        upsert(&mut reopened, config)
            .unwrap_err()
            .to_string()
            .contains("entrance exceeds")
    );
    assert_eq!(reopened, before);
    reopened
        .apply_change(&Change::DetachProceduralField {
            instance_id: instance_id(),
        })
        .unwrap();
    assert!(reopened.production_design.procedural_fields.is_empty());
    assert_eq!(reopened.scenes[0].nodes, baseline);
    reopened.validate().unwrap();
}

fn influence() -> ProceduralInfluence {
    ProceduralInfluence {
        shape: InfluenceShape::Radial,
        falloff: InfluenceFalloff::Linear,
        center_x: 350,
        center_y: 250,
        radius: 200,
        offset_x: 20,
        offset_y: -10,
        scale_percent: 150,
        rotation_deg: 30,
        fill: "#FFFFFF".into(),
    }
}
fn influenced(field: ProceduralInfluence) -> ProceduralConfig {
    ProceduralConfig {
        distribution: FieldDistribution::Grid,
        count: 3,
        columns: 3,
        origin_x: 200,
        origin_y: 200,
        area_width: 300,
        area_height: 100,
        size: 40,
        fill: "#000000".into(),
        influence: Some(field),
        ..Default::default()
    }
}
#[test]
fn influence_has_exact_cross_language_geometry_color_and_original_identity() {
    let config = influenced(influence());
    let nodes = realize_procedural_field(instance_id(), &config).unwrap();
    let poses = |nodes: &[CanvasNode]| {
        nodes
            .iter()
            .map(|node| {
                (
                    node.x,
                    node.y,
                    node.width,
                    node.rotation_deg,
                    node.style.fill.clone().unwrap(),
                )
            })
            .collect::<Vec<_>>()
    };
    assert_eq!(
        poses(&nodes),
        vec![
            (235.0, 220.0, 50.0, 15.0, "#7F7F7F".into()),
            (340.0, 210.0, 60.0, 30.0, "#FFFFFF".into()),
            (435.0, 220.0, 50.0, 15.0, "#7F7F7F".into()),
        ]
    );
    let mut smooth = influence();
    smooth.center_x = 400;
    smooth.falloff = InfluenceFalloff::Smoothstep;
    let nodes = realize_procedural_field(instance_id(), &influenced(smooth)).unwrap();
    assert_eq!(
        poses(&nodes),
        vec![
            (231.5, 227.5, 43.0, 4.0, "#272727".into()),
            (338.0, 214.0, 56.0, 25.0, "#D7D7D7".into()),
            (438.0, 214.0, 56.0, 25.0, "#D7D7D7".into()),
        ]
    );
    let mut plain = config.clone();
    plain.influence = None;
    assert_eq!(
        nodes.iter().map(|node| node.id).collect::<Vec<_>>(),
        realize_procedural_field(instance_id(), &plain)
            .unwrap()
            .iter()
            .map(|node| node.id)
            .collect::<Vec<_>>()
    );
    let mut descending = config;
    descending.fill = "#FFFFFF".into();
    descending.influence.as_mut().unwrap().fill = "#000000".into();
    assert_eq!(
        realize_procedural_field(instance_id(), &descending).unwrap()[0]
            .style
            .fill
            .as_deref(),
        Some("#808080")
    );
    let legacy = serde_json::to_value(ProceduralConfig::default()).unwrap();
    assert!(legacy.get("influence").is_none());
    let recovered: ProceduralConfig = serde_json::from_value(legacy).unwrap();
    assert_eq!(recovered, ProceduralConfig::default());
}
#[test]
fn influence_reopens_and_updates_the_existing_instance_without_overwriting_independent_human_style()
{
    let mut p = project();
    upsert(&mut p, influenced(influence())).unwrap();
    let node_id = p.scenes[0].nodes[0].id;
    let mut style = p.scenes[0].nodes[0].style.clone();
    style.stroke = Some("#123456".into());
    style.stroke_width = 2.0;
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id,
        style,
    })
    .unwrap();
    let mut next = influence();
    next.offset_x = -20;
    next.fill = "#40A0FF".into();
    upsert(&mut p, influenced(next)).unwrap();
    let reopened: Project = serde_json::from_slice(&serde_json::to_vec(&p).unwrap()).unwrap();
    reopened.validate().unwrap();
    assert_eq!(
        reopened.scenes[0].nodes[0].style.stroke.as_deref(),
        Some("#123456")
    );
    assert_eq!(
        reopened.production_design.procedural_fields[0]
            .config
            .influence
            .as_ref()
            .unwrap()
            .offset_x,
        -20
    );
    let mut bad = influence();
    bad.offset_x = 1920;
    let before = p.clone();
    assert!(upsert(&mut p, influenced(bad)).is_err());
    assert_eq!(p, before);
    let mut sequence = influence();
    sequence.rotation_deg = 0;
    let config = ProceduralConfig {
        reveal_step_frames: 3,
        influence: Some(sequence),
        ..influenced(influence())
    };
    upsert(&mut p, config).unwrap();
    for node in &p.production_design.procedural_fields[0].baseline {
        let keys = node
            .keyframes
            .iter()
            .filter(|key| key.property == MotionProperty::Y)
            .collect::<Vec<_>>();
        assert_eq!(keys.first().unwrap().value, node.y + 12.0);
        assert_eq!(keys.last().unwrap().value, node.y);
    }
}
#[test]
fn influence_rejects_unsafe_bounds_and_unadmitted_rotating_entrances() {
    let mut p = project();
    let before = p.clone();
    let mut bad = influence();
    bad.radius = 0;
    assert!(upsert(&mut p, influenced(bad)).is_err());
    assert_eq!(p, before);
    let mut bad = influence();
    bad.scale_percent = 400;
    assert!(upsert(&mut p, influenced(bad)).is_err());
    assert_eq!(p, before);
    let mut malformed = serde_json::to_value(influence()).unwrap();
    malformed["effect_grant"] = serde_json::json!("forbidden");
    assert!(serde_json::from_value::<ProceduralInfluence>(malformed).is_err());
}

#[test]
fn influence_updates_honor_project_and_scene_timing_locks_before_any_source_mutation() {
    for project_scope in [false, true] {
        let mut p = project();
        upsert(&mut p, influenced(influence())).unwrap();
        let resource = if project_scope {
            format!("project:{}", p.id)
        } else {
            format!("scene:{}", p.scenes[0].id)
        };
        p.apply_change(&Change::SetLock {
            resource,
            kind: LockKind::Timing,
            note: "Keep authored timing".into(),
        })
        .unwrap();
        let before = p.clone();
        let mut field = influence();
        field.offset_x = -20;
        assert!(upsert(&mut p, influenced(field)).is_err());
        assert_eq!(p, before);
    }
}

#[test]
fn influenced_orientation_remains_constant_during_each_exact_staggered_entrance() {
    let config = ProceduralConfig {
        reveal_step_frames: 3,
        ..influenced(influence())
    };
    let mut p = project();
    upsert(&mut p, config).unwrap();
    let nodes = &p.scenes[0].nodes;
    assert_eq!(
        nodes
            .iter()
            .map(|node| node.rotation_deg as i32)
            .collect::<Vec<_>>(),
        vec![15, 30, 15]
    );
    assert!(nodes.iter().all(|node| {
        node.keyframes.len() >= 4
            && node
                .keyframes
                .iter()
                .all(|key| key.property != MotionProperty::RotationDeg)
    }));
    let reopened: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    assert_eq!(reopened, p);
}

fn authored_path() -> ProceduralConfig {
    ProceduralConfig {
        distribution: FieldDistribution::Path,
        count: 4,
        origin_x: 100,
        origin_y: 100,
        area_width: 420,
        area_height: 220,
        size: 20,
        path: Some(ProceduralPath {
            closed: false,
            points: vec![
                ProceduralPathPoint {
                    x_percent: 0,
                    y_percent: 0,
                },
                ProceduralPathPoint {
                    x_percent: 100,
                    y_percent: 0,
                },
                ProceduralPathPoint {
                    x_percent: 100,
                    y_percent: 100,
                },
            ],
        }),
        ..Default::default()
    }
}
#[test]
fn editable_path_distance_matches_studio_with_unequal_and_reversed_segments() {
    let mut config = authored_path();
    let nodes = realize_procedural_field(instance_id(), &config).unwrap();
    let coordinates = |nodes: &[CanvasNode]| {
        nodes
            .iter()
            .map(|node| (node.x, node.y))
            .collect::<Vec<_>>()
    };
    assert_eq!(
        coordinates(&nodes),
        vec![
            (100.0, 100.0),
            (300.0, 100.0),
            (500.0, 100.0),
            (500.0, 300.0)
        ]
    );
    config.path.as_mut().unwrap().points.reverse();
    let reversed = realize_procedural_field(instance_id(), &config).unwrap();
    assert_eq!(
        coordinates(&reversed),
        vec![
            (500.0, 300.0),
            (500.0, 100.0),
            (300.0, 100.0),
            (100.0, 100.0)
        ]
    );
    assert_eq!(
        nodes.iter().map(|node| node.id).collect::<Vec<_>>(),
        reversed.iter().map(|node| node.id).collect::<Vec<_>>()
    );
    config.count = 1;
    assert_eq!(
        coordinates(&realize_procedural_field(instance_id(), &config).unwrap()),
        vec![(500.0, 300.0)]
    );
}
#[test]
fn closed_path_does_not_duplicate_an_endpoint_and_reopens_with_editable_points() {
    let mut config = authored_path();
    config.area_width = 220;
    let path = config.path.as_mut().unwrap();
    path.closed = true;
    path.points.push(ProceduralPathPoint {
        x_percent: 0,
        y_percent: 100,
    });
    let mut p = project();
    upsert(&mut p, config.clone()).unwrap();
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .map(|node| (node.x, node.y))
            .collect::<Vec<_>>(),
        vec![
            (100.0, 100.0),
            (300.0, 100.0),
            (300.0, 300.0),
            (100.0, 300.0)
        ]
    );
    let reopened: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    reopened.validate().unwrap();
    assert_eq!(reopened, p);
    assert_eq!(
        reopened.production_design.procedural_fields[0].config.path,
        config.path
    );
}
#[test]
fn path_rejects_degenerate_segments_limits_and_inactive_source_without_mutation() {
    let good = authored_path();
    let mut cases = vec![];
    let mut missing = good.clone();
    missing.path = None;
    cases.push(missing);
    let mut closed = good.clone();
    closed.path.as_mut().unwrap().closed = true;
    closed.path.as_mut().unwrap().points.pop();
    cases.push(closed);
    let mut duplicate = good.clone();
    let first = duplicate.path.as_ref().unwrap().points[0];
    duplicate.path.as_mut().unwrap().points[1] = first;
    cases.push(duplicate);
    let mut outside = good.clone();
    outside.path.as_mut().unwrap().points[0].x_percent = 101;
    cases.push(outside);
    let mut capacity = good.clone();
    capacity.path.as_mut().unwrap().points = vec![
        ProceduralPathPoint {
            x_percent: 0,
            y_percent: 0
        };
        17
    ];
    cases.push(capacity);
    let mut collapsed = good.clone();
    collapsed.area_width = 21;
    collapsed.area_height = 21;
    collapsed.path.as_mut().unwrap().points = vec![
        ProceduralPathPoint {
            x_percent: 0,
            y_percent: 0,
        },
        ProceduralPathPoint {
            x_percent: 1,
            y_percent: 1,
        },
    ];
    cases.push(collapsed);
    let mut hidden = good.clone();
    hidden.distribution = FieldDistribution::Grid;
    cases.push(hidden);
    for config in cases {
        let mut p = project();
        let before = p.clone();
        assert!(upsert(&mut p, config).is_err());
        assert_eq!(p, before);
    }
}
#[test]
fn adaptive_grid_reflows_count_and_field_aspect_without_reducing_size_or_overrides() {
    let mut config = ProceduralConfig {
        distribution: FieldDistribution::AdaptiveGrid,
        ..Default::default()
    };
    let nodes = realize_procedural_field(instance_id(), &config).unwrap();
    assert_eq!(
        (nodes[0].x, nodes[0].y, nodes[4].x, nodes[4].y),
        (276.0, 247.0, 276.0, 433.0)
    );
    config.count = 20;
    let twenty = realize_procedural_field(instance_id(), &config).unwrap();
    assert_eq!(twenty[0].x, twenty[5].x);
    assert!(
        twenty
            .iter()
            .all(|node| node.width == 32.0 && node.height == 32.0)
    );
    config.count = 12;
    config.area_width = 200;
    config.columns = 16;
    let portrait = realize_procedural_field(instance_id(), &config).unwrap();
    assert_eq!(
        (portrait[0].x, portrait[1].x, portrait[2].x),
        (214.0, 314.0, 214.0)
    );
    let mut p = project();
    upsert(&mut p, authored_path()).unwrap();
    let ids = p.scenes[0]
        .nodes
        .iter()
        .map(|node| node.id)
        .collect::<Vec<_>>();
    let mut style = p.scenes[0].nodes[0].style.clone();
    style.fill = Some("#FFAA22".into());
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id: ids[0],
        style,
    })
    .unwrap();
    config.count = 4;
    upsert(&mut p, config).unwrap();
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .map(|node| node.id)
            .collect::<Vec<_>>(),
        ids
    );
    assert_eq!(p.scenes[0].nodes[0].style.fill.as_deref(), Some("#FFAA22"));
    let before = p.clone();
    assert!(
        upsert(
            &mut p,
            ProceduralConfig {
                distribution: FieldDistribution::AdaptiveGrid,
                count: 2,
                area_width: 20,
                area_height: 20,
                size: 20,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert_eq!(p, before);
}
