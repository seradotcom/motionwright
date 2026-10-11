use motionwright_domain::*;
use uuid::Uuid;

fn project() -> Project {
    let mut project = Project::new("Creative production").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Hero".into(),
            objective: "A typographic product reveal, not fabricated UI evidence".into(),
            duration_seconds: 6,
        })
        .unwrap();
    project
}
fn insert(project: &mut Project, id: Uuid, config: HeroConfig) {
    project
        .apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id: project.scenes[0].id,
            config,
        })
        .unwrap();
}
#[test]
fn split_explanation_reflows_without_changing_stable_object_identity() {
    let id = Uuid::new_v4();
    let original = realize_product_hero(id, &HeroConfig::default(), 1920, 1080).unwrap();
    let config = HeroConfig {
        layout: HeroLayout::SplitExplanation,
        wordmark: "DETAIL".into(),
        headline: "First, the problem".into(),
        body: "Then, explain the solution with original copy that can be edited.".into(),
        ..HeroConfig::default()
    };
    for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
        let nodes = realize_product_hero(id, &config, width, height).unwrap();
        assert_eq!(nodes.len(), 6);
        assert_eq!(
            nodes.iter().map(|node| node.id).collect::<Vec<_>>(),
            original.iter().map(|node| node.id).collect::<Vec<_>>()
        );
        for node in &nodes {
            assert!(node.name.starts_with("SplitExplanation / "));
            assert!(node.x >= 0.0 && node.y >= 0.0);
            assert!(node.x + node.width <= f64::from(width));
            assert!(node.y + node.height <= f64::from(height));
        }
        let rule = nodes
            .iter()
            .find(|node| node.name.ends_with("rule"))
            .unwrap();
        if width > height {
            assert!(rule.height > rule.width, "landscape is column-based");
        } else {
            assert!(rule.width > rule.height, "square/portrait are stacked");
        }
        let disclosure = nodes
            .iter()
            .find(|node| node.name.ends_with("disclosure"))
            .unwrap();
        assert!(
            disclosure
                .text
                .as_deref()
                .unwrap()
                .contains("NOT A CAPTURE")
        );
    }
    // A missing layout in a legitimate older creative document is a real
    // default, not a refused migration, changed renderer or renamed node.
    let mut legacy = serde_json::to_value(HeroConfig::default()).unwrap();
    legacy.as_object_mut().unwrap().remove("layout");
    let restored: HeroConfig = serde_json::from_value(legacy).unwrap();
    assert_eq!(restored.layout, HeroLayout::ProductHeroReveal);
    assert_eq!(
        realize_product_hero(id, &restored, 1920, 1080).unwrap(),
        original
    );
}

#[test]
fn switching_component_family_is_atomic_and_respects_human_copy() {
    let mut project = project();
    let id = Uuid::new_v4();
    insert(&mut project, id, HeroConfig::default());
    let eyebrow = hero_node_id(id, "eyebrow");
    let body = hero_node_id(id, "body");
    project
        .apply_change(&Change::UpdateCanvasText {
            scene_id: project.scenes[0].id,
            node_id: eyebrow,
            text: Some("This measured statement is human-authored.".into()),
        })
        .unwrap();
    let before_revision = project.revision;
    insert(
        &mut project,
        id,
        HeroConfig {
            layout: HeroLayout::SplitExplanation,
            wordmark: "DETAIL".into(),
            ..HeroConfig::default()
        },
    );
    assert_eq!(project.revision, before_revision);
    assert_eq!(
        project.scenes[0]
            .nodes
            .iter()
            .find(|node| node.id == eyebrow)
            .unwrap()
            .text
            .as_deref(),
        Some("This measured statement is human-authored.")
    );
    project
        .apply_change(&Change::UpdateCanvasText {
            scene_id: project.scenes[0].id,
            node_id: body,
            text: Some("Independent human narrative".into()),
        })
        .unwrap();
    let prior = project.clone();
    assert!(
        project
            .apply_change(&Change::UpsertProductHero {
                instance_id: id,
                scene_id: project.scenes[0].id,
                config: HeroConfig {
                    layout: HeroLayout::SplitExplanation,
                    body: "A conflicting regeneration".into(),
                    ..HeroConfig::default()
                },
            })
            .is_err()
    );
    assert_eq!(project, prior, "human overrides cannot be lost on conflict");
}

#[test]
fn hero_identity_and_generated_base_are_stable() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(&mut p, id, HeroConfig::default());
    let baseline = p.production_design.heroes[0].clone();
    assert_eq!(p.scenes[0].nodes.len(), 6);
    insert(&mut p, id, HeroConfig::default());
    assert_eq!(baseline, p.production_design.heroes[0]);
    assert_eq!(p.scenes[0].nodes, baseline.baseline);
    assert_eq!(
        p,
        serde_json::from_str::<Project>(&serde_json::to_string(&p).unwrap()).unwrap()
    );
}
#[test]
fn legacy_project_defaults_to_empty_design_without_migrating_authority() {
    let p = project();
    let mut json = serde_json::to_value(&p).unwrap();
    json.as_object_mut().unwrap().remove("production_design");
    let restored: Project = serde_json::from_value(json).unwrap();
    restored.validate().unwrap();
    assert_eq!(restored.generation, p.generation);
    assert_eq!(restored.production_design, ProductionDesign::default());
}
#[test]
fn compatible_human_copy_survives_a_brand_update() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(&mut p, id, HeroConfig::default());
    let node_id = hero_node_id(id, "body");
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: p.scenes[0].id,
        node_id,
        text: Some("This sentence was written by a person.".into()),
    })
    .unwrap();
    let config = HeroConfig {
        accent: "#FADCA2".into(),
        ..HeroConfig::default()
    };
    insert(&mut p, id, config);
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == node_id)
            .unwrap()
            .text
            .as_deref(),
        Some("This sentence was written by a person.")
    );
}
#[test]
fn conflicting_copy_or_deleted_node_aborts_component_update_atomically() {
    for deleted in [false, true] {
        let mut p = project();
        let id = Uuid::new_v4();
        insert(&mut p, id, HeroConfig::default());
        let node_id = hero_node_id(id, "body");
        if deleted {
            p.apply_change(&Change::RemoveCanvasNode {
                scene_id: p.scenes[0].id,
                node_id,
            })
            .unwrap();
        } else {
            p.apply_change(&Change::UpdateCanvasText {
                scene_id: p.scenes[0].id,
                node_id,
                text: Some("Human copy".into()),
            })
            .unwrap();
        }
        let before = p.clone();
        let config = HeroConfig {
            body: "New generated copy".into(),
            ..HeroConfig::default()
        };
        assert!(
            p.apply_change(&Change::UpsertProductHero {
                instance_id: id,
                scene_id: p.scenes[0].id,
                config
            })
            .is_err()
        );
        assert_eq!(p, before);
    }
}
#[test]
fn locked_component_style_rejects_update_and_detach_preserves_nodes() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(&mut p, id, HeroConfig::default());
    p.apply_change(&Change::SetNodePropertyLock {
        scene_id: p.scenes[0].id,
        node_id: hero_node_id(id, "wordmark"),
        property: NodeProperty::Style,
        locked: true,
    })
    .unwrap();
    let config = HeroConfig {
        accent: "#FADCA2".into(),
        ..HeroConfig::default()
    };
    let before = p.clone();
    assert!(
        p.apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id: p.scenes[0].id,
            config
        })
        .is_err()
    );
    assert_eq!(before, p);
    p.apply_change(&Change::DetachProductHero { instance_id: id })
        .unwrap();
    assert_eq!(before.scenes[0].nodes, p.scenes[0].nodes);
    assert!(p.production_design.heroes.is_empty());
}
#[test]
fn three_aspects_are_reflowed_and_extreme_copy_is_explicitly_rejected() {
    let id = Uuid::new_v4();
    for (w, h) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
        let nodes = realize_product_hero(id, &HeroConfig::default(), w, h).unwrap();
        for node in &nodes {
            assert!(node.x >= 0.0 && node.y >= 0.0);
            assert!(node.x + node.width <= f64::from(w));
            assert!(node.y + node.height <= f64::from(h));
            assert_eq!(
                node.id,
                hero_node_id(id, node.name.split(" / ").last().unwrap())
            );
        }
    }
    let config = HeroConfig {
        headline: "a".repeat(65),
        ..HeroConfig::default()
    };
    assert!(realize_product_hero(id, &config, 1080, 1920).is_err());
    let config = HeroConfig {
        headline: "a".repeat(25),
        ..HeroConfig::default()
    };
    assert!(realize_product_hero(id, &config, 1080, 1920).is_err());
}
#[test]
fn patch_preview_is_pure_and_commit_is_atomic_on_failure() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(&mut p, id, HeroConfig::default());
    let patch = CreativePatch {
        scene_id: p.scenes[0].id,
        base_revision: p.revision,
        rationale: "Tighten one line without regenerating the shot".into(),
        edits: vec![ScopedCanvasEdit::Text {
            node_id: hero_node_id(id, "body"),
            text: "Revised line".into(),
        }],
    };
    let before = p.clone();
    let preview = p.preview_creative_patch(&patch).unwrap();
    assert_eq!(p, before);
    assert_eq!(preview.before.len(), 1);
    assert_eq!(preview.after[0].text.as_deref(), Some("Revised line"));
    assert_eq!(preview.dirty_end, p.scenes[0].duration);
    let mut bad = patch.clone();
    bad.edits.push(ScopedCanvasEdit::Text {
        node_id: Uuid::new_v4(),
        text: "Outside selection".into(),
    });
    assert!(
        p.apply_change(&Change::ApplyCreativePatch { patch: bad })
            .is_err()
    );
    assert_eq!(p, before);
    p.apply_change(&Change::ApplyCreativePatch { patch })
        .unwrap();
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == hero_node_id(id, "body"))
            .unwrap()
            .text
            .as_deref(),
        Some("Revised line")
    );
}
#[test]
fn patch_never_bypasses_property_locks_or_revision_preconditions() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(&mut p, id, HeroConfig::default());
    let node_id = hero_node_id(id, "body");
    let patch = CreativePatch {
        scene_id: p.scenes[0].id,
        base_revision: p.revision + 1,
        rationale: "Edit".into(),
        edits: vec![ScopedCanvasEdit::Text {
            node_id,
            text: "No".into(),
        }],
    };
    assert!(p.preview_creative_patch(&patch).is_err());
    p.apply_change(&Change::SetNodePropertyLock {
        scene_id: p.scenes[0].id,
        node_id,
        property: NodeProperty::Text,
        locked: true,
    })
    .unwrap();
    assert!(
        p.preview_creative_patch(&CreativePatch {
            base_revision: p.revision,
            ..patch
        })
        .is_err()
    );
}
fn plan(p: &Project) -> ProductionPlan {
    ProductionPlan {
        objective: "Explain an editable creative workflow".into(),
        audience: "Software teams".into(),
        concept: "Typography carries the central idea".into(),
        reference_constraints: vec!["Rhythm follows meaning, not arbitrary cuts".into()],
        exclusions: vec!["Do not fabricate application screenshots".into()],
        shots: vec![PlannedShot {
            scene_id: p.scenes[0].id,
            purpose: "State the concept".into(),
            claim_ids: vec![],
            asset_ids: vec![],
            evidence_kind: NarrativeEvidenceKind::GraphicStudy,
        }],
        clock: ProductionClock::Timeline,
        approval: None,
    }
}
#[test]
fn plan_approval_is_content_bound_and_graphics_are_not_capture_evidence() {
    let mut p = project();
    let mut plan = plan(&p);
    plan.approval = Some(PlanApproval {
        reviewer: "Owner".into(),
        note: "Approve this concept for production".into(),
        content_sha256: plan.content_digest().unwrap(),
    });
    p.apply_change(&Change::SetProductionPlan {
        plan: Some(plan.clone()),
    })
    .unwrap();
    plan.concept = "Different concept".into();
    assert!(
        p.apply_change(&Change::SetProductionPlan {
            plan: Some(plan.clone())
        })
        .is_err()
    );
    plan.approval = None;
    plan.shots[0].evidence_kind = NarrativeEvidenceKind::RealCapture;
    assert!(
        p.apply_change(&Change::SetProductionPlan { plan: Some(plan) })
            .is_err()
    );
}
#[test]
fn capsule_is_an_attachment_not_a_runtime_grant() {
    let mut p = project();
    let asset_id = Uuid::new_v4();
    let hash = "ab".repeat(32);
    p.apply_change(&Change::AddAsset {
        asset: Asset {
            id: asset_id,
            name: "Authored source".into(),
            media_type: "application/zip".into(),
            content_sha256: Some(hash.clone()),
            source_revision: None,
        },
    })
    .unwrap();
    let capsule = NativeCapsule {
        id: Uuid::new_v4(),
        scene_id: p.scenes[0].id,
        source_asset_id: asset_id,
        source_sha256: hash,
        label: "Native source preserved".into(),
        fidelity: FidelityReport {
            renderer: "hyperframes".into(),
            renderer_version: "unverified".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Unavailable,
            losses: vec![
                "Opaque source only; renderer execution and parameter edits are not admitted"
                    .into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: vec![],
        native_editor_hint: "Open in the source application's trusted environment".into(),
    };
    p.apply_change(&Change::UpsertNativeCapsule {
        capsule: capsule.clone(),
    })
    .unwrap();
    assert!(!capsule.fidelity.usable_without_loss_consent());
    let mut forged = serde_json::to_value(capsule).unwrap();
    forged["execute_command"] = serde_json::json!("untrusted-code");
    assert!(serde_json::from_value::<NativeCapsule>(forged).is_err());
}

#[test]
fn metric_evidence_reflows_without_inventing_attested_sources_or_resetting_object_ids() {
    let id = Uuid::new_v4();
    let original = realize_product_hero(id, &HeroConfig::default(), 1920, 1080).unwrap();
    let config = HeroConfig {
        layout: HeroLayout::MetricEvidence,
        eyebrow: "USER AUTHORED / NOT VERIFIED".into(),
        headline: "42% growth".into(),
        body: "A reported figure needs a source, date and independently checked evidence.".into(),
        wordmark: "DATA".into(),
        accent: "#D9A46E".into(),
        ..HeroConfig::default()
    };
    for (w, h) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
        let nodes = realize_product_hero(id, &config, w, h).unwrap();
        assert_eq!(nodes.len(), HERO_ROLES.len());
        assert_eq!(
            nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
            original.iter().map(|n| n.id).collect::<Vec<_>>(),
        );
        for node in &nodes {
            assert!(node.name.starts_with("MetricEvidence / "));
            assert!(node.x >= 0.0 && node.y >= 0.0);
            assert!(node.x + node.width <= f64::from(w));
            assert!(node.y + node.height <= f64::from(h));
        }
        let rule = nodes.iter().find(|n| n.name.ends_with("rule")).unwrap();
        if w > h {
            assert!(rule.height > rule.width);
        } else {
            assert!(rule.width > rule.height);
        }
        let fact = nodes.iter().find(|n| n.name.ends_with("headline")).unwrap();
        assert_eq!(fact.text.as_deref(), Some("42% growth"));
        assert_eq!(fact.style.fill.as_deref(), Some("#D9A46E"));
        let disclosure = nodes
            .iter()
            .find(|n| n.name.ends_with("disclosure"))
            .unwrap();
        assert_eq!(
            disclosure.text.as_deref(),
            Some("METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED"),
        );
    }
    let serde = serde_json::to_string(&config).unwrap();
    let restored: HeroConfig = serde_json::from_str(&serde).unwrap();
    assert_eq!(restored.layout, HeroLayout::MetricEvidence);
    let quiet = HeroConfig {
        motion: false,
        ..config
    };
    assert!(
        realize_product_hero(id, &quiet, 1920, 1080)
            .unwrap()
            .iter()
            .all(|n| n.keyframes.is_empty())
    );
}

#[test]
fn metric_evidence_family_change_protects_human_copy_and_rejects_conflicts() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(&mut p, id, HeroConfig::default());
    let eyebrow = hero_node_id(id, "eyebrow");
    let body = hero_node_id(id, "body");
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: p.scenes[0].id,
        node_id: eyebrow,
        text: Some("Human-authored source context".into()),
    })
    .unwrap();
    let config = HeroConfig {
        layout: HeroLayout::MetricEvidence,
        headline: "42% growth".into(),
        wordmark: "DATA".into(),
        ..HeroConfig::default()
    };
    insert(&mut p, id, config.clone());
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == eyebrow)
            .unwrap()
            .text
            .as_deref(),
        Some("Human-authored source context"),
    );
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == body)
            .unwrap()
            .name,
        "MetricEvidence / body",
    );
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: p.scenes[0].id,
        node_id: body,
        text: Some("Human-controlled explanation".into()),
    })
    .unwrap();
    let original = p.clone();
    assert!(
        p.apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id: p.scenes[0].id,
            config: HeroConfig {
                body: "An incompatible regenerated metric claim".into(),
                ..config
            },
        })
        .is_err()
    );
    assert_eq!(
        p, original,
        "conflicts must not partially apply a generated metric"
    );
}

#[test]
fn metric_source_disclosure_is_frozen_until_explicit_detach() {
    let mut p = project();
    let id = Uuid::new_v4();
    insert(
        &mut p,
        id,
        HeroConfig {
            layout: HeroLayout::MetricEvidence,
            headline: "42% growth".into(),
            body: "Reported figures require measured source evidence.".into(),
            wordmark: "DATA".into(),
            ..HeroConfig::default()
        },
    );
    let scene_id = p.scenes[0].id;
    let node_id = hero_node_id(id, "disclosure");
    assert!(p.scenes[0].nodes.iter().any(|node| node.id == node_id
        && node.text.as_deref()
            == Some("METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED")));

    let notice = p.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == node_id)
        .unwrap();
    let hidden = CanvasTransform {
        x: notice.x,
        y: notice.y,
        width: notice.width,
        height: notice.height,
        rotation_deg: notice.rotation_deg,
        opacity: 0.0,
    };
    for bad in [
        Change::TransformCanvasNode {
            scene_id,
            node_id,
            transform: hidden,
        },
        Change::UpdateCanvasText {
            scene_id,
            node_id,
            text: Some("This is confirmed evidence".into()),
        },
        Change::RemoveCanvasNode { scene_id, node_id },
    ] {
        let before = p.clone();
        let mut staging = p.clone();
        let error = staging.apply_change(&bad).unwrap_err();
        assert!(
            error.to_string().contains("disclosure"),
            "bad edit must fail because the bound notice is protected: {error}",
        );
        assert_eq!(
            p, before,
            "source state stays untouched in caller workspace"
        );
    }

    // The operator can still add locks: those do not change visual content.
    p.apply_change(&Change::SetNodePropertyLock {
        scene_id,
        node_id,
        property: NodeProperty::Text,
        locked: true,
    })
    .unwrap();

    p.apply_change(&Change::DetachProductHero { instance_id: id })
        .unwrap();
    assert!(p.production_design.heroes.is_empty());
    assert!(p.scenes[0].nodes.iter().any(|node| node.id == node_id));
}
