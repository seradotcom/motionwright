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
    production::{ProductionConnection, ProductionCoordinator},
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

fn usage() -> ! {
    eprintln!(
        "usage: native-film180-e2e seed FRESH_DATABASE NEW_SOURCE_WAV NEW_PROJECT_JSON |\
         readback EXISTING_DATABASE NEW_OBSERVATION_JSON |\
         render EXISTING_DATABASE OWNED_CONNECTION NEW_EVIDENCE_JSON"
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
            Some("New scene retains original intent"),
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
    let request_id = NATIVE_REQUEST;
    let previous =
        service.latest_production_receipt(original.id, &format!("{request_id}:start"))?;
    if previous.is_some() {
        return Err(
            "native Film180 original effect identity already exists; inspect outcome, never retry"
                .into(),
        );
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
        "render" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            render_original(&database, &connection, &evidence).await?;
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
