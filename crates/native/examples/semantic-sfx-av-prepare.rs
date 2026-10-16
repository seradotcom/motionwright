//! CI-only: attach two original SFX sources to the *same* real project that
//! Motionwright will render, then prepare an exact 48 kHz stereo PCM master.
//! The pinned Semwright Driver Host performs the later H.264/AAC mux.
use motionwright_domain::{Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent};
use motionwright_native::{
    production::prepare_semantic_sfx_av_audio,
    sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx},
};
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::Path};
use uuid::Uuid;

const FPS: u64 = 30;
const SHORT_FRAMES: u64 = 60;
const COMPOSITION_FRAMES: u64 = 540;
const AUDIO_RATE: usize = 48_000;
const SFX_FRAMES: u16 = 24;

fn original_stereo_wav(left_hz: f64, right_hz: f64) -> Vec<u8> {
    let total = usize::from(SFX_FRAMES) * 1600;
    let data_bytes = u32::try_from(total * 4).unwrap();
    let mut bytes = Vec::with_capacity(44 + data_bytes as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(data_bytes + 36).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&(AUDIO_RATE as u32).to_le_bytes());
    bytes.extend_from_slice(&((AUDIO_RATE * 4) as u32).to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for index in 0..total {
        let time = index as f64 / AUDIO_RATE as f64;
        for hz in [left_hz, right_hz] {
            let amplitude = (12_000.0 * (time * hz * std::f64::consts::TAU).sin()).round() as i16;
            bytes.extend_from_slice(&amplitude.to_le_bytes());
        }
    }
    bytes
}

fn original_file(root: &Path, name: &str, bytes: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
    let path = root.join(name);
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

struct StemSpec {
    name: &'static str,
    event: SfxEvent,
    offset: i16,
    gain_milli_db: i32,
    tones: (f64, f64),
    scene_index: usize,
}

fn attach(
    service: &StudioService,
    project: &mut Project,
    output: &Path,
    spec: StemSpec,
) -> Result<VerifiedSfxWav, Box<dyn std::error::Error>> {
    let StemSpec {
        name,
        event,
        offset,
        gain_milli_db,
        tones,
        scene_index,
    } = spec;
    let original = original_stereo_wav(tones.0, tones.1);
    original_file(output, name, &original)?;
    let imported = service.import_asset_file(
        project.id,
        &project.stamp(),
        &format!("original-asset-{name}"),
        output.join(name),
        format!("Original owner synthetic {name}"),
        "audio/wav".into(),
    )?;
    *project = imported.project;
    let asset = project
        .assets
        .last()
        .ok_or("imported asset missing")?
        .clone();
    let digest = asset.content_sha256.ok_or("imported SHA-256 missing")?;
    let cue = SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id: Uuid::new_v4(),
        scene_id: project.scenes[scene_index].id,
        event,
        anchor: SfxAnchor::SceneStart,
        offset_frames: offset,
        source_asset_id: asset.id,
        source_sha256: digest.clone(),
        source_frames: SFX_FRAMES,
        gain_milli_db,
        attack_frames: 3,
        release_frames: 6,
        original_source_attestation: "Original generated PCM; not external media rights".into(),
    };
    *project = service
        .apply(
            project.id,
            &project.stamp(),
            &format!("original-cue-{name}"),
            &Change::UpsertSemanticSfx { cue },
        )?
        .project;
    let verified =
        service.verified_semantic_sfx_wav(project.id, &project.stamp(), asset.id, &digest)?;
    if verified != original || hex::encode(Sha256::digest(&verified)) != digest {
        return Err("original imported PCM differs from owner CAS".into());
    }
    Ok(VerifiedSfxWav {
        asset_id: asset.id,
        source_sha256: digest,
        bytes: verified,
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("source-bound native SFX AV fixture requires disposable GitHub CI".into());
    }
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.len() != 2 {
        return Err(
            "usage: semantic-sfx-av-prepare EXISTING_OWNER_DATABASE OWNER_OUTPUT_DIRECTORY".into(),
        );
    }
    let database = Path::new(&arguments[0]);
    let output = Path::new(&arguments[1]);
    if !output.is_dir()
        || fs::symlink_metadata(output)?.file_type().is_symlink()
        || output.canonicalize()? != output
    {
        return Err("SFX output must be the existing canonical owner output directory".into());
    }
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("semantic SFX native AV requires exactly one existing owner project".into());
    }
    let mut project = projects.into_iter().next().unwrap();
    let composition =
        std::env::var("MOTIONWRIGHT_E2E_FIXTURE").as_deref() == Ok("semantic-sfx-composition-av");
    let master_frames = if composition {
        COMPOSITION_FRAMES
    } else {
        SHORT_FRAMES
    };
    let expected_scene_duration = if composition { 6 } else { 2 };
    if project.scenes.len() != if composition { 3 } else { 1 }
        || project.scenes.iter().enumerate().any(|(index, scene)| {
            scene.start
                != motionwright_domain::RationalTime::new(
                    (index as i64) * expected_scene_duration,
                    1,
                )
                .unwrap_or(motionwright_domain::RationalTime::ZERO)
                || scene.duration
                    != motionwright_domain::RationalTime::new(expected_scene_duration, 1)
                        .unwrap_or(motionwright_domain::RationalTime::ZERO)
                || !scene.beats.is_empty()
        })
        || !project.audio.sfx_cues.is_empty()
        || project.deliverables.is_empty()
        || project.deliverables[0].frame_rate.num != FPS as i64
        || project.deliverables[0].frame_rate.den != 1
        || project.deliverables[0].audio_sample_rate_hz != 48_000
    {
        return Err(
            "semantic SFX AV fixture needs the exact original 60- or 540-frame authored project"
                .into(),
        );
    }
    let original_project_id = project.id;
    let original_generation = project.generation;
    let initial_revision = project.revision;
    let first = attach(
        &service,
        &mut project,
        output,
        StemSpec {
            name: "original-focus-sfx.wav",
            event: SfxEvent::UiFocus,
            offset: 6,
            gain_milli_db: -12_000,
            tones: (440.0, 660.0),
            scene_index: 0,
        },
    )?;
    let second = attach(
        &service,
        &mut project,
        output,
        StemSpec {
            name: "original-confirm-sfx.wav",
            event: SfxEvent::Confirmation,
            offset: 20,
            gain_milli_db: -12_000,
            tones: (740.0, 980.0),
            scene_index: if composition { 2 } else { 0 },
        },
    )?;
    if project.id != original_project_id
        || project.generation != original_generation
        || project.revision <= initial_revision
        || project.audio.sfx_cues.len() != 2
    {
        return Err(
            "semantic SFX fixture changed owner identity or did not persist two cues".into(),
        );
    }
    let schedule = project.validated_sfx_schedule()?;
    if schedule.len() != 2
        || schedule[0].absolute_start_frame != 6
        || schedule[0].absolute_end_frame != 30
        || schedule[1].absolute_start_frame != if composition { 380 } else { 20 }
        || schedule[1].absolute_end_frame != if composition { 404 } else { 44 }
    {
        return Err(
            "SFX native original sample schedule differs from authored frame anchors".into(),
        );
    }
    let sources = [first, second];
    let mixed = mix_source_bound_semantic_sfx(
        &project,
        project.deliverables[0].id,
        &sources,
        master_frames,
    )?;
    if mixed.rendered_frames != master_frames
        || mixed.wav_bytes.len() != 44 + master_frames as usize * 1600 * 4
        || mixed.sample_peak_pcm16 == 0
        || mixed.original_sources != 2
    {
        return Err("native original SFX mix is incomplete or silent".into());
    }
    // The independent numeric original-source control is now compared with
    // the first-class owner-native preparation, not used as the mux source.
    let prepared = prepare_semantic_sfx_av_audio(
        &service,
        project.id,
        &project.stamp(),
        project.deliverables[0].id,
        master_frames,
        output,
    )?;
    let prepared_bytes = fs::read(output.join(&prepared.audio.relative_path))?;
    if prepared.proof.exact_pcm_sha256 != mixed.output_sha256
        || prepared.audio.sha256 != mixed.output_sha256
        || prepared.proof.revision != project.revision
        || prepared_bytes != mixed.wav_bytes
    {
        return Err("First-class source-bound native SFX preparation changed original PCM".into());
    }
    // Portable public synthetic evidence keeps its fixed review filename.
    // The Native SDK mux consumes the *source-digest-addressed* private WAV.
    original_file(output, "semantic-sfx-master.wav", &prepared_bytes)?;
    let persisted = service.project(project.id)?;
    if persisted != project {
        return Err("persisted owner project differs from the actual SFX authoring state".into());
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "schema":"motionwright.semantic-sfx-av-prepared/1",
            "scope":"real imported stereo WAV sources and sample-exact owner-native SFX mixer; native AV mux pending",
            "project_id":project.id,
            "generation":project.generation,
            "revision":project.revision,
            "scene_id":project.scenes[0].id,
            "scene_ids":project.scenes.iter().map(|s|s.id).collect::<Vec<_>>(),
            "deliverable_id":project.deliverables[0].id,
            "frames":master_frames,
            "fps":"30/1",
            "sample_rate":48000,
            "channels":2,
            "sample_count":master_frames * 1600,
            "first_source_sha256":sources[0].source_sha256,
            "second_source_sha256":sources[1].source_sha256,
            "source_cue_ids": project.audio.sfx_cues.iter().map(|c|c.id).collect::<Vec<_>>(),
            "scheduled_sfx":schedule,
            "audio_relative":prepared.audio.relative_path,
            "audio_sha256":prepared.audio.sha256,
            "native_preparation_verified": true,
            "native_preparation_revision": prepared.proof.revision,
            "original_source_count":prepared.proof.original_asset_sha256.len(),
            "sample_peak_pcm16":mixed.sample_peak_pcm16,
            "creative_approval":"REQUIRED",
            "legal_rights":"NOT_PERFORMED"
        }))?
    );
    Ok(())
}
