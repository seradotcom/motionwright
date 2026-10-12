use motionwright_domain::*;
use uuid::Uuid;

fn config() -> HeroConfig {
    HeroConfig {
        layout: HeroLayout::StepSequence,
        eyebrow: "WORKFLOW / AUTHOR MARKED STEP".into(),
        headline: "Inspect the intended result".into(),
        body:
            "Review the native project state and confirm the change before approving the next step."
                .into(),
        wordmark: "02 / 04".into(),
        accent: "#D9A46E".into(),
        ..HeroConfig::default()
    }
}

#[test]
fn step_sequence_reflows_to_three_aspects_without_recreating_editable_objects() {
    let id = Uuid::new_v4();
    let original = realize_product_hero(id, &HeroConfig::default(), 1920, 1080).unwrap();
    let stable: Vec<_> = original.iter().map(|n| n.id).collect();
    for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
        let nodes = realize_product_hero(id, &config(), width, height).unwrap();
        assert_eq!(nodes.len(), 6);
        assert_eq!(nodes.iter().map(|n| n.id).collect::<Vec<_>>(), stable);
        assert!(nodes.iter().all(|n| n.name.starts_with("StepSequence / ")));
        for node in &nodes {
            node.validate().unwrap();
            assert!(node.x >= 0.0 && node.y >= 0.0);
            assert!(node.x + node.width <= f64::from(width));
            assert!(node.y + node.height <= f64::from(height));
        }
        let rule = nodes.iter().find(|n| n.name.ends_with("rule")).unwrap();
        assert_eq!(rule.style.fill.as_deref(), Some("#D9A46E"));
        assert_eq!(rule.height > rule.width, width > height);
        assert_eq!(
            nodes
                .iter()
                .find(|n| n.name.ends_with("disclosure"))
                .unwrap()
                .text
                .as_deref(),
            Some("STEP SEQUENCE / EDITORIAL STUDY · NOT AN OBSERVED WORKFLOW")
        );
        let index = nodes.iter().find(|n| n.name.ends_with("wordmark")).unwrap();
        assert_eq!(index.text.as_deref(), Some("02 / 04"));
        assert_eq!(index.style.fill.as_deref(), Some("#D9A46E"));
    }
}

#[test]
fn step_sequence_entrance_is_canonical_and_static_equivalent_is_honest() {
    let id = Uuid::new_v4();
    let moving = realize_product_hero(id, &config(), 1920, 1080).unwrap();
    assert!(moving.iter().all(|n| !n.keyframes.is_empty()));
    assert!(
        moving
            .iter()
            .flat_map(|n| &n.keyframes)
            .all(|k| k.interpolation == MotionInterpolation::Hold
                || k.interpolation == MotionInterpolation::EaseOutCubic)
    );
    assert!(
        moving
            .iter()
            .flat_map(|n| &n.keyframes)
            .all(|k| k.property != MotionProperty::RotationDeg)
    );
    let still = realize_product_hero(
        id,
        &HeroConfig {
            motion: false,
            ..config()
        },
        1920,
        1080,
    )
    .unwrap();
    assert!(still.iter().all(|n| n.keyframes.is_empty()));
    assert!(
        still
            .iter()
            .find(|n| n.name.ends_with("disclosure"))
            .unwrap()
            .text
            .as_ref()
            .unwrap()
            .contains("NOT AN OBSERVED")
    );
}

#[test]
fn saved_component_stays_editable_through_exact_json_roundtrip() {
    let mut p = Project::new("Original workflow step").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Native Step 02".into(),
        objective: "An original, editable explanation of this authored step".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scene_id = p.scenes[0].id;
    let component_id = Uuid::new_v4();
    p.apply_change(&Change::UpsertProductHero {
        instance_id: component_id,
        scene_id,
        config: config(),
    })
    .unwrap();
    assert_eq!(p.production_design.heroes.len(), 1);
    assert_eq!(p.scenes[0].nodes.len(), 6);
    assert_eq!(p.scenes[0].status, SceneStatus::Draft);
    let before = p.clone();
    let restored: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    restored.validate().unwrap();
    assert_eq!(restored, before);
    assert_eq!(
        restored.production_design.heroes[0].config.layout,
        HeroLayout::StepSequence
    );
}

#[test]
fn human_text_override_survives_a_compatible_family_switch() {
    let mut p = Project::new("Compatible creative source").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Scene".into(),
        objective: "Explain one step".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let id = Uuid::new_v4();
    let scene_id = p.scenes[0].id;
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id,
        config: HeroConfig::default(),
    })
    .unwrap();
    let eyebrow = hero_node_id(id, "eyebrow");
    p.scenes[0]
        .nodes
        .iter_mut()
        .find(|n| n.id == eyebrow)
        .unwrap()
        .text = Some("Owner-edited editorial context".into());
    let new = HeroConfig {
        eyebrow: HeroConfig::default().eyebrow,
        ..config()
    };
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id,
        config: new.clone(),
    })
    .unwrap();
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == eyebrow)
            .unwrap()
            .text
            .as_deref(),
        Some("Owner-edited editorial context")
    );
    let body = hero_node_id(id, "body");
    p.scenes[0]
        .nodes
        .iter_mut()
        .find(|n| n.id == body)
        .unwrap()
        .text = Some("Human source explanation preserved".into());
    let before = p.clone();
    let error = p
        .apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id,
            config: HeroConfig {
                body: "A conflicting regenerated fact".into(),
                ..new
            },
        })
        .unwrap_err();
    assert!(error.to_string().contains("override conflict"));
    assert_eq!(p, before, "no partial loss of human editorial work");
}

#[test]
fn nonbreakable_copied_statements_and_scope_locks_fail_closed() {
    let id = Uuid::new_v4();
    assert!(
        realize_product_hero(
            id,
            &HeroConfig {
                headline: "X".repeat(20),
                ..config()
            },
            1920,
            1080
        )
        .is_err()
    );
    let mut p = Project::new("Locked narrative step").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Scene".into(),
        objective: "One authored step".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scene_id = p.scenes[0].id;
    p.locks.push(ProjectLock {
        id: Uuid::new_v4(),
        resource: format!("scene:{scene_id}"),
        kind: LockKind::Content,
        note: "Reviewed and protected".into(),
    });
    let before = p.clone();
    assert!(
        p.apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id,
            config: config(),
        })
        .is_err()
    );
    assert_eq!(p, before);
}
