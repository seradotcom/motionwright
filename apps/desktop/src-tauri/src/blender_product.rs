//! Desktop read adapters for the existing Blender source and receipt readers.
//!
//! These functions observe the canonical Store. A snapshot, its CAS digest, and
//! absence of a local attempt are data; none authorize native execution or retry.
//! Initial actor/resource admission remains the canonical application boundary.
//! No connection, native coordinator, effect grant, or private journal is created.

use motionwright_domain::Project;
use motionwright_native::production::{
    BlenderProductAttemptProjection, BlenderProductAttemptRequest, BlenderProductSourceScope,
    BlenderProductSourceSnapshot, read_blender_product_attempt, read_blender_product_source,
};
use motionwright_service::StudioService;
use semwright_native_sdk::ErrorCode;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductReadRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductAttemptReadRequest {
    pub source: BlenderProductReadRequest,
    pub original_request_id: String,
    pub receipt_id: Option<Uuid>,
}

/// Failure of this read only. It does not revise a prior native attempt's state.
#[derive(Debug, Serialize)]
pub struct BlenderProductReadFailure {
    pub schema: &'static str,
    pub code: ErrorCode,
    pub message: &'static str,
}

fn failure(code: ErrorCode, message: &'static str) -> BlenderProductReadFailure {
    BlenderProductReadFailure {
        schema: "motionwright.blender.product-read-failure/1",
        code,
        message,
    }
}

pub fn read_task_failure() -> BlenderProductReadFailure {
    failure(
        ErrorCode::Unavailable,
        "The saved Blender source read did not complete.",
    )
}

fn checked_scope(
    service: &StudioService,
    request: &BlenderProductReadRequest,
) -> Result<BlenderProductSourceScope, BlenderProductReadFailure> {
    if [
        request.project_id,
        request.generation,
        request.scene_id,
        request.capsule_id,
    ]
    .iter()
    .any(Uuid::is_nil)
    {
        return Err(failure(
            ErrorCode::InvalidArgument,
            "The saved Blender source scope is incomplete.",
        ));
    }
    let project = service.project(request.project_id).map_err(|_| {
        failure(
            ErrorCode::Unavailable,
            "The current Blender project could not be read.",
        )
    })?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err(failure(
            ErrorCode::StaleReference,
            "The project changed; read its current Blender source again.",
        ));
    }
    Ok(BlenderProductSourceScope {
        project_id: project.id,
        // Resource identity comes from the durable Project, never an IPC string.
        expected: project.stamp(),
        scene_id: request.scene_id,
        capsule_id: request.capsule_id,
    })
}

/// Revalidate after returning from the blocking reader, before projecting to IPC.
/// This is a freshness check, not a source of actor or native effect authority.
pub fn ensure_read_current(
    service: &StudioService,
    observed: &Project,
) -> Result<(), BlenderProductReadFailure> {
    let current = service.project(observed.id).map_err(|_| {
        failure(
            ErrorCode::Unavailable,
            "The current Blender project could not be read.",
        )
    })?;
    if current != *observed {
        return Err(failure(
            ErrorCode::StaleReference,
            "The project changed during the saved Blender source read.",
        ));
    }
    Ok(())
}

pub fn read_source(
    service: &StudioService,
    request: &BlenderProductReadRequest,
) -> Result<BlenderProductSourceSnapshot, BlenderProductReadFailure> {
    let scope = checked_scope(service, request)?;
    // The native reader owns complete watermark history, exact CAS bytes and
    // closed recipe decoding. Do not reconstruct those rules in the Desktop.
    read_blender_product_source(service, &scope).map_err(|error| {
        failure(
            error.code,
            "The saved Blender source or its original lineage is unavailable.",
        )
    })
}

pub fn read_attempt(
    service: &StudioService,
    request: &BlenderProductAttemptReadRequest,
) -> Result<BlenderProductAttemptProjection, BlenderProductReadFailure> {
    let scope = checked_scope(service, &request.source)?;
    read_blender_product_attempt(
        service,
        &BlenderProductAttemptRequest {
            scope,
            original_request_id: request.original_request_id.clone(),
            receipt_id: request.receipt_id,
        },
    )
    .map_err(|error| {
        failure(
            error.code,
            "The original Blender attempt could not be read; its outcome is not changed.",
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, Fidelity, FidelityReport, NativeCapsule, RendererKind};
    use motionwright_native::{
        blender_product_scene::{CameraCurve, ProductScene},
        production::BLENDER_PRODUCT_RECIPE_MEDIA_TYPE,
    };
    use serde_json::{Value, json};
    use std::fs;
    use tempfile::{TempDir, tempdir};

    struct Fixture {
        service: StudioService,
        project: Project,
        request: BlenderProductReadRequest,
        recipe: ProductScene,
        root: TempDir,
    }

    fn fixture() -> Fixture {
        let root = tempdir().unwrap();
        let service = StudioService::open(root.path().join("desktop-blender.sqlite3")).unwrap();
        let mut project = service
            .create_project("Desktop Blender reader fixture")
            .unwrap();
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "reader-fixture-scene",
                &Change::AddScene {
                    name: "Saved Blender source".into(),
                    objective: "Read real SQLite and CAS without native execution".into(),
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
                "reader-fixture-renderer",
                &Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::Blender,
                },
            )
            .unwrap()
            .project;
        let recipe = ProductScene::vessel("DesktopReader", CameraCurve::BezierAutoClamped);
        let source = root.path().join("recipe.json");
        fs::write(&source, serde_json::to_vec(&recipe).unwrap()).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "reader-fixture-asset",
                &source,
                "recipe.json".into(),
                BLENDER_PRODUCT_RECIPE_MEDIA_TYPE.into(),
            )
            .unwrap()
            .project;
        let asset = project.assets.last().unwrap().clone();
        let capsule_id = Uuid::now_v7();
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "reader-fixture-capsule",
                &Change::UpsertNativeCapsule {
                    capsule: NativeCapsule {
                        id: capsule_id,
                        scene_id,
                        source_asset_id: asset.id,
                        source_sha256: asset.content_sha256.unwrap(),
                        label: "Source reader fixture".into(),
                        fidelity: FidelityReport {
                            renderer: "Blender".into(),
                            renderer_version: "unobserved".into(),
                            visual: Fidelity::Unavailable,
                            temporal: Fidelity::Unavailable,
                            structural: Fidelity::Unavailable,
                            editable: Fidelity::Unavailable,
                            losses: vec!["Unit fixture; no Blender execution.".into()],
                            evidence_sha256: None,
                        },
                        editable_parameters: vec!["product.annotation.body".into()],
                        native_editor_hint: "Blender".into(),
                        property_fidelity: vec![],
                    },
                },
            )
            .unwrap()
            .project;
        let request = BlenderProductReadRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            capsule_id,
        };
        Fixture {
            root,
            service,
            project,
            request,
            recipe,
        }
    }

    fn request_json(request: &BlenderProductReadRequest) -> Value {
        json!({
            "project_id": request.project_id, "generation": request.generation,
            "revision": request.revision, "scene_id": request.scene_id,
            "capsule_id": request.capsule_id
        })
    }

    #[test]
    fn blender_reader_dtos_reject_injected_authority_paths_and_journals() {
        let f = fixture();
        let source = request_json(&f.request);
        for field in [
            "connection",
            "output_root",
            "effect_grant",
            "execution_grant",
            "journal",
            "expected",
            "automatic_retry",
        ] {
            let mut injected = source.clone();
            injected[field] = json!("not-admitted");
            assert!(
                serde_json::from_value::<BlenderProductReadRequest>(injected.clone()).is_err(),
                "{field}"
            );
            let nested = json!({"source": injected, "original_request_id": "original"});
            assert!(serde_json::from_value::<BlenderProductAttemptReadRequest>(nested).is_err());
            let mut outer = json!({"source": source, "original_request_id": "original"});
            outer[field] = json!("not-admitted");
            assert!(serde_json::from_value::<BlenderProductAttemptReadRequest>(outer).is_err());
        }
        assert!(serde_json::from_value::<BlenderProductReadRequest>(source).is_ok());
    }

    #[test]
    fn blender_reader_checks_current_generation_revision_and_resource_membership() {
        let f = fixture();
        for field in 0..4 {
            let mut request = f.request.clone();
            match field {
                0 => request.generation = Uuid::now_v7(),
                1 => request.revision += 1,
                2 => request.scene_id = Uuid::now_v7(),
                _ => request.capsule_id = Uuid::now_v7(),
            }
            assert!(read_source(&f.service, &request).is_err());
        }
        let mut absent = f.request.clone();
        absent.project_id = Uuid::now_v7();
        assert!(read_source(&f.service, &absent).is_err());
        let mut nil = f.request.clone();
        nil.scene_id = Uuid::nil();
        assert_eq!(
            read_source(&f.service, &nil).unwrap_err().code,
            ErrorCode::InvalidArgument
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(
            f.service
                .production_receipt_watermark(f.project.id)
                .unwrap()
                .count,
            0
        );
    }

    #[test]
    fn blender_reader_returns_verified_cas_data_without_receipt_or_effect_authority() {
        let f = fixture();
        let before = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let result = read_source(&f.service, &f.request).unwrap();
        ensure_read_current(&f.service, &result.project).unwrap();
        assert_eq!(result.project, f.project);
        assert_eq!(result.effective_recipe, f.recipe);
        assert_eq!(result.source_kind, "authored_recipe");
        assert!(result.source_cas_verified);
        assert!(result.attempt.is_none() && result.lineage.is_none());
        assert!(!result.execution_grant_inherited && !result.native_dispatch_performed);
        assert!(!result.source_acceptance_inherited && !result.visual_acceptance);
        assert!(!result.full_1200_frame_render && !result.decoded_pixel_difference_verified);
        assert!(!result.human_acceptance);
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(
            f.service
                .production_receipt_watermark(f.project.id)
                .unwrap(),
            before
        );
    }

    #[test]
    fn blender_reader_rejects_a_project_changed_before_ipc_projection() {
        let f = fixture();
        let observed = read_source(&f.service, &f.request).unwrap();
        f.service
            .apply(
                f.project.id,
                &f.project.stamp(),
                "edit-after-reader",
                &Change::AddScene {
                    name: "Concurrent source change".into(),
                    objective: "Invalidate the observed snapshot".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap();
        assert_eq!(
            ensure_read_current(&f.service, &observed.project)
                .unwrap_err()
                .code,
            ErrorCode::StaleReference
        );
    }

    #[test]
    fn blender_attempt_absence_is_a_read_error_and_never_a_new_dispatch() {
        let f = fixture();
        let before = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let request = BlenderProductAttemptReadRequest {
            source: f.request.clone(),
            original_request_id: "original-never-observed-locally".into(),
            receipt_id: None,
        };
        let failure = read_attempt(&f.service, &request).unwrap_err();
        assert_eq!(failure.code, ErrorCode::Unavailable);
        let output = serde_json::to_string(&failure).unwrap();
        assert!(!output.contains(&f.root.path().to_string_lossy().to_string()));
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(
            f.service
                .production_receipt_watermark(f.project.id)
                .unwrap(),
            before
        );
    }

    #[test]
    fn blender_imported_recipe_reader_keeps_new_generation_and_no_execution_grant() {
        let f = fixture();
        let bundle = f.root.path().join("portable-source");
        f.service
            .export_project_bundle(f.project.id, &bundle)
            .unwrap();
        let destination = StudioService::open(f.root.path().join("imported.sqlite3")).unwrap();
        let imported = destination.import_project_bundle(&bundle).unwrap();
        assert_eq!(imported.id, f.project.id);
        assert_ne!(imported.generation, f.project.generation);
        let mut request = f.request.clone();
        assert!(read_source(&destination, &request).is_err());
        request.generation = imported.generation;
        let before = destination
            .production_receipt_watermark(imported.id)
            .unwrap();
        let result = read_source(&destination, &request).unwrap();
        assert_eq!(result.project, imported);
        assert_eq!(result.effective_recipe, f.recipe);
        assert!(result.attempt.is_none());
        assert!(!result.execution_grant_inherited && !result.native_dispatch_performed);
        assert!(!result.source_acceptance_inherited && !result.human_acceptance);
        assert_eq!(destination.project(imported.id).unwrap(), imported);
        assert_eq!(
            destination
                .production_receipt_watermark(imported.id)
                .unwrap(),
            before
        );
    }
}
