//! Read-only observation of a source already attached to the current project.
//! This command neither imports a source nor grants runtime, editing or export.

use crate::AppState;
use motionwright_creative_library::{
    AttachedSourceInspection, MAX_ATTACHED_INSPECTION_BYTES, inspect_attached_native_source,
};
use motionwright_domain::{Asset, NativeCapsule, Project, RevisionStamp};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use tauri::State;
use uuid::Uuid;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeAttachmentInspectionRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    capsule_id: Uuid,
}

/// A report of bytes observed at this exact project stamp, not an import,
/// runtime grant, export token, provider receipt or editable source handle.
#[derive(Debug, Serialize)]
pub struct NativeAttachmentInspectionResponse {
    schema: &'static str,
    project_id: Uuid,
    project_resource: String,
    project_generation: Uuid,
    project_revision: u64,
    observation: AttachedSourceInspection,
    owner_source_bytes_exported: bool,
    project_changed: bool,
    operation: &'static str,
    owner_runtime_granted: bool,
    source_semantically_imported: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct AttachedSourceSelection {
    stamp: RevisionStamp,
    capsule: NativeCapsule,
    asset: Asset,
}

fn select_current_attachment(
    project: &Project,
    request: &NativeAttachmentInspectionRequest,
) -> Result<AttachedSourceSelection, String> {
    if request.project_id.is_nil() || request.generation.is_nil() || request.capsule_id.is_nil() {
        return Err(
            "Native source inspection requires exact project and capsule identities.".into(),
        );
    }
    if project.id != request.project_id
        || project.generation != request.generation
        || project.revision != request.revision
    {
        return Err("The project changed; reload before inspecting its native attachment.".into());
    }
    project
        .validate()
        .map_err(|_| "The current project cannot admit a native source observation.".to_string())?;
    let capsule = project
        .production_design
        .capsules
        .iter()
        .find(|capsule| capsule.id == request.capsule_id)
        .ok_or_else(|| {
            "The exact native source capsule is not attached to this project.".to_string()
        })?
        .clone();
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .ok_or_else(|| "The native source asset is absent from the current project.".to_string())?
        .clone();
    if asset.content_sha256.as_deref() != Some(capsule.source_sha256.as_str()) {
        return Err("Native source capsule does not match its stored asset fingerprint.".into());
    }
    Ok(AttachedSourceSelection {
        stamp: RevisionStamp::from(project),
        capsule,
        asset,
    })
}

fn current_attachment(
    service: &StudioService,
    request: &NativeAttachmentInspectionRequest,
) -> Result<AttachedSourceSelection, String> {
    let project = service.project(request.project_id).map_err(|_| {
        "The current project is unavailable for native source inspection.".to_string()
    })?;
    select_current_attachment(&project, request)
}

fn require_same_attachment(
    selected: &AttachedSourceSelection,
    current: &AttachedSourceSelection,
) -> Result<(), String> {
    if selected != current {
        return Err(
            "The attached source changed during inspection; reload the current project.".into(),
        );
    }
    Ok(())
}

fn inspect_from_service(
    service: &StudioService,
    request: &NativeAttachmentInspectionRequest,
) -> Result<NativeAttachmentInspectionResponse, String> {
    let selected = current_attachment(service, request)?;
    // Storage owns CAS path, read-budget and digest enforcement. No path,
    // source digest or replacement bytes come from the WebView, and this
    // module does not introduce a second path-based CAS reader.
    let bytes = service.store().lock()
        .read_blob(&selected.capsule.source_sha256, MAX_ATTACHED_INSPECTION_BYTES as u64)
        .map_err(|_| {
            "The original source is absent, corrupt or exceeds the inspection budget. Its project record is unchanged.".to_string()
        })?;
    let observation = inspect_attached_native_source(&selected.capsule, &selected.asset, &bytes)
        .map_err(|error| error.to_string())?;
    // A metadata-only capsule change matters even if a broken writer failed
    // to advance its revision. This comparison also includes the full stamp.
    require_same_attachment(&selected, &current_attachment(service, request)?)?;
    Ok(NativeAttachmentInspectionResponse {
        schema: "motionwright.native-attachment-inspection/1",
        project_id: request.project_id,
        project_resource: selected.stamp.resource,
        project_generation: selected.stamp.generation,
        project_revision: selected.stamp.revision,
        observation,
        owner_source_bytes_exported: false,
        project_changed: false,
        operation: "read_only_attach_first",
        owner_runtime_granted: false,
        source_semantically_imported: false,
    })
}

/// Only current project identity and a saved capsule ID are accepted. The
/// existing application wires this command; no imported observation JSON
/// can authorize another operation.
#[tauri::command]
pub async fn native_attached_source_inspection(
    state: State<'_, AppState>,
    request: NativeAttachmentInspectionRequest,
) -> Result<NativeAttachmentInspectionResponse, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || inspect_from_service(&service, &request))
        .await
        .map_err(|_| {
            "Native source inspection was interrupted; no project edit was made.".to_string()
        })?
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, Fidelity, FidelityReport, NativePropertyFidelity};
    use serde_json::json;
    use std::{fs, path::PathBuf};

    struct TestFolder(PathBuf);
    impl Drop for TestFolder {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn source_project(bytes: &[u8]) -> (Project, NativeAttachmentInspectionRequest) {
        use sha2::{Digest, Sha256};
        let mut project = Project::new("Attached source inspection fixture").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Source holder".into(),
                objective: "Keep original source bytes without interpreting them".into(),
                duration_seconds: 1,
            })
            .unwrap();
        let asset = Asset {
            id: Uuid::now_v7(),
            name: "Owner original source".into(),
            media_type: "application/x-blender".into(),
            content_sha256: Some(hex::encode(Sha256::digest(bytes))),
            source_revision: Some("fixture-import".into()),
        };
        let capsule = NativeCapsule {
            id: Uuid::now_v7(),
            scene_id: project.scenes[0].id,
            source_asset_id: asset.id,
            source_sha256: asset.content_sha256.clone().unwrap(),
            label: "Opaque owner source".into(),
            fidelity: FidelityReport {
                renderer: "owner-declared-blender".into(),
                renderer_version: "not-observed".into(),
                visual: Fidelity::Unavailable,
                temporal: Fidelity::Unavailable,
                structural: Fidelity::Unavailable,
                editable: Fidelity::Unavailable,
                losses: vec!["Only original source bytes may be observed".into()],
                evidence_sha256: None,
            },
            editable_parameters: vec![],
            native_editor_hint: "Owner-controlled application".into(),
            property_fidelity: vec![],
        };
        let request = NativeAttachmentInspectionRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            capsule_id: capsule.id,
        };
        project.assets.push(asset);
        project.production_design.capsules.push(capsule);
        project.validate().unwrap();
        (project, request)
    }

    fn stored_source() -> (
        TestFolder,
        StudioService,
        Project,
        NativeAttachmentInspectionRequest,
        Vec<u8>,
    ) {
        let directory =
            std::env::temp_dir().join(format!("mw-attached-inspection-{}", Uuid::now_v7()));
        fs::create_dir(&directory).unwrap();
        let folder = TestFolder(directory);
        let bytes = b"BLENDER-v400 original opaque fixture bytes".to_vec();
        let source = folder.0.join("import-source.blend");
        fs::write(&source, &bytes).unwrap();
        let service = StudioService::open(folder.0.join("project.sqlite3")).unwrap();
        let (project, request) = source_project(&bytes);
        let blob = service.store().lock().ingest_blob_file(&source).unwrap();
        assert_eq!(
            blob.sha256,
            project.production_design.capsules[0].source_sha256
        );
        service.store().lock().create_project(&project).unwrap();
        fs::remove_file(&source).unwrap();
        (folder, service, project, request, bytes)
    }

    #[test]
    fn attachment_request_rejects_paths_bytes_claims_and_extra_authority() {
        let (_, request) = source_project(b"BLENDER-v400 fixture");
        let allowed = json!({
            "project_id": request.project_id,
            "generation": request.generation,
            "revision": request.revision,
            "capsule_id": request.capsule_id,
        });
        assert!(
            serde_json::from_value::<NativeAttachmentInspectionRequest>(allowed.clone()).is_ok()
        );
        for key in [
            "path",
            "source_sha256",
            "bytes",
            "observation",
            "effect_grant",
            "argv",
            "editable",
        ] {
            let mut rejected = allowed.clone();
            rejected
                .as_object_mut()
                .unwrap()
                .insert(key.into(), json!("caller value"));
            assert!(
                serde_json::from_value::<NativeAttachmentInspectionRequest>(rejected).is_err(),
                "{key}"
            );
        }
    }

    #[test]
    fn attachment_requires_full_project_stamp_and_exact_capsule() {
        let (project, request) = source_project(b"BLENDER-v400 fixture");
        for changed in [
            NativeAttachmentInspectionRequest {
                project_id: Uuid::now_v7(),
                ..request.clone()
            },
            NativeAttachmentInspectionRequest {
                generation: Uuid::now_v7(),
                ..request.clone()
            },
            NativeAttachmentInspectionRequest {
                revision: request.revision + 1,
                ..request.clone()
            },
            NativeAttachmentInspectionRequest {
                capsule_id: Uuid::now_v7(),
                ..request.clone()
            },
            NativeAttachmentInspectionRequest {
                capsule_id: Uuid::nil(),
                ..request.clone()
            },
        ] {
            assert!(select_current_attachment(&project, &changed).is_err());
        }
    }

    #[test]
    fn attachment_rechecks_capsule_metadata_even_at_the_same_revision() {
        let (project, request) = source_project(b"BLENDER-v400 fixture");
        let selected = select_current_attachment(&project, &request).unwrap();
        let mut changed = project.clone();
        changed.production_design.capsules[0]
            .property_fidelity
            .push(NativePropertyFidelity {
                path: "camera.zoom".into(),
                fidelity: Fidelity::Native,
                loss_note: None,
            });
        assert_eq!(RevisionStamp::from(&changed), selected.stamp);
        let current = select_current_attachment(&changed, &request).unwrap();
        assert!(require_same_attachment(&selected, &current).is_err());
    }

    #[test]
    fn attachment_rechecks_asset_metadata_even_at_the_same_revision() {
        let (project, request) = source_project(b"BLENDER-v400 fixture");
        let selected = select_current_attachment(&project, &request).unwrap();
        let mut changed = project.clone();
        changed.assets[0].media_type = "application/octet-stream".into();
        let current = select_current_attachment(&changed, &request).unwrap();
        assert!(require_same_attachment(&selected, &current).is_err());
    }

    #[test]
    fn attachment_rejects_foreign_scene_or_source_digest() {
        let (project, request) = source_project(b"BLENDER-v400 fixture");
        let mut changed = project.clone();
        changed.production_design.capsules[0].scene_id = Uuid::now_v7();
        assert!(select_current_attachment(&changed, &request).is_err());
        changed = project;
        changed.assets[0].content_sha256 = Some("a".repeat(64));
        assert!(select_current_attachment(&changed, &request).is_err());
    }

    #[test]
    fn attachment_reads_original_cas_after_import_path_is_removed_and_after_reopen() {
        let (folder, service, project, request, bytes) = stored_source();
        let observed = inspect_from_service(&service, &request).unwrap();
        assert_eq!(observed.project_resource, project.resource_key());
        assert_eq!(observed.project_generation, project.generation);
        assert_eq!(observed.project_revision, project.revision);
        assert_eq!(observed.observation.source_format, "blender_project_opaque");
        assert!(!observed.observation.semantically_editable_by_motionwright);
        assert!(!observed.observation.runtime_or_imported_code_executed);
        assert!(!observed.project_changed && !observed.owner_source_bytes_exported);
        assert!(!observed.owner_runtime_granted && !observed.source_semantically_imported);
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(
                    &project.production_design.capsules[0].source_sha256,
                    MAX_ATTACHED_INSPECTION_BYTES as u64,
                )
                .unwrap(),
            bytes
        );
        drop(service);
        let reopened = StudioService::open(folder.0.join("project.sqlite3")).unwrap();
        let again = inspect_from_service(&reopened, &request).unwrap();
        assert_eq!(observed.observation, again.observation);
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[test]
    fn attachment_rejects_physical_cas_tamper_and_missing_source_without_changing_project() {
        let (_folder, service, project, request, _) = stored_source();
        let digest = &project.production_design.capsules[0].source_sha256;
        // Only the fixture corrupts its own CAS file. The command accepts no path.
        let physical = service.store().lock().verified_blob_path(digest).unwrap();
        fs::write(&physical, b"BLENDER-v400 tampered bytes").unwrap();
        let error = inspect_from_service(&service, &request).unwrap_err();
        assert!(error.contains("absent, corrupt or exceeds"));
        assert!(!error.contains(physical.to_string_lossy().as_ref()));
        assert_eq!(service.project(project.id).unwrap(), project);
        fs::remove_file(physical).unwrap();
        assert!(inspect_from_service(&service, &request).is_err());
        assert_eq!(service.project(project.id).unwrap(), project);
    }
}
