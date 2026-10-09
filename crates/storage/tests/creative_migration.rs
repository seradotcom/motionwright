use motionwright_domain::{Change, HeroConfig, Project};
use motionwright_storage::Store;
use uuid::Uuid;

#[test]
fn legacy_database_is_read_without_revision_changes_and_upgraded_only_on_successful_write() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("legacy.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let created = store
        .create_named_project("Legacy editorial project")
        .unwrap();
    drop(store);
    // Materialize the prior serialized format as a fixture. No production
    // database, external account, or current user project is accessed.
    let mut old = serde_json::to_value(&created).unwrap();
    old["schema_version"] = serde_json::json!(1);
    old.as_object_mut().unwrap().remove("production_design");
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute(
        "UPDATE projects SET document_json=?1 WHERE id=?2",
        rusqlite::params![old.to_string(), created.id.to_string()],
    )
    .unwrap();
    drop(db);
    let mut store = Store::open(&path).unwrap();
    let observed = store.load_project(created.id).unwrap();
    assert_eq!(observed.schema_version, 1);
    assert_eq!(observed.revision, created.revision);
    assert_eq!(observed.generation, created.generation);
    let rejected = store.apply(
        created.id,
        &observed.stamp(),
        "failed-upgrade",
        &Change::UndoCreativePatch {
            patch_id: Uuid::now_v7(),
        },
    );
    assert!(rejected.is_err());
    assert_eq!(store.load_project(created.id).unwrap(), observed);
    let updated = store
        .apply(
            created.id,
            &observed.stamp(),
            "successful-upgrade",
            &Change::AddScene {
                name: "Editable hero".into(),
                objective: "New creative state".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    assert_eq!(
        updated.schema_version,
        motionwright_domain::PROJECT_SCHEMA_VERSION
    );
    assert_eq!(updated.revision, observed.revision + 1);
    assert_eq!(updated.generation, observed.generation);
    let with_hero = store
        .apply(
            updated.id,
            &updated.stamp(),
            "hero",
            &Change::UpsertProductHero {
                instance_id: Uuid::now_v7(),
                scene_id: updated.scenes[0].id,
                config: HeroConfig::default(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(with_hero.production_design.heroes.len(), 1);
    assert_eq!(store.load_project(with_hero.id).unwrap(), with_hero);
}

#[test]
fn newer_creative_state_cannot_be_mislabeled_as_the_legacy_format() {
    let mut project = Project::new("Protected new format").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Hero".into(),
            objective: "New content".into(),
            duration_seconds: 6,
        })
        .unwrap();
    project
        .apply_change(&Change::UpsertProductHero {
            instance_id: Uuid::now_v7(),
            scene_id: project.scenes[0].id,
            config: HeroConfig::default(),
        })
        .unwrap();
    project.schema_version = 1;
    assert!(
        project
            .validate()
            .unwrap_err()
            .to_string()
            .contains("schema 2")
    );
    let previous = project.clone();
    assert!(
        project
            .apply_change(&Change::RenameProject {
                title: "Cannot hide format mismatch".into()
            })
            .is_err()
    );
    assert_eq!(project, previous);
    project.schema_version = motionwright_domain::PROJECT_SCHEMA_VERSION + 1;
    assert!(project.validate().is_err());
}

#[test]
fn creative_bundle_preserves_editable_state_but_not_previous_generation_authority() {
    let temp = tempfile::tempdir().unwrap();
    let mut source = Store::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source.create_named_project("Portable hero").unwrap();
    let p = source
        .apply(
            p.id,
            &p.stamp(),
            "scene",
            &Change::AddScene {
                name: "Hero".into(),
                objective: "Portable editable study".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let p = source
        .apply(
            p.id,
            &p.stamp(),
            "component",
            &Change::UpsertProductHero {
                instance_id: Uuid::now_v7(),
                scene_id: p.scenes[0].id,
                config: HeroConfig::default(),
            },
        )
        .unwrap()
        .project;
    let folder = temp.path().join("hero.motionwright");
    source.export_project_bundle(p.id, &folder).unwrap();
    let mut destination = Store::open(temp.path().join("destination.sqlite3")).unwrap();
    assert!(
        destination
            .inspect_project_bundle(&folder)
            .unwrap()
            .project
            .rotates_generation
    );
    let imported = destination.import_project_bundle(&folder).unwrap();
    assert_eq!(
        imported.schema_version,
        motionwright_domain::PROJECT_SCHEMA_VERSION
    );
    assert_eq!(imported.id, p.id);
    assert_eq!(imported.revision, p.revision);
    assert_eq!(imported.production_design, p.production_design);
    assert_eq!(imported.scenes, p.scenes);
    assert_ne!(imported.generation, p.generation);
    assert!(
        destination
            .apply(
                imported.id,
                &p.stamp(),
                "old-authority",
                &Change::RenameProject {
                    title: "Stale source cannot write".into()
                }
            )
            .is_err()
    );
}
