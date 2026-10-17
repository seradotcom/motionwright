//! CI-only: attach two original SFX sources to the *same* real project that
//! Motionwright will render, then prepare an exact 48 kHz stereo PCM master.
//! The pinned Semwright Driver Host performs the later H.264/AAC mux.
use motionwright_domain::{Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent};
use motionwright_native::production::prepare_semantic_sfx_av_audio;
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fs, io::Write, path::Path};
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

/// Observe the physically written canonical WAV container and its PCM payload.
/// This does not reconstruct the mix, compare AAC, measure LUFS or true peak.
fn inspect_prepared_pcm(
    bytes: &[u8],
    master_frames: u64,
) -> Result<(String, u16), Box<dyn std::error::Error>> {
    let data_len = usize::try_from(master_frames)?
        .checked_mul(1600 * 4)
        .ok_or("SFX PCM byte count overflow")?;
    let wav_len = data_len
        .checked_add(44)
        .ok_or("SFX WAV byte count overflow")?;
    if bytes.len() != wav_len
        || &bytes[0..4] != b"RIFF"
        || u32::from_le_bytes(bytes[4..8].try_into()?) != u32::try_from(wav_len - 8)?
        || &bytes[8..16] != b"WAVEfmt "
        || u32::from_le_bytes(bytes[16..20].try_into()?) != 16
        || u16::from_le_bytes(bytes[20..22].try_into()?) != 1
        || u16::from_le_bytes(bytes[22..24].try_into()?) != 2
        || u32::from_le_bytes(bytes[24..28].try_into()?) != 48_000
        || u32::from_le_bytes(bytes[28..32].try_into()?) != 192_000
        || u16::from_le_bytes(bytes[32..34].try_into()?) != 4
        || u16::from_le_bytes(bytes[34..36].try_into()?) != 16
        || &bytes[36..40] != b"data"
        || u32::from_le_bytes(bytes[40..44].try_into()?) != u32::try_from(data_len)?
    {
        return Err("prepared SFX WAV is not its exact 48 kHz stereo PCM16 container".into());
    }
    let pcm = &bytes[44..];
    let peak = pcm
        .as_chunks::<2>()
        .0
        .iter()
        .map(|sample| i16::from_le_bytes(*sample).unsigned_abs())
        .max()
        .unwrap_or(0);
    Ok((hex::encode(Sha256::digest(pcm)), peak))
}

struct ImportedOriginal {
    asset_id: Uuid,
    source_sha256: String,
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
) -> Result<ImportedOriginal, Box<dyn std::error::Error>> {
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
    Ok(ImportedOriginal {
        asset_id: asset.id,
        source_sha256: digest,
    })
}

/// Source preparation only. Unit tests can exercise the same CAS/preparer path
/// without creating a Broker, a connection or a native production receipt.
fn prepare_existing_fixture(
    database: &Path,
    output: &Path,
    composition: bool,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    if !database.is_file() || fs::symlink_metadata(database)?.file_type().is_symlink() {
        return Err("SFX source preparation requires the existing owner database".into());
    }
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
    let mut project = projects
        .into_iter()
        .next()
        .expect("one owner project checked");
    project.validate()?;
    let before_receipts = service.production_receipt_watermark(project.id)?;
    if before_receipts.count != 0 || before_receipts.latest_id.is_some() {
        return Err("SFX source fixture must precede every native dispatch".into());
    }
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
        || !project.audio.voice_tracks.is_empty()
        || project.audio.active_voice_track_id.is_some()
        || !project.audio.transcript.is_empty()
        || !project.audio.cues.is_empty()
        || project.audio.mix.target_lufs.is_some()
        || project.audio.mix.target_true_peak_dbfs.is_some()
        || project.deliverables.is_empty()
        || project.deliverables[0].frame_rate.num != FPS as i64
        || project.deliverables[0].frame_rate.den != 1
        || project.deliverables[0].audio_sample_rate_hz != 48_000
        || !project.deliverables[0].included_scene_ids.is_empty()
        || project.deliverables[0].burn_in_captions
    {
        return Err(
            "semantic SFX AV fixture needs the exact original 60- or 540-frame authored project"
                .into(),
        );
    }
    // Never overwrite or silently resume a previously prepared source fixture.
    for name in [
        "original-focus-sfx.wav",
        "original-confirm-sfx.wav",
        "semantic-sfx-master.wav",
        "semantic-sfx-project.json",
        "semantic-sfx.motionwright",
        "semantic-sfx-reopened",
    ] {
        match fs::symlink_metadata(output.join(name)) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            _ => return Err("SFX source output already exists or cannot be inspected".into()),
        }
    }
    let visual_scenes = project.scenes.clone();
    let production_design = project.production_design.clone();
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
        || project.scenes != visual_scenes
        || project.production_design != production_design
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
    // Use the one current owner-native preparation path. Its post-write source
    // admission deliberately uses the same canonical mixer internally; that is
    // not an independent audio oracle and is not an AAC readback.
    let prepared = prepare_semantic_sfx_av_audio(
        &service,
        project.id,
        &project.stamp(),
        project.deliverables[0].id,
        master_frames,
        output,
    )?;
    let prepared_path = output.join(&prepared.audio.relative_path);
    let metadata = fs::symlink_metadata(&prepared_path)?;
    let expected_bytes = 44 + master_frames * 1600 * 4;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() != expected_bytes
    {
        return Err("prepared SFX WAV is absent or exceeds its exact physical clock".into());
    }
    let prepared_bytes = fs::read(&prepared_path)?;
    let actual_wav_sha256 = hex::encode(Sha256::digest(&prepared_bytes));
    let (pcm_payload_sha256, observed_sample_peak) =
        inspect_prepared_pcm(&prepared_bytes, master_frames)?;
    let expected_sources = sources
        .iter()
        .map(|source| source.source_sha256.as_str())
        .collect::<BTreeSet<_>>();
    let prepared_sources = prepared
        .proof
        .original_asset_sha256
        .iter()
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    if prepared.proof.project_id != project.id
        || prepared.proof.generation != project.generation
        || prepared.proof.revision != project.revision
        || prepared.proof.deliverable_id != project.deliverables[0].id
        || prepared.proof.master_frames != master_frames
        || prepared.audio.sample_rate != 48_000
        || prepared.audio.channels != 2
        || prepared.audio.sha256 != actual_wav_sha256
        // This legacy proof field hashes the COMPLETE WAV, not only PCM.
        || prepared.proof.exact_pcm_sha256 != actual_wav_sha256
        || prepared.proof.sample_peak_pcm16 != observed_sample_peak
        || observed_sample_peak == 0
        || sources[0].asset_id == sources[1].asset_id
        || expected_sources.len() != 2
        || prepared.proof.original_asset_sha256.len() != 2
        || prepared_sources != expected_sources
    {
        return Err(
            "First-class SFX preparation differs from its source/stamp/physical WAV".into(),
        );
    }
    // Portable public synthetic evidence keeps its fixed review filename.
    // The Native SDK mux consumes the *source-digest-addressed* private WAV.
    original_file(output, "semantic-sfx-master.wav", &prepared_bytes)?;

    // The seed's earlier composition bundle predates these two SFX assets.
    // Export the current source through the existing Store bundle API, then
    // reopen it in an explicitly isolated child of the same output directory.
    let source_project_bytes = serde_json::to_vec_pretty(&project)?;
    original_file(output, "semantic-sfx-project.json", &source_project_bytes)?;
    let source_bundle = output.join("semantic-sfx.motionwright");
    service.export_project_bundle(project.id, &source_bundle)?;
    let bundle_manifest_bytes = fs::read(source_bundle.join("manifest.json"))?;
    let reopened_root = output.join("semantic-sfx-reopened");
    fs::create_dir(&reopened_root)?;
    let reopened_service = StudioService::open(reopened_root.join("studio.sqlite3"))?;
    let reopened = reopened_service.import_project_bundle(&source_bundle)?;
    // Comparison only: import deliberately rotates these two fields. This
    // clone is never saved, dispatched or used as an authority stamp.
    let mut reopened_semantics = reopened.clone();
    reopened_semantics.generation = project.generation;
    reopened_semantics.updated_at = project.updated_at;
    if reopened.id != project.id
        || reopened.generation == project.generation
        || reopened.revision != project.revision
        || reopened_semantics != project
        || reopened_service
            .production_receipt_watermark(reopened.id)?
            .count
            != 0
    {
        return Err(
            "reopened SFX source lost identity, parameters, rights or expected generation rotation"
                .into(),
        );
    }
    for source in &sources {
        let original_bytes = service.verified_semantic_sfx_wav(
            project.id,
            &project.stamp(),
            source.asset_id,
            &source.source_sha256,
        )?;
        let reopened_bytes = reopened_service.verified_semantic_sfx_wav(
            reopened.id,
            &reopened.stamp(),
            source.asset_id,
            &source.source_sha256,
        )?;
        if reopened_bytes != original_bytes {
            return Err("reopened original SFX CAS differs from its exported source bytes".into());
        }
    }
    let persisted = service.project(project.id)?;
    let after_receipts = service.production_receipt_watermark(project.id)?;
    if persisted != project || after_receipts != before_receipts {
        return Err("persisted owner project differs from the actual SFX authoring state".into());
    }
    Ok(serde_json::Value::Object(
        [
            ("schema", json!("motionwright.semantic-sfx-av-prepared/1")),
            ("scope", json!("original imported stereo WAV sources and existing owner-native SFX preparation; native AV mux and independent final-media readback pending")),
            ("project_id", json!(project.id)),
            ("generation", json!(project.generation)),
            ("revision", json!(project.revision)),
            ("scene_id", json!(project.scenes[0].id)),
            ("scene_ids", json!(project.scenes.iter().map(|s|s.id).collect::<Vec<_>>())),
            ("deliverable_id", json!(project.deliverables[0].id)),
            ("frames", json!(master_frames)),
            ("fps", json!("30/1")),
            ("sample_rate", json!(48000)),
            ("channels", json!(2)),
            ("sample_count", json!(master_frames * 1600)),
            ("sample_count_unit", json!("stereo_sample_frames")),
            ("first_source_sha256", json!(sources[0].source_sha256)),
            ("second_source_sha256", json!(sources[1].source_sha256)),
            ("source_cue_ids", json!(project.audio.sfx_cues.iter().map(|c|c.id).collect::<Vec<_>>())),
            ("scheduled_sfx", json!(schedule)),
            ("audio_relative", json!(prepared.audio.relative_path)),
            ("audio_sha256", json!(prepared.audio.sha256)),
            ("native_preparation_verified", json!(true)),
            ("native_preparation_revision", json!(prepared.proof.revision)),
            ("original_source_count", json!(prepared.proof.original_asset_sha256.len())),
            ("sample_peak_pcm16", json!(observed_sample_peak)),
            ("pcm_payload_sha256", json!(pcm_payload_sha256)),
            ("physical_wav_byte_count", json!(prepared_bytes.len())),
            ("proof_exact_pcm_sha256_semantics", json!("sha256_of_complete_canonical_wav")),
            ("independent_mixer_comparison", json!(false)),
            ("native_render_dispatched", json!(false)),
            ("production_receipt_count", json!(after_receipts.count)),
            ("final_aac_pcm_verified", json!(false)),
            ("source_project_relative", json!("semantic-sfx-project.json")),
            ("source_project_sha256", json!(hex::encode(Sha256::digest(&source_project_bytes)))),
            ("source_bundle_relative", json!("semantic-sfx.motionwright")),
            ("source_bundle_manifest_sha256", json!(hex::encode(Sha256::digest(&bundle_manifest_bytes)))),
            ("source_reopened_generation", json!(reopened.generation)),
            ("source_reopened_revision", json!(reopened.revision)),
            ("source_reopened_cas_verified", json!(true)),
            ("source_reopened_native_receipts", json!(0)),
            ("source_reopened_native_rendered", json!(false)),
            ("creative_approval", json!("REQUIRED")),
            ("legal_rights", json!("NOT_PERFORMED")),
        ]
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect(),
    ))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("source-bound native SFX AV fixture requires disposable GitHub CI".into());
    }
    let composition = match std::env::var("MOTIONWRIGHT_E2E_FIXTURE").as_deref() {
        Ok("semantic-sfx-composition-av") => true,
        Ok("semantic-sfx-av") => false,
        _ => {
            return Err(
                "semantic SFX source preparation requires its explicit fixture profile".into(),
            );
        }
    };
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.len() != 2 {
        return Err(
            "usage: semantic-sfx-av-prepare EXISTING_OWNER_DATABASE OWNER_OUTPUT_DIRECTORY".into(),
        );
    }
    let result = prepare_existing_fixture(
        Path::new(&arguments[0]),
        Path::new(&arguments[1]),
        composition,
    )?;
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

#[cfg(test)]
mod source_contract_tests {
    use super::*;

    fn seed_three_scenes(database: &Path) -> Project {
        let service = StudioService::open(database).unwrap();
        let mut project = service
            .create_project("Original numerical SFX source fixture")
            .unwrap();
        for index in 0..3 {
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("original-scene-{index}"),
                    &Change::AddScene {
                        name: format!("Original scene {index}"),
                        objective: "Source-only SFX schedule, no native image claim".into(),
                        duration_seconds: 6,
                    },
                )
                .unwrap()
                .project;
        }
        project
    }

    #[test]
    fn three_scene_fixture_uses_existing_cas_preparer_without_native_receipts() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("studio.sqlite3");
        let original = seed_three_scenes(&database);
        let output = temporary.path().join("output");
        fs::create_dir(&output).unwrap();
        let output = output.canonicalize().unwrap();
        let report = prepare_existing_fixture(&database, &output, true).unwrap();
        assert_eq!(report["frames"], 540);
        assert_eq!(report["sample_count"], 864_000);
        assert_eq!(report["physical_wav_byte_count"], 3_456_044);
        assert_eq!(report["production_receipt_count"], 0);
        assert_eq!(report["independent_mixer_comparison"], false);
        assert_eq!(report["final_aac_pcm_verified"], false);
        let owner_audio = output.join(report["audio_relative"].as_str().unwrap());
        let bytes = fs::read(&owner_audio).unwrap();
        assert_eq!(
            bytes,
            fs::read(output.join("semantic-sfx-master.wav")).unwrap()
        );
        assert_eq!(
            hex::encode(Sha256::digest(&bytes)),
            report["audio_sha256"].as_str().unwrap()
        );
        assert_eq!(
            hex::encode(Sha256::digest(&bytes[44..])),
            report["pcm_payload_sha256"].as_str().unwrap(),
        );
        // Inspect the actual written source WAV; no second mix or native output.
        assert!(
            bytes[44 + 180 * 1600 * 4..44 + 360 * 1600 * 4]
                .iter()
                .all(|sample_byte| *sample_byte == 0)
        );
        let service = StudioService::open(&database).unwrap();
        let prepared_project = service.project(original.id).unwrap();
        assert_eq!(prepared_project.scenes, original.scenes);
        assert_eq!(
            prepared_project.production_design,
            original.production_design
        );
        assert_eq!(prepared_project.id, original.id);
        assert_eq!(prepared_project.generation, original.generation);
        assert_eq!(prepared_project.audio.sfx_cues.len(), 2);
        assert_eq!(
            service
                .production_receipt_watermark(original.id)
                .unwrap()
                .count,
            0
        );
        let exported: Project =
            serde_json::from_slice(&fs::read(output.join("semantic-sfx-project.json")).unwrap())
                .unwrap();
        assert_eq!(exported, prepared_project);
        let reopened_service =
            StudioService::open(output.join("semantic-sfx-reopened/studio.sqlite3")).unwrap();
        let reopened = reopened_service.project(original.id).unwrap();
        assert_ne!(reopened.generation, prepared_project.generation);
        assert_eq!(reopened.audio, prepared_project.audio);
        assert_eq!(reopened.assets, prepared_project.assets);
        let original_cue = reopened.audio.sfx_cues[1].clone();
        let mut edited_cue = original_cue.clone();
        edited_cue.gain_milli_db = -18_000;
        let edited = reopened_service
            .apply(
                reopened.id,
                &reopened.stamp(),
                "reopened-source-gain-edit",
                &Change::UpsertSemanticSfx {
                    cue: edited_cue.clone(),
                },
            )
            .unwrap()
            .project;
        assert_eq!(edited.generation, reopened.generation);
        assert!(edited.revision > reopened.revision);
        assert_eq!(edited.audio.sfx_cues[1], edited_cue);
        assert_eq!(edited.audio.sfx_cues[1].id, original_cue.id);
        assert_eq!(
            edited.audio.sfx_cues[1].original_source_attestation,
            original_cue.original_source_attestation
        );
        assert_eq!(reopened_service.project(edited.id).unwrap(), edited);
        assert_eq!(
            reopened_service
                .production_receipt_watermark(edited.id)
                .unwrap()
                .count,
            0
        );
        // Editing the reopened source does not mutate or reuse the old mix as
        // evidence for the new revision; there is no second preparation/render.
        assert_eq!(service.project(original.id).unwrap(), prepared_project);
        // Known, local source state is rejected before any second authoring step.
        assert!(prepare_existing_fixture(&database, &output, true).is_err());
        assert_eq!(service.project(original.id).unwrap(), prepared_project);
        assert_eq!(fs::read(owner_audio).unwrap(), bytes);
    }

    #[test]
    fn wrong_fixture_clock_rejects_before_import_or_output() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("studio.sqlite3");
        let original = seed_three_scenes(&database);
        let output = temporary.path().join("output");
        fs::create_dir(&output).unwrap();
        let output = output.canonicalize().unwrap();
        assert!(prepare_existing_fixture(&database, &output, false).is_err());
        assert_eq!(fs::read_dir(&output).unwrap().count(), 0);
        let service = StudioService::open(&database).unwrap();
        assert_eq!(service.project(original.id).unwrap(), original);
        assert_eq!(
            service
                .production_receipt_watermark(original.id)
                .unwrap()
                .count,
            0
        );
    }

    #[test]
    fn physical_pcm_observation_rejects_truncation_wrong_channels_and_clock() {
        let original = original_stereo_wav(440.0, 660.0);
        let (pcm_sha256, peak) = inspect_prepared_pcm(&original, u64::from(SFX_FRAMES)).unwrap();
        assert_eq!(pcm_sha256, hex::encode(Sha256::digest(&original[44..])));
        assert!(peak > 0);
        assert!(
            inspect_prepared_pcm(&original[..original.len() - 4], u64::from(SFX_FRAMES)).is_err()
        );
        let mut wrong_channels = original.clone();
        wrong_channels[22..24].copy_from_slice(&1_u16.to_le_bytes());
        assert!(inspect_prepared_pcm(&wrong_channels, u64::from(SFX_FRAMES)).is_err());
        assert!(inspect_prepared_pcm(&original, u64::from(SFX_FRAMES) + 1).is_err());
    }
}
