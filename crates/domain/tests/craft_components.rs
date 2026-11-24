use motionwright_domain::*;
use uuid::Uuid;

const INSTANCE: &str = "00000000-0000-4000-8000-000000000005";
fn instance() -> Uuid {
    Uuid::parse_str(INSTANCE).unwrap()
}
fn config(layout: HeroLayout) -> HeroConfig {
    HeroConfig {
        layout,
        eyebrow: "EDITORIAL / ORIGINAL STUDY".into(),
        headline: "A deliberate original idea".into(),
        body: "An independently authored supporting idea with clear intent".into(),
        wordmark: "MW".into(),
        foreground: "#F2F4F3".into(),
        accent: "#86C8D1".into(),
        motion: true,
    }
}
fn project() -> Project {
    let mut p = Project::new("Original editorial craft family").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Original craft".into(),
        objective: "Original visual concept; no measured or testimonial assertion".into(),
        duration_seconds: 6,
    })
    .unwrap();
    p
}
#[test]
fn original_contrast_and_illustrative_quote_are_editable_and_reflow_three_aspects() {
    let old = realize_product_hero(instance(), &HeroConfig::default(), 1920, 1080).unwrap();
    let identity = old.iter().map(|node| node.id).collect::<Vec<_>>();
    for family in [HeroLayout::ContrastComparison, HeroLayout::EditorialQuote] {
        let cfg = config(family);
        let mut sources = Vec::new();
        for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
            let nodes = realize_product_hero(instance(), &cfg, width, height).unwrap();
            assert_eq!(nodes.len(), HERO_ROLES.len());
            assert_eq!(nodes.iter().map(|n| n.id).collect::<Vec<_>>(), identity);
            assert_eq!(
                nodes,
                realize_product_hero(instance(), &cfg, width, height).unwrap()
            );
            for node in &nodes {
                assert!(node.x >= f64::from(width) * 0.05);
                assert!(node.y >= f64::from(height) * 0.05);
                assert!(node.x + node.width <= f64::from(width) * 0.96);
                assert!(node.y + node.height <= f64::from(height) * 0.96);
                assert!(node.style.fill.is_some());
                assert!(!node.property_locks.contains(&NodeProperty::Text));
                assert!(!node.property_locks.contains(&NodeProperty::Position));
                assert!(
                    (4..=6).contains(&node.keyframes.len()),
                    "motion-enabled craft node must have editable bounded Y/opacity entrance keys"
                );
            }
            let rule = &nodes[4];
            assert_eq!(rule.kind, "rectangle");
            if family == HeroLayout::ContrastComparison {
                assert_eq!(rule.height > rule.width, width > height);
                assert_eq!(
                    nodes[5].text.as_deref(),
                    Some("CONCEPTUAL COMPARISON / NOT MEASURED EVIDENCE")
                );
                // Comparison states always remain distinct layouts instead
                // of a renamed copy of SplitExplanation.
                assert!(nodes[1].x < nodes[2].x || height >= width);
                if height < width {
                    assert!(nodes[2].x > f64::from(width) * 0.5);
                } else {
                    assert!(nodes[2].y > nodes[1].y + nodes[1].height);
                }
            } else {
                assert!(rule.width > rule.height);
                assert_eq!(
                    nodes[5].text.as_deref(),
                    Some("ILLUSTRATIVE QUOTE / ATTRIBUTION NOT VERIFIED")
                );
                assert!(nodes[1].x > nodes[0].x);
            }
            sources.push(nodes);
        }
        assert_ne!(sources[0][1].width, sources[1][1].width);
    }
}
#[test]
fn source_copy_changes_preserve_human_style_and_stable_object_camera() {
    let mut p = project();
    let id = instance();
    let initial = config(HeroLayout::ContrastComparison);
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: initial.clone(),
    })
    .unwrap();
    let camera = p.scenes[0].camera.clone();
    let eyebrow = hero_node_id(id, "eyebrow");
    let body = hero_node_id(id, "body");
    let style = p.scenes[0]
        .nodes
        .iter()
        .find(|n| n.id == body)
        .unwrap()
        .style
        .clone();
    let new_style = NodeStyle {
        fill: Some("#FFE0A0".into()),
        ..style
    };
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id: body,
        style: new_style.clone(),
    })
    .unwrap();
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: p.scenes[0].id,
        node_id: eyebrow,
        text: Some("HUMAN EDITED ORIGINAL".into()),
    })
    .unwrap();
    let before = p.clone();
    let next = HeroConfig {
        layout: HeroLayout::EditorialQuote,
        headline: "An authored illustrative quotation".into(),
        body: "An original context statement".into(),
        ..initial
    };
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: next,
    })
    .unwrap();
    assert_eq!(p.scenes[0].camera, camera);
    assert_eq!(p.scenes[0].nodes.len(), 6);
    assert_eq!(p.scenes[0].nodes[0].id, before.scenes[0].nodes[0].id);
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == eyebrow)
            .unwrap()
            .text
            .as_deref(),
        Some("HUMAN EDITED ORIGINAL")
    );
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == body)
            .unwrap()
            .style
            .fill,
        new_style.fill
    );
    assert_eq!(
        p.production_design.heroes[0].config.layout,
        HeroLayout::EditorialQuote
    );
    p.validate().unwrap();
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(restored, p);
}
#[test]
fn ambiguous_human_copy_and_oversized_source_are_rejected_without_original_mutation() {
    let mut p = project();
    let id = instance();
    let cfg = config(HeroLayout::ContrastComparison);
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: cfg.clone(),
    })
    .unwrap();
    let headline = hero_node_id(id, "headline");
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: p.scenes[0].id,
        node_id: headline,
        text: Some("An independent human-authored state".into()),
    })
    .unwrap();
    let before = p.clone();
    let error = p
        .apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id: p.scenes[0].id,
            config: HeroConfig {
                headline: "An incompatible generated state".into(),
                ..cfg.clone()
            },
        })
        .unwrap_err();
    assert!(error.to_string().contains("override conflict"));
    assert_eq!(p, before);
    let illegal = HeroConfig {
        headline: "UNBREAKABLEORIGINALWORD".repeat(3),
        ..cfg
    };
    assert!(realize_product_hero(id, &illegal, 1080, 1920).is_err());
}
#[test]
fn static_craft_layouts_are_explicitly_not_animated() {
    let cfg = HeroConfig {
        motion: false,
        ..config(HeroLayout::EditorialQuote)
    };
    let nodes = realize_product_hero(instance(), &cfg, 1920, 1080).unwrap();
    assert!(nodes.iter().all(|n| n.keyframes.is_empty()));
}

#[test]
fn attached_craft_source_cannot_remove_or_falsely_verify_its_disclosure() {
    for family in [HeroLayout::ContrastComparison, HeroLayout::EditorialQuote] {
        let mut p = project();
        let id = instance();
        p.apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id: p.scenes[0].id,
            config: config(family),
        })
        .unwrap();
        let safety = hero_node_id(id, "disclosure");
        let mut altered = p.clone();
        assert!(
            altered
                .apply_change(&Change::UpdateCanvasText {
                    scene_id: p.scenes[0].id,
                    node_id: safety,
                    text: Some("INDEPENDENTLY VERIFIED ENDORSEMENT".into()),
                })
                .unwrap_err()
                .to_string()
                .contains("disclosure")
        );
        // A visually hidden or removed disclaimer is equally misleading,
        // even when its string has not been changed.
        let live = p.scenes[0].nodes.iter().find(|n| n.id == safety).unwrap();
        let mut hidden = p.clone();
        assert!(
            hidden
                .apply_change(&Change::TransformCanvasNode {
                    scene_id: p.scenes[0].id,
                    node_id: safety,
                    transform: CanvasTransform {
                        x: live.x,
                        y: live.y,
                        width: live.width,
                        height: live.height,
                        rotation_deg: live.rotation_deg,
                        opacity: 0.0,
                    },
                })
                .unwrap_err()
                .to_string()
                .contains("disclosure")
        );
        let mut removed = p.clone();
        assert!(
            removed
                .apply_change(&Change::RemoveCanvasNode {
                    scene_id: p.scenes[0].id,
                    node_id: safety,
                })
                .unwrap_err()
                .to_string()
                .contains("disclosure")
        );
        assert_eq!(
            p.scenes[0]
                .nodes
                .iter()
                .find(|n| n.id == safety)
                .unwrap()
                .text,
            p.production_design.heroes[0].baseline[5].text
        );
        p.apply_change(&Change::DetachProductHero { instance_id: id })
            .unwrap();
        p.apply_change(&Change::UpdateCanvasText {
            scene_id: p.scenes[0].id,
            node_id: safety,
            text: Some("Detached custom editorial notice".into()),
        })
        .unwrap();
        assert!(p.production_design.heroes.is_empty());
    }
}

#[test]
fn typography_focus_and_contrast_keep_whole_copy_glyph_room_and_original_ids() {
    let original = realize_product_hero(instance(), &HeroConfig::default(), 1920, 1080).unwrap();
    for layout in [HeroLayout::TypographyFocus, HeroLayout::TypographyContrast] {
        for (headline, body) in [
            ("One clear idea", "Space for the supporting thought."),
            ("Una idea clara", "Espacio para lo que importa."),
            ("Une idée claire", "Un espace pour le sens du message."),
        ] {
            let cfg = HeroConfig {
                headline: headline.into(),
                body: body.into(),
                ..config(layout)
            };
            for (width, height) in [(1920, 1080), (1080, 1080), (1080, 1920)] {
                let nodes = realize_product_hero(instance(), &cfg, width, height).unwrap();
                assert_eq!(
                    nodes.iter().map(|node| node.id).collect::<Vec<_>>(),
                    original.iter().map(|node| node.id).collect::<Vec<_>>()
                );
                assert_eq!(nodes.iter().filter(|node| node.kind == "text").count(), 4);
                assert_eq!(
                    nodes.iter().filter(|node| node.kind == "rectangle").count(),
                    2
                );
                let words = |value: &str| value.split_whitespace().collect::<Vec<_>>().join(" ");
                assert_eq!(words(nodes[1].text.as_deref().unwrap()), words(headline));
                assert_eq!(words(nodes[2].text.as_deref().unwrap()), words(body));
                for node in &nodes {
                    if let (Some(text), Some(size)) = (&node.text, node.style.font_size) {
                        assert!(
                            size * ((text.lines().count() - 1) as f64 * 1.12 + 1.4)
                                <= node.height * 0.96
                        );
                    }
                    assert!(node.x >= f64::from(width) * 0.05);
                    assert!(
                        node.y + node.height + 18.0 * f64::from(width.min(height)) / 1080.0
                            <= f64::from(height) * 0.96
                    );
                }
                let end = |node: &CanvasNode| {
                    node.keyframes
                        .iter()
                        .rfind(|key| key.property == MotionProperty::Opacity)
                        .unwrap()
                        .at
                };
                assert_eq!(end(&nodes[1]), RationalTime::new(9, 10).unwrap());
                assert_eq!(
                    end(&nodes[2]),
                    RationalTime::new(
                        if layout == HeroLayout::TypographyFocus {
                            9
                        } else {
                            13
                        },
                        10
                    )
                    .unwrap()
                );
                if layout == HeroLayout::TypographyFocus {
                    for node in &nodes {
                        assert_eq!(end(node), end(&nodes[1]));
                    }
                }
            }
            let static_nodes = realize_product_hero(
                instance(),
                &HeroConfig {
                    motion: false,
                    ..cfg
                },
                1920,
                1080,
            )
            .unwrap();
            assert!(static_nodes.iter().all(|node| node.keyframes.is_empty()));
        }
    }
}

#[test]
fn typography_variations_preserve_human_appearance_and_report_copy_conflicts() {
    let mut p = project();
    let scene_id = p.scenes[0].id;
    p.apply_change(&Change::UpsertProductHero {
        instance_id: instance(),
        scene_id,
        config: config(HeroLayout::TypographyFocus),
    })
    .unwrap();
    let node = p.scenes[0].nodes[1].clone();
    let mut style = node.style.clone();
    style.fill = Some("#FFAA22".into());
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id,
        node_id: node.id,
        style,
    })
    .unwrap();
    p.apply_change(&Change::UpsertProductHero {
        instance_id: instance(),
        scene_id,
        config: config(HeroLayout::TypographyContrast),
    })
    .unwrap();
    assert_eq!(p.scenes[0].nodes[1].style.fill.as_deref(), Some("#FFAA22"));
    let reopened: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    assert_eq!(p, reopened);
    p.apply_change(&Change::UpdateCanvasText {
        scene_id,
        node_id: node.id,
        text: Some("The human direction".into()),
    })
    .unwrap();
    let saved = p.clone();
    assert!(
        p.apply_change(&Change::UpsertProductHero {
            instance_id: instance(),
            scene_id,
            config: HeroConfig {
                headline: "A conflicting idea".into(),
                ..config(HeroLayout::TypographyContrast)
            }
        })
        .is_err()
    );
    assert_eq!(p, saved);
    for layout in [HeroLayout::TypographyFocus, HeroLayout::TypographyContrast] {
        assert!(
            realize_product_hero(
                instance(),
                &HeroConfig {
                    headline: "X".repeat(60),
                    ..config(layout)
                },
                1080,
                1920
            )
            .is_err()
        );
        assert!(
            realize_product_hero(
                instance(),
                &HeroConfig {
                    headline: "A\n\nB".into(),
                    ..config(layout)
                },
                1920,
                1080
            )
            .is_err()
        );
    }
}
