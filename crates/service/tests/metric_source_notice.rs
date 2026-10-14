//! Source-unverified metric label must survive all rejected Studio CAS edits.
//! On-disk projects are never deleted or silently migrated by this write guard.
use motionwright_domain::{Change, HeroConfig, HeroLayout, Project, hero_node_id};
use motionwright_service::StudioService;
use uuid::Uuid;

fn write(service: &StudioService, p: &Project, change: Change) -> Project {
    service
        .apply(p.id, &p.stamp(), &Uuid::now_v7().to_string(), &change)
        .unwrap()
        .project
}

#[test]
fn metric_disclosure_edits_are_rejected_without_persistent_changes() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("metric-integrity.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service.create_project("Original source caption").unwrap();
    p = write(
        &service,
        &p,
        Change::AddScene {
            name: "Authored statistic".into(),
            objective: "Not yet independently verified".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let instance_id = Uuid::new_v4();
    p = write(
        &service,
        &p,
        Change::UpsertProductHero {
            instance_id,
            scene_id,
            config: HeroConfig {
                layout: HeroLayout::MetricEvidence,
                headline: "42% growth".into(),
                wordmark: "DATA".into(),
                body: "Reported source is pending verification".into(),
                ..HeroConfig::default()
            },
        },
    );
    let node_id = hero_node_id(instance_id, "disclosure");
    assert!(p.scenes[0].nodes.iter().any(|node| node.id == node_id
        && node.text.as_deref()
            == Some("METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED")));
    for edit in [
        Change::UpdateCanvasText {
            scene_id,
            node_id,
            text: Some("Verified and approved".into()),
        },
        Change::RemoveCanvasNode { scene_id, node_id },
    ] {
        assert!(
            service
                .apply(p.id, &p.stamp(), &Uuid::now_v7().to_string(), &edit)
                .is_err()
        );
        assert_eq!(service.project(p.id).unwrap(), p);
    }
    drop(service);

    let reopened = StudioService::open(&db).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    let detached = write(&reopened, &p, Change::DetachProductHero { instance_id });
    assert_eq!(detached.revision, p.revision + 1);
    assert!(detached.production_design.heroes.is_empty());
    assert!(
        detached.scenes[0]
            .nodes
            .iter()
            .any(|node| node.id == node_id)
    );
}
