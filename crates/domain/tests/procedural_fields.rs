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
