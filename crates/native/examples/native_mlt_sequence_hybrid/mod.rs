//! Original, source-bound 180-frame MC/Manim/MC CI producer.
//!
//! Uses the saved Motionwright source, the existing real renderer entrypoints
//! and the pinned semantic MLT Driver Host. The 33-frame legacy producer stays
//! separate. A source or decoder observation is never a physical acceptance.
use super::*;
use motionwright_domain::{
    AudioCodec, DeliverableProfile, DeliveryArchetype, DeliveryNarrativeRole, DeliveryRecipe,
    DeliverySceneIntent, OutputColorSpace, OutputContainer, Project, RendererKind, VideoCodec,
};
use motionwright_native::{
    film::{MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY},
    manim_static_projection::{build_manim_static_plan_v2, prepare_saved_manim_static_source},
    production::capture_production_file,
};
use serde_json::Value;
use std::error::Error;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const FRAMES: u64 = 180;
const SEGMENT_FRAMES: u64 = 60;
const WAV_BYTES: u64 = 1_152_044;
const PCM_FRAMES: u64 = 288_000;
// The downstream strict original-JSON reader admits at most 8 MiB. These are
// independent custody limits, not new credits in a native/Graph resource pool.
const JSON_FILE_LIMIT: u64 = 8 * 1024 * 1024;
const PRIVATE_JSON_TOTAL_LIMIT: u64 = 32 * 1024 * 1024;
const PROJECT_LIMIT: u64 = JSON_FILE_LIMIT;
const PRIVATE_ORIGINALS: [&str; 10] = [
    "source-project.original.json",
    "source-project.reopened.json",
    "hybrid-plan.source-only.json",
    "declared-readback-contract.json",
    "hybrid-render.serialized.json",
    "hybrid-timeline.serialized.json",
    "hybrid-master.serialized.json",
    "result.private.json",
    "native-receipt-custody.private.json",
    "native-production-receipts.private.json",
];
const GEOMETRY: [[f64; 4]; 3] = [
    [400.0, 420.0, 180.0, 140.0],
    [750.0, 420.0, 180.0, 140.0],
    [1100.0, 420.0, 180.0, 140.0],
];
const COLORS: [&str; 3] = ["#FF0000", "#00FF00", "#0000FF"];

fn need(condition: bool, message: &'static str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}
fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn digest_literal(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn ci_envelope() -> Result<()> {
    need(
        std::env::var("GITHUB_ACTIONS").as_deref() == Ok("true")
            && std::env::var("RUNNER_OS").as_deref() == Ok("Linux")
            && std::env::var("MOTIONWRIGHT_MLT_MODE").as_deref() == Ok("hybrid-av")
            && std::env::var_os("MOTIONWRIGHT_MLT_ORIGINAL_WAV").is_none()
            && std::env::var_os("MOTIONWRIGHT_MLT_ORIGINAL_WAV_SHA256").is_none(),
        "Hybrid production requires the original Linux G2 mode and input envelope",
    )
}
fn regular_parent(path: &Path) -> Result<()> {
    need(path.is_absolute(), "Hybrid path must be absolute")?;
    need(
        !path.components().any(|part| {
            matches!(
                part,
                std::path::Component::CurDir | std::path::Component::ParentDir
            )
        }),
        "Hybrid path is not a canonical member",
    )?;
    let parent = path.parent().ok_or("Hybrid path has no parent")?;
    for ancestor in parent.ancestors() {
        let metadata = fs::symlink_metadata(ancestor)?;
        need(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "Hybrid path has an unsafe ancestor",
        )?;
    }
    Ok(())
}
fn unused(path: &Path) -> Result<()> {
    regular_parent(path)?;
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
        Ok(_) => {
            Err("Hybrid output already exists; preserve and inspect the original attempt".into())
        }
    }
}
fn capture_absolute(path: &Path, maximum: u64) -> Result<Vec<u8>> {
    regular_parent(path)?;
    let parent = path.parent().ok_or("Hybrid source parent is absent")?;
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or("Hybrid source member is not UTF-8")?;
    Ok(capture_production_file(parent, name, maximum)?
        .bytes()
        .to_vec())
}
fn new_bytes(path: &Path, bytes: &[u8]) -> Result<()> {
    need(
        bytes.len() as u64 <= JSON_FILE_LIMIT,
        "Hybrid retained original exceeds the independent per-file custody limit",
    )?;
    unused(path)?;
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    need(
        capture_absolute(path, JSON_FILE_LIMIT)? == bytes,
        "Hybrid retained output readback changed",
    )
}
struct BoundedJson {
    bytes: Vec<u8>,
    limit: u64,
}
impl Write for BoundedJson {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        let total = (self.bytes.len() as u64)
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| std::io::Error::other("Hybrid original JSON byte count overflowed"))?;
        if total > self.limit {
            return Err(std::io::Error::other(
                "Hybrid original JSON exceeds its independent byte limit",
            ));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn json_bytes(value: &impl serde::Serialize) -> Result<Vec<u8>> {
    let mut output = BoundedJson {
        bytes: Vec::new(),
        limit: JSON_FILE_LIMIT,
    };
    serde_json::to_writer_pretty(&mut output, value)?;
    output.write_all(b"\n")?;
    Ok(output.bytes)
}
struct PrivateEvidence {
    directory: PathBuf,
    charged: u64,
    attempted: BTreeSet<&'static str>,
}
impl PrivateEvidence {
    fn new(directory: &Path) -> Self {
        Self {
            directory: directory.to_path_buf(),
            charged: 0,
            attempted: BTreeSet::new(),
        }
    }
    fn bytes(&mut self, name: &'static str, bytes: &[u8]) -> Result<()> {
        need(
            PRIVATE_ORIGINALS.contains(&name),
            "Hybrid private original is outside its fixed custody namespace",
        )?;
        need(
            bytes.len() as u64 <= JSON_FILE_LIMIT,
            "Hybrid private original exceeds its per-file custody limit",
        )?;
        let next = self
            .charged
            .checked_add(bytes.len() as u64)
            .ok_or("Hybrid private original aggregate byte count overflowed")?;
        need(
            next <= PRIVATE_JSON_TOTAL_LIMIT,
            "Hybrid private original aggregate exceeds its independent custody limit",
        )?;
        need(
            self.attempted.insert(name),
            "Hybrid private original already has an attempted custody write",
        )?;
        // Charge before create_new. An I/O failure retains both its partial
        // original and the charge; neither a replacement nor a refill occurs.
        self.charged = next;
        new_bytes(&self.directory.join(name), bytes)
    }
    fn json(&mut self, name: &'static str, value: &impl serde::Serialize) -> Result<()> {
        let bytes = json_bytes(value)?;
        self.bytes(name, &bytes)
    }
}
fn source_wave(bytes: &[u8], expected_sha256: &str, expected_bytes: u64) -> Result<()> {
    need(
        digest_literal(expected_sha256) && expected_bytes == WAV_BYTES,
        "Hybrid original WAV identity is outside its closed profile",
    )?;
    need(
        bytes.len() as u64 == WAV_BYTES && sha256(bytes) == expected_sha256,
        "Hybrid original WAV bytes do not match their admitted identity",
    )?;
    need(
        &bytes[0..4] == b"RIFF"
            && &bytes[8..16] == b"WAVEfmt "
            && &bytes[36..40] == b"data"
            && u32::from_le_bytes(bytes[4..8].try_into()?) == (WAV_BYTES - 8) as u32
            && u32::from_le_bytes(bytes[16..20].try_into()?) == 16
            && u16::from_le_bytes(bytes[20..22].try_into()?) == 1
            && u16::from_le_bytes(bytes[22..24].try_into()?) == 2
            && u32::from_le_bytes(bytes[24..28].try_into()?) == 48_000
            && u32::from_le_bytes(bytes[28..32].try_into()?) == 192_000
            && u16::from_le_bytes(bytes[32..34].try_into()?) == 4
            && u16::from_le_bytes(bytes[34..36].try_into()?) == 16
            && u32::from_le_bytes(bytes[40..44].try_into()?) == (PCM_FRAMES * 4) as u32,
        "Hybrid source is not the unchanged six-second Film180 PCM16 stereo WAV",
    )
}
fn source_node(index: usize) -> CanvasNode {
    let mut source = super::node(index);
    source.name = format!("Original hybrid contribution {index}");
    [source.x, source.y, source.width, source.height] = GEOMETRY[index];
    source.style.fill = Some(COLORS[index].into());
    source
}
fn apply(service: &StudioService, project: &mut Project, key: &str, change: Change) -> Result<()> {
    *project = service
        .apply(project.id, &RevisionStamp::from(&*project), key, &change)?
        .project;
    Ok(())
}
fn master(project: &Project) -> Result<&DeliverableProfile> {
    let mut profiles = project
        .deliverables
        .iter()
        .filter(|profile| profile.name == "Master 16:9");
    let profile = profiles.next().ok_or("Hybrid master profile is absent")?;
    need(
        profiles.next().is_none(),
        "Hybrid master profile is ambiguous",
    )?;
    Ok(profile)
}
fn options(project: &Project) -> Result<FilmBuildOptions> {
    let profile = master(project)?;
    let recipe = project
        .production_design
        .delivery_recipes
        .get(&profile.id)
        .ok_or("Hybrid source has no saved delivery recipe")?;
    need(
        recipe.scene_intents.len() == 3 && recipe.original_mix.is_none(),
        "Hybrid saved narrative source changed",
    )?;
    let mut intents = Vec::new();
    for (scene, intent) in project.scenes.iter().zip(&recipe.scene_intents) {
        need(
            intent.scene_id == scene.id
                && intent.role == Some(DeliveryNarrativeRole::Evidence)
                && intent.archetype == Some(DeliveryArchetype::Statement),
            "Hybrid original scene intent changed",
        )?;
        intents.push(SceneFilmIntent {
            scene_id: scene.id,
            role: NarrativeRole::Evidence,
            archetype: Archetype::Statement,
        });
    }
    Ok(FilmBuildOptions {
        frame_rate: Rate::new(30, 1)?,
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: intents,
    })
}
fn verify_source(project: &Project, expected_sha256: &str) -> Result<()> {
    project.validate()?;
    need(
        digest_literal(expected_sha256) && sha256(&json_bytes(project)?) == expected_sha256,
        "Hybrid saved source differs from the original project digest",
    )?;
    need(
        project.scenes.len() == 3
            && project.deliverables.len() == 3
            && project.audio.voice_tracks.len() == 1
            && project.audio.transcript.is_empty(),
        "Hybrid source roster is not the original three-scene project",
    )?;
    let profile = master(project)?;
    need(
        profile.width == 1920
            && profile.height == 1080
            && profile.frame_rate == RationalTime::new(30, 1)?
            && profile.video_codec == VideoCodec::H264
            && profile.audio_codec == AudioCodec::Aac
            && profile.audio_sample_rate_hz == 48_000
            && profile.container == OutputContainer::Mp4
            && profile.color_space == OutputColorSpace::Rec709
            && !profile.captions
            && !profile.burn_in_captions
            && profile.included_scene_ids.is_empty(),
        "Hybrid master profile changed",
    )?;
    let voice = &project.audio.voice_tracks[0];
    need(
        profile.voice_track_id == Some(voice.id)
            && voice.sample_rate_hz == 48_000
            && voice.channels == 2
            && voice.measured_duration == RationalTime::new(6, 1)?
            && digest_literal(&voice.source_sha256),
        "Hybrid original measured voice changed",
    )?;
    for (index, scene) in project.scenes.iter().enumerate() {
        need(
            scene.start == RationalTime::new(index as i64 * 2, 1)?
                && scene.duration == RationalTime::new(2, 1)?
                && scene.renderer
                    == (if index == 1 {
                        RendererKind::ManimCommunity
                    } else {
                        RendererKind::MotionCanvas
                    })
                && scene.nodes.len() == 1
                && scene.beats.is_empty(),
            "Hybrid source cut or renderer changed",
        )?;
        let node = &scene.nodes[0];
        let mut expected = source_node(index);
        expected.id = node.id;
        need(
            node == &expected,
            "Hybrid original editable geometry, color, or style changed",
        )?;
    }
    let options = options(project)?;
    let _ = build_manim_static_plan_v2(
        project,
        &RevisionStamp::from(project),
        project.scenes[1].id,
        profile.id,
    )?;
    let segments = build_motion_canvas_segments(project, profile.id, &options)?;
    need(
        segments.len() == 2
            && segments[0].scene_ids == [project.scenes[0].id]
            && segments[1].scene_ids == [project.scenes[2].id]
            && segments
                .iter()
                .all(|segment| segment.frame_count == SEGMENT_FRAMES),
        "Hybrid native Motion Canvas partition changed",
    )
}

fn seed_source(
    database: &Path,
    source: &Path,
    expected_sha256: &str,
    expected_bytes: u64,
    project_json: &Path,
) -> Result<Value> {
    // Refuse wrong source/alias/outputs before StudioService can create a Store.
    need(
        digest_literal(expected_sha256) && expected_bytes == WAV_BYTES,
        "Hybrid original source identity is invalid",
    )?;
    let raw = capture_absolute(source, WAV_BYTES)?;
    source_wave(&raw, expected_sha256, expected_bytes)?;
    unused(database)?;
    unused(project_json)?;
    need(
        database != project_json && database != source && project_json != source,
        "Hybrid source and output paths must be distinct",
    )?;
    let service = StudioService::open(database)?;
    need(
        service.projects(2)?.is_empty(),
        "Hybrid source Store must be empty",
    )?;
    let mut project =
        service.create_project("Original editable MC-Manim-MC 180-frame native source")?;
    let mut visual_language = project.visual_language.clone();
    let mut matte = false;
    for token in &mut visual_language.palette {
        if token.name.eq_ignore_ascii_case("background")
            || token.name.eq_ignore_ascii_case("surface")
        {
            token.name.make_ascii_lowercase();
            token.value = "#000000".into();
            matte = true;
        }
    }
    need(
        matte,
        "Hybrid source has no authored background or surface token",
    )?;
    apply(
        &service,
        &mut project,
        "hybrid180:opaque-black-matte",
        Change::SetVisualLanguage { visual_language },
    )?;
    for index in 0..3 {
        apply(
            &service,
            &mut project,
            &format!("hybrid180:scene:{index}"),
            Change::AddScene {
                name: format!("Original native contribution {index}"),
                objective: "Three source-bound native contributions and exact cuts".into(),
                duration_seconds: 2,
            },
        )?;
        let scene_id = project
            .scenes
            .last()
            .ok_or("Hybrid source scene was not created")?
            .id;
        if index == 1 {
            apply(
                &service,
                &mut project,
                "hybrid180:manim-renderer",
                Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::ManimCommunity,
                },
            )?;
        }
        apply(
            &service,
            &mut project,
            &format!("hybrid180:node:{index}"),
            Change::AddCanvasNode {
                scene_id,
                node: source_node(index),
            },
        )?;
    }
    project = service
        .import_voice_file(
            project.id,
            &RevisionStamp::from(&project),
            "hybrid180:original-wave",
            source,
            VoiceImportMetadata {
                name: "original-film180-wave.wav".into(),
                media_type: "audio/wav".into(),
                label: "Original Film180 synthetic source, not narration or approved mix".into(),
            },
        )?
        .project;
    let voice = project
        .audio
        .voice_tracks
        .last()
        .ok_or("Hybrid original WAV import is absent")?
        .clone();
    need(
        voice.source_sha256 == expected_sha256
            && voice.measured_duration == RationalTime::new(6, 1)?
            && voice.sample_rate_hz == 48_000
            && voice.channels == 2,
        "Hybrid original WAV import changed its measured identity",
    )?;
    let mut profile = master(&project)?.clone();
    profile.width = 1920;
    profile.height = 1080;
    profile.frame_rate = RationalTime::new(30, 1)?;
    profile.voice_track_id = Some(voice.id);
    profile.captions = false;
    profile.burn_in_captions = false;
    let profile_id = profile.id;
    apply(
        &service,
        &mut project,
        "hybrid180:master-profile",
        Change::UpsertDeliverable { profile },
    )?;
    let scene_intents = project
        .scenes
        .iter()
        .map(|scene| DeliverySceneIntent {
            scene_id: scene.id,
            role: Some(DeliveryNarrativeRole::Evidence),
            archetype: Some(DeliveryArchetype::Statement),
        })
        .collect();
    apply(
        &service,
        &mut project,
        "hybrid180:saved-intents",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(DeliveryRecipe {
                scene_intents,
                ..Default::default()
            }),
        },
    )?;
    let bytes = json_bytes(&project)?;
    let project_sha256 = sha256(&bytes);
    verify_source(&project, &project_sha256)?;
    // Verify original input again before publishing any successful source proof.
    source_wave(
        &capture_absolute(source, WAV_BYTES)?,
        expected_sha256,
        expected_bytes,
    )?;
    new_bytes(project_json, &bytes)?;
    drop(service);
    let reopened_service = StudioService::open(database)?;
    let reopened = reopened_service.project(project.id)?;
    need(
        reopened == project,
        "Hybrid saved source changed after its Store was closed",
    )?;
    verify_source(&reopened, &project_sha256)?;
    Ok(
        json!({ "schema":"motionwright.native.hybrid-av-source/1", "project_id":project.id,
        "generation":project.generation,"revision":project.revision,"resource":project.resource_key(),
        "project_sha256":project_sha256,"master_profile_id":profile_id,
        "width":1920,"height":1080,"fps_num":30,"fps_den":1,"frame_count":FRAMES,
        "scene_count":3,"deliverable_count":3,"source_segment_frames":[60,60,60],
        "source_renderers":["motion_canvas","manim_static","motion_canvas"],"cut_frames":[60,120],
        "original_audio_sha256":expected_sha256,"original_audio_bytes":expected_bytes,
        "measured_original_audio_frames":PCM_FRAMES,"saved_reopen":true,
        "native_render_performed":false,"automatic_retry":false,"physical_acceptance":false,"human_approval":false }),
    )
}
pub(super) fn seed(
    database: &Path,
    source: &Path,
    expected_sha256: &str,
    expected_bytes: u64,
    project_json: &Path,
) -> Result<()> {
    ci_envelope()?;
    println!(
        "{}",
        serde_json::to_string(&seed_source(
            database,
            source,
            expected_sha256,
            expected_bytes,
            project_json
        )?)?
    );
    Ok(())
}

fn source_plan(
    service: &StudioService,
    project: &Project,
    project_sha256: &str,
    options: &FilmBuildOptions,
) -> Result<Value> {
    let profile_id = master(project)?.id;
    let segments = build_motion_canvas_segments(project, profile_id, options)?;
    need(
        segments.len() == 2,
        "Hybrid source plan must contain two real MC runs",
    )?;
    let middle = prepare_saved_manim_static_source(
        service,
        project.id,
        &RevisionStamp::from(project),
        project.scenes[1].id,
        profile_id,
    )?;
    Ok(
        json!({"schema":"motionwright.native.hybrid-source-plan/1","project_sha256":project_sha256,
        "contributions":[
            {"renderer":"motion_canvas","scene_ids":segments[0].scene_ids,"segment_id":segments[0].id,
                "output_start_frame":0,"frame_count":60},
            {"renderer":"manim_static","scene_ids":[project.scenes[1].id],"plan_sha256":middle.closure.plan_sha256,
                "output_start_frame":60,"frame_count":60},
            {"renderer":"motion_canvas","scene_ids":segments[1].scene_ids,"segment_id":segments[1].id,
                "output_start_frame":120,"frame_count":60}
        ]}),
    )
}
fn serialized_return(result: &Value) -> Value {
    json!({"encoding":"serialized_native_return_not_wire","error":null,"result":result,
        "automatic_retry":false,"host_streams_or_receiver_closure_proved":false})
}
fn original_native_result<T>(
    outcome: semwright_native_sdk::Result<T>,
    evidence: &mut PrivateEvidence,
    name: &'static str,
) -> Result<T> {
    match outcome {
        Ok(value) => Ok(value),
        Err(error) => {
            // Custody failure is secondary. It must not replace the original
            // native error or turn an uncertain effect into a known failure.
            let retained = evidence.json(
                name,
                &json!({"encoding":"serialized_native_return_not_wire",
                "error":&error,"result":null,"automatic_retry":false,
                "host_streams_or_receiver_closure_proved":false}),
            );
            if retained.is_err() {
                eprintln!("hybrid-native-secondary: original-error-file-custody-failed");
            }
            Err(error.into())
        }
    }
}
fn create_private_directory(path: &Path) -> Result<()> {
    unused(path)?;
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)?;
    Ok(())
}
fn retain_original_receipts(
    service: &StudioService,
    project: &Project,
    evidence: &mut PrivateEvidence,
) -> Result<()> {
    let watermark = service.production_receipt_watermark(project.id)?;
    need(
        watermark.count <= 20_000,
        "Hybrid original receipt history exceeds its bounded inventory",
    )?;
    let mut before = None;
    let mut receipts = Vec::new();
    let mut serialized_pages = 0_u64;
    loop {
        let batch =
            service.production_receipts_snapshot_page(project.id, &watermark, before, 256)?;
        if batch.is_empty() {
            break;
        }
        let next = batch.last().map(|receipt| receipt.id);
        need(
            next != before,
            "Hybrid original receipt cursor did not advance",
        )?;
        before = next;
        // Row count alone is not a memory or file limit. Bound each returned
        // page before retaining it in the aggregate; the complete envelope is
        // serialized through the same independent limit before file creation.
        serialized_pages = serialized_pages
            .checked_add(json_bytes(&batch)?.len() as u64)
            .ok_or("Hybrid original receipt snapshot byte count overflowed")?;
        need(
            serialized_pages <= JSON_FILE_LIMIT,
            "Hybrid original receipt snapshot exceeds its independent byte limit",
        )?;
        receipts.extend(batch);
        need(
            receipts.len() as u64 <= watermark.count,
            "Hybrid original receipt history exceeded its snapshot",
        )?;
        if receipts.len() as u64 == watermark.count {
            break;
        }
    }
    need(
        receipts.len() as u64 == watermark.count
            && service.production_receipt_watermark(project.id)? == watermark
            && service.project(project.id)? == *project,
        "Hybrid original receipt/source snapshot changed",
    )?;
    evidence.json(
        "native-production-receipts.private.json",
        &json!({
            "watermark": {"latest_id": watermark.latest_id, "count": watermark.count},
            "receipts": receipts,
            "original_receipt_order_preserved": true,
            "automatic_retry": false,
            "Host_receiver_closure_proved": false,
        }),
    )
}
pub(super) async fn assemble(
    database: &Path,
    connection_path: &Path,
    expected_project_sha256: &str,
    evidence_dir: &Path,
) -> Result<()> {
    ci_envelope()?;
    need(
        digest_literal(expected_project_sha256),
        "Hybrid original project digest is invalid",
    )?;
    regular_parent(database)?;
    let metadata = fs::symlink_metadata(database)?;
    need(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "Hybrid source Store is not an existing regular file",
    )?;
    unused(evidence_dir)?;
    // The exact wrapper retains this seed original before launching this
    // independent process. Do not regenerate an absent original from a Store.
    let original_path = evidence_dir
        .parent()
        .ok_or("Hybrid original evidence parent is absent")?
        .join("hybrid-source.original.json");
    let original_bytes = capture_absolute(&original_path, PROJECT_LIMIT)?;
    need(
        sha256(&original_bytes) == expected_project_sha256,
        "Hybrid original source JSON digest changed",
    )?;
    let original_project: Project = serde_json::from_slice(&original_bytes)?;
    verify_source(&original_project, expected_project_sha256)?;
    let service = StudioService::open(database)?;
    let mut projects = service.projects(2)?;
    need(
        projects.len() == 1,
        "Hybrid source Store does not contain exactly one original project",
    )?;
    let project = projects.remove(0);
    need(
        project == original_project,
        "Hybrid independent Store reopen changed the original editable source",
    )?;
    verify_source(&project, expected_project_sha256)?;
    need(
        service.production_receipts(project.id, 1)?.is_empty(),
        "Hybrid Store has an existing production attempt; inspect originals without redispatch",
    )?;
    let profile_id = master(&project)?.id;
    let options = options(&project)?;
    let plan = source_plan(&service, &project, expected_project_sha256, &options)?;
    let connection = ProductionConnection::load(connection_path)?;
    need(
        connection.resource == project.resource_key(),
        "Hybrid owner connection identifies another source project",
    )?;
    let output = connection.output_root.clone();
    let namespace = format!("mw-hybrid-{}", &expected_project_sha256[..20]);
    for suffix in [
        "source.wav",
        "mc-0.mkv",
        "mc-2.mkv",
        "visual.mkv",
        "master.mp4",
    ] {
        unused(&output.join(format!("{namespace}-{suffix}")))?;
    }
    let voice_id = master(&project)?
        .voice_track_id
        .ok_or("Hybrid original voice selection is absent")?;
    let verified =
        service.verified_master_voice(project.id, &RevisionStamp::from(&project), voice_id)?;
    let wave = capture_absolute(&verified.path, WAV_BYTES)?;
    source_wave(&wave, &verified.sha256, verified.size_bytes)?;
    need(
        verified.sha256 == project.audio.voice_tracks[0].source_sha256,
        "Hybrid saved original voice changed",
    )?;
    // Bind the existing Owner's real project grant before private publication
    // or the first native effect. No caller-supplied grant/path is added to the
    // ProductionConnection, and the fixed CLI remains unchanged.
    let grant_path = PathBuf::from(
        std::env::var_os("MW_HYBRID_PROJECT_GRANT_PATH")
            .ok_or("Hybrid original project-grant receipt is absent")?,
    );
    let grant_sha256 = std::env::var("MW_HYBRID_PROJECT_GRANT_SHA256")
        .map_err(|_| "Hybrid original project-grant digest is absent")?;
    need(
        grant_path
            == evidence_dir
                .parent()
                .ok_or("Hybrid original evidence parent is absent")?
                .join("hybrid-project-grant.original.json"),
        "Hybrid project-grant receipt is outside the original producer namespace",
    )?;
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?
        .with_hybrid_project_grant(
            &grant_path,
            &grant_sha256,
            expected_project_sha256,
            connection_path,
        )?;
    create_private_directory(evidence_dir)?;
    let mut evidence = PrivateEvidence::new(evidence_dir);
    evidence.bytes("source-project.original.json", &original_bytes)?;
    evidence.json("source-project.reopened.json", &project)?;
    evidence.json("hybrid-plan.source-only.json", &plan)?;
    evidence.json("declared-readback-contract.json", &json!({
        "schema":"motionwright.native.hybrid-declared-readback/1","project_sha256":expected_project_sha256,
        "width":1920,"height":1080,"fps_num":30,"fps_den":1,"frame_count":FRAMES,
        "source_segment_frames":[60,60,60],"source_renderers":["motion_canvas","manim_static","motion_canvas"],
        "cut_frames":[60,120],"source_geometry":GEOMETRY,"source_colors":COLORS,
        "measured_original_audio_frames":PCM_FRAMES,"original_audio_sha256":verified.sha256,
        "pixel_difference_policy":"retain_all_original_measurements",
        "automatic_retry":false,"physical_acceptance":false,"human_approval":false}))?;
    let audio = MltAudioArtifact {
        relative_path: format!("{namespace}-source.wav"),
        sha256: verified.sha256,
        sample_rate: 48_000,
        channels: 2,
    };
    // Input inspection above has no output effect. The sealed producer stages
    // these original CAS bytes only after its first durable budget debit and
    // operation intent, before the original native MC dispatch.
    // This concrete high-level entrypoint reserves the complete closed route
    // before its first effect. It does not expose a budgeted generic client.
    let result = assemble_original(
        &coordinator,
        &project,
        profile_id,
        &options,
        &plan,
        &audio,
        &mut evidence,
    )
    .await;
    // Always retain private original transport/receipt state on a failed later
    // phase. A secondary writer failure cannot replace the original result.
    let custody_result = evidence.json(
        "native-receipt-custody.private.json",
        &coordinator.private_receipt_custody_snapshot(),
    );
    let receipts_result = retain_original_receipts(&service, &project, &mut evidence);
    let result = match result {
        Ok(result) => {
            custody_result?;
            receipts_result?;
            result
        }
        Err(error) => {
            if custody_result.is_err() {
                eprintln!("hybrid-native-secondary: receipt-custody-file-failed");
            }
            if receipts_result.is_err() {
                eprintln!("hybrid-native-secondary: original-receipt-export-failed");
            }
            return Err(error);
        }
    };
    evidence.json("result.private.json", &result)?;
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

async fn assemble_original(
    coordinator: &ProductionCoordinator,
    project: &Project,
    profile_id: Uuid,
    options: &FilmBuildOptions,
    plan: &Value,
    audio: &MltAudioArtifact,
    evidence: &mut PrivateEvidence,
) -> Result<Value> {
    let project_sha256 = plan
        .get("project_sha256")
        .and_then(Value::as_str)
        .ok_or("Hybrid original declared project digest is absent")?;
    eprintln!("hybrid-native-stage: original-sources-start");
    let sources = original_native_result(
        coordinator
            .realize_native_hybrid_sources(
                project.id,
                &RevisionStamp::from(project),
                profile_id,
                options,
            )
            .await,
        evidence,
        "hybrid-render.serialized.json",
    )?;
    // Retain the complete native return even when a following join rejects it.
    evidence.json(
        "hybrid-render.serialized.json",
        &serialized_return(sources.evidence()),
    )?;
    need(
        sources.evidence().get("plan") == Some(plan),
        "Hybrid native realizations differ from the declared original plan",
    )?;
    eprintln!("hybrid-native-stage: original-timeline-start");
    let timeline = original_native_result(
        coordinator.assemble_native_hybrid_timeline(&sources).await,
        evidence,
        "hybrid-timeline.serialized.json",
    )?;
    evidence.json(
        "hybrid-timeline.serialized.json",
        &serialized_return(timeline.evidence()),
    )?;
    eprintln!("hybrid-native-stage: original-master-start");
    let master = original_native_result(
        coordinator
            .assemble_native_hybrid_master(&sources, &timeline, audio)
            .await,
        evidence,
        "hybrid-master.serialized.json",
    )?;
    evidence.json(
        "hybrid-master.serialized.json",
        &serialized_return(master.evidence()),
    )?;
    let native_master = master
        .evidence()
        .pointer("/master/artifact")
        .ok_or("Hybrid native master artifact is absent")?;
    Ok(json!({"schema":"motionwright.native.hybrid-av-summary/1",
        "project_id":project.id,"generation":project.generation,"revision":project.revision,
        "project_sha256":project_sha256,"width":1920,"height":1080,"fps_num":30,"fps_den":1,
        "frame_count":FRAMES,"source_segment_frames":[60,60,60],
        "source_renderers":["motion_canvas","manim_static","motion_canvas"],"cut_frames":[60,120],
        "measured_original_audio_frames":PCM_FRAMES,"original_audio_sha256":audio.sha256,
        "master_artifact":native_master,"master_artifact_path":native_master.get("path"),
        "master_artifact_sha256":native_master.get("sha256"),"master_artifact_bytes":native_master.get("bytes"),
        "visual_artifact_path":timeline.evidence().get("artifact_path"),
        "visual_artifact_sha256":timeline.evidence().get("artifact_sha256"),
        "visual_artifact_bytes":timeline.evidence().get("artifact_bytes"),
        "editable_source_reopened_unchanged":true,"automatic_retry":false,
        "Host_receiver_closure_proved":false,"packet_PTS_and_AAC_priming_verified":false,
        "physical_acceptance":false,"human_approval":false}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hybrid_private_original_limits_refuse_before_publish_and_preserve_failed_write_charge() {
        let dir = tempfile::tempdir().unwrap();
        let mut evidence = PrivateEvidence::new(dir.path());
        // A serialized original includes JSON quotes/newline. The caller may
        // not use that value's own length as its supposed independent quota.
        let oversized = "x".repeat(JSON_FILE_LIMIT as usize);
        assert!(evidence.json("result.private.json", &oversized).is_err());
        assert!(!dir.path().join("result.private.json").exists());
        assert_eq!(evidence.charged, 0);
        drop(oversized);

        let original = dir.path().join("source-project.original.json");
        fs::write(&original, b"preserve original failed-attempt custody").unwrap();
        assert!(
            evidence
                .json("source-project.original.json", &json!({"changed":true}))
                .is_err()
        );
        let failed_charge = evidence.charged;
        assert!(failed_charge > 0);
        assert!(
            evidence
                .json("source-project.original.json", &json!({"changed":false}))
                .is_err()
        );
        assert_eq!(evidence.charged, failed_charge);
        assert_eq!(
            fs::read(&original).unwrap(),
            b"preserve original failed-attempt custody"
        );

        // Exercise the remaining aggregate boundary without materializing
        // 32 MiB of synthetic custody files in a unit control.
        evidence.charged = PRIVATE_JSON_TOTAL_LIMIT - 2;
        assert!(
            evidence
                .json("source-project.reopened.json", &false)
                .is_err()
        );
        assert!(!dir.path().join("source-project.reopened.json").exists());
        assert_eq!(evidence.charged, PRIVATE_JSON_TOTAL_LIMIT - 2);
        assert!(evidence.json("not-an-original.json", &Value::Null).is_err());
        assert!(!dir.path().join("not-an-original.json").exists());
    }
    fn original_wave(directory: &Path) -> (PathBuf, String) {
        // Synthetic unit-test bytes are not retained production evidence.
        let mut bytes = Vec::with_capacity(WAV_BYTES as usize);
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&((WAV_BYTES - 8) as u32).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16_u32.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&2_u16.to_le_bytes());
        bytes.extend_from_slice(&48_000_u32.to_le_bytes());
        bytes.extend_from_slice(&192_000_u32.to_le_bytes());
        bytes.extend_from_slice(&4_u16.to_le_bytes());
        bytes.extend_from_slice(&16_u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&((PCM_FRAMES * 4) as u32).to_le_bytes());
        for index in 0..PCM_FRAMES {
            bytes.extend_from_slice(&((((index % 120) as i16) - 60) * 220).to_le_bytes());
            bytes.extend_from_slice(&((((index % 80) as i16) - 40) * 230).to_le_bytes());
        }
        let sha = sha256(&bytes);
        let path = directory.join("original-wave.wav");
        new_bytes(&path, &bytes).unwrap();
        (path, sha)
    }
    #[test]
    fn hybrid_seed_reopens_exact_three_renderer_source_and_original_wave() {
        let dir = tempfile::tempdir().unwrap();
        let (wave, sha) = original_wave(dir.path());
        let before = fs::read(&wave).unwrap();
        let db = dir.path().join("hybrid.sqlite");
        let project_file = dir.path().join("hybrid-source.original.json");
        let result = seed_source(&db, &wave, &sha, WAV_BYTES, &project_file).unwrap();
        let service = StudioService::open(&db).unwrap();
        let project = service.projects(2).unwrap().remove(0);
        verify_source(&project, result["project_sha256"].as_str().unwrap()).unwrap();
        assert_eq!(fs::read(&wave).unwrap(), before);
        assert_eq!(
            fs::read(project_file).unwrap(),
            json_bytes(&project).unwrap()
        );
        assert!(
            service
                .production_receipts(project.id, 10)
                .unwrap()
                .is_empty()
        );
        assert_eq!(result["frame_count"], 180);
        assert_eq!(
            result["source_renderers"],
            json!(["motion_canvas", "manim_static", "motion_canvas"])
        );
    }
    #[test]
    fn hybrid_wrong_original_identity_cannot_create_a_store_or_source_receipt() {
        let dir = tempfile::tempdir().unwrap();
        let (wave, sha) = original_wave(dir.path());
        let before = fs::read(&wave).unwrap();
        for (index, (digest, size)) in [("0".repeat(64), WAV_BYTES), (sha.clone(), 211_244)]
            .into_iter()
            .enumerate()
        {
            let db = dir.path().join(format!("refused-{index}.sqlite"));
            let source = dir.path().join(format!("refused-{index}.json"));
            assert!(seed_source(&db, &wave, &digest, size, &source).is_err());
            assert!(!db.exists());
            assert!(!source.exists());
        }
        assert_eq!(fs::read(&wave).unwrap(), before);
    }
    #[test]
    fn hybrid_original_source_change_cannot_qualify_as_reopened_original() {
        let dir = tempfile::tempdir().unwrap();
        let (wave, sha) = original_wave(dir.path());
        let db = dir.path().join("hybrid.sqlite");
        let original = dir.path().join("hybrid-source.original.json");
        let seeded = seed_source(&db, &wave, &sha, WAV_BYTES, &original).unwrap();
        let service = StudioService::open(&db).unwrap();
        let mut changed = service.projects(2).unwrap().remove(0);
        changed.scenes[1].nodes[0].style.fill = Some("#FF0000".into());
        assert!(verify_source(&changed, seeded["project_sha256"].as_str().unwrap()).is_err());
        // Even a newly computed digest cannot relabel a changed source as this
        // declared original fixture; a future edit must own its own contract.
        assert!(verify_source(&changed, &sha256(&json_bytes(&changed).unwrap())).is_err());
    }
    #[test]
    fn hybrid_existing_original_project_receipt_is_preserved() {
        let dir = tempfile::tempdir().unwrap();
        let (wave, sha) = original_wave(dir.path());
        let db = dir.path().join("hybrid.sqlite");
        let original = dir.path().join("hybrid-source.original.json");
        fs::write(&original, b"retain this original").unwrap();
        assert!(seed_source(&db, &wave, &sha, WAV_BYTES, &original).is_err());
        assert!(!db.exists());
        assert_eq!(fs::read(original).unwrap(), b"retain this original");
    }
}
