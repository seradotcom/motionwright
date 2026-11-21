//! Snapshot authority for existing local bundle and OTIO export commands.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use super::local_error::sanitized;
use super::project_library::{OpenProjectRequest, open_project};
use motionwright_domain::{Project, otio_interchange};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

#[derive(Debug, Deserialize)]
pub struct ProjectExportRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub path: String,
    pub effect_grant: Uuid,
}

pub type ExportBundleRequest = ProjectExportRequest;
pub type ExportOtioRequest = ProjectExportRequest;

#[derive(Debug, Deserialize)]
pub struct NativeSourceExportRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub path: String,
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct NativeSourceExportResponse {
    pub path: String,
    pub capsule_id: Uuid,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub size_bytes: String,
    pub editor_launched: bool,
}

#[derive(Debug, Serialize)]
pub struct BundleExportResponse {
    pub destination: String,
    pub blob_count: usize,
    pub total_blob_bytes: String,
}

#[derive(Debug, Serialize)]
pub struct OtioExportResponse {
    pub path: String,
    pub scene_count: usize,
    pub loss_report: Vec<String>,
}

// A local phase result, not an actor credential or a serialized retry token.
// Its project and destination come from the request whose grant was consumed.
struct PreparedProjectExport {
    project: Project,
    path: String,
}

fn prepare_project_export(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ProjectExportRequest,
) -> Result<PreparedProjectExport, String> {
    let project = open_project(
        service,
        OpenProjectRequest {
            project_id: request.project_id,
        },
    )
    .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Effect grant scope is stale; refresh the project before retrying.".into());
    }
    let scope = EffectScope::project(request.project_id, request.generation, request.revision);
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        scope,
        request.path.trim(),
    )?;
    Ok(PreparedProjectExport {
        project,
        path: request.path,
    })
}

pub fn export_project_bundle(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ExportBundleRequest,
) -> Result<BundleExportResponse, String> {
    let prepared = prepare_project_export(service, grants, request)?;
    execute_bundle_export(service, prepared)
}

fn execute_bundle_export(
    service: &StudioService,
    prepared: PreparedProjectExport,
) -> Result<BundleExportResponse, String> {
    let destination = absolute_bundle_path(&prepared.path)?;
    let manifest = service
        .export_project_bundle_at(prepared.project.id, &prepared.project.stamp(), &destination)
        .map_err(sanitized)?;
    let total_blob_bytes = manifest
        .blobs
        .iter()
        .map(|blob| blob.size_bytes)
        .sum::<u64>();
    Ok(BundleExportResponse {
        destination: destination.display().to_string(),
        blob_count: manifest.blobs.len(),
        total_blob_bytes: total_blob_bytes.to_string(),
    })
}

/// Export only the exact saved CAS source for one selected native capsule.
/// The deliver grant binds project/revision, capsule UUID and destination path.
/// This does not launch a native editor or treat imported bytes as executable.
pub fn export_native_capsule_source(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: NativeSourceExportRequest,
) -> Result<NativeSourceExportResponse, String> {
    if request.scene_id.is_nil() || request.capsule_id.is_nil() {
        return Err("Choose a saved native capsule and scene before exporting.".into());
    }
    let project = open_project(
        service,
        OpenProjectRequest {
            project_id: request.project_id,
        },
    )
    .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Native source export is stale; inspect the current saved project.".into());
    }
    let candidates: Vec<_> = project
        .production_design
        .capsules
        .iter()
        .filter(|item| item.id == request.capsule_id)
        .collect();
    if candidates.len() != 1
        || candidates[0].scene_id != request.scene_id
        || !project
            .scenes
            .iter()
            .any(|scene| scene.id == request.scene_id)
    {
        return Err("The selected saved native source is unavailable in this scene.".into());
    }
    let capsule = candidates[0];
    let assets: Vec<_> = project
        .assets
        .iter()
        .filter(|asset| asset.id == capsule.source_asset_id)
        .collect();
    if assets.len() != 1
        || assets[0].content_sha256.as_deref() != Some(capsule.source_sha256.as_str())
    {
        return Err(
            "The original native source asset is missing or has a different digest.".into(),
        );
    }
    let asset = assets[0];
    let destination = absolute_bundle_path(&request.path)?;
    if destination.file_name().is_none()
        || destination.parent().is_none_or(|parent| !parent.is_dir())
    {
        return Err(
            "Select an absolute native source file path within an existing directory.".into(),
        );
    }
    let source_extension = Path::new(asset.name.trim())
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let destination_extension = destination
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    if !source_extension.is_empty() && !source_extension.eq_ignore_ascii_case(destination_extension)
    {
        return Err(
            "Preserve the native source file extension for reopening in its editor.".into(),
        );
    }
    let subject = format!(
        "native-source|{}|{}",
        request.capsule_id,
        request.path.trim()
    );
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        &subject,
    )?;
    // A peer mutation after issue/consume cannot substitute a later source.
    if service.project(project.id).map_err(sanitized)? != project {
        return Err("The native source changed before export; no file was created.".into());
    }
    const MAX_NATIVE_SOURCE_EXPORT: u64 = 64 * 1024 * 1024;
    let bytes = service
        .store()
        .lock()
        .read_blob(&capsule.source_sha256, MAX_NATIVE_SOURCE_EXPORT)
        .map_err(sanitized)?;
    let observed = format!("{:x}", Sha256::digest(&bytes));
    if bytes.is_empty() || observed != capsule.source_sha256 {
        return Err("The retained native source bytes do not match the saved digest.".into());
    }
    let parent = destination
        .parent()
        .ok_or("Native source destination has no parent.")?;
    let mut staging = tempfile::Builder::new()
        .prefix(".motionwright-native-source-")
        .tempfile_in(parent)
        .map_err(|_| "Could not prepare the native source export file.".to_string())?;
    staging
        .as_file_mut()
        .write_all(&bytes)
        .map_err(|_| "Could not write the native source export.".to_string())?;
    staging
        .as_file_mut()
        .sync_all()
        .map_err(|_| "Could not finalize the native source export.".to_string())?;
    staging.persist_noclobber(&destination).map_err(|error| {
        if error.error.kind() == std::io::ErrorKind::AlreadyExists {
            "Destination already exists; the original file was not overwritten.".to_string()
        } else {
            "Could not publish the native source export.".to_string()
        }
    })?;
    Ok(NativeSourceExportResponse {
        path: destination.display().to_string(),
        capsule_id: capsule.id,
        source_asset_id: asset.id,
        source_sha256: observed,
        size_bytes: bytes.len().to_string(),
        editor_launched: false,
    })
}

pub fn export_otio(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ExportOtioRequest,
) -> Result<OtioExportResponse, String> {
    let prepared = prepare_project_export(service, grants, request)?;
    execute_otio_export(service, prepared)
}

fn execute_otio_export(
    _service: &StudioService,
    prepared: PreparedProjectExport,
) -> Result<OtioExportResponse, String> {
    let project = prepared.project;
    let interchange = otio_interchange(&project).map_err(sanitized)?;
    let destination = absolute_otio_path(&prepared.path)?;
    let parent = destination
        .parent()
        .ok_or_else(|| "OTIO export destination has no parent directory.".to_string())?;
    if !parent.is_dir() {
        return Err("OTIO export directory must already exist.".into());
    }
    publish_otio(&destination, interchange.body.as_bytes())?;

    Ok(OtioExportResponse {
        path: destination.display().to_string(),
        scene_count: interchange.scene_count,
        loss_report: interchange.loss_report,
    })
}

fn publish_otio(destination: &Path, body: &[u8]) -> Result<(), String> {
    publish_otio_with(destination, |file| {
        file.write_all(body)
            .map_err(|_| "Motionwright could not write the OTIO export.".to_string())?;
        file.sync_all()
            .map_err(|_| "Motionwright could not finalize the OTIO export.".to_string())
    })
}

fn otio_create_error(error: std::io::Error) -> String {
    if error.kind() == std::io::ErrorKind::AlreadyExists {
        "OTIO export destination already exists; Motionwright will not overwrite it.".to_string()
    } else {
        "Motionwright could not create the OTIO export.".to_string()
    }
}

fn publish_otio_with(
    destination: &Path,
    write_and_sync: impl FnOnce(&mut File) -> Result<(), String>,
) -> Result<(), String> {
    let parent = destination
        .parent()
        .ok_or_else(|| "OTIO export destination has no parent directory.".to_string())?;
    let mut staging = tempfile::Builder::new()
        .prefix(".motionwright-otio-")
        .tempfile_in(parent)
        .map_err(otio_create_error)?;
    write_and_sync(staging.as_file_mut())?;
    staging
        .persist_noclobber(destination)
        .map_err(|error| otio_create_error(error.error))?;
    Ok(())
}

fn absolute_otio_path(value: &str) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Choose an absolute OTIO export path.".into());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("OTIO export paths must be absolute.".into());
    }
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if extension != "otio" {
        return Err("OTIO export path must use the .otio extension.".into());
    }
    Ok(path)
}

pub fn absolute_bundle_path(value: &str) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Choose an absolute portable bundle path.".into());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("Portable bundle paths must be absolute.".into());
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Asset, Change, Fidelity, FidelityReport, NativeCapsule};
    use std::{collections::BTreeMap, fs, path::Path};
    use tempfile::{TempDir, tempdir};

    const CONTENT: &[u8] = b"immutable local export fixture bytes";

    struct Fixture {
        root: TempDir,
        database: PathBuf,
        service: StudioService,
        project: Project,
    }

    fn fixture() -> Fixture {
        let root = tempdir().unwrap();
        let database = root.path().join("state.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let created = service.create_project("Reviewed export").unwrap();
        let input = root.path().join("fixture.bin");
        fs::write(&input, CONTENT).unwrap();
        let blob = service.store().lock().ingest_blob_file(&input).unwrap();
        let project = service
            .apply(
                created.id,
                &created.stamp(),
                "add-fixture-asset",
                &Change::AddAsset {
                    asset: Asset {
                        id: Uuid::now_v7(),
                        name: "fixture.bin".into(),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(blob.sha256),
                        source_revision: Some("local-import".into()),
                    },
                },
            )
            .unwrap()
            .project;
        fs::create_dir(root.path().join("exports")).unwrap();
        Fixture {
            root,
            database,
            service,
            project,
        }
    }

    fn native_source_fixture(f: &Fixture) -> (Project, Uuid, Uuid) {
        let with_scene = f
            .service
            .apply(
                f.project.id,
                &f.project.stamp(),
                "source-export-scene",
                &Change::AddScene {
                    name: "Native source roundtrip".into(),
                    objective: "Recover exact original bytes".into(),
                    duration_seconds: 3,
                },
            )
            .unwrap()
            .project;
        let scene_id = with_scene.scenes.last().unwrap().id;
        let source = &with_scene.assets[0];
        let capsule = NativeCapsule {
            id: Uuid::now_v7(),
            scene_id,
            source_asset_id: source.id,
            source_sha256: source.content_sha256.clone().unwrap(),
            label: "Source, not a rendered claim".into(),
            native_editor_hint: "Open in the trusted native editor".into(),
            fidelity: FidelityReport {
                renderer: "unverified source".into(),
                renderer_version: "unverified".into(),
                visual: Fidelity::Unavailable,
                temporal: Fidelity::Unavailable,
                structural: Fidelity::Unavailable,
                editable: Fidelity::Unavailable,
                losses: vec!["No native realization or editor execution was verified.".into()],
                evidence_sha256: None,
            },
            editable_parameters: vec![],
            property_fidelity: vec![],
        };
        let capsule_id = capsule.id;
        let saved = f
            .service
            .apply(
                with_scene.id,
                &with_scene.stamp(),
                "source-export-capsule",
                &Change::UpsertNativeCapsule { capsule },
            )
            .unwrap()
            .project;
        (saved, scene_id, capsule_id)
    }

    fn native_request(
        project: &Project,
        scene_id: Uuid,
        capsule_id: Uuid,
        grants: &EffectGrantRegistry,
        path: &Path,
    ) -> NativeSourceExportRequest {
        let destination = path.to_str().unwrap().to_owned();
        let subject = format!("native-source|{}|{}", capsule_id, destination);
        let grant = grants
            .issue(
                EffectKind::DeliverLocal,
                EffectScope::project(project.id, project.generation, project.revision),
                &subject,
            )
            .unwrap();
        NativeSourceExportRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            capsule_id,
            path: destination,
            effect_grant: grant.token,
        }
    }

    fn request(f: &Fixture, grants: &EffectGrantRegistry, path: &Path) -> ProjectExportRequest {
        let path = path.to_str().unwrap().to_owned();
        let scope = EffectScope::project(f.project.id, f.project.generation, f.project.revision);
        let token = grants
            .issue(EffectKind::DeliverLocal, scope, &path)
            .unwrap()
            .token;
        ProjectExportRequest {
            project_id: f.project.id,
            generation: f.project.generation,
            revision: f.project.revision,
            path,
            effect_grant: token,
        }
    }

    fn consume_again(
        grants: &EffectGrantRegistry,
        project: &Project,
        path: &Path,
        token: Uuid,
    ) -> Result<(), String> {
        grants.consume(
            token,
            EffectKind::DeliverLocal,
            EffectScope::project(project.id, project.generation, project.revision),
            path.to_str().unwrap(),
        )
    }

    fn peer_edit(f: &Fixture) -> Project {
        let peer = StudioService::open(&f.database).unwrap();
        let changed = peer
            .apply(
                f.project.id,
                &f.project.stamp(),
                "peer-edit-after-export-grant",
                &Change::RenameProject {
                    title: "Unreviewed later export".into(),
                },
            )
            .unwrap()
            .project;
        assert_eq!(changed.generation, f.project.generation);
        assert_eq!(changed.revision, f.project.revision + 1);
        changed
    }

    fn files(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
        fn visit(root: &Path, path: &Path, out: &mut BTreeMap<PathBuf, Vec<u8>>) {
            for entry in fs::read_dir(path).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    visit(root, &path, out);
                } else {
                    out.insert(
                        path.strip_prefix(root).unwrap().to_owned(),
                        fs::read(path).unwrap(),
                    );
                }
            }
        }
        let mut out = BTreeMap::new();
        visit(root, root, &mut out);
        out
    }

    #[test]
    fn native_source_export_then_reimport_preserves_both_original_versions_and_reopens_store() {
        use super::super::file_import::{
            FileImportDispatchRequest, NativeCapsuleSourcePrepareRequest,
            import_native_capsule_source_file, prepare_native_capsule_source_file,
        };
        use motionwright_domain::NativeCapsuleSourceSpec;

        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let initial = f
            .service
            .apply(
                f.project.id,
                &f.project.stamp(),
                "first-native-source-scene",
                &Change::AddScene {
                    name: "Editable source roundtrip".into(),
                    objective: "Keep both genuine saved source versions".into(),
                    duration_seconds: 3,
                },
            )
            .unwrap()
            .project;
        let scene_id = initial.scenes.last().unwrap().id;
        let capsule_id = Uuid::now_v7();
        let source = f.root.path().join("editable-source.json");
        let original_bytes: &[u8] = br#"{"headline":"Original native source"}"#;
        let edited_bytes: &[u8] = br#"{"headline":"Changed in user's editor"}"#;
        fs::write(&source, original_bytes).unwrap();

        // Real first-party prepare, one-use import grant and SQLite/CAS commit;
        // only the external editor gesture is represented by test-owned bytes.
        let import = |project: &Project, path: &Path| -> Project {
            let spec = NativeCapsuleSourceSpec {
                name: "editable-source.json".into(),
                media_type: "application/json".into(),
                capsule_id,
                scene_id,
                label: "Editable authored native source".into(),
                editable_parameters: vec!["headline".into()],
                native_editor_hint: "Trusted editor, not launched by Motionwright".into(),
            };
            let prepared = prepare_native_capsule_source_file(
                &f.service,
                NativeCapsuleSourcePrepareRequest {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    path: path.to_str().unwrap().to_owned(),
                    spec,
                },
            )
            .unwrap();
            let grant = grants
                .issue(
                    EffectKind::ImportLocal,
                    EffectScope::project(project.id, project.generation, project.revision),
                    &prepared.path,
                )
                .unwrap();
            let receipt = import_native_capsule_source_file(
                &f.service,
                &grants,
                FileImportDispatchRequest {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: Uuid::new_v4().to_string(),
                    effect_grant: grant.token,
                    path: prepared.path,
                    intent: prepared.intent,
                },
            )
            .unwrap();
            assert!(!receipt.replayed);
            assert_eq!(receipt.project.revision, project.revision + 1);
            receipt.project
        };
        let first = import(&initial, &source);
        let first_capsule = &first.production_design.capsules[0];
        let first_digest = first_capsule.source_sha256.clone();
        let original_asset_id = first_capsule.source_asset_id;
        let export_v1 = f.root.path().join("exports/recovered-first.json");
        let receipt_v1 = export_native_capsule_source(
            &f.service,
            &grants,
            native_request(&first, scene_id, capsule_id, &grants, &export_v1),
        )
        .unwrap();
        assert_eq!(receipt_v1.source_sha256, first_digest);
        assert_eq!(fs::read(&export_v1).unwrap().as_slice(), original_bytes);

        // User edits only the recovered copy, then explicitly saves its new
        // version with the same capsule ID and its original CAS blob untouched.
        fs::write(&export_v1, edited_bytes).unwrap();
        let second = import(&first, &export_v1);
        assert_eq!(second.id, first.id);
        assert_eq!(second.generation, first.generation);
        assert_eq!(second.assets.len(), first.assets.len() + 1);
        assert_eq!(second.production_design.capsules.len(), 1);
        let second_capsule = &second.production_design.capsules[0];
        assert_eq!(second_capsule.id, capsule_id);
        assert_ne!(second_capsule.source_asset_id, original_asset_id);
        assert_ne!(second_capsule.source_sha256, first_digest);
        let export_v2 = f.root.path().join("exports/recovered-second.json");
        let receipt_v2 = export_native_capsule_source(
            &f.service,
            &grants,
            native_request(&second, scene_id, capsule_id, &grants, &export_v2),
        )
        .unwrap();
        assert_eq!(receipt_v2.source_sha256, second_capsule.source_sha256);
        assert_eq!(fs::read(&export_v2).unwrap().as_slice(), edited_bytes);

        // Close the original writer and its import closure before an entirely
        // new service handle independently reopens the durable Store.
        drop(import);
        drop(f.service);
        let independent_reader = StudioService::open(&f.database).unwrap();
        assert_eq!(independent_reader.project(second.id).unwrap(), second);
        let store = independent_reader.store();
        assert_eq!(
            store
                .lock()
                .read_blob(&first_digest, 4096)
                .unwrap()
                .as_slice(),
            original_bytes
        );
        assert_eq!(
            store
                .lock()
                .read_blob(&second_capsule.source_sha256, 4096)
                .unwrap()
                .as_slice(),
            edited_bytes
        );
        assert_eq!(fs::read(&source).unwrap().as_slice(), original_bytes);
    }

    #[test]
    fn native_source_exports_exact_original_bytes_without_launch_or_replay() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let (saved, scene_id, capsule_id) = native_source_fixture(&f);
        let destination = f.root.path().join("exports/original-copy.bin");
        let request = native_request(&saved, scene_id, capsule_id, &grants, &destination);
        let token = request.effect_grant;
        let result = export_native_capsule_source(&f.service, &grants, request).unwrap();
        assert_eq!(fs::read(&destination).unwrap(), CONTENT);
        assert_eq!(result.path, destination.to_str().unwrap());
        assert_eq!(result.capsule_id, capsule_id);
        assert_eq!(result.source_asset_id, saved.assets[0].id);
        assert_eq!(
            result.source_sha256,
            saved.production_design.capsules[0].source_sha256
        );
        assert_eq!(result.size_bytes, CONTENT.len().to_string());
        assert!(!result.editor_launched);
        assert_eq!(files(&f.root.path().join("exports")).len(), 1);
        assert_eq!(f.service.project(saved.id).unwrap(), saved);
        let subject = format!("native-source|{}|{}", capsule_id, destination.display());
        assert!(
            grants
                .consume(
                    token,
                    EffectKind::DeliverLocal,
                    EffectScope::project(saved.id, saved.generation, saved.revision),
                    &subject
                )
                .is_err()
        );
    }

    #[test]
    fn native_source_export_cannot_overwrite_an_existing_file_or_switch_capsule() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let (saved, scene_id, capsule_id) = native_source_fixture(&f);
        let destination = f.root.path().join("exports/occupied.bin");
        fs::write(&destination, b"private foreign original").unwrap();
        let request = native_request(&saved, scene_id, capsule_id, &grants, &destination);
        assert!(export_native_capsule_source(&f.service, &grants, request).is_err());
        assert_eq!(fs::read(&destination).unwrap(), b"private foreign original");
        let elsewhere = f.root.path().join("exports/other.bin");
        let mut swapped = native_request(&saved, scene_id, capsule_id, &grants, &elsewhere);
        swapped.capsule_id = Uuid::new_v4();
        assert!(export_native_capsule_source(&f.service, &grants, swapped).is_err());
        assert!(!elsewhere.exists());
        let authorized = f.root.path().join("exports/authorized.bin");
        let changed_path = f.root.path().join("exports/ungranted.bin");
        let mut swapped_path = native_request(&saved, scene_id, capsule_id, &grants, &authorized);
        swapped_path.path = changed_path.to_str().unwrap().to_string();
        assert!(export_native_capsule_source(&f.service, &grants, swapped_path).is_err());
        assert!(!authorized.exists() && !changed_path.exists());
        let wrong_extension = f.root.path().join("exports/not-a-source.txt");
        let request = native_request(&saved, scene_id, capsule_id, &grants, &wrong_extension);
        assert!(export_native_capsule_source(&f.service, &grants, request).is_err());
        assert!(!wrong_extension.exists());
        assert_eq!(f.service.project(saved.id).unwrap(), saved);
    }

    #[test]
    fn native_source_export_rejects_a_peer_revision_before_creating_the_destination() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let (saved, scene_id, capsule_id) = native_source_fixture(&f);
        let destination = f.root.path().join("exports/stale.bin");
        let request = native_request(&saved, scene_id, capsule_id, &grants, &destination);
        let newer = f
            .service
            .apply(
                saved.id,
                &saved.stamp(),
                "peer-native-source-export",
                &Change::RenameProject {
                    title: "Newer owner revision".into(),
                },
            )
            .unwrap()
            .project;
        assert!(export_native_capsule_source(&f.service, &grants, request).is_err());
        assert!(!destination.exists());
        assert_eq!(f.service.project(saved.id).unwrap(), newer);
    }

    #[test]
    fn bundle_peer_revision_after_consumption_cannot_publish_a_later_snapshot() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let destination = f.root.path().join("exports/checked-bundle");
        let req = request(&f, &grants, &destination);
        let token = req.effect_grant;
        let prepared = prepare_project_export(&f.service, &grants, req).unwrap();
        assert_eq!(prepared.project, f.project);
        let changed = peer_edit(&f);
        let result = execute_bundle_export(&f.service, prepared);
        assert!(consume_again(&grants, &f.project, &destination, token).is_err());
        assert_eq!(f.service.project(f.project.id).unwrap(), changed);
        assert!(
            matches!(&result, Err(error) if error.contains("stale project base")),
            "bundle must reject the later snapshot using the retained revision"
        );
        assert!(
            !destination.exists(),
            "rejected export must not create a bundle"
        );
    }

    #[test]
    fn otio_peer_revision_after_consumption_never_replaces_the_captured_project() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let destination = f.root.path().join("exports/checked.otio");
        let req = request(&f, &grants, &destination);
        let token = req.effect_grant;
        let prepared = prepare_project_export(&f.service, &grants, req).unwrap();
        let expected = otio_interchange(&f.project).unwrap();
        let changed = peer_edit(&f);
        assert_ne!(expected.body, otio_interchange(&changed).unwrap().body);
        let response = execute_otio_export(&f.service, prepared).unwrap();
        assert!(consume_again(&grants, &f.project, &destination, token).is_err());
        assert_eq!(f.service.project(f.project.id).unwrap(), changed);
        assert_eq!(response.path, destination.to_str().unwrap());
        assert!(
            fs::read(&destination).unwrap() == expected.body.as_bytes(),
            "OTIO must serialize the captured project, never a post-grant reload"
        );
    }

    #[test]
    fn exact_bundle_command_exports_verified_project_journal_and_original_blob() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let destination = f.root.path().join("exports/exact-bundle");
        let req = request(&f, &grants, &destination);
        let token = req.effect_grant;
        let response = export_project_bundle(&f.service, &grants, req).unwrap();
        assert_eq!(response.destination, destination.to_str().unwrap());
        assert_eq!(response.blob_count, 1);
        assert_eq!(response.total_blob_bytes, CONTENT.len().to_string());
        let manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(destination.join("manifest.json")).unwrap()).unwrap();
        assert_eq!(
            manifest["backup"]["project"],
            serde_json::to_value(&f.project).unwrap()
        );
        assert_eq!(manifest["backup"]["events"].as_array().unwrap().len(), 1);
        assert!(files(&destination).values().any(|bytes| bytes == CONTENT));
        let inspection = StudioService::open(f.root.path().join("inspection.sqlite3")).unwrap();
        let inspected = inspection.inspect_project_bundle(&destination).unwrap();
        assert_eq!(inspected.blob_count, 1);
        assert_eq!(inspected.project.project_id, f.project.id);
        assert_eq!(inspected.project.revision, f.project.revision);
        assert!(consume_again(&grants, &f.project, &destination, token).is_err());
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
    }

    #[test]
    fn exact_otio_command_preserves_the_existing_response_and_document() {
        let f = fixture();
        let grants = EffectGrantRegistry::default();
        let destination = f.root.path().join("exports/exact.otio");
        let req = request(&f, &grants, &destination);
        let token = req.effect_grant;
        let expected = otio_interchange(&f.project).unwrap();
        let response = export_otio(&f.service, &grants, req).unwrap();
        assert_eq!(response.path, destination.to_str().unwrap());
        assert_eq!(response.scene_count, expected.scene_count);
        assert_eq!(response.loss_report, expected.loss_report);
        assert_eq!(fs::read(&destination).unwrap(), expected.body.as_bytes());
        let document: serde_json::Value =
            serde_json::from_slice(&fs::read(&destination).unwrap()).unwrap();
        assert_eq!(
            document["metadata"]["motionwright"]["project_id"],
            f.project.id.to_string()
        );
        assert_eq!(
            document["metadata"]["motionwright"]["generation"],
            f.project.generation.to_string()
        );
        assert_eq!(
            document["metadata"]["motionwright"]["revision"],
            f.project.revision
        );
        assert!(consume_again(&grants, &f.project, &destination, token).is_err());
    }

    #[test]
    fn occupied_destinations_preserve_foreign_files_and_consume_only_the_original_grant() {
        for bundle in [true, false] {
            let f = fixture();
            let grants = EffectGrantRegistry::default();
            let area = f.root.path().join("exports");
            let destination = area.join(if bundle {
                "occupied-bundle"
            } else {
                "occupied.otio"
            });
            if bundle {
                fs::create_dir(&destination).unwrap();
                fs::write(
                    destination.join("foreign.txt"),
                    b"preserve foreign bundle directory",
                )
                .unwrap();
            } else {
                fs::write(&destination, b"preserve foreign OTIO bytes").unwrap();
            }
            let before = files(&area);
            let req = request(&f, &grants, &destination);
            let token = req.effect_grant;
            let rejected = if bundle {
                export_project_bundle(&f.service, &grants, req).is_err()
            } else {
                export_otio(&f.service, &grants, req).is_err()
            };
            assert!(rejected);
            assert!(files(&area) == before, "foreign destination bytes changed");
            assert!(consume_again(&grants, &f.project, &destination, token).is_err());
            assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        }
    }

    #[test]
    fn changed_destination_subject_is_rejected_before_any_export() {
        for bundle in [true, false] {
            let f = fixture();
            let grants = EffectGrantRegistry::default();
            let area = f.root.path().join("exports");
            let original = area.join(if bundle {
                "authorized-bundle"
            } else {
                "authorized.otio"
            });
            let different = area.join(if bundle {
                "different-bundle"
            } else {
                "different.otio"
            });
            let before = files(&area);
            let mut req = request(&f, &grants, &original);
            let token = req.effect_grant;
            req.path = different.to_str().unwrap().to_owned();
            let result = prepare_project_export(&f.service, &grants, req);
            assert!(result.is_err());
            assert!(files(&area) == before);
            assert!(consume_again(&grants, &f.project, &original, token).is_err());
            assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        }
    }

    fn interrupted_publication(finalization: bool) {
        let f = fixture();
        let area = f.root.path().join("exports");
        let destination = area.join("interrupted.otio");
        fs::write(area.join("foreign.txt"), b"untouched foreign export").unwrap();
        let before = files(&area);
        let body = otio_interchange(&f.project).unwrap().body;
        assert!(body.len() > 7);
        let mut visible_during_write = None;
        let result = publish_otio_with(&destination, |file| {
            let bytes = if finalization {
                body.as_bytes()
            } else {
                &body.as_bytes()[..7]
            };
            file.write_all(bytes).unwrap();
            visible_during_write = fs::read(&destination).ok();
            Err(if finalization {
                "injected finalization failure after complete real write".to_string()
            } else {
                "injected write failure after a real prefix".to_string()
            })
        });
        assert!(result.is_err());
        assert!(
            visible_during_write.is_none(),
            "unconfirmed OTIO bytes were visible before publication"
        );
        assert!(
            !destination.exists(),
            "failed OTIO export remained published"
        );
        assert_eq!(
            files(&area),
            before,
            "foreign bytes changed or staging leaked"
        );
    }

    #[test]
    fn failed_otio_write_never_exposes_or_retains_a_partial_destination() {
        interrupted_publication(false);
    }

    #[test]
    fn failed_otio_finalization_never_exposes_or_retains_unconfirmed_bytes() {
        interrupted_publication(true);
    }

    #[test]
    fn late_otio_competitor_is_preserved_and_staging_is_removed() {
        let f = fixture();
        let area = f.root.path().join("exports");
        let destination = area.join("contended.otio");
        fs::write(area.join("foreign.txt"), b"untouched foreign export").unwrap();
        let losing_body = otio_interchange(&f.project).unwrap().body;
        let winner_project = peer_edit(&f);
        let winner_body = otio_interchange(&winner_project).unwrap().body;
        assert_ne!(losing_body, winner_body);
        let mut expected = files(&area);
        expected.insert(
            PathBuf::from("contended.otio"),
            winner_body.as_bytes().to_vec(),
        );
        let result = publish_otio_with(&destination, |staging| {
            staging.write_all(losing_body.as_bytes()).unwrap();
            staging.sync_all().unwrap();
            assert!(!destination.exists());
            let mut winner = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&destination)
                .unwrap();
            winner.write_all(winner_body.as_bytes()).unwrap();
            winner.sync_all().unwrap();
            drop(winner);
            Ok(())
        });
        assert_eq!(
            result,
            Err(otio_create_error(std::io::Error::from(
                std::io::ErrorKind::AlreadyExists
            )))
        );
        assert_eq!(files(&area), expected, "winner changed or staging leaked");
        assert_eq!(f.service.project(f.project.id).unwrap(), winner_project);
    }
}
