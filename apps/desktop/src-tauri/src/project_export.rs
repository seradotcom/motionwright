//! Snapshot authority for existing local bundle and OTIO export commands.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use super::local_error::sanitized;
use super::project_library::{OpenProjectRequest, open_project};
use motionwright_domain::{Project, otio_interchange};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use std::{fs::OpenOptions, io::Write, path::PathBuf};
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
        .export_project_bundle(prepared.project.id, &destination)
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

pub fn export_otio(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ExportOtioRequest,
) -> Result<OtioExportResponse, String> {
    let prepared = prepare_project_export(service, grants, request)?;
    execute_otio_export(service, prepared)
}

fn execute_otio_export(
    service: &StudioService,
    prepared: PreparedProjectExport,
) -> Result<OtioExportResponse, String> {
    let project = service.project(prepared.project.id).map_err(sanitized)?;
    let interchange = otio_interchange(&project).map_err(sanitized)?;
    let destination = absolute_otio_path(&prepared.path)?;
    let parent = destination
        .parent()
        .ok_or_else(|| "OTIO export destination has no parent directory.".to_string())?;
    if !parent.is_dir() {
        return Err("OTIO export directory must already exist.".into());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "OTIO export destination already exists; Motionwright will not overwrite it."
                    .to_string()
            } else {
                "Motionwright could not create the OTIO export.".to_string()
            }
        })?;
    file.write_all(interchange.body.as_bytes())
        .map_err(|_| "Motionwright could not write the OTIO export.".to_string())?;
    file.sync_all()
        .map_err(|_| "Motionwright could not finalize the OTIO export.".to_string())?;
    Ok(OtioExportResponse {
        path: destination.display().to_string(),
        scene_count: interchange.scene_count,
        loss_report: interchange.loss_report,
    })
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
    use motionwright_domain::{Asset, Change};
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
        let inspected = f.service.inspect_project_bundle(&destination).unwrap();
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
}
