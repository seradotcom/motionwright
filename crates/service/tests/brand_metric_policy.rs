//! A mandatory BrandProfile is enforced by the durable Studio CAS transaction,
//! not by treating a mutating domain staging object as an immutable snapshot.
use motionwright_domain::{
    BrandException, BrandProfile, BrandRule, Change, HeroConfig, HeroLayout, Project, hero_node_id,
};
use motionwright_service::StudioService;
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}

#[test]
fn rejected_human_metric_accent_cannot_change_durable_project_or_revision() {
    let folder = tempfile::tempdir().unwrap();
    let database = folder.path().join("metric-brand.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let mut project = service.create_project("Exact brand authority").unwrap();
    project = apply(
        &service,
        &project,
        "metric-scene",
        Change::AddScene {
            name: "Source-bound metric".into(),
            objective: "Original editorial graphic study".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = project.scenes[0].id;
    let instance_id = Uuid::new_v4();
    project = apply(
        &service,
        &project,
        "metric-component",
        Change::UpsertProductHero {
            instance_id,
            scene_id,
            config: HeroConfig {
                layout: HeroLayout::MetricEvidence,
                headline: "42% growth".into(),
                body: "An editorial figure requires separately checked source evidence.".into(),
                wordmark: "DATA".into(),
                accent: "#D9A46E".into(),
                ..HeroConfig::default()
            },
        },
    );
    let rule_id = Uuid::new_v4();
    let policy = BrandProfile {
        id: Uuid::new_v4(),
        label: "Source-bound palette".into(),
        version: 1,
        rules: vec![
            BrandRule::AllowedAccents {
                id: rule_id,
                colors: vec!["#D9A46E".into()],
            },
            BrandRule::RequiredWordmark {
                id: Uuid::new_v4(),
                text: "DATA".into(),
            },
        ],
    };
    project = apply(
        &service,
        &project,
        "brand-policy",
        Change::SetBrandGovernance {
            profile: Some(policy.clone()),
            exceptions: vec![],
        },
    );
    let headline_id = hero_node_id(instance_id, "headline");
    let mut denied_style = project.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == headline_id)
        .unwrap()
        .style
        .clone();
    denied_style.fill = Some("#EE0000".into());
    let invalid_change = Change::UpdateCanvasStyle {
        scene_id,
        node_id: headline_id,
        style: denied_style.clone(),
    };
    let source = project.clone();
    assert!(
        service
            .apply(
                project.id,
                &project.stamp(),
                "denied-style",
                &invalid_change
            )
            .is_err(),
        "the policy must reject a direct human style edit"
    );
    assert_eq!(service.project(project.id).unwrap(), source);
    drop(service);

    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), source);
    let waiver = BrandException {
        id: Uuid::new_v4(),
        rule_id,
        scene_id,
        brand_sha256: policy.content_digest().unwrap(),
        campaign: "One editorial slide".into(),
        author: "Recorded operator (not authenticated)".into(),
        rationale: "Local palette exception, retaining the rest of the brand".into(),
    };
    let authorized = apply(
        &reopened,
        &source,
        "brand-waiver",
        Change::SetBrandGovernance {
            profile: Some(policy),
            exceptions: vec![waiver],
        },
    );
    let committed = apply(
        &reopened,
        &authorized,
        "authorized-style",
        Change::UpdateCanvasStyle {
            scene_id,
            node_id: headline_id,
            style: denied_style,
        },
    );
    assert_eq!(committed.revision, source.revision + 2);
    assert_eq!(
        reopened.project(committed.id).unwrap().scenes[0]
            .nodes
            .iter()
            .find(|node| node.id == headline_id)
            .unwrap()
            .style
            .fill
            .as_deref(),
        Some("#EE0000"),
    );
    committed.validate().unwrap();
}
