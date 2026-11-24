//! Owner-facing Manim V3 inert source round trip. The existing trusted compiler
//! and CAS inspector own all source interpretation; no imported code runs.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use super::project_export::absolute_bundle_path;
use motionwright_domain::{Project, RevisionStamp};
use motionwright_native::manim_editable_projection::{
    EditableProperty, PreviewedEditableEdit, export_saved_manim_editable_source_bundle,
    inspect_saved_manim_editable_source, preview_saved_manim_editable_edit,
};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{Read, Seek, SeekFrom, Write};
use uuid::Uuid;

const SOURCE_SCHEMA: &str = "motionwright.manim-editable-source/1";
const MIME: &str = "application/vnd.motionwright.manim-source+zip";
const MAX_BUNDLE: usize = 34 * 1024 * 1024;
const STALE: &str =
    "The saved Manim scene or delivery profile changed. Inspect the current source again.";

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub active_branch: Uuid,
    pub scene_id: Uuid,
    pub deliverable_id: Uuid,
}

#[derive(Debug, Serialize)]
pub struct SourceSnapshot {
    pub schema: &'static str,
    pub source: SourceRequest,
    pub bundle_sha256: String,
    pub size_bytes: String,
    pub media_type: &'static str,
    pub native_execution_performed: bool,
    pub project_modified: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportRequest {
    pub source: SourceRequest,
    pub expected_bundle_sha256: String,
    pub path: String,
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct ExportResponse {
    pub schema: &'static str,
    pub source: SourceRequest,
    pub path: String,
    pub bundle_sha256: String,
    pub size_bytes: String,
    pub media_type: &'static str,
    pub native_execution_performed: bool,
    pub project_modified: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AttachedRequest {
    pub source: SourceRequest,
    pub asset_id: Uuid,
    pub expected_sha256: String,
}

#[derive(Debug, Serialize)]
pub struct AttachedResponse {
    pub schema: &'static str,
    pub source: SourceRequest,
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub original_plan_sha256: String,
    pub current_plan_sha256: String,
    pub origin_revision: u64,
    pub revision_only_rebound: bool,
    pub node_ids: Vec<Uuid>,
    pub editable_parameters: Vec<&'static str>,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EditRequest {
    pub source: SourceRequest,
    pub original_asset_id: Uuid,
    pub original_sha256: String,
    pub edited_asset_id: Uuid,
    pub edited_sha256: String,
    pub node_id: Uuid,
    pub property: EditableProperty,
}

#[derive(Debug, Serialize)]
pub struct EditResponse {
    pub schema: &'static str,
    pub request: EditRequest,
    pub preview: PreviewedEditableEdit,
}

fn saved_project(service: &StudioService, request: &SourceRequest) -> Result<Project, String> {
    if [
        request.project_id,
        request.generation,
        request.active_branch,
        request.scene_id,
        request.deliverable_id,
    ]
    .iter()
    .any(Uuid::is_nil)
    {
        return Err("Choose a saved Manim scene and output profile.".into());
    }
    let project = service
        .project(request.project_id)
        .map_err(|_| "The saved project is unavailable.".to_string())?;
    if project.generation != request.generation
        || project.revision != request.revision
        || project.active_branch != request.active_branch
    {
        return Err(STALE.into());
    }
    Ok(project)
}

fn require_current(service: &StudioService, project: &Project) -> Result<(), String> {
    if service.project(project.id).map_err(|_| STALE.to_string())? != *project {
        return Err(STALE.into());
    }
    Ok(())
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn valid_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn verify_asset(project: &Project, id: Uuid, digest: &str) -> Result<(), String> {
    if id.is_nil()
        || !valid_sha(digest)
        || !project.assets.iter().any(|asset| {
            asset.id == id
                && asset.media_type == MIME
                && asset.content_sha256.as_deref() == Some(digest)
        })
    {
        return Err("Choose the exact attached Manim editable package and digest.".into());
    }
    Ok(())
}

fn compile(
    service: &StudioService,
    project: &Project,
    source: &SourceRequest,
) -> Result<Vec<u8>, String> {
    let bytes = export_saved_manim_editable_source_bundle(
        service,
        project.id,
        &RevisionStamp::from(project),
        source.scene_id,
        source.deliverable_id,
    )
    .map_err(|error| error.message)?;
    if bytes.is_empty() || bytes.len() > MAX_BUNDLE {
        return Err("The generated Manim bundle exceeds its bounded source budget.".into());
    }
    require_current(service, project)?;
    Ok(bytes)
}

pub fn inspect_source(
    service: &StudioService,
    source: SourceRequest,
) -> Result<SourceSnapshot, String> {
    let project = saved_project(service, &source)?;
    let bytes = compile(service, &project, &source)?;
    Ok(SourceSnapshot {
        schema: SOURCE_SCHEMA,
        source,
        bundle_sha256: sha(&bytes),
        size_bytes: bytes.len().to_string(),
        media_type: MIME,
        native_execution_performed: false,
        project_modified: false,
    })
}

fn export_subject(request: &ExportRequest) -> String {
    format!(
        "manim-editable-source|{}|{}|{}|{}|{}",
        request.source.active_branch,
        request.source.scene_id,
        request.source.deliverable_id,
        request.expected_bundle_sha256,
        request.path
    )
}

pub fn export_source(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ExportRequest,
) -> Result<ExportResponse, String> {
    let project = saved_project(service, &request.source)?;
    let bytes = compile(service, &project, &request.source)?;
    let digest = sha(&bytes);
    if digest != request.expected_bundle_sha256 {
        return Err(STALE.into());
    }
    if request.path.trim() != request.path || request.path.len() > 1600 {
        return Err("Choose one absolute .mwedit.zip destination.".into());
    }
    let destination = absolute_bundle_path(&request.path)?;
    if destination
        .file_name()
        .and_then(|name| name.to_str())
        .is_none_or(|name| !name.ends_with(".mwedit.zip"))
        || destination.parent().is_none_or(|parent| !parent.is_dir())
    {
        return Err("Choose a new .mwedit.zip file in an existing absolute directory.".into());
    }
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        EffectScope::project(project.id, project.generation, project.revision),
        &export_subject(&request),
    )?;
    require_current(service, &project)?;
    let parent = destination
        .parent()
        .ok_or("Manim source destination has no parent.")?;
    let mut staging = tempfile::Builder::new()
        .prefix(".motionwright-manim-source-")
        .tempfile_in(parent)
        .map_err(|_| "Could not prepare the Manim source file.".to_string())?;
    let file = staging.as_file_mut();
    file.write_all(&bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "Could not finalize the staged Manim source.".to_string())?;
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "Could not verify staged Manim bytes.".to_string())?;
    let mut observed = Vec::new();
    file.take(MAX_BUNDLE as u64 + 1)
        .read_to_end(&mut observed)
        .map_err(|_| "Could not read staged Manim bytes.".to_string())?;
    if observed != bytes {
        return Err("The staged Manim source differs from the inspected package.".into());
    }
    require_current(service, &project)?;
    staging.persist_noclobber(&destination).map_err(|error| {
        if error.error.kind() == std::io::ErrorKind::AlreadyExists {
            "Destination already exists. The original file was not overwritten.".to_string()
        } else {
            "Manim source publication could not be confirmed. Inspect the original destination."
                .to_string()
        }
    })?;
    Ok(ExportResponse {
        schema: "motionwright.manim-editable-source-export/1",
        source: request.source,
        path: request.path,
        bundle_sha256: digest,
        size_bytes: bytes.len().to_string(),
        media_type: MIME,
        native_execution_performed: false,
        project_modified: false,
    })
}

pub fn inspect_attached(
    service: &StudioService,
    request: AttachedRequest,
) -> Result<AttachedResponse, String> {
    let project = saved_project(service, &request.source)?;
    verify_asset(&project, request.asset_id, &request.expected_sha256)?;
    let read = inspect_saved_manim_editable_source(
        service,
        project.id,
        &RevisionStamp::from(&project),
        request.asset_id,
    )
    .map_err(|error| error.message)?;
    if read.current_binding.scene_id != request.source.scene_id
        || read.current_binding.deliverable_id != request.source.deliverable_id
        || read.source_sha256 != request.expected_sha256
        || read.source_asset_id != request.asset_id
        || read.native_execution_performed
        || read.imported_source_authority_used
    {
        return Err(
            "This attached package does not describe the selected saved scene and profile.".into(),
        );
    }
    require_current(service, &project)?;
    Ok(AttachedResponse {
        schema: "motionwright.manim-editable-source-attached/1",
        source: request.source,
        asset_id: read.source_asset_id,
        source_sha256: read.source_sha256,
        original_plan_sha256: read.original_closure.plan_sha256,
        current_plan_sha256: read.current_closure.plan_sha256,
        origin_revision: read.origin_binding.revision,
        revision_only_rebound: read.revision_only_rebound,
        node_ids: read
            .current_plan
            .nodes
            .iter()
            .map(|node| node.node_id)
            .collect(),
        editable_parameters: read.editable_parameters,
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

/// The result is one ordinary human-reviewable Change. Imported Python is
/// compared to trusted compiler output as bytes, never launched or authorized.
pub fn preview_edit(service: &StudioService, request: EditRequest) -> Result<EditResponse, String> {
    let project = saved_project(service, &request.source)?;
    verify_asset(
        &project,
        request.original_asset_id,
        &request.original_sha256,
    )?;
    verify_asset(&project, request.edited_asset_id, &request.edited_sha256)?;
    if request.original_asset_id == request.edited_asset_id || request.node_id.is_nil() {
        return Err("Choose a separate edited package and an original node.".into());
    }
    let original = inspect_attached(
        service,
        AttachedRequest {
            source: request.source.clone(),
            asset_id: request.original_asset_id,
            expected_sha256: request.original_sha256.clone(),
        },
    )?;
    if !original.node_ids.contains(&request.node_id) {
        return Err("The node is not part of the inspected original package.".into());
    }
    let preview = preview_saved_manim_editable_edit(
        service,
        project.id,
        &RevisionStamp::from(&project),
        request.original_asset_id,
        request.edited_asset_id,
        request.node_id,
        request.property,
    )
    .map_err(|error| error.message)?;
    if preview.original_source_sha256 != request.original_sha256
        || preview.edited_source_sha256 != request.edited_sha256
        || preview.current_plan_sha256 != original.current_plan_sha256
        || preview.native_execution_performed
        || preview.imported_source_authority_used
    {
        return Err(STALE.into());
    }
    require_current(service, &project)?;
    Ok(EditResponse {
        schema: "motionwright.manim-editable-source-edit/1",
        request,
        preview,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        CanvasNode, CanvasTransform, Change, CoordinateSpace, NodeStyle, RationalTime,
        RendererKind, VisualToken,
    };
    use std::collections::BTreeSet;
    use std::path::Path;

    fn apply(service: &StudioService, project: &Project, change: Change) -> Project {
        service
            .apply(
                project.id,
                &RevisionStamp::from(project),
                &format!("manim-exchange-{}", Uuid::now_v7()),
                &change,
            )
            .unwrap()
            .project
    }
    fn selection(project: &Project, scene_id: Uuid) -> SourceRequest {
        SourceRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            active_branch: project.active_branch,
            scene_id,
            deliverable_id: project.deliverables[0].id,
        }
    }
    fn fixture(service: &StudioService) -> (Project, Uuid, Uuid) {
        let mut project = service
            .create_project("Manim source exchange fixture")
            .unwrap();
        project = apply(
            service,
            &project,
            Change::AddScene {
                name: "Original shapes".into(),
                objective: "Editable Manim source".into(),
                duration_seconds: 4,
            },
        );
        let scene_id = project.scenes.last().unwrap().id;
        project = apply(
            service,
            &project,
            Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::ManimCommunity,
            },
        );
        let mut language = project.visual_language.clone();
        language
            .palette
            .retain(|row| !row.name.eq_ignore_ascii_case("background"));
        language.palette.push(VisualToken {
            name: "background".into(),
            value: "#153047".into(),
        });
        project = apply(
            service,
            &project,
            Change::SetVisualLanguage {
                visual_language: language,
            },
        );
        let node_id = Uuid::now_v7();
        project = apply(
            service,
            &project,
            Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id: node_id,
                    name: "Editable rectangle".into(),
                    kind: "rectangle".into(),
                    parent_id: None,
                    x: 600.0,
                    y: 400.0,
                    width: 320.0,
                    height: 160.0,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                    text: None,
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: 0,
                    style: NodeStyle {
                        fill: Some("#EAC454".into()),
                        ..Default::default()
                    },
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                },
            },
        );
        let mut output = project.deliverables[0].clone();
        output.width = 960;
        output.height = 540;
        output.frame_rate = RationalTime::new(30, 1).unwrap();
        output.captions = false;
        output.burn_in_captions = false;
        project = apply(
            service,
            &project,
            Change::UpsertDeliverable { profile: output },
        );
        (project, scene_id, node_id)
    }
    fn export_original(
        service: &StudioService,
        project: &Project,
        scene_id: Uuid,
        path: &Path,
    ) -> ExportResponse {
        let source = selection(project, scene_id);
        let snapshot = inspect_source(service, source.clone()).unwrap();
        let grants = EffectGrantRegistry::default();
        let mut request = ExportRequest {
            source,
            expected_bundle_sha256: snapshot.bundle_sha256.clone(),
            path: path.to_str().unwrap().into(),
            effect_grant: Uuid::nil(),
        };
        request.effect_grant = grants
            .issue(
                EffectKind::DeliverLocal,
                EffectScope::project(project.id, project.generation, project.revision),
                &export_subject(&request),
            )
            .unwrap()
            .token;
        let response = export_source(service, &grants, request).unwrap();
        assert_eq!(sha(&std::fs::read(path).unwrap()), response.bundle_sha256);
        assert_eq!(response.size_bytes, snapshot.size_bytes);
        assert!(!response.native_execution_performed && !response.project_modified);
        response
    }
    fn import(
        service: &StudioService,
        project: &Project,
        path: &Path,
        name: &str,
    ) -> (Project, Uuid) {
        let next = service
            .import_asset_file(
                project.id,
                &RevisionStamp::from(project),
                name,
                path,
                name.into(),
                MIME.into(),
            )
            .unwrap()
            .project;
        let id = next.assets.iter().find(|a| a.name == name).unwrap().id;
        (next, id)
    }

    #[test]
    fn manim_source_exchange_original_bundle_preview_apply_and_reopen() {
        let root = tempfile::tempdir().unwrap();
        let db = root.path().join("source.sqlite3");
        let service = StudioService::open(&db).unwrap();
        let (mut project, scene_id, node_id) = fixture(&service);
        let original_path = root.path().join("original.mwedit.zip");
        let edited_path = root.path().join("edited.mwedit.zip");
        let original = export_original(&service, &project, scene_id, &original_path);
        assert_eq!(service.project(project.id).unwrap(), project);
        // Construct a separate compiler-produced carrier, not executed Python.
        // Restore the original source controls before attaching the two packages.
        let pose = CanvasTransform {
            x: 600.0,
            y: 400.0,
            width: 320.0,
            height: 160.0,
            rotation_deg: 0.0,
            opacity: 1.0,
        };
        let changed = CanvasTransform {
            x: 640.0,
            rotation_deg: 12.0,
            ..pose.clone()
        };
        project = apply(
            &service,
            &project,
            Change::TransformCanvasNode {
                scene_id,
                node_id,
                transform: changed.clone(),
            },
        );
        let edited = export_original(&service, &project, scene_id, &edited_path);
        project = apply(
            &service,
            &project,
            Change::TransformCanvasNode {
                scene_id,
                node_id,
                transform: pose,
            },
        );
        let (next, original_id) = import(&service, &project, &original_path, "original.mwedit.zip");
        let (next, edited_id) = import(&service, &next, &edited_path, "edited.mwedit.zip");
        project = next;
        let source = selection(&project, scene_id);
        let inspected = inspect_attached(
            &service,
            AttachedRequest {
                source: source.clone(),
                asset_id: original_id,
                expected_sha256: original.bundle_sha256.clone(),
            },
        )
        .unwrap();
        assert!(inspected.revision_only_rebound);
        assert_eq!(inspected.node_ids, vec![node_id]);
        let request = EditRequest {
            source,
            original_asset_id: original_id,
            original_sha256: original.bundle_sha256,
            edited_asset_id: edited_id,
            edited_sha256: edited.bundle_sha256,
            node_id,
            property: EditableProperty::Transform,
        };
        let preview = preview_edit(&service, request.clone()).unwrap();
        assert!(
            !preview.preview.native_execution_performed
                && !preview.preview.imported_source_authority_used
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        let mut invalid = request.clone();
        invalid.edited_sha256 = "0".repeat(64);
        assert!(preview_edit(&service, invalid).is_err());
        let mut invalid = request.clone();
        invalid.property = EditableProperty::Style;
        assert!(preview_edit(&service, invalid).is_err());
        let after = apply(&service, &project, preview.preview.change);
        assert_eq!(after.revision, project.revision + 1);
        assert_eq!(after.assets, project.assets);
        assert!(preview_edit(&service, request).is_err());
        drop(service);
        let reopened = StudioService::open(&db).unwrap();
        assert_eq!(reopened.project(after.id).unwrap(), after);
        let read = inspect_attached(
            &reopened,
            AttachedRequest {
                source: selection(&after, scene_id),
                asset_id: edited_id,
                expected_sha256: after
                    .assets
                    .iter()
                    .find(|a| a.id == edited_id)
                    .unwrap()
                    .content_sha256
                    .clone()
                    .unwrap(),
            },
        )
        .unwrap();
        assert_eq!(read.node_ids, vec![node_id]);
    }

    #[test]
    fn manim_source_export_never_overwrites_or_accepts_wrong_grant_or_revision() {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("source.sqlite3")).unwrap();
        let (project, scene_id, _) = fixture(&service);
        let source = selection(&project, scene_id);
        let snapshot = inspect_source(&service, source.clone()).unwrap();
        let grants = EffectGrantRegistry::default();
        let dest = root.path().join("retained.mwedit.zip");
        std::fs::write(&dest, b"original").unwrap();
        let mut request = ExportRequest {
            source,
            expected_bundle_sha256: snapshot.bundle_sha256,
            path: dest.to_str().unwrap().into(),
            effect_grant: Uuid::nil(),
        };
        let scope = EffectScope::project(project.id, project.generation, project.revision);
        request.effect_grant = grants
            .issue(EffectKind::DeliverLocal, scope, "wrong subject")
            .unwrap()
            .token;
        assert!(export_source(&service, &grants, request).is_err());
        let mut request = ExportRequest {
            source: selection(&project, scene_id),
            expected_bundle_sha256: inspect_source(&service, selection(&project, scene_id))
                .unwrap()
                .bundle_sha256,
            path: dest.to_str().unwrap().into(),
            effect_grant: Uuid::nil(),
        };
        request.effect_grant = grants
            .issue(EffectKind::DeliverLocal, scope, &export_subject(&request))
            .unwrap()
            .token;
        assert!(export_source(&service, &grants, request).is_err());
        assert_eq!(std::fs::read(&dest).unwrap(), b"original");
        let mut stale = selection(&project, scene_id);
        stale.revision += 1;
        assert!(inspect_source(&service, stale).is_err());
        assert_eq!(service.project(project.id).unwrap(), project);
    }
}
