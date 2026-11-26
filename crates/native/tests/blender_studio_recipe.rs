//! Actual saved source reader; no Blender execution or effect admission.
use motionwright_domain::{Change, NativeCapsuleSourceSpec, RendererKind};
use motionwright_native::blender_product_scene::{
    CameraCurve, Coating, ProductCameraMotion, ProductScene, project_product_scene,
};
use motionwright_native::production::{
    BLENDER_PRODUCT_RECIPE_MEDIA_TYPE, BlenderProductSourceScope, decode_blender_product_recipe,
    read_blender_product_source,
};
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[test]
fn studio_recipe_fixture_is_an_exact_saved_and_reopened_native_product_source() {
    let bytes = include_bytes!("fixtures/blender-product-studio-recipe.json");
    let expected = ProductScene {
        version: 1,
        namespace: "StudioVessel".into(),
        coating: Coating::Graphite,
        roughness: 0.37,
        metallic: 0.72,
        samples: 96,
        annotation: "AUTHOR SOURCE\nGRAPHITE".into(),
        camera_curve: CameraCurve::Linear,
        camera_motion: None,
    };
    assert_saved_recipe(bytes, expected);
}

#[test]
fn authored_spatial_camera_recipe_retains_both_paths_after_real_cas_reopen() {
    let mut expected = ProductScene::vessel("StudioVessel", CameraCurve::Linear);
    expected.camera_motion = Some(ProductCameraMotion {
        arc_degrees: 25,
        dolly_percent: 12,
    });
    let bytes = serde_json::to_vec_pretty(&expected).unwrap();
    assert_saved_recipe(&bytes, expected);
}

fn assert_saved_recipe(bytes: &[u8], expected: ProductScene) {
    assert_eq!(decode_blender_product_recipe(bytes).unwrap(), expected);
    let root = tempfile::tempdir().unwrap();
    let database = root.path().join("recipe.sqlite3");
    let source = root.path().join("blender-product-StudioVessel.json");
    std::fs::write(&source, bytes).unwrap();
    let service = StudioService::open(&database).unwrap();
    let mut project = service.create_project("Studio product recipe").unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "scene",
            &Change::AddScene {
                name: "Product".into(),
                objective: "Owned recipe".into(),
                duration_seconds: 40,
            },
        )
        .unwrap()
        .project;
    let scene_id = project.scenes[0].id;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            },
        )
        .unwrap()
        .project;
    let capsule_id = Uuid::new_v4();
    let saved = service
        .import_native_capsule_source_file(
            project.id,
            &project.stamp(),
            "recipe",
            &source,
            NativeCapsuleSourceSpec {
                name: "blender-product-StudioVessel.json".into(),
                media_type: BLENDER_PRODUCT_RECIPE_MEDIA_TYPE.into(),
                capsule_id,
                scene_id,
                label: "Product vessel / StudioVessel".into(),
                editable_parameters: vec![
                    "product.coating.variant".into(),
                    "product.coating.roughness".into(),
                    "product.coating.metallic".into(),
                    "product.annotation.body".into(),
                ],
                native_editor_hint: "Motionwright Blender product recipe v1".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(saved.revision, project.revision + 1);
    let digest = format!("{:x}", Sha256::digest(bytes));
    drop(service);
    std::fs::remove_file(source).unwrap();
    let reopened = StudioService::open(database).unwrap();
    let snapshot = read_blender_product_source(
        &reopened,
        &BlenderProductSourceScope {
            project_id: saved.id,
            expected: saved.stamp(),
            scene_id,
            capsule_id,
        },
    )
    .unwrap();
    assert_eq!(snapshot.project, saved);
    assert_eq!(snapshot.current_source.sha256, digest);
    assert_eq!(snapshot.current_source, snapshot.original_recipe);
    assert_eq!(snapshot.effective_recipe, expected);
    assert_eq!(
        project_product_scene(&snapshot.effective_recipe).unwrap(),
        project_product_scene(&expected).unwrap()
    );
    assert_eq!(snapshot.source_kind, "authored_recipe");
    assert!(snapshot.source_cas_verified);
    assert!(!snapshot.native_dispatch_performed && !snapshot.execution_grant_inherited);
    assert!(!snapshot.visual_acceptance && !snapshot.human_acceptance);
    assert!(snapshot.attempt.is_none() && snapshot.lineage.is_none());
}
