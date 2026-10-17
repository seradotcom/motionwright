//! Real pinned Broker/Driver Host two-segment timeline smoke test.
//! Never run on the developer workstation; the workflow provisions the
//! Semwright and Motion Canvas/MLT sandbox in a disposable CI runner.
use motionwright_domain::{
    AlignmentEvidence, BlendMode, CanvasNode, Change, CoordinateSpace, DeliveryArchetype,
    DeliveryNarrativeRole, DeliveryRecipe, DeliveryRecipeSchema, DeliverySceneIntent, NodeStyle,
    OriginalMixRecipe, Project, RationalTime, RevisionStamp, TranscriptSegment,
};
use motionwright_native::{
    film::{FilmBuildOptions, SceneFilmIntent, build_motion_canvas_segments},
    production::{
        MltAudioArtifact, MltMultisegmentAvMasterRequest, MltMultisegmentOriginalMixMasterRequest,
        ProductionConnection, ProductionCoordinator, canonical_original_mix_film_options,
    },
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| {
        eprintln!(
            "usage: native-mlt-sequence-e2e seed DATABASE | assemble DATABASE CONNECTION EVIDENCE"
        );
        std::process::exit(2);
    })
}
fn fixture_mode() -> Result<String, Box<dyn std::error::Error>> {
    let mode = std::env::var("MOTIONWRIGHT_MLT_MODE").unwrap_or_else(|_| "pcm".into());
    if !matches!(
        mode.as_str(),
        "pcm" | "video-only" | "final-mp4" | "captioned-mp4" | "original-mix-mp4"
    ) {
        return Err("Unsupported native MLT fixture mode".into());
    }
    Ok(mode)
}
fn node(scene_number: usize) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: format!("Native segment tile {scene_number:02}"),
        kind: "rectangle".into(),
        parent_id: None,
        x: 420.0,
        y: 320.0,
        width: 200.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(if scene_number.is_multiple_of(2) {
                "#F5F5F2".into()
            } else {
                "#8FCEDB".into()
            }),
            stroke: None,
            stroke_width: 0.0,
            font_family: None,
            font_size: None,
            font_weight: None,
            line_height: None,
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
/// Synthetic source tone used only in GitHub Actions to verify actual mux
/// clocks; this is not a real voice take or human-approved sound design.
fn write_synthetic_stereo_wav(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let sample_count = 52_800_u32; // 33 real frames / 30 fps at exactly 48 kHz
    let pcm_bytes = sample_count * 4;
    let mut output = OpenOptions::new().write(true).create_new(true).open(path)?;
    output.write_all(b"RIFF")?;
    output.write_all(&(pcm_bytes + 36).to_le_bytes())?;
    output.write_all(b"WAVEfmt ")?;
    output.write_all(&16_u32.to_le_bytes())?;
    output.write_all(&1_u16.to_le_bytes())?;
    output.write_all(&2_u16.to_le_bytes())?;
    output.write_all(&48_000_u32.to_le_bytes())?;
    output.write_all(&192_000_u32.to_le_bytes())?;
    output.write_all(&4_u16.to_le_bytes())?;
    output.write_all(&16_u16.to_le_bytes())?;
    output.write_all(b"data")?;
    output.write_all(&pcm_bytes.to_le_bytes())?;
    for index in 0..sample_count {
        // Real 48 kHz stereo import; distinct bounded left-400Hz and
        // right-600Hz source signatures expose swapped, silent, and
        // unintended channel merges after the native AAC mux.
        // One period is exactly 120 / 80 samples respectively.
        let left = (((index % 120) as i16) - 60) * 250;
        let right = (((index % 80) as i16) - 40) * 260;
        output.write_all(&left.to_le_bytes())?;
        output.write_all(&right.to_le_bytes())?;
    }
    output.sync_all()?;
    Ok(())
}

// Analytic test signals, not speech, licensed music, or human sound-design approval.
// Pure integer arithmetic keeps each source WAV byte-identical across providers.
const CANONICAL_FIXTURE_SAMPLES: u32 = 96_000;
const CANONICAL_VOICE_WINDOWS: [(u32, u32); 2] = [(8_000, 28_000), (52_800, 86_400)];
const CANONICAL_SFX_WINDOWS: [(u32, u32); 3] = [(1_600, 6_400), (49_600, 55_200), (92_000, 96_000)];

fn fixture_knot(index: u32, seed: u32) -> i64 {
    let mut value = seed ^ index.wrapping_mul(0x9E37_79B9);
    value ^= value >> 16;
    value = value.wrapping_mul(0x7FEB_352D);
    value ^= value >> 15;
    value = value.wrapping_mul(0x846C_A68B);
    value ^= value >> 16;
    i64::from(value >> 16) - 32_768
}

fn fixture_smooth_noise(at: u32, period: u32, seed: u32) -> i64 {
    let index = at / period;
    let remainder = i64::from(at % period);
    let period = i64::from(period);
    let weight = 3 * remainder * remainder * period - 2 * remainder * remainder * remainder;
    let scale = period * period * period;
    (fixture_knot(index, seed) * (scale - weight) + fixture_knot(index + 1, seed) * weight) / scale
}

fn fixture_ramp(position: u32) -> i64 {
    let position = i64::from(position.min(256));
    (3 * position * position * 256 - 2 * position * position * position) * 32_768
        / (256 * 256 * 256)
}

fn fixture_chirp(at: u32, base_hz: u32, sweep_hz: u32, phase_offset: u32) -> i64 {
    let at = u64::from(at);
    let phase = (at * u64::from(base_hz) * 65_536 / 48_000
        + at * at * u64::from(sweep_hz) * 65_536
            / (2 * 48_000 * u64::from(CANONICAL_FIXTURE_SAMPLES))
        + u64::from(phase_offset))
        % 65_536;
    // Integer approximation to sine, with a rational curvature correction.
    // The chirp's phase is derived directly from sample index, never wall time.
    let angle = phase as i64 - 32_768;
    let parabola = 4 * angle * (32_768 - angle.abs()) / 32_768;
    parabola + 9 * (parabola * parabola.abs() / 32_768 - parabola) / 40
}

fn nonperiodic_bus_sample(bus: usize, at: u32, channel: usize) -> i16 {
    const FREQUENCIES: [[[(u32, u32, u32); 2]; 2]; 3] = [
        [
            [(193, 371, 0), (887, 109, 20_000)],
            [(251, 431, 16_384), (1019, 137, 28_673)],
        ],
        [
            [(139, 89, 6_144), (431, 217, 23_111)],
            [(173, 107, 3_211), (557, 173, 48_333)],
        ],
        [
            [(1103, 397, 21_555), (1877, 197, 5_111)],
            [(1301, 503, 41_991), (2137, 251, 28_913)],
        ],
    ];
    let seed = 0xA4D0_0001_u32
        .wrapping_add((bus as u32).wrapping_mul(0x0010_1001))
        .wrapping_add((channel as u32).wrapping_mul(0x0100_1101));
    let [first, second] = FREQUENCIES[bus][channel];
    let signal = (3 * fixture_chirp(at, first.0, first.1, first.2)
        + fixture_chirp(at, second.0, second.1, second.2))
        / 4;
    let amplitude = [14_000_i64, 22_000, 12_000][bus];
    let envelope = match bus {
        0 => CANONICAL_VOICE_WINDOWS
            .iter()
            .find(|&&(start, end)| start <= at && at < end)
            .map_or(0, |&(start, end)| {
                fixture_ramp(at - start).min(fixture_ramp(end - at))
            }),
        1 => 24_576 + fixture_smooth_noise(at, 2_107, seed ^ 0x68E3_1DA4) / 8,
        2 => CANONICAL_SFX_WINDOWS
            .iter()
            .find(|&&(start, end)| start <= at && at < end)
            .map_or(0, |&(start, end)| {
                let attack = fixture_ramp(at - start);
                // Retain a real end signature: a missing final 100 samples must fail.
                if end == CANONICAL_FIXTURE_SAMPLES {
                    attack
                } else {
                    attack.min(fixture_ramp(end - at))
                }
            }),
        _ => unreachable!("Writer admits exactly three fixture buses"),
    };
    (signal * amplitude * envelope / (32_768 * 32_768)) as i16
}

fn write_nonperiodic_bus_wav(
    path: &std::path::Path,
    bus: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::io::Write;
    if bus > 2 {
        return Err("Canonical fixture admits only voice, music, and SFX buses".into());
    }
    let pcm_bytes = CANONICAL_FIXTURE_SAMPLES * 4;
    let mut output = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    output.write_all(b"RIFF")?;
    output.write_all(&(pcm_bytes + 36).to_le_bytes())?;
    output.write_all(b"WAVEfmt ")?;
    output.write_all(&16_u32.to_le_bytes())?;
    output.write_all(&1_u16.to_le_bytes())?;
    output.write_all(&2_u16.to_le_bytes())?;
    output.write_all(&48_000_u32.to_le_bytes())?;
    output.write_all(&192_000_u32.to_le_bytes())?;
    output.write_all(&4_u16.to_le_bytes())?;
    output.write_all(&16_u16.to_le_bytes())?;
    output.write_all(b"data")?;
    output.write_all(&pcm_bytes.to_le_bytes())?;
    for at in 0..CANONICAL_FIXTURE_SAMPLES {
        for channel in 0..2 {
            output.write_all(&nonperiodic_bus_sample(bus, at, channel).to_le_bytes())?;
        }
    }
    output.sync_all()?;
    Ok(())
}

fn seed_canonical_original_mix(
    service: &StudioService,
    mut project: Project,
    database: &Path,
) -> Result<Project, Box<dyn std::error::Error>> {
    let files = [
        "canonical-voice.wav",
        "canonical-music.wav",
        "canonical-sfx.wav",
    ]
    .map(|suffix| database.with_extension(suffix));
    for (bus, path) in files.iter().enumerate() {
        write_nonperiodic_bus_wav(path, bus)?;
    }
    project = service
        .import_voice_file(
            project.id,
            &project.stamp(),
            "native-canonical-mix-import-voice",
            &files[0],
            VoiceImportMetadata {
                name: "canonical-original-numerical-voice.wav".into(),
                media_type: "audio/wav".into(),
                label: "Original numerical timing fixture; not speech or approved listening".into(),
            },
        )?
        .project;
    let track = project
        .audio
        .voice_tracks
        .last()
        .ok_or("Canonical fixture voice import produced no measured source")?
        .clone();
    if track.sample_rate_hz != 48_000
        || track.channels != 2
        || track.measured_duration != RationalTime::new(2, 1)?
    {
        return Err("Canonical mix WAV did not retain its exact measured two-second clock".into());
    }
    let mut bus_ids = [track.asset_id, Uuid::nil(), Uuid::nil()];
    for bus in 1..3 {
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                &format!("native-canonical-mix-import-bus-{bus}"),
                &files[bus],
                format!("Original numerical source bus {bus}"),
                "audio/wav".into(),
            )?
            .project;
        let imported = project
            .assets
            .last()
            .ok_or("Canonical mix source asset was not imported")?;
        if imported.content_sha256.as_deref()
            != Some(hex::encode(Sha256::digest(fs::read(&files[bus])?)).as_str())
        {
            return Err("Canonical source bus import changed its original WAV identity".into());
        }
        bus_ids[bus] = imported.id;
    }
    let mut profile = project
        .deliverables
        .iter()
        .find(|profile| profile.name == "Master 16:9")
        .ok_or("Canonical mix lost its existing master profile")?
        .clone();
    profile.voice_track_id = Some(track.id);
    profile.captions = false;
    profile.burn_in_captions = false;
    let profile_id = profile.id;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "native-canonical-mix-selected-profile",
            &Change::UpsertDeliverable { profile },
        )?
        .project;
    for (index, (start, end)) in CANONICAL_VOICE_WINDOWS.into_iter().enumerate() {
        // Authored fixture timing is declared explicitly. These intervals do
        // not claim measured ASR, intelligibility or a human approval event.
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("native-canonical-mix-numerical-window-{index}"),
                &Change::AddTranscriptSegment {
                    segment: TranscriptSegment {
                        id: Uuid::now_v7(),
                        voice_track_id: track.id,
                        start: RationalTime::new(i64::from(start), 48_000)?,
                        end: RationalTime::new(i64::from(end), 48_000)?,
                        text: format!("ORIGINAL NUMERICAL WINDOW {index}; NOT SPOKEN WORDS"),
                        speaker: None,
                        alignment: AlignmentEvidence::Manual,
                    },
                },
            )?
            .project;
    }
    let mut mix = project.audio.mix.clone();
    mix.voice_gain_db = 0.0;
    mix.music_gain_db = -12.0;
    mix.target_lufs = None;
    mix.target_true_peak_dbfs = None;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "native-canonical-mix-authored-global-gains",
            &Change::SetMixIntent { mix },
        )?
        .project;
    let recipe = DeliveryRecipe {
        schema: DeliveryRecipeSchema::V1,
        scene_intents: project
            .scenes
            .iter()
            .map(|scene| DeliverySceneIntent {
                scene_id: scene.id,
                role: Some(DeliveryNarrativeRole::Mechanism),
                archetype: Some(DeliveryArchetype::Statement),
            })
            .collect(),
        original_mix: Some(OriginalMixRecipe {
            music_asset_id: Some(bus_ids[1]),
            sfx_asset_id: Some(bus_ids[2]),
            sfx_gain_db: -3.0,
            duck_attenuation_db: 12.0,
            duck_attack_samples: 2_400,
            duck_release_samples: 4_800,
        }),
    };
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "native-canonical-mix-save-delivery-recipe",
            &Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: Some(recipe),
            },
        )?
        .project;
    // Later preparation must reopen the exact bytes through owner CAS. It
    // cannot accidentally continue using the transient import filenames.
    for path in files {
        fs::remove_file(path)?;
    }
    Ok(project)
}

fn seeded_database(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let mode = fixture_mode()?;
    let canonical_mix = mode == "original-mix-mp4";
    let service = StudioService::open(path)?;
    if !service.projects(2)?.is_empty() {
        return Err("Native multi-segment test DB must start empty".into());
    }
    let mut project = service.create_project("Source-bound two-segment actual MLT E2E")?;
    // Projects start with three legitimate aspect profiles. Keep all of them;
    // mutate the existing master instead of silently changing the fixture model.
    let mut profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.name == "Master 16:9")
        .ok_or("Default master deliverable is missing")?
        .clone();
    profile.width = 1280;
    profile.height = 720;
    // Keep the pinned Motion Canvas browser at its proven native 30 fps.
    // Existing modes retain their 32+1-frame cut. The canonical mix uses
    // the same 33 scenes with a 28-frame final scene: exactly 32+28 frames.
    profile.frame_rate = RationalTime::new(30, 1)?;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-multisegment-720p-30fps-profile",
            &Change::UpsertDeliverable { profile },
        )?
        .project;
    for number in 0..33 {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-multisegment-scene-{number:02}"),
                &Change::AddScene {
                    name: format!("Native source scene {number:02}"),
                    objective: format!("Pinned Motion Canvas scene {}", number + 1),
                    duration_seconds: 1,
                },
            )?
            .project;
        let scene_id = project.scenes.last().ok_or("new scene was missing")?.id;
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-multisegment-frame-duration-{number:02}"),
                &Change::SetSceneDuration {
                    scene_id,
                    duration: RationalTime::new(
                        if canonical_mix && number == 32 { 28 } else { 1 },
                        30,
                    )?,
                },
            )?
            .project;
        let mut tile = node(number);
        if canonical_mix {
            // Distinct source positions preserve scene-order observability;
            // this static cut does not claim animation/property coverage.
            tile.x = 96.0 + number as f64 * 29.0;
            tile.y = 250.0 + (number % 5) as f64 * 35.0;
        }
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-multisegment-object-{number:02}"),
                &Change::AddCanvasNode {
                    scene_id,
                    node: tile,
                },
            )?
            .project;
    }
    if matches!(mode.as_str(), "final-mp4" | "captioned-mp4") {
        let wav = path.with_extension("synthetic-owner-voice.wav");
        write_synthetic_stereo_wav(&wav)?;
        project = service
            .import_voice_file(
                project.id,
                &RevisionStamp::from(&project),
                "native-multisegment-measured-voice",
                &wav,
                VoiceImportMetadata {
                    name: "synthetic-owner-voice.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "CI source tone, no creative approval".into(),
                },
            )?
            .project;
        let track = project
            .audio
            .voice_tracks
            .last()
            .ok_or("Actual imported WAV did not produce a measured voice track")?;
        if track.sample_rate_hz != 48_000
            || track.channels != 2
            || track.measured_duration != RationalTime::new(11, 10)?
        {
            return Err("Native 33-frame test voice lost exact measured duration".into());
        }
        let track_id = track.id;
        let mut profile = project
            .deliverables
            .iter()
            .find(|candidate| candidate.name == "Master 16:9")
            .ok_or("Master profile lost after voice import")?
            .clone();
        profile.voice_track_id = Some(track_id);
        let captioned = mode == "captioned-mp4";
        if captioned {
            profile.captions = true;
            profile.burn_in_captions = true;
        }
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-multisegment-selected-measured-voice",
                &Change::UpsertDeliverable { profile },
            )?
            .project;
        if captioned {
            // Native 32+1-frame segmentation means captions must be contained
            // in an exact single 1/30s shot. No invented scene crossing,
            // silent frame rounding, voice synthesis or audio mastering.
            for (index, (start, end, text)) in [
                (
                    RationalTime::new(15, 30)?,
                    RationalTime::new(16, 30)?,
                    "SOURCE CAPTION 15",
                ),
                (
                    RationalTime::new(32, 30)?,
                    RationalTime::new(33, 30)?,
                    "SOURCE CAPTION 32",
                ),
            ]
            .into_iter()
            .enumerate()
            {
                project = service
                    .apply(
                        project.id,
                        &RevisionStamp::from(&project),
                        &format!("native-captioned-master-cue-{index}"),
                        &Change::AddTranscriptSegment {
                            segment: TranscriptSegment {
                                id: Uuid::now_v7(),
                                voice_track_id: track_id,
                                start,
                                end,
                                text: text.into(),
                                speaker: None,
                                alignment: AlignmentEvidence::Manual,
                            },
                        },
                    )?
                    .project;
            }
        }
    }
    if canonical_mix {
        project = seed_canonical_original_mix(&service, project, path)?;
    }
    let profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.name == "Master 16:9")
        .ok_or("Master deliverable was lost after source seeding")?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id":project.id,"generation":project.generation,
            "revision":project.revision,"resource":project.resource_key(),
            "width":profile.width,"height":profile.height,
            "fps_num":profile.frame_rate.num,"fps_den":profile.frame_rate.den,
            "scene_count":project.scenes.len(),
            "deliverable_count":project.deliverables.len(),
            "master_profile_id":profile.id,
            "fixture_mode":mode,
            "fixture_frame_count":if canonical_mix { 60 } else { 33 },
            "fixture_audio_sample_frames":if canonical_mix { 96_000 } else { 52_800 },
            "fixture_speech_or_human_approval":false,
        }))?
    );
    Ok(())
}
async fn assemble(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let mode = fixture_mode()?;
    let canonical_mix = mode == "original-mix-mp4";
    let expected_frames = if canonical_mix { 60 } else { 33 };
    let expected_last_segment = if canonical_mix { 28 } else { 1 };
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("Expected one application-owned source project".into());
    }
    let project = projects.into_iter().next().ok_or("project absent")?;
    if project.scenes.len() != 33 || project.deliverables.len() != 3 {
        return Err(format!(
            "Native test project shape changed: expected 33 scenes and three default deliverables; found {} scenes and {} deliverables",
            project.scenes.len(),
            project.deliverables.len(),
        ).into());
    }
    let profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.name == "Master 16:9")
        .ok_or("Native test master profile was removed")?;
    if (
        profile.width,
        profile.height,
        profile.frame_rate.num,
        profile.frame_rate.den,
    ) != (1280, 720, 30, 1)
    {
        return Err("Native multi-segment project profile changed".into());
    }
    // Reopened SQLite must preserve every exact source scene boundary.
    for (index, scene) in project.scenes.iter().enumerate() {
        let start = RationalTime::new(index as i64, 30)?;
        let duration = if canonical_mix && index == 32 { 28 } else { 1 };
        if scene.start != start || scene.duration != RationalTime::new(duration, 30)? {
            return Err("Native two-segment fixture lost its exact 30 fps timebase".into());
        }
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("Semwright owner connection belongs to a foreign resource".into());
    }
    eprintln!("mlt-e2e-stage: source-loaded");
    let final_master_mode = matches!(mode.as_str(), "final-mp4" | "captioned-mp4");
    let captioned_mode = mode == "captioned-mp4";
    let staged_audio = if final_master_mode {
        let track_id = profile
            .voice_track_id
            .ok_or("Final multisegment test lacks an explicitly selected voice take")?;
        let verified =
            service.verified_master_voice(project.id, &RevisionStamp::from(&project), track_id)?;
        if verified.sha256.len() != 64 {
            return Err("Actual measured voice digest is malformed".into());
        }
        let name = format!("mw-ci-source-{}.wav", &verified.sha256[..16]);
        let bytes = fs::read(&verified.path)?;
        if bytes.len() as u64 != verified.size_bytes
            || hex::encode(Sha256::digest(&bytes)) != verified.sha256
        {
            return Err("Actual measured voice CAS bytes did not match trusted source".into());
        }
        let mut staged = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(connection.output_root.join(&name))?;
        staged.write_all(&bytes)?;
        staged.sync_all()?;
        Some(MltAudioArtifact {
            relative_path: name,
            sha256: verified.sha256,
            sample_rate: 48_000,
            channels: 2,
        })
    } else {
        None
    };
    eprintln!("mlt-e2e-stage: audio-staged");
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let options = if canonical_mix {
        canonical_original_mix_film_options(&project, profile.id)?
    } else {
        FilmBuildOptions {
            frame_rate: Rate::new(30, 1)?,
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        }
    };
    let staged_audio = if canonical_mix {
        Some(
            coordinator
                .prepare_native_canonical_original_mix_av_audio(
                    project.id,
                    &project.stamp(),
                    profile.id,
                    expected_frames,
                )?
                .audio,
        )
    } else {
        staged_audio
    };
    if captioned_mode {
        // Recompute the canonical native Film from the reopened owner source,
        // rather than inferring captions merely from a saved checkbox.
        let projected = build_motion_canvas_segments(&project, profile.id, &options)?;
        let cue_count: usize = projected
            .iter()
            .map(|segment| segment.film.cues.cues.len())
            .sum();
        let shot_count: usize = projected
            .iter()
            .flat_map(|segment| &segment.film.sequences)
            .flat_map(|sequence| &sequence.beats)
            .flat_map(|beat| &beat.shots)
            .map(|shot| shot.captions.len())
            .sum();
        if projected.len() != 2 || cue_count != 2 || shot_count != 2 {
            return Err("Captioned source was not two exact Semwright native Film cues".into());
        }
    }
    eprintln!("mlt-e2e-stage: motion-render-start");
    let render = coordinator
        .render_motion_canvas_segments(
            project.id,
            &RevisionStamp::from(&project),
            if canonical_mix {
                "real-native-canonical-mix-cut-60"
            } else {
                "real-native-cut-33-scenes"
            },
            profile.id,
            &options,
        )
        .await?;
    eprintln!("mlt-e2e-stage: motion-render-complete");
    if render.segments.len() != 2
        || render.segments[0].frame_count != 32
        || render.segments[1].frame_count != expected_last_segment
    {
        return Err(
            "Actual native Film did not preserve the declared two-segment partition".into(),
        );
    }
    let video_only = mode != "pcm";
    eprintln!("mlt-e2e-stage: semantic-mlt-start");
    let evidence = if video_only {
        coordinator
            .assemble_native_mlt_video_only_timeline(
                project.id,
                &RevisionStamp::from(&project),
                if canonical_mix {
                    "real-mlt-canonical-mix-video-60"
                } else {
                    "real-mlt-video-only-33"
                },
                profile.id,
                &options,
                &render,
            )
            .await?
    } else {
        coordinator
            .assemble_native_mlt_video_timeline(
                project.id,
                &RevisionStamp::from(&project),
                "real-mlt-timeline-33",
                profile.id,
                &options,
                &render,
            )
            .await?
    };
    eprintln!("mlt-e2e-stage: semantic-mlt-complete");
    let profile_expected = if video_only {
        "lossless-video-only"
    } else {
        "lossless"
    };
    let scope_expected = if video_only {
        "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
    } else {
        "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
    };
    if evidence.frame_count != expected_frames
        || evidence.transport_pcm_audio == video_only
        || evidence.native_render_profile != profile_expected
        || evidence.provider_project_cleanup != "not_requested_requires_foreground_broker_consent"
        || (video_only && evidence.native_frame_count_observed != Some(expected_frames))
        || evidence
            .native_frame_count_observed
            .is_some_and(|observed| observed != expected_frames)
        || evidence.source.verified_video_segments.len() != 2
        || evidence.evidence_scope != scope_expected
    {
        return Err(
            "Native MLT timeline did not return the two-segment lossless intermediary".into(),
        );
    }
    let body = serde_json::to_vec_pretty(&evidence)?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(evidence_path)?;
    output.write_all(&body)?;
    output.sync_all()?;
    if !fs::metadata(evidence_path)?.is_file() {
        return Err("Native MLT evidence was not saved".into());
    }
    eprintln!("mlt-e2e-stage: final-mux-start");
    let av_master = if let Some(audio) = staged_audio.as_ref().filter(|_| !canonical_mix) {
        let evidence = coordinator
            .assemble_native_mlt_multisegment_av_master(
                project.id,
                &RevisionStamp::from(&project),
                MltMultisegmentAvMasterRequest {
                    request_id: "real-mlt-multisegment-av-33",
                    deliverable_id: profile.id,
                    options: &options,
                    rendered: &render,
                    visual: &evidence,
                    audio,
                },
            )
            .await?;
        let expected_master_scope = if captioned_mode {
            "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
        } else {
            "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
        };
        if evidence.frame_count != 33
            || evidence.measured_voice.audio_pcm_frames != 52_800
            || evidence.measured_voice.caption_cue_count != captioned_mode.then_some(2)
            || evidence.evidence_scope != expected_master_scope
        {
            return Err(
                "Actual native MP4 did not certify the source cut and measured audio".into(),
            );
        }
        let av_path = evidence_path.with_file_name("native-multisegment-av-master-evidence.json");
        let mut save = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(av_path)?;
        save.write_all(&serde_json::to_vec_pretty(&evidence)?)?;
        save.sync_all()?;
        Some(evidence)
    } else {
        None
    };
    let original_mix_master = if canonical_mix {
        let audio = staged_audio
            .as_ref()
            .ok_or("Canonical source mix WAV was not staged")?;
        let mixed = coordinator
            .assemble_native_mlt_multisegment_original_mix_master(
                project.id,
                &project.stamp(),
                MltMultisegmentOriginalMixMasterRequest {
                    request_id: "real-mlt-canonical-original-mix-60",
                    deliverable_id: profile.id,
                    rendered: &render,
                    visual: &evidence,
                    audio,
                },
            )
            .await?;
        if mixed.frame_count != 60
            || mixed.measured_voice.audio_pcm_frames != 96_000
            || mixed.measured_voice.caption_cue_count.is_some()
            || mixed.original_mix.original_mix.frames != 96_000
            || mixed.original_mix.original_mix.measured_music_duck_windows != 2
            || mixed.original_mix.staged_wav_sha256 != audio.sha256
            || mixed.canonical_dependency.profile_id != profile.id
            || mixed.canonical_dependency.project_generation != project.generation
            || mixed.canonical_dependency.project_revision != project.revision
            || mixed.evidence_scope
                != "actual-native-canonical-original-mix-h264-aac-source-bound-not-mastered-or-human-approved"
        {
            return Err(
                "Canonical native original mix failed its exact saved-source receipt contract"
                    .into(),
            );
        }
        let mut save = OpenOptions::new().write(true).create_new(true).open(
            evidence_path.with_file_name("native-multisegment-original-mix-master-evidence.json"),
        )?;
        save.write_all(&serde_json::to_vec_pretty(&mixed)?)?;
        save.sync_all()?;
        Some(mixed)
    } else {
        None
    };
    // Cross-check the real native create(s) against the *persisted*
    // application receipt stream, not a synthetic mock or the provider's
    // in-memory project list. An owner-forward MLT close remains prohibited.
    let reopened = StudioService::open(database)?;
    let lifecycle = reopened.mlt_cleanup_inventory_snapshot(
        project.id,
        project.generation,
        project.revision,
    )?;
    if lifecycle.confirmed_native_creations_current_revision == 0
        || lifecycle.confirmed_native_creations_current_revision > 4
        || lifecycle.unconfirmed_or_invalid_creation_requests != 0
        || lifecycle.observed_close_request_identities != 0
        || lifecycle.source_receipts_scanned == 0
        || !lifecycle.complete_source_receipt_snapshot
        || lifecycle.native_provider_liveness_verified
        || lifecycle.foreground_cleanup_consent_obtained
        || lifecycle.verified_native_project_cleanup
        || lifecycle.automatic_destructive_cleanup_allowed
    {
        return Err(
            "Real native MLT receipts did not establish a bounded, honest project lifecycle".into(),
        );
    }
    let lifecycle_path = evidence_path.with_file_name("native-mlt-lifetime-inventory.json");
    let lifecycle_bytes = serde_json::to_vec_pretty(&lifecycle)?;
    let lifecycle_sha256 = hex::encode(Sha256::digest(&lifecycle_bytes));
    let mut lifecycle_output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lifecycle_path)?;
    lifecycle_output.write_all(&lifecycle_bytes)?;
    lifecycle_output.sync_all()?;
    eprintln!("mlt-e2e-stage: native-lifetime-receipt-verified");

    let master = av_master
        .as_ref()
        .map(|e| &e.master)
        .or_else(|| original_mix_master.as_ref().map(|e| &e.master));
    let master_audio_sha256 = av_master
        .as_ref()
        .map(|e| &e.measured_voice.audio_sha256)
        .or_else(|| original_mix_master.as_ref().map(|e| &e.staged_audio.sha256));
    let master_scope = av_master
        .as_ref()
        .map(|e| &e.evidence_scope)
        .or_else(|| original_mix_master.as_ref().map(|e| &e.evidence_scope));

    println!(
        "{}",
        serde_json::to_string(&json!({
            "native_mlt_multisegment_e2e":"PASS",
            "native_mlt_lifetime_inventory_sha256": lifecycle_sha256,
            "native_mlt_lifetime_receipts_scanned": lifecycle.source_receipts_scanned,
            "native_mlt_lifetime_confirmed_current_creations": lifecycle.confirmed_native_creations_current_revision,
            "native_mlt_lifetime_close_requests_observed": lifecycle.observed_close_request_identities,
            "native_mlt_cleanup_verified": false,
            "project_id":project.id,
            "frame_count":evidence.frame_count,
            "segment_count":evidence.source.verified_video_segments.len(),
            "artifact_path":evidence.artifact_path,
            "artifact_sha256":evidence.artifact_sha256,
            "artifact_bytes":evidence.artifact_bytes,
            "transport_pcm_audio":evidence.transport_pcm_audio,
            "native_render_profile":evidence.native_render_profile,
            "provider_project_cleanup":evidence.provider_project_cleanup,
            "native_frame_count_observed":evidence.native_frame_count_observed,
            "evidence_scope":evidence.evidence_scope,
            "master_artifact_path":master.and_then(|e| e.pointer("/artifact/path")),
            "master_artifact_sha256":master.and_then(|e| e.pointer("/artifact/sha256")),
            "master_artifact_bytes":master.and_then(|e| e.pointer("/artifact/bytes")),
            "master_audio_sha256":master_audio_sha256,
            "master_decoded_audio_sha256":master.and_then(|e| e.pointer("/decoded_audio/sha256")),
            "master_evidence_scope":master_scope,
            "native_captioned_master_requested":captioned_mode,
            "source_bound_caption_cue_count":av_master.as_ref()
                .and_then(|e| e.measured_voice.caption_cue_count),
            "fixture_mode":mode,
            "canonical_original_mix_requested":canonical_mix,
            "canonical_mix_source_plan_sha256":original_mix_master.as_ref()
                .map(|e| &e.original_mix.original_mix.source_plan_sha256),
            "canonical_mix_decoded_source_pcm_sha256":original_mix_master.as_ref()
                .map(|e| &e.original_mix.original_mix.rendered_pcm_sha256),
            "canonical_dependency":original_mix_master.as_ref().map(|e| &e.canonical_dependency),
            "fixture_speech_or_human_approval":false,
        }))?
    );
    Ok(())
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let action = args.next().ok_or("Missing command")?;
    let database = arg(&mut args);
    match action.to_str().ok_or("Non UTF-8 action")? {
        "seed" => seeded_database(&database),
        "assemble" => {
            let connection = arg(&mut args);
            let output = arg(&mut args);
            assemble(&database, &connection, &output).await
        }
        _ => Err("Unknown native MLT E2E command".into()),
    }
}

#[cfg(test)]
mod fixture_tests {
    use super::*;

    #[test]
    fn integer_fixture_wavs_match_independently_calibrated_source_bytes() {
        let root = tempfile::Builder::new()
            .prefix("native-mix-signal-")
            .tempdir_in(env!("CARGO_MANIFEST_DIR"))
            .unwrap();
        for (bus, (wav_sha256, pcm_sha256)) in [
            (
                "92600baa4f59af065cf2d4132cd84943313e6c842392c226efe8eac00e176c49",
                "741d45438a68fa44d875398653c588f02a3a2674638070b1dc0149795a37f0a8",
            ),
            (
                "708d8953769f4a92228df3417487e04344c6a3a3a07f89e4bc36e3f9b1031ad1",
                "3ea758e4872cd184d0f3ffb60e535e4f72d1f416867a396682bdf6a0de4c7e26",
            ),
            (
                "efe99c94f674aa38fb094c9d37ba00ba2baca0c5b87cdc14449d79dbbca74a11",
                "b9c647103eee2fd4201da8be2da5d5b7bbe4ec2b77aa030d225e1f5ded1c4026",
            ),
        ]
        .into_iter()
        .enumerate()
        {
            let path = root.path().join(format!("source-bus-{bus}.wav"));
            write_nonperiodic_bus_wav(&path, bus).unwrap();
            let bytes = fs::read(&path).unwrap();
            assert_eq!(bytes.len(), 384_044);
            assert_eq!(hex::encode(Sha256::digest(&bytes)), wav_sha256);
            assert_eq!(hex::encode(Sha256::digest(&bytes[44..])), pcm_sha256);
            assert!(write_nonperiodic_bus_wav(&path, bus).is_err());
        }
    }
}
