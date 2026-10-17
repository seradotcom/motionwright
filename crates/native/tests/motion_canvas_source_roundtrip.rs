//! Persisted authored Motion Canvas source survives bundle import and a new-process edit.
//! An equivalent Film is a source-continuity assertion, NOT proof of rendered pixels.
//! The independent pixel inspector is tooling/native_motion_canvas_roundtrip.py.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, MotionInterpolation, MotionProperty, NodeStyle,
    RevisionStamp,
};
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use std::collections::BTreeSet;
use uuid::Uuid;

fn film_options(scene_id: Uuid) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    }
}

fn add_source(service: &StudioService) -> (motionwright_domain::Project, Uuid, Uuid) {
    let project = service
        .create_project("Editable native source roundtrip")
        .unwrap();
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "motion-canvas-source:scene",
            &Change::AddScene {
                name: "Original, two-second shot".into(),
                objective: "Persist a source object, then edit and reopen it".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let scene_id = project.scenes[0].id;
    let node_id = Uuid::new_v4();
    let node = CanvasNode {
        id: node_id,
        name: "Owned source rectangle".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 780.0,
        y: 320.0,
        width: 180.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some("#F5F5F2".into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: None,
            font_size: None,
            font_weight: None,
            line_height: None,
            blend_mode: BlendMode::Normal,
        },
        relations: Vec::new(),
        property_locks: BTreeSet::new(),
        keyframes: Vec::new(),
    };
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "motion-canvas-source:node",
            &Change::AddCanvasNode { scene_id, node },
        )
        .unwrap()
        .project;
    let profile_id = project.deliverables[0].id;
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "motion-canvas-source:authored-motion",
            &Change::SetCanvasLinearPositionMotion {
                scene_id,
                node_id,
                deliverable_id: profile_id,
                start_x: 600.0,
                start_y: 320.0,
                end_frame: 30,
            },
        )
        .unwrap()
        .project;
    (project, scene_id, node_id)
}

fn projected_film(project: &motionwright_domain::Project, scene_id: Uuid) -> serde_json::Value {
    let segments =
        build_motion_canvas_segments(project, project.deliverables[0].id, &film_options(scene_id))
            .unwrap();
    assert_eq!(segments.len(), 1);
    assert_eq!(segments[0].frame_count, 60);
    assert_eq!(segments[0].scene_ids, vec![scene_id]);
    assert_eq!(segments[0].film.output.width, 1920);
    assert_eq!(segments[0].film.output.height, 1080);
    serde_json::to_value(&segments[0].film).unwrap()
}

// Existing native-production.yml production-source selects production::* without
// changing a shared workflow or running a native provider in this test.
#[cfg(test)]
mod production {
    use super::*;

    #[test]
    fn current_source_survives_new_process_portable_import_reedit_and_second_reopen() {
        let scratch = tempfile::tempdir().unwrap();
        let database = scratch.path().join("authoring.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let (original, scene_id, node_id) = add_source(&service);
        let original_film = projected_film(&original, scene_id);
        assert_eq!(original.scenes[0].nodes[0].id, node_id);
        assert_eq!(original.scenes[0].nodes[0].keyframes.len(), 4);

        let bundle = scratch.path().join("editable-source.motionwright");
        service.export_project_bundle(original.id, &bundle).unwrap();
        drop(service);

        // An independent StudioService instance must read the same authoritative
        // values from SQLite, not a browser draft or a retained in-memory clone.
        let reopening = StudioService::open(&database).unwrap();
        let reopened = reopening.project(original.id).unwrap();
        assert_eq!(reopened, original);
        assert_eq!(projected_film(&reopened, scene_id), original_film);
        drop(reopening);

        let imported_database = scratch.path().join("imported.sqlite3");
        let destination = StudioService::open(&imported_database).unwrap();
        let imported = destination.import_project_bundle(&bundle).unwrap();
        assert_eq!(imported.id, original.id);
        assert_eq!(imported.revision, original.revision);
        assert_ne!(imported.generation, original.generation);
        assert_eq!(imported.scenes, original.scenes);
        assert_eq!(imported.assets, original.assets);
        assert_eq!(imported.deliverables, original.deliverables);
        assert_eq!(projected_film(&imported, scene_id), original_film);

        // Deliberately edit the imported canonical source. The original bundle
        // remains intact, while a new revision controls the next native Film.
        let mut updated_style = imported.scenes[0].nodes[0].style.clone();
        updated_style.fill = Some("#E85A35".into());
        let edited = destination
            .apply(
                imported.id,
                &RevisionStamp::from(&imported),
                "motion-canvas-source:edit-after-import",
                &Change::UpdateCanvasStyle {
                    scene_id,
                    node_id,
                    style: updated_style,
                },
            )
            .unwrap()
            .project;
        assert_eq!(edited.id, original.id);
        assert_eq!(edited.generation, imported.generation);
        assert_eq!(edited.revision, imported.revision + 1);
        assert_eq!(edited.scenes[0].nodes[0].id, node_id);
        assert_eq!(
            edited.scenes[0].nodes[0].keyframes,
            original.scenes[0].nodes[0].keyframes,
        );
        assert_eq!(edited.assets, imported.assets);
        assert_eq!(edited.deliverables, imported.deliverables);
        assert_eq!(
            edited.scenes[0].nodes[0].style.fill.as_deref(),
            Some("#E85A35"),
        );
        let edited_film = projected_film(&edited, scene_id);
        assert_ne!(
            edited_film, original_film,
            "source edit must change canonical Film"
        );
        assert!(
            destination
                .apply(
                    imported.id,
                    &RevisionStamp::from(&imported),
                    "motion-canvas-source:stale-edit",
                    &Change::UpdateCanvasStyle {
                        scene_id,
                        node_id,
                        style: original.scenes[0].nodes[0].style.clone(),
                    },
                )
                .is_err()
        );
        drop(destination);

        let final_service = StudioService::open(&imported_database).unwrap();
        let reedited = final_service.project(original.id).unwrap();
        assert_eq!(reedited, edited);
        assert_eq!(projected_film(&reedited, scene_id), edited_film);

        // Reopening the untouched bundle again must reconstruct the OLD Film,
        // never silently resolve it to this changed imported project.
        let old_copy_db = scratch.path().join("original-reopened-again.sqlite3");
        let other = StudioService::open(old_copy_db).unwrap();
        let old_copy = other.import_project_bundle(&bundle).unwrap();
        assert_eq!(projected_film(&old_copy, scene_id), original_film);
        assert_ne!(old_copy.generation, reedited.generation);
    }

    #[test]
    fn source_motion_is_not_silently_retimed_into_a_different_backend_profile() {
        let scratch = tempfile::tempdir().unwrap();
        let service = StudioService::open(scratch.path().join("retime.sqlite3")).unwrap();
        let (project, scene_id, node_id) = add_source(&service);
        let baseline = projected_film(&project, scene_id);
        let mut unsupported = film_options(scene_id);
        unsupported.frame_rate = Rate { num: 60, den: 1 };
        assert!(
            build_motion_canvas_segments(&project, project.deliverables[0].id, &unsupported)
                .is_err(),
            "a different frame rate must be a closed unsupported result",
        );
        let observed = service.project(project.id).unwrap();
        assert_eq!(observed, project);
        assert_eq!(projected_film(&observed, scene_id), baseline);
        assert!(observed.scenes[0].nodes[0].keyframes.iter().any(|frame| {
            frame.property == MotionProperty::X
                && frame.interpolation == MotionInterpolation::Linear
        }));
        assert_eq!(observed.scenes[0].nodes[0].id, node_id);
    }
    #[test]
    fn pinned_semwright_managed_binding_rejects_stale_film_and_realization() {
        let scratch = tempfile::tempdir().unwrap();
        let service =
            StudioService::open(scratch.path().join("native-managed-binding.sqlite3")).unwrap();
        let (project, scene_id, node_id) = add_source(&service);
        let first_segments = build_motion_canvas_segments(
            &project,
            project.deliverables[0].id,
            &film_options(scene_id),
        )
        .unwrap();
        let original_film = first_segments[0].film.clone();
        let original_realization = semwright_motion_authoring::realize(&original_film).unwrap();
        assert!(!original_realization.instructions.is_empty());

        // This is an authoritative pinned-type validation of the source
        // binding only. It is not an actual provider registration, grant,
        // native code generation, saved-source reopen, or render receipt.
        let binding = semwright_motion_authoring::ManagedBinding {
            schema_version: 1,
            intent: original_film.clone(),
            intent_digest: semwright_semantic_composition::canonical_digest(&original_film)
                .unwrap(),
            realization: original_realization.clone(),
            realization_digest: semwright_semantic_composition::canonical_digest(
                &original_realization,
            )
            .unwrap(),
            projection_digest: semwright_semantic_composition::canonical_digest(
                &serde_json::json!({"fixture_projection_not_host_verified": true}),
            )
            .unwrap(),
        };
        binding.validate().unwrap();

        let mut stale_digest = binding.clone();
        stale_digest.intent.output.width += 1;
        assert!(
            stale_digest.validate().is_err(),
            "a changed Film must never reuse the prior intent digest"
        );
        let mut forged_realization = binding.clone();
        forged_realization.realization.instructions.clear();
        assert!(
            forged_realization.validate().is_err(),
            "a missing native instruction must never pass the old Film binding"
        );
        let mut edited_style = project.scenes[0].nodes[0].style.clone();
        edited_style.fill = Some("#E85A35".into());
        let edited = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "motion-canvas-source:pinned-binding-edit",
                &Change::UpdateCanvasStyle {
                    scene_id,
                    node_id,
                    style: edited_style,
                },
            )
            .unwrap()
            .project;
        let edited_film = build_motion_canvas_segments(
            &edited,
            edited.deliverables[0].id,
            &film_options(scene_id),
        )
        .unwrap()[0]
            .film
            .clone();
        assert_ne!(edited_film, original_film);

        // Prove the authoring edit changes ONLY the predeclared source color
        // in canonical Film. Other transforms, timing, camera, rights and
        // asset/source nodes must remain identical across the persisted edit.
        fn rewrite_color_only(value: &mut serde_json::Value, from: &str, to: &str) -> usize {
            match value {
                serde_json::Value::String(text) if text == from => {
                    *text = to.into();
                    1
                }
                serde_json::Value::Array(items) => items
                    .iter_mut()
                    .map(|item| rewrite_color_only(item, from, to))
                    .sum(),
                serde_json::Value::Object(fields) => fields
                    .values_mut()
                    .map(|item| rewrite_color_only(item, from, to))
                    .sum(),
                _ => 0,
            }
        }
        let mut permitted_film = serde_json::to_value(&original_film).unwrap();
        let admitted_color_changes = rewrite_color_only(&mut permitted_film, "#F5F5F2", "#E85A35");
        assert!(
            admitted_color_changes > 0,
            "the requested visual fill must be represented in canonical Film"
        );
        assert_eq!(
            permitted_film,
            serde_json::to_value(&edited_film).unwrap(),
            "an edit of one source fill must not modify unrelated Film properties, source identity or frame clock"
        );

        let mut obsolete = binding;
        obsolete.intent = edited_film;
        assert!(
            obsolete.validate().is_err(),
            "edits must regenerate the pinned Film digest and realization"
        );
    }
} // mod production
