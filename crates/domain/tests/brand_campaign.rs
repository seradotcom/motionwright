use motionwright_domain::{
    BRAND_ROLLOUT_SCHEMA, BrandCampaignRollout, BrandProfile, BrandRule, Change, HeroConfig,
    LockKind, Project,
};
use uuid::Uuid;

fn source() -> Project {
    let mut project = Project::new("Original multi-scene brand campaign").unwrap();
    for (i, label) in ["First original scene", "Second original scene"]
        .into_iter()
        .enumerate()
    {
        project
            .apply_change(&Change::AddScene {
                name: label.into(),
                objective: "Owner authored campaign, not automatic publishing".into(),
                duration_seconds: 6,
            })
            .unwrap();
        let scene = project.scenes[i].id;
        project
            .apply_change(&Change::UpsertProductHero {
                instance_id: Uuid::new_v4(),
                scene_id: scene,
                config: HeroConfig::default(),
            })
            .unwrap();
    }
    project
        .apply_change(&Change::SetBrandGovernance {
            profile: Some(BrandProfile {
                id: Uuid::new_v4(),
                label: "Original brand policy".into(),
                version: 1,
                rules: vec![
                    BrandRule::AllowedAccents {
                        id: Uuid::new_v4(),
                        colors: vec!["#A5C8DF".into(), "#F5B84A".into()],
                    },
                    BrandRule::RequiredWordmark {
                        id: Uuid::new_v4(),
                        text: "Mw".into(),
                    },
                ],
            }),
            exceptions: vec![],
        })
        .unwrap();
    project
}
fn rollout(p: &Project) -> BrandCampaignRollout {
    BrandCampaignRollout{
        schema:BRAND_ROLLOUT_SCHEMA.into(),project_id:p.id,generation:p.generation,
        expected_revision:p.revision,
        brand_sha256:p.production_design.brand_profile.as_ref().unwrap().content_digest().unwrap(),
        campaign:"Launch original cut".into(),
        scene_ids:p.scenes.iter().map(|scene|scene.id).collect(),
        accent:Some("#F5B84A".into()),wordmark:Some("Mw".into()),
        reviewer_declaration:"Source owner (self-declared)".into(),
        rationale:"Match the exact saved brand accent across two current editable shots, preserving manual edits".into(),
        accepts_semantic_component_rebuild:true,
    }
}
#[test]
fn one_owner_review_restyles_two_original_semantic_components_and_retains_source_decisions() {
    let mut p = source();
    let before = p.clone();
    let proposal = rollout(&p);
    let report = p.preview_brand_campaign(&proposal).unwrap();
    assert_eq!(report.changed_count, 2);
    assert_eq!(report.rows.len(), 2);
    assert!(report.owner_review_required && !report.committed && !report.output_rendered);
    assert_eq!(p, before, "preflight never applies a change");
    p.apply_change(&Change::ApplyBrandCampaign {
        rollout: Box::new(proposal),
    })
    .unwrap();
    assert_eq!(p.production_design.heroes.len(), 2);
    assert_eq!(p.production_design.project_decisions.len(), 2);
    for hero in &p.production_design.heroes {
        assert_eq!(hero.config.accent, "#F5B84A");
        assert_eq!(hero.config.wordmark, "Mw");
        let originals = before
            .production_design
            .heroes
            .iter()
            .find(|entry| entry.id == hero.id)
            .unwrap();
        assert_eq!(hero.id, originals.id, "component identity is stable");
        assert_eq!(hero.scene_id, originals.scene_id);
        assert_eq!(hero.config.headline, originals.config.headline);
    }
    for decision in &p.production_design.project_decisions {
        assert!(decision.decision.contains("Launch original cut"));
        assert!(decision.decision.contains("policy SHA-256"));
        assert_eq!(decision.author, "Source owner (self-declared)");
    }
    assert_eq!(p.assets, before.assets);
}
#[test]
fn invalid_brand_identity_capability_or_user_consent_fails_closed() {
    let source = source();
    let good = rollout(&source);
    let mut cases = Vec::new();
    let mut bad = good.clone();
    bad.project_id = Uuid::new_v4();
    cases.push(bad);
    let mut bad = good.clone();
    bad.generation = Uuid::new_v4();
    cases.push(bad);
    let mut bad = good.clone();
    bad.expected_revision += 1;
    cases.push(bad);
    let mut bad = good.clone();
    bad.brand_sha256 = "f".repeat(64);
    cases.push(bad);
    let mut bad = good.clone();
    bad.scene_ids.push(bad.scene_ids[0]);
    cases.push(bad);
    let mut bad = good.clone();
    bad.scene_ids = vec![Uuid::new_v4()];
    cases.push(bad);
    let mut bad = good.clone();
    bad.accent = Some("#000000".into());
    cases.push(bad);
    let mut bad = good.clone();
    bad.wordmark = Some("Unauthorized".into());
    cases.push(bad);
    let mut bad = good.clone();
    bad.accepts_semantic_component_rebuild = false;
    cases.push(bad);
    let mut bad = good.clone();
    bad.reviewer_declaration.clear();
    cases.push(bad);
    let mut bad = good.clone();
    bad.accent = None;
    bad.wordmark = None;
    cases.push(bad);
    for (i, proposal) in cases.into_iter().enumerate() {
        let mut trial = source.clone();
        assert!(
            trial
                .apply_change(&Change::ApplyBrandCampaign {
                    rollout: Box::new(proposal)
                })
                .is_err(),
            "case {i} should refuse"
        );
        assert_eq!(
            trial, source,
            "rejection must not advance a project revision"
        );
    }
}
#[test]
fn locked_last_scene_causes_atomic_rollback_instead_of_one_applied_brand_update() {
    let mut source = source();
    let locked = source.scenes[1].id;
    source
        .apply_change(&Change::SetLock {
            resource: format!("scene:{locked}"),
            kind: LockKind::Style,
            note: "Human style override protection".into(),
        })
        .unwrap();
    let snapshot = source.clone();
    let proposal = rollout(&source);
    assert!(
        source.preview_brand_campaign(&proposal).is_ok(),
        "the source-bound preview does not claim to pre-approve late locks"
    );
    assert!(
        source
            .apply_change(&Change::ApplyBrandCampaign {
                rollout: Box::new(proposal)
            })
            .is_err()
    );
    assert_eq!(
        source, snapshot,
        "first scene must not be updated on the second-scene conflict"
    );
}
#[test]
fn protected_scene_and_project_lock_refuse_any_brand_propagation() {
    let mut source = source();
    source.scenes[0].status = motionwright_domain::SceneStatus::Approved;
    let snapshot = source.clone();
    assert!(
        source
            .apply_change(&Change::ApplyBrandCampaign {
                rollout: Box::new(rollout(&source))
            })
            .is_err()
    );
    assert_eq!(source, snapshot);
    source.scenes[0].status = motionwright_domain::SceneStatus::Draft;
    source
        .apply_change(&Change::SetLock {
            resource: source.resource_key(),
            kind: LockKind::Content,
            note: "Project-wide owner lock".into(),
        })
        .unwrap();
    let snapshot = source.clone();
    assert!(
        source
            .apply_change(&Change::ApplyBrandCampaign {
                rollout: Box::new(rollout(&source))
            })
            .is_err()
    );
    assert_eq!(source, snapshot);
}
#[test]
fn strict_json_schema_rejects_untrusted_executables_and_unknown_provider_fields() {
    let p = source();
    let mut content = serde_json::to_value(rollout(&p)).unwrap();
    content["execute"] = "curl malicious provider".into();
    assert!(serde_json::from_value::<BrandCampaignRollout>(content).is_err());
    let mut content = serde_json::to_value(rollout(&p)).unwrap();
    content["scene_ids"][0] = "outside-source-scene".into();
    assert!(serde_json::from_value::<BrandCampaignRollout>(content).is_err());
}
