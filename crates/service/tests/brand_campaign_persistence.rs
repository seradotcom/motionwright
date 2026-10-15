use motionwright_domain::{
    BRAND_ROLLOUT_SCHEMA, BrandCampaignRollout, BrandProfile, BrandRule, Change, HeroConfig,
    Project, RevisionStamp,
};
use motionwright_service::StudioService;
use uuid::Uuid;
fn commit(service: &StudioService, p: &Project, key: &str, change: Change) -> Project {
    service
        .apply(p.id, &RevisionStamp::from(p), key, &change)
        .unwrap()
        .project
}
fn source(service: &StudioService) -> Project {
    let mut p = service
        .create_project("Original two-scene campaign")
        .unwrap();
    for (i, name) in ["Original opening", "Original proof"]
        .into_iter()
        .enumerate()
    {
        p = commit(
            service,
            &p,
            &format!("brand:scene:{i}"),
            Change::AddScene {
                name: name.into(),
                objective: "Source-authored brand story".into(),
                duration_seconds: 6,
            },
        );
        p = commit(
            service,
            &p,
            &format!("brand:hero:{i}"),
            Change::UpsertProductHero {
                instance_id: Uuid::new_v4(),
                scene_id: p.scenes[i].id,
                config: HeroConfig::default(),
            },
        );
    }
    commit(
        service,
        &p,
        "brand:policy",
        Change::SetBrandGovernance {
            profile: Some(BrandProfile {
                id: Uuid::new_v4(),
                label: "Exact, original brand palette".into(),
                version: 1,
                rules: vec![BrandRule::AllowedAccents {
                    id: Uuid::new_v4(),
                    colors: vec!["#A5C8DF".into(), "#F5B84A".into()],
                }],
            }),
            exceptions: vec![],
        },
    )
}
fn rollout(project: &Project) -> BrandCampaignRollout {
    BrandCampaignRollout{
        schema:BRAND_ROLLOUT_SCHEMA.into(),project_id:project.id,generation:project.generation,
        expected_revision:project.revision,
        brand_sha256:project.production_design.brand_profile.as_ref().unwrap().content_digest().unwrap(),
        campaign:"Original local campaign".into(),scene_ids:project.scenes.iter().map(|s|s.id).collect(),
        accent:Some("#F5B84A".into()),wordmark:None,
        reviewer_declaration:"Owner attestation, not authenticated".into(),
        rationale:"Review both editable scenes and apply one allowlisted accent in a single project revision".into(),
        accepts_semantic_component_rebuild:true,
    }
}
#[test]
fn multi_scene_campaign_changes_once_and_persists_after_reopen() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("brand.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let p = source(&service);
    let edit = Change::ApplyBrandCampaign {
        rollout: Box::new(rollout(&p)),
    };
    let result = service
        .apply(p.id, &RevisionStamp::from(&p), "brand:apply:once", &edit)
        .unwrap();
    assert_eq!(result.project.revision, p.revision + 1);
    assert_eq!(result.project.production_design.project_decisions.len(), 2);
    for hero in &result.project.production_design.heroes {
        assert_eq!(hero.config.accent, "#F5B84A");
    }
    let replay = service
        .apply(p.id, &RevisionStamp::from(&p), "brand:apply:once", &edit)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, result.project);
    assert!(
        service
            .apply(
                p.id,
                &RevisionStamp::from(&p),
                "brand:duplicate-second",
                &edit
            )
            .is_err()
    );
    drop(service);
    assert_eq!(
        StudioService::open(&db).unwrap().project(p.id).unwrap(),
        result.project
    );
}
#[test]
fn competing_owner_sessions_cannot_both_commit_same_source_revision() {
    use std::sync::{Arc, Barrier};
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("brand-owners.sqlite3");
    let original = StudioService::open(&db).unwrap();
    let p = source(&original);
    let gate = Arc::new(Barrier::new(2));
    let mut handles = Vec::new();
    for i in 0..2 {
        let client = StudioService::open(&db).unwrap();
        let project = p.clone();
        let proposal = rollout(&p);
        let gate = Arc::clone(&gate);
        handles.push(std::thread::spawn(move || {
            gate.wait();
            client
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("brand:race:{i}"),
                    &Change::ApplyBrandCampaign {
                        rollout: Box::new(proposal),
                    },
                )
                .is_ok()
        }));
    }
    let applied = handles
        .into_iter()
        .map(|h| h.join().unwrap())
        .filter(|ok| *ok)
        .count();
    assert_eq!(
        applied, 1,
        "only one independent owner client may commit the shared revision"
    );
    let actual = original.project(p.id).unwrap();
    assert_eq!(actual.revision, p.revision + 1);
    assert_eq!(actual.production_design.project_decisions.len(), 2);
}
#[test]
fn portable_import_must_not_admit_stale_brand_policy_generation() {
    let tmp = tempfile::tempdir().unwrap();
    let first = StudioService::open(tmp.path().join("first.sqlite3")).unwrap();
    let p = source(&first);
    let stale = rollout(&p);
    let backup = first.export_project(p.id).unwrap();
    let second = StudioService::open(tmp.path().join("second.sqlite3")).unwrap();
    let imported = second.import_project(&backup).unwrap();
    assert_ne!(imported.generation, p.generation);
    assert!(
        second
            .apply(
                imported.id,
                &RevisionStamp::from(&imported),
                "brand:stale-after-import",
                &Change::ApplyBrandCampaign {
                    rollout: Box::new(stale)
                }
            )
            .is_err()
    );
    assert_eq!(second.project(imported.id).unwrap(), imported);
}
