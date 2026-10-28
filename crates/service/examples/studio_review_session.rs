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
//!
//! A native-owned test executable may reuse this same Session and JSONL runner
//! with a GraphBackend constructed from actual Host authority and this opened
//! Store. The standalone executable supplies none; no input flag installs one.

#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
#[allow(dead_code)] // This transport deliberately exposes only the tested local subset.
mod effect_grants;

#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
#[allow(dead_code)] // The exact desktop read/creation module, without Tauri transport.
mod project_library;

#[path = "../../../apps/desktop/src-tauri/src/project_projection.rs"]
#[allow(dead_code)] // The exact desktop bounded read module, without Tauri transport.
mod project_projection;

#[path = "../../../apps/desktop/src-tauri/src/caption_export.rs"]
mod caption_export;

#[path = "../../../apps/desktop/src-tauri/src/file_import.rs"]
#[allow(dead_code)] // The exact desktop import/recovery module, without Tauri transport.
mod file_import;

use effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::{
    AlignmentEvidence, CanvasNode, Change, CreativePatch, CueEvidence, GraphAuthoringContext,
    HeroConfig, NodeStyle, Project, RevisionStamp, hero_node_id,
};
use motionwright_service::{BundleFingerprint, SemanticGraphContextRequest, StudioService};
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

fn source_locked_sdk_revision() -> Result<&'static str, String> {
    static REVISION: std::sync::OnceLock<Result<String, String>> = std::sync::OnceLock::new();
    match REVISION.get_or_init(|| {
        let lock: Value = serde_json::from_str(include_str!("../../../SOURCE_LOCK.json"))
            .map_err(|_| "Compiled source lock is invalid".to_owned())?;
        let revision = lock
            .pointer("/dependencies/semwright/revision")
            .and_then(Value::as_str)
            .ok_or_else(|| "Compiled source lock has no Semwright revision".to_owned())?;
        if revision.len() != 40
            || !revision
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err("Compiled source lock has an invalid Semwright revision".to_owned());
        }
        Ok(revision.to_owned())
    }) {
        Ok(revision) => Ok(revision.as_str()),
        Err(error) => Err(error.clone()),
    }
}

/// Private-process configuration seam for the native-owned test executable.
/// This trait neither issues native authority nor exposes a JSON capability.
///
/// The production adapter must bind this actual StudioService Store to the
/// genuine owner mount before dispatch, including after Session reopens it.
/// A database path or equal project UUID/generation/revision is insufficient.
/// Context and writes must use the real Native client and current read/write
/// admission; a previous Ready response supplies no write permission.
///
/// Mutation errors use the shared, sanitized GraphInvokeFailure Desktop codec,
/// preserving code/outcome_known. Recovery errors describe only that read and
/// are sanitized ordinary strings; they cannot establish the original effect's
/// outcome. Original SDK diagnostics and envelopes remain private.
pub trait GraphBackend {
    fn authoring_context(
        &mut self,
        service: &StudioService,
        database: &Path,
        request: &SemanticGraphContextRequest,
    ) -> Result<GraphAuthoringContext, Value>;

    /// Session has checked the current project and consumed its one-time grant.
    /// Only the original UpsertSemanticGraph or RemoveSemanticGraph is passed.
    /// The original request key/change must reach the native durable operation;
    /// do not rebase the source, normalize the key or retry an UNKNOWN effect.
    fn apply_change(
        &mut self,
        service: &StudioService,
        database: &Path,
        project: &Project,
        request_id: &str,
        change: &Change,
    ) -> Result<Project, Value>;

    /// Read the original operation through current authenticated Native authority.
    /// The Native adapter parses the exact shared Desktop GraphRecoveryRequest,
    /// derives its original identity without effects, and validates the historical
    /// response with that same codec. Session introduces no recovery DTO or grant.
    /// Every callback, including after reopen, must bind the actual current Store.
    /// No mutation, new request key, Project refresh or automatic retry belongs here.
    fn operation_recovery(
        &mut self,
        _service: &StudioService,
        _database: &Path,
        _request: Value,
    ) -> Result<Value, Value> {
        Err(Value::String("Graph recovery read is unavailable.".into()))
    }
}

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
    fingerprint: BundleFingerprint,
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

// Only the projection commands use the typed desktop failure vocabulary.
fn projection_request<T: DeserializeOwned>(args: Value) -> Result<T, Value> {
    request(args).map_err(|_| Value::String("Invalid project projection request.".into()))
}

fn projection_encoded(
    result: Result<impl Serialize, motionwright_service::ProjectionFailure>,
) -> Result<Value, Value> {
    let value = result.map_err(|failure| {
        serde_json::to_value(failure)
            .unwrap_or_else(|_| Value::String("Cannot encode project projection failure.".into()))
    })?;
    serde_json::to_value(value)
        .map_err(|_| Value::String("Cannot encode project projection response.".into()))
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
    graph_backend: Option<Box<dyn GraphBackend>>,
}

impl Session {
    pub fn open(database: impl AsRef<Path>, synthetic_fixture: bool) -> Result<Self, String> {
        Self::open_with_graph_backend(database, synthetic_fixture, |_, _| Ok(None))
    }

    /// The native-owned adapter is configured from the actual opened Store,
    /// before synthetic project seeding. No JSON request reaches this factory.
    pub fn open_with_graph_backend(
        database: impl AsRef<Path>,
        synthetic_fixture: bool,
        factory: impl FnOnce(&Path, &StudioService) -> Result<Option<Box<dyn GraphBackend>>, String>,
    ) -> Result<Self, String> {
        let database = database.as_ref().to_owned();
        if !database.is_absolute() {
            return Err("Choose an absolute, isolated test database path".into());
        }
        let directory = database
            .parent()
            .ok_or("Test database needs a parent directory")?;
        fs::create_dir_all(directory).map_err(|error| error.to_string())?;
        let service = StudioService::open(&database).map_err(|error| error.to_string())?;
        let graph_backend = factory(&database, &service)?;
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
            graph_backend,
        })
    }

    fn checked_project(
        &self,
        id: Uuid,
        generation: Uuid,
        revision: u64,
    ) -> Result<Project, String> {
        let project = project_library::open_project(
            &self.service,
            project_library::OpenProjectRequest { project_id: id },
        )
        .map_err(|error| error.to_string())?;
        if project.generation != generation || project.revision != revision {
            return Err("Effect grant scope is stale; refresh the project before retrying.".into());
        }
        Ok(project)
    }

    fn dispatch_wire(&mut self, command: &str, args: Value) -> Result<Value, Value> {
        match command {
            "semantic_graph_authoring_context" => self.graph_context(args),
            "semantic_graph_operation_recovery" => self.graph_operation_recovery(args),
            "apply_change" => self.apply_change(args),
            "project_projection_head" => projection_encoded(project_projection::head(
                &self.service,
                projection_request(args)?,
            )),
            "project_scene_page" => projection_encoded(project_projection::scenes(
                &self.service,
                projection_request(args)?,
            )),
            "project_scene_nodes" => projection_encoded(project_projection::nodes(
                &self.service,
                projection_request(args)?,
            )),
            _ => self.dispatch(command, args).map_err(Value::String),
        }
    }

    fn graph_context(&mut self, args: Value) -> Result<Value, Value> {
        let request: SemanticGraphContextRequest = request(args)?;
        if let Some(backend) = self.graph_backend.as_mut() {
            let project = project_library::open_project(
                &self.service,
                project_library::OpenProjectRequest {
                    project_id: request.source_snapshot.project_id,
                },
            )
            .map_err(|error| Value::String(error.to_string()))?;
            request
                .source_snapshot
                .require_current(&project)
                .map_err(|error| Value::String(error.to_string()))?;
            encoded(backend.authoring_context(&self.service, &self.database, &request)?)
                .map_err(Value::String)
        } else {
            encoded(
                self.service
                    .semantic_graph_authoring_context(&request)
                    .map_err(|error| Value::String(error.to_string()))?,
            )
            .map_err(Value::String)
        }
    }

    fn graph_operation_recovery(&mut self, args: Value) -> Result<Value, Value> {
        // Unwrap only the existing JSONL invoke envelope. The exact original
        // payload, including invalid/unknown fields, reaches the one shared
        // Native/Desktop decoder; nothing is normalized or silently removed.
        let original: Value =
            request(args).map_err(|_| Value::String("Invalid Graph recovery request.".into()))?;
        let backend = self
            .graph_backend
            .as_mut()
            .ok_or_else(|| Value::String("Graph recovery read is unavailable.".into()))?;
        backend
            .operation_recovery(&self.service, &self.database, original)
            // A read failure can never prove that the original write failed.
            // Keep backend originals private even if an adapter returns an
            // unexpected mutation error object or sensitive diagnostic string.
            .map_err(|_| Value::String("Graph recovery read could not be completed.".into()))
    }

    fn apply_change(&mut self, args: Value) -> Result<Value, Value> {
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
            Change::ApplyVoiceAlignment { .. }
            | Change::ImportMeasuredVoice { .. }
            | Change::AddVoiceTrack { .. } => true,
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
        let source = match &request.change {
            Change::UpsertSemanticGraph { graph } => Some(&graph.authorship.source_snapshot),
            Change::RemoveSemanticGraph {
                source_snapshot, ..
            } => Some(source_snapshot),
            _ => None,
        };
        if let (Some(source), Some(backend)) = (source, self.graph_backend.as_mut()) {
            // The local grant must not authorize a native operation on another
            // project/generation selected by the Graph payload. Revision/branch
            // freshness and exact historical replay remain the native Store's job.
            if source.project_id != project.id || source.generation != project.generation {
                return Err(Value::String(
                    "Graph source and effect grant project scope differ.".into(),
                ));
            }
            return encoded(backend.apply_change(
                &self.service,
                &self.database,
                &project,
                &request.request_id,
                &request.change,
            )?)
            .map_err(Value::String);
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
        .map_err(Value::String)
    }

    fn dispatch(&mut self, command: &str, args: Value) -> Result<Value, String> {
        match command {
            "scene_boundary_authoring_context" => encoded(
                self.service
                    .scene_boundary_authoring_context(&request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
            "export_caption_sidecar" => encoded(caption_export::export_caption_sidecar(
                &self.service,
                &self.grants,
                request(args)?,
            )?),
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
                        "pinned_revision": source_locked_sdk_revision()?,
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
            "get_project" | "reopen" => {
                let request: ProjectRequest = request(args)?;
                if command == "reopen" {
                    // Replace the connection from persisted SQLite state and discard all grants.
                    let mut replacement = Self::open(&self.database, false)?;
                    replacement.desktop_invoke_fixture = self.desktop_invoke_fixture;
                    // Grants are replaced; Native must revalidate this new Store
                    // origin before any later backend callback dispatches.
                    replacement.graph_backend = self.graph_backend.take();
                    *self = replacement;
                }
                encoded(
                    project_library::open_project(
                        &self.service,
                        project_library::OpenProjectRequest {
                            project_id: request.project_id,
                        },
                    )
                    .map_err(|error| error.to_string())?,
                )
            }
            "prepare_file_import" => encoded(
                file_import::prepare_file_import(&self.service, request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
            "import_asset_file" => encoded(
                file_import::import_asset_file(&self.service, &self.grants, request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
            "import_voice_file" => encoded(
                file_import::import_voice_file(&self.service, &self.grants, request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
            "recover_file_import" => encoded(
                file_import::recover_file_import(&self.service, request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
            "prepare_native_capsule_source_file" => encoded(
                file_import::prepare_native_capsule_source_file(&self.service, request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
            "import_native_capsule_source_file" => encoded(
                file_import::import_native_capsule_source_file(
                    &self.service,
                    &self.grants,
                    request(args)?,
                )
                .map_err(|error| error.to_string())?,
            ),
            "recover_native_capsule_source_file" => encoded(
                file_import::recover_native_capsule_source_file(&self.service, request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
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
                let inspected = self
                    .service
                    .inspect_project_bundle_with_fingerprint(&source)
                    .map_err(|error| error.to_string())?;
                let plan = inspected.plan;
                Ok(json!({
                    "project_id": plan.project.project_id,
                    "title": plan.project.title,
                    "source_generation": plan.project.source_generation,
                    "revision": plan.project.revision.to_string(),
                    "event_count": plan.project.event_count,
                    "blob_count": plan.blob_count,
                    "total_blob_bytes": plan.total_blob_bytes.to_string(),
                    "rotates_generation": plan.project.rotates_generation,
                    "fingerprint": inspected.fingerprint,
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
                        .import_project_bundle_inspected(&source, &request.fingerprint)
                        .map_err(|error| error.to_string())?,
                )
            }
            "preview_branch_combination" => encoded(
                self.service
                    .preview_branch_combination(&request(args)?)
                    .map_err(|error| error.to_string())?,
            ),
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
            Ok(invoke) => match self.dispatch_wire(&invoke.command, invoke.args) {
                Ok(result) => json!({"id": invoke.id, "ok": true, "result": result}),
                Err(error) => json!({"id": invoke.id, "ok": false, "error": error}),
            },
            Err(error) => json!({"id": id, "ok": false, "error": error.to_string()}),
        }
    }
}

/// Reuse the exact CLI parser, private Session gates and bounded JSONL loop.
/// Native owns the factory and asynchronous client/runtime; Service does not
/// depend on Native. A standalone run has no injected Graph backend.
pub fn run_with_graph_backend(
    factory: impl FnOnce(&Path, &StudioService) -> Result<Option<Box<dyn GraphBackend>>, String>,
) -> Result<(), Box<dyn std::error::Error>> {
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
    let mut session = Session::open_with_graph_backend(database, synthetic, factory)?;
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

fn main() -> Result<(), Box<dyn std::error::Error>> {
    run_with_graph_backend(|_, _| Ok(None))
}

#[cfg(test)]
#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod semantic_graph_source_fixture;

#[cfg(test)]
mod graph_backend_tests {
    use super::*;
    use motionwright_domain::{GraphAvailability, GraphSourceSnapshot, RendererKind};
    use std::sync::{Arc, Mutex};

    // These are Session wiring/parser tests. The injected backend only refuses
    // operations; it creates no Ready, NativeRef, Host permit or successful
    // native mutation. Installed native evidence remains a separate gate.
    #[derive(Default)]
    struct Calls {
        contexts: Vec<(PathBuf, Value)>,
        changes: Vec<(PathBuf, Project, String, Change, bool)>,
    }

    struct RejectingBackend {
        origin: StudioService,
        calls: Arc<Mutex<Calls>>,
    }

    fn failure_sentinel() -> Value {
        // An opaque transport sentinel, not another GraphInvokeFailure schema
        // or a production error encoder. In production A1's shared codec owns it.
        json!({"code":"test_transport_sentinel","outcome_known":false})
    }

    impl GraphBackend for RejectingBackend {
        fn authoring_context(
            &mut self,
            _service: &StudioService,
            database: &Path,
            request: &SemanticGraphContextRequest,
        ) -> Result<GraphAuthoringContext, Value> {
            self.calls
                .lock()
                .unwrap()
                .contexts
                .push((database.to_owned(), serde_json::to_value(request).unwrap()));
            Err(failure_sentinel())
        }

        fn apply_change(
            &mut self,
            service: &StudioService,
            database: &Path,
            project: &Project,
            request_id: &str,
            change: &Change,
        ) -> Result<Project, Value> {
            self.calls.lock().unwrap().changes.push((
                database.to_owned(),
                project.clone(),
                request_id.to_owned(),
                change.clone(),
                Arc::ptr_eq(&self.origin.store(), &service.store()),
            ));
            Err(failure_sentinel())
        }
    }

    fn seeded_project(service: &StudioService) -> Project {
        let project = service.create_project("Session Graph wiring test").unwrap();
        let project = service
            .apply(
                project.id,
                &project.stamp(),
                "session-seed-scene",
                &Change::AddScene {
                    name: "Graph source selection".into(),
                    objective: "Check transport guards without native authority".into(),
                    duration_seconds: 6,
                },
            )
            .unwrap()
            .project;
        service
            .apply(
                project.id,
                &project.stamp(),
                "session-seed-renderer",
                &Change::SetSceneRenderer {
                    scene_id: project.scenes[0].id,
                    renderer: RendererKind::ManimCommunity,
                },
            )
            .unwrap()
            .project
    }

    fn injected_session() -> (tempfile::TempDir, Session, Project, Arc<Mutex<Calls>>) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("session.sqlite3");
        let calls = Arc::new(Mutex::new(Calls::default()));
        let observed = calls.clone();
        let session = Session::open_with_graph_backend(&path, false, move |_, service| {
            let backend: Box<dyn GraphBackend> = Box::new(RejectingBackend {
                origin: service.clone(),
                calls: observed,
            });
            Ok(Some(backend))
        })
        .unwrap();
        let project = seeded_project(&session.service);
        (directory, session, project, calls)
    }

    fn wire(command: &str, payload: Value) -> Value {
        json!({"id":Uuid::now_v7(),"command":command,"args":{"request":payload}})
    }

    fn issue_edit(session: &mut Session, project: &Project, key: &str) -> Uuid {
        let response = session.handle(wire(
            "issue_effect_grant",
            json!({
                "effect":"project_edit","project_id":project.id,
                "generation":project.generation,"revision":project.revision,"subject":key
            }),
        ));
        assert_eq!(response["ok"], true, "{response}");
        serde_json::from_value(response["result"]["token"].clone()).unwrap()
    }

    fn apply_wire(project: &Project, key: &str, grant: Uuid, change: &Change) -> Value {
        wire(
            "apply_change",
            json!({
                "project_id":project.id,"generation":project.generation,
                "revision":project.revision,"request_id":key,
                "effect_grant":grant,"change":change
            }),
        )
    }

    fn context_request(project: &Project) -> SemanticGraphContextRequest {
        SemanticGraphContextRequest {
            source_snapshot: GraphSourceSnapshot::from_project(project),
            scene_id: project.scenes[0].id,
            variant_profile_ref: project.deliverables[0].id,
        }
    }

    #[test]
    fn standalone_and_desktop_fixture_flags_do_not_install_graph_authority() {
        let directory = tempfile::tempdir().unwrap();
        let mut session = Session::open(directory.path().join("ordinary.sqlite3"), false).unwrap();
        let project = seeded_project(&session.service);
        let journal = session.service.history(project.id, 0, 1024).unwrap();
        for desktop_fixture in [false, true] {
            session.desktop_invoke_fixture = desktop_fixture;
            let response = session.handle(wire(
                "semantic_graph_authoring_context",
                serde_json::to_value(context_request(&project)).unwrap(),
            ));
            assert_eq!(response["ok"], true);
            assert_eq!(
                response["result"]["availability"],
                serde_json::to_value(GraphAvailability::Blocked).unwrap()
            );
            assert!(session.graph_backend.is_none());
        }
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(semantic_graph_source_fixture::graph_for(&project, false)),
        };
        let grant = issue_edit(&mut session, &project, "ordinary-graph");
        let response = session.handle(apply_wire(&project, "ordinary-graph", grant, &change));
        assert_eq!(response["ok"], false);
        assert_eq!(session.service.project(project.id).unwrap(), project);
        assert_eq!(
            session.service.history(project.id, 0, 1024).unwrap(),
            journal
        );
    }

    #[test]
    fn factory_observes_the_actual_open_store_before_synthetic_seeding() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("factory-refused.sqlite3");
        let result = Session::open_with_graph_backend(&path, true, |database, service| {
            assert_eq!(database, path.as_path());
            assert!(service.projects(1).unwrap().is_empty());
            Err("Test factory refuses configuration before seeding".into())
        });
        assert!(result.is_err());
        let reopened = StudioService::open(&path).unwrap();
        assert!(reopened.projects(1).unwrap().is_empty());
    }

    #[test]
    fn graph_delegation_consumes_one_grant_and_preserves_the_opaque_failure_and_original_change() {
        let (_directory, mut session, project, calls) = injected_session();
        let journal = session.service.history(project.id, 0, 1024).unwrap();
        let graph = semantic_graph_source_fixture::graph_for(&project, false);
        let changes = [
            Change::UpsertSemanticGraph {
                graph: Box::new(graph.clone()),
            },
            Change::RemoveSemanticGraph {
                graph_id: graph.id,
                scene_id: graph.scene_id,
                source_snapshot: GraphSourceSnapshot::from_project(&project),
            },
        ];
        for (index, change) in changes.iter().enumerate() {
            let key = format!(" original-domain-key-{index} ");
            let grant = issue_edit(&mut session, &project, &key);
            let input = apply_wire(&project, &key, grant, change);
            let response = session.handle(input.clone());
            assert_eq!(response["id"], input["id"]);
            assert_eq!(response["ok"], false);
            assert_eq!(response["error"], failure_sentinel());
            assert_eq!(session.handle(input)["ok"], false);
            let seen = calls.lock().unwrap();
            assert_eq!(seen.changes.len(), index + 1);
            let (database, current, original_key, original_change, same_store) =
                &seen.changes[index];
            assert_eq!(database, &session.database);
            assert_eq!(current, &project);
            assert_eq!(original_key, &key);
            assert_eq!(original_change, change);
            assert!(*same_store);
        }
        assert_eq!(session.service.project(project.id).unwrap(), project);
        assert_eq!(
            session.service.history(project.id, 0, 1024).unwrap(),
            journal
        );
    }

    #[test]
    fn stale_scope_and_missing_grant_never_reach_the_injected_backend() {
        let (_directory, mut session, project, calls) = injected_session();
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(semantic_graph_source_fixture::graph_for(&project, false)),
        };
        let missing = session.handle(apply_wire(&project, "missing", Uuid::now_v7(), &change));
        assert_eq!(missing["ok"], false);
        let grant = issue_edit(&mut session, &project, "current-guard");
        let mut stale = apply_wire(&project, "current-guard", grant, &change);
        stale["args"]["request"]["revision"] = json!(project.revision + 1);
        assert_eq!(session.handle(stale)["ok"], false);
        assert!(calls.lock().unwrap().changes.is_empty());
        // checked_project still precedes consume, so the valid scope can use
        // the original unconsumed grant and reaches the refusing backend once.
        let response = session.handle(apply_wire(&project, "current-guard", grant, &change));
        assert_eq!(response["error"], failure_sentinel());
        assert_eq!(calls.lock().unwrap().changes.len(), 1);
    }

    #[test]
    fn graph_source_cannot_escape_the_consumed_grant_project_or_generation() {
        let (_directory, mut session, project, calls) = injected_session();
        let journal = session.service.history(project.id, 0, 1024).unwrap();
        for field in ["project", "generation"] {
            let mut graph = semantic_graph_source_fixture::graph_for(&project, false);
            match field {
                "project" => graph.authorship.source_snapshot.project_id = Uuid::now_v7(),
                "generation" => graph.authorship.source_snapshot.generation = Uuid::now_v7(),
                _ => unreachable!(),
            }
            let key = format!("wrong-graph-{field}");
            let change = Change::UpsertSemanticGraph {
                graph: Box::new(graph),
            };
            let grant = issue_edit(&mut session, &project, &key);
            assert_eq!(
                session.handle(apply_wire(&project, &key, grant, &change))["ok"],
                false
            );
        }
        assert!(calls.lock().unwrap().changes.is_empty());
        assert_eq!(session.service.project(project.id).unwrap(), project);
        assert_eq!(
            session.service.history(project.id, 0, 1024).unwrap(),
            journal
        );
    }

    #[test]
    fn non_graph_edits_keep_the_service_route_and_historical_graph_source_is_not_rebased() {
        let (_directory, mut session, project, calls) = injected_session();
        let original = Change::UpsertSemanticGraph {
            graph: Box::new(semantic_graph_source_fixture::graph_for(&project, false)),
        };
        let rename = Change::RenameProject {
            title: "Ordinary service edit".into(),
        };
        let grant = issue_edit(&mut session, &project, "ordinary-rename");
        let response = session.handle(apply_wire(&project, "ordinary-rename", grant, &rename));
        assert_eq!(response["ok"], true);
        let current: Project = serde_json::from_value(response["result"].clone()).unwrap();
        assert_eq!(current.revision, project.revision + 1);
        assert!(calls.lock().unwrap().changes.is_empty());
        let grant = issue_edit(&mut session, &current, "original-graph-key");
        let response = session.handle(apply_wire(&current, "original-graph-key", grant, &original));
        assert_eq!(response["error"], failure_sentinel());
        let seen = calls.lock().unwrap();
        assert_eq!(seen.changes[0].1, current);
        assert_eq!(seen.changes[0].3, original);
        assert_eq!(session.service.project(project.id).unwrap(), current);
    }

    #[test]
    fn context_and_invoke_parsers_cannot_install_or_bypass_the_native_backend() {
        let (_directory, mut session, project, calls) = injected_session();
        let request = context_request(&project);
        let response = session.handle(wire(
            "semantic_graph_authoring_context",
            serde_json::to_value(&request).unwrap(),
        ));
        assert_eq!(response["error"], failure_sentinel());
        let mut stale = request.clone();
        stale.source_snapshot.branch_id = Uuid::now_v7();
        assert_eq!(
            session.handle(wire(
                "semantic_graph_authoring_context",
                serde_json::to_value(stale).unwrap()
            ))["ok"],
            false
        );
        let mut forged = serde_json::to_value(&request).unwrap();
        forged["availability"] = json!("ready");
        assert_eq!(
            session.handle(wire("semantic_graph_authoring_context", forged))["ok"],
            false
        );
        let mut forged_invoke = wire(
            "semantic_graph_authoring_context",
            serde_json::to_value(&request).unwrap(),
        );
        forged_invoke["graph_backend"] = json!({"ready":true});
        assert_eq!(session.handle(forged_invoke)["ok"], false);
        let seen = calls.lock().unwrap();
        assert_eq!(seen.contexts.len(), 1);
        assert_eq!(seen.contexts[0].0, session.database);
        assert_eq!(seen.contexts[0].1, serde_json::to_value(request).unwrap());
    }

    struct RecoveryCall {
        database: PathBuf,
        original: Value,
        same_store: bool,
    }

    struct RefusingRecoveryBackend {
        origin: StudioService,
        calls: Arc<Mutex<Vec<RecoveryCall>>>,
    }

    impl GraphBackend for RefusingRecoveryBackend {
        fn authoring_context(
            &mut self,
            _service: &StudioService,
            _database: &Path,
            _request: &SemanticGraphContextRequest,
        ) -> Result<GraphAuthoringContext, Value> {
            panic!("A recovery read must not request authoring readiness")
        }

        fn apply_change(
            &mut self,
            _service: &StudioService,
            _database: &Path,
            _project: &Project,
            _request_id: &str,
            _change: &Change,
        ) -> Result<Project, Value> {
            panic!("A recovery read must not dispatch a mutation")
        }

        fn operation_recovery(
            &mut self,
            service: &StudioService,
            database: &Path,
            original: Value,
        ) -> Result<Value, Value> {
            self.calls.lock().unwrap().push(RecoveryCall {
                database: database.to_owned(),
                original,
                same_store: Arc::ptr_eq(&self.origin.store(), &service.store()),
            });
            // Deliberately wrong private diagnostic shape proves that Session
            // cannot expose it as a known failure of the original mutation.
            Err(json!({
                "outcome_known": true,
                "private_original": "do-not-expose-recovery-diagnostic"
            }))
        }
    }

    fn recovery_backend(session: &mut Session) -> Arc<Mutex<Vec<RecoveryCall>>> {
        let calls = Arc::new(Mutex::new(Vec::new()));
        session.graph_backend = Some(Box::new(RefusingRecoveryBackend {
            origin: session.service.clone(),
            calls: calls.clone(),
        }));
        calls
    }

    #[test]
    fn recovery_without_a_native_implementation_is_an_ordinary_read_refusal() {
        let (_directory, mut session, project, calls) = injected_session();
        let journal = session.service.history(project.id, 0, 1024).unwrap();
        let original = json!({
            "request_id": Uuid::now_v7().to_string(),
            "change": Change::UpsertSemanticGraph {
                graph: Box::new(semantic_graph_source_fixture::graph_for(&project, false)),
            },
        });
        for has_default_backend in [true, false] {
            if !has_default_backend {
                session.graph_backend = None;
            }
            let response =
                session.handle(wire("semantic_graph_operation_recovery", original.clone()));
            assert_eq!(response["ok"], false);
            assert!(response["error"].is_string());
            assert!(response["error"].get("outcome_known").is_none());
            assert_eq!(session.service.project(project.id).unwrap(), project);
            assert_eq!(
                session.service.history(project.id, 0, 1024).unwrap(),
                journal
            );
        }
        let observed = calls.lock().unwrap();
        assert!(observed.contexts.is_empty());
        assert!(observed.changes.is_empty());
    }

    #[test]
    fn recovery_preserves_original_payload_without_current_revision_or_grant_requirements() {
        let (_directory, mut session, project, _) = injected_session();
        let calls = recovery_backend(&mut session);
        let original = json!({
            "request_id": Uuid::now_v7().to_string().to_uppercase(),
            "change": Change::UpsertSemanticGraph {
                graph: Box::new(semantic_graph_source_fixture::graph_for(&project, false)),
            },
        });
        let current = session
            .service
            .apply(
                project.id,
                &project.stamp(),
                "later-ordinary-edit",
                &Change::RenameProject {
                    title: "Later live project".into(),
                },
            )
            .unwrap()
            .project;
        assert!(current.revision > project.revision);
        let journal = session.service.history(project.id, 0, 1024).unwrap();
        let unused_grant = issue_edit(&mut session, &current, "grant-survives-recovery");
        let response = session.handle(wire("semantic_graph_operation_recovery", original.clone()));
        assert_eq!(response["ok"], false);
        assert_eq!(
            response["error"],
            "Graph recovery read could not be completed."
        );
        assert!(!response.to_string().contains("do-not-expose"));
        assert_eq!(session.service.project(project.id).unwrap(), current);
        assert_eq!(
            session.service.history(project.id, 0, 1024).unwrap(),
            journal
        );
        {
            let observed = calls.lock().unwrap();
            assert_eq!(observed.len(), 1);
            assert_eq!(observed[0].original, original);
            assert_eq!(observed[0].database, session.database);
            assert!(observed[0].same_store);
        }
        // Recovery neither needs nor consumes a write grant.
        let rename = Change::RenameProject {
            title: "Grant was not consumed".into(),
        };
        assert_eq!(
            session.handle(apply_wire(
                &current,
                "grant-survives-recovery",
                unused_grant,
                &rename,
            ))["ok"],
            true
        );
    }

    #[test]
    fn recovery_after_reopen_uses_current_store_and_preserves_invalid_fields_for_shared_decoder() {
        let (_directory, mut session, project, _) = injected_session();
        let calls = recovery_backend(&mut session);
        let graph = semantic_graph_source_fixture::graph_for(&project, false);
        let original = json!({
            "request_id": Uuid::now_v7().to_string(),
            "change": Change::RemoveSemanticGraph {
                graph_id: graph.id,
                scene_id: graph.scene_id,
                source_snapshot: GraphSourceSnapshot::from_project(&project),
            },
            // The shared Native decoder must reject this field. Session must
            // not drop it and turn an invalid request into a valid one.
            "forged_authority": {"ready": true},
        });
        let malformed_envelope = session.handle(json!({
            "id": 1, "command": "semantic_graph_operation_recovery", "args": {}
        }));
        assert_eq!(malformed_envelope["ok"], false);
        assert!(malformed_envelope["error"].is_string());
        assert!(calls.lock().unwrap().is_empty());
        assert_eq!(
            session.handle(wire("reopen", json!({"project_id": project.id}),))["ok"],
            true
        );
        let journal = session.service.history(project.id, 0, 1024).unwrap();
        let response = session.handle(wire("semantic_graph_operation_recovery", original.clone()));
        assert_eq!(response["ok"], false);
        assert!(response["error"].is_string());
        let observed = calls.lock().unwrap();
        assert_eq!(observed.len(), 1);
        assert_eq!(observed[0].original, original);
        assert_eq!(observed[0].database, session.database);
        assert!(!observed[0].same_store);
        assert_eq!(session.service.project(project.id).unwrap(), project);
        assert_eq!(
            session.service.history(project.id, 0, 1024).unwrap(),
            journal
        );
    }

    #[test]
    fn reopen_discards_grants_and_exposes_the_new_actual_store_to_the_backend() {
        let (_directory, mut session, project, calls) = injected_session();
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(semantic_graph_source_fixture::graph_for(&project, false)),
        };
        let previous_session = session.workspace_session;
        let grant = issue_edit(&mut session, &project, "before-reopen");
        let response = session.handle(wire("reopen", json!({"project_id":project.id})));
        assert_eq!(response["ok"], true);
        assert_ne!(session.workspace_session, previous_session);
        assert_eq!(
            session.handle(apply_wire(&project, "before-reopen", grant, &change))["ok"],
            false
        );
        assert!(calls.lock().unwrap().changes.is_empty());
        let grant = issue_edit(&mut session, &project, "after-reopen");
        let response = session.handle(apply_wire(&project, "after-reopen", grant, &change));
        assert_eq!(response["error"], failure_sentinel());
        let seen = calls.lock().unwrap();
        assert_eq!(seen.changes.len(), 1);
        assert!(
            !seen.changes[0].4,
            "The callback must receive the replacement Store"
        );
    }
}

#[cfg(test)]
mod source_lock_tests {
    use super::*;

    #[test]
    fn bootstrap_reports_compiled_source_locked_sdk() {
        let directory = tempfile::tempdir().unwrap();
        let mut session = Session::open(
            directory.path().join("source-bound-bootstrap.sqlite3"),
            false,
        )
        .unwrap();
        let response = session.handle(json!({"id": 1, "command": "bootstrap", "args": {}}));
        assert_eq!(response["ok"], true);
        let lock: Value = serde_json::from_str(include_str!("../../../SOURCE_LOCK.json")).unwrap();
        let expected = lock["dependencies"]["semwright"]["revision"]
            .as_str()
            .unwrap();
        assert_eq!(
            response["result"]["native_sdk"]["pinned_revision"],
            expected
        );
        let sdk_packages: Vec<_> = include_str!("../../../Cargo.lock")
            .split("[[package]]")
            .filter(|package| {
                package
                    .lines()
                    .any(|line| line.trim() == "name = \"semwright-native-sdk\"")
            })
            .collect();
        assert_eq!(sdk_packages.len(), 1);
        let suffix = format!("#{expected}\"");
        assert!(
            sdk_packages[0].lines().any(|line| {
                line.trim_start().starts_with("source = ") && line.ends_with(&suffix)
            })
        );
    }
}
