//! N83: original, bounded native preview reconstruction fixture.
//!
//! Each command opens the same SQLite project in a new process. A stage marker
//! is exclusively created before any Store or native effect. Existing markers
//! prohibit automatic replay. This producer never grants master acceptance.

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use motionwright_domain::{
    CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RenderImpactCause,
    RenderTailInspectRequest, RendererKind, RevisionStamp, SourceFrameWindow,
};
use motionwright_native::film::{FilmBuildOptions, SceneFilmIntent, build_motion_canvas_window};
use motionwright_native::production::{
    MotionCanvasWindowRequest, ProductionConnection, ProductionCoordinator,
    read_verified_production_artifact,
};
use motionwright_service::{BlobDescriptor, RenderWindowCacheKey, StudioService};
use motionwright_storage::StorageError;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_semantic_composition::canonical_digest;
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

// These sizes define this fixture, not an admission limit on other projects.
const SCENE_FRAMES: u64 = 30;
const TOTAL_FRAMES: u64 = 90;
const DIRTY_START: u64 = 30;
const DIRTY_END: u64 = 60;
const MAX_ORIGINAL_BYTES: u64 = 128 * 1024 * 1024;
const ORIGINAL_FILL: &str = "#1B62D1";
const EDITED_FILL: &str = "#F06A28";

fn require(condition: bool, message: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(message.to_owned().into())
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn unix_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_millis())
        .unwrap_or(0)
}

fn read_json(path: &Path) -> Result<Value> {
    let metadata = fs::symlink_metadata(path)?;
    require(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "original must be a regular file",
    )?;
    require(
        metadata.len() <= MAX_ORIGINAL_BYTES,
        "original exceeds bounded reader",
    )?;
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn write_json<T: Serialize + ?Sized>(path: &Path, value: &T) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    write_new(path, &bytes)
}

fn descriptor(root: &Path, path: &Path) -> Result<Value> {
    let relative = path.strip_prefix(root)?;
    require(
        relative
            .components()
            .all(|part| matches!(part, Component::Normal(_))),
        "unsafe original relative path",
    )?;
    let bytes = fs::read(path)?;
    Ok(
        json!({"path": relative.to_string_lossy().replace('\\', "/"), "bytes": bytes.len(), "sha256": digest(&bytes)}),
    )
}

fn command_text(root: &Path, arguments: &[&str]) -> Result<String> {
    let result = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(arguments)
        .output()?;
    require(result.status.success(), "source identity Git query failed")?;
    Ok(String::from_utf8(result.stdout)?.trim().to_owned())
}

fn source_identity() -> Result<Value> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or("native crate is not inside a repository")?;
    let head = command_text(root, &["rev-parse", "HEAD"])?;
    let expected = std::env::var("MW_NATIVE_EXPECTED_SOURCE_SHA")
        .map_err(|_| "final source admission MW_NATIVE_EXPECTED_SOURCE_SHA is required")?;
    require(
        head == expected && head.len() == 40,
        "native source is not the admitted final source",
    )?;
    require(
        command_text(
            root,
            &["status", "--porcelain=v1", "--untracked-files=normal"],
        )?
        .is_empty(),
        "native source is dirty",
    )?;
    let lock = read_json(&root.join("SOURCE_LOCK.json"))?;
    let sdk = lock
        .pointer("/dependencies/semwright/revision")
        .and_then(Value::as_str)
        .ok_or("SOURCE_LOCK semwright revision missing")?;
    require(
        sdk.len() == 40 && sdk.chars().all(|c| c.is_ascii_hexdigit()),
        "invalid selected SDK revision",
    )?;
    let mut locks = BTreeMap::new();
    for relative in [
        "Cargo.lock",
        "SOURCE_LOCK.json",
        "rust-toolchain.toml",
        "apps/studio/package-lock.json",
    ] {
        let path = root.join(relative);
        let raw = fs::read(&path)?;
        let git = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(["show", &format!("HEAD:{relative}")])
            .output()?;
        require(
            git.status.success() && git.stdout == raw,
            "selected source lock differs from its committed original",
        )?;
        locks.insert(relative.to_owned(), digest(&raw));
    }
    Ok(json!({"motionwright_sha": head, "sdk_revision": sdk,
        "locks_sha256": digest(&serde_json::to_vec(&locks)?), "locks": locks}))
}

struct Stage {
    path: PathBuf,
    finished: bool,
}

impl Stage {
    fn start(root: &Path, name: &str, database: &Path, source: &Value) -> Result<Self> {
        fs::create_dir_all(root)?;
        let path = root.join(name);
        fs::create_dir(&path)?;
        write_json(
            &path.join("stage.started.json"),
            &json!({
                "schema": "motionwright.native-incremental-stage/1", "stage": name,
                "started_unix_ms": unix_millis(), "pid": std::process::id(),
                "database": database, "source": source, "automatic_retry": false,
                "native_execution_result": "UNKNOWN_UNTIL_ORIGINAL_TERMINAL",
                "master_eligible": false, "acceptance_granted": false,
            }),
        )?;
        Ok(Self {
            path,
            finished: false,
        })
    }

    fn complete(&mut self, source: &Value) -> Result<()> {
        require(
            source_identity()? == *source,
            "source changed during the stage",
        )?;
        write_json(
            &self.path.join("stage.terminal.json"),
            &json!({
                "schema": "motionwright.native-incremental-stage-terminal/1",
                "status": "COMPLETED", "finished_unix_ms": unix_millis(),
                "source": source, "automatic_retry": false, "master_eligible": false,
                "acceptance_granted": false,
            }),
        )?;
        self.finished = true;
        Ok(())
    }

    fn uncertain(&mut self, error: &str) -> Result<()> {
        write_json(
            &self.path.join("stage.unknown.json"),
            &json!({
                "schema": "motionwright.native-incremental-stage-terminal/1", "status": "UNKNOWN",
                "error": error, "finished_unix_ms": unix_millis(), "automatic_retry": false,
                "effects_absent_asserted": false, "master_eligible": false,
            }),
        )?;
        self.finished = true;
        Ok(())
    }
}

impl Drop for Stage {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.uncertain(
                "stage ended without an original terminal; preserve and reconcile, do not replay",
            );
        }
    }
}

fn single_project(service: &StudioService) -> Result<Project> {
    let projects = service.projects(2)?;
    require(
        projects.len() == 1,
        "fixture requires its original single-project database",
    )?;
    Ok(service.project(projects[0].id)?)
}

fn options(project: &Project) -> Result<FilmBuildOptions> {
    Ok(FilmBuildOptions {
        frame_rate: Rate::new(30, 1)?,
        font_family: "Instrument Sans Variable".to_owned(),
        mono_font_family: "IBM Plex Mono".to_owned(),
        scene_intents: project
            .scenes
            .iter()
            .map(|scene| SceneFilmIntent {
                scene_id: scene.id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            })
            .collect(),
    })
}

fn middle_id(project: &Project) -> Result<Uuid> {
    require(
        project.scenes.len() == 3,
        "the original three scenes must be preserved",
    )?;
    Ok(project.scenes[1].id)
}

fn state(project: &Project, source: &Value, after: bool) -> Result<Value> {
    require(project.scenes.len() == 3, "fixture scene identity changed")?;
    let profile = project
        .deliverables
        .first()
        .ok_or("preview profile missing")?;
    require(
        profile.width == 640 && profile.height == 360,
        "fixture output profile changed",
    )?;
    require(
        profile.frame_rate.den > 0 && profile.frame_rate.num == 30 * profile.frame_rate.den,
        "fixture preview frame rate must be exactly 30/1",
    )?;
    require(
        profile.included_scene_ids.is_empty(),
        "fixture cannot include a cut or filtered scene list",
    )?;
    for (index, scene) in project.scenes.iter().enumerate() {
        require(
            scene.start.den > 0 && scene.start.num == index as i64 * scene.start.den,
            "fixture scene start is not the exact consecutive second",
        )?;
        require(
            scene.duration.den > 0 && scene.duration.num == scene.duration.den,
            "fixture scene duration is not exactly one second",
        )?;
        require(
            scene.renderer == RendererKind::MotionCanvas,
            "fixture contains a non-Motion-Canvas scene",
        )?;
        require(
            scene.nodes.len() == 1,
            "fixture scene must preserve exactly one source rectangle",
        )?;
        let node = &scene.nodes[0];
        require(
            node.kind == "rectangle"
                && node.parent_id.is_none()
                && node.relations.is_empty()
                && node.keyframes.is_empty(),
            "fixture rectangle acquired hierarchy, relations, or animation",
        )?;
        require(
            node.style.fill.as_deref()
                == Some(if after && index == 1 {
                    EDITED_FILL
                } else {
                    ORIGINAL_FILL
                }),
            "fixture rectangle fill is not the declared before/after source value",
        )?;
    }
    let opts = options(project)?;
    let whole = build_motion_canvas_window(
        project,
        profile.id,
        project.scenes[0].id,
        SourceFrameWindow {
            start: 0,
            end: SCENE_FRAMES,
        },
        &opts,
    )?;
    require(
        whole.segment_index == 0 && whole.segment.frame_count == TOTAL_FRAMES,
        "fixture must map to one original 90-frame Motion Canvas film",
    )?;
    require(
        whole.segment.scene_ids
            == project
                .scenes
                .iter()
                .map(|scene| scene.id)
                .collect::<Vec<_>>(),
        "fixture film must preserve all three scene identities",
    )?;
    let mut dependencies = serde_json::to_value(project)?;
    let object = dependencies
        .as_object_mut()
        .ok_or("project serialization is not an object")?;
    for field in [
        "scenes",
        "revision",
        "updated_at",
        "branches",
        "branch_workspaces",
    ] {
        object.remove(field);
    }
    let dependencies =
        json!({"project": dependencies, "source": source, "options": opts, "profile": profile});
    let dependency_hash = digest(&serde_json::to_vec(&dependencies)?);
    let mut segments = Vec::new();
    for (index, scene) in project.scenes.iter().enumerate() {
        let subgraph_hash = digest(&serde_json::to_vec(scene)?);
        segments.push(json!({"id": scene.id.to_string(), "start_frame": index as u64 * SCENE_FRAMES,
            "end_frame_exclusive": (index as u64 + 1) * SCENE_FRAMES,
            "plan_sha256": subgraph_hash, "film_sha256": subgraph_hash,
            "digest_scope": "Canonical serialized Scene source subgraph; global state.film_sha256 binds the compiled full Film",
            "dependencies_sha256": dependency_hash,
            "capture_id": if after && index == 1 { "dirty-original" } else { "before-original" },
        }));
    }
    let film_digest = canonical_digest(&whole.segment.film)?;
    Ok(
        json!({"revision": project.revision, "film_sha256": film_digest.as_str(),
        "dependencies_sha256": dependency_hash, "segments": segments,
        "dependency_closure": dependencies, "film": whole.segment.film}),
    )
}

fn save_state(stage: &Stage, project: &Project, source: &Value, after: bool) -> Result<Value> {
    write_json(&stage.path.join("project.original.json"), project)?;
    let value = state(project, source, after)?;
    write_json(&stage.path.join("state.original.json"), &value)?;
    write_json(&stage.path.join("film.original.json"), &value["film"])?;
    Ok(value)
}

fn seed(database: &Path, stage: &Stage, source: &Value) -> Result<()> {
    require(
        !database.exists(),
        "seed database already exists; never overwrite original",
    )?;
    let service = StudioService::open(database)?;
    let mut project = service.create_project("N83 native incremental preview fixture")?;
    require(
        project.scenes.is_empty(),
        "new-project default scenes need explicit fixture handling",
    )?;
    let mut node_ids = Vec::new();
    for index in 0..3 {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("n83-seed-scene-{index}"),
                &Change::AddScene {
                    name: format!("N83 scene {}", index + 1),
                    objective: "One static rectangle for bounded incremental reconstruction"
                        .to_owned(),
                    duration_seconds: 1,
                },
            )?
            .project;
        let scene_id = project.scenes.last().ok_or("new scene not persisted")?.id;
        let node_id = Uuid::new_v4();
        let node = CanvasNode {
            id: node_id,
            name: format!("N83 rectangle {}", index + 1),
            kind: "rectangle".to_owned(),
            parent_id: None,
            x: 180.0,
            y: 100.0,
            width: 280.0,
            height: 160.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle {
                fill: Some(ORIGINAL_FILL.to_owned()),
                ..Default::default()
            },
            relations: Vec::new(),
            property_locks: BTreeSet::new(),
            keyframes: Vec::new(),
        };
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("n83-seed-node-{index}"),
                &Change::AddCanvasNode { scene_id, node },
            )?
            .project;
        node_ids.push(node_id);
    }
    let mut profile = project
        .deliverables
        .first()
        .ok_or("default preview profile missing")?
        .clone();
    profile.width = 640;
    profile.height = 360;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "n83-seed-profile",
            &Change::UpsertDeliverable { profile },
        )?
        .project;
    save_state(stage, &project, source, false)?;
    write_json(
        &stage.path.join("fixture.identity.json"),
        &json!({
            "project_id": project.id, "generation": project.generation, "before_revision": project.revision,
            "resource": project.resource_key(), "width": project.deliverables[0].width,
            "height": project.deliverables[0].height, "frame_rate": project.deliverables[0].frame_rate,
            "middle_scene_id": middle_id(&project)?, "middle_node_id": node_ids[1],
            "deliverable_id": project.deliverables[0].id, "total_frames": TOTAL_FRAMES,
            "source_window": {"start": DIRTY_START, "end": DIRTY_END},
            "fixture_only": true, "universal_frame_quota": false,
        }),
    )?;
    drop(service);
    Ok(())
}

fn edit(database: &Path, root: &Path, stage: &Stage, source: &Value) -> Result<()> {
    successful_stage(root, "before")?;
    let service = StudioService::open(database)?;
    let before = single_project(&service)?;
    preserved_state(root, "before", &state(&before, source, false)?)?;
    let identity = read_json(&root.join("seed/fixture.identity.json"))?;
    require(
        serde_json::to_value(before.id)? == identity["project_id"]
            && serde_json::to_value(before.generation)? == identity["generation"]
            && serde_json::to_value(before.revision)? == identity["before_revision"],
        "seed project binding changed",
    )?;
    let keys: Vec<RenderWindowCacheKey> =
        serde_json::from_value(read_json(&root.join("before/store-keys.original.json"))?)?;
    require(
        keys.len() == TOTAL_FRAMES as usize,
        "before Store key inventory is incomplete",
    )?;
    for (index, key) in keys.iter().enumerate() {
        require(
            key.project_id == before.id
                && key.expected == RevisionStamp::from(&before)
                && key.deliverable_id == before.deliverables[0].id
                && key.source_window
                    == (SourceFrameWindow {
                        start: index as u64,
                        end: index as u64 + 1,
                    }),
            "before Store key inventory is reordered, foreign, or incomplete",
        )?;
    }
    let mut prior_lookups = Vec::new();
    {
        let shared = service.store();
        let store = shared.lock();
        for key in &keys {
            require(
                key.expected == RevisionStamp::from(&before),
                "before cache key is not bound to the original revision",
            )?;
            prior_lookups.push(json!({"key": key, "present_before_edit": store.lookup_render_window_proxy(key)?.is_some()}));
        }
    }
    write_json(
        &stage.path.join("store-lookup-before-edit.original.json"),
        &prior_lookups,
    )?;
    require(
        prior_lookups
            .iter()
            .all(|entry| entry["present_before_edit"] == true),
        "a before proxy is absent immediately before edit; the complete stale-authority demonstration is unavailable",
    )?;
    let request = RenderTailInspectRequest {
        expected_generation: before.generation,
        expected_revision: before.revision,
        changed_scene_ids: vec![middle_id(&before)?],
        cause: RenderImpactCause::SceneVisual,
        tail_frames: 0,
        tail_source_asset_id: None,
    };
    write_json(&stage.path.join("impact-request.original.json"), &request)?;
    let impact = before.inspect_render_tail_impact(&request)?;
    let impact = serde_json::to_value(impact)?;
    write_json(&stage.path.join("impact.original.json"), &impact)?;
    require(
        impact["project_id"] == serde_json::to_value(before.id)?
            && impact["project_generation"] == serde_json::to_value(before.generation)?
            && impact["project_revision"] == json!(before.revision),
        "impact is not bound to the original project authority",
    )?;
    require(
        impact["cause"] == serde_json::to_value(RenderImpactCause::SceneVisual)?
            && impact["requested_scene_ids"] == json!([middle_id(&before)?])
            && impact["declared_audio_tail_frames"] == json!(0),
        "impact request binding differs from the actual source edit",
    )?;
    let profiles = impact
        .get("profiles")
        .and_then(Value::as_array)
        .ok_or("impact profile observations missing")?;
    let selected = profiles
        .iter()
        .filter(|profile| profile["deliverable_id"] == json!(before.deliverables[0].id))
        .collect::<Vec<_>>();
    require(
        selected.len() == 1,
        "impact must identify the original deliverable exactly once",
    )?;
    let selected = selected[0];
    require(
        selected.get("output_frame_projection_verified") == Some(&Value::Bool(false))
            && selected.get("is_scoped_selection") == Some(&Value::Bool(false))
            && selected.get("exceeds_source_duration") == Some(&Value::Bool(false)),
        "impact has an unsupported scope or claims unverified output projection",
    )?;
    require(
        selected["source_timeline_windows"] == json!([{"start": DIRTY_START, "end": DIRTY_END}])
            && selected["affected_scene_ids"] == json!([middle_id(&before)?]),
        "impact source window differs from the exact middle-scene 30..60 edit",
    )?;
    require(
        impact.get("actually_invalidated_caches") == Some(&Value::Bool(false))
            && impact.get("native_frames_regenerated_or_avoided") == Some(&Value::Null)
            && impact.get("native_renderer_saved_wallclock_micros") == Some(&Value::Null)
            && impact["technical_acceptance"]
                == "DEPENDENCY_PREFLIGHT_ONLY_NATIVE_COMPARISON_NOT_RUN",
        "dependency inspection must not claim native work, cache invalidation, or acceptance",
    )?;
    write_json(
        &stage.path.join("impact-selection-check.original.json"),
        &json!({
            "selected_profile": selected, "inspection_original": descriptor(root, &stage.path.join("impact.original.json"))?,
            "output_frame_projection_verified": false, "physical_comparison_not_yet_run": true,
            "store_invalidation_observed_separately": true, "native_savings_claimed": false,
        }),
    )?;
    write_json(&stage.path.join("project-before.original.json"), &before)?;
    let node_id = serde_json::from_value(identity["middle_node_id"].clone())?;
    let after = service
        .apply(
            before.id,
            &RevisionStamp::from(&before),
            "n83-visible-middle-fill-edit",
            &Change::UpdateCanvasStyle {
                scene_id: middle_id(&before)?,
                node_id,
                style: NodeStyle {
                    fill: Some(EDITED_FILL.to_owned()),
                    ..Default::default()
                },
            },
        )?
        .project;
    require(
        after.revision == before.revision + 1 && after.generation == before.generation,
        "edit must advance exactly one revision",
    )?;
    require(
        serde_json::to_value(&after.scenes[0])? == serde_json::to_value(&before.scenes[0])?
            && serde_json::to_value(&after.scenes[2])? == serde_json::to_value(&before.scenes[2])?,
        "edit changed an outer scene",
    )?;
    save_state(stage, &after, source, true)?;
    let store = service.store();
    let store = store.lock();
    let mut observations = Vec::new();
    for key in &keys {
        let (stale, outcome) = match store.lookup_render_window_proxy(key) {
            Ok(None) => (false, "MISSING_REVISION_CONFLICT".to_owned()),
            Err(StorageError::Conflict { expected, actual }) => (
                expected == key.expected.revision && actual == after.revision,
                format!("REVISION_CONFLICT expected={expected} actual={actual}"),
            ),
            Err(error) => (false, format!("UNEXPECTED_ERROR: {error}")),
            Ok(Some(_)) => (false, "STALE_ENTRY_RETURNED".to_owned()),
        };
        observations.push(json!({"key": key, "stale": stale, "outcome": outcome}));
    }
    write_json(
        &stage.path.join("store-stale-lookup.original.json"),
        &observations,
    )?;
    require(
        !observations.is_empty() && observations.iter().all(|v| v["stale"] == true),
        "Store reused old revision authority after edit",
    )?;
    Ok(())
}

fn successful_stage(root: &Path, name: &str) -> Result<()> {
    let terminal = read_json(&root.join(name).join("stage.terminal.json"))?;
    require(
        terminal["status"] == "COMPLETED",
        "required preceding original stage has no completed terminal",
    )
}

fn preserved_state(root: &Path, name: &str, current: &Value) -> Result<()> {
    let original = read_json(&root.join(name).join("state.original.json"))?;
    require(
        original == *current,
        "persisted project differs from the original stage binding",
    )
}

fn string_field<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("required string field missing: {key}").into())
}

fn number_field(value: &Value, key: &str) -> Result<u64> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("required unsigned field missing: {key}").into())
}

fn project_binding(project: &Project) -> Value {
    json!({"id": project.id.to_string(), "generation": project.generation})
}

/// Copy exact owner-produced bytes once. This does not create replacement native
/// evidence, reinterpret a verification verdict, or infer frames from filenames.
fn capture_original(
    root: &Path,
    stage: &Stage,
    owner_output_root: &Path,
    project: &Project,
    source: &Value,
    state: &Value,
    native_evidence: &Value,
    original_capture: &Value,
    capture_id: &str,
    role: &str,
    expected_prefix: u64,
) -> Result<Value> {
    let evidence_path = stage.path.join("native-evidence.original.json");
    require(
        read_json(&evidence_path)? == *native_evidence,
        "preserved original native evidence changed",
    )?;
    require(
        native_evidence["project_resource"] == project.resource_key()
            && native_evidence["generation"] == serde_json::to_value(project.generation)?
            && native_evidence["revision"] == json!(project.revision),
        "native result is not bound to the reopened project revision",
    )?;
    if role == "after_dirty" {
        require(
            native_evidence["request"]["deliverable_id"]
                == serde_json::to_value(project.deliverables[0].id)?
                && native_evidence["request"]["source_scene_id"]
                    == serde_json::to_value(middle_id(project)?)?
                && native_evidence["request"]["source_window"]
                    == json!({"start": DIRTY_START, "end": DIRTY_END})
                && native_evidence["film_digest"] == state["film_sha256"],
            "native dirty result is not bound to its exact Film, profile and source window",
        )?;
    } else {
        require(
            native_evidence["deliverable_id"] == serde_json::to_value(project.deliverables[0].id)?
                && original_capture["scene_ids"]
                    == json!(
                        project
                            .scenes
                            .iter()
                            .map(|scene| scene.id)
                            .collect::<Vec<_>>()
                    ),
            "native full result is not bound to the saved profile and all original scenes",
        )?;
    }
    let verification = original_capture
        .get("verification")
        .ok_or("native verification missing")?;
    write_json(&stage.path.join("verification.original.json"), verification)?;
    let artifact = original_capture
        .get("artifact")
        .ok_or("native artifact descriptor missing")?;
    write_json(
        &stage.path.join("owner-artifact-descriptor.original.json"),
        artifact,
    )?;
    let directory = string_field(artifact, "directory")?;
    require(
        !directory.is_empty()
            && Path::new(directory)
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
        "native artifact directory is not a safe relative path",
    )?;
    let manifest_path = string_field(artifact, "manifest")?;
    require(
        manifest_path == format!("{directory}/artifact-manifest.json"),
        "native manifest not bound to artifact directory",
    )?;
    require(
        number_field(artifact, "frame_count")? == expected_prefix,
        "native artifact frame count differs from physical prefix",
    )?;
    let manifest_bytes = read_verified_production_artifact(
        owner_output_root,
        manifest_path,
        string_field(artifact, "manifest_sha256")?,
        16 * 1024 * 1024,
    )?;
    write_new(
        &stage.path.join("artifact-manifest.original.json"),
        &manifest_bytes,
    )?;
    let manifest: Value = serde_json::from_slice(&manifest_bytes)?;
    let plan = manifest.get("plan").ok_or("native artifact plan missing")?;
    require(
        number_field(plan, "width")? == 640 && number_field(plan, "height")? == 360,
        "native artifact dimensions do not match fixture profile",
    )?;
    let fps_denominator = match plan.get("fps_denominator") {
        None => 1,
        Some(value) => value
            .as_u64()
            .ok_or("native frame rate denominator is not an unsigned integer")?,
    };
    require(
        number_field(plan, "fps")? == 30 && fps_denominator == 1,
        "native artifact frame rate does not match exact source clock",
    )?;
    require(
        number_field(plan, "first_frame")? == 0
            && number_field(plan, "end_frame_exclusive")? == expected_prefix
            && number_field(plan, "frame_count")? == expected_prefix,
        "native producer must retain its original first_frame zero prefix",
    )?;
    let source_frames = manifest
        .get("frames")
        .and_then(Value::as_array)
        .ok_or("native manifest frames missing")?;
    require(
        source_frames.len() == expected_prefix as usize,
        "native manifest prefix is incomplete",
    )?;
    let exposed_cost = native_evidence.get("cost");
    let exposed_timings = original_capture.get("timings");
    if role == "after_dirty" {
        let cost = exposed_cost.ok_or("original native window cost missing")?;
        require(
            cost["original_prefix_pngs"] == json!(expected_prefix)
                && cost["selected_pngs"] == json!(DIRTY_END - DIRTY_START)
                && cost["start_calls_observed"] == json!(1)
                && cost.get("internal_simulation_steps") == Some(&Value::Null),
            "native window cost differs from its original prefix or fresh start observation",
        )?;
        require(
            exposed_timings.is_some(),
            "original native window timings missing",
        )?;
    }
    let mut copied_png_payload_bytes = 0_u64;
    let mut frames = Vec::new();
    for (index, frame) in source_frames.iter().enumerate() {
        require(
            number_field(frame, "index")? == index as u64,
            "native source frame order is not exact",
        )?;
        let file = string_field(frame, "file")?;
        require(
            file == format!("frames/{index:06}.png")
                && Path::new(file)
                    .components()
                    .all(|part| matches!(part, Component::Normal(_))),
            "native frame has unsafe relative path",
        )?;
        let original_relative = format!("{directory}/{file}");
        let bytes = read_verified_production_artifact(
            owner_output_root,
            &original_relative,
            string_field(frame, "sha256")?,
            24 * 1024 * 1024,
        )?;
        require(
            bytes.len() as u64 == number_field(frame, "bytes")?,
            "native original frame size differs from manifest",
        )?;
        require(
            bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
            "native original is not a PNG",
        )?;
        let destination = stage
            .path
            .join("frames")
            .join(format!("source-{index:08}.png"));
        write_new(&destination, &bytes)?;
        copied_png_payload_bytes = copied_png_payload_bytes
            .checked_add(bytes.len() as u64)
            .ok_or("original PNG copy payload counter overflow")?;
        let mut record = descriptor(root, &destination)?;
        record["source_index"] = json!(index);
        record["owner_relative_path"] = json!(original_relative);
        frames.push(record);
    }
    write_json(
        &stage.path.join("native-prefix-cost.original.json"),
        &json!({
            "fresh_start_count": 1, "observed_original_prefix_frames": source_frames.len(),
            "first_frame": 0, "end_frame_exclusive": expected_prefix,
            "copied_original_png_payload_bytes": copied_png_payload_bytes,
            "copy_counter_scope": "Acknowledged original PNG file payload only; not physical disk I/O or transfer",
            "existing_native_window_cost": exposed_cost,
            "existing_original_capture_timings": exposed_timings,
            "missing_full_capture_timings_inferred": false,
            "internal_simulation_steps_observed": false, "internal_simulation_steps_claimed": false,
            "compilation_and_index_cost_observed": false,
            "encode_savings_measured": false, "transfer_savings_measured": false,
            "wallclock_speedup_claimed": false, "full_e03_04_accounting_complete": false,
        }),
    )?;
    let job = original_capture
        .get("job_ref")
        .ok_or("native original job reference missing")?;
    require(!job.is_null(), "native original job reference is null")?;
    let job_identity = match job.as_str() {
        Some(value) => value.to_owned(),
        None => serde_json::to_string(job)?,
    };
    require(
        !job_identity.is_empty() && job_identity.len() <= 512,
        "native job reference cannot be represented by bounded identity",
    )?;
    let captured = json!({"id": capture_id, "role": role, "source": source,
        "project": project_binding(project), "revision": project.revision,
        "film_sha256": state["film_sha256"], "dependencies_sha256": state["dependencies_sha256"],
        "job_id": job_identity, "first_frame": 0, "end_frame_exclusive": expected_prefix,
        "receipt": descriptor(root, &evidence_path)?, "frames": frames,
        "artifact_manifest": descriptor(root, &stage.path.join("artifact-manifest.original.json"))?,
        "native_verification": descriptor(root, &stage.path.join("verification.original.json"))?,
        "master_eligible": false, "acceptance_granted": false,
    });
    write_json(&stage.path.join("capture.original.json"), &captured)?;
    Ok(captured)
}

fn cache_before(
    service: &StudioService,
    project: &Project,
    root: &Path,
    stage: &Stage,
    state: &Value,
    capture: &Value,
) -> Result<()> {
    let mut keys = Vec::new();
    let mut records = Vec::new();
    let frames = capture["frames"]
        .as_array()
        .ok_or("before original frames missing")?;
    for frame in frames {
        let index = number_field(frame, "source_index")?;
        let fingerprint = json!({"project": project_binding(project), "revision": project.revision,
            "deliverable_id": project.deliverables[0].id, "source_window": {"start": index, "end": index + 1},
            "film_sha256": state["film_sha256"], "dependencies_sha256": state["dependencies_sha256"]});
        let key = RenderWindowCacheKey {
            project_id: project.id,
            expected: RevisionStamp::from(project),
            deliverable_id: project.deliverables[0].id,
            source_window: SourceFrameWindow {
                start: index,
                end: index + 1,
            },
            fingerprint_sha256: digest(&serde_json::to_vec(&fingerprint)?),
        };
        let file = root.join(string_field(frame, "path")?);
        let result = service.put_render_window_proxy_verified(
            &key,
            &file,
            &BlobDescriptor {
                sha256: string_field(frame, "sha256")?.to_owned(),
                size_bytes: number_field(frame, "bytes")?,
            },
            24 * 1024 * 1024,
            1,
        )?;
        // Keep every completed Store effect before attempting the next frame.
        write_json(
            &stage
                .path
                .join("store-admissions")
                .join(format!("frame-{index:08}.json")),
            &json!({"key": key, "record": result}),
        )?;
        let store = service.store();
        let store = store.lock();
        require(
            store.lookup_render_window_proxy(&key)?.is_some(),
            "new verified before proxy was not returned by Store",
        )?;
        records.push(json!({"key": key, "record": result}));
        keys.push(key);
    }
    write_json(&stage.path.join("store-keys.original.json"), &keys)?;
    write_json(&stage.path.join("store-admissions.original.json"), &records)?;
    Ok(())
}

async fn native_stage(
    name: &str,
    database: &Path,
    connection_path: &Path,
    root: &Path,
    stage: &Stage,
    source: &Value,
) -> Result<()> {
    let after = name != "before";
    require(
        connection_path.is_absolute(),
        "native connection file must have an absolute path",
    )?;
    successful_stage(root, if after { "edit" } else { "seed" })?;
    let service = StudioService::open(database)?;
    let project = single_project(&service)?;
    let current = save_state(stage, &project, source, after)?;
    preserved_state(root, if after { "edit" } else { "seed" }, &current)?;
    let connection = ProductionConnection::load(connection_path)?;
    let owner_output_root = connection.output_root.clone();
    write_json(
        &stage.path.join("connection-identity.original.json"),
        &json!({
            "connection_file_sha256": digest(&fs::read(connection_path)?), "owner_output_root": owner_output_root,
            "source": source, "profile": project.deliverables[0], "expected": RevisionStamp::from(&project),
        }),
    )?;
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    write_json(
        &stage.path.join("receipts-before.original.json"),
        &coordinator.receipts(project.id, 128)?,
    )?;
    let opts = options(&project)?;
    let stamp = RevisionStamp::from(&project);
    let request_id = match name {
        "before" => "n83-before-full-original",
        "dirty" => "n83-dirty-middle-original",
        "full-after" => "n83-full-after-independent-original",
        _ => return Err("unsupported native stage".into()),
    };
    let mut prior_requests = Vec::new();
    let mut request_already_exists = false;
    for suffix in ["apply", "render-start"] {
        let operation_request_id = format!("{request_id}:motion:0:{suffix}");
        let receipt = service.latest_production_receipt(project.id, &operation_request_id)?;
        request_already_exists |= receipt.is_some();
        prior_requests.push(json!({"request_id": operation_request_id, "prior_receipt": receipt}));
    }
    write_json(
        &stage.path.join("fresh-request-admission.original.json"),
        &json!({
            "request_id": request_id, "checked_before_any_native_call": true,
            "prior_requests": prior_requests, "request_already_exists": request_already_exists,
            "replay_allowed": false,
        }),
    )?;
    require(
        !request_already_exists,
        "native operation request already has an original receipt; do not replay or count old work as new",
    )?;
    let (raw, original, prefix, capture_id, role) = if name == "dirty" {
        let result = coordinator
            .render_motion_canvas_window(
                project.id,
                &stamp,
                request_id,
                &MotionCanvasWindowRequest {
                    deliverable_id: project.deliverables[0].id,
                    source_scene_id: middle_id(&project)?,
                    source_window: SourceFrameWindow {
                        start: DIRTY_START,
                        end: DIRTY_END,
                    },
                    options: opts,
                },
            )
            .await;
        let evidence = match result {
            Ok(evidence) => evidence,
            Err(error) => {
                write_json(
                    &stage.path.join("native-command-error.original.json"),
                    &json!({
                        "error": &error, "effects_absent_asserted": false, "automatic_retry": false,
                    }),
                )?;
                write_json(
                    &stage.path.join("receipts-after-error.original.json"),
                    &coordinator.receipts(project.id, 128)?,
                )?;
                return Err(error.into());
            }
        };
        let raw = serde_json::to_value(&evidence)?;
        write_json(&stage.path.join("native-evidence.original.json"), &raw)?;
        let original = raw
            .get("original_capture")
            .cloned()
            .ok_or("original window prefix capture missing")?;
        require(
            original["start_origin"] == "fresh_authenticated_call",
            "dirty original is not a fresh authenticated native start",
        )?;
        (raw, original, DIRTY_END, "dirty-original", "after_dirty")
    } else {
        let result = coordinator
            .render_motion_canvas_segments(
                project.id,
                &stamp,
                request_id,
                project.deliverables[0].id,
                &opts,
            )
            .await;
        let evidence = match result {
            Ok(evidence) => evidence,
            Err(error) => {
                write_json(
                    &stage.path.join("native-command-error.original.json"),
                    &json!({
                        "error": &error, "effects_absent_asserted": false, "automatic_retry": false,
                    }),
                )?;
                write_json(
                    &stage.path.join("receipts-after-error.original.json"),
                    &coordinator.receipts(project.id, 128)?,
                )?;
                return Err(error.into());
            }
        };
        let raw = serde_json::to_value(&evidence)?;
        write_json(&stage.path.join("native-evidence.original.json"), &raw)?;
        let segments = raw
            .get("segments")
            .and_then(Value::as_array)
            .ok_or("original native segments missing")?;
        require(
            segments.len() == 1 && number_field(&segments[0], "frame_count")? == TOTAL_FRAMES,
            "fixture requires one full original native 90-frame segment",
        )?;
        let original = segments[0].clone();
        (
            raw,
            original,
            TOTAL_FRAMES,
            if after {
                "full-after-original"
            } else {
                "before-original"
            },
            if after { "full_after" } else { "before" },
        )
    };
    // Preserve receipts independently even when later original-byte checks fail.
    write_json(
        &stage.path.join("receipts-after.original.json"),
        &coordinator.receipts(project.id, 128)?,
    )?;
    let started = service
        .latest_production_receipt(project.id, &format!("{request_id}:motion:0:render-start"))?;
    write_json(
        &stage.path.join("fresh-render-start-receipt.original.json"),
        &started,
    )?;
    require(
        started.is_some(),
        "native start has no preserved original production receipt",
    )?;
    let captured = capture_original(
        root,
        stage,
        &owner_output_root,
        &project,
        source,
        &current,
        &raw,
        &original,
        capture_id,
        role,
        prefix,
    )?;
    if name == "before" {
        cache_before(&service, &project, root, stage, &current, &captured)?;
    }
    let readback = service.project(project.id)?;
    require(
        RevisionStamp::from(&readback) == stamp,
        "native production modified project authority",
    )?;
    write_json(
        &stage.path.join("project-readback.original.json"),
        &readback,
    )?;
    drop(coordinator);
    drop(service);
    Ok(())
}

fn assemble(database: &Path, root: &Path, stage: &Stage, source: &Value) -> Result<()> {
    for name in ["seed", "before", "edit", "dirty", "full-after"] {
        successful_stage(root, name)?;
    }
    let service = StudioService::open(database)?;
    let project = single_project(&service)?;
    let current = save_state(stage, &project, source, true)?;
    preserved_state(root, "edit", &current)?;
    preserved_state(root, "dirty", &current)?;
    preserved_state(root, "full-after", &current)?;
    let before = read_json(&root.join("before/state.original.json"))?;
    let after = read_json(&root.join("edit/state.original.json"))?;
    require(
        number_field(&after, "revision")? == number_field(&before, "revision")? + 1,
        "nonconsecutive fixture edit",
    )?;
    let global_changed = before["dependencies_sha256"] != after["dependencies_sha256"];
    write_json(
        &stage.path.join("invalidation-decision.original.json"),
        &json!({
            "global_dependencies_changed": global_changed,
            "required_ranges": if global_changed { json!([{ "start_frame": 0, "end_frame_exclusive": TOTAL_FRAMES }]) }
                else { json!([{ "start_frame": DIRTY_START, "end_frame_exclusive": DIRTY_END }]) },
            "output_frame_projection_verified": false,
            "projection_authority": "Original SceneVisual inspection remains unchanged; RGBA verifier separately tests this fixture projection",
            "automatic_rerender": false,
        }),
    )?;
    require(
        !global_changed,
        "global dependencies changed: invalidate all frames; original middle-prefix run is insufficient and will not be retried",
    )?;
    let captures = ["before", "dirty", "full-after"]
        .iter()
        .map(|name| read_json(&root.join(name).join("capture.original.json")))
        .collect::<Result<Vec<_>>>()?;
    let manifest = json!({"schema": "motionwright.native-incremental-reconstruction/1", "evidence_kind": "native",
        "source": source, "project": project_binding(&project), "before": before, "after": after,
        "invalidated_ranges": [{"start_frame": DIRTY_START, "end_frame_exclusive": DIRTY_END,
            "causes": ["SceneVisual:original-middle-rectangle-fill-edit"]}],
        "captures": captures, "full_after_capture_id": "full-after-original",
        "costs": {"setup": {"before": {"rendered_frames": TOTAL_FRAMES}},
            "operation": {"rendered_frames": DIRTY_END, "encode": {"measured": false, "avoided": 0},
                "transfer": {"measured": false, "avoided": 0}},
            "validation": {"rendered_frames": TOTAL_FRAMES}},
        "impact_original": descriptor(root, &root.join("edit/impact.original.json"))?,
        "output_frame_projection_verified": false, "master_eligible": false,
        "store_authority_granted": false, "acceptance_granted": false,
    });
    write_json(
        &stage.path.join("reconstruction-input.original.json"),
        &manifest,
    )?;
    write_json(
        &stage.path.join("reconstruction-input.descriptor.json"),
        &descriptor(root, &stage.path.join("reconstruction-input.original.json"))?,
    )?;
    drop(service);
    Ok(())
}

fn same_plain_directory(observed: &Value, expected: &Path) -> Result<()> {
    let observed = Path::new(
        observed
            .as_str()
            .ok_or("reported directory is not a string")?,
    );
    for path in [observed, expected] {
        require(
            path.is_absolute()
                && !path
                    .components()
                    .any(|part| matches!(part, Component::CurDir | Component::ParentDir)),
            "preview directories must be absolute plain paths",
        )?;
        for ancestor in path.ancestors() {
            let metadata = fs::symlink_metadata(ancestor)?;
            require(
                metadata.is_dir() && !metadata.file_type().is_symlink(),
                "preview directory or ancestor is linked or not a directory",
            )?;
        }
    }
    // Windows canonicalize may add a verbatim prefix that Python resolve omits.
    // Resolve both sides only after retaining the plain-directory checks.
    require(
        fs::canonicalize(observed)? == fs::canonicalize(expected)?,
        "reported preview directory differs from its original stage directory",
    )
}

fn verify_reconstruction_originals(root: &Path, stage: &Stage, python: &Path) -> Result<Value> {
    require(
        python.is_absolute() && python.is_file(),
        "Python must be an existing absolute interpreter path",
    )?;
    let repository = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or("native crate is not inside a repository")?;
    let helper_relative = "tooling/native_incremental_reconstruction.py";
    let helper = repository.join(helper_relative);
    let helper_bytes = fs::read(&helper)?;
    let committed = Command::new("git")
        .arg("-C")
        .arg(repository)
        .args(["show", &format!("HEAD:{helper_relative}")])
        .output()?;
    require(
        committed.status.success() && committed.stdout == helper_bytes,
        "preview verifier is not the exact committed helper",
    )?;
    let input = root.join("assemble-input/reconstruction-input.original.json");
    let input_descriptor = descriptor(root, &input)?;
    require(
        read_json(&root.join("assemble-input/reconstruction-input.descriptor.json"))?
            == input_descriptor,
        "assembled original manifest changed before verification",
    )?;
    let report_path = stage.path.join("verification-report.original.json");
    let preview_path = stage.path.join("preview");
    let stdout_path = stage.path.join("verification.stdout.original.json");
    let stderr_path = stage.path.join("verification.stderr.original.txt");
    let intent = json!({
        "schema": "motionwright.native-incremental-preview-verifier-invocation/1",
        "python": python, "helper": helper, "helper_sha256": digest(&helper_bytes),
        "input": input_descriptor, "artifact_root": root, "preview_output": preview_path,
        "report": report_path, "max_frames": TOTAL_FRAMES, "timeout_seconds": 180,
        "native_render_dispatched": false, "automatic_retry": false,
    });
    write_json(
        &stage.path.join("verification.intent.original.json"),
        &intent,
    )?;
    let stdout = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&stdout_path)?;
    let stderr = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&stderr_path)?;
    let mut child = Command::new(python)
        .arg(&helper)
        .arg(&input)
        .arg("--artifact-root")
        .arg(root)
        .arg("--output-dir")
        .arg(&preview_path)
        .arg("--report")
        .arg(&report_path)
        .arg("--max-frames")
        .arg(TOTAL_FRAMES.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()?;
    let child_pid = child.id();
    let started = Instant::now();
    let (status, timed_out) = loop {
        if let Some(status) = child.try_wait()? {
            break (status, false);
        }
        if started.elapsed() >= Duration::from_secs(180) {
            child.kill()?;
            break (child.wait()?, true);
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    write_json(
        &stage.path.join("verification.terminal.original.json"),
        &json!({
            "schema": "motionwright.native-incremental-preview-verifier-terminal/1",
            "pid": child_pid, "exit_code": status.code(), "timed_out": timed_out,
            "stdout": descriptor(root, &stdout_path)?, "stderr": descriptor(root, &stderr_path)?,
            "helper_sha256": digest(&helper_bytes), "automatic_retry": false,
            "native_render_dispatched": false, "master_eligible": false,
        }),
    )?;
    require(
        !timed_out && status.success(),
        "original preview verification/materialization did not complete successfully",
    )?;
    let report = read_json(&report_path)?;
    require(
        read_json(&stdout_path)? == report,
        "verifier stdout differs from its original report",
    )?;
    require(
        report["input_manifest_sha256"] == input_descriptor["sha256"],
        "verifier report is not bound to the exact assembled manifest bytes",
    )?;
    same_plain_directory(&report["preview_output"], &preview_path)?;
    same_plain_directory(&report["artifact_root"], root)?;
    write_json(
        &stage.path.join("verification-report.descriptor.json"),
        &descriptor(root, &report_path)?,
    )?;
    Ok(report)
}

fn same_fields(actual: &Value, expected: &Value, fields: &[&str], message: &str) -> Result<()> {
    require(
        fields.iter().all(|field| {
            actual.get(*field).is_some() && actual.get(*field) == expected.get(*field)
        }),
        message,
    )
}

fn original_blob(root: &Path, record: &Value) -> Result<Vec<u8>> {
    let relative = string_field(record, "path")?;
    require(
        !relative.contains('\\')
            && Path::new(relative)
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
        "preview lineage requires a plain relative original path",
    )?;
    let bytes = read_verified_production_artifact(
        root,
        relative,
        string_field(record, "sha256")?,
        24 * 1024 * 1024,
    )?;
    require(
        bytes.len() as u64 == number_field(record, "bytes")?,
        "preview lineage original byte size changed",
    )?;
    Ok(bytes)
}

/// Build new preview keys from the current authority and preserved reconstruction
/// lineage. Older native captures keep their original revision and receipt.
fn preview_admission_plan(
    root: &Path,
    admission_path: &Path,
    project: &Project,
    source: &Value,
    current: &Value,
    report: &Value,
) -> Result<Vec<Value>> {
    require(
        report["schema"] == "motionwright.native-incremental-reconstruction-result/1"
            && report["status"] == "PREVIEW_ONLY"
            && report["result"] == "PASS_PREVIEW_RECONSTRUCTION"
            && report["evidence_kind"] == "native",
        "preview verifier did not establish this native preview reconstruction",
    )?;
    for field in [
        "master_eligible",
        "store_authority_granted",
        "native_execution_verified",
        "acceptance_granted",
    ] {
        require(
            report.get(field) == Some(&Value::Bool(false)),
            "preview report attempts to grant unverified authority",
        )?;
    }
    same_fields(
        &report["source"],
        source,
        &["motionwright_sha", "sdk_revision", "locks_sha256"],
        "preview report source binding changed",
    )?;
    require(
        report["project"] == project_binding(project)
            && report["after_revision"] == json!(project.revision),
        "preview report is stale or belongs to another project generation",
    )?;
    let input_path = root.join("assemble-input/reconstruction-input.original.json");
    let manifest = read_json(&input_path)?;
    require(
        report["input_manifest_sha256"] == descriptor(root, &input_path)?["sha256"]
            && manifest["after"] == *current
            && report["before_revision"] == manifest["before"]["revision"],
        "preview report does not bind the original before/after manifest",
    )?;
    let preview_root = admission_path.join("preview");
    same_plain_directory(&report["preview_output"], &preview_root)?;
    let report_descriptor = descriptor(
        root,
        &admission_path.join("verification-report.original.json"),
    )?;
    require(
        read_json(&admission_path.join("verification-report.descriptor.json"))?
            == report_descriptor,
        "original verifier report bytes changed",
    )?;
    let frames = report["frames"]
        .as_array()
        .ok_or("preview report frames missing")?;
    let segments = manifest["after"]["segments"]
        .as_array()
        .ok_or("after source segments missing")?;
    let captures = manifest["captures"]
        .as_array()
        .ok_or("original native captures missing")?;
    require(
        frames.len() == TOTAL_FRAMES as usize,
        "materialized preview is incomplete",
    )?;
    let mut plan = Vec::new();
    for (index, frame) in frames.iter().enumerate() {
        require(
            frame["frame_index"] == json!(index) && frame["source_index"] == json!(index),
            "preview frame order is not the original source order",
        )?;
        let selected_segments = segments
            .iter()
            .filter(|segment| {
                segment["start_frame"]
                    .as_u64()
                    .is_some_and(|start| start <= index as u64)
                    && segment["end_frame_exclusive"]
                        .as_u64()
                        .is_some_and(|end| (index as u64) < end)
            })
            .collect::<Vec<_>>();
        require(
            selected_segments.len() == 1,
            "preview frame has an ambiguous source segment",
        )?;
        let segment = selected_segments[0];
        let selected_captures = captures
            .iter()
            .filter(|capture| capture["id"] == segment["capture_id"])
            .collect::<Vec<_>>();
        require(
            selected_captures.len() == 1,
            "preview frame has an ambiguous original capture",
        )?;
        let capture = selected_captures[0];
        require(
            (capture["role"] == "before" || capture["role"] == "after_dirty")
                && frame["capture_id"] == capture["id"]
                && frame["producer_role"] == capture["role"]
                && frame["source_revision"] == capture["revision"],
            "preview frame rebound an old capture or used the comparison reference as its producer",
        )?;
        let originals = capture["frames"]
            .as_array()
            .ok_or("capture original frames missing")?;
        let original = originals
            .get(index)
            .ok_or("capture original source index missing")?;
        require(
            original["source_index"] == json!(index) && frame["relative_path"] == original["path"],
            "preview frame points to a different original source file",
        )?;
        same_fields(
            frame,
            original,
            &["sha256", "bytes"],
            "preview original file descriptor changed",
        )?;
        let lineage = frame
            .get("lineage")
            .ok_or("preview producer lineage missing")?;
        require(
            lineage["producer_job_id"] == capture["job_id"]
                && lineage["producer_film_sha256"] == capture["film_sha256"]
                && lineage["producer_dependencies_sha256"] == capture["dependencies_sha256"],
            "preview producer job, Film or dependency lineage changed",
        )?;
        same_fields(
            &lineage["producer_receipt"],
            &capture["receipt"],
            &["path", "sha256", "bytes"],
            "preview native producer receipt was rebound",
        )?;
        same_fields(
            &lineage["segment"],
            segment,
            &[
                "id",
                "start_frame",
                "end_frame_exclusive",
                "plan_sha256",
                "film_sha256",
                "dependencies_sha256",
            ],
            "preview source segment lineage changed",
        )?;
        let materialized = frame
            .get("materialized")
            .ok_or("verified materialization descriptor missing")?;
        require(
            materialized["path"] == json!(format!("frame-{index:08}.png")),
            "materialized preview frame path is not exact",
        )?;
        same_fields(
            materialized,
            original,
            &["sha256", "bytes"],
            "materialized preview is not the selected original PNG",
        )?;
        let original_bytes = original_blob(root, original)?;
        let preview_bytes = original_blob(&preview_root, materialized)?;
        require(
            preview_bytes == original_bytes,
            "materialized preview differs byte-for-byte from its selected original",
        )?;
        let _receipt_bytes = original_blob(root, &capture["receipt"])?;
        let admitted_lineage = json!({
            "schema": "motionwright.native-incremental-preview-lineage/1", "frame_index": index,
            "source": source, "project": project_binding(project), "admitted_revision": project.revision,
            "deliverable_id": project.deliverables[0].id, "film_sha256": current["film_sha256"],
            "dependencies_sha256": current["dependencies_sha256"], "report": report_descriptor,
            "input_manifest_sha256": report["input_manifest_sha256"], "original_capture_id": capture["id"],
            "original_revision": capture["revision"], "original_source_index": index,
            "original_file": original, "producer": lineage, "materialized": materialized,
            "rgba_sha256": frame["rgba_sha256"], "color_metadata_sha256": frame["color_metadata_sha256"],
            "comparison_reference": frame["comparison_reference"],
            "native_evidence_rebound": false, "master_eligible": false, "acceptance_granted": false,
        });
        let key = RenderWindowCacheKey {
            project_id: project.id,
            expected: RevisionStamp::from(project),
            deliverable_id: project.deliverables[0].id,
            source_window: SourceFrameWindow {
                start: index as u64,
                end: index as u64 + 1,
            },
            fingerprint_sha256: digest(&serde_json::to_vec(&admitted_lineage)?),
        };
        plan.push(json!({"key": key, "lineage": admitted_lineage, "blob": materialized}));
    }
    Ok(plan)
}

fn rejected_before_keys(service: &StudioService, root: &Path, project: &Project) -> Result<Value> {
    let old: Vec<RenderWindowCacheKey> =
        serde_json::from_value(read_json(&root.join("before/store-keys.original.json"))?)?;
    require(
        old.len() == TOTAL_FRAMES as usize,
        "original before Store key inventory changed",
    )?;
    let mut observations = Vec::new();
    for (index, key) in old.iter().enumerate() {
        require(
            key.project_id == project.id
                && key.expected.generation == project.generation
                && key.expected.revision.checked_add(1) == Some(project.revision)
                && key.deliverable_id == project.deliverables[0].id
                && key.source_window
                    == (SourceFrameWindow {
                        start: index as u64,
                        end: index as u64 + 1,
                    }),
            "before Store key is not the preserved previous revision",
        )?;
        let (rejected, result) = match service.lookup_render_window_proxy(key) {
            Ok(None) => (false, "MISSING_REVISION_CONFLICT".to_owned()),
            Ok(Some(_)) => (false, "STALE_BEFORE_KEY_RETURNED".to_owned()),
            Err(StorageError::Conflict { expected, actual }) => (
                expected == key.expected.revision && actual == project.revision,
                format!("REVISION_CONFLICT expected={expected} actual={actual}"),
            ),
            Err(error) => (false, format!("UNEXPECTED_ERROR: {error}")),
        };
        observations.push(json!({"key": key, "rejected": rejected, "result": result}));
    }
    Ok(
        json!({"all_rejected": observations.iter().all(|row| row["rejected"] == true), "observations": observations}),
    )
}

fn admit_preview(
    database: &Path,
    python: &Path,
    root: &Path,
    stage: &Stage,
    source: &Value,
) -> Result<()> {
    successful_stage(root, "assemble-input")?;
    let service = StudioService::open(database)?;
    let project = single_project(&service)?;
    let current = save_state(stage, &project, source, true)?;
    preserved_state(root, "assemble-input", &current)?;
    let report = verify_reconstruction_originals(root, stage, python)?;
    let plan = preview_admission_plan(root, &stage.path, &project, source, &current, &report)?;
    write_json(&stage.path.join("admission-plan.original.json"), &plan)?;
    let old_before = rejected_before_keys(&service, root, &project)?;
    write_json(
        &stage
            .path
            .join("before-keys-before-admission.original.json"),
        &old_before,
    )?;
    require(
        old_before["all_rejected"] == true,
        "old before authority remained readable before new preview admission",
    )?;
    let mut inventory = Vec::new();
    for (index, entry) in plan.iter().enumerate() {
        let key: RenderWindowCacheKey = serde_json::from_value(entry["key"].clone())?;
        require(
            service.lookup_render_window_proxy(&key)?.is_none(),
            "reconstructed preview key already exists; do not replay admission",
        )?;
        let blob = &entry["blob"];
        let expected = BlobDescriptor {
            sha256: string_field(blob, "sha256")?.to_owned(),
            size_bytes: number_field(blob, "bytes")?,
        };
        let path = stage.path.join("preview").join(string_field(blob, "path")?);
        let record = service.put_render_window_proxy_verified(
            &key,
            &path,
            &expected,
            24 * 1024 * 1024,
            1,
        )?;
        let row = json!({"key": key, "record": record, "lineage": entry["lineage"], "blob": blob,
            "preview_only": true, "native_evidence_rebound": false, "master_eligible": false});
        // Preserve each completed Service effect before the next admission.
        write_json(
            &stage
                .path
                .join("store-admissions")
                .join(format!("frame-{index:08}.json")),
            &row,
        )?;
        let hit = service
            .lookup_render_window_proxy(&key)?
            .ok_or("new after-revision preview admission is missing")?;
        require(
            hit.record == record
                && hit.record.blob_sha256 == expected.sha256
                && hit.record.size_bytes == expected.size_bytes
                && hit.validated_bytes == expected.size_bytes,
            "new preview lookup differs from its verified Service admission",
        )?;
        inventory.push(row);
    }
    write_json(
        &stage.path.join("store-admissions.original.json"),
        &inventory,
    )?;
    write_json(
        &stage.path.join("store-admissions.descriptor.json"),
        &descriptor(root, &stage.path.join("store-admissions.original.json"))?,
    )?;
    let old_after = rejected_before_keys(&service, root, &project)?;
    write_json(
        &stage.path.join("before-keys-after-admission.original.json"),
        &old_after,
    )?;
    require(
        old_after["all_rejected"] == true,
        "preview admission revived obsolete before keys",
    )?;
    require(
        state(&service.project(project.id)?, source, true)? == current,
        "preview admission modified source authority",
    )?;
    write_json(
        &stage.path.join("preview-admission.original.json"),
        &json!({
            "schema": "motionwright.native-incremental-preview-admission/1", "project": project_binding(&project),
            "revision": project.revision, "source": source, "admitted_frames": inventory.len(),
            "admission": "existing StudioService::put_render_window_proxy_verified",
            "native_render_dispatched": false, "native_evidence_rebound": false,
            "master_eligible": false, "acceptance_granted": false,
        }),
    )?;
    drop(service);
    Ok(())
}

fn reopen_preview(database: &Path, root: &Path, stage: &Stage, source: &Value) -> Result<()> {
    successful_stage(root, "admit-preview")?;
    let admission_path = root.join("admit-preview");
    let admission_started = read_json(&admission_path.join("stage.started.json"))?;
    require(
        admission_started["pid"] != json!(std::process::id()),
        "preview readback requires a separate process invocation",
    )?;
    let service = StudioService::open(database)?;
    let project = single_project(&service)?;
    let current = save_state(stage, &project, source, true)?;
    preserved_state(root, "admit-preview", &current)?;
    let report = read_json(&admission_path.join("verification-report.original.json"))?;
    let plan = preview_admission_plan(root, &admission_path, &project, source, &current, &report)?;
    require(
        read_json(&admission_path.join("admission-plan.original.json"))? == json!(plan),
        "reconstructed preview lineage or keys changed after Store handle closure",
    )?;
    let inventory_path = admission_path.join("store-admissions.original.json");
    require(
        read_json(&admission_path.join("store-admissions.descriptor.json"))?
            == descriptor(root, &inventory_path)?,
        "original Service admission inventory changed",
    )?;
    let inventory = read_json(&inventory_path)?;
    let inventory = inventory
        .as_array()
        .ok_or("Service admission inventory is not an array")?;
    require(
        inventory.len() == plan.len(),
        "Service admission inventory is incomplete",
    )?;
    let mut readback = Vec::new();
    for (index, (entry, original)) in plan.iter().zip(inventory).enumerate() {
        same_fields(
            original,
            entry,
            &["key", "lineage", "blob"],
            "Service admission lineage inventory changed",
        )?;
        let key: RenderWindowCacheKey = serde_json::from_value(entry["key"].clone())?;
        let hit = service
            .lookup_render_window_proxy(&key)?
            .ok_or("after-revision preview key is absent after independent reopen")?;
        require(
            serde_json::to_value(&hit.record)? == original["record"],
            "reopened preview returns a different original cache receipt",
        )?;
        let blob = &entry["blob"];
        require(
            hit.record.blob_sha256 == string_field(blob, "sha256")?
                && hit.record.size_bytes == number_field(blob, "bytes")?
                && hit.validated_bytes == number_field(blob, "bytes")?,
            "reopened preview cache hit differs from the original bytes",
        )?;
        let bytes = {
            let shared = service.store();
            let store = shared.lock();
            store.read_blob(&hit.record.blob_sha256, 24 * 1024 * 1024)?
        };
        let materialized = original_blob(&admission_path.join("preview"), blob)?;
        require(
            bytes == materialized,
            "Store readback bytes differ from the originally materialized preview",
        )?;
        let readback_path = stage
            .path
            .join("frames")
            .join(format!("frame-{index:08}.png"));
        write_new(&readback_path, &bytes)?;
        let row = json!({"key": key, "record": hit.record, "validated_bytes": hit.validated_bytes,
            "lineage": entry["lineage"], "readback": descriptor(root, &readback_path)?,
            "same_original_bytes": true, "master_eligible": false});
        write_json(
            &stage
                .path
                .join("store-readbacks")
                .join(format!("frame-{index:08}.json")),
            &row,
        )?;
        readback.push(row);
    }
    let old = rejected_before_keys(&service, root, &project)?;
    write_json(
        &stage.path.join("before-keys-after-reopen.original.json"),
        &old,
    )?;
    require(
        old["all_rejected"] == true,
        "independent reopen revived obsolete before authority",
    )?;
    require(
        state(&service.project(project.id)?, source, true)? == current,
        "preview readback modified source authority",
    )?;
    write_json(&stage.path.join("store-readbacks.original.json"), &readback)?;
    write_json(
        &stage.path.join("preview-reopen.original.json"),
        &json!({
            "schema": "motionwright.native-incremental-preview-reopen/1", "project": project_binding(&project),
            "revision": project.revision, "source": source, "verified_frames": readback.len(),
            "admission_pid": admission_started["pid"], "readback_pid": std::process::id(),
            "same_original_bytes": true, "old_before_keys_rejected": true,
            "new_admissions_performed": false, "native_render_dispatched": false,
            "native_evidence_rebound": false, "master_eligible": false, "acceptance_granted": false,
        }),
    )?;
    drop(service);
    Ok(())
}

fn n83_source_scene_at_frame_30fps(project: &Project, frame: u64) -> Result<Uuid> {
    let mut matches = Vec::new();
    for scene in &project.scenes {
        require(
            scene.start.den > 0 && scene.duration.den > 0,
            "nonpositive source-time denominator",
        )?;
        let end = scene
            .start
            .checked_add(scene.duration)
            .map_err(|error| format!("source interval overflow: {error}"))?;
        require(end.den > 0, "nonpositive source-end denominator")?;
        let at_or_after_start =
            i128::from(frame) * i128::from(scene.start.den) >= i128::from(scene.start.num) * 30;
        let before_end = i128::from(frame) * i128::from(end.den) < i128::from(end.num) * 30;
        if at_or_after_start && before_end {
            matches.push(scene.id);
        }
    }
    require(
        matches.len() == 1,
        "source probe is not in exactly one scene",
    )?;
    Ok(matches[0])
}

fn n83_time_ripple_impact(
    project: &Project,
    changed_scene: Uuid,
    end_frame: u64,
    stage: &Stage,
    label: &str,
) -> Result<Value> {
    let request = RenderTailInspectRequest {
        expected_generation: project.generation,
        expected_revision: project.revision,
        changed_scene_ids: vec![changed_scene],
        cause: RenderImpactCause::TimeRipple,
        tail_frames: 0,
        tail_source_asset_id: None,
    };
    let impact = project.inspect_render_tail_impact(&request)?;
    write_json(
        &stage.path.join(format!("{label}.request.original.json")),
        &request,
    )?;
    write_json(
        &stage.path.join(format!("{label}.impact.original.json")),
        &impact,
    )?;
    let profiles = impact
        .profiles
        .iter()
        .filter(|profile| profile.deliverable_id == project.deliverables[0].id)
        .collect::<Vec<_>>();
    require(
        profiles.len() == 1,
        "time-ripple profile binding is not unique",
    )?;
    let profile = profiles[0];
    require(
        profile.source_timeline_windows
            == vec![SourceFrameWindow {
                start: 30,
                end: end_frame,
            }]
            && profile.affected_scene_ids == vec![project.scenes[1].id, project.scenes[2].id],
        "time-ripple closure does not cover the real downstream scene",
    )?;
    require(
        !profile.is_scoped_selection
            && !profile.exceeds_source_duration
            && !profile.output_frame_projection_verified,
        "time-ripple preflight must not claim physical output projection",
    )?;
    require(
        !impact.actually_invalidated_caches
            && impact.native_frames_regenerated_or_avoided.is_none()
            && impact.native_renderer_saved_wallclock_micros.is_none(),
        "read-only preflight cannot claim Store effects or native work",
    )?;
    Ok(serde_json::to_value(impact)?)
}

fn n83_assert_stale_and_unadmitted(
    service: &StudioService,
    before: &Project,
    after: &Project,
    admitted_keys: &[RenderWindowCacheKey],
    stage: &Stage,
    label: &str,
) -> Result<()> {
    let mut rows = Vec::new();
    for key in admitted_keys {
        let stale_kind = match service.lookup_render_window_proxy(key) {
            Err(motionwright_storage::StorageError::Conflict { expected, actual })
                if expected == before.revision && actual == after.revision =>
            {
                "EXPECTED_REVISION_CONFLICT"
            }
            Err(error) => return Err(format!("unexpected stale-key error: {error}").into()),
            Ok(_) => return Err("old revision lookup did not reject stale authority".into()),
        };
        // Negative lookup only. This does not publish, relabel, or authorize
        // the old fingerprint for the new Project revision.
        let mut new_key = key.clone();
        new_key.expected = RevisionStamp::from(after);
        require(
            service.lookup_render_window_proxy(&new_key)?.is_none(),
            "new revision unexpectedly inherited an unadmitted old fingerprint",
        )?;
        rows.push(json!({
            "source_window": key.source_window,
            "old_revision": before.revision,
            "current_revision": after.revision,
            "old_key_outcome": stale_kind,
            "current_key_with_old_fingerprint": "MISS",
            "new_admission_attempted": false,
        }));
    }
    write_json(
        &stage.path.join(format!("{label}.lookup-originals.json")),
        &rows,
    )?;
    Ok(())
}

fn n83_time_ripple_negative(
    database: &Path,
    stage: &Stage,
    source: &Value,
    admitted_keys: &[RenderWindowCacheKey],
) -> Result<()> {
    // Caller must have completed the positive same-source reconstruction,
    // current-revision admission, process close, and independent readback.
    let service = StudioService::open(database)?;
    let before = single_project(&service)?;
    let before_state = state(&before, source, true)?;
    let evidence_root = stage
        .path
        .parent()
        .ok_or("negative stage has no evidence root")?;
    preserved_state(evidence_root, "reopen-preview", &before_state)?;
    require(
        admitted_keys.len() == 90,
        "negative control requires all90 admitted preview keys",
    )?;
    let mut positive_hits = Vec::new();
    for (index, key) in admitted_keys.iter().enumerate() {
        require(
            key.project_id == before.id
                && key.expected == RevisionStamp::from(&before)
                && key.deliverable_id == before.deliverables[0].id
                && key.source_window
                    == (SourceFrameWindow {
                        start: index as u64,
                        end: index as u64 + 1,
                    }),
            "admitted keys are incomplete, reordered, or not the positive after revision",
        )?;
        require(
            service.lookup_render_window_proxy(key)?.is_some(),
            "positive after proxy was not present before the duration mutation",
        )?;
        positive_hits.push(json!({"key": key, "present": true}));
    }
    write_json(&stage.path.join("before.project.original.json"), &before)?;
    write_json(
        &stage.path.join("before.state.original.json"),
        &before_state,
    )?;
    write_json(
        &stage.path.join("before.positive-cache-hits.original.json"),
        &positive_hits,
    )?;
    n83_time_ripple_impact(&before, before.scenes[1].id, 90, stage, "before")?;

    // A real existing Change: no digest mutation, arbitrary profile edit,
    // synthetic audio tail, or GlobalMix label.
    let duration = before.scenes[1]
        .duration
        .checked_add(before.scenes[0].duration)
        .map_err(|error| format!("duration overflow: {error}"))?;
    let change = Change::SetSceneDuration {
        scene_id: before.scenes[1].id,
        duration,
    };
    write_json(&stage.path.join("duration-change.original.json"), &change)?;
    let after = service
        .apply(
            before.id,
            &RevisionStamp::from(&before),
            "n83-terminal-time-ripple-negative-once",
            &change,
        )?
        .project;
    // Preserve the completed mutation before any later assertion.
    write_json(&stage.path.join("after.project.original.json"), &after)?;
    require(
        after.id == before.id
            && after.generation == before.generation
            && after.revision == before.revision + 1,
        "duration edit did not create exactly the expected next revision",
    )?;
    require(
        after.scenes.len() == 3
            && after
                .scenes
                .iter()
                .map(|scene| scene.id)
                .collect::<Vec<_>>()
                == before
                    .scenes
                    .iter()
                    .map(|scene| scene.id)
                    .collect::<Vec<_>>(),
        "duration edit changed scene identity/order",
    )?;
    let mut expected_middle = before.scenes[1].clone();
    expected_middle.duration = duration;
    let mut expected_third = before.scenes[2].clone();
    expected_third.start = expected_third
        .start
        .checked_add(before.scenes[0].duration)
        .map_err(|error| format!("downstream start overflow: {error}"))?;
    require(
        serde_json::to_value(&after.scenes[0])? == serde_json::to_value(&before.scenes[0])?
            && serde_json::to_value(&after.scenes[1])? == serde_json::to_value(&expected_middle)?
            && serde_json::to_value(&after.scenes[2])? == serde_json::to_value(&expected_third)?,
        "actual mutation differs from duration plus downstream-start ripple",
    )?;
    n83_time_ripple_impact(&after, after.scenes[1].id, 120, stage, "after")?;

    // Independent source-time witness, outside the original visible[30,60).
    // At2.5s/frame75 the active source scene changes third→middle.
    let probe = 75;
    let before_scene = n83_source_scene_at_frame_30fps(&before, probe)?;
    let after_scene = n83_source_scene_at_frame_30fps(&after, probe)?;
    require(
        before_scene == before.scenes[2].id && after_scene == after.scenes[1].id,
        "duration mutation did not change downstream source-time ownership",
    )?;
    write_json(
        &stage
            .path
            .join("outside-visible-window.model-witness.original.json"),
        &json!({
            "frame": probe, "frame_rate": {"num":30,"den":1},
            "original_visible_window": {"start":30,"end":60},
            "before_source_scene": before_scene,
            "after_source_scene": after_scene,
            "before_third_start": before.scenes[2].start,
            "after_third_start": after.scenes[2].start,
            "source_model_change_observed": true,
            "new_pixels_rendered_or_compared": false,
        }),
    )?;
    n83_assert_stale_and_unadmitted(
        &service,
        &before,
        &after,
        admitted_keys,
        stage,
        "after-mutation",
    )?;
    drop(service);

    let reopened = StudioService::open(database)?;
    let readback = single_project(&reopened)?;
    require(
        serde_json::to_value(&readback)? == serde_json::to_value(&after)?,
        "Store handle reopen returned a duration change different from saved source",
    )?;
    n83_assert_stale_and_unadmitted(
        &reopened,
        &before,
        &readback,
        admitted_keys,
        stage,
        "store-handle-reopen",
    )?;
    write_json(
        &stage.path.join("model-store-negative.result.json"),
        &json!({
            "schema":"motionwright.n83.time-ripple-negative/1",
            "scope":"MODEL_AND_STORE_INVALIDATION_ONLY",
            "source":source,"project_id":after.id,
            "generation":after.generation,
            "before_revision":before.revision,"after_revision":after.revision,
            "positive_before_mutation_cache_hits":positive_hits.len(),
            "outside_visible_probe":75,
            "old_stamp_rejected_after_mutation_and_reopen":true,
            "new_stamp_old_fingerprint_not_admitted":true,
            "conservative_scope":true,
            "fine_grained_cache_retention_claimed":false,
            "native_calls":0,"new_rendered_frames":0,
            "new_encode_calls":0,"new_transfer_calls":0,
            "fourth_native_call_or_fallback":false,
            "ripple_native_output":"NOT_RUN",
            "master_eligible":false,"acceptance_granted":false,
        }),
    )?;
    drop(reopened);
    Ok(())
}

fn dependency_negative(database: &Path, root: &Path, stage: &Stage, source: &Value) -> Result<()> {
    successful_stage(root, "reopen-preview")?;
    let inventory_path = root.join("admit-preview/store-admissions.original.json");
    require(
        read_json(&root.join("admit-preview/store-admissions.descriptor.json"))?
            == descriptor(root, &inventory_path)?,
        "negative control admission inventory changed",
    )?;
    let inventory = read_json(&inventory_path)?;
    let rows = inventory
        .as_array()
        .ok_or("negative control admission inventory is not an array")?;
    let keys = rows
        .iter()
        .map(|row| {
            serde_json::from_value(row["key"].clone())
                .map_err(|error| -> Box<dyn Error + Send + Sync> { Box::new(error) })
        })
        .collect::<Result<Vec<RenderWindowCacheKey>>>()?;
    n83_time_ripple_negative(database, stage, source, &keys)
}

#[tokio::main]
async fn main() -> Result<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    let name = arguments.first().map(String::as_str).ok_or(
        "usage: native-incremental-e2e seed|edit|assemble-input|reopen-preview|dependency-negative DB EVIDENCE_DIR; before|dirty|full-after DB CONNECTION EVIDENCE_DIR; admit-preview DB PYTHON EVIDENCE_DIR")?;
    let native = matches!(name, "before" | "dirty" | "full-after");
    let auxiliary_argument = native || name == "admit-preview";
    require(
        matches!(
            name,
            "seed"
                | "before"
                | "edit"
                | "dirty"
                | "full-after"
                | "assemble-input"
                | "admit-preview"
                | "reopen-preview"
                | "dependency-negative"
        ),
        "unknown original stage",
    )?;
    require(
        arguments.len() == if auxiliary_argument { 4 } else { 3 },
        "wrong argument count",
    )?;
    let database = PathBuf::from(&arguments[1]);
    let root = PathBuf::from(&arguments[if auxiliary_argument { 3 } else { 2 }]);
    require(
        database.is_absolute() && root.is_absolute(),
        "database and evidence directory must be absolute",
    )?;
    let source = source_identity()?;
    let mut stage = Stage::start(&root, name, &database, &source)?;
    let result = match name {
        "seed" => seed(&database, &stage, &source),
        "edit" => edit(&database, &root, &stage, &source),
        "assemble-input" => assemble(&database, &root, &stage, &source),
        "admit-preview" => {
            admit_preview(&database, Path::new(&arguments[2]), &root, &stage, &source)
        }
        "reopen-preview" => reopen_preview(&database, &root, &stage, &source),
        "dependency-negative" => dependency_negative(&database, &root, &stage, &source),
        _ => {
            native_stage(
                name,
                &database,
                Path::new(&arguments[2]),
                &root,
                &stage,
                &source,
            )
            .await
        }
    };
    match result {
        Ok(()) => stage.complete(&source),
        Err(error) => {
            let text = error.to_string();
            if let Err(preservation) = stage.uncertain(&text) {
                eprintln!(
                    "Original stage remains UNKNOWN; terminal preservation failed: {preservation}"
                );
            }
            Err(error)
        }
    }
}

#[cfg(test)]
mod source_controls {
    use super::*;

    /// Exercises the real Store, source edit and canonical Film planner without
    /// creating a Broker session or dispatching a native producer. This catches
    /// a fixture that would fail before its first useful physical comparison.
    #[test]
    fn seed_edit_reopen_preserves_outer_scenes_and_shared_dependencies() {
        let temporary = tempfile::tempdir().expect("source-control directory");
        let database = temporary.path().join("source-control.sqlite3");
        let path = temporary.path().join("source-control-originals");
        fs::create_dir(&path).expect("source-control originals");
        let stage = Stage {
            path,
            finished: true,
        };
        let source = json!({"scope": "synthetic-source-control-no-native-admission"});
        seed(&database, &stage, &source).expect("real source fixture must be plannable");

        let service = StudioService::open(&database).expect("independent Store reopen");
        let before = single_project(&service).expect("one preserved source project");
        let old_state = state(&before, &source, false).expect("before canonical Film");
        let middle = &before.scenes[1];
        let after = service
            .apply(
                before.id,
                &RevisionStamp::from(&before),
                "n83-source-control-fill-edit",
                &Change::UpdateCanvasStyle {
                    scene_id: middle.id,
                    node_id: middle.nodes[0].id,
                    style: NodeStyle {
                        fill: Some(EDITED_FILL.to_owned()),
                        ..Default::default()
                    },
                },
            )
            .expect("one genuine source edit")
            .project;
        let new_state = state(&after, &source, true).expect("after canonical Film");
        assert_eq!(after.revision, before.revision + 1);
        assert_eq!(after.generation, before.generation);
        assert_ne!(old_state["film_sha256"], new_state["film_sha256"]);
        assert_eq!(
            old_state["dependencies_sha256"],
            new_state["dependencies_sha256"]
        );
        assert_eq!(old_state["segments"][0], new_state["segments"][0]);
        assert_eq!(old_state["segments"][2], new_state["segments"][2]);
        assert_ne!(
            old_state["segments"][1]["plan_sha256"],
            new_state["segments"][1]["plan_sha256"]
        );
        drop(service);

        let reopened = StudioService::open(&database).expect("after-edit Store reopen");
        let persisted = single_project(&reopened).expect("same project after Store handle closure");
        assert_eq!(
            state(&persisted, &source, true).expect("persisted canonical Film"),
            new_state
        );
    }
}

#[cfg(test)]
mod n83_time_ripple_domain_controls {
    use super::*;

    fn model() -> Project {
        let mut project = Project::new("N83 real duration dependency").unwrap();
        for index in 0..3 {
            project
                .apply_change(&Change::AddScene {
                    name: format!("scene-{index}"),
                    objective: "bounded dependency control".into(),
                    duration_seconds: 1,
                })
                .unwrap();
        }
        project
    }

    fn report(project: &Project, cause: RenderImpactCause) -> Value {
        serde_json::to_value(
            project
                .inspect_render_tail_impact(&RenderTailInspectRequest {
                    expected_generation: project.generation,
                    expected_revision: project.revision,
                    changed_scene_ids: vec![project.scenes[1].id],
                    cause,
                    tail_frames: 0,
                    tail_source_asset_id: None,
                })
                .unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn actual_duration_ripple_moves_frame75_outside_original_visible_window() {
        let mut project = model();
        let before = project.clone();
        let duration = before.scenes[1]
            .duration
            .checked_add(before.scenes[0].duration)
            .unwrap();
        project
            .apply_change(&Change::SetSceneDuration {
                scene_id: before.scenes[1].id,
                duration,
            })
            .unwrap();
        assert_eq!(
            n83_source_scene_at_frame_30fps(&before, 75).unwrap(),
            before.scenes[2].id
        );
        assert_eq!(
            n83_source_scene_at_frame_30fps(&project, 75).unwrap(),
            before.scenes[1].id
        );
        assert_eq!(
            project.scenes[2].start,
            before.scenes[2]
                .start
                .checked_add(before.scenes[0].duration)
                .unwrap()
        );
        let result = report(&project, RenderImpactCause::TimeRipple);
        let profile = result["profiles"]
            .as_array()
            .unwrap()
            .iter()
            .find(|p| p["deliverable_id"] == json!(project.deliverables[0].id))
            .unwrap();
        assert_eq!(
            profile["source_timeline_windows"],
            json!([{"start":30,"end":120}])
        );
        assert_eq!(
            profile["affected_scene_ids"],
            json!([project.scenes[1].id, project.scenes[2].id])
        );
        assert_eq!(profile["output_frame_projection_verified"], json!(false));
        assert_eq!(result["actually_invalidated_caches"], json!(false));
        assert_eq!(result["native_frames_regenerated_or_avoided"], Value::Null);
    }

    #[test]
    fn visual_only_window_cannot_stand_for_real_time_ripple_dependency() {
        let project = model();
        let visual = report(&project, RenderImpactCause::SceneVisual);
        let ripple = report(&project, RenderImpactCause::TimeRipple);
        let id = json!(project.deliverables[0].id);
        let select = |report: &Value| -> Value {
            report["profiles"]
                .as_array()
                .unwrap()
                .iter()
                .find(|p| p["deliverable_id"] == id)
                .unwrap()
                .clone()
        };
        assert_eq!(
            select(&visual)["source_timeline_windows"],
            json!([{"start":30,"end":60}])
        );
        assert_eq!(
            select(&ripple)["source_timeline_windows"],
            json!([{"start":30,"end":90}])
        );
        assert_eq!(
            select(&ripple)["affected_scene_ids"],
            json!([project.scenes[1].id, project.scenes[2].id])
        );
    }
}
