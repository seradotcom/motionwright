use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, FontRight, NodeStyle, OutputColorSpace,
    OutputContainer, RationalTime, RendererKind, RevisionStamp, RightsStatus, VideoCodec,
    VisualToken,
};
use motionwright_manim_profile::ManimRenderProfile;
use motionwright_native::production::{
    ManimRuntimeVersionState, ProductionConnection, ProductionCoordinator,
};
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

#[path = "native_manim_editable/source_cycle.rs"]
mod editable_cycle;
#[path = "native_manim_graph/source_cycle.rs"]
mod graph_cycle;

fn usage() -> ! {
    eprintln!(
        "usage: native-manim-e2e <seed-graph-cycle DATABASE REPORT_JSON | graph-cycle-edit-prepare DATABASE ORIGINAL_NATIVE OUTPUT_ROOT REPORT_JSON REQUEST_ID | graph-cycle-save-readback DATABASE PENDING_JSON OWNER_TERMINAL_JSON REPORT_JSON | realize-graph DATABASE CONNECTION EVIDENCE_JSON SAVED_JSON REQUEST_ID | realize-graph-bundle DATABASE CONNECTION EVIDENCE_JSON SAVED_JSON INPUT_DESCRIPTOR_JSON REQUEST_ID | seed-graph-profile DATABASE | observe-graph-profile DATABASE CONNECTION SEED_JSON EVIDENCE_JSON | seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON [REQUEST_ID] | realize-bundle DATABASE CONNECTION EVIDENCE_JSON INPUT_DESCRIPTOR_JSON REQUEST_ID | source-cycle-edit DATABASE ORIGINAL_NATIVE OUTPUT_ROOT REPORT REQUEST_ID | source-cycle-readback DATABASE EDIT_REPORT CURRENT_NATIVE OUTPUT_ROOT REPORT | realize-camera DATABASE CONNECTION EVIDENCE_JSON PROJECT_ID SCENE_ID DELIVERABLE_ID REQUEST_ID>"
    );
    std::process::exit(2);
}

fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

struct NodeFixture<'a> {
    kind: &'a str,
    name: &'a str,
    text: Option<&'a str>,
    position: (f64, f64),
    size: (f64, f64),
    fill: &'a str,
}

fn node(fixture: NodeFixture<'_>) -> CanvasNode {
    let (x, y) = fixture.position;
    let (width, height) = fixture.size;
    CanvasNode {
        id: Uuid::now_v7(),
        name: fixture.name.into(),
        kind: fixture.kind.into(),
        parent_id: None,
        x,
        y,
        width,
        height,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: fixture.text.map(str::to_owned),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(fixture.fill.into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: (fixture.kind == "text").then(|| "Montserrat".into()),
            font_size: (fixture.kind == "text").then_some(42.0),
            font_weight: (fixture.kind == "text").then_some(700),
            line_height: (fixture.kind == "text").then_some(1.0),
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

const EDITABLE_FONT: &[u8] =
    include_bytes!("../../manim-profile/tests/fixtures/Montserrat-variable.ttf");
const EDITABLE_FONT_LICENSE: &[u8] =
    include_bytes!("../../manim-profile/tests/fixtures/Montserrat-OFL.txt");
const EDITABLE_FONT_ORIGIN: &[u8] =
    include_bytes!("../../manim-profile/tests/fixtures/Montserrat-source.json");
const EDITABLE_FONT_SHA256: &str =
    "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7";

fn retain_seed_fixture(path: &Path, bytes: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    if std::fs::read(path)? != bytes {
        return Err("licensed font fixture changed during exclusive retention".into());
    }
    Ok(())
}

fn seed(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("native-manim-e2e seed database must start empty".into());
    }

    let initial = service.create_project("Native Manim Community round-trip E2E")?;
    let with_scene = service
        .apply(
            initial.id,
            &RevisionStamp::from(&initial),
            "native-manim-seed-scene",
            &Change::AddScene {
                name: "Verified mathematical explainer".into(),
                objective: "Render typed Motionwright primitives through Semwright Driver Host"
                    .into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = with_scene.scenes[0].id;
    let with_renderer = service
        .apply(
            with_scene.id,
            &RevisionStamp::from(&with_scene),
            "native-manim-set-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::ManimCommunity,
            },
        )?
        .project;

    let mut project = with_renderer;
    if hex::encode(Sha256::digest(EDITABLE_FONT)) != EDITABLE_FONT_SHA256
        || hex::encode(Sha256::digest(EDITABLE_FONT_LICENSE))
            != "8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e"
        || hex::encode(Sha256::digest(EDITABLE_FONT_ORIGIN))
            != "2656ac500e19cee1a35c1293985c33741c9f1e030b458bdff967a4a02d2f8ab0"
    {
        return Err("pinned Montserrat font, OFL or origin identity changed".into());
    }
    let font_path = database.with_extension("Montserrat-variable.ttf");
    retain_seed_fixture(&font_path, EDITABLE_FONT)?;
    retain_seed_fixture(
        &database.with_extension("Montserrat-OFL.txt"),
        EDITABLE_FONT_LICENSE,
    )?;
    retain_seed_fixture(
        &database.with_extension("Montserrat-source.json"),
        EDITABLE_FONT_ORIGIN,
    )?;
    project = service
        .import_asset_file(
            project.id,
            &RevisionStamp::from(&project),
            "native-manim-seed-font",
            &font_path,
            "Pinned Montserrat variable".into(),
            "font/ttf".into(),
        )?
        .project;
    let font_asset_id = project
        .assets
        .iter()
        .find(|asset| {
            asset.name == "Pinned Montserrat variable"
                && asset.content_sha256.as_deref() == Some(EDITABLE_FONT_SHA256)
                && asset.media_type == "font/ttf"
        })
        .ok_or("licensed native font was not retained in Project/CAS")?
        .id;
    let mut language = project.visual_language.clone();
    language
        .palette
        .retain(|row| !row.name.eq_ignore_ascii_case("background"));
    language.palette.push(VisualToken {
        name: "background".into(),
        value: "#153047".into(),
    });
    language.fonts.push(FontRight {
        family: "Montserrat".into(),
        source: format!("asset:{font_asset_id}"),
        rights_status: RightsStatus::Cleared,
    });
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-manim-seed-licensed-font",
            &Change::SetVisualLanguage {
                visual_language: language,
            },
        )?
        .project;

    let fixtures = [
        node(NodeFixture {
            kind: "rectangle",
            name: "Equation panel",
            text: None,
            position: (260.0, 180.0),
            size: (720.0, 300.0),
            fill: "#4F7CAC",
        }),
        node(NodeFixture {
            kind: "circle",
            name: "Focus point",
            text: None,
            position: (1120.0, 390.0),
            size: (200.0, 200.0),
            fill: "#F5B84A",
        }),
        node(NodeFixture {
            kind: "text",
            name: "Equation label",
            text: Some("x² + y² = r²"),
            position: (620.0, 720.0),
            size: (520.0, 120.0),
            fill: "#FFFFFF",
        }),
    ];
    for (index, fixture) in fixtures.into_iter().enumerate() {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-manim-seed-node-{index}"),
                &Change::AddCanvasNode {
                    scene_id,
                    node: fixture,
                },
            )?
            .project;
    }

    let mut output = project
        .deliverables
        .first()
        .ok_or("seed project has no saved deliverable")?
        .clone();
    output.width = 640;
    output.height = 360;
    output.frame_rate = RationalTime::new(12, 1)?;
    output.container = OutputContainer::Mp4;
    output.video_codec = VideoCodec::H264;
    output.color_space = OutputColorSpace::Rec709;
    output.captions = false;
    output.burn_in_captions = false;
    output.included_scene_ids = vec![scene_id];
    let deliverable_id = output.id;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-manim-seed-deliverable",
            &Change::UpsertDeliverable { profile: output },
        )?
        .project;
    let prepared =
        motionwright_native::manim_editable_projection::prepare_saved_manim_editable_source(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            scene_id,
            deliverable_id,
        )?;
    if prepared.plan.clock.frame_count != 24
        || prepared.plan.fonts.len() != 1
        || prepared.plan.fonts[0].sha256 != EDITABLE_FONT_SHA256
    {
        return Err("modern seed failed the actual editable-source admission".into());
    }

    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id": project.id,
            "resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "scene_id": scene_id,
            "primitive_count": 3,
            "deliverable_id": deliverable_id,
            "font_asset_id": font_asset_id,
            "font_sha256": EDITABLE_FONT_SHA256,
            "font_license_sha256": hex::encode(Sha256::digest(EDITABLE_FONT_LICENSE)),
            "plan_sha256": prepared.closure.plan_sha256,
            "frame_count": prepared.plan.clock.frame_count,
            "native_execution_performed": false
        }))?
    );
    Ok(())
}

const GRAPH_PROFILE_SEED_SCHEMA: &str = "motionwright.manim.graph-profile-seed/1";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphProfileSeed {
    schema: String,
    project_id: Uuid,
    resource: String,
    source_snapshot: motionwright_domain::GraphSourceSnapshot,
    scene_id: Uuid,
    deliverable_id: Uuid,
    asset_pins: serde_json::Value,
    helper_pins: serde_json::Value,
    graph_saved: bool,
    native_execution_performed: bool,
    profile_admitted: bool,
}

fn graph_profile_helper_pins() -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let schema = motionwright_manim_profile::semantic_graph_profile_probe::
        fixed_graph_profile_observation_schema_v1();
    let pins = schema
        .pointer("/properties/helper_pins/const")
        .and_then(serde_json::Value::as_array)
        .filter(|pins| pins.len() == 6)
        .ok_or("Compiler Graph helper closure is absent")?;
    Ok(json!(pins))
}

fn check_graph_profile_seed(
    project: &motionwright_domain::Project,
    seed: &GraphProfileSeed,
) -> Result<(), Box<dyn std::error::Error>> {
    if seed.schema != GRAPH_PROFILE_SEED_SCHEMA
        || seed.project_id != project.id
        || seed.resource != project.resource_key()
        || seed.source_snapshot != motionwright_domain::GraphSourceSnapshot::from_project(project)
        || seed.graph_saved
        || seed.native_execution_performed
        || seed.profile_admitted
        || !project.production_design.semantic_graphs.is_empty()
        || seed.helper_pins != graph_profile_helper_pins()?
        || seed.asset_pins
            != serde_json::to_value(
                motionwright_manim_profile::semantic_graph_source::graph_builtin_asset_pins(),
            )?
    {
        return Err("Graph profile seed is not the original current source selection".into());
    }
    motionwright_native::manim_graph_projection::validate_manim_semantic_graph_scene_output(
        project,
        &seed.source_snapshot,
        seed.scene_id,
        seed.deliverable_id,
    )?;
    Ok(())
}

fn create_graph_profile_seed(
    database: &Path,
) -> Result<GraphProfileSeed, Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Graph profile seed database must start empty".into());
    }
    let mut project = service.create_project("Initial native Graph profile observation")?;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "graph-profile-seed-scene",
            &Change::AddScene {
                name: "Initial Graph scene".into(),
                objective: "Observe the fixed native profile before creating any Graph".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = project.scenes[0].id;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "graph-profile-seed-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::ManimCommunity,
            },
        )?
        .project;
    let mut output = project
        .deliverables
        .first()
        .ok_or("Graph seed project has no saved output")?
        .clone();
    output.width = 640;
    output.height = 360;
    output.frame_rate = RationalTime::new(12, 1)?;
    output.container = OutputContainer::Mp4;
    output.video_codec = VideoCodec::H264;
    output.color_space = OutputColorSpace::Rec709;
    output.captions = false;
    output.burn_in_captions = false;
    output.included_scene_ids = vec![scene_id];
    let deliverable_id = output.id;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "graph-profile-seed-output",
            &Change::UpsertDeliverable { profile: output },
        )?
        .project;
    let seed = GraphProfileSeed {
        schema: GRAPH_PROFILE_SEED_SCHEMA.into(),
        project_id: project.id,
        resource: project.resource_key(),
        source_snapshot: motionwright_domain::GraphSourceSnapshot::from_project(&project),
        scene_id,
        deliverable_id,
        asset_pins: serde_json::to_value(
            motionwright_manim_profile::semantic_graph_source::graph_builtin_asset_pins(),
        )?,
        helper_pins: graph_profile_helper_pins()?,
        graph_saved: false,
        native_execution_performed: false,
        profile_admitted: false,
    };
    check_graph_profile_seed(&project, &seed)?;
    if !service.production_receipts(project.id, 1)?.is_empty() {
        return Err("Source-only Graph seed unexpectedly contains a production receipt".into());
    }
    Ok(seed)
}

fn seed_graph_profile(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    println!(
        "{}",
        serde_json::to_string(&create_graph_profile_seed(database)?)?
    );
    Ok(())
}

#[cfg(unix)]
fn graph_profile_original_identity(
    metadata: &std::fs::Metadata,
) -> (u64, u64, u32, u64, u64, i64, i64, i64, i64) {
    use std::os::unix::fs::MetadataExt;
    (
        metadata.dev(),
        metadata.ino(),
        metadata.mode(),
        metadata.nlink(),
        metadata.len(),
        metadata.mtime(),
        metadata.mtime_nsec(),
        metadata.ctime(),
        metadata.ctime_nsec(),
    )
}

#[cfg(unix)]
fn graph_profile_open_original(
    path: &Path,
    before: &std::fs::Metadata,
) -> Result<std::fs::File, Box<dyn std::error::Error>> {
    use std::os::unix::fs::OpenOptionsExt;
    // A path replaced after its first metadata read must neither follow a
    // symlink nor block waiting for a writer on an unexpected FIFO.
    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    let opened = file.metadata()?;
    if !opened.is_file()
        || graph_profile_original_identity(before) != graph_profile_original_identity(&opened)
    {
        return Err("Graph profile original input changed before descriptor capture".into());
    }
    Ok(file)
}

#[cfg(unix)]
fn graph_profile_original_file(
    path: &Path,
    limit: u64,
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    use std::{io::Read, os::unix::fs::MetadataExt};
    let before = std::fs::symlink_metadata(path)?;
    if !path.is_absolute()
        || path.canonicalize()? != path
        || !before.is_file()
        || before.file_type().is_symlink()
        || before.nlink() != 1
        || limit == 0
        || limit > 64 * 1024
        || before.len() > limit
    {
        return Err("Graph profile original input is not a bounded canonical regular file".into());
    }
    let mut file = graph_profile_open_original(path, &before)?;
    let opened = file.metadata()?;
    if graph_profile_original_identity(&before) != graph_profile_original_identity(&opened) {
        return Err("Graph profile original input changed before descriptor capture".into());
    }
    let mut bytes = Vec::new();
    (&mut file).take(limit + 1).read_to_end(&mut bytes)?;
    let after = file.metadata()?;
    let final_path = std::fs::symlink_metadata(path)?;
    if bytes.len() as u64 != opened.len()
        || bytes.len() as u64 > limit
        || graph_profile_original_identity(&opened) != graph_profile_original_identity(&after)
        || graph_profile_original_identity(&after) != graph_profile_original_identity(&final_path)
        || path.canonicalize()? != path
    {
        return Err("Graph profile original input changed during descriptor capture".into());
    }
    Ok(bytes)
}

#[cfg(not(unix))]
fn graph_profile_original_file(
    _path: &Path,
    _limit: u64,
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    Err(semwright_native_sdk::Error::new(
        semwright_native_sdk::ErrorCode::Unsupported,
        "This physical Graph probe caller requires Unix descriptor identity for original inputs",
    )
    .into())
}

fn graph_profile_captured_bytes(
    value: &serde_json::Value,
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    if value["encoding"] != "hex" {
        return Err("Graph transport custody requires original binary bytes".into());
    }
    let bytes = hex::decode(
        value["value"]
            .as_str()
            .ok_or("Graph custody bytes are absent")?,
    )?;
    if value["bytes"].as_u64() != Some(bytes.len() as u64)
        || value["sha256"] != hex::encode(Sha256::digest(&bytes))
    {
        return Err("Graph transport original byte identity changed".into());
    }
    Ok(bytes)
}

fn retain_graph_profile_transport(
    root: &Path,
    capture: &serde_json::Value,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    // The complete lossless snapshot is retained first, including error terminals.
    // If a later convenience-file write fails, the snapshot still has every byte.
    let mut snapshot_bytes = serde_json::to_vec_pretty(capture)?;
    snapshot_bytes.push(b'\n');
    retain_seed_fixture(
        &root.join("graph-profile-transport.original.json"),
        &snapshot_bytes,
    )?;
    let mut files = vec![json!({
        "relative_path":"graph-profile-transport.original.json",
        "sha256":hex::encode(Sha256::digest(&snapshot_bytes)),
        "bytes":snapshot_bytes.len(),
    })];
    for (index, entry) in capture["entries"]
        .as_array()
        .ok_or("Graph transport entries are absent")?
        .iter()
        .enumerate()
    {
        for (name, value) in [
            ("args.json", &entry["args_json"]),
            ("stdout.bin", &entry["stdout"]["original"]),
            ("stderr.bin", &entry["stderr"]["original"]),
        ] {
            let bytes = graph_profile_captured_bytes(value)?;
            let relative = format!("graph-profile-transport-{index:02}.{name}");
            retain_seed_fixture(&root.join(&relative), &bytes)?;
            files.push(json!({
                "relative_path":relative,
                "sha256":hex::encode(Sha256::digest(&bytes)),
                "bytes":bytes.len(),
            }));
        }
    }
    Ok(json!(files))
}

fn require_graph_profile_transport(
    capture: &serde_json::Value,
    observation: &motionwright_native::production::ManimGraphProfileObservation,
) -> Result<(), Box<dyn std::error::Error>> {
    if capture["schema"] != "motionwright.production.private-transport-custody/1"
        || capture["mutex_poisoned"] != false
        || capture["authority"] != false
    {
        return Err("Graph original transport custody is incomplete".into());
    }
    let entries = capture["entries"]
        .as_array()
        .ok_or("Graph transport entries are absent")?;
    if entries.len() != 2 {
        return Err("Graph observation must retain one registry and one Graph doctor".into());
    }
    let projected = serde_json::to_value(observation)?;
    for (index, (command, args)) in [
        ("doctor", &b"{}"[..]),
        (
            "driver.manim-community.doctor",
            &br#"{"probe_graph_profile":true}"#[..],
        ),
    ]
    .into_iter()
    .enumerate()
    {
        let entry = &entries[index];
        if entry["command"] != command
            || entry["mutation"] != false
            || entry["process_started"] != true
            || entry["process_exit"]["success"] != true
            || entry["transport_terminal"]["kind"] != "success"
            || entry["connection_identity"] != observation.connection_identity()
            || entry["executable_sha256"] != projected["transport_executable_sha256"]
            || entry["stdin_mode"] != "null"
            || entry["automatic_retry"] != false
            || graph_profile_captured_bytes(&entry["args_json"])? != args
        {
            return Err(
                "Graph transport attempt does not match the original selected request".into(),
            );
        }
        for stream in ["stdout", "stderr"] {
            if entry[stream]["eof_observed"] != true
                || entry[stream]["read_error_observed"] != false
                || entry[stream]["capture_limit_exceeded"] != false
            {
                return Err("Graph transport stream is incomplete".into());
            }
        }
        let raw: serde_json::Value =
            serde_json::from_slice(&graph_profile_captured_bytes(&entry["stdout"]["original"])?)?;
        let expected_request = if index == 0 {
            &projected["registry_broker_request_id"]
        } else {
            &projected["broker_request_id"]
        };
        if raw["ok"] != true || raw["command"] != command || &raw["request_id"] != expected_request
        {
            return Err("Graph original envelope lost its request binding".into());
        }
        if index == 1
            && (raw["data"]["graph_profile_probe"]["observation"]
                != serde_json::to_value(observation.fixed_profile())?
                || raw["execution"]["provenance"]["provider_generation"]
                    != json!(observation.provider_generation())
                || raw["execution"]["provenance"]["descriptor_sha256"]
                    != observation.descriptor_sha256())
        {
            return Err("Graph normalized observation differs from its original envelope".into());
        }
    }
    Ok(())
}

async fn observe_graph_profile(
    database: &Path,
    connection_path: &Path,
    seed_path: &Path,
    evidence_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    use motionwright_native::production::{ManimGraphProfileState, ProductionTransportCustody};
    let seed_bytes = graph_profile_original_file(seed_path, 64 * 1024)?;
    let seed: GraphProfileSeed = serde_json::from_slice(&seed_bytes)?;
    let service = StudioService::open(database)?;
    let project = service.project(seed.project_id)?;
    check_graph_profile_seed(&project, &seed)?;
    let connection_bytes = graph_profile_original_file(connection_path, 16 * 1024)?;
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key()
        || serde_json::from_slice::<serde_json::Value>(&connection_bytes)?
            != serde_json::to_value(&connection)?
    {
        return Err("Graph profile connection does not match its retained original".into());
    }
    if !evidence_path.is_absolute() || evidence_path.try_exists()? {
        return Err("Graph profile observation requires a fresh absolute evidence file".into());
    }
    let root = evidence_path
        .parent()
        .ok_or("Graph evidence has no owner directory")?;
    // Exclusive retention also prevents repeating this caller in the same attempt directory.
    retain_seed_fixture(&root.join("graph-profile-seed.original.json"), &seed_bytes)?;
    retain_seed_fixture(
        &root.join("graph-profile-connection.original.json"),
        &connection_bytes,
    )?;
    write_camera_evidence(
        &root.join("graph-profile-project.before.json"),
        &json!(&project),
    )?;
    write_camera_evidence(
        &root.join("graph-profile-invocation-intent.json"),
        &json!({
            "schema":"motionwright.manim.graph-profile-intent/1",
            "source_snapshot":seed.source_snapshot,
            "scene_id":seed.scene_id,
            "deliverable_id":seed.deliverable_id,
            "builtin_registry_requests":1,
            "graph_profile_probe_requests":1,
            "render_requests":0,
            "automatic_retry":false,
        }),
    )?;
    let custody = ProductionTransportCustody::default();
    let coordinator =
        ProductionCoordinator::new(service, connection)?.with_transport_custody(&custody);
    let attempted = coordinator
        .observe_manim_graph_profile(&seed.source_snapshot, seed.scene_id, seed.deliverable_id)
        .await;
    let capture = custody.private_snapshot();
    let retained = retain_graph_profile_transport(root, &capture);
    let mut evidence = json!({
        "schema":"motionwright.manim.graph-profile-native-observation/1",
        "status":"UNVERIFIED",
        "source_snapshot":seed.source_snapshot,
        "scene_id":seed.scene_id,
        "deliverable_id":seed.deliverable_id,
        "original_seed_sha256":hex::encode(Sha256::digest(&seed_bytes)),
        "original_connection_sha256":hex::encode(Sha256::digest(&connection_bytes)),
        "render_performed":false,
        "decoder_performed":false,
        "graph_saved":false,
        "scene_mutation_performed":false,
        "profile_admitted":false,
        "future_label_coverage_verified":false,
        "human_acceptance":false,
        "automatic_retry":false,
    });
    let observation = match attempted {
        Ok(observation) => observation,
        Err(original_error) => {
            match retained {
                Ok(files) => evidence["transport_original_files"] = files,
                Err(custody_error) => {
                    evidence["custody_error"] = json!({"message":custody_error.to_string()});
                }
            }
            return Err(retain_native_error_to(
                evidence_path,
                &mut evidence,
                original_error,
                None,
                &mut std::io::stderr().lock(),
                "native_manim_graph_profile_observation",
            )
            .into());
        }
    };
    evidence["observation"] = json!(&observation);
    let verification = (|| -> Result<(), Box<dyn std::error::Error>> {
        evidence["transport_original_files"] = retained?;
        if observation.state() != ManimGraphProfileState::Observed {
            return Err(
                "The fixed Graph profile was not observed; inspect the original doctor".into(),
            );
        }
        require_graph_profile_transport(&capture, &observation)?;
        if graph_profile_original_file(seed_path, 64 * 1024)? != seed_bytes
            || graph_profile_original_file(connection_path, 16 * 1024)? != connection_bytes
        {
            return Err("Graph observation original input changed during dispatch".into());
        }
        let reopened = StudioService::open(database)?;
        let current = reopened.project(project.id)?;
        if current != project
            || !current.production_design.semantic_graphs.is_empty()
            || !reopened.production_receipts(project.id, 1)?.is_empty()
        {
            return Err("Graph observation changed the saved project or its receipt ledger".into());
        }
        write_camera_evidence(
            &root.join("graph-profile-project.after.json"),
            &json!(&current),
        )?;
        Ok(())
    })();
    if let Err(error) = verification {
        evidence["failure_stage"] = json!("original_observation_verification");
        evidence["verification_error"] = json!({"message":error.to_string()});
        retain_camera_diagnostic_to(evidence_path, &evidence, &mut std::io::stderr().lock());
        return Err(error);
    }
    evidence["status"] = json!("OBSERVED_FIXED_SAMPLE_REQUIRES_SEALED_CUSTODY");
    evidence["project_unchanged_after_reopen"] = json!(true);
    evidence["graph_absent_after_reopen"] = json!(true);
    evidence["production_receipts_absent"] = json!(true);
    evidence["builtin_registry_requests"] = json!(1);
    evidence["graph_profile_probe_requests"] = json!(1);
    evidence["render_requests"] = json!(0);
    write_camera_evidence(evidence_path, &evidence)?;
    println!("{}", serde_json::to_string(&evidence)?);
    Ok(())
}

async fn realize(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    request_id: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    realize_with_bundle(database, connection_path, evidence_path, request_id, None).await
}

async fn realize_with_bundle(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    request_id: &str,
    input: Option<motionwright_native::manim_editable_projection::OriginalEditableBundleInput>,
) -> Result<(), Box<dyn std::error::Error>> {
    if !evidence_path.parent().is_some_and(Path::is_dir) || evidence_path.exists() {
        return Err("editable evidence requires an existing parent and unused destination".into());
    }
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("native-manim-e2e expected exactly one seeded project".into());
    }
    let project = projects.into_iter().next().expect("length checked");
    let scene = project
        .scenes
        .first()
        .ok_or("seeded Manim project has no scene")?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err("seeded scene is not owned by the Manim Community renderer".into());
    }
    let start_id = format!("{request_id}:start");
    if service
        .latest_production_receipt(project.id, &start_id)?
        .is_some()
    {
        return Err("editable attempt already exists; inspect its original outcome".into());
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("owner-provisioned connection is not bound to the seeded project".into());
    }
    let expected = RevisionStamp::from(&project);
    let profile = ManimRenderProfile {
        width: 640,
        height: 360,
        frame_rate: 12,
    };
    let deliverable_id =
        motionwright_native::manim_static_projection::select_saved_manim_deliverable_for_profile(
            &project, &expected, scene.id, &profile,
        )?;
    let prepared =
        motionwright_native::manim_editable_projection::prepare_saved_manim_editable_source(
            &service,
            project.id,
            &expected,
            scene.id,
            deliverable_id,
        )?;
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let runtime_observation = coordinator
        .observe_manim_runtime_version(project.id, &expected)
        .await;
    let runtime_probe_evidence = match &runtime_observation {
        Ok(observation) => json!({
            "schema": "motionwright-manim-runtime-probe-e2e/1",
            "project_id": project.id,
            "generation": project.generation,
            "revision": project.revision,
            "observation": observation,
            "error": null,
            "render_started": false,
        }),
        Err(error) => json!({
            "schema": "motionwright-manim-runtime-probe-e2e/1",
            "project_id": project.id,
            "generation": project.generation,
            "revision": project.revision,
            "observation": null,
            "error": {
                "code": format!("{:?}", error.code),
                "message": &error.message,
                "outcome_known": error.outcome_known,
            },
            "render_started": false,
        }),
    };
    let runtime_probe_path =
        evidence_path.with_file_name("motionwright-manim-runtime-observation.json");
    retain_runtime_probe_to(
        &runtime_probe_path,
        &runtime_probe_evidence,
        runtime_observation.as_ref().err().cloned(),
        write_camera_evidence,
        &mut std::io::stderr().lock(),
    )?;
    let runtime_observation = runtime_observation?;
    if runtime_observation.state() != ManimRuntimeVersionState::ObservedCompatible {
        return Err("native Manim runtime version was not observed as compatible; see the original runtime observation".into());
    }

    let attempted = match &input {
        Some(input) => {
            coordinator
                .reopen_manim_editable_scene(
                    project.id,
                    &expected,
                    request_id,
                    scene.id,
                    deliverable_id,
                    input.clone(),
                )
                .await
        }
        None => {
            coordinator
                .realize_manim_scene(project.id, &expected, request_id, scene.id, profile)
                .await
        }
    };
    let receipt_result = coordinator.receipts(project.id, 256);
    let mut evidence = json!({
        "runtime_observation": &runtime_observation,
        "project_id": project.id, "resource": project.resource_key(),
        "generation": project.generation, "revision": project.revision,
        "scene_id": scene.id, "deliverable_id": deliverable_id, "request_id": request_id,
        "profile": profile, "recent_receipts": receipt_result.as_ref().ok(),
        "receipt_read_error": receipt_result.as_ref().err(), "receipt_window_limit": 256,
        "decoded_frames_verified": false, "human_acceptance": false,
        "native_bundle_reopen_verified": false, "requested_original_source_bundle": input,
    });
    let result = match attempted {
        Ok(result) => result,
        Err(error) => {
            return Err(retain_native_error_to(
                evidence_path,
                &mut evidence,
                error,
                None,
                &mut std::io::stderr().lock(),
                "native_manim_e2e",
            )
            .into());
        }
    };
    let receipts = match receipt_result {
        Ok(receipts) => receipts,
        Err(error) => {
            return Err(retain_native_error_to(
                evidence_path,
                &mut evidence,
                error,
                Some(result),
                &mut std::io::stderr().lock(),
                "native_manim_e2e",
            )
            .into());
        }
    };
    drop(coordinator);
    finish_native_evidence(
        evidence_path,
        &mut evidence,
        result,
        |evidence| {
            let result = &evidence["result"];
            if result.get("renderer").and_then(|v| v.as_str()) != Some("manim-community")
                || result.get("native_driver").and_then(|v| v.as_str())
                    != Some("driver:manim-community")
                || result.get("runtime_contract").and_then(|v| v.as_str())
                    != Some("Manim Community 0.21.0")
                || result.get("project_revision").and_then(|v| v.as_u64()) != Some(project.revision)
                || result.get("scene_id") != Some(&json!(scene.id))
                || result.get("plan_sha256").and_then(|v| v.as_str())
                    != Some(prepared.closure.plan_sha256.as_str())
            {
                return Err("native editable result lost canonical source/runtime identity".into());
            }
            let artifact = result
                .get("artifact")
                .ok_or("native editable artifact absent")?;
            let relative = artifact
                .get("relative_path")
                .and_then(|v| v.as_str())
                .ok_or("native editable artifact path absent")?;
            let digest = artifact
                .get("sha256")
                .and_then(|v| v.as_str())
                .ok_or("native editable artifact digest absent")?;
            let bytes = artifact
                .get("bytes")
                .and_then(|v| v.as_u64())
                .ok_or("native editable artifact length absent")?;
            if !relative.ends_with(".mp4")
                || digest.len() != 64
                || bytes == 0
                || bytes > 512 * 1024 * 1024
            {
                return Err("native editable artifact exceeds the admitted bounds".into());
            }
            let source = artifact
                .get("editable_source")
                .ok_or("native editable source not verified")?;
            if artifact.get("editable_source_custody_error").is_some()
                || source
                    .get("native_object_getters_verified")
                    .and_then(|v| v.as_bool())
                    != Some(true)
                || source.get("observed_object_count").and_then(|v| v.as_u64())
                    != Some(prepared.plan.nodes.len() as u64)
                || source.get("observed_font_count").and_then(|v| v.as_u64())
                    != Some(prepared.plan.fonts.len() as u64)
                || source.pointer("/source/closure") != Some(&json!(prepared.closure))
            {
                return Err("native editable source/font/getter custody is incomplete".into());
            }
            if let Some(input) = &input {
                if artifact
                    .get("editable_input_source_custody_error")
                    .is_some()
                    || artifact.pointer("/editable_input_source/input") != Some(&json!(input))
                    || source.pointer("/source/sha256") != Some(&json!(input.sha256))
                    || source.pointer("/source/bytes") != Some(&json!(input.bytes))
                {
                    return Err(
                        "independent original-bundle open lost byte-original input custody".into(),
                    );
                }
            } else if artifact.get("editable_input_source").is_some() {
                return Err("generated render unexpectedly reported a bundle input".into());
            }
            let reopened = StudioService::open(database)?;
            if reopened.project(project.id)? != project {
                return Err("saved editable project changed during native realization".into());
            }
            let start = reopened
                .latest_production_receipt(project.id, &start_id)?
                .ok_or("original editable start receipt was not durable")?;
            let terminal = reopened
                .latest_production_receipt(project.id, &format!("{request_id}:result"))?
                .ok_or("original editable result receipt was not durable")?;
            if start.command != "driver.manim-community.editable.render.start"
                || terminal.command != "driver.manim-community.render.result"
                || start.stage != "completed"
                || terminal.stage != "completed"
                || start.generation != project.generation
                || terminal.generation != project.generation
                || start.revision != project.revision
                || terminal.revision != project.revision
                || result.pointer("/result/receipt") != Some(&json!(terminal))
            {
                return Err("editable start/result lost durable command or stamp identity".into());
            }
            let completed_commands: BTreeSet<_> = receipts
                .iter()
                .filter(|receipt| receipt.stage == "completed")
                .map(|receipt| receipt.command.as_str())
                .collect();
            for required in [
                "driver.manim-community.editable.render.start",
                "driver.manim-community.render.status",
                "driver.manim-community.render.result",
            ] {
                if !completed_commands.contains(required) {
                    return Err(
                        format!("native editable receipt ledger is missing {required}").into(),
                    );
                }
            }
            evidence["original_start_receipt"] = json!(start);
            evidence["original_result_receipt"] = json!(terminal);
            evidence["receipt_count"] = json!(receipts.len());
            evidence["completed_commands"] = json!(completed_commands);
            evidence["font_fixture_origin"] = serde_json::from_slice(EDITABLE_FONT_ORIGIN)?;
            evidence["font_fixture_license_sha256"] =
                json!(hex::encode(Sha256::digest(EDITABLE_FONT_LICENSE)));
            Ok(())
        },
        write_camera_evidence,
        "native_manim_e2e",
    )?;
    if let Err(error) = writeln!(&mut std::io::stdout().lock(), "{evidence}") {
        let _ = writeln!(
            &mut std::io::stderr().lock(),
            "{}",
            json!({
                "editable_stdout_error": error.to_string(), "evidence_retained": true,
            })
        );
    }
    Ok(())
}

struct CameraTarget {
    project_id: Uuid,
    scene_id: Uuid,
    deliverable_id: Uuid,
    request_id: String,
}

fn text_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> String {
    args.next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage())
}

fn write_camera_evidence(
    path: &Path,
    evidence: &serde_json::Value,
) -> Result<(), Box<dyn std::error::Error>> {
    // Encode before creating a destination. Filesystem write/sync failures
    // still require the retained diagnostic and a failed process exit.
    let mut encoded = serde_json::to_vec_pretty(evidence)?;
    encoded.push(b'\n');
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&encoded)?;
    file.sync_all()?;
    Ok(())
}

fn retain_camera_diagnostic_to(
    evidence_path: &Path,
    evidence: &serde_json::Value,
    stderr: &mut impl Write,
) {
    // A closed diagnostic stream must never prevent the file attempt.
    let _ = writeln!(stderr, "{evidence}");
    let diagnostic_path = evidence_path.with_extension("diagnostic.json");
    if let Err(custody_error) = write_camera_evidence(&diagnostic_path, evidence) {
        let _ = writeln!(
            stderr,
            "{}",
            json!({
                "camera_diagnostic_custody_error": custody_error.to_string(),
                "diagnostic_path": diagnostic_path,
                "primary_outcome_preserved": true
            })
        );
    }
}

// The seven-field probe sidecar is separate from the render terminal writer.
// Retention failures may block a successful probe, but never replace an error
// already returned by the original doctor invocation.
fn retain_runtime_probe_to(
    probe_path: &Path,
    probe: &serde_json::Value,
    original_error: Option<semwright_native_sdk::Error>,
    publish: impl FnOnce(&Path, &serde_json::Value) -> Result<(), Box<dyn std::error::Error>>,
    stderr: &mut impl Write,
) -> Result<(), Box<dyn std::error::Error>> {
    let publication = publish(probe_path, probe);
    if let Some(original_error) = original_error {
        // Secondary custody fields belong only to the diagnostic. The sidecar
        // remains exactly the existing seven-field observation contract.
        let mut diagnostic = probe.clone();
        if let Err(custody_error) = publication {
            diagnostic["custody_error"] = json!({
                "stage": "runtime_probe_evidence_write",
                "message": custody_error.to_string(),
            });
        }
        return Err(retain_native_error_to(
            probe_path,
            &mut diagnostic,
            original_error,
            None,
            stderr,
            "native_manim_runtime_probe",
        )
        .into());
    }
    if let Err(custody_error) = publication {
        let mut diagnostic = probe.clone();
        diagnostic["native_manim_runtime_probe"] = json!("UNVERIFIED");
        diagnostic["failure_stage"] = json!("runtime_probe_evidence_write");
        diagnostic["custody_error"] = json!({"message": custody_error.to_string()});
        retain_camera_diagnostic_to(probe_path, &diagnostic, stderr);
        return Err(custody_error);
    }
    Ok(())
}

fn retain_native_error_to(
    evidence_path: &Path,
    evidence: &mut serde_json::Value,
    original_error: semwright_native_sdk::Error,
    completed_result: Option<serde_json::Value>,
    stderr: &mut impl Write,
    status_key: &str,
) -> semwright_native_sdk::Error {
    if let Some(result) = completed_result {
        evidence[status_key] = json!("UNVERIFIED");
        evidence["failure_stage"] = json!("receipt_read");
        evidence["result"] = result;
    } else {
        evidence[status_key] = json!(if original_error.outcome_known {
            "FAIL"
        } else {
            "UNKNOWN"
        });
        evidence["failure_stage"] = json!("native_dispatch");
    }
    evidence["error"] = json!(&original_error);
    retain_camera_diagnostic_to(evidence_path, evidence, stderr);
    original_error
}

fn retain_camera_error_to(
    evidence_path: &Path,
    evidence: &mut serde_json::Value,
    original_error: semwright_native_sdk::Error,
    completed_result: Option<serde_json::Value>,
    stderr: &mut impl Write,
) -> semwright_native_sdk::Error {
    retain_native_error_to(
        evidence_path,
        evidence,
        original_error,
        completed_result,
        stderr,
        "native_manim_camera_dispatch",
    )
}

fn retain_camera_error(
    evidence_path: &Path,
    evidence: &mut serde_json::Value,
    original_error: semwright_native_sdk::Error,
    completed_result: Option<serde_json::Value>,
) -> semwright_native_sdk::Error {
    retain_camera_error_to(
        evidence_path,
        evidence,
        original_error,
        completed_result,
        &mut std::io::stderr().lock(),
    )
}

fn verify_camera_custody(
    database: &Path,
    project: &motionwright_domain::Project,
    start_id: &str,
    result_id: &str,
    evidence: &mut serde_json::Value,
) -> Result<(), Box<dyn std::error::Error>> {
    evidence["failure_stage"] = json!("database_reopen");
    let reopened = StudioService::open(database)?;
    evidence["failure_stage"] = json!("project_reopen");
    if reopened.project(project.id)? != *project {
        return Err("saved camera project changed during native realization".into());
    }
    evidence["failure_stage"] = json!("start_receipt_reopen");
    let start = reopened
        .latest_production_receipt(project.id, start_id)?
        .ok_or("original camera start receipt was not durable")?;
    evidence["failure_stage"] = json!("result_receipt_reopen");
    let terminal = reopened
        .latest_production_receipt(project.id, result_id)?
        .ok_or("original camera result receipt was not durable")?;
    evidence["failure_stage"] = json!("receipt_identity");
    if start.command != "driver.manim-community.camera.render.start"
        || terminal.command != "driver.manim-community.render.result"
        || start.stage != "completed"
        || terminal.stage != "completed"
        || start.generation != project.generation
        || terminal.generation != project.generation
        || start.revision != project.revision
        || terminal.revision != project.revision
    {
        return Err(
            "camera start/result did not retain their original durable commands and stamp".into(),
        );
    }
    evidence["original_start_receipt"] = json!(start);
    evidence["original_result_receipt"] = json!(terminal);
    Ok(())
}

fn finish_native_evidence(
    evidence_path: &Path,
    evidence: &mut serde_json::Value,
    completed_result: serde_json::Value,
    verify: impl FnOnce(&mut serde_json::Value) -> Result<(), Box<dyn std::error::Error>>,
    publish: impl FnOnce(&Path, &serde_json::Value) -> Result<(), Box<dyn std::error::Error>>,
    status_key: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    // Retain the native return value before any reopen, verification or write.
    // This function never dispatches a replacement native job.
    evidence["result"] = completed_result;
    evidence[status_key] = json!("UNVERIFIED");
    evidence["failure_stage"] = json!("post_dispatch_verification");
    if let Err(error) = verify(evidence) {
        evidence["custody_error"] = json!({"message": error.to_string()});
        retain_camera_diagnostic_to(evidence_path, evidence, &mut std::io::stderr().lock());
        return Err(error);
    }
    let mut publication = evidence.clone();
    if let Some(object) = publication.as_object_mut() {
        object.remove("failure_stage");
    }
    publication[status_key] = json!("PASS");
    if let Err(error) = publish(evidence_path, &publication) {
        evidence["failure_stage"] = json!("evidence_write");
        evidence["custody_error"] = json!({"message": error.to_string()});
        retain_camera_diagnostic_to(evidence_path, evidence, &mut std::io::stderr().lock());
        return Err(error);
    }
    *evidence = publication;
    Ok(())
}

fn finish_camera_evidence(
    evidence_path: &Path,
    evidence: &mut serde_json::Value,
    completed_result: serde_json::Value,
    verify: impl FnOnce(&mut serde_json::Value) -> Result<(), Box<dyn std::error::Error>>,
    publish: impl FnOnce(&Path, &serde_json::Value) -> Result<(), Box<dyn std::error::Error>>,
) -> Result<(), Box<dyn std::error::Error>> {
    finish_native_evidence(
        evidence_path,
        evidence,
        completed_result,
        verify,
        publish,
        "native_manim_camera_dispatch",
    )
}

async fn realize_camera(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    target: CameraTarget,
) -> Result<(), Box<dyn std::error::Error>> {
    let parent = evidence_path
        .parent()
        .ok_or("camera evidence has no parent")?;
    if !parent.is_dir() || evidence_path.exists() {
        return Err("camera evidence requires an existing parent and unused destination".into());
    }
    let service = StudioService::open(database)?;
    let project = service.project(target.project_id)?;
    let expected = RevisionStamp::from(&project);
    let start_id = format!("{}:start", target.request_id);
    if service
        .latest_production_receipt(project.id, &start_id)?
        .is_some()
    {
        return Err("camera attempt already exists; inspect its original outcome".into());
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("camera connection is not bound to the saved project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let attempted = coordinator
        .realize_manim_camera_scene(
            project.id,
            &expected,
            &target.request_id,
            target.scene_id,
            target.deliverable_id,
        )
        .await;
    let receipt_result = coordinator.receipts(project.id, 256);
    let mut evidence = json!({
        "project_id": project.id,
        "resource": project.resource_key(),
        "generation": project.generation,
        "revision": project.revision,
        "scene_id": target.scene_id,
        "deliverable_id": target.deliverable_id,
        "request_id": target.request_id,
        "recent_receipts": receipt_result.as_ref().ok(),
        "receipt_read_error": receipt_result.as_ref().err(),
        "receipt_window_limit": 256,
        "decoded_frames_verified": false,
        "motion_acceptance": "NOT_ASSESSED"
    });
    let result = match attempted {
        Ok(result) => result,
        Err(error) => {
            return Err(retain_camera_error(evidence_path, &mut evidence, error, None).into());
        }
    };
    if let Err(error) = receipt_result {
        return Err(retain_camera_error(evidence_path, &mut evidence, error, Some(result)).into());
    }
    // The coordinator checked source, native provenance, getters and MP4
    // custody. Reopen and local publication cannot discard that return value.
    drop(coordinator);
    finish_camera_evidence(
        evidence_path,
        &mut evidence,
        result,
        |evidence| {
            verify_camera_custody(
                database,
                &project,
                &start_id,
                &format!("{}:result", target.request_id),
                evidence,
            )
        },
        write_camera_evidence,
    )?;
    // The evidence file is durable already. A closed stdout is only a
    // presentation error and never causes another native attempt.
    if let Err(error) = writeln!(&mut std::io::stdout().lock(), "{evidence}") {
        let _ = writeln!(
            &mut std::io::stderr().lock(),
            "{}",
            json!({"camera_stdout_error": error.to_string(), "evidence_retained": true})
        );
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let command = args
        .next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage());

    match command.as_str() {
        "seed" => {
            let database = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed(&database)
        }
        "seed-graph-cycle" => {
            let database = path_arg(&mut args);
            let report = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let value = graph_cycle::seed(&database, &report)?;
            println!("{}", serde_json::to_string(&value)?);
            Ok(())
        }
        "graph-cycle-edit-prepare" => {
            let database = path_arg(&mut args);
            let native = path_arg(&mut args);
            let output = path_arg(&mut args);
            let report = path_arg(&mut args);
            let request = text_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let value = graph_cycle::prepare_edit(&database, &native, &output, &report, &request)?;
            println!("{}", serde_json::to_string(&value)?);
            Ok(())
        }
        "graph-cycle-save-readback" => {
            let database = path_arg(&mut args);
            let source = path_arg(&mut args);
            let terminal = path_arg(&mut args);
            let report = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let value = graph_cycle::finish_save(&database, &source, &terminal, &report)?;
            println!("{}", serde_json::to_string(&value)?);
            Ok(())
        }
        command @ ("realize-graph" | "realize-graph-bundle") => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            let saved = path_arg(&mut args);
            let input = if command == "realize-graph-bundle" {
                Some(path_arg(&mut args))
            } else {
                None
            };
            let request = text_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            graph_cycle::realize(
                &database,
                &connection,
                &evidence,
                &saved,
                input.as_deref(),
                &request,
            )
            .await
        }
        "seed-graph-profile" => {
            let database = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed_graph_profile(&database)
        }
        "observe-graph-profile" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let seed = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            observe_graph_profile(&database, &connection, &seed, &evidence).await
        }
        "realize" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            let request_id = args
                .next()
                .map(|value| value.into_string().unwrap_or_else(|_| usage()))
                .unwrap_or_else(|| "native-manim-real-driver-host".into());
            if args.next().is_some() {
                usage();
            }
            realize(&database, &connection, &evidence, &request_id).await
        }
        "realize-bundle" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            let input_path = path_arg(&mut args);
            let request = text_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let input = editable_cycle::input_descriptor(&input_path)?;
            realize_with_bundle(&database, &connection, &evidence, &request, Some(input)).await
        }
        "source-cycle-edit" => {
            let database = path_arg(&mut args);
            let original_native = path_arg(&mut args);
            let output_root = path_arg(&mut args);
            let report = path_arg(&mut args);
            let request = text_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let result =
                editable_cycle::edit(&database, &original_native, &output_root, &report, &request)?;
            println!("{}", serde_json::to_string(&result)?);
            Ok(())
        }
        "source-cycle-readback" => {
            let database = path_arg(&mut args);
            let edit_report = path_arg(&mut args);
            let current_native = path_arg(&mut args);
            let output_root = path_arg(&mut args);
            let report = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let result = editable_cycle::readback(
                &database,
                &edit_report,
                &current_native,
                &output_root,
                &report,
            )?;
            println!("{}", serde_json::to_string(&result)?);
            Ok(())
        }
        "realize-camera" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            let target = CameraTarget {
                project_id: text_arg(&mut args).parse()?,
                scene_id: text_arg(&mut args).parse()?,
                deliverable_id: text_arg(&mut args).parse()?,
                request_id: text_arg(&mut args),
            };
            if args.next().is_some() {
                usage();
            }
            realize_camera(&database, &connection, &evidence, target).await
        }
        _ => usage(),
    }
}

#[cfg(test)]
mod camera_custody_tests {
    use super::*;

    #[test]
    fn diagnostic_write_failure_preserves_original_native_unknown() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        let diagnostic_path = evidence_path.with_extension("diagnostic.json");
        std::fs::write(&diagnostic_path, b"existing custody evidence").unwrap();
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::Timeout,
            "original native outcome is unknown",
        )
        .uncertain();
        let expected = serde_json::to_value(&original).unwrap();
        let mut evidence = json!({"request_id": "original-attempt"});
        let returned = retain_camera_error(&evidence_path, &mut evidence, original, None);
        assert_eq!(serde_json::to_value(&returned).unwrap(), expected);
        assert_eq!(evidence["native_manim_camera_dispatch"], "UNKNOWN");
        assert_eq!(evidence["error"], expected);
        assert_eq!(
            std::fs::read(&diagnostic_path).unwrap(),
            b"existing custody evidence"
        );
    }

    #[test]
    fn receipt_read_failure_retains_opaque_result_without_publishing_pass() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        let opaque_result = json!({"opaque_retention_marker": [3, 7, 11]});
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::BackendFailed,
            "original receipt read failed",
        );
        let expected = serde_json::to_value(&original).unwrap();
        let mut evidence = json!({"request_id": "receipt-custody-attempt"});
        let returned = retain_camera_error(
            &evidence_path,
            &mut evidence,
            original,
            Some(opaque_result.clone()),
        );
        assert_eq!(serde_json::to_value(&returned).unwrap(), expected);
        let stored: serde_json::Value = serde_json::from_slice(
            &std::fs::read(evidence_path.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(stored, evidence);
        assert_eq!(stored["result"], opaque_result);
        assert_eq!(stored["error"], expected);
        assert_eq!(stored["native_manim_camera_dispatch"], "UNVERIFIED");
        assert_eq!(stored["failure_stage"], "receipt_read");
        assert!(!evidence_path.exists());
    }
    struct BrokenDiagnosticStream;

    impl Write for BrokenDiagnosticStream {
        fn write(&mut self, _buf: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "diagnostic stream closed",
            ))
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn broken_stderr_still_attempts_diagnostic_and_preserves_native_unknown() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::Unavailable,
            "original provider generation is unavailable",
        )
        .uncertain()
        .recipe_progress(1, "driver.manim-community.camera.render.start");
        let expected = serde_json::to_value(&original).unwrap();
        let mut evidence = json!({"request_id": "original-camera-attempt"});
        let returned = retain_camera_error_to(
            &evidence_path,
            &mut evidence,
            original,
            None,
            &mut BrokenDiagnosticStream,
        );
        assert_eq!(serde_json::to_value(returned).unwrap(), expected);
        let diagnostic_path = evidence_path.with_extension("diagnostic.json");
        let original_bytes = std::fs::read(&diagnostic_path).unwrap();
        let stored: serde_json::Value = serde_json::from_slice(&original_bytes).unwrap();
        assert_eq!(stored["error"], expected);
        assert_eq!(stored["native_manim_camera_dispatch"], "UNKNOWN");
        // Both the stderr failure and create_new collision remain secondary.
        retain_camera_diagnostic_to(&evidence_path, &evidence, &mut BrokenDiagnosticStream);
        assert_eq!(std::fs::read(diagnostic_path).unwrap(), original_bytes);
        assert!(!evidence_path.exists());
    }

    fn assert_opaque_completion_retained(
        evidence_path: &Path,
        evidence: &serde_json::Value,
        original: &serde_json::Value,
        stage: &str,
    ) {
        let stored: serde_json::Value = serde_json::from_slice(
            &std::fs::read(evidence_path.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(&stored, evidence);
        assert_eq!(&stored["result"], original);
        assert_eq!(stored["native_manim_camera_dispatch"], "UNVERIFIED");
        assert_eq!(stored["failure_stage"], stage);
        assert!(
            !stored["custody_error"]["message"]
                .as_str()
                .unwrap()
                .is_empty()
        );
        assert_eq!(stored["decoded_frames_verified"], false);
        assert_eq!(stored["motion_acceptance"], "NOT_ASSESSED");
    }

    fn unfinished_camera_evidence() -> serde_json::Value {
        json!({
            "request_id": "original-camera-attempt",
            "decoded_frames_verified": false,
            "motion_acceptance": "NOT_ASSESSED"
        })
    }

    #[test]
    fn database_reopen_failure_retains_native_completion_before_custody() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("project.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service.create_project("Camera custody fixture").unwrap();
        drop(service);
        let evidence_path = temporary.path().join("camera.json");
        let opaque = json!({"job_ref": "native-original", "opaque": [3, 7, 11]});
        let mut evidence = unfinished_camera_evidence();
        let failure = finish_camera_evidence(
            &evidence_path,
            &mut evidence,
            opaque.clone(),
            |evidence| {
                // A directory is a real SQLite reopen failure, not a mock render.
                verify_camera_custody(
                    temporary.path(),
                    &project,
                    "original:start",
                    "original:result",
                    evidence,
                )
            },
            |_, _| panic!("failed custody may not publish success"),
        )
        .unwrap_err();
        assert!(!failure.to_string().is_empty());
        assert_opaque_completion_retained(&evidence_path, &evidence, &opaque, "database_reopen");
        assert!(!evidence_path.exists());
    }

    #[test]
    fn changed_project_reopen_retains_native_completion_without_success() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("project.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let mut expected = service.create_project("Camera custody fixture").unwrap();
        // The retained source no longer equals the durably reopened project.
        expected.revision += 1;
        drop(service);
        let evidence_path = temporary.path().join("camera.json");
        let opaque = json!({"job_ref": "native-original", "opaque": {"digest": "retained"}});
        let mut evidence = unfinished_camera_evidence();
        let failure = finish_camera_evidence(
            &evidence_path,
            &mut evidence,
            opaque.clone(),
            |evidence| {
                verify_camera_custody(
                    &database,
                    &expected,
                    "original:start",
                    "original:result",
                    evidence,
                )
            },
            |_, _| panic!("changed project may not publish success"),
        )
        .unwrap_err();
        assert_eq!(
            failure.to_string(),
            "saved camera project changed during native realization"
        );
        assert_opaque_completion_retained(&evidence_path, &evidence, &opaque, "project_reopen");
        assert!(!evidence_path.exists());
    }

    #[test]
    fn missing_durable_start_or_result_retains_native_completion() {
        for retain_start in [false, true] {
            let temporary = tempfile::tempdir().unwrap();
            let database = temporary.path().join("project.sqlite3");
            let service = StudioService::open(&database).unwrap();
            let project = service.create_project("Camera custody fixture").unwrap();
            if retain_start {
                service
                    .append_production_receipt(motionwright_storage::ProductionReceiptInput {
                        project_id: project.id,
                        generation: project.generation,
                        revision: project.revision,
                        request_id: "original:start".into(),
                        request_sha256: "ab".repeat(32),
                        command: "driver.manim-community.camera.render.start".into(),
                        stage: "completed".into(),
                        payload: json!({"fixture_only": true}),
                    })
                    .unwrap();
            }
            drop(service);
            let evidence_path = temporary.path().join("camera.json");
            let opaque = json!({"job_ref": "native-original", "opaque": [13, 17]});
            let mut evidence = unfinished_camera_evidence();
            assert!(
                finish_camera_evidence(
                    &evidence_path,
                    &mut evidence,
                    opaque.clone(),
                    |evidence| {
                        verify_camera_custody(
                            &database,
                            &project,
                            "original:start",
                            "original:result",
                            evidence,
                        )
                    },
                    |_, _| panic!("incomplete receipts may not publish success"),
                )
                .is_err()
            );
            assert_opaque_completion_retained(
                &evidence_path,
                &evidence,
                &opaque,
                if retain_start {
                    "result_receipt_reopen"
                } else {
                    "start_receipt_reopen"
                },
            );
            assert!(!evidence_path.exists());
        }
    }

    #[test]
    fn evidence_destination_collision_retains_native_completion_and_existing_bytes() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        std::fs::write(&evidence_path, b"existing original custody").unwrap();
        let opaque = json!({"job_ref": "native-original", "opaque": [19, 23]});
        let mut evidence = unfinished_camera_evidence();
        assert!(
            finish_camera_evidence(
                &evidence_path,
                &mut evidence,
                opaque.clone(),
                |_| Ok(()),
                write_camera_evidence,
            )
            .is_err()
        );
        assert_opaque_completion_retained(&evidence_path, &evidence, &opaque, "evidence_write");
        assert_eq!(
            std::fs::read(evidence_path).unwrap(),
            b"existing original custody"
        );
    }

    #[test]
    fn write_or_sync_failure_retains_completion_and_does_not_overwrite_partial_custody() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        let opaque = json!({"job_ref": "native-original", "opaque": [29, 31]});
        let mut evidence = unfinished_camera_evidence();
        let failure = finish_camera_evidence(
            &evidence_path,
            &mut evidence,
            opaque.clone(),
            |_| Ok(()),
            |path, _| {
                // Inject a write/sync failure after a partial create_new write.
                // This documents the existing writer's limit; no native effect runs.
                let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
                file.write_all(b"{partial-private-evidence")?;
                Err(std::io::Error::other("injected evidence sync failure").into())
            },
        )
        .unwrap_err();
        assert_eq!(failure.to_string(), "injected evidence sync failure");
        assert_opaque_completion_retained(&evidence_path, &evidence, &opaque, "evidence_write");
        assert_eq!(
            std::fs::read(&evidence_path).unwrap(),
            b"{partial-private-evidence"
        );
    }
}

#[cfg(test)]
mod editable_seed_and_custody_tests {
    use super::*;

    #[test]
    fn modern_seed_reopens_real_font_cas_and_saved_profile_without_native_jobs() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("editable.sqlite3");
        seed(&database).unwrap();
        let service = StudioService::open(&database).unwrap();
        let projects = service.projects(2).unwrap();
        assert_eq!(projects.len(), 1);
        let project = projects[0].clone();
        let scene = &project.scenes[0];
        assert_eq!(scene.renderer, RendererKind::ManimCommunity);
        let requested = ManimRenderProfile {
            width: 640,
            height: 360,
            frame_rate: 12,
        };
        let expected = RevisionStamp::from(&project);
        let deliverable_id = motionwright_native::manim_static_projection::select_saved_manim_deliverable_for_profile(
            &project, &expected, scene.id, &requested,
        ).unwrap();
        let package =
            motionwright_native::manim_editable_projection::prepare_saved_manim_editable_source(
                &service,
                project.id,
                &expected,
                scene.id,
                deliverable_id,
            )
            .unwrap();
        assert_eq!(package.plan.render_profile, requested);
        assert_eq!(package.plan.clock.frame_count, 24);
        assert_eq!(package.plan.fonts.len(), 1);
        assert_eq!(package.plan.fonts[0].sha256, EDITABLE_FONT_SHA256);
        assert_eq!(package.plan.background.color, "#153047");
        let title = package
            .plan
            .nodes
            .iter()
            .find_map(|node| node.typography.as_ref())
            .unwrap();
        assert_eq!(title.text, "x² + y² = r²");
        assert_eq!(title.weight, 700);
        let actual_font = service
            .verified_brand_font(project.id, &expected, "Montserrat")
            .unwrap();
        assert_eq!(actual_font.bytes, EDITABLE_FONT);
        assert_eq!(actual_font.source.sha256, EDITABLE_FONT_SHA256);
        assert_eq!(
            std::fs::read(database.with_extension("Montserrat-OFL.txt")).unwrap(),
            EDITABLE_FONT_LICENSE
        );
        assert_eq!(
            std::fs::read(database.with_extension("Montserrat-source.json")).unwrap(),
            EDITABLE_FONT_ORIGIN
        );
        assert!(
            service
                .production_receipts(project.id, 256)
                .unwrap()
                .is_empty()
        );
        drop(service);
        assert!(seed(&database).is_err());
        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert!(
            reopened
                .production_receipts(project.id, 256)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn editable_unknown_is_preserved_and_known_completion_is_not_erased_by_custody_failure() {
        let temporary = tempfile::tempdir().unwrap();
        let unknown_path = temporary.path().join("unknown.json");
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::Timeout,
            "one original editable invocation has unknown outcome",
        )
        .uncertain()
        .recipe_progress(1, "driver.manim-community.editable.render.start");
        let serialized = serde_json::to_value(&original).unwrap();
        let mut evidence = json!({"request_id":"original-editable", "human_acceptance":false});
        let returned = retain_native_error_to(
            &unknown_path,
            &mut evidence,
            original,
            None,
            &mut Vec::new(),
            "native_manim_e2e",
        );
        assert_eq!(serde_json::to_value(returned).unwrap(), serialized);
        assert_eq!(evidence["native_manim_e2e"], "UNKNOWN");
        assert_eq!(evidence["error"], serialized);
        let retained: serde_json::Value = serde_json::from_slice(
            &std::fs::read(unknown_path.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(retained, evidence);
        assert!(!unknown_path.exists());

        let complete_path = temporary.path().join("complete.json");
        let known = json!({"job_ref":"original-native-job","result":{"opaque":[2,3,5]}});
        let mut evidence = json!({"request_id":"original-complete", "human_acceptance":false});
        let failure = finish_native_evidence(
            &complete_path,
            &mut evidence,
            known.clone(),
            |_| Err("source custody changed after acknowledged native completion".into()),
            |_, _| panic!("failed custody must not publish PASS"),
            "native_manim_e2e",
        )
        .unwrap_err();
        assert!(!failure.to_string().is_empty());
        assert_eq!(evidence["native_manim_e2e"], "UNVERIFIED");
        assert_eq!(evidence["result"], known);
        let retained: serde_json::Value = serde_json::from_slice(
            &std::fs::read(complete_path.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(retained, evidence);
        assert!(!complete_path.exists());
    }
}

#[cfg(test)]
mod runtime_probe_custody_tests {
    use super::*;

    fn original_probe_error() -> semwright_native_sdk::Error {
        semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::Timeout,
            "original doctor transport outcome",
        )
        .uncertain()
        .recipe_progress(1, "driver.manim-community.doctor")
    }

    fn probe_evidence(error: Option<&semwright_native_sdk::Error>) -> serde_json::Value {
        json!({
            "schema": "motionwright-manim-runtime-probe-e2e/1",
            "project_id": Uuid::nil(),
            "generation": Uuid::nil(),
            "revision": 0,
            "observation": if error.is_none() {
                json!({"source_control_only": true})
            } else {
                serde_json::Value::Null
            },
            "error": error,
            "render_started": false,
        })
    }

    struct ClosedDiagnosticStream;

    impl Write for ClosedDiagnosticStream {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "closed diagnostic stream",
            ))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Err(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "closed diagnostic stream",
            ))
        }
    }

    #[test]
    fn original_probe_error_survives_sidecar_collision_and_secondary_custody_failures() {
        let temporary = tempfile::tempdir().unwrap();
        let sidecar = temporary
            .path()
            .join("motionwright-manim-runtime-observation.json");
        let diagnostic = sidecar.with_extension("diagnostic.json");
        std::fs::write(&sidecar, b"prior probe sidecar").unwrap();
        std::fs::write(&diagnostic, b"prior probe diagnostic").unwrap();
        let original = original_probe_error();
        let expected_error = serde_json::to_value(&original).unwrap();
        let probe = probe_evidence(Some(&original));
        let before = probe.clone();
        let publications = std::cell::Cell::new(0);
        let returned = retain_runtime_probe_to(
            &sidecar,
            &probe,
            Some(original),
            |path, value| {
                publications.set(publications.get() + 1);
                write_camera_evidence(path, value)
            },
            &mut ClosedDiagnosticStream,
        )
        .unwrap_err();
        assert_eq!(
            serde_json::to_value(
                returned
                    .downcast_ref::<semwright_native_sdk::Error>()
                    .unwrap()
            )
            .unwrap(),
            expected_error,
        );
        assert_eq!(publications.get(), 1);
        assert_eq!(std::fs::read(&sidecar).unwrap(), b"prior probe sidecar");
        assert_eq!(
            std::fs::read(&diagnostic).unwrap(),
            b"prior probe diagnostic"
        );
        assert_eq!(probe, before);
        assert_eq!(probe.as_object().unwrap().len(), 7);
        assert!(!temporary.path().join("native-result.json").exists());
    }

    #[test]
    fn original_probe_error_survives_partial_sidecar_write_and_retains_secondary_diagnostic() {
        let temporary = tempfile::tempdir().unwrap();
        let sidecar = temporary
            .path()
            .join("motionwright-manim-runtime-observation.json");
        let original = original_probe_error();
        let expected_error = serde_json::to_value(&original).unwrap();
        let probe = probe_evidence(Some(&original));
        let returned = retain_runtime_probe_to(
            &sidecar,
            &probe,
            Some(original),
            |path, _| {
                let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
                file.write_all(b"{partial-probe-sidecar")?;
                Err(std::io::Error::other("injected probe sync failure").into())
            },
            &mut Vec::new(),
        )
        .unwrap_err();
        assert_eq!(
            serde_json::to_value(
                returned
                    .downcast_ref::<semwright_native_sdk::Error>()
                    .unwrap()
            )
            .unwrap(),
            expected_error,
        );
        let diagnostic: serde_json::Value = serde_json::from_slice(
            &std::fs::read(sidecar.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(diagnostic["error"], expected_error);
        assert_eq!(diagnostic["native_manim_runtime_probe"], "UNKNOWN");
        assert_eq!(diagnostic["render_started"], false);
        assert_eq!(
            diagnostic["custody_error"]["stage"],
            "runtime_probe_evidence_write"
        );
        assert_eq!(
            diagnostic["custody_error"]["message"],
            "injected probe sync failure"
        );
        assert_eq!(std::fs::read(&sidecar).unwrap(), b"{partial-probe-sidecar");
        assert_eq!(probe.as_object().unwrap().len(), 7);
    }

    #[test]
    fn successful_probe_still_blocks_on_sidecar_failure_and_preserves_observation() {
        let temporary = tempfile::tempdir().unwrap();
        let sidecar = temporary
            .path()
            .join("motionwright-manim-runtime-observation.json");
        std::fs::write(&sidecar, b"existing probe sidecar").unwrap();
        let probe = probe_evidence(None);
        let returned = retain_runtime_probe_to(
            &sidecar,
            &probe,
            None,
            write_camera_evidence,
            &mut Vec::new(),
        )
        .unwrap_err();
        assert_eq!(
            returned.downcast_ref::<std::io::Error>().unwrap().kind(),
            std::io::ErrorKind::AlreadyExists,
        );
        let diagnostic: serde_json::Value = serde_json::from_slice(
            &std::fs::read(sidecar.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(diagnostic["observation"], probe["observation"]);
        assert_eq!(diagnostic["native_manim_runtime_probe"], "UNVERIFIED");
        assert_eq!(diagnostic["failure_stage"], "runtime_probe_evidence_write");
        assert_eq!(diagnostic["render_started"], false);
        assert_eq!(std::fs::read(&sidecar).unwrap(), b"existing probe sidecar");
        assert!(!temporary.path().join("native-result.json").exists());
    }

    #[test]
    fn successful_probe_retains_exact_seven_field_sidecar_without_terminal_publication() {
        let temporary = tempfile::tempdir().unwrap();
        let sidecar = temporary
            .path()
            .join("motionwright-manim-runtime-observation.json");
        let probe = probe_evidence(None);
        retain_runtime_probe_to(
            &sidecar,
            &probe,
            None,
            write_camera_evidence,
            &mut Vec::new(),
        )
        .unwrap();
        let retained: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&sidecar).unwrap()).unwrap();
        assert_eq!(retained, probe);
        assert_eq!(retained.as_object().unwrap().len(), 7);
        assert_eq!(retained["error"], serde_json::Value::Null);
        assert_eq!(retained["render_started"], false);
        assert!(!sidecar.with_extension("diagnostic.json").exists());
        assert!(!temporary.path().join("native-result.json").exists());
    }
}

#[cfg(test)]
mod graph_profile_tests {
    use super::*;

    #[test]
    fn seed_reopens_a_real_empty_graph_scene_without_native_receipts() {
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("source.sqlite3");
        let seed = create_graph_profile_seed(&database).unwrap();
        let reopened = StudioService::open(&database).unwrap();
        let project = reopened.project(seed.project_id).unwrap();
        check_graph_profile_seed(&project, &seed).unwrap();
        assert!(project.production_design.semantic_graphs.is_empty());
        assert!(project.scenes[0].nodes.is_empty());
        assert!(project.scenes[0].beats.is_empty());
        assert_eq!(
            project.scenes[0].camera,
            motionwright_domain::CameraState::default()
        );
        assert_eq!(project.deliverables[0].width, 640);
        assert_eq!(project.deliverables[0].height, 360);
        assert_eq!(
            project.deliverables[0].frame_rate,
            RationalTime::new(12, 1).unwrap()
        );
        assert_eq!(seed.asset_pins.as_array().unwrap().len(), 9);
        assert_eq!(seed.helper_pins.as_array().unwrap().len(), 6);
        assert!(
            reopened
                .production_receipts(seed.project_id, 1)
                .unwrap()
                .is_empty()
        );
        assert!(create_graph_profile_seed(&database).is_err());
        assert_eq!(reopened.project(seed.project_id).unwrap(), project);
    }

    #[test]
    fn stale_seed_foreign_scope_or_modified_pins_fail_before_native_observation() {
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("source.sqlite3");
        let original = create_graph_profile_seed(&database).unwrap();
        let service = StudioService::open(&database).unwrap();
        let project = service.project(original.project_id).unwrap();
        for case in 0..10 {
            let mut seed = original.clone();
            match case {
                0 => seed.source_snapshot.project_id = Uuid::new_v4(),
                1 => seed.source_snapshot.generation = Uuid::new_v4(),
                2 => seed.source_snapshot.branch_id = Uuid::new_v4(),
                3 => seed.source_snapshot.revision += 1,
                4 => seed.scene_id = Uuid::new_v4(),
                5 => seed.deliverable_id = Uuid::new_v4(),
                6 => seed.asset_pins[0]["sha256"] = json!("00".repeat(32)),
                7 => seed.profile_admitted = true,
                8 => seed.resource.push_str("-foreign"),
                _ => seed.helper_pins[0]["sha256"] = json!("00".repeat(32)),
            }
            assert!(check_graph_profile_seed(&project, &seed).is_err(), "{case}");
        }
        let mut extended = serde_json::to_value(&original).unwrap();
        extended["ready"] = json!(true);
        assert!(serde_json::from_value::<GraphProfileSeed>(extended).is_err());
        assert_eq!(service.project(original.project_id).unwrap(), project);
    }

    #[cfg(unix)]
    #[test]
    fn original_input_replaced_by_fifo_is_rejected_without_a_writer_or_blocking_open() {
        use std::{ffi::CString, os::unix::ffi::OsStrExt};
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let path = root.join("original-then-fifo");
        std::fs::write(&path, b"original input").unwrap();
        assert_eq!(
            graph_profile_original_file(&path, 64).unwrap(),
            b"original input"
        );
        let before = std::fs::symlink_metadata(&path).unwrap();
        assert!(before.is_file());
        std::fs::remove_file(&path).unwrap();
        let native = CString::new(path.as_os_str().as_bytes()).unwrap();
        // Replace only after the caller's regular-file metadata observation.
        // There is deliberately no FIFO writer and no substitute runtime.
        assert_eq!(unsafe { libc::mkfifo(native.as_ptr(), 0o600) }, 0);
        let error = graph_profile_open_original(&path, &before)
            .expect_err("changed input must fail before any read");
        assert!(
            error
                .to_string()
                .contains("changed before descriptor capture")
        );
    }

    #[test]
    fn original_binary_custody_preserves_unknown_and_refuses_overwrite() {
        fn original(bytes: &[u8]) -> serde_json::Value {
            json!({
                "encoding":"hex", "bytes":bytes.len(),
                "sha256":hex::encode(Sha256::digest(bytes)), "value":hex::encode(bytes),
            })
        }
        let temp = tempfile::tempdir().unwrap();
        let stdout = b"  {broken\xff\n";
        let stderr = b"\x00original-stderr\r\n";
        let capture = json!({
            "entries":[{
                "args_json":original(br#"{"probe_graph_profile":true}"#),
                "stdout":{"original":original(stdout)},
                "stderr":{"original":original(stderr)},
                "transport_terminal":{"kind":"error","original_error":{
                    "code":"timeout","message":"original UNKNOWN","outcome_known":false,
                }},
            }],
        });
        retain_graph_profile_transport(temp.path(), &capture).unwrap();
        let snapshot = temp.path().join("graph-profile-transport.original.json");
        let before = std::fs::read(&snapshot).unwrap();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&before).unwrap(),
            capture
        );
        assert_eq!(
            std::fs::read(temp.path().join("graph-profile-transport-00.stdout.bin")).unwrap(),
            stdout
        );
        assert_eq!(
            std::fs::read(temp.path().join("graph-profile-transport-00.stderr.bin")).unwrap(),
            stderr
        );
        assert!(retain_graph_profile_transport(temp.path(), &capture).is_err());
        assert_eq!(std::fs::read(snapshot).unwrap(), before);
        let mut altered = capture["entries"][0]["stdout"]["original"].clone();
        altered["bytes"] = json!(1);
        assert!(graph_profile_captured_bytes(&altered).is_err());
        #[cfg(unix)]
        {
            let raw = temp
                .path()
                .join("graph-profile-transport-00.stdout.bin")
                .canonicalize()
                .unwrap();
            assert_eq!(
                graph_profile_original_file(&raw, 64 * 1024).unwrap(),
                stdout
            );
            assert!(graph_profile_original_file(&raw, 1).is_err());
            let symbolic = temp.path().join("symbolic-original");
            std::os::unix::fs::symlink(&raw, &symbolic).unwrap();
            assert!(graph_profile_original_file(&symbolic, 64 * 1024).is_err());
            let alias = temp.path().join("hardlinked-original");
            std::fs::hard_link(&raw, &alias).unwrap();
            assert!(graph_profile_original_file(&raw, 64 * 1024).is_err());
        }
    }
}
