use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;

fn apply(service: &StudioService, p: &Project, request: &str, change: Change) -> Project {
    service
        .apply(p.id, &p.stamp(), request, &change)
        .unwrap()
        .project
}
#[test]
fn ten_revisions_preserve_human_work_across_database_reopen_and_reject_stale_agents() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("creative.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = service.create_project("Revision ten").unwrap();
    p = apply(
        &service,
        &p,
        "scene",
        Change::AddScene {
            name: "Hero".into(),
            objective: "Persistent editable composition".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let id = Uuid::new_v4();
    p = apply(
        &service,
        &p,
        "component",
        Change::UpsertProductHero {
            instance_id: id,
            scene_id,
            config: HeroConfig::default(),
        },
    );
    let node_id = hero_node_id(id, "body");
    p = apply(
        &service,
        &p,
        "human",
        Change::UpdateCanvasText {
            scene_id,
            node_id,
            text: Some("A deliberate human editorial decision.".into()),
        },
    );
    let stale = p.stamp();
    for revision in 1..=10 {
        let config = HeroConfig {
            eyebrow: format!("CREATIVE PRODUCTION / REVISION {revision:02}"),
            ..HeroConfig::default()
        };
        p = apply(
            &service,
            &p,
            &format!("iteration-{revision}"),
            Change::UpsertProductHero {
                instance_id: id,
                scene_id,
                config,
            },
        );
        assert_eq!(
            p.scenes[0]
                .nodes
                .iter()
                .find(|node| node.id == node_id)
                .unwrap()
                .text
                .as_deref(),
            Some("A deliberate human editorial decision.")
        );
        assert_eq!(p.production_design.heroes[0].id, id);
        assert_eq!(p.scenes[0].nodes.len(), 6);
    }
    let stale_patch = Change::ApplyCreativePatch {
        patch: CreativePatch {
            scene_id,
            base_revision: stale.revision,
            rationale: "A stale agent must not overwrite newer human work".into(),
            edits: vec![ScopedCanvasEdit::Text {
                node_id,
                text: "Stale replacement".into(),
            }],
        },
    };
    assert!(
        service
            .apply(p.id, &stale, "stale-agent", &stale_patch)
            .is_err()
    );
    drop(service);
    let reopened = StudioService::open(&path).unwrap().project(p.id).unwrap();
    assert_eq!(reopened, p);
}
#[test]
fn new_design_state_participates_in_branch_checkout_and_restore() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("branch.sqlite3")).unwrap();
    let mut p = service.create_project("Component branches").unwrap();
    p = apply(
        &service,
        &p,
        "scene",
        Change::AddScene {
            name: "Hero".into(),
            objective: "Branch-preserving component".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let id = Uuid::new_v4();
    let main = p.active_branch;
    p = apply(
        &service,
        &p,
        "hero",
        Change::UpsertProductHero {
            instance_id: id,
            scene_id,
            config: HeroConfig::default(),
        },
    );
    p = apply(
        &service,
        &p,
        "branch",
        Change::CreateBranch {
            name: "Alternate brand".into(),
        },
    );
    let branch = p
        .branches
        .iter()
        .find(|branch| branch.name == "Alternate brand")
        .unwrap()
        .id;
    p = apply(
        &service,
        &p,
        "checkout-alternate",
        Change::CheckoutBranch { branch_id: branch },
    );
    p = apply(
        &service,
        &p,
        "brand",
        Change::UpsertProductHero {
            instance_id: id,
            scene_id,
            config: HeroConfig {
                accent: "#EBC592".into(),
                ..HeroConfig::default()
            },
        },
    );
    p = apply(
        &service,
        &p,
        "checkout-main",
        Change::CheckoutBranch { branch_id: main },
    );
    assert_eq!(
        p.production_design.heroes[0].config.accent,
        HeroConfig::default().accent
    );
    p = apply(
        &service,
        &p,
        "checkout-alternate-again",
        Change::CheckoutBranch { branch_id: branch },
    );
    assert_eq!(p.production_design.heroes[0].config.accent, "#EBC592");
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|node| node.id == hero_node_id(id, "wordmark"))
            .unwrap()
            .style
            .fill
            .as_deref(),
        Some("#EBC592")
    );
}
