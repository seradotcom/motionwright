//! JSONL test transport for the existing StudioService, SQLite journal and CAS.
//!
//! This is an opt-in integration-test executable, never a production backend or
//! a Tauri/installer/native-renderer certification. It listens only on stdin.
//! Requests use `{id, command, args}` and responses `{id, ok, result|error}`.
//! Local imports, portable bundles, waveform and project changes preserve the
//! desktop invoke payloads and use the existing service and one-time grant
//! registry. Grants belong only to this process and are discarded on reopen.
//! `--desktop-invoke-fixture` opts into the desktop UI routes in browser tests;
//! the response still identifies service-test-jsonl, never installed Tauri.
//! No render, remote, installation or publication authority is exposed.
//!
//! Run: `studio_review_session --database /absolute/test.sqlite3 --synthetic-fixture`
//! The fixture flag creates explicitly synthetic editable content only when the
//! selected database is empty. Every scene, node and asset is committed through
//! StudioService; project IDs, revisions and event history are never canned.

#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
#[allow(dead_code)] // This transport deliberately exposes only the tested local subset.
mod effect_grants;

#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
#[allow(dead_code)] // The exact desktop read/creation module, without Tauri transport.
mod project_library;

use effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::{
    AlignmentEvidence, CanvasNode, Change, CreativePatch, CueEvidence, HeroConfig, NodeStyle,
    Project, RevisionStamp, hero_node_id,
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::{
    fs,
    io::{self, BufRead, Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

const FIXTURE_LABEL: &str = "synthetic_test_fixture";
const MAX_REQUEST_BYTES: usize = 4 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Invoke {
    id: Value,
    command: String,
    #[serde(default)]
    args: Value,
}

// Keep these names and field types identical to the desktop invoke boundary.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApplyRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    effect_grant: Uuid,
    change: Change,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GrantRequest {
    effect: EffectKind,
    project_id: Option<Uuid>,
    generation: Option<Uuid>,
    revision: Option<u64>,
    subject: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProjectRequest {
    project_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    patch: CreativePatch,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UndoRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    patch_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HistoryRequest {
    project_id: Uuid,
    after_revision: u64,
    limit: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecentHistoryRequest {
    project_id: Uuid,
    through_revision: u64,
    limit: usize,
}

// These request fields and defaults follow apps/desktop/src-tauri/src/main.rs
// at descendant base 101ac038775a6201bcb16f7ca056bfdd0ab05805. Only transport
// parsing and response shaping live here; StudioService owns every operation.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ImportAssetRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    effect_grant: Uuid,
    path: String,
    name: Option<String>,
    media_type: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ImportVoiceRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    effect_grant: Uuid,
    path: String,
    name: Option<String>,
    media_type: Option<String>,
    label: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WaveformPageRequest {
    project_id: Uuid,
    track_id: Uuid,
    page_index: u64,
    page_size: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BundlePathRequest {
    path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ImportBundleRequest {
    path: String,
    effect_grant: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExportBundleRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    path: String,
    effect_grant: Uuid,
}

fn encoded(value: impl Serialize) -> Result<Value, String> {
    serde_json::to_value(value).map_err(|error| error.to_string())
}

fn request<T: DeserializeOwned>(args: Value) -> Result<T, String> {
    let value = args
        .get("request")
        .ok_or_else(|| "Expected the desktop invoke args.request payload".to_string())?;
    serde_json::from_value(value.clone()).map_err(|error| error.to_string())
}

fn absolute_asset_path(value: &str) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Choose an absolute asset file path.".into());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("Asset file paths must be absolute.".into());
    }
    Ok(path)
}

fn absolute_bundle_path(value: &str) -> Result<PathBuf, String> {
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

fn infer_media_type(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "flac" => "audio/flac",
        "ogg" | "oga" => "audio/ogg",
        "m4a" | "aac" => "audio/mp4",
        "aif" | "aiff" => "audio/aiff",
        "caf" => "audio/x-caf",
        "mp4" => "video/mp4",
        "mov" => "video/quicktime",
        "webm" => "video/webm",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "glb" => "model/gltf-binary",
        "gltf" => "model/gltf+json",
        "json" => "application/json",
        "srt" => "application/x-subrip",
        "vtt" => "text/vtt",
        "txt" => "text/plain",
        _ => "application/octet-stream",
    }
}

fn import_name(source: &Path, name: Option<&str>, kind: &str) -> Result<String, String> {
    name.map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            source
                .file_name()
                .and_then(|value| value.to_str())
                .map(str::to_owned)
        })
        .ok_or_else(|| format!("{kind} file name is unavailable."))
}

fn fixture_node(text: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Independent synthetic note".into(),
        kind: "text".into(),
        parent_id: None,
        x: 120.0,
        y: 160.0,
        width: 720.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some(text.into()),
        coordinate_space: Default::default(),
        z_index: 1,
        style: NodeStyle {
            fill: Some("#F2F4F3".into()),
            font_family: Some("Inter".into()),
            font_size: Some(36.0),
            ..Default::default()
        },
        relations: Vec::new(),
        property_locks: Default::default(),
        keyframes: Vec::new(),
    }
}

fn seed(service: &StudioService, directory: &Path) -> Result<(), String> {
    let mut project = service
        .create_project("Synthetic review fixture — editable preservation study")
        .map_err(|error| error.to_string())?;
    let mut apply = |change: Change| -> Result<(), String> {
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &Uuid::now_v7().to_string(),
                &change,
            )
            .map_err(|error| error.to_string())?
            .project;
        Ok(())
    };
    apply(Change::AddScene {
        name: "Synthetic review scene".into(),
        objective: "Exercise scoped edits and preservation with synthetic content".into(),
        duration_seconds: 6,
    })?;
    apply(Change::AddScene {
        name: "Independent synthetic scene".into(),
        objective: "Retain unrelated author work through review and reopen".into(),
        duration_seconds: 4,
    })?;
    let scene_id = project.scenes[0].id;
    let other_scene_id = project.scenes[1].id;
    for change in [
        Change::UpsertProductHero {
            instance_id: Uuid::now_v7(),
            scene_id,
            config: HeroConfig {
                eyebrow: "SYNTHETIC TEST FIXTURE".into(),
                headline: "A deliberate revision.".into(),
                body: "This editable synthetic copy is used to verify preservation.".into(),
                wordmark: "TEST".into(),
                motion: false,
                ..HeroConfig::default()
            },
        },
        Change::AddCanvasNode {
            scene_id: other_scene_id,
            node: fixture_node("Independent work must survive every scoped revision."),
        },
    ] {
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &Uuid::now_v7().to_string(),
                &change,
            )
            .map_err(|error| error.to_string())?
            .project;
    }
    let source = directory.join(format!("synthetic-review-{}.svg", Uuid::now_v7()));
    fs::write(
        &source,
        br##"<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><title>Synthetic test fixture</title><rect width="64" height="64" fill="#A5C8DF"/></svg>"##,
    )
    .map_err(|error| error.to_string())?;
    let imported = service.import_asset_file(
        project.id,
        &project.stamp(),
        &Uuid::now_v7().to_string(),
        &source,
        "Synthetic source — persistence fixture".into(),
        "image/svg+xml".into(),
    );
    // The source is owned by this fixture. Its CAS copy must stand on its own.
    fs::remove_file(&source).map_err(|error| error.to_string())?;
    imported.map_err(|error| error.to_string())?;
    Ok(())
}

pub struct Session {
    database: PathBuf,
    service: StudioService,
    grants: EffectGrantRegistry,
    workspace_session: Uuid,
    desktop_invoke_fixture: bool,
}

impl Session {
    pub fn open(database: impl AsRef<Path>, synthetic_fixture: bool) -> Result<Self, String> {
        let database = database.as_ref().to_owned();
        if !database.is_absolute() {
            return Err("Choose an absolute, isolated test database path".into());
        }
        let directory = database
            .parent()
            .ok_or("Test database needs a parent directory")?;
        fs::create_dir_all(directory).map_err(|error| error.to_string())?;
        let service = StudioService::open(&database).map_err(|error| error.to_string())?;
        if synthetic_fixture
            && service
                .projects(1)
                .map_err(|error| error.to_string())?
                .is_empty()
        {
            seed(&service, directory)?;
        }
        Ok(Self {
            database,
            service,
            grants: EffectGrantRegistry::default(),
            workspace_session: Uuid::now_v7(),
            desktop_invoke_fixture: false,
        })
    }

    fn checked_project(
        &self,
        id: Uuid,
        generation: Uuid,
        revision: u64,
    ) -> Result<Project, String> {
        let project = self
            .service
            .project(id)
            .map_err(|error| error.to_string())?;
        if project.generation != generation || project.revision != revision {
            return Err("Effect grant scope is stale; refresh the project before retrying.".into());
        }
        Ok(project)
    }

    fn dispatch(&mut self, command: &str, args: Value) -> Result<Value, String> {
        match command {
            "bootstrap" => {
                let selection_request = match args.get("request") {
                    None | Some(Value::Null) => project_library::BootstrapRequest::default(),
                    Some(value) => {
                        serde_json::from_value(value.clone()).map_err(|error| error.to_string())?
                    }
                };
                let (project, project_selection) =
                    project_library::bootstrap_project(&self.service, selection_request);
                let hero = project
                    .as_ref()
                    .and_then(|project| project.production_design.heroes.first());
                let other = project.as_ref().and_then(|project| {
                    project
                        .scenes
                        .iter()
                        .find(|scene| scene.name == "Independent synthetic scene")
                });
                Ok(json!({
                    "project": project,
                    "project_selection": project_selection,
                    "native_sdk": {
                        "application": "motionwright",
                        "pinned_revision": "54d3459a051adbce4cae9f60c0dea8f60c824be6",
                        "version": "1.0.0",
                        // Selects UI routes for the explicit browser fixture;
                        // it is not a claim that this process hosts Tauri.
                        "mode": if self.desktop_invoke_fixture { "tauri" } else { "browser-demo" }
                    },
                    "test_transport": "service-test-jsonl",
                    "evidence_scope": "real_service_sqlite_cas; excludes_tauri_installer_native_render",
                    "desktop_invoke_fixture": self.desktop_invoke_fixture,
                    "fixture": {
                        "label": FIXTURE_LABEL,
                        "scene_id": hero.map(|item| item.scene_id),
                        "node_id": hero.map(|item| hero_node_id(item.id, "body")),
                        "hero_instance_id": hero.map(|item| item.id),
                        "other_scene_id": other.map(|scene| scene.id),
                        "other_node_id": other.and_then(|scene| scene.nodes.first().map(|node| node.id)),
                        "asset_sha256": project.as_ref().and_then(|project| project.assets.first()).and_then(|asset| asset.content_sha256.as_deref())
                    }
                }))
            }
            "list_projects" => encoded(project_library::list_projects(
                &self.service,
                request(args)?,
            )?),
            "open_project" => encoded(project_library::open_project(
                &self.service,
                request(args)?,
            )?),
            "issue_project_creation_grant" => encoded(project_library::issue_creation_grant(
                &self.grants,
                self.workspace_session,
                // Explicit test-transport window label; the desktop wrapper
                // instead supplies the actual injected WebviewWindow label.
                "service-test-window",
                &request(args)?,
            )?),
            "create_project" => encoded(project_library::create_project(
                &self.service,
                &self.grants,
                self.workspace_session,
                "service-test-window",
                request(args)?,
            )?),
            "recover_project_creation" => encoded(project_library::recover_project_creation(
                &self.service,
                &request(args)?,
            )?),
            "issue_effect_grant" => {
                let request: GrantRequest = request(args)?;
                if !matches!(
                    request.effect,
                    EffectKind::ProjectEdit | EffectKind::ImportLocal | EffectKind::DeliverLocal
                ) {
                    return Err("This test transport exposes project_edit, import_local and deliver_local grants only; no render or external authority".into());
                }
                let scope = match (request.project_id, request.generation, request.revision) {
                    (Some(id), Some(generation), Some(revision)) => {
                        self.checked_project(id, generation, revision)?;
                        EffectScope::project(id, generation, revision)
                    }
                    (None, None, None) => EffectScope::unscoped_import(),
                    _ => {
                        return Err("Effect grant project scope must be complete or absent.".into());
                    }
                };
                encoded(
                    self.grants
                        .issue(request.effect, scope, request.subject.trim())?,
                )
            }
            "apply_change" => {
                let request: ApplyRequest = request(args)?;
                let project =
                    self.checked_project(request.project_id, request.generation, request.revision)?;
                self.grants.consume(
                    request.effect_grant,
                    EffectKind::ProjectEdit,
                    EffectScope::project(request.project_id, request.generation, request.revision),
                    request.request_id.trim(),
                )?;
                let trusted_audio_evidence = match &request.change {
                    Change::ImportMeasuredVoice { .. } | Change::AddVoiceTrack { .. } => true,
                    Change::AddTranscriptSegment { segment }
                    | Change::UpsertTranscriptSegment { segment } => {
                        matches!(segment.alignment, AlignmentEvidence::Measured { .. })
                    }
                    Change::AddAudioCue { cue } | Change::UpsertAudioCue { cue } => matches!(
                        cue.evidence,
                        CueEvidence::Measured | CueEvidence::TranscriptAligned
                    ),
                    _ => false,
                };
                if trusted_audio_evidence {
                    return Err("Measured audio evidence can only enter through the qualified audio import/alignment boundary.".into());
                }
                let expected = RevisionStamp {
                    resource: project.resource_key(),
                    generation: request.generation,
                    revision: request.revision,
                };
                encoded(
                    self.service
                        .apply(
                            request.project_id,
                            &expected,
                            &request.request_id,
                            &request.change,
                        )
                        .map_err(|error| error.to_string())?
                        .project,
                )
            }
            "get_project" | "reopen" => {
                let request: ProjectRequest = request(args)?;
                if command == "reopen" {
                    // Replace the connection from persisted SQLite state and discard all grants.
                    let mut replacement = Self::open(&self.database, false)?;
                    replacement.desktop_invoke_fixture = self.desktop_invoke_fixture;
                    *self = replacement;
                }
                encoded(
                    self.service
                        .project(request.project_id)
                        .map_err(|error| error.to_string())?,
                )
            }
            "import_asset_file" => {
                let request: ImportAssetRequest = request(args)?;
                let source = absolute_asset_path(&request.path)?;
                let project =
                    self.checked_project(request.project_id, request.generation, request.revision)?;
                self.grants.consume(
                    request.effect_grant,
                    EffectKind::ImportLocal,
                    EffectScope::project(request.project_id, request.generation, request.revision),
                    request.path.trim(),
                )?;
                if !source.is_file() {
                    return Err("Asset source must be an existing local file.".into());
                }
                let name = import_name(&source, request.name.as_deref(), "Asset")?;
                let media_type = request
                    .media_type
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_owned)
                    .unwrap_or_else(|| infer_media_type(&source).to_owned());
                encoded(
                    self.service
                        .import_asset_file(
                            request.project_id,
                            &project.stamp(),
                            &Uuid::now_v7().to_string(),
                            &source,
                            name,
                            media_type,
                        )
                        .map_err(|error| error.to_string())?
                        .project,
                )
            }
            "import_voice_file" => {
                let request: ImportVoiceRequest = request(args)?;
                let source = absolute_asset_path(&request.path)?;
                let project =
                    self.checked_project(request.project_id, request.generation, request.revision)?;
                self.grants.consume(
                    request.effect_grant,
                    EffectKind::ImportLocal,
                    EffectScope::project(request.project_id, request.generation, request.revision),
                    request.path.trim(),
                )?;
                if !source.is_file() {
                    return Err("Voice source must be an existing local file.".into());
                }
                let name = import_name(&source, request.name.as_deref(), "Voice")?;
                let label = request
                    .label
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_owned)
                    .or_else(|| {
                        source
                            .file_stem()
                            .and_then(|value| value.to_str())
                            .map(str::to_owned)
                    })
                    .unwrap_or_else(|| "Voice take".into());
                let media_type = request
                    .media_type
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_owned)
                    .unwrap_or_else(|| infer_media_type(&source).to_owned());
                if !media_type.starts_with("audio/") {
                    return Err("Voice import requires a supported audio file.".into());
                }
                encoded(
                    self.service
                        .import_voice_file(
                            request.project_id,
                            &project.stamp(),
                            &Uuid::now_v7().to_string(),
                            &source,
                            VoiceImportMetadata {
                                name,
                                media_type,
                                label,
                            },
                        )
                        .map_err(|error| error.to_string())?
                        .project,
                )
            }
            "waveform_page" => {
                let request: WaveformPageRequest = request(args)?;
                encoded(
                    self.service
                        .waveform_page(
                            request.project_id,
                            request.track_id,
                            request.page_index,
                            request.page_size,
                        )
                        .map_err(|error| error.to_string())?,
                )
            }
            "export_project_bundle" => {
                let request: ExportBundleRequest = request(args)?;
                self.checked_project(request.project_id, request.generation, request.revision)?;
                self.grants.consume(
                    request.effect_grant,
                    EffectKind::DeliverLocal,
                    EffectScope::project(request.project_id, request.generation, request.revision),
                    request.path.trim(),
                )?;
                let destination = absolute_bundle_path(&request.path)?;
                let manifest = self
                    .service
                    .export_project_bundle(request.project_id, &destination)
                    .map_err(|error| error.to_string())?;
                Ok(json!({
                    "destination": destination.display().to_string(),
                    "blob_count": manifest.blobs.len(),
                    "total_blob_bytes": manifest.blobs.iter().map(|blob| blob.size_bytes).sum::<u64>().to_string(),
                }))
            }
            "inspect_project_bundle" => {
                let request: BundlePathRequest = request(args)?;
                let source = absolute_bundle_path(&request.path)?;
                let plan = self
                    .service
                    .inspect_project_bundle(&source)
                    .map_err(|error| error.to_string())?;
                Ok(json!({
                    "project_id": plan.project.project_id,
                    "title": plan.project.title,
                    "source_generation": plan.project.source_generation,
                    "revision": plan.project.revision.to_string(),
                    "event_count": plan.project.event_count,
                    "blob_count": plan.blob_count,
                    "total_blob_bytes": plan.total_blob_bytes.to_string(),
                    "rotates_generation": plan.project.rotates_generation,
                }))
            }
            "import_project_bundle" => {
                let request: ImportBundleRequest = request(args)?;
                self.grants.consume(
                    request.effect_grant,
                    EffectKind::ImportLocal,
                    EffectScope::unscoped_import(),
                    request.path.trim(),
                )?;
                let source = absolute_bundle_path(&request.path)?;
                encoded(
                    self.service
                        .import_project_bundle(&source)
                        .map_err(|error| error.to_string())?,
                )
            }
            "preview_creative_patch" => {
                let request: PatchRequest = request(args)?;
                let project =
                    self.checked_project(request.project_id, request.generation, request.revision)?;
                encoded(
                    project
                        .preview_creative_patch(&request.patch)
                        .map_err(|error| error.to_string())?,
                )
            }
            "preview_creative_patch_undo" => {
                let request: UndoRequest = request(args)?;
                let project =
                    self.checked_project(request.project_id, request.generation, request.revision)?;
                encoded(
                    project
                        .preview_creative_patch_undo(request.patch_id)
                        .map_err(|error| error.to_string())?,
                )
            }
            "project_history" => {
                let request: HistoryRequest = request(args)?;
                encoded(
                    self.service
                        .history(request.project_id, request.after_revision, request.limit)
                        .map_err(|error| error.to_string())?,
                )
            }
            "project_history_recent" => {
                let request: RecentHistoryRequest = request(args)?;
                encoded(
                    self.service
                        .history_recent(request.project_id, request.through_revision, request.limit)
                        .map_err(|error| error.to_string())?,
                )
            }
            _ => Err(format!("Unsupported test transport command: {command}")),
        }
    }

    pub fn handle(&mut self, input: Value) -> Value {
        let id = input.get("id").cloned().unwrap_or(Value::Null);
        match serde_json::from_value::<Invoke>(input) {
            Ok(invoke) => match self.dispatch(&invoke.command, invoke.args) {
                Ok(result) => json!({"id": invoke.id, "ok": true, "result": result}),
                Err(error) => json!({"id": invoke.id, "ok": false, "error": error}),
            },
            Err(error) => json!({"id": id, "ok": false, "error": error.to_string()}),
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    if args.next().as_deref() != Some("--database") {
        return Err(
            "Usage: studio_review_session --database /absolute/test.sqlite3 [--synthetic-fixture] [--desktop-invoke-fixture]"
                .into(),
        );
    }
    let database = args.next().ok_or("Missing test database path")?;
    let mut synthetic = false;
    let mut desktop_invoke_fixture = false;
    for arg in args {
        match arg.as_str() {
            "--synthetic-fixture" if !synthetic => synthetic = true,
            "--desktop-invoke-fixture" if !desktop_invoke_fixture => desktop_invoke_fixture = true,
            _ => return Err("Unknown or duplicate test transport option".into()),
        }
    }
    let mut session = Session::open(database, synthetic)?;
    session.desktop_invoke_fixture = desktop_invoke_fixture;
    let input = io::stdin();
    let mut input = input.lock();
    let output = io::stdout();
    let mut output = output.lock();
    loop {
        let mut line = Vec::new();
        if (&mut input)
            .take(MAX_REQUEST_BYTES as u64 + 1)
            .read_until(b'\n', &mut line)?
            == 0
        {
            break;
        }
        let response = if line.len() > MAX_REQUEST_BYTES {
            if line.last() != Some(&b'\n') {
                input.skip_until(b'\n')?;
            }
            json!({"id": null, "ok": false, "error": "Test request exceeds 4 MiB"})
        } else {
            match serde_json::from_slice(&line) {
                Ok(input) => session.handle(input),
                Err(error) => json!({"id": null, "ok": false, "error": error.to_string()}),
            }
        };
        serde_json::to_writer(&mut output, &response)?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
    Ok(())
}
