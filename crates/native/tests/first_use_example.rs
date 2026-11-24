//! Actual creation authority and SQLite with the bundled first-party example.
//! Film realization here produces source plans only: no provider, account,
//! installed application, native render or human acceptance is involved.
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
mod effect_grants;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
mod project_library;

#[cfg(test)]
mod first_use_tests {
    use super::{effect_grants::EffectGrantRegistry, project_library::*};
    use motionwright_domain::{Change, Project, ProjectTemplate};
    use motionwright_native::film::{
        FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
        build_motion_canvas_segments,
    };
    use motionwright_native::hyperframes::compile_hyperframes_source;
    use motionwright_service::{ProjectCreationIntent, StudioService};
    use motionwright_storage::StorageError;
    use semwright_media_time::Rate;
    use serde_json::json;
    use uuid::Uuid;

    fn intent() -> ProjectCreationIntent {
        ProjectCreationIntent {
            request_id: Uuid::new_v4(),
            title: "Editable local motion study".into(),
            template: Some(ProjectTemplate::MotionStudyV1),
        }
    }
    fn create(service: &StudioService, request: &ProjectCreationIntent) -> Project {
        let grants = EffectGrantRegistry::default();
        let session = Uuid::new_v4();
        let grant = issue_creation_grant(&grants, session, "main", request).unwrap();
        create_project(
            service,
            &grants,
            session,
            "main",
            CreateProjectRequest {
                intent: request.clone(),
                effect_grant: grant.token,
            },
        )
        .unwrap()
        .project
    }

    #[test]
    fn bundled_example_reopens_as_complete_editable_native_source() {
        let dir = tempfile::tempdir().unwrap();
        let database = dir.path().join("example.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let original = create(&service, &intent());
        assert_eq!(original.revision, 0);
        assert_eq!(original.scenes.len(), 2);
        assert!(original.assets.is_empty());
        assert!(original.model_invocations.is_empty());
        assert!(original.scenes.iter().all(|scene| scene.nodes.len() == 2
            && scene.nodes.iter().any(|node| node.keyframes.len() == 4)));
        let profile = &original.deliverables[0];
        let recipe = &original.production_design.delivery_recipes[&profile.id];
        assert_eq!(recipe.scene_intents.len(), 2);
        let options = FilmBuildOptions {
            frame_rate:Rate::new(30,1).unwrap(), font_family:MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family:MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents:recipe.scene_intents.iter().map(|intent|
                serde_json::from_value(json!({"scene_id":intent.scene_id,"role":intent.role,"archetype":intent.archetype})).unwrap()
            ).collect(),
        };
        let segments = build_motion_canvas_segments(&original, profile.id, &options).unwrap();
        assert_eq!(
            segments.iter().map(|part| part.frame_count).sum::<u64>(),
            180
        );
        for segment in &segments {
            semwright_motion_authoring::realize(&segment.film).unwrap();
        }
        let hyperframes = compile_hyperframes_source(&original, profile.id).unwrap();
        assert_eq!(hyperframes.inventory().composition.frame_count, 180);
        assert!(
            service
                .production_receipts(original.id, 100)
                .unwrap()
                .is_empty()
        );
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(reopened.project(original.id).unwrap(), original);
        assert_eq!(reopened.projects(100).unwrap().len(), 1);
    }

    #[test]
    fn example_selection_is_bound_to_the_grant_and_original_request() {
        let dir = tempfile::tempdir().unwrap();
        let service = StudioService::open(dir.path().join("example.sqlite3")).unwrap();
        let grants = EffectGrantRegistry::default();
        let session = Uuid::new_v4();
        let example = intent();
        let mut blank = example.clone();
        blank.template = None;
        let blank_grant = issue_creation_grant(&grants, session, "main", &blank).unwrap();
        assert!(
            create_project(
                &service,
                &grants,
                session,
                "main",
                CreateProjectRequest {
                    intent: example.clone(),
                    effect_grant: blank_grant.token,
                }
            )
            .is_err()
        );
        assert!(service.projects(100).unwrap().is_empty());
        let original = create(&service, &example);
        assert!(matches!(
            service.create_project_with_intent(&blank),
            Err(StorageError::RequestReuse)
        ));
        assert!(service.recover_project_creation(&blank).is_err());
        assert_eq!(service.project(original.id).unwrap(), original);
        for extra in [
            json!(null),
            json!("unreviewed-template"),
            json!({"motion-study-v1":null}),
        ] {
            let mut wire = serde_json::to_value(&example).unwrap();
            wire["template"] = extra;
            assert!(serde_json::from_value::<ProjectCreationIntent>(wire).is_err());
        }
        let legacy: ProjectCreationIntent = serde_json::from_value(json!({
            "request_id":Uuid::new_v4(), "title":"Legacy blank project",
        }))
        .unwrap();
        assert_eq!(legacy.template, None);
        assert!(
            serde_json::to_value(&legacy)
                .unwrap()
                .get("template")
                .is_none()
        );
    }

    #[test]
    fn lost_example_reply_recovers_the_original_without_replacing_saved_edits() {
        let dir = tempfile::tempdir().unwrap();
        let database = dir.path().join("example.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let request = intent();
        let original = create(&service, &request);
        let edited = service
            .apply(
                original.id,
                &original.stamp(),
                "saved-independent-edit",
                &Change::RenameProject {
                    title: "My edited motion study".into(),
                },
            )
            .unwrap()
            .project;
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        let recovered = recover_project_creation(&reopened, &request)
            .unwrap()
            .unwrap();
        assert!(recovered.replayed);
        assert_eq!(recovered.project, original);
        assert_eq!(reopened.project(original.id).unwrap(), edited);
        assert_eq!(reopened.projects(100).unwrap().len(), 1);
        assert!(
            reopened
                .production_receipts(original.id, 100)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn bundled_example_exports_and_imports_portably_without_accounts_or_external_media() {
        let dir = tempfile::tempdir().unwrap();
        let source = StudioService::open(dir.path().join("source.sqlite3")).unwrap();
        let original = create(&source, &intent());
        let bundle = dir.path().join("motion-study.mwproject");
        source.export_project_bundle(original.id, &bundle).unwrap();
        let destination = dir.path().join("destination");
        std::fs::create_dir(&destination).unwrap();
        let imported = StudioService::open(destination.join("reopened.sqlite3")).unwrap();
        let restored = imported.import_project_bundle(&bundle).unwrap();
        assert_eq!(restored.scenes, original.scenes);
        assert_eq!(restored.deliverables, original.deliverables);
        assert_eq!(
            restored.production_design.delivery_recipes,
            original.production_design.delivery_recipes
        );
        assert_eq!(restored.assets, original.assets);
        assert!(
            imported
                .production_receipts(restored.id, 100)
                .unwrap()
                .is_empty()
        );
        let source = compile_hyperframes_source(&restored, restored.deliverables[0].id).unwrap();
        assert_eq!(source.inventory().composition.frame_count, 180);
        drop(imported);
        let reopened = StudioService::open(destination.join("reopened.sqlite3")).unwrap();
        assert_eq!(reopened.project(restored.id).unwrap(), restored);
    }
}
