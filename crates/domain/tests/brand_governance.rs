use motionwright_domain::*;
use uuid::Uuid;

fn seeded() -> Project {
    let mut project = Project::new("Brand governance pilot").unwrap();
    for name in ["Hero A", "Hero B"] {
        project
            .apply_change(&Change::AddScene {
                name: name.into(),
                objective: "Editable scene".into(),
                duration_seconds: 6,
            })
            .unwrap();
    }
    for scene in project.scenes.clone() {
        project
            .apply_change(&Change::UpsertProductHero {
                instance_id: Uuid::new_v4(),
                scene_id: scene.id,
                config: HeroConfig::default(),
            })
            .unwrap();
    }
    project
}
fn ban(phrase: &str) -> BrandProfile {
    BrandProfile {
        id: Uuid::new_v4(),
        label: "Motionwright brand".into(),
        version: 1,
        rules: vec![BrandRule::ForbiddenPhrase {
            id: Uuid::new_v4(),
            phrase: phrase.into(),
        }],
    }
}
fn govern(
    project: &mut Project,
    profile: Option<BrandProfile>,
    exceptions: Vec<BrandException>,
) -> Result<()> {
    project.apply_change(&Change::SetBrandGovernance {
        profile,
        exceptions,
    })
}
#[test]
fn preference_does_not_override_brand_and_canvas_changes_are_checked() {
    let mut p = seeded();
    let brand = ban("PROMISED");
    govern(&mut p, Some(brand), vec![]).unwrap();
    p.apply_change(&Change::SetTasteProfile {
        profile: Some(TasteProfile {
            id: Uuid::new_v4(),
            label: "Inferred aesthetic".into(),
            preferences: vec![TastePreference {
                axis: "copy".into(),
                preference: "Say PROMISED for energy".into(),
                inferred: true,
            }],
        }),
    })
    .unwrap();
    let scene = p.scenes[0].id;
    let hero = &p.production_design.heroes[0];
    let node = hero_node_id(hero.id, "headline");
    let mut attempt = p.clone();
    let rejected = attempt.apply_change(&Change::UpdateCanvasText {
        scene_id: scene,
        node_id: node,
        text: Some("PROMISED outcomes".into()),
    });
    assert!(rejected.unwrap_err().to_string().contains("brand rule"));
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == node)
            .unwrap()
            .text
            .as_deref(),
        Some("Make the work.\nKeep the craft.")
    );
}
#[test]
fn exception_is_exact_scene_rule_and_profile_digest() {
    let mut p = seeded();
    let brand = ban("PROHIBITED");
    let rule_id = brand.rules[0].id();
    let allowed_scene = p.scenes[0].id;
    let forbidden_scene = p.scenes[1].id;
    let exception = BrandException {
        id: Uuid::new_v4(),
        rule_id,
        scene_id: allowed_scene,
        brand_sha256: brand.content_digest().unwrap(),
        campaign: "Demo launch".into(),
        author: "Owner (recorded)".into(),
        rationale: "Campaign-approved quoted case study".into(),
    };
    govern(&mut p, Some(brand.clone()), vec![exception.clone()]).unwrap();
    let id = p.production_design.heroes[0].id;
    p.apply_change(&Change::UpdateCanvasText {
        scene_id: allowed_scene,
        node_id: hero_node_id(id, "body"),
        text: Some("This quote says PROHIBITED".into()),
    })
    .unwrap();
    let id = p.production_design.heroes[1].id;
    assert!(
        p.clone()
            .apply_change(&Change::UpdateCanvasText {
                scene_id: forbidden_scene,
                node_id: hero_node_id(id, "body"),
                text: Some("PROHIBITED for another scene".into()),
            })
            .is_err()
    );
    let mut updated = brand;
    updated.version += 1;
    assert!(
        p.clone()
            .apply_change(&Change::SetBrandGovernance {
                profile: Some(updated.clone()),
                exceptions: vec![exception.clone()],
            })
            .is_err(),
        "policy update invalidates old exception digest"
    );
    let new_exception = BrandException {
        brand_sha256: updated.content_digest().unwrap(),
        ..exception
    };
    govern(&mut p, Some(updated), vec![new_exception]).unwrap();
    assert!(p.validate().is_ok());
}

#[test]
fn authored_palette_and_wordmark_are_constrained_even_after_human_override() {
    let mut p = seeded();
    let hero = p.production_design.heroes[0].clone();
    let brand = BrandProfile {
        id: Uuid::new_v4(),
        label: "Our visual rules".into(),
        version: 1,
        rules: vec![
            BrandRule::AllowedAccents {
                id: Uuid::new_v4(),
                colors: vec!["#A5C8DF".into()],
            },
            BrandRule::RequiredWordmark {
                id: Uuid::new_v4(),
                text: "Mw".into(),
            },
        ],
    };
    govern(&mut p, Some(brand), vec![]).unwrap();
    let scene_id = hero.scene_id;
    let mut candidate = p.clone();
    let node_id = hero_node_id(hero.id, "wordmark");
    assert!(
        candidate
            .apply_change(&Change::UpdateCanvasText {
                scene_id,
                node_id,
                text: Some("Not the brand".into()),
            })
            .is_err()
    );
    let mut candidate = p.clone();
    let mut style = candidate.scenes[0]
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .unwrap()
        .style
        .clone();
    style.fill = Some("#EE0000".into());
    assert!(
        candidate
            .apply_change(&Change::UpdateCanvasStyle {
                scene_id,
                node_id,
                style,
            })
            .is_err()
    );
}
