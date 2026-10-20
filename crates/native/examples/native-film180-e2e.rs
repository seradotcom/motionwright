//! An original, bounded CI-only 180-frame two-scene native Film producer.
//!
//! Reuses Motionwright StudioService, the persisted DeliveryRecipe,
//! ProductionCoordinator, and the pinned Semwright Driver Host. No alternate
//! renderer, untrusted project code, imported media or retry of UNKNOWN effects.
//! Native rendering is NEVER performed by the seed/readback source tests.
use motionwright_domain::{
    AlignmentEvidence, BlendMode, CanvasNode, Change, CoordinateSpace, DeliveryArchetype,
    DeliveryNarrativeRole, DeliveryRecipe, DeliverySceneIntent, NodeStyle, Project, RationalTime,
    RevisionStamp, TranscriptSegment, caption_sidecar,
};
use motionwright_native::{
    film::{
        FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
        SceneFilmIntent, build_motion_canvas_segments,
    },
    production::{
        MltAudioArtifact, MltAvMasterRequest, MotionCanvasRenderEvidence, ProductionConnection,
        ProductionCoordinator,
    },
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use semwright_media_time::{Rate, ResolvedCue};
use semwright_motion_authoring::{Archetype, NarrativeRole};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, HashSet},
    env,
    error::Error,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

type FixtureResult<T> = Result<T, Box<dyn Error>>;
const FRAME_COUNT: u64 = 180;
const FRAME_RATE: u32 = 30;
const PCM_RATE: u32 = 48_000;
const PCM_FRAMES: u32 = 288_000;
const CAPTION_WINDOWS: [(i64, i64); 2] = [(6, 27), (33, 72)];
const NATIVE_REQUEST: &str = "film180-original-native-single-run-v1";
const EDITED_REQUEST: &str = "film180-edited-native-single-run-v1";

fn usage() -> ! {
    eprintln!(
        "usage: native-film180-e2e seed FRESH_DATABASE NEW_SOURCE_WAV NEW_PROJECT_JSON |\
         readback EXISTING_DATABASE NEW_OBSERVATION_JSON |\
         edit EXISTING_DATABASE ORIGINAL_NATIVE_EVIDENCE NEW_EDITED_PROJECT_JSON |\
         render EXISTING_DATABASE OWNED_CONNECTION NEW_EVIDENCE_JSON |\
         stage-audio EXISTING_DATABASE OWNED_CONNECTION SOURCE_WAV OWNER_FILENAME NEW_AUDIO_RECEIPT |\
         master EXISTING_DATABASE OWNED_CONNECTION NATIVE_FILM_RESULT WAV_OWNER_RELATIVE WAV_SHA256 NEW_MASTER_EVIDENCE"
    );
    std::process::exit(2);
}

fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

fn must_be_unused(path: &Path) -> FixtureResult<()> {
    if !path.is_absolute()
        || path.is_symlink()
        || path.exists()
        || !path.parent().is_some_and(Path::is_dir)
    {
        return Err(
            "native Film180 output requires a fresh absolute path with an existing parent".into(),
        );
    }
    Ok(())
}

fn source_node(
    label: &str,
    kind: &str,
    text: Option<&str>,
    x: f64,
    y: f64,
    ordinal: i32,
) -> CanvasNode {
    let is_text = kind == "text";
    CanvasNode {
        id: Uuid::now_v7(),
        name: label.into(),
        kind: kind.into(),
        parent_id: None,
        x,
        y,
        width: if is_text { 1040.0 } else { 150.0 },
        height: if is_text { 120.0 } else { 150.0 },
        rotation_deg: 0.0,
        opacity: 1.0,
        text: text.map(str::to_owned),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: ordinal,
        style: NodeStyle {
            fill: Some(if is_text {
                "#F5F5F2".into()
            } else {
                "#F5B84A".into()
            }),
            stroke: None,
            stroke_width: 0.0,
            font_family: is_text.then(|| MOTION_CANVAS_FONT_FAMILY.into()),
            font_size: is_text.then_some(44.0),
            font_weight: is_text.then_some(700),
            line_height: is_text.then_some(1.1),
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

fn apply(
    service: &StudioService,
    project: &mut Project,
    key: &str,
    change: Change,
) -> FixtureResult<()> {
    *project = service
        .apply(project.id, &RevisionStamp::from(&*project), key, &change)?
        .project;
    Ok(())
}

fn write_synthetic_stereo(path: &Path) -> FixtureResult<()> {
    must_be_unused(path)?;
    // 48kHz PCM16 stereo; first-party synthetic distinct sawtooth sources.
    // These are not narration, captured human speech, or a loudness approval.
    let length = PCM_FRAMES
        .checked_mul(4)
        .ok_or("audio frame-size overflow")?;
    let mut output = OpenOptions::new().create_new(true).write(true).open(path)?;
    output.write_all(b"RIFF")?;
    output.write_all(&(length + 36).to_le_bytes())?;
    output.write_all(b"WAVEfmt ")?;
    output.write_all(&16_u32.to_le_bytes())?;
    output.write_all(&1_u16.to_le_bytes())?;
    output.write_all(&2_u16.to_le_bytes())?;
    output.write_all(&PCM_RATE.to_le_bytes())?;
    output.write_all(&(PCM_RATE * 4).to_le_bytes())?;
    output.write_all(&4_u16.to_le_bytes())?;
    output.write_all(&16_u16.to_le_bytes())?;
    output.write_all(b"data")?;
    output.write_all(&length.to_le_bytes())?;
    for i in 0..PCM_FRAMES {
        let l = ((i % 120) as i16 - 60) * 220;
        let r = ((i % 80) as i16 - 40) * 230;
        output.write_all(&l.to_le_bytes())?;
        output.write_all(&r.to_le_bytes())?;
    }
    output.sync_all()?;
    if fs::metadata(path)?.len() != u64::from(length + 44) {
        return Err("first-party original audio source byte count changed".into());
    }
    Ok(())
}

fn authored_options(project: &Project) -> FixtureResult<FilmBuildOptions> {
    if project.scenes.len() != 2
        || project
            .scenes
            .iter()
            .any(|s| s.duration != RationalTime::new(3, 1).unwrap())
        || project.scenes[0].start != RationalTime::ZERO
        || project.scenes[1].start != RationalTime::new(3, 1)?
        || project.scenes[0].nodes.len() != 3
        || project.scenes[1].nodes.len() != 1
    {
        return Err("not the authentic saved first-party two-scene Film180 source".into());
    }
    let profile = project
        .deliverables
        .first()
        .ok_or("source has no deliverable")?;
    if profile.width != 1920
        || profile.height != 1080
        || profile.frame_rate != RationalTime::new(30, 1)?
        || !profile.captions
        || !profile.burn_in_captions
        || profile.voice_track_id.is_none()
    {
        return Err("saved Film180 deliverable profile is not admitted".into());
    }
    let recipe = project
        .production_design
        .delivery_recipes
        .get(&profile.id)
        .ok_or("saved 180-frame native source is missing its delivery recipe")?;
    let expected = [
        (
            DeliveryNarrativeRole::Evidence,
            DeliveryArchetype::EvidenceFrame,
        ),
        (
            DeliveryNarrativeRole::Evidence,
            DeliveryArchetype::Statement,
        ),
    ];
    if recipe.scene_intents.len() != 2 || recipe.original_mix.is_some() {
        return Err("saved Film180 narrative source was changed".into());
    }
    let mut intents = Vec::new();
    for (i, scene) in project.scenes.iter().enumerate() {
        let intent = recipe
            .scene_intents
            .get(i)
            .ok_or("source scene intent missing")?;
        if intent.scene_id != scene.id
            || intent.role != Some(expected[i].0)
            || intent.archetype != Some(expected[i].1)
        {
            return Err(
                "saved narrative role/archetype source no longer matches the project".into(),
            );
        }
        intents.push(SceneFilmIntent {
            scene_id: scene.id,
            role: NarrativeRole::Evidence,
            archetype: if i == 0 {
                Archetype::EvidenceFrame
            } else {
                Archetype::Statement
            },
        });
    }
    let source_ids = project
        .scenes
        .iter()
        .flat_map(|s| s.nodes.iter().map(|n| n.id))
        .collect::<HashSet<_>>();
    if source_ids.len() != 4 {
        return Err("native Film180 source node identities are missing or duplicated".into());
    }
    let selected = profile
        .voice_track_id
        .ok_or("source did not select original voice")?;
    let voice = project
        .audio
        .voice_tracks
        .iter()
        .find(|v| v.id == selected)
        .ok_or("source voice identity not found")?;
    if voice.measured_duration != RationalTime::new(6, 1)?
        || voice.sample_rate_hz != PCM_RATE
        || voice.channels != 2
        || project.audio.transcript.len() != 2
        || project
            .audio
            .transcript
            .iter()
            .any(|c| c.voice_track_id != selected)
    {
        return Err("persisted synthetic voice/caption source is not admitted".into());
    }
    for (cue, (first, last)) in project.audio.transcript.iter().zip(CAPTION_WINDOWS) {
        let rate = i64::from(FRAME_RATE);
        if cue.start != RationalTime::new(first, rate)?
            || cue.end != RationalTime::new(last, rate)?
            || cue.alignment != AlignmentEvidence::Manual
        {
            return Err("original manually aligned caption windows were altered".into());
        }
    }
    Ok(FilmBuildOptions {
        frame_rate: Rate::new(FRAME_RATE, 1)?,
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: intents,
    })
}

fn verified_projection(project: &Project) -> FixtureResult<String> {
    project.validate()?;
    let profile = &project.deliverables[0];
    let opts = authored_options(project)?;
    let segments = build_motion_canvas_segments(project, profile.id, &opts)?;
    if segments.len() != 1
        || segments[0].frame_count != FRAME_COUNT
        || segments[0].duration != RationalTime::new(6, 1)?
        || segments[0].scene_ids != project.scenes.iter().map(|s| s.id).collect::<Vec<_>>()
        || segments[0].film.sequences.len() != 1
        || segments[0].film.sequences[0].beats.len() != 2
        || segments[0].film.cues.cues.len() != 2
    {
        return Err("native Film180 projection does not conserve the single original run".into());
    }
    let resolved = segments[0].film.cues.resolve()?;
    let windows = resolved
        .values()
        .map(|c| match c {
            ResolvedCue::Resolved { start, end } => {
                Ok((start.num * 30 / start.den, end.num * 30 / end.den))
            }
            ResolvedCue::Unknown { .. } => Err("Film180 caption cue became unknown"),
        })
        .collect::<Result<BTreeSet<_>, _>>()?;
    if windows != BTreeSet::from(CAPTION_WINDOWS) {
        return Err("native Film180 caption boundaries differ from saved manual voice".into());
    }
    let srt = caption_sidecar(project, profile.id)?;
    if srt.cue_count != 2
        || !srt.body.contains("00:00:00.200 --> 00:00:00.900")
        || !srt.body.contains("00:00:01.100 --> 00:00:02.400")
    {
        return Err("native Film180 sidecar disagrees with original caption source".into());
    }
    let film = serde_json::to_vec(&segments[0].film)?;
    Ok(hex::encode(Sha256::digest(&film)))
}

fn seed_original(database: &Path, wav_path: &Path, project_json: &Path) -> FixtureResult<Project> {
    for path in [database, wav_path, project_json] {
        must_be_unused(path)?;
    }
    if database == wav_path || database == project_json || wav_path == project_json {
        return Err("source database, PCM and project receipt must be distinct".into());
    }
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("original Film180 source database is not empty".into());
    }
    let mut project = service.create_project("Original synthetic two-shot native Film")?;
    for (i, title) in ["Opening evidence", "Following evidence"]
        .into_iter()
        .enumerate()
    {
        apply(
            &service,
            &mut project,
            &format!("film180:add-scene:{i}"),
            Change::AddScene {
                name: title.into(),
                objective: "Two independently editable authoring scenes, one native run".into(),
                duration_seconds: 3,
            },
        )?;
    }
    let ids = project.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
    for (i, node) in [
        source_node(
            "Source title",
            "text",
            Some("First authored synthetic title"),
            180.,
            140.,
            1,
        ),
        source_node(
            "Source detail",
            "text",
            Some("Authorial source remains editable"),
            180.,
            330.,
            2,
        ),
        source_node("Source shape", "rectangle", None, 1310., 360., 3),
        source_node(
            "Second source title",
            "text",
            Some(ORIGINAL_SECOND_TEXT),
            180.,
            160.,
            1,
        ),
    ]
    .into_iter()
    .enumerate()
    {
        apply(
            &service,
            &mut project,
            &format!("film180:add-node:{i}"),
            Change::AddCanvasNode {
                scene_id: ids[usize::from(i == 3)],
                node,
            },
        )?;
    }
    write_synthetic_stereo(wav_path)?;
    project = service
        .import_voice_file(
            project.id,
            &RevisionStamp::from(&project),
            "film180:import-original-synthetic-wave",
            wav_path,
            VoiceImportMetadata {
                name: "first-party-two-tone.wav".into(),
                media_type: "audio/wav".into(),
                label: "Synthetic six-second source, not actual narration".into(),
            },
        )?
        .project;
    let voice = project
        .audio
        .voice_tracks
        .last()
        .ok_or("original synthetic WAV import did not create an admissible voice")?
        .clone();
    if voice.measured_duration != RationalTime::new(6, 1)?
        || voice.sample_rate_hz != PCM_RATE
        || voice.channels != 2
    {
        return Err("original synthetic audio measured profile changed during import".into());
    }
    let mut profile = project.deliverables[0].clone();
    profile.captions = true;
    profile.burn_in_captions = true;
    profile.voice_track_id = Some(voice.id);
    apply(
        &service,
        &mut project,
        "film180:source-profile",
        Change::UpsertDeliverable { profile },
    )?;
    for (i, ((first, last), message)) in CAPTION_WINDOWS
        .into_iter()
        .zip([
            "Opening source-bound caption",
            "Second source-bound caption",
        ])
        .enumerate()
    {
        apply(
            &service,
            &mut project,
            &format!("film180:source-cue:{i}"),
            Change::AddTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::now_v7(),
                    voice_track_id: voice.id,
                    start: RationalTime::new(first, i64::from(FRAME_RATE))?,
                    end: RationalTime::new(last, i64::from(FRAME_RATE))?,
                    text: message.into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            },
        )?;
    }
    let profile_id = project.deliverables[0].id;
    apply(
        &service,
        &mut project,
        "film180:persist-scene-intents",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(DeliveryRecipe {
                scene_intents: vec![
                    DeliverySceneIntent {
                        scene_id: ids[0],
                        role: Some(DeliveryNarrativeRole::Evidence),
                        archetype: Some(DeliveryArchetype::EvidenceFrame),
                    },
                    DeliverySceneIntent {
                        scene_id: ids[1],
                        role: Some(DeliveryNarrativeRole::Evidence),
                        archetype: Some(DeliveryArchetype::Statement),
                    },
                ],
                ..Default::default()
            }),
        },
    )?;

    let film_sha = verified_projection(&project)?;
    let original_bytes = serde_json::to_vec_pretty(&project)?;
    let mut source_file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(project_json)?;
    source_file.write_all(&original_bytes)?;
    source_file.write_all(b"\n")?;
    source_file.sync_all()?;
    drop(service);
    let fresh_service = StudioService::open(database)?;
    let reopened = fresh_service.project(project.id)?;
    if reopened != project || verified_projection(&reopened)? != film_sha {
        return Err(
            "fresh-store saved Film180 readback changed source identities or native Film".into(),
        );
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "schema":"motionwright.native.film180.synthetic-source/1",
            "project_id":project.id,"generation":project.generation,
            "revision":project.revision,"scene_ids":ids,
            "deliverable_id":profile_id,
            "synthetic_WAV_sha256":voice.source_sha256,
            "canonical_Film_sha256":film_sha,
            "frame_count":FRAME_COUNT,"scene_boundary_frame":90,
            "manually_timed_caption_frames":CAPTION_WINDOWS,
            "saved_reopen":true,"native_render_performed":false,
            "source_only":true,"human_approval":false
        }))?
    );
    Ok(project)
}

fn bound_stage_request(project: &Project) -> FixtureResult<&'static str> {
    let second = &project.scenes[1];
    let [node] = second.nodes.as_slice() else {
        return Err("Film180 stage source lost its single editable second subject".into());
    };
    match node.text.as_deref() {
        Some(ORIGINAL_SECOND_TEXT) => Ok(NATIVE_REQUEST),
        Some(EDITED_SECOND_TEXT) => Ok(EDITED_REQUEST),
        _ => Err("Film180 original/edited source text is not an admitted revision".into()),
    }
}

async fn render_original(database: &Path, connection: &Path, evidence: &Path) -> FixtureResult<()> {
    if env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("Film180 original native effects require disposable GitHub Actions".into());
    }
    must_be_unused(evidence)?;
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    let [project] = projects.as_slice() else {
        return Err("native Film180 requires exactly one original saved project".into());
    };
    let original = project.clone();
    let film_sha = verified_projection(&original)?;
    let connection = ProductionConnection::load(connection)?;
    if connection.resource != original.resource_key() {
        return Err(
            "native Film180 owner connection is not bound to the exact saved project".into(),
        );
    }
    let request_id = bound_stage_request(&original)?;
    if request_id == EDITED_REQUEST {
        // A later revision is not evidence that the first physical render
        // succeeded. The prior, separately authorized native result and
        // native verification must have durable original receipts.
        for (suffix, command) in [
            (
                "motion:0:render-result",
                "driver.motion-canvas.render.result",
            ),
            ("motion:0:verify", "driver.motion-canvas.composition.verify"),
        ] {
            let prior = format!("{NATIVE_REQUEST}:{suffix}");
            let receipt = service
                .latest_production_receipt(original.id, &prior)?
                .ok_or("original terminal native receipt is absent")?;
            if receipt.stage != "completed"
                || receipt.command != command
                || receipt.generation != original.generation
                || receipt.revision >= original.revision
            {
                return Err(
                    "edited native Film180 cannot precede the original verified result".into(),
                );
            }
        }
    }
    // Mirror the original ProductionCoordinator effect IDs exactly. A
    // generic ":start" would miss an already applied composition or a render
    // whose native outcome is UNKNOWN; never issue that mutation twice.
    for suffix in ["motion:0:apply", "motion:0:render-start"] {
        let effect_id = format!("{request_id}:{suffix}");
        if service
            .latest_production_receipt(original.id, &effect_id)?
            .is_some()
        {
            return Err(
                "native Film180 original effect identity already exists; inspect outcome, never retry"
                    .into(),
            );
        }
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let outcome = coordinator
        .render_motion_canvas_segments(
            original.id,
            &RevisionStamp::from(&original),
            request_id,
            original.deliverables[0].id,
            &authored_options(&original)?,
        )
        .await;
    match outcome {
        Ok(native) => {
            if native.generation != original.generation
                || native.revision != original.revision
                || native.segments.len() != 1
                || native.segments[0].frame_count != FRAME_COUNT
            {
                return Err(
                    "native Film180 result differs from exact source revision/clock".into(),
                );
            }
            let original_verified = verified_projection(&original)?;
            if original_verified != film_sha {
                return Err("original Film180 source changed during native dispatch".into());
            }
            let body = serde_json::to_vec_pretty(&native)?;
            if body.len() > 8 * 1024 * 1024 {
                return Err("native Film180 evidence outside bounded receipt budget".into());
            }
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(evidence)?;
            file.write_all(&body)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            println!(
                "{}",
                serde_json::to_string(&json!({
                    "schema":"motionwright.native.film180.original-result/1",
                    "native_film180_host":"SUCCEEDED_REPORTED",
                    "canonical_Film_sha256":film_sha,
                    "project_id":original.id,"generation":original.generation,
                    "revision":original.revision,"frame_count":FRAME_COUNT,
                    "physical_pixels_independently_decoded":false,
                    "original_audio_master_produced":false,
                    "human_approval":false
                }))?
            );
        }
        Err(error) => {
            let diag = evidence.with_file_name("original-film180-native-diagnostic.json");
            // Preserve original unknown outcome, including code and candidates,
            // without resending or mutating it. This file is private CI custody.
            if must_be_unused(&diag).is_ok() {
                let payload = serde_json::to_vec_pretty(&json!({
                    "schema":"motionwright.private-film180-native-diagnostic/1",
                    "project_id":original.id,"generation":original.generation,
                    "revision":original.revision,"original_request_id":request_id,
                    "code":format!("{:?}",error.code),
                    "outcome_known":error.outcome_known,
                    "candidates":error.candidates,
                    "error":error.message,
                    "rerun_authorized":false
                }))?;
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&diag)?;
                file.write_all(&payload)?;
                file.write_all(b"\n")?;
                file.sync_all()?;
            }
            return Err(
                "Original native Film180 producer failed; preserve diagnostic, never retry".into(),
            );
        }
    }
    Ok(())
}

const ORIGINAL_SECOND_TEXT: &str = "New scene retains original intent";
const EDITED_SECOND_TEXT: &str = "Edited scene retained after reopening";

/// A new synthetic CI source revision is admissible only after the *original*
/// native producer returned a terminal, source-bound result and its original
/// start receipt exists. This does not inspect pixels or grant any effect.
fn edit_saved_project(
    database: &Path,
    first_render_evidence: &Path,
    project_json: &Path,
) -> FixtureResult<()> {
    if env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("Film180 edit-after-native fixture is restricted to disposable CI".into());
    }
    must_be_unused(project_json)?;
    if !first_render_evidence.is_absolute()
        || first_render_evidence.is_symlink()
        || !first_render_evidence.is_file()
        || !(1..=8 * 1024 * 1024).contains(&fs::metadata(first_render_evidence)?.len())
    {
        return Err(
            "source-bound first native result file is missing or outside the budget".into(),
        );
    }
    let source: MotionCanvasRenderEvidence =
        serde_json::from_slice(&fs::read(first_render_evidence)?)?;
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    let [original] = projects.as_slice() else {
        return Err("source-bound original Film180 project not found".into());
    };
    let old_film_sha = verified_projection(original)?;
    if source.project_resource != original.resource_key()
        || source.generation != original.generation
        || source.revision != original.revision
        || source.deliverable_id != original.deliverables[0].id
        || source.frame_rate != Rate::new(FRAME_RATE, 1)?
        || source.segments.len() != 1
        || source.segments[0].frame_count != FRAME_COUNT
        || source.segments[0].scene_ids != original.scenes.iter().map(|s| s.id).collect::<Vec<_>>()
        || source.segments[0]
            .verification
            .pointer("/report/support_level")
            .and_then(serde_json::Value::as_str)
            != Some("native")
    {
        return Err("original native render receipt is not bound to this saved source".into());
    }
    // A completed start alone is not a terminal result. Require the
    // original result and native verification under the same revision.
    for (suffix, command) in [
        ("motion:0:apply", "driver.motion-canvas.composition.apply"),
        ("motion:0:render-start", "driver.motion-canvas.render.start"),
        (
            "motion:0:render-result",
            "driver.motion-canvas.render.result",
        ),
        ("motion:0:verify", "driver.motion-canvas.composition.verify"),
    ] {
        let id = format!("{NATIVE_REQUEST}:{suffix}");
        let receipt = service
            .latest_production_receipt(original.id, &id)?
            .ok_or("original native effect receipts are incomplete")?;
        if receipt.stage != "completed"
            || receipt.command != command
            || receipt.generation != original.generation
            || receipt.revision != original.revision
        {
            return Err(
                "original native receipt has wrong command, revision or terminal status".into(),
            );
        }
    }
    let second = &original.scenes[1];
    let [node] = second.nodes.as_slice() else {
        return Err("original second source scene is not one editable subject".into());
    };
    if node.kind != "text" || node.text.as_deref() != Some(ORIGINAL_SECOND_TEXT) {
        return Err(
            "original source second editable text no longer matches admitted content".into(),
        );
    }
    let scene_id = second.id;
    let node_id = node.id;
    let baseline = original.clone();
    let edited = service
        .apply(
            baseline.id,
            &RevisionStamp::from(&baseline),
            "film180:edit-existing-second-text-after-native",
            &Change::UpdateCanvasText {
                scene_id,
                node_id,
                text: Some(EDITED_SECOND_TEXT.into()),
            },
        )?
        .project;
    if edited.revision != baseline.revision + 1
        || edited.generation != baseline.generation
        || edited.scenes[0] != baseline.scenes[0]
        || edited.scenes[1].nodes[0].id != node_id
        || edited.scenes[1].nodes[0].text.as_deref() != Some(EDITED_SECOND_TEXT)
        || edited.audio != baseline.audio
        || edited.assets != baseline.assets
        || edited.deliverables != baseline.deliverables
        || edited.production_design != baseline.production_design
    {
        return Err("native original edit changed content beyond the one authorized text".into());
    }
    let edited_film_sha = verified_projection(&edited)?;
    if edited_film_sha == old_film_sha {
        return Err("saved editable source text changed but native Film stayed identical".into());
    }
    let bytes = serde_json::to_vec_pretty(&edited)?;
    let mut out = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(project_json)?;
    out.write_all(&bytes)?;
    out.write_all(b"\n")?;
    out.sync_all()?;
    drop(service);
    let reopened = StudioService::open(database)?.project(edited.id)?;
    if reopened != edited || verified_projection(&reopened)? != edited_film_sha {
        return Err("edited Film180 project did not survive a new SQLite open".into());
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "schema":"motionwright.native.film180.edited-after-native/1",
            "project_id":edited.id,"generation":edited.generation,
            "baseline_revision":baseline.revision,"revision":edited.revision,
            "source_node_id":node_id,"source_scene_id":scene_id,
            "original_Film_sha256":old_film_sha,"edited_Film_sha256":edited_film_sha,
            "frame_count":FRAME_COUNT,
            "first_native_result_source_bound":true,
            "new_render_performed":false,"human_approval":false
        }))?
    );
    Ok(())
}

fn verify_original_audio_file_binding(relative: &str, sha256: &str) -> FixtureResult<()> {
    let path = Path::new(relative);
    if relative.len() < 5
        || relative.len() > 160
        || !relative.ends_with(".wav")
        || path.components().count() != 1
        || !matches!(
            path.components().next(),
            Some(std::path::Component::Normal(_))
        )
        || !relative
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
        || relative.starts_with('.')
    {
        return Err(
            "native Film180 source audio must be one closed owner-output WAV component".into(),
        );
    }
    if sha256.len() != 64
        || !sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("native Film180 source WAV digest is not lowercase SHA256".into());
    }
    Ok(())
}

/// Admit an already imported, first-party synthetic WAV into the exact
/// owner output root that the authorized Host uses. No arbitrary source,
/// external rights assumption, native effect or silent overwrite is allowed.
fn stage_owned_source_wav(
    database: &Path,
    connection_path: &Path,
    original_wav: &Path,
    owner_name: &str,
    receipt: &Path,
) -> FixtureResult<()> {
    if env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("Film180 original WAV staging requires a disposable CI runner".into());
    }
    must_be_unused(receipt)?;
    if !original_wav.is_absolute() || original_wav.is_symlink() || !original_wav.is_file() {
        return Err(
            "original imported first-party source WAV is not an absolute ordinary file".into(),
        );
    }
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    let [project] = projects.as_slice() else {
        return Err("source-bound WAV staging requires one original saved project".into());
    };
    verified_projection(project)?;
    let profile = &project.deliverables[0];
    let voice_id = profile
        .voice_track_id
        .ok_or("original saved voice not selected")?;
    let voice = project
        .audio
        .voice_tracks
        .iter()
        .find(|v| v.id == voice_id)
        .ok_or("saved first-party voice not registered in CAS")?;
    verify_original_audio_file_binding(owner_name, &voice.source_sha256)?;
    if voice.measured_duration != RationalTime::new(6, 1)?
        || voice.channels != 2
        || voice.sample_rate_hz != PCM_RATE
    {
        return Err("first-party saved voice duration or channel profile changed".into());
    }
    let actual = fs::read(original_wav)?;
    let expected_bytes = usize::try_from(PCM_FRAMES)? * 4 + 44;
    if actual.len() != expected_bytes || hex::encode(Sha256::digest(&actual)) != voice.source_sha256
    {
        return Err("original owner-imported WAV bytes are not the saved CAS source".into());
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("original WAV owner output is bound to a different project".into());
    }
    let output_root = &connection.output_root;
    if !output_root.is_absolute() || !output_root.is_dir() || output_root.is_symlink() {
        return Err("owner output root is not an existing ordinary absolute directory".into());
    }
    let destination = output_root.join(owner_name);
    must_be_unused(&destination)?;
    let mut staged = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&destination)?;
    staged.write_all(&actual)?;
    staged.sync_all()?;
    if fs::metadata(&destination)?.len() != expected_bytes as u64
        || hex::encode(Sha256::digest(fs::read(&destination)?)) != voice.source_sha256
    {
        return Err("staged first-party WAV bytes changed inside original owner root".into());
    }
    let payload = serde_json::to_vec_pretty(&json!({
        "schema":"motionwright.native.film180.owner-staged-audio/1",
        "project_id":project.id,
        "generation":project.generation,
        "revision":project.revision,
        "owner_filename":owner_name,
        "source_sha256":voice.source_sha256,
        "source_bytes":expected_bytes,
        "native_Broker_audio_effect_performed":false,
        "staged_source_rights":"original first-party synthetic fixture only",
        "master_or_audition_verified":false,
        "human_approval":false,
    }))?;
    let mut out = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(receipt)?;
    out.write_all(&payload)?;
    out.write_all(b"\n")?;
    out.sync_all()?;
    println!("{}", String::from_utf8(payload)?);
    Ok(())
}

/// Final H264/AAC master only from the existing, original native Film output.
/// The old MLT AAC right-channel tail defect remains RED until an independent
/// full decoded-PCM and video-frame observer admits this newly sourced result.
async fn master_original(
    database: &Path,
    connection: &Path,
    motion_evidence: &Path,
    wav_relative_path: &str,
    wav_sha256: &str,
    evidence: &Path,
) -> FixtureResult<()> {
    if env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("Film180 full AV master requires an authorized disposable CI Host".into());
    }
    must_be_unused(evidence)?;
    if !motion_evidence.is_absolute()
        || !motion_evidence.is_file()
        || motion_evidence.is_symlink()
        || !(1..=8 * 1024 * 1024).contains(&fs::metadata(motion_evidence)?.len())
    {
        return Err("original Motion Canvas evidence is missing or unbounded".into());
    }
    verify_original_audio_file_binding(wav_relative_path, wav_sha256)?;
    let motion: MotionCanvasRenderEvidence = serde_json::from_slice(&fs::read(motion_evidence)?)?;
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    let [project] = projects.as_slice() else {
        return Err("AV source project missing or duplicated".into());
    };
    let profile = project
        .deliverables
        .first()
        .ok_or("AV source has no deliverable")?;
    let request_id = bound_stage_request(project)?;
    let original_film_sha = verified_projection(project)?;
    if motion.project_resource != project.resource_key()
        || motion.generation != project.generation
        || motion.revision != project.revision
        || motion.deliverable_id != profile.id
        || motion.frame_rate != Rate::new(30, 1)?
        || motion.segments.len() != 1
        || motion.segments[0].frame_count != FRAME_COUNT
        || motion.segments[0].scene_ids != project.scenes.iter().map(|s| s.id).collect::<Vec<_>>()
        || motion.segments[0]
            .verification
            .pointer("/report/support_level")
            .and_then(serde_json::Value::as_str)
            != Some("native")
    {
        return Err("AV master input does not bind the original native single-run source".into());
    }
    for (suffix, command) in [
        (
            "motion:0:render-result",
            "driver.motion-canvas.render.result",
        ),
        ("motion:0:verify", "driver.motion-canvas.composition.verify"),
    ] {
        let id = format!("{request_id}:{suffix}");
        let receipt = service
            .latest_production_receipt(project.id, &id)?
            .ok_or("actual original Film180 terminal native receipt is missing")?;
        if receipt.stage != "completed"
            || receipt.command != command
            || receipt.generation != project.generation
            || receipt.revision != project.revision
        {
            return Err("original Film180 native terminal receipt not admitted for AV".into());
        }
    }
    let voice_id = profile
        .voice_track_id
        .ok_or("AV source has no selected measured voice")?;
    let voice = project
        .audio
        .voice_tracks
        .iter()
        .find(|v| v.id == voice_id)
        .ok_or("AV source voice has no CAS registration")?;
    if voice.source_sha256 != wav_sha256
        || voice.sample_rate_hz != PCM_RATE
        || voice.channels != 2
        || voice.measured_duration != RationalTime::new(6, 1)?
    {
        return Err("AV original registered PCM digest or source sample clock differs".into());
    }
    let native_request = if request_id == NATIVE_REQUEST {
        "film180-original-av-master-v1"
    } else {
        "film180-edited-av-master-v1"
    };
    for suffix in ["mlt:frames-encode", "mlt:av-mux"] {
        let id = format!("{native_request}:{suffix}");
        if service
            .latest_production_receipt(project.id, &id)?
            .is_some()
        {
            return Err("original MLT mux already has a durable claim; never retry an unknown native AV operation".into());
        }
    }
    let connection = ProductionConnection::load(connection)?;
    if connection.resource != project.resource_key() {
        return Err("MLT master owner connection belongs to a different saved project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let audio = MltAudioArtifact {
        relative_path: wav_relative_path.into(),
        sha256: wav_sha256.into(),
        sample_rate: PCM_RATE,
        channels: 2,
    };
    let result = coordinator
        .assemble_mlt_av_master(
            project.id,
            &RevisionStamp::from(project),
            MltAvMasterRequest {
                request_id: native_request,
                deliverable_id: profile.id,
                motion: &motion,
                audio: &audio,
                sync: None,
            },
        )
        .await;
    let result = match result {
        Ok(value) => value,
        Err(error) => {
            // The native mutator may have UNKNOWN outcome; its new durable
            // claim remains in the existing StudioService receipt journal.
            let diag = evidence.with_file_name("film180-original-av-error.json");
            if must_be_unused(&diag).is_ok() {
                let bytes = serde_json::to_vec_pretty(&json!({
                    "schema":"motionwright.private-native-film180-av-diagnostic/1",
                    "source_Film_sha256":original_film_sha,
                    "source_project_id":project.id,
                    "source_revision":project.revision,
                    "error":{
                        "code":format!("{:?}",error.code),
                        "message":error.message,
                        "outcome_known":error.outcome_known,
                        "candidates":error.candidates
                    },
                    "repeat_native_mutation_authorized":false,
                    "master_content_verified":false
                }))?;
                let mut file = OpenOptions::new().create_new(true).write(true).open(diag)?;
                file.write_all(&bytes)?;
                file.write_all(b"\n")?;
                file.sync_all()?;
            }
            return Err(
                "native Film180 AV master failed or is uncertain: original evidence preserved"
                    .into(),
            );
        }
    };
    if result.project_resource != project.resource_key()
        || result.generation != project.generation
        || result.revision != project.revision
        || result.frame_count != FRAME_COUNT
        || result.deliverable_id != profile.id
        || result.frame_rate != Rate::new(30, 1)?
    {
        return Err("native AV result source identity or 180-frame clock changed".into());
    }
    let bytes = serde_json::to_vec_pretty(&result)?;
    if bytes.is_empty() || bytes.len() > 8 * 1024 * 1024 {
        return Err("native Film180 AV receipt is empty or unbounded".into());
    }
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(evidence)?;
    file.write_all(&bytes)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "schema":"motionwright.native.film180.av-master/1",
            "native_result_reported":"SUCCEEDED",
            "source_project_id":project.id,"generation":project.generation,
            "revision":project.revision,"canonical_Film_sha256":original_film_sha,
            "frame_count":result.frame_count,
            "original_source_audio_sha256":wav_sha256,
            "master_audio_tail_sample_fidelity_verified":false,
            "all_180_decoded_frame_pixels_verified":false,
            "human_listening_or_visual_approval":false
        }))?
    );
    Ok(())
}

fn readback_original(database: &Path, observation: &Path) -> FixtureResult<()> {
    must_be_unused(observation)?;
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    let [project] = projects.as_slice() else {
        return Err("saved Film180 source project is missing or duplicated".into());
    };
    let film_sha = verified_projection(project)?;
    let mut out = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(observation)?;
    out.write_all(
        serde_json::to_string_pretty(&json!({
            "schema":"motionwright.native.film180.saved-readback/1",
            "project_id":project.id,"generation":project.generation,"revision":project.revision,
            "saved_film_sha256":film_sha,"scene_count":2,
            "frame_count":FRAME_COUNT,"native_dispatch_performed_by_readback":false,
            "source_stamps_verified":true,"human_approval":false,
        }))?
        .as_bytes(),
    )?;
    out.write_all(b"\n")?;
    out.sync_all()?;
    Ok(())
}

#[tokio::main]
async fn main() -> FixtureResult<()> {
    let mut args = env::args_os().skip(1);
    let subcommand = args
        .next()
        .and_then(|x| x.into_string().ok())
        .unwrap_or_else(|| usage());
    match subcommand.as_str() {
        "seed" => {
            let database = path_arg(&mut args);
            let wave = path_arg(&mut args);
            let project = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed_original(&database, &wave, &project)?;
        }
        "readback" => {
            let database = path_arg(&mut args);
            let target = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            readback_original(&database, &target)?;
        }
        "edit" => {
            let database = path_arg(&mut args);
            let original_native_evidence = path_arg(&mut args);
            let edited_project = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            edit_saved_project(&database, &original_native_evidence, &edited_project)?;
        }
        "render" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            render_original(&database, &connection, &evidence).await?;
        }
        "stage-audio" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let wave = path_arg(&mut args);
            let owner_name = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let receipt = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            stage_owned_source_wav(&database, &connection, &wave, &owner_name, &receipt)?;
        }
        "master" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let film_result = path_arg(&mut args);
            let wav_relative = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let wav_sha = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            master_original(
                &database,
                &connection,
                &film_result,
                &wav_relative,
                &wav_sha,
                &evidence,
            )
            .await?;
        }
        _ => usage(),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn six_second_saved_source_reopens_and_retains_original_clock() {
        let directory = tempfile::tempdir().unwrap();
        let db = directory.path().join("owner.sqlite3");
        let wave = directory.path().join("original.wav");
        let json = directory.path().join("original.json");
        let project = seed_original(&db, &wave, &json).unwrap();
        assert_eq!(project.scenes.len(), 2);
        assert_eq!(project.scenes[0].nodes.len(), 3);
        assert_eq!(project.scenes[1].nodes.len(), 1);
        assert_eq!(fs::metadata(&wave).unwrap().len(), 1_152_044);
        assert_eq!(
            verified_projection(&project).unwrap(),
            verified_projection(
                &StudioService::open(&db)
                    .unwrap()
                    .project(project.id)
                    .unwrap()
            )
            .unwrap(),
        );
        let obs = directory.path().join("readback.json");
        readback_original(&db, &obs).unwrap();
        assert!(obs.exists());
        assert!(
            seed_original(&db, &wave, &json).is_err(),
            "never reuse a prior effect's source"
        );
    }

    #[test]
    fn editable_second_revision_uses_distinct_native_identity_and_requires_original_result() {
        let directory = tempfile::tempdir().unwrap();
        let db = directory.path().join("owner.sqlite3");
        let wave = directory.path().join("original.wav");
        let json = directory.path().join("original.json");
        let project = seed_original(&db, &wave, &json).unwrap();
        assert_eq!(bound_stage_request(&project).unwrap(), NATIVE_REQUEST);
        let mut edited = project.clone();
        edited.scenes[1].nodes[0].text = Some(EDITED_SECOND_TEXT.into());
        assert_eq!(bound_stage_request(&edited).unwrap(), EDITED_REQUEST);
        assert_ne!(
            verified_projection(&edited).unwrap(),
            verified_projection(&project).unwrap()
        );
        edited.scenes[1].nodes[0].text = Some("unauthorized arbitrary replacement".into());
        assert!(bound_stage_request(&edited).is_err());

        let missing_original_receipt = directory.path().join("missing-native-result.json");
        let edited_json = directory.path().join("edited.json");
        assert!(edit_saved_project(&db, &missing_original_receipt, &edited_json).is_err());
        assert!(!edited_json.exists());
        let reopened = StudioService::open(&db)
            .unwrap()
            .project(project.id)
            .unwrap();
        assert_eq!(
            reopened, project,
            "failed native edit must not mutate saved source"
        );
    }

    #[test]
    fn original_av_requires_single_bounded_source_wav_and_exact_digest() {
        let correct = "a".repeat(64);
        assert!(verify_original_audio_file_binding("owner-voice_01.wav", &correct).is_ok());
        for bad in [
            "../owner-voice_01.wav",
            "folder/owner-voice_01.wav",
            ".original.wav",
            "owner-voice.wav/../../fake.wav",
            "owner-voice.wav\\evil",
            "owner-voice.wav:ads",
            "original.WAV",
            "one.wav\nfoo",
        ] {
            assert!(
                verify_original_audio_file_binding(bad, &correct).is_err(),
                "{bad}"
            );
        }
        for sha in [
            "",
            "uppercase",
            &"A".repeat(64),
            &"g".repeat(64),
            &"f".repeat(63),
        ] {
            assert!(verify_original_audio_file_binding("owner.wav", sha).is_err());
        }
    }

    #[test]
    fn changed_saved_source_intent_or_caption_boundaries_fail_before_native_dispatch() {
        let directory = tempfile::tempdir().unwrap();
        let db = directory.path().join("owner.sqlite3");
        let wave = directory.path().join("original.wav");
        let json = directory.path().join("original.json");
        let mut project = seed_original(&db, &wave, &json).unwrap();
        let profile_id = project.deliverables[0].id;
        project
            .production_design
            .delivery_recipes
            .get_mut(&profile_id)
            .unwrap()
            .scene_intents[0]
            .role = Some(DeliveryNarrativeRole::Hook);
        assert!(authored_options(&project).is_err());
        let mut original = StudioService::open(&db)
            .unwrap()
            .project(project.id)
            .unwrap();
        original.audio.transcript[1].end = RationalTime::new(74, 30).unwrap();
        assert!(authored_options(&original).is_err());
    }
}
