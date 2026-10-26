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
         render-captured EXISTING_DATABASE OWNED_CONNECTION ORIGINAL_HOST_CUSTODY_DIR NEW_EVIDENCE_JSON |\
         render-fixed-text-v4 EXISTING_DATABASE OWNED_CONNECTION ORIGINAL_HOST_CUSTODY_DIR CASE_JSON NEW_EVIDENCE_JSON |\
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

async fn render_original(
    database: &Path,
    connection: &Path,
    evidence: &Path,
    custody_source: Option<&Path>,
) -> FixtureResult<()> {
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
    let stdin_readback = custody_source
        .map(|source| stdin_readback::Readback::begin(source, &connection, evidence))
        .transpose()?;
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let outcome = coordinator
        .render_motion_canvas_segments(
            original.id,
            &RevisionStamp::from(&original),
            request_id,
            original.deliverables[0].id,
            &authored_options(&original)?,
        )
        .await;
    let native_succeeded = outcome.is_ok();
    // Serialize the complete typed native return before interpreting custody.
    // These snapshots do not replace original RTE/wire records.
    let native_snapshot = match &outcome {
        Ok(value) => json!({
            "schema":"motionwright.private-native-return-snapshot/1",
            "native_result":"returned_success", "value":value,
            "wire_bytes":false, "automatic_retry":false
        }),
        Err(error) => json!({
            "schema":"motionwright.private-native-return-snapshot/1",
            "native_result":"returned_error", "error":error,
            "wire_bytes":false, "automatic_retry":false
        }),
    };
    let native_snapshot_error = stdin_readback
        .as_ref()
        .and_then(|reader| reader.retain_native_return(&native_snapshot).err())
        .map(|error| error.to_string());
    let native_snapshot_retained = native_snapshot_error.is_none();
    let primary = (|| -> FixtureResult<()> {
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
                if stdin_readback.is_some() {
                    // The complete original Error was snapshotted above. Keep
                    // its typed return, including UNKNOWN, ahead of custody.
                    return Err(error.into());
                }
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
                    "Original native Film180 producer failed; preserve diagnostic, never retry"
                        .into(),
                );
            }
        }
        Ok(())
    })();
    let custody = stdin_readback
        .map(|reader| {
            reader.finish(
                &service,
                &original,
                request_id,
                native_succeeded,
                native_snapshot_retained,
                native_snapshot_error.as_deref(),
            )
        })
        .transpose();
    if primary.is_err() {
        if let Err(error) = &custody {
            // Existing private CI stderr retains this secondary failure. The
            // original native Error remains the function's returned error.
            eprintln!(
                "Secondary stdin custody failure: {error}; preserve the original native outcome; no retry"
            );
        }
        return primary;
    }
    // Native success and custody failure are separate original observations.
    // Preserve the exact secondary error; neither outcome authorizes redispatch.
    custody?;
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

mod fixed_text_v4 {
    use super::*;
    use semwright_motion_authoring::{Film, FontFallback, realize};
    use semwright_native_sdk::{Error as NativeError, ErrorCode};
    use semwright_semantic_composition::strict_decode;
    use serde::{Deserialize, Serialize};
    use serde_json::Value;
    use std::collections::BTreeMap;

    type NativeResult<T> = std::result::Result<T, NativeError>;
    const INPUT_LIMIT: usize = 4 * 1024 * 1024;
    const GENERATED_LIMIT: usize = 32 * 1024 * 1024;
    const KEYS: [&str; 12] = [
        "fixed-causal",
        "flow-control-base",
        "flow-control-with-fixed",
        "flow-text-control",
        "fixed-multirun",
        "fixed-multirun-control",
        "fixed-transform-clip",
        "fixed-camera-scene",
        "fixed-counter-captions",
        "fixed-layer-behind",
        "fixed-layer-equal",
        "fixed-layer-front",
    ];
    #[derive(Clone, Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Original {
        path: PathBuf,
        bytes: u64,
        sha256: String,
    }
    impl Original {
        fn read(&self, limit: usize) -> FixtureResult<Vec<u8>> {
            require(
                self.bytes > 0 && self.bytes <= limit as u64 && sha_shape(&self.sha256),
                "Original descriptor exceeds its byte/digest bound",
            )?;
            let bytes = stdin_readback::read_source(&self.path, limit)?;
            require(
                bytes.len() as u64 == self.bytes && sha(&bytes) == self.sha256,
                "Original bytes differ from the frozen descriptor",
            )?;
            Ok(bytes)
        }
        fn descriptor(&self) -> Value {
            json!({"bytes":self.bytes,"sha256":&self.sha256})
        }
    }
    #[derive(Clone, Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Runtime {
        semwright_revision: String,
        driver_sha256: String,
        node_sha256: String,
        native_source_sha256: BTreeMap<String, String>,
        render_helper: Original,
        dependency_lock: Original,
        core_package: Original,
        core_renderer: Original,
    }
    #[derive(Clone, Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Case {
        schema: String,
        arm: String,
        key: String,
        film: Original,
        managed_project: Original,
        expectations: Original,
        fixture_set: Original,
        snapshot: Original,
        compiled_project: Original,
        compile_component: Original,
        generated_inventory: Original,
        generated_directory: PathBuf,
        runtime: Runtime,
    }
    struct Loaded {
        case: Case,
        film: Film,
        film_value: Value,
        original_project: Value,
        original_snapshot: Value,
        compiled_project: Value,
        component: Value,
        inventory: Value,
        expectations: Value,
        originals: Vec<(String, Original, Vec<u8>)>,
        case_bytes: Vec<u8>,
        case_path: PathBuf,
        helper: Vec<u8>,
    }
    fn require(ok: bool, reason: &str) -> FixtureResult<()> {
        if ok { Ok(()) } else { Err(reason.into()) }
    }
    fn sha(bytes: &[u8]) -> String {
        hex::encode(Sha256::digest(bytes))
    }
    fn sha_shape(value: &str) -> bool {
        value.len() == 64
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }
    fn decode<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> FixtureResult<T> {
        strict_decode(bytes).map_err(|error| format!("Invalid original JSON: {error}").into())
    }
    fn frames(key: &str) -> Vec<u64> {
        match key {
            "fixed-counter-captions" => vec![0, 29, 30, 45, 119, 120, 179],
            "flow-control-base" | "flow-control-with-fixed" | "fixed-transform-clip" => {
                vec![45, 120, 179]
            }
            _ => vec![0, 45, 120, 179],
        }
    }
    fn relative(value: &str) -> FixtureResult<()> {
        require(
            !value.is_empty()
                && value.len() <= 320
                && !value.starts_with('/')
                && !value.contains('\\')
                && !value.contains(':')
                && !value.chars().any(char::is_control)
                && value
                    .split('/')
                    .all(|part| !part.is_empty() && part != "." && part != ".."),
            "Original member path escapes its closed directory",
        )
    }
    fn exact_keys(value: &Value, names: &[&str]) -> FixtureResult<()> {
        require(
            value.as_object().is_some_and(|map| {
                map.len() == names.len() && names.iter().all(|name| map.contains_key(*name))
            }),
            "Original object fields differ from the selected contract",
        )
    }
    fn text_field(value: &Value, key: &str) -> FixtureResult<String> {
        value
            .get(key)
            .and_then(Value::as_str)
            .filter(|text| {
                !text.is_empty() && text.len() <= 1024 && !text.chars().any(char::is_control)
            })
            .map(str::to_owned)
            .ok_or_else(|| format!("Original field {key} is not bounded text").into())
    }
    fn native_error(reason: impl Into<String>) -> NativeError {
        NativeError::new(ErrorCode::BackendFailed, reason)
    }
    fn native_data(value: &Value) -> NativeResult<&Value> {
        if value["replayed"] != false || value["result"]["provenance_checked"] != true {
            return Err(native_error(
                "Native return was replayed or has unchecked provenance",
            ));
        }
        value["result"]
            .get("data")
            .filter(|data| data.is_object())
            .ok_or_else(|| native_error("Native return has no original object data"))
    }
    fn merge_managed_font(
        fonts: &mut BTreeMap<String, String>,
        family: &str,
        digest: &str,
    ) -> FixtureResult<()> {
        require(sha_shape(digest), "Imported font digest is invalid")?;
        let key = family.to_lowercase();
        if let Some(previous) = fonts.get(&key) {
            return require(
                previous == digest,
                "Repeated managed family has conflicting original assets",
            );
        }
        require(
            !fonts.values().any(|previous| previous == digest),
            "One imported digest is ambiguously assigned to different managed families",
        )?;
        fonts.insert(key, digest.to_owned());
        Ok(())
    }
    fn requested_managed_fonts(film: &Film) -> FixtureResult<BTreeMap<String, String>> {
        let mut result = BTreeMap::new();
        for font in [&film.editorial.font, &film.editorial.mono_font] {
            if let Some(digest) = &font.asset_digest {
                merge_managed_font(&mut result, &font.family, digest.as_str())?;
            }
        }
        Ok(result)
    }
    fn retain_input(
        originals: &mut Vec<(String, Original, Vec<u8>)>,
        role: &str,
        source: &Original,
        limit: usize,
    ) -> FixtureResult<Vec<u8>> {
        let bytes = source.read(limit)?;
        originals.push((role.to_owned(), source.clone(), bytes.clone()));
        Ok(bytes)
    }
    impl Loaded {
        fn read(case_path: &Path) -> FixtureResult<Self> {
            let case_bytes = stdin_readback::read_source(case_path, 65536)?;
            let case: Case = decode(&case_bytes)?;
            require(
                case.schema == "motionwright.private-fixed-text-render-case/4"
                    && matches!(case.arm.as_str(), "RED" | "GREEN")
                    && KEYS.contains(&case.key.as_str()),
                "Unsupported FixedTxt case identity",
            )?;
            let lock: Value = decode(include_bytes!("../../../SOURCE_LOCK.json"))?;
            require(
                lock["dependencies"]["semwright"]["revision"] == case.runtime.semwright_revision
                    && sha_shape(&case.runtime.driver_sha256)
                    && sha_shape(&case.runtime.node_sha256),
                "Runtime input differs from the caller's compiled source lock",
            )?;
            let pair = &case.runtime.native_source_sha256;
            require(
                pair.len() == 2
                    && pair.contains_key("RED")
                    && pair.contains_key("GREEN")
                    && pair.values().all(|value| sha_shape(value))
                    && pair["RED"] != pair["GREEN"],
                "RED/GREEN requires distinct original pins, not inherited results",
            )?;
            let mut originals = Vec::new();
            macro_rules! input {
                ($role:literal, $original:expr, $limit:expr) => {
                    retain_input(&mut originals, $role, $original, $limit)?
                };
            }
            let film_raw = input!("film", &case.film, INPUT_LIMIT);
            let film: Film = decode(&film_raw)?;
            film.validate()
                .map_err(|error| format!("Original Film rejected: {error}"))?;
            let realized = realize(&film)
                .map_err(|error| format!("Original Film realization rejected: {error}"))?;
            require(
                film.output.width == 1920
                    && film.output.height == 1080
                    && film.output.frame_rate.num == 30
                    && film.output.frame_rate.den == 1
                    && film.sequences.len() == 1
                    && film.shots().count() == 1
                    && realized
                        .schedule
                        .frame_count(&film.output)
                        .map_err(|e| e.to_string())?
                        == 180,
                "Original Film does not have the exact single-shot 180-frame clock",
            )?;
            for font in [&film.editorial.font, &film.editorial.mono_font] {
                require(
                    font.fallback == FontFallback::Deny && font.permitted_fallbacks.is_empty(),
                    "FixedTxt requires original fonts without fallback",
                )?;
                if font.asset_digest.is_none() {
                    require(
                        matches!(
                            font.family.as_str(),
                            "Instrument Sans Variable" | "IBM Plex Mono"
                        ),
                        "An unpinned original face is not bundled",
                    )?;
                }
            }
            requested_managed_fonts(&film)?;
            let film_value = serde_json::to_value(&film)?;
            let original_project: Value = decode(&input!(
                "managed-project",
                &case.managed_project,
                INPUT_LIMIT
            ))?;
            require(
                original_project["id"] == film.id,
                "Original Film and managed Project IDs differ",
            )?;
            let expectations: Value =
                decode(&input!("expectations", &case.expectations, INPUT_LIMIT))?;
            exact_keys(
                &expectations,
                &["schema", "key", "aliases", "expected", "film_sha256"],
            )?;
            let expected = &expectations["expected"];
            require(
                expectations["schema"] == "motionwright-fixedtxt-film-expectations/4"
                    && expectations["key"] == case.key
                    && expectations["film_sha256"] == case.film.sha256
                    && expected["status"] == "EXPECTATIONS_ONLY"
                    && expected["authority"] == "ORIGINAL_NATIVE_PNGS_AND_ACTUAL_PROBES"
                    && expected["fps"] == 30
                    && expected["frameCount"] == 180
                    && expected["frames"] == json!(frames(&case.key))
                    && expected["thresholds"]["status"] == "PROPOSED_NOT_CALIBRATED",
                "Fixture expectations differ from their authored source and sample schedule",
            )?;
            let aliases = expectations["aliases"]
                .as_object()
                .ok_or("Original aliases are absent")?;
            let subjects: BTreeSet<String> = film
                .shots()
                .flat_map(|shot| &shot.subjects)
                .map(|subject| subject.id.clone())
                .collect();
            let alias_ids: BTreeSet<String> = aliases
                .values()
                .map(|value| {
                    value
                        .as_str()
                        .map(str::to_owned)
                        .ok_or("Original alias is not text")
                })
                .collect::<Result<_, _>>()?;
            require(
                alias_ids == subjects && aliases.len() == subjects.len(),
                "Aliases do not uniquely cover authored subjects",
            )?;
            let fixture_set: Value =
                decode(&input!("fixture-set", &case.fixture_set, INPUT_LIMIT))?;
            require(
                fixture_set["schema"] == "motionwright-fixedtxt-film-fixture-set/4"
                    && fixture_set["status"] == "AUTHORED_FILM_FIXTURES_WRITTEN"
                    && fixture_set["fixture_count"] == 12
                    && fixture_set["managed_project_original"] == case.managed_project.descriptor(),
                "Canonical fixture set does not bind the original managed Project",
            )?;
            let roster = fixture_set["fixtures"]
                .as_array()
                .ok_or("Canonical fixture roster is absent")?;
            let mut roster_keys = BTreeSet::new();
            let mut selected = None;
            for row in roster {
                let key = text_field(row, "key")?;
                require(
                    KEYS.contains(&key.as_str())
                        && roster_keys.insert(key.clone())
                        && row["expected_frames"] == 180,
                    "Canonical fixture roster is incomplete or duplicated",
                )?;
                if key == case.key {
                    selected = Some(row);
                }
            }
            require(
                roster_keys.len() == 12 && roster.len() == 12,
                "Canonical fixture set does not contain all twelve cases",
            )?;
            let selected = selected.ok_or("Selected fixture is absent from canonical set")?;
            require(
                selected["film"]["bytes"] == case.film.bytes
                    && selected["film"]["sha256"] == case.film.sha256
                    && selected["expectations"]["bytes"] == case.expectations.bytes
                    && selected["expectations"]["sha256"] == case.expectations.sha256,
                "Selected Film or expectations differ from their canonical fixture set",
            )?;
            let envelope: Value = decode(&input!("snapshot", &case.snapshot, INPUT_LIMIT))?;
            require(
                envelope["ok"] == true
                    && envelope["command"] == "driver.motion-canvas.project.inspect"
                    && envelope["execution"]["provenance"]["provider"] == "driver:motion-canvas"
                    && envelope["execution"]["provenance"]["source"] == "driver",
                "Original snapshot is not a Motion Canvas CLI observation",
            )?;
            let original_snapshot = envelope["data"].clone();
            exact_keys(
                &original_snapshot,
                &["project", "fingerprint", "refs", "generated"],
            )?;
            require(
                original_snapshot["project"] == original_project
                    && original_snapshot["fingerprint"] == case.managed_project.sha256,
                "Original snapshot does not bind the exact original managed bytes",
            )?;
            let compiled_project: Value = decode(&input!(
                "compiled-project",
                &case.compiled_project,
                INPUT_LIMIT
            ))?;
            let component: Value = decode(&input!(
                "compile-component",
                &case.compile_component,
                INPUT_LIMIT
            ))?;
            let inventory: Value = decode(&input!(
                "generated-inventory",
                &case.generated_inventory,
                INPUT_LIMIT
            ))?;
            require(
                component["schema"] == "motionwright.fixed-text-compile-component/4"
                    && component["status"] == "CANONICAL_SOURCE_COMPONENT_WRITTEN"
                    && component["film_original"] == case.film.descriptor()
                    && component["managed_project_original"] == case.managed_project.descriptor()
                    && component["project_derived"]["bytes"] == case.compiled_project.bytes
                    && component["project_derived"]["sha256"] == case.compiled_project.sha256
                    && component["project_derived"]["generation"] == compiled_project["generation"]
                    && component["project_derived"]["revision"] == compiled_project["revision"]
                    && component["expected_frames"] == 180
                    && component["native_render_performed"] == false
                    && component["compiler"]["inventory"] == inventory
                    && component["compiler"]["runtime_source_sha256"] == pair[&case.arm]
                    && fixture_set["compiler"]["runtime_source_sha256"] == pair[&case.arm]
                    && component["compiler"]["generated_fingerprint"]
                        == selected["canonical_validation_generated_fingerprint"]
                    && component["compiler"]["generated_fingerprint"]
                        .as_str()
                        .is_some_and(sha_shape)
                    && compiled_project["authoring"]["intent"] == film_value
                    && compiled_project["settings"]["background"]
                        .as_str()
                        .is_some(),
                "Canonical component is not bound to the current Film, Project and runtime arm",
            )?;
            require(
                case.generated_directory.is_absolute()
                    && fs::canonicalize(&case.generated_directory)? == case.generated_directory,
                "Generated compiler directory is not canonical",
            )?;
            let rows = inventory
                .as_array()
                .ok_or("Generated inventory is not an array")?;
            require(
                !rows.is_empty() && rows.len() <= 256,
                "Generated inventory exceeds its file bound",
            )?;
            let mut paths = BTreeSet::new();
            let mut total = 0_u64;
            let mut native_source_seen = false;
            for row in rows {
                exact_keys(row, &["path", "bytes", "sha256"])?;
                let path = text_field(row, "path")?;
                relative(&path)?;
                require(
                    paths.insert(path.clone()),
                    "Generated inventory has a duplicate path",
                )?;
                let source = Original {
                    path: case.generated_directory.join(&path),
                    bytes: row["bytes"]
                        .as_u64()
                        .ok_or("Generated byte count is not an integer")?,
                    sha256: text_field(row, "sha256")?,
                };
                total = total
                    .checked_add(source.bytes)
                    .ok_or("Generated byte count overflow")?;
                require(
                    total <= GENERATED_LIMIT as u64,
                    "Generated compiler sources exceed 32 MiB",
                )?;
                let bytes = retain_input(
                    &mut originals,
                    &format!("generated:{path}"),
                    &source,
                    8 * 1024 * 1024,
                )?;
                if path == "src/semwright-authoring-native.ts" {
                    require(
                        sha(&bytes) == pair[&case.arm],
                        "Generated native adapter differs from the selected arm",
                    )?;
                    native_source_seen = true;
                }
            }
            require(
                native_source_seen,
                "Canonical compiler did not emit its native adapter",
            )?;
            let helper = input!("render-helper", &case.runtime.render_helper, 65536);
            input!(
                "dependency-lock",
                &case.runtime.dependency_lock,
                2 * 1024 * 1024
            );
            let package: Value = decode(&input!(
                "core-package",
                &case.runtime.core_package,
                256 * 1024
            ))?;
            require(
                package["name"] == "@motion-canvas/core" && package["version"] == "3.17.2",
                "Selected runtime core package differs from the fixed native contract",
            )?;
            input!(
                "core-renderer",
                &case.runtime.core_renderer,
                2 * 1024 * 1024
            );
            Ok(Self {
                case,
                film,
                film_value,
                original_project,
                original_snapshot,
                compiled_project,
                component,
                inventory,
                expectations,
                originals,
                case_bytes,
                case_path: case_path.to_path_buf(),
                helper,
            })
        }
        fn recheck(&self) -> FixtureResult<()> {
            require(
                stdin_readback::read_source(&self.case_path, 65536)? == self.case_bytes,
                "Original case descriptor changed",
            )?;
            for (_, original, bytes) in &self.originals {
                require(
                    original.read(GENERATED_LIMIT)?.as_slice() == bytes.as_slice(),
                    "An original changed after admission",
                )?;
            }
            Ok(())
        }
        fn retain(&self, output: &Path) -> FixtureResult<()> {
            stdin_readback::new_private_directory(output)?;
            stdin_readback::new_bytes(&output.join("case.original.json"), &self.case_bytes)?;
            let mut inventory = Vec::new();
            for (ordinal, (role, original, bytes)) in self.originals.iter().enumerate() {
                let name = format!("input-{ordinal:04}.original.bin");
                stdin_readback::new_bytes(&output.join(&name), bytes)?;
                inventory.push(json!({"role":role,"original":original,"retained":name}));
            }
            stdin_readback::new_json(
                &output.join("inputs.original.json"),
                &json!({
                    "schema":"motionwright.private-fixed-text-original-inputs/4",
                    "originals":inventory,"authority":false,"native_render_performed":false
                }),
                1024 * 1024,
            )
        }
    }
    async fn call(
        coordinator: &ProductionCoordinator,
        project: &Project,
        request: &str,
        suffix: &str,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<Value> {
        coordinator
            .execute(
                project.id,
                &RevisionStamp::from(project),
                &format!("{request}:motion:0:{suffix}"),
                command,
                args,
                mutation,
            )
            .await
    }
    impl Loaded {
        async fn dispatch(
            &self,
            coordinator: &ProductionCoordinator,
            project: &Project,
            request: &str,
            retained: &Path,
        ) -> NativeResult<Value> {
            let before = call(
                coordinator,
                project,
                request,
                "inspect-before",
                "driver.motion-canvas.project.inspect",
                json!({}),
                false,
            )
            .await?;
            if native_data(&before)? != &self.original_snapshot
                || native_data(&before)?["project"] != self.original_project
            {
                return Err(native_error(
                    "Fresh native snapshot differs from the frozen original",
                ));
            }
            let planned = call(
                coordinator,
                project,
                request,
                "plan",
                "driver.motion-canvas.composition.plan",
                json!({
                    "film":&self.film_value,
                    "budget":{"max_iterations":8,"max_operations":4096,"max_findings":1024,
                              "max_observations":128,"max_elapsed_ms":120000}
                }),
                false,
            )
            .await?;
            let data = native_data(&planned)?;
            if data["plan"]["body"]["intent"] != self.film_value {
                return Err(native_error("Native plan changed the original Film intent"));
            }
            let plan_ref = text_field(data, "plan_ref").map_err(|e| native_error(e.to_string()))?;
            let fingerprint = text_field(data, "resulting_fingerprint")
                .map_err(|e| native_error(e.to_string()))?;
            if !sha_shape(&fingerprint) || data["plan"]["digest"] != plan_ref {
                return Err(native_error("Native plan identity is invalid"));
            }
            let rechecked = call(
                coordinator,
                project,
                request,
                "inspect-before-apply",
                "driver.motion-canvas.project.inspect",
                json!({}),
                false,
            )
            .await?;
            if native_data(&rechecked)? != &self.original_snapshot {
                return Err(native_error(
                    "Original native snapshot changed before apply",
                ));
            }
            self.recheck()
                .map_err(|e| NativeError::new(ErrorCode::Conflict, e.to_string()))?;
            let applied = call(
                coordinator,
                project,
                request,
                "apply",
                "driver.motion-canvas.composition.apply",
                json!({"plan_ref":&plan_ref,"dry_run":false}),
                true,
            )
            .await?;
            let data = native_data(&applied).map_err(NativeError::uncertain)?;
            if data["applied"] != true
                || data["execution_status"] != "completed"
                || data["plan_ref"] != plan_ref
                || data["fingerprint"] != fingerprint
            {
                return Err(native_error(
                    "Native apply did not return the selected plan/fingerprint",
                )
                .uncertain());
            }
            let inspected = call(
                coordinator,
                project,
                request,
                "inspect-after-apply",
                "driver.motion-canvas.project.inspect",
                json!({}),
                false,
            )
            .await?;
            let data = native_data(&inspected)?;
            if data["fingerprint"] != fingerprint
                || data["project"] != self.compiled_project
                || data["generated"] != self.inventory
            {
                return Err(native_error(
                    "Actual native projection differs from the canonical component",
                ));
            }
            let validated = call(
                coordinator,
                project,
                request,
                "validate-after-apply",
                "driver.motion-canvas.project.validate",
                json!({}),
                false,
            )
            .await?;
            let data = native_data(&validated)?;
            if data["valid"] != true
                || data["fingerprint"] != fingerprint
                || data["generated_fingerprint"]
                    != self.component["compiler"]["generated_fingerprint"]
            {
                return Err(native_error(
                    "Actual native compiler fingerprint differs from the component",
                ));
            }
            self.recheck()
                .map_err(|e| NativeError::new(ErrorCode::Conflict, e.to_string()))?;
            let started = call(
                coordinator,
                project,
                request,
                "render-start",
                "driver.motion-canvas.render.start",
                json!({
                    "expected_fingerprint":&fingerprint,
                    "profile":{"first_frame":0,"end_frame_exclusive":180,"scale":"full",
                               "transparent":false,"timeout_ms":300000}
                }),
                true,
            )
            .await?;
            let start = native_data(&started).map_err(NativeError::uncertain)?;
            let job_ref = text_field(start, "job_ref")
                .map_err(|e| native_error(e.to_string()).uncertain())?;
            let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(330);
            let mut succeeded = false;
            for index in 0..=330_u32 {
                let status = call(
                    coordinator,
                    project,
                    request,
                    &format!("render-status-{index}"),
                    "driver.motion-canvas.render.status",
                    json!({"job_ref":&job_ref}),
                    false,
                )
                .await
                .map_err(NativeError::uncertain)?;
                let data = native_data(&status).map_err(NativeError::uncertain)?;
                if data["job_ref"] != job_ref {
                    return Err(
                        native_error("Native status changed the original job identity").uncertain(),
                    );
                }
                match data["state"].as_str() {
                    Some("succeeded") => {
                        if data["artifact"]["frame_count"] != 180 {
                            return Err(native_error("Native result has a different frame count")
                                .uncertain());
                        }
                        succeeded = true;
                        break;
                    }
                    Some("failed" | "cancelled") => {
                        let original_state = data["state"].as_str().unwrap_or("failed");
                        let retention = stdin_readback::new_json(
                            &retained.join("render-terminal.original-value.json"),
                            &status,
                            8 * 1024 * 1024,
                        );
                        if let Err(secondary) = retention {
                            eprintln!(
                                "Secondary native terminal retention failure: {secondary}; no retry"
                            );
                        }
                        return Err(NativeError::new(
                            if original_state == "cancelled" {
                                ErrorCode::Cancelled
                            } else {
                                ErrorCode::BackendFailed
                            },
                            format!(
                                "Original native render returned {original_state}; original receipt retained"
                            ),
                        ));
                    }
                    Some("queued" | "starting" | "rendering") => {}
                    _ => return Err(native_error("Native job state is unrecognized").uncertain()),
                }
                if index == 330 || tokio::time::Instant::now() >= deadline {
                    break;
                }
                tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            }
            if !succeeded {
                return Err(NativeError::new(
                    ErrorCode::Timeout,
                    "Original render did not reach an observed terminal",
                )
                .uncertain());
            }
            let result = call(
                coordinator,
                project,
                request,
                "render-result",
                "driver.motion-canvas.render.result",
                json!({"job_ref":&job_ref}),
                false,
            )
            .await
            .map_err(NativeError::uncertain)?;
            let data = native_data(&result).map_err(NativeError::uncertain)?;
            if data["job_ref"] != job_ref
                || data["state"] != "succeeded"
                || data["artifact"]["frame_count"] != 180
            {
                return Err(
                    native_error("Original render result differs from its successful job")
                        .uncertain(),
                );
            }
            let artifact = data["artifact"].clone();
            stdin_readback::new_json(
                &retained.join("render-result.original-value.json"),
                &result,
                8 * 1024 * 1024,
            )
            .map_err(|e| {
                native_error(format!(
                    "Original native result retention failed: {e}; no redispatch"
                ))
            })?;
            let verification = call(
                coordinator,
                project,
                request,
                "verify",
                "driver.motion-canvas.composition.verify",
                json!({"plan_ref":&plan_ref,"job_ref":&job_ref}),
                false,
            )
            .await?;
            let verification_data = native_data(&verification)?.clone();
            // RED may have FAIL checks. Retain the original report without
            // asserting a nonexistent serialized report.verdict or acceptance.
            Ok(json!({
                "schema":"motionwright.native.fixed-text-render-result/4",
                "arm":&self.case.arm,"fixture":&self.case.key,"frame_count":180,
                "film_original_sha256":&self.case.film.sha256,
                "managed_project_original_sha256":&self.case.managed_project.sha256,
                "expectations_original_sha256":&self.case.expectations.sha256,
                "component_original_sha256":&self.case.compile_component.sha256,
                "snapshot_original_sha256":&self.case.snapshot.sha256,
                "generated_fingerprint":&self.component["compiler"]["generated_fingerprint"],
                "native_adapter_sha256":&self.case.runtime.native_source_sha256[&self.case.arm],
                "aliases":&self.expectations["aliases"],
                "project_resource":project.resource_key(),"generation":&project.generation,"revision":project.revision,
                "native_render_result":&result,
                "segments":[{"frame_count":180,"plan_ref":plan_ref,"fingerprint":fingerprint,
                             "job_ref":job_ref,"artifact":artifact,"verification":verification_data}],
                "physical_pixels_independently_decoded":false,"formal_acceptance":false,
                "human_acceptance":false,"automatic_retry":false
            }))
        }
    }

    impl Loaded {
        fn verify_host_input(
            &self,
            evidence: &Path,
            output_root: &Path,
            native: &Value,
        ) -> FixtureResult<Value> {
            let custody = evidence.with_extension("host-stdin");
            let observation: Value = decode(&stdin_readback::read_source(
                &custody.join("readback.observation.json"),
                8 * 1024 * 1024,
            )?)?;
            let selected = &observation["selected_capture_join"];
            require(
                selected["matched"] == true && selected["pipe_write_all_completed"] == true,
                "Original selected Host input has no completed joined pipe write",
            )?;
            let ordinal = selected["ordinal"]
                .as_u64()
                .filter(|n| (1..=64).contains(n))
                .ok_or("Original capture ordinal is absent")?;
            let program = stdin_readback::read_source(
                &custody.join(format!("capture-{ordinal:04}.stdin")),
                65536,
            )?;
            require(
                selected["capture"]["stdin_sha256"] == sha(&program),
                "Original captured stdin digest changed",
            )?;
            let newline = program
                .iter()
                .position(|b| *b == b'\n')
                .ok_or("Original Host configuration line absent")?;
            let line = std::str::from_utf8(&program[..newline])?;
            let raw = line
                .strip_prefix("globalThis.__SEMWRIGHT_RENDER_INPUT__ = ")
                .and_then(|value| value.strip_suffix(';'))
                .ok_or("Original Host program prefix changed")?;
            require(
                program.get(newline + 1..) == Some(self.helper.as_slice()),
                "Original Host helper differs from its frozen bytes",
            )?;
            let config: Value = decode(raw.as_bytes())?;
            exact_keys(
                &config,
                &[
                    "authoring",
                    "renderInputDigest",
                    "dependencyLockDigest",
                    "fontResourcesDigest",
                    "fontResourcePins",
                    "managedFonts",
                    "name",
                    "width",
                    "height",
                    "fps",
                    "fpsNum",
                    "fpsDen",
                    "firstFrame",
                    "endFrameExclusive",
                    "colorSpace",
                    "background",
                    "alpha",
                    "timeoutMs",
                ],
            )?;
            require(
                config["authoring"] == true
                    && config["name"] == "frames"
                    && config["width"] == 1920
                    && config["height"] == 1080
                    && config["fps"].as_f64() == Some(30.0)
                    && config["fpsNum"] == 30
                    && config["fpsDen"] == 1
                    && config["firstFrame"] == 0
                    && config["endFrameExclusive"] == 180
                    && config["colorSpace"] == "srgb"
                    && config["alpha"] == false
                    && config["timeoutMs"] == 300000
                    && config["background"] == self.compiled_project["settings"]["background"]
                    && config["dependencyLockDigest"] == self.case.runtime.dependency_lock.sha256
                    && config["renderInputDigest"].as_str().is_some_and(sha_shape),
                "Original Host input differs from the admitted Film/plan/runtime",
            )?;
            let pins = config["fontResourcePins"]
                .as_array()
                .ok_or("Original font resource pins absent")?;
            require(
                (4..=128).contains(&pins.len()),
                "Original font pin count exceeds its bound",
            )?;
            let mut resources = BTreeMap::new();
            for pin in pins {
                exact_keys(pin, &["path", "sha256"])?;
                let path = text_field(pin, "path")?;
                let digest = text_field(pin, "sha256")?;
                relative(&path)?;
                require(
                    sha_shape(&digest),
                    "Original font resource digest is invalid",
                )?;
                require(
                    resources.insert(path, digest).is_none(),
                    "Original font resource pin is duplicated",
                )?;
            }
            let managed = config["managedFonts"]
                .as_array()
                .ok_or("Original managedFonts array absent")?;
            let requested = requested_managed_fonts(&self.film)?;
            require(
                managed.len() <= 2 && managed.len() == requested.len(),
                "Original managedFonts does not match admitted Film assets",
            )?;
            let assets = self.compiled_project["assets"]
                .as_array()
                .ok_or("Compiled asset roster absent")?;
            let mut observed_families = BTreeSet::new();
            let mut aggregate = Sha256::new();
            for (path, digest) in &resources {
                aggregate.update(path.as_bytes());
                aggregate.update([0]);
                aggregate.update(digest.as_bytes());
                aggregate.update([0]);
            }
            for font in managed {
                exact_keys(
                    font,
                    &[
                        "family",
                        "path",
                        "sha256",
                        "bytes",
                        "weights",
                        "codepoints",
                        "sample",
                        "face_loaded",
                        "metadata",
                    ],
                )?;
                let family = text_field(font, "family")?;
                let path = text_field(font, "path")?;
                let digest = text_field(font, "sha256")?;
                relative(&path)?;
                let family_key = family.to_lowercase();
                require(
                    sha_shape(&digest)
                        && requested.get(&family_key) == Some(&digest)
                        && observed_families.insert(family_key)
                        && font["face_loaded"] == false
                        && assets.iter().any(|asset| {
                            asset["kind"] == "font"
                                && asset["path"] == path.as_str()
                                && asset["sha256"] == digest.as_str()
                                && asset["bytes"] == font["bytes"]
                        }),
                    "Original managed font differs from its admitted imported asset",
                )?;
                aggregate.update(b"managed-font\0");
                aggregate.update(path.as_bytes());
                aggregate.update([0]);
                aggregate.update(digest.as_bytes());
                aggregate.update([0]);
            }
            require(
                config["fontResourcesDigest"] == hex::encode(aggregate.finalize()),
                "Original font closure digest differs",
            )?;
            let managed_count = managed.len();
            let artifact = &native["segments"][0]["artifact"];
            let directory = text_field(artifact, "directory")?;
            relative(&directory)?;
            require(
                !directory.contains('/'),
                "Artifact directory is not one output-root child",
            )?;
            let manifest_bytes = stdin_readback::read_source(
                &output_root.join(&directory).join("artifact-manifest.json"),
                1024 * 1024,
            )?;
            require(
                artifact["manifest_sha256"] == sha(&manifest_bytes)
                    && artifact["manifest_bytes"].as_u64() == Some(manifest_bytes.len() as u64),
                "Original artifact manifest differs from its render result",
            )?;
            let manifest: Value = decode(&manifest_bytes)?;
            require(
                manifest["native_observations"]["render_input_digest"]
                    == config["renderInputDigest"]
                    && manifest["native_observations"]["font_resources_sha256"]
                        == config["fontResourcesDigest"]
                    && manifest["plan"]["first_frame"] == 0
                    && manifest["plan"]["end_frame_exclusive"] == 180
                    && manifest["plan"]["frame_count"] == 180,
                "Original observations do not join the captured Host input",
            )?;
            Ok(json!({
                "schema":"motionwright.private-fixed-text-original-host-binding/4",
                "arm":&self.case.arm,"fixture":&self.case.key,"original_Host_stdin_sha256":sha(&program),
                "original_artifact_manifest_sha256":sha(&manifest_bytes),"original_config":&config,
                "selected_capture_join":selected,"selected_pipe_write_all_completed":true,
                "child_consumption":"not_observed","original_sources_joined":true,"imported_font_count":managed_count,
                "glyph_fidelity_accepted":false,"browser_font_readiness_inferred":false,
                "receiver_lifetime_completeness_claimed":false,"formal_acceptance":false
            }))
        }
    }
    pub(super) async fn run(
        database: &Path,
        connection_path: &Path,
        custody_source: &Path,
        case_path: &Path,
        evidence: &Path,
    ) -> FixtureResult<()> {
        require(
            cfg!(target_os = "linux"),
            "Original Host custody requires Linux",
        )?;
        require(
            env::var("GITHUB_ACTIONS").as_deref() == Ok("true"),
            "FixedTxt native effects require the isolated CI owner",
        )?;
        must_be_unused(evidence)?;
        let loaded = Loaded::read(case_path)?;
        let service = StudioService::open(database)?;
        let projects = service.projects(2)?;
        let [project] = projects.as_slice() else {
            return Err("FixedTxt requires one existing production project".into());
        };
        let original = project.clone();
        original.validate()?;
        let connection = ProductionConnection::load(connection_path)?;
        require(
            connection.resource == original.resource_key(),
            "Connection differs from the saved production resource",
        )?;
        let output_root = connection.output_root.clone();
        let request = format!(
            "fixed-text-v4-{}-{}",
            loaded.case.arm.to_ascii_lowercase(),
            loaded.case.key
        );
        for suffix in ["motion:0:apply", "motion:0:render-start"] {
            require(
                service
                    .latest_production_receipt(original.id, &format!("{request}:{suffix}"))?
                    .is_none(),
                "Original effect identity already exists; inspect, never retry",
            )?;
        }
        let retained = evidence.with_extension("fixed-text");
        loaded.retain(&retained)?;
        let reader = stdin_readback::Readback::begin(custody_source, &connection, evidence)?;
        reader.require_binary_pins(
            &loaded.case.runtime.driver_sha256,
            &loaded.case.runtime.node_sha256,
        )?;
        let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
        let outcome = loaded
            .dispatch(&coordinator, &original, &request, &retained)
            .await;
        let succeeded = outcome.is_ok();
        let snapshot = match &outcome {
            Ok(value) => json!({"schema":"motionwright.private-native-return-snapshot/1",
                "native_result":"returned_success","value":value,"wire_bytes":false,"automatic_retry":false}),
            Err(error) => json!({"schema":"motionwright.private-native-return-snapshot/1",
                "native_result":"returned_error","error":error,"wire_bytes":false,"automatic_retry":false}),
        };
        let snapshot_error = reader
            .retain_native_return(&snapshot)
            .err()
            .map(|e| e.to_string());
        let result_retention = match &outcome {
            Ok(value) => stdin_readback::new_json(evidence, value, 8 * 1024 * 1024),
            Err(_) => Ok(()),
        };
        let custody = reader.finish(
            &service,
            &original,
            &request,
            succeeded,
            snapshot_error.is_none(),
            snapshot_error.as_deref(),
        );
        let native = match outcome {
            Ok(value) => value,
            Err(error) => {
                if let Err(secondary) = &custody {
                    eprintln!(
                        "Secondary stdin custody failure: {secondary}; original native Error retained; no retry"
                    );
                }
                return Err(error.into());
            }
        };
        if let Err(error) = result_retention {
            if let Err(secondary) = &custody {
                eprintln!("Secondary stdin custody failure: {secondary}; no retry");
            }
            return Err(error);
        }
        custody?;
        let binding = (|| -> FixtureResult<Value> {
            loaded.recheck()?;
            loaded.verify_host_input(evidence, &output_root, &native)
        })();
        let binding = match binding {
            Ok(value) => value,
            Err(error) => {
                if let Err(secondary) = stdin_readback::new_json(
                    &retained.join("host-binding-error.original.json"),
                    &json!({"schema":"motionwright.private-fixed-text-post-native-error/4",
                        "error":error.to_string(),"native_result_retained":true,
                        "native_return_snapshot_retained":snapshot_error.is_none(),"automatic_retry":false}),
                    65536,
                ) {
                    eprintln!("Secondary binding error retention failure: {secondary}; no retry");
                }
                return Err(error);
            }
        };
        stdin_readback::new_json(
            &retained.join("host-binding.observation.json"),
            &binding,
            8 * 1024 * 1024,
        )?;
        println!(
            "{}",
            serde_json::to_string(&json!({
                "schema":"motionwright.native.fixed-text.original-result/4","native_fixed_text_host":"SUCCEEDED_REPORTED",
                "arm":&loaded.case.arm,"fixture":&loaded.case.key,"frame_count":180,
                "project_id":original.id,"generation":&original.generation,"revision":original.revision,
                "original_Host_stdin_joined":true,"selected_pipe_write_all_completed":true,"child_consumption":"not_observed",
                "physical_pixels_independently_decoded":false,"receiver_lifetime_completeness_claimed":false,
                "formal_acceptance":false,"human_approval":false,"automatic_retry":false
            }))?
        );
        Ok(())
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn samples_cover_all_twelve_cases_and_caption_boundaries() {
            assert_eq!(
                frames("fixed-counter-captions"),
                vec![0, 29, 30, 45, 119, 120, 179]
            );
            assert_eq!(KEYS.iter().map(|key| frames(key).len()).sum::<usize>(), 48);
            assert_eq!(KEYS.iter().copied().collect::<BTreeSet<_>>().len(), 12);
            for key in [
                "flow-control-base",
                "flow-control-with-fixed",
                "fixed-transform-clip",
            ] {
                assert_eq!(frames(key), vec![45, 120, 179]);
            }
        }
        #[test]
        fn duplicate_original_descriptor_fields_are_rejected() {
            assert!(
                decode::<Original>(br#"{"path":"/original","bytes":1,"bytes":2,"sha256":"ab"}"#)
                    .is_err()
            );
            assert!(
                decode::<Original>(
                    br#"{"path":"/original","bytes":1,"sha256":"ab","substitute":true}"#
                )
                .is_err()
            );
        }
        #[test]
        fn compiler_members_cannot_escape_the_original_directory() {
            for path in [
                "",
                "../native.ts",
                "/native.ts",
                "a//native.ts",
                "a/./native.ts",
                "a/../native.ts",
                "a\\native.ts",
                "C:native.ts",
                "a\nnative.ts",
            ] {
                assert!(relative(path).is_err());
            }
            assert!(relative("src/semwright-authoring-native.ts").is_ok());
        }
        #[test]
        fn pins_require_exact_lowercase_sha256() {
            assert!(sha_shape(&"ab".repeat(32)));
            for value in [
                "ab".repeat(31),
                "AB".repeat(32),
                "gg".repeat(32),
                "ab".repeat(33),
            ] {
                assert!(!sha_shape(&value));
            }
        }
        #[test]
        fn native_return_requires_fresh_checked_provenance() {
            let original = json!({"replayed":false,"result":{"provenance_checked":true,"data":{"fixture":"synthetic-only"}}});
            assert_eq!(native_data(&original).unwrap()["fixture"], "synthetic-only");
            let mut replayed = original.clone();
            replayed["replayed"] = json!(true);
            assert!(native_data(&replayed).is_err());
            let mut unchecked = original;
            unchecked["result"]["provenance_checked"] = json!(false);
            assert!(native_data(&unchecked).is_err());
            assert!(native_data(&json!({"ok":false,"error":{"code":"UNKNOWN"}})).is_err());
        }
        #[test]
        fn repeated_managed_family_with_same_asset_has_one_join() {
            let mut fonts = BTreeMap::new();
            merge_managed_font(&mut fonts, "Synthetic Face", &"ab".repeat(32)).unwrap();
            merge_managed_font(&mut fonts, "synthetic face", &"ab".repeat(32)).unwrap();
            assert_eq!(fonts.len(), 1);
        }
        #[test]
        fn conflicting_assets_for_same_managed_family_are_rejected() {
            let mut fonts = BTreeMap::new();
            merge_managed_font(&mut fonts, "Synthetic Face", &"ab".repeat(32)).unwrap();
            let original = fonts.clone();
            assert!(merge_managed_font(&mut fonts, "Synthetic Face", &"cd".repeat(32)).is_err());
            assert_eq!(fonts, original);
        }
        #[test]
        fn one_managed_digest_cannot_alias_distinct_family_joins() {
            let mut fonts = BTreeMap::new();
            merge_managed_font(&mut fonts, "Synthetic Face", &"ab".repeat(32)).unwrap();
            let original = fonts.clone();
            assert!(merge_managed_font(&mut fonts, "Other Face", &"ab".repeat(32)).is_err());
            assert_eq!(fonts, original);
        }
    }
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
            render_original(&database, &connection, &evidence, None).await?;
        }
        "render-captured" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let source = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            render_original(&database, &connection, &evidence, Some(&source)).await?;
        }
        "render-fixed-text-v4" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let custody = path_arg(&mut args);
            let case = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            fixed_text_v4::run(&database, &connection, &custody, &case, &evidence).await?;
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

mod stdin_readback {
    use super::{FixtureResult, ProductionConnection, Project, StudioService, must_be_unused};
    use semwright_native_sdk::types::{ProviderIdentity, SourceKind};
    use serde::{Deserialize, Serialize};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::{
        collections::BTreeSet,
        fs::{self, File, Metadata, OpenOptions},
        io::{Read, Write},
        path::{Path, PathBuf},
    };

    const RECORDS: usize = 64;
    const INPUT: usize = 65_536;
    const META: usize = 8_192;
    const TERMINAL: usize = 2_048;
    const SCOPE: &str = "original Host stdin and pipe write only; child consumption unobserved";

    #[derive(Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Session {
        schema: String,
        provider: ProviderIdentity,
        driver_sha256: String,
        tool: String,
        tool_sha256: String,
        queue_capacity: usize,
        max_records: usize,
        max_input_bytes: usize,
        max_total_input_bytes: usize,
        max_capture_metadata_bytes: usize,
        max_terminal_metadata_bytes: usize,
        scope: String,
    }
    #[derive(Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Capture {
        provider: ProviderIdentity,
        provider_process_id: u32,
        job_id: String,
        request_id: String,
        parent_request_id: String,
        session_id: String,
        tool: String,
        stdin_bytes: usize,
        stdin_sha256: String,
        terminal_file_required: bool,
    }
    #[derive(Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct PipeTerminal {
        write_state: String,
        #[serde(default)]
        partial_delivery_possible: Option<bool>,
        #[serde(default)]
        io_error_kind: Option<String>,
        #[serde(default)]
        raw_os_error: Option<i32>,
        child_consumption: String,
        native_render_success: String,
    }
    #[derive(Deserialize, Serialize, Default)]
    #[serde(deny_unknown_fields)]
    struct Losses {
        capture_full: u64,
        capture_closed: u64,
        capture_oversized: u64,
        terminal_receiver_lost: u64,
    }
    impl Losses {
        fn zero(&self) -> bool {
            self.capture_full == 0
                && self.capture_closed == 0
                && self.capture_oversized == 0
                && self.terminal_receiver_lost == 0
        }
    }
    #[derive(Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Closed {
        schema: String,
        status: String,
        reason: String,
        capture_records_written: usize,
        terminal_records_written: usize,
        input_bytes_reserved: usize,
        queued_captures_discarded: usize,
        queue_disconnection_proven: bool,
        loss_counters_at_observer_close: Losses,
        expected_job_coverage: String,
        child_consumption: String,
        native_render_success: String,
    }
    impl Closed {
        fn validate(&self) -> FixtureResult<()> {
            if self.schema != "semwright.host-stdin-custody-close/1"
                || !matches!(self.status.as_str(), "observer_closed" | "incomplete")
                || !matches!(
                    self.reason.as_str(),
                    "owner_shutdown"
                        | "channel_closed"
                        | "identity_or_metadata_rejected"
                        | "capture_budget_exhausted"
                        | "capture_publication_failed"
                        | "terminal_metadata_rejected"
                        | "terminal_publication_failed"
                )
                || self.capture_records_written > RECORDS
                || self.terminal_records_written > self.capture_records_written
                || self.input_bytes_reserved > RECORDS * INPUT
                || self.expected_job_coverage != "caller_must_join_original_job_and_parent_ids"
                || self.child_consumption != "not_observed"
                || self.native_render_success != "not_inferred"
            {
                return Err("incompatible original observer closure".into());
            }
            Ok(())
        }
        fn loss_free_close(&self) -> bool {
            self.status == "observer_closed"
                && matches!(self.reason.as_str(), "owner_shutdown" | "channel_closed")
                && self.queue_disconnection_proven
                && self.queued_captures_discarded == 0
                && self.capture_records_written == self.terminal_records_written
                && self.loss_counters_at_observer_close.zero()
        }
    }
    struct Record {
        ordinal: usize,
        header: Option<Vec<u8>>,
        stdin: Option<Vec<u8>>,
        terminal: Option<Vec<u8>>,
    }
    pub(super) struct Readback {
        source: PathBuf,
        source_identity: Metadata,
        target: PathBuf,
        declaration: Session,
        declaration_bytes: Vec<u8>,
        ticket_path: PathBuf,
        ticket_bytes: Vec<u8>,
        session_id: String,
        prior: BTreeSet<usize>,
        prior_provider_pid: Option<u32>,
    }
    fn present(path: &Path) -> FixtureResult<bool> {
        match fs::symlink_metadata(path) {
            Ok(_) => Ok(true),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(e) => Err(e.into()),
        }
    }
    fn directory(path: &Path) -> FixtureResult<Metadata> {
        let m = fs::symlink_metadata(path)?;
        if !path.is_absolute()
            || !m.is_dir()
            || m.file_type().is_symlink()
            || fs::canonicalize(path)? != path
        {
            return Err("stdin custody directory is not canonical".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if m.mode() & 0o777 != 0o700 {
                return Err("stdin custody directory is not private".into());
            }
        }
        Ok(m)
    }
    fn same_object(a: &Metadata, b: &Metadata) -> bool {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            a.dev() == b.dev() && a.ino() == b.ino() && a.uid() == b.uid()
        }
        #[cfg(not(unix))]
        {
            let _ = (a, b);
            false
        }
    }
    fn unchanged(a: &Metadata, b: &Metadata) -> bool {
        if !same_object(a, b) || a.len() != b.len() || a.modified().ok() != b.modified().ok() {
            return false;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            a.ctime() == b.ctime()
                && a.ctime_nsec() == b.ctime_nsec()
                && a.nlink() == b.nlink()
                && a.mode() == b.mode()
        }
        #[cfg(not(unix))]
        {
            false
        }
    }
    // This is the same custody reader's open/fstat boundary. Do not delegate
    // its private-file checks to a bytes-only observation, or substitute a
    // path metadata check for the metadata of this actual open descriptor.
    fn open_original_input(path: &Path, named: &Metadata) -> FixtureResult<(File, Metadata)> {
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            // A FIFO substituted after the initial regular-file check must
            // not wait for a writer. No symlink leaf is followed either.
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let file = options.open(path)?;
        let opened = file.metadata()?;
        if !opened.is_file() || !unchanged(named, &opened) {
            return Err("custody file changed before reading".into());
        }
        Ok((file, opened))
    }

    fn read_original(path: &Path, limit: usize, private: bool) -> FixtureResult<Vec<u8>> {
        #[cfg(not(unix))]
        let _ = private;

        let named = fs::symlink_metadata(path)?;
        if !named.is_file()
            || named.file_type().is_symlink()
            || named.len() > limit as u64
            || fs::canonicalize(path)? != path
        {
            return Err("original custody file is outside its bound".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if named.nlink() != 1 || (private && named.mode() & 0o077 != 0) {
                return Err("original custody file is not private and singly linked".into());
            }
        }
        let (mut file, before) = open_original_input(path, &named)?;
        let mut bytes = Vec::with_capacity(limit.min(META));
        Read::by_ref(&mut file)
            .take(limit as u64 + 1)
            .read_to_end(&mut bytes)?;
        let after = file.metadata()?;
        let linked = fs::symlink_metadata(path)?;
        if bytes.len() > limit
            || bytes.len() as u64 != before.len()
            || !unchanged(&before, &after)
            || !unchanged(&after, &linked)
            || fs::canonicalize(path)? != path
        {
            return Err("custody file changed while reading".into());
        }
        Ok(bytes)
    }

    fn read_bounded(path: &Path, limit: usize) -> FixtureResult<Vec<u8>> {
        read_original(path, limit, true)
    }
    pub(super) fn read_source(path: &Path, limit: usize) -> FixtureResult<Vec<u8>> {
        read_original(path, limit, false)
    }
    pub(super) fn new_private_directory(path: &Path) -> FixtureResult<()> {
        must_be_unused(path)?;
        let mut builder = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(path)?;
        directory(path)?;
        Ok(())
    }
    impl Readback {
        pub(super) fn require_binary_pins(&self, driver: &str, tool: &str) -> FixtureResult<()> {
            if !digest(driver)
                || !digest(tool)
                || self.declaration.driver_sha256 != driver
                || self.declaration.tool_sha256 != tool
            {
                return Err(
                    "Original selected Host binary declarations differ from frozen runtime inputs"
                        .into(),
                );
            }
            // This is a source join; the declaration grants no authority.
            Ok(())
        }
    }
    pub(super) fn new_bytes(path: &Path, bytes: &[u8]) -> FixtureResult<()> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        Ok(())
    }
    pub(super) fn new_json(path: &Path, value: &Value, limit: usize) -> FixtureResult<()> {
        let bytes = serde_json::to_vec_pretty(value)?;
        if bytes.len() > limit {
            return Err("custody snapshot JSON exceeds its bound".into());
        }
        new_bytes(path, &bytes)
    }
    fn retain_optional(
        source: &Path,
        target: &Path,
        limit: usize,
    ) -> FixtureResult<Option<Vec<u8>>> {
        if !present(source)? {
            return Ok(None);
        }
        let bytes = read_bounded(source, limit)?;
        new_bytes(target, &bytes)?;
        Ok(Some(bytes))
    }
    fn digest(value: &str) -> bool {
        value.len() == 64
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }
    fn id(value: &str) -> bool {
        !value.is_empty() && value.len() <= 512 && !value.chars().any(char::is_control)
    }
    fn validate_session(s: &Session) -> FixtureResult<()> {
        s.provider.validate_external()?;
        if s.schema != "semwright.host-stdin-custody/1"
            || s.provider.id != "driver:motion-canvas"
            || s.provider.kind != SourceKind::Driver
            || s.tool != "motion-node"
            || !digest(&s.driver_sha256)
            || !digest(&s.tool_sha256)
            || s.queue_capacity != 8
            || s.max_records != RECORDS
            || s.max_input_bytes != INPUT
            || s.max_total_input_bytes != RECORDS * INPUT
            || s.max_capture_metadata_bytes != META
            || s.max_terminal_metadata_bytes != TERMINAL
            || s.scope != SCOPE
        {
            return Err("original Host custody declaration is incompatible".into());
        }
        Ok(())
    }
    fn capture_identity(c: &Capture, s: &Session, pid: Option<u32>) -> FixtureResult<()> {
        if c.provider != s.provider
            || c.tool != s.tool
            || c.provider_process_id == 0
            || pid.is_some_and(|expected| expected != c.provider_process_id)
            || !c.terminal_file_required
            || c.stdin_bytes > INPUT
            || !digest(&c.stdin_sha256)
            || [
                &c.job_id,
                &c.request_id,
                &c.parent_request_id,
                &c.session_id,
                &c.tool,
            ]
            .into_iter()
            .any(|value| !id(value))
        {
            return Err("capture identity differs from the selected Host".into());
        }
        Ok(())
    }
    impl Readback {
        pub(super) fn begin(
            source: &Path,
            connection: &ProductionConnection,
            evidence: &Path,
        ) -> FixtureResult<Self> {
            if !cfg!(target_os = "linux") {
                return Err(semwright_native_sdk::Error::new(
                    semwright_native_sdk::ErrorCode::Unsupported,
                    "Original daemon stdin custody readback requires its Linux owner profile",
                )
                .into());
            }
            let source_identity = directory(source)?;
            let declaration_bytes = read_bounded(&source.join("session.json"), META)?;
            let declaration: Session = serde_json::from_slice(&declaration_bytes)?;
            validate_session(&declaration)?;
            if present(&source.join("closed.json"))? {
                return Err(
                    "original stdin observer already closed; do not issue another render".into(),
                );
            }
            let ticket_bytes = read_bounded(&connection.session_file, 128)?;
            let session_id = std::str::from_utf8(&ticket_bytes)?.trim().to_owned();
            uuid::Uuid::parse_str(&session_id)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if fs::metadata(&connection.session_file)?.uid() != source_identity.uid() {
                    return Err("owner ticket and custody directory have different owners".into());
                }
            }
            let mut prior = BTreeSet::new();
            let mut prior_provider_pid = None;
            for ordinal in 1..=RECORDS {
                let stem = format!("capture-{ordinal:04}");
                let names = [
                    format!("{stem}.stdin"),
                    format!("{stem}.json"),
                    format!("{stem}.terminal.json"),
                ];
                let exists = names
                    .iter()
                    .map(|n| present(&source.join(n)))
                    .collect::<FixtureResult<Vec<_>>>()?;
                if exists.iter().any(|v| *v) {
                    if !exists.iter().all(|v| *v) || ordinal != prior.len() + 1 {
                        return Err("earlier capture is unsettled or has a gap; inspect before another render".into());
                    }
                    let original = read_bounded(&source.join(&names[1]), META)?;
                    let capture: Capture = serde_json::from_slice(&original)?;
                    capture_identity(&capture, &declaration, prior_provider_pid)?;
                    prior_provider_pid = Some(capture.provider_process_id);
                    prior.insert(ordinal);
                }
            }
            if prior.len() == RECORDS {
                return Err("original stdin observer capacity is exhausted".into());
            }
            let target = evidence.with_extension("host-stdin");
            must_be_unused(&target)?;
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            builder.create(&target)?;
            directory(&target)?;
            new_bytes(&target.join("session.original.json"), &declaration_bytes)?;
            new_json(
                &target.join("readback.intent.json"),
                &json!({
                    "schema":"motionwright.private-host-stdin-readback-intent/1",
                    "original_custody_directory":source,
                    "connection_sha256":connection.identity()?, "prior_ordinals":prior,
                    "declaration_grants_authority":false, "automatic_retry":false
                }),
                META,
            )?;
            Ok(Self {
                source: source.to_path_buf(),
                source_identity,
                target,
                declaration,
                declaration_bytes,
                ticket_path: connection.session_file.clone(),
                ticket_bytes,
                session_id,
                prior,
                prior_provider_pid,
            })
        }
        pub(super) fn retain_native_return(&self, value: &Value) -> FixtureResult<()> {
            new_json(
                &self.target.join("native-return.snapshot.json"),
                value,
                8 * 1024 * 1024,
            )
        }
    }

    fn parse_terminal(bytes: &[u8]) -> FixtureResult<PipeTerminal> {
        // The typed decode also rejects duplicate keys; the Value checks exact
        // presence, since null and an absent optional field differ in this format.
        let terminal: PipeTerminal = serde_json::from_slice(bytes)?;
        let value: Value = serde_json::from_slice(bytes)?;
        let object = value
            .as_object()
            .ok_or("original pipe terminal is not an object")?;
        let unavailable = matches!(
            terminal.write_state.as_str(),
            "terminal_receiver_lost" | "observer_stopped_terminal_unavailable"
        );
        let expected: &[&str] = if unavailable {
            &["write_state", "child_consumption", "native_render_success"]
        } else {
            &[
                "write_state",
                "partial_delivery_possible",
                "io_error_kind",
                "raw_os_error",
                "child_consumption",
                "native_render_success",
            ]
        };
        if object.len() != expected.len()
            || expected.iter().any(|key| !object.contains_key(*key))
            || terminal.child_consumption != "not_observed"
            || terminal.native_render_success != "not_inferred"
        {
            return Err("original pipe terminal shape or observation scope changed".into());
        }
        let partial = match terminal.write_state.as_str() {
            "not_observed" | "write_all_failed" | "write_all_interrupted" => Some(true),
            "not_attempted" | "empty_input_no_write" | "write_all_completed" => Some(false),
            "terminal_receiver_lost" | "observer_stopped_terminal_unavailable" => None,
            _ => return Err("unknown original pipe write state".into()),
        };
        if terminal.partial_delivery_possible != partial {
            return Err("original pipe terminal partial-delivery flag is inconsistent".into());
        }
        if terminal.write_state == "write_all_failed" {
            if !terminal
                .io_error_kind
                .as_deref()
                .is_some_and(|kind| kind.len() <= 64 && id(kind))
            {
                return Err("failed original pipe write has no bounded I/O error kind".into());
            }
        } else if terminal.io_error_kind.is_some() || terminal.raw_os_error.is_some() {
            return Err("original pipe terminal adds an unobserved I/O error".into());
        }
        Ok(terminal)
    }
    impl PipeTerminal {
        fn write_completed(&self) -> bool {
            self.write_state == "write_all_completed"
        }
    }
    fn select_record(
        records: &[Record],
        declaration: &Session,
        parent: &str,
        session: &str,
        prior_pid: Option<u32>,
    ) -> FixtureResult<(usize, Capture, PipeTerminal)> {
        let mut selected = None;
        let mut provider_pid = prior_pid;
        let mut jobs = BTreeSet::new();
        let mut requests = BTreeSet::new();
        for record in records {
            let original = record
                .header
                .as_deref()
                .ok_or("new capture metadata is not yet retained")?;
            let capture: Capture = serde_json::from_slice(original)?;
            capture_identity(&capture, declaration, provider_pid)?;
            provider_pid = Some(capture.provider_process_id);
            if !jobs.insert(capture.job_id.clone()) || !requests.insert(capture.request_id.clone())
            {
                return Err("duplicate original Host job or request capture".into());
            }
            if capture.parent_request_id != parent {
                continue;
            }
            if selected.is_some() {
                return Err("more than one original Host capture matches the render parent".into());
            }
            if capture.session_id != session {
                return Err("original Host capture belongs to another persisted session".into());
            }
            let stdin = record
                .stdin
                .as_deref()
                .ok_or("matching original stdin is not retained")?;
            if stdin.len() != capture.stdin_bytes
                || stdin.len() > INPUT
                || format!("{:x}", Sha256::digest(stdin)) != capture.stdin_sha256
            {
                return Err(
                    "matching original stdin differs from its Host length or digest".into(),
                );
            }
            let terminal = parse_terminal(
                record
                    .terminal
                    .as_deref()
                    .ok_or("matching original Host pipe terminal is not retained")?,
            )?;
            if (stdin.is_empty() && terminal.write_completed())
                || (!stdin.is_empty() && terminal.write_state == "empty_input_no_write")
            {
                return Err(
                    "original pipe terminal does not match the retained input length".into(),
                );
            }
            selected = Some((record.ordinal, capture, terminal));
        }
        selected
            .ok_or_else(|| "no original Host capture matches the completed render parent".into())
    }
    fn receipt_parent(
        value: &Value,
        project: &Project,
        request: &str,
        declaration: &Session,
    ) -> FixtureResult<String> {
        let effect = format!("{request}:motion:0:render-start");
        if value["project_id"] != json!(project.id)
            || value["generation"] != json!(project.generation)
            || value["revision"] != json!(project.revision)
            || value["request_id"].as_str() != Some(effect.as_str())
            || value["stage"] != "completed"
            || value["command"] != "driver.motion-canvas.render.start"
            || value.pointer("/payload/result/provenance_checked") != Some(&Value::Bool(true))
        {
            return Err(
                "original completed render-start receipt is absent or bound to another revision"
                    .into(),
            );
        }
        let provenance = value
            .pointer("/payload/result/execution/provenance")
            .ok_or("original render-start provenance is absent")?;
        if provenance["provider"] != declaration.provider.id
            || provenance["source"] != json!(declaration.provider.kind)
            || provenance["provider_version"] != declaration.provider.version
        {
            return Err(
                "original render-start provider differs from the selected Host declaration".into(),
            );
        }
        // Host tool-job identifiers are deliberately not compared with MC job_ref.
        value
            .pointer("/payload/broker_request_id")
            .and_then(Value::as_str)
            .filter(|parent| id(parent))
            .map(str::to_owned)
            .ok_or_else(|| "original render-start Broker parent identifier is absent".into())
    }

    fn publication_ready_for_snapshot(records: &[Record], parent: &str) -> FixtureResult<bool> {
        let mut selected = None;
        for record in records {
            // Every Some(header) has already been copied create_new verbatim.
            let Some(original) = record.header.as_deref() else {
                continue;
            };
            let capture: Capture = serde_json::from_slice(original)?;
            if capture.parent_request_id != parent {
                continue;
            }
            if selected.is_some() {
                return Err("duplicate parent observed while awaiting original publication".into());
            }
            selected = Some(record);
        }
        // Presence stops waiting even for NotObserved/Interrupted. The existing
        // strict join and terminal decoder decide what was actually observed.
        Ok(selected.is_some_and(|record| record.stdin.is_some() && record.terminal.is_some()))
    }
    fn retain_capture_part(
        source: &Path,
        target: &Path,
        limit: usize,
        errors: &mut Vec<Value>,
    ) -> Option<Vec<u8>> {
        match retain_optional(source, target, limit) {
            Ok(bytes) => bytes,
            Err(error) => {
                errors.push(json!({"source":source,"target":target,"error":error.to_string()}));
                None
            }
        }
    }
    impl Readback {
        pub(super) fn finish(
            self,
            service: &StudioService,
            project: &Project,
            request: &str,
            native_succeeded: bool,
            native_snapshot_retained: bool,
            native_snapshot_error: Option<&str>,
        ) -> FixtureResult<()> {
            let effect = format!("{request}:motion:0:render-start");
            let (receipt, receipt_lookup_error) =
                match service.latest_production_receipt(project.id, &effect) {
                    Ok(Some(value)) => match serde_json::to_value(value) {
                        Ok(value) => (Some(value), None),
                        Err(error) => (None, Some(error.to_string())),
                    },
                    Ok(None) => (
                        None,
                        Some("original render-start receipt is absent".to_owned()),
                    ),
                    Err(error) => (None, Some(error.to_string())),
                };
            let receipt_retention_error = new_json(
                &self.target.join("broker-receipt.snapshot.json"),
                &json!({
                    "schema":"motionwright.private-production-receipt-snapshot/1",
                    "source":"existing StudioService receipt", "wire_bytes":false,
                    "receipt":receipt,"lookup_error":receipt_lookup_error,
                    "snapshot_grants_authority":false
                }),
                8 * 1024 * 1024,
            )
            .err()
            .map(|error| error.to_string());
            let publication_parent = receipt
                .as_ref()
                .map(|value| receipt_parent(value, project, request, &self.declaration))
                .transpose();
            let expected_parent = publication_parent
                .as_ref()
                .ok()
                .and_then(|value| value.as_deref());
            let publication_parent_available = expected_parent.is_some();
            let mut publication_wait_error =
                publication_parent.as_ref().err().map(ToString::to_string);
            let mut records: Vec<Record> = (1..=RECORDS)
                .filter(|ordinal| !self.prior.contains(ordinal))
                .map(|ordinal| Record {
                    ordinal,
                    header: None,
                    stdin: None,
                    terminal: None,
                })
                .collect();
            let mut capture_errors = Vec::new();
            let publication_started = std::time::Instant::now();
            let publication_budget = std::time::Duration::from_secs(2);
            let mut publication_passes = 0_u32;
            let mut publication_ready_observed = false;
            let mut publication_budget_exhausted = false;
            let mut publication_stopped_on_close = false;
            // Publication is independent of the native result. Revisit only
            // absent fixed names. Each observed original is retained once;
            // neither an observed I/O failure nor a producer effect is retried.
            for pass in 1..=41 {
                publication_passes = pass;
                for record in &mut records {
                    let stem = format!("capture-{:04}", record.ordinal);
                    if record.header.is_none() {
                        let name = format!("{stem}.json");
                        record.header = retain_capture_part(
                            &self.source.join(&name),
                            &self.target.join(&name),
                            META,
                            &mut capture_errors,
                        );
                    }
                    if record.stdin.is_none() {
                        let name = format!("{stem}.stdin");
                        record.stdin = retain_capture_part(
                            &self.source.join(&name),
                            &self.target.join(&name),
                            INPUT,
                            &mut capture_errors,
                        );
                    }
                    if record.terminal.is_none() {
                        let name = format!("{stem}.terminal.json");
                        record.terminal = retain_capture_part(
                            &self.source.join(&name),
                            &self.target.join(&name),
                            TERMINAL,
                            &mut capture_errors,
                        );
                    }
                }
                if !capture_errors.is_empty() {
                    break;
                }
                let Some(parent) = expected_parent else {
                    break;
                };
                match publication_ready_for_snapshot(&records, parent) {
                    Ok(true) => {
                        publication_ready_observed = true;
                        break;
                    }
                    Ok(false) => (),
                    Err(error) => {
                        publication_wait_error = Some(error.to_string());
                        break;
                    }
                }
                if publication_stopped_on_close {
                    break;
                }
                match present(&self.source.join("closed.json")) {
                    Ok(true) => {
                        publication_stopped_on_close = true;
                        continue;
                    }
                    Ok(false) => (),
                    Err(error) => {
                        publication_wait_error = Some(error.to_string());
                        break;
                    }
                }
                let remaining = publication_budget.saturating_sub(publication_started.elapsed());
                if remaining.is_zero() || pass == 40 {
                    publication_budget_exhausted = true;
                    break;
                }
                std::thread::sleep(remaining.min(std::time::Duration::from_millis(50)));
            }
            let publication_elapsed_ms = publication_started.elapsed().as_millis();
            records.retain(|record| {
                record.header.is_some() || record.stdin.is_some() || record.terminal.is_some()
            });
            let (closed_bytes, closed_read_error) = match retain_optional(
                &self.source.join("closed.json"),
                &self.target.join("closed.original.json"),
                META,
            ) {
                Ok(bytes) => (bytes, None),
                Err(error) => (None, Some(error.to_string())),
            };
            let identity = (|| -> FixtureResult<()> {
                let current = directory(&self.source)?;
                if !same_object(&self.source_identity, &current) {
                    return Err("original stdin custody directory was replaced".into());
                }
                if read_bounded(&self.source.join("session.json"), META)? != self.declaration_bytes
                {
                    return Err(
                        "original stdin custody declaration changed during rendering".into(),
                    );
                }
                // The existing CLI may atomically rewrite an unchanged ticket.
                // Compare its original bytes, not an invented session or inode.
                if read_bounded(&self.ticket_path, 128)? != self.ticket_bytes {
                    return Err(
                        "existing owner session changed during rendering; no redispatch".into(),
                    );
                }
                Ok(())
            })();
            let identity_error = identity.as_ref().err().map(ToString::to_string);
            let selected = (|| -> FixtureResult<(usize, Capture, PipeTerminal)> {
                if let Some(error) = identity_error.as_deref() {
                    return Err(error.into());
                }
                if !capture_errors.is_empty() {
                    return Err("one or more original capture files could not be retained; inspect capture_read_errors".into());
                }
                let receipt = receipt
                    .as_ref()
                    .ok_or("original completed render-start receipt was not retained")?;
                let parent = receipt_parent(receipt, project, request, &self.declaration)?;
                select_record(
                    &records,
                    &self.declaration,
                    &parent,
                    &self.session_id,
                    self.prior_provider_pid,
                )
            })();
            let closed: FixtureResult<Option<Closed>> = (|| {
                if let Some(error) = closed_read_error.as_deref() {
                    return Err(error.into());
                }
                let Some(bytes) = closed_bytes.as_deref() else {
                    return Ok(None);
                };
                let value: Closed = serde_json::from_slice(bytes)?;
                value.validate()?;
                Ok(Some(value))
            })();
            let selected_observation = match &selected {
                Ok((ordinal, capture, terminal)) => json!({
                    "matched":true,"ordinal":ordinal,"capture":capture,"terminal":terminal,
                    "pipe_write_all_completed":terminal.write_completed(),
                    "child_consumption":"not_observed","native_render_success":"not_inferred",
                    "joined_to":"original completed render-start Broker parent and persisted session",
                    "Host_job_id_equals_MC_job_ref_claimed":false
                }),
                Err(error) => json!({
                    "matched":false,"error":error.to_string(),
                    "pipe_write_all_completed":null,"child_consumption":"not_observed",
                    "native_render_success":"not_inferred"
                }),
            };
            let closure = closed.as_ref().ok().and_then(Option::as_ref);
            let closure_observation = match &closed {
                Ok(Some(value)) => json!({"original_close_record_present":true,"original":value}),
                Ok(None) => json!({"original_close_record_present":false,"original":null}),
                Err(error) => json!({"original_close_record_present":closed_bytes.is_some(),
                                     "read_or_validation_error":error.to_string()}),
            };
            new_json(
                &self.target.join("readback.observation.json"),
                &json!({
                    "schema":"motionwright.private-host-stdin-readback/1",
                    "native_success_returned":native_succeeded,
                    "native_return_snapshot_retained":native_snapshot_retained,
                    "native_return_snapshot_error":native_snapshot_error,
                    "original_identity_unchanged":identity.is_ok(),"identity_error":identity_error,
                    "publication_observation":{
                        "passes":publication_passes,"maximum_passes":41,"wait_budget_ms":2000,
                        "elapsed_ms":publication_elapsed_ms,"parent_available":publication_parent_available,
                        "ready_for_single_join":publication_ready_observed,
                        "budget_exhausted":publication_budget_exhausted,
                        "stopped_on_original_close_file":publication_stopped_on_close,
                        "error":publication_wait_error,"producer_redispatched":false,
                        "each_observed_original_retained_once":true
                    },
                    "new_capture_ordinals_retained":records.iter().map(|record| record.ordinal).collect::<Vec<_>>(),
                    "capture_read_errors":capture_errors,"receipt_lookup_error":receipt_lookup_error,
                    "receipt_retention_error":receipt_retention_error,
                    "selected_capture_join":selected_observation,
                    "observer_closure":closure_observation,
                    "loss_counters_observed":closure.is_some(),
                    "receiver_queue_disconnection_proven":closure.map(|value| value.queue_disconnection_proven),
                    "observer_loss_free_close":closure.map(Closed::loss_free_close),
                    "receiver_lifetime_completeness_claimed":false,
                    "global_job_coverage_verified":false,"custody_complete":false,
                    "declaration_grants_authority":false,
                    "child_consumption":"not_observed","native_render_success":"not_inferred",
                    "final_G2_requires":"original owner receiver closure observed through the existing lifecycle before sealing; no fabricated closure or SIGTERM assumption",
                    "automatic_retry":false
                }),
                8 * 1024 * 1024,
            )?;
            if !native_snapshot_retained {
                return Err(format!(
                    "original native return snapshot could not be retained: {}; no redispatch",
                    native_snapshot_error.unwrap_or("reason unavailable")
                )
                .into());
            }
            if let Some(error) = receipt_retention_error {
                return Err(format!(
                    "original receipt snapshot retention failed: {error}; no redispatch"
                )
                .into());
            }
            let (_, _, terminal) = selected?;
            if let Some(error) = publication_wait_error {
                return Err(format!(
                    "original publication observation failed: {error}; no redispatch"
                )
                .into());
            }
            if native_succeeded && !terminal.write_completed() {
                return Err(format!("native success was returned, but original pipe state is {}; child consumption is not observed; no redispatch",
                    terminal.write_state).into());
            }
            if let Some(closure) = closed? {
                if !closure.loss_free_close() {
                    return Err("selected capture was observed, but original receiver closure is incomplete or reports losses; no redispatch".into());
                }
            }
            // Absent closure is explicitly unobserved, not complete. The owner
            // may still be alive; final G2 sealing remains a separate obligation.
            Ok(())
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        const RAW: &[u8] = b"synthetic contract input; no native process\n";
        const PARENT: &str = "synthetic-original-broker-parent";
        const SESSION: &str = "00000000-0000-4000-8000-000000000123";
        fn declaration() -> Session {
            Session {
                schema: "semwright.host-stdin-custody/1".into(),
                provider: ProviderIdentity::external(
                    SourceKind::Driver,
                    "motion-canvas",
                    "synthetic-test",
                )
                .unwrap(),
                driver_sha256: "ab".repeat(32),
                tool: "motion-node".into(),
                tool_sha256: "cd".repeat(32),
                queue_capacity: 8,
                max_records: RECORDS,
                max_input_bytes: INPUT,
                max_total_input_bytes: RECORDS * INPUT,
                max_capture_metadata_bytes: META,
                max_terminal_metadata_bytes: TERMINAL,
                scope: SCOPE.into(),
            }
        }
        fn terminal(state: &str) -> Vec<u8> {
            let value = if matches!(
                state,
                "terminal_receiver_lost" | "observer_stopped_terminal_unavailable"
            ) {
                json!({"write_state":state,"child_consumption":"not_observed","native_render_success":"not_inferred"})
            } else {
                json!({"write_state":state,
                    "partial_delivery_possible":matches!(state, "not_observed" | "write_all_failed" | "write_all_interrupted"),
                    "io_error_kind":if state == "write_all_failed" { Some("BrokenPipe") } else { None },
                    "raw_os_error":if state == "write_all_failed" { Some(32) } else { None },
                    "child_consumption":"not_observed","native_render_success":"not_inferred"})
            };
            serde_json::to_vec(&value).unwrap()
        }
        fn record() -> Record {
            let declaration = declaration();
            let header = Capture {
                provider: declaration.provider,
                provider_process_id: 1234,
                job_id: "synthetic-Host-tool-job".into(),
                request_id: "synthetic-tool-start-request".into(),
                parent_request_id: PARENT.into(),
                session_id: SESSION.into(),
                tool: "motion-node".into(),
                stdin_bytes: RAW.len(),
                stdin_sha256: format!("{:x}", Sha256::digest(RAW)),
                terminal_file_required: true,
            };
            Record {
                ordinal: 1,
                header: Some(serde_json::to_vec(&header).unwrap()),
                stdin: Some(RAW.to_vec()),
                terminal: Some(terminal("write_all_completed")),
            }
        }
        fn change_header(record: &mut Record, change: impl FnOnce(&mut Value)) {
            let mut value: Value = serde_json::from_slice(record.header.as_ref().unwrap()).unwrap();
            change(&mut value);
            record.header = Some(serde_json::to_vec(&value).unwrap());
        }
        fn closed() -> Closed {
            Closed {
                schema: "semwright.host-stdin-custody-close/1".into(),
                status: "observer_closed".into(),
                reason: "owner_shutdown".into(),
                capture_records_written: 1,
                terminal_records_written: 1,
                input_bytes_reserved: RAW.len(),
                queued_captures_discarded: 0,
                queue_disconnection_proven: true,
                loss_counters_at_observer_close: Losses::default(),
                expected_job_coverage: "caller_must_join_original_job_and_parent_ids".into(),
                child_consumption: "not_observed".into(),
                native_render_success: "not_inferred".into(),
            }
        }
        #[test]
        fn selected_capture_joins_original_parent_session_and_bytes() {
            let (_, capture, terminal) =
                select_record(&[record()], &declaration(), PARENT, SESSION, None).unwrap();
            assert_eq!(capture.parent_request_id, PARENT);
            assert_eq!(capture.job_id, "synthetic-Host-tool-job");
            assert_ne!(capture.job_id, "synthetic-MC-job-ref");
            assert!(terminal.write_completed());
            assert_eq!(terminal.child_consumption, "not_observed");
            assert_eq!(terminal.native_render_success, "not_inferred");
        }
        #[test]
        fn crossed_parent_or_session_never_matches() {
            for (parent, session) in [("another-parent", SESSION), (PARENT, "another-session")] {
                assert!(select_record(&[record()], &declaration(), parent, session, None).is_err());
            }
        }
        #[test]
        fn crossed_provider_or_tool_is_rejected() {
            let mut wrong_provider = record();
            change_header(&mut wrong_provider, |value| {
                value["provider"] = serde_json::to_value(
                    ProviderIdentity::external(SourceKind::Driver, "blender", "synthetic-test")
                        .unwrap(),
                )
                .unwrap()
            });
            let mut wrong_tool = record();
            change_header(&mut wrong_tool, |value| {
                value["tool"] = json!("another-tool")
            });
            for capture in [wrong_provider, wrong_tool] {
                assert!(select_record(&[capture], &declaration(), PARENT, SESSION, None).is_err());
            }
        }
        #[test]
        fn duplicate_parent_captures_are_not_selected_arbitrarily() {
            let first = record();
            let mut second = record();
            second.ordinal = 2;
            change_header(&mut second, |value| {
                value["job_id"] = json!("second-Host-tool-job");
                value["request_id"] = json!("second-tool-start-request");
            });
            assert!(
                select_record(&[first, second], &declaration(), PARENT, SESSION, None).is_err()
            );
        }
        #[test]
        fn changed_original_stdin_or_length_is_rejected() {
            let mut tampered = record();
            tampered.stdin.as_mut().unwrap()[0] ^= 1;
            let mut wrong_length = record();
            change_header(&mut wrong_length, |value| {
                value["stdin_bytes"] = json!(RAW.len() + 1)
            });
            for capture in [tampered, wrong_length] {
                assert!(select_record(&[capture], &declaration(), PARENT, SESSION, None).is_err());
            }
        }
        #[test]
        fn provider_process_transition_is_not_same_owner_lifetime() {
            assert!(
                select_record(&[record()], &declaration(), PARENT, SESSION, Some(999)).is_err()
            );
            assert!(
                select_record(&[record()], &declaration(), PARENT, SESSION, Some(1234)).is_ok()
            );
        }
        #[test]
        fn not_observed_remains_uncertain_after_matching() {
            let mut capture = record();
            capture.terminal = Some(terminal("not_observed"));
            let (_, _, observed) =
                select_record(&[capture], &declaration(), PARENT, SESSION, None).unwrap();
            assert_eq!(observed.write_state, "not_observed");
            assert_eq!(observed.partial_delivery_possible, Some(true));
            assert!(!observed.write_completed());
        }
        #[test]
        fn interrupted_delivery_is_preserved_without_completion() {
            let mut capture = record();
            capture.terminal = Some(terminal("write_all_interrupted"));
            let (_, _, observed) =
                select_record(&[capture], &declaration(), PARENT, SESSION, None).unwrap();
            assert_eq!(observed.write_state, "write_all_interrupted");
            assert_eq!(observed.partial_delivery_possible, Some(true));
            assert!(!observed.write_completed());
        }
        #[test]
        fn unavailable_terminal_formats_remain_distinct() {
            for state in [
                "terminal_receiver_lost",
                "observer_stopped_terminal_unavailable",
            ] {
                let observed = parse_terminal(&terminal(state)).unwrap();
                assert_eq!(observed.write_state, state);
                assert_eq!(observed.partial_delivery_possible, None);
                assert!(!observed.write_completed());
            }
        }
        #[test]
        fn failed_write_retains_original_io_details() {
            let observed = parse_terminal(&terminal("write_all_failed")).unwrap();
            assert_eq!(observed.io_error_kind.as_deref(), Some("BrokenPipe"));
            assert_eq!(observed.raw_os_error, Some(32));
            assert_eq!(observed.partial_delivery_possible, Some(true));
            assert!(!observed.write_completed());
        }
        #[test]
        fn terminal_unknown_duplicate_missing_or_extra_fields_are_rejected() {
            let original: Value = serde_json::from_slice(&terminal("write_all_completed")).unwrap();
            for mutation in 0..4 {
                let mut value = original.clone();
                match mutation {
                    0 => value["write_state"] = json!("success"),
                    1 => {
                        value.as_object_mut().unwrap().remove("raw_os_error");
                    }
                    2 => value["invented_consumption_proof"] = json!(true),
                    _ => value["partial_delivery_possible"] = json!(true),
                }
                assert!(parse_terminal(&serde_json::to_vec(&value).unwrap()).is_err());
            }
            assert!(parse_terminal(br#"{"write_state":"terminal_receiver_lost","write_state":"observer_stopped_terminal_unavailable","child_consumption":"not_observed","native_render_success":"not_inferred"}"#).is_err());
        }
        #[test]
        fn observer_closure_does_not_erase_losses_or_discarded_captures() {
            assert!(closed().loss_free_close());
            for mutation in 0..8 {
                let mut value = closed();
                match mutation {
                    0 => value.loss_counters_at_observer_close.capture_full = 1,
                    1 => value.loss_counters_at_observer_close.capture_closed = 1,
                    2 => value.loss_counters_at_observer_close.capture_oversized = 1,
                    3 => value.loss_counters_at_observer_close.terminal_receiver_lost = 1,
                    4 => value.queued_captures_discarded = 1,
                    5 => value.queue_disconnection_proven = false,
                    6 => value.terminal_records_written = 0,
                    _ => value.status = "incomplete".into(),
                }
                assert!(!value.loss_free_close());
            }
        }
        #[test]
        fn observer_close_schema_and_scope_are_strict() {
            let mut value = serde_json::to_value(closed()).unwrap();
            value["invented_child_consumption"] = json!(true);
            assert!(serde_json::from_value::<Closed>(value).is_err());
            let mut wrong_scope = closed();
            wrong_scope.child_consumption = "observed".into();
            assert!(wrong_scope.validate().is_err());
        }
        #[test]
        fn completed_write_without_input_is_not_accepted() {
            let mut capture = record();
            capture.stdin = Some(Vec::new());
            change_header(&mut capture, |value| {
                value["stdin_bytes"] = json!(0);
                value["stdin_sha256"] = json!(format!("{:x}", Sha256::digest([])));
            });
            assert!(select_record(&[capture], &declaration(), PARENT, SESSION, None).is_err());
        }
        #[test]
        fn publication_waits_for_delayed_original_terminal_without_upgrading_its_state() {
            let mut capture = record();
            capture.terminal = None;
            assert!(
                !publication_ready_for_snapshot(std::slice::from_ref(&capture), PARENT).unwrap()
            );
            capture.terminal = Some(terminal("not_observed"));
            assert!(
                publication_ready_for_snapshot(std::slice::from_ref(&capture), PARENT).unwrap()
            );
            let (_, _, observed) =
                select_record(&[capture], &declaration(), PARENT, SESSION, None).unwrap();
            assert!(!observed.write_completed());
            assert_eq!(observed.write_state, "not_observed");
        }
        #[test]
        fn another_parents_complete_capture_does_not_end_publication_wait() {
            assert!(!publication_ready_for_snapshot(&[record()], "another-parent").unwrap());
            let pending = Record {
                ordinal: 1,
                header: None,
                stdin: Some(RAW.to_vec()),
                terminal: None,
            };
            assert!(!publication_ready_for_snapshot(&[pending], PARENT).unwrap());
        }
        #[test]
        fn duplicate_or_invalid_retained_metadata_stops_publication_wait() {
            assert!(publication_ready_for_snapshot(&[record(), record()], PARENT).is_err());
            let mut invalid = record();
            invalid.header = Some(b"{".to_vec());
            assert!(publication_ready_for_snapshot(&[invalid], PARENT).is_err());
        }
        #[test]
        fn absent_terminal_does_not_turn_capture_into_delivery() {
            let mut capture = record();
            capture.terminal = None;
            assert!(select_record(&[capture], &declaration(), PARENT, SESSION, None).is_err());
        }

        #[cfg(unix)]
        struct OriginalInputRoot(PathBuf);

        #[cfg(unix)]
        impl OriginalInputRoot {
            fn new() -> Self {
                use std::os::unix::fs::DirBuilderExt;
                let path = std::env::temp_dir().join(format!(
                    "motionwright-stdin-original-{}",
                    uuid::Uuid::now_v7()
                ));
                let mut builder = fs::DirBuilder::new();
                builder.mode(0o700).create(&path).unwrap();
                Self(fs::canonicalize(path).unwrap())
            }

            fn original(&self, name: &str, bytes: &[u8]) -> PathBuf {
                let path = self.0.join(name);
                new_bytes(&path, bytes).unwrap();
                path
            }
        }

        #[cfg(unix)]
        impl Drop for OriginalInputRoot {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }

        #[cfg(unix)]
        #[test]
        fn original_reader_preserves_private_bytes_empty_presence_and_the_bound() {
            let root = OriginalInputRoot::new();
            let original = root.original("capture.stdin", RAW);
            assert_eq!(read_bounded(&original, RAW.len()).unwrap(), RAW);
            assert!(read_bounded(&original, RAW.len() - 1).is_err());
            let empty = root.original("empty.stdout", b"");
            assert_eq!(read_bounded(&empty, 16).unwrap(), b"");
            assert!(read_bounded(&root.0.join("absent.stdout"), 16).is_err());
        }

        #[cfg(unix)]
        #[test]
        fn original_reader_keeps_private_mode_and_single_link_requirements() {
            use std::os::unix::fs::PermissionsExt;
            let root = OriginalInputRoot::new();
            let original = root.original("capture.stdin", RAW);
            fs::set_permissions(&original, fs::Permissions::from_mode(0o644)).unwrap();
            assert!(read_bounded(&original, RAW.len()).is_err());
            // Public source inputs remain distinct from private Host custody.
            assert_eq!(read_source(&original, RAW.len()).unwrap(), RAW);
            fs::set_permissions(&original, fs::Permissions::from_mode(0o600)).unwrap();
            let alias = root.0.join("another-name.stdin");
            fs::hard_link(&original, &alias).unwrap();
            assert!(read_bounded(&original, RAW.len()).is_err());
            assert!(read_source(&original, RAW.len()).is_err());
            fs::remove_file(alias).unwrap();
            assert_eq!(read_bounded(&original, RAW.len()).unwrap(), RAW);
        }

        #[cfg(unix)]
        #[test]
        fn original_reader_rejects_replaced_regular_and_symlink_after_metadata() {
            use std::os::unix::fs::symlink;
            let root = OriginalInputRoot::new();
            let original = root.original("capture.stdin", RAW);
            let named = fs::symlink_metadata(&original).unwrap();
            let held_name = root.0.join("held-original.stdin");
            fs::rename(&original, &held_name).unwrap();
            root.original("capture.stdin", RAW);
            // Equal bytes/length do not authorize a different opened inode.
            assert!(open_original_input(&original, &named).is_err());
            fs::remove_file(&original).unwrap();
            symlink(&held_name, &original).unwrap();
            assert!(open_original_input(&original, &named).is_err());
            assert!(read_source(&original, RAW.len()).is_err());
        }

        #[cfg(unix)]
        #[test]
        fn original_reader_rejects_fifo_replacement_without_a_writer() {
            use std::ffi::CString;
            use std::os::fd::AsRawFd;
            use std::os::unix::ffi::OsStrExt;
            let root = OriginalInputRoot::new();
            let original = root.original("capture.stdin", RAW);
            let named = fs::symlink_metadata(&original).unwrap();
            // Verify the same opener's descriptor flags before introducing a
            // FIFO. A regression removing nonblocking admission fails here
            // without entering a potentially blocking open in this control.
            let (opened, observed) = open_original_input(&original, &named).unwrap();
            assert!(unchanged(&named, &observed));
            let flags = unsafe { libc::fcntl(opened.as_raw_fd(), libc::F_GETFL) };
            assert!(flags >= 0);
            assert_ne!(flags & libc::O_NONBLOCK, 0);
            drop(opened);
            fs::remove_file(&original).unwrap();
            let name = CString::new(original.as_os_str().as_bytes()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
            // This deliberately reuses the real regular-file metadata from
            // before substitution. The reader must reject the actual FIFO FD
            // before reading; no writer, process, native call or retry exists.
            assert!(open_original_input(&original, &named).is_err());
        }
    }
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
