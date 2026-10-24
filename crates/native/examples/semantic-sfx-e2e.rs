//! CI-only real owner-local original SFX WAVs -> StudioService CAS -> native
//! stereo PCM mix. Does not claim an audiovisual MP4 or creative approval.
use motionwright_domain::{
    Asset, Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent,
};
use motionwright_native::sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx};
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::Path};
use uuid::Uuid;

fn wav(frames: u16, left_hz: f64, right_hz: f64) -> Vec<u8> {
    let samples = usize::from(frames) * 1600;
    let size = (samples * 4) as u32;
    let mut result = Vec::with_capacity(44 + size as usize);
    result.extend_from_slice(b"RIFF");
    result.extend_from_slice(&(size + 36).to_le_bytes());
    result.extend_from_slice(b"WAVEfmt ");
    result.extend_from_slice(&16_u32.to_le_bytes());
    result.extend_from_slice(&1_u16.to_le_bytes());
    result.extend_from_slice(&2_u16.to_le_bytes());
    result.extend_from_slice(&48000_u32.to_le_bytes());
    result.extend_from_slice(&192000_u32.to_le_bytes());
    result.extend_from_slice(&4_u16.to_le_bytes());
    result.extend_from_slice(&16_u16.to_le_bytes());
    result.extend_from_slice(b"data");
    result.extend_from_slice(&size.to_le_bytes());
    for sample in 0..samples {
        let t = sample as f64 / 48000.0;
        for hz in [left_hz, right_hz] {
            let value = (12000.0 * (t * hz * std::f64::consts::TAU).sin()).round() as i16;
            result.extend_from_slice(&value.to_le_bytes());
        }
    }
    result
}
fn import(
    service: &StudioService,
    project: &mut Project,
    directory: &Path,
    name: &str,
    data: Vec<u8>,
) -> Result<(Asset, Vec<u8>), Box<dyn std::error::Error>> {
    let file = directory.join(name);
    fs::write(&file, &data)?;
    *project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            name,
            &file,
            format!("Original synthetic source {name}"),
            "audio/wav".into(),
        )?
        .project;
    let asset = project
        .assets
        .last()
        .ok_or("import did not create source Asset")?
        .clone();
    Ok((asset, data))
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let output = std::env::args().nth(1).ok_or("expected output directory")?;
    let output = Path::new(&output);
    fs::create_dir_all(output)?;
    let service = StudioService::open(output.join("original-sfx.sqlite3"))?;
    if !service.projects(1)?.is_empty() {
        return Err("original SFX example requires clean owner-local database".into());
    }
    let mut project =
        service.create_project("Original source-bound semantic sound / synthetic evidence")?;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "create-scene",
            &Change::AddScene {
                name: "Synthetic source audio choreography".into(),
                objective: "Proof of two authored original PCM effects".into(),
                duration_seconds: 6,
            },
        )?
        .project;
    let (first, first_bytes) = import(
        &service,
        &mut project,
        output,
        "original-focus.wav",
        wav(30, 330.0, 550.0),
    )?;
    let (second, second_bytes) = import(
        &service,
        &mut project,
        output,
        "original-confirmation.wav",
        wav(30, 720.0, 1020.0),
    )?;
    for (index, (asset, offset, event)) in [
        (first.clone(), 5, SfxEvent::UiFocus),
        (second.clone(), 20, SfxEvent::Confirmation),
    ]
    .into_iter()
    .enumerate()
    {
        let cue = SemanticSfxCue {
            schema: SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id: project.scenes[0].id,
            event,
            anchor: SfxAnchor::SceneStart,
            offset_frames: offset,
            source_asset_id: asset.id,
            source_sha256: asset.content_sha256.clone().unwrap(),
            source_frames: 30,
            gain_milli_db: -16000,
            attack_frames: 3,
            release_frames: 10,
            tail_policy: Default::default(),
            original_source_attestation:
                "I generated this original synthetic stereo tone from explicit numeric samples"
                    .into(),
        };
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("original-effect-{index}"),
                &Change::UpsertSemanticSfx { cue },
            )?
            .project;
    }
    let sources = [(first, first_bytes), (second, second_bytes)]
        .into_iter()
        .map(|(asset, original)| {
            let bytes = service.verified_semantic_sfx_wav(
                project.id,
                &project.stamp(),
                asset.id,
                asset.content_sha256.as_deref().unwrap(),
            )?;
            if bytes != original {
                return Err("owner-local CAS changed original audio bytes".into());
            }
            Ok(VerifiedSfxWav {
                asset_id: asset.id,
                source_sha256: asset.content_sha256.unwrap(),
                bytes,
            })
        })
        .collect::<Result<Vec<_>, Box<dyn std::error::Error>>>()?;
    let mixed = mix_source_bound_semantic_sfx(&project, project.deliverables[0].id, &sources, 180)?;
    let master = output.join("original-sfx-mix.wav");
    let mut file = fs::File::create(&master)?;
    file.write_all(&mixed.wav_bytes)?;
    file.sync_all()?;
    fs::write(
        output.join("editable-project.json"),
        serde_json::to_vec_pretty(&project)?,
    )?;
    let motionwright_sha = std::env::var("GITHUB_SHA").unwrap_or_else(|_| "0".repeat(40));
    if motionwright_sha.len() != 40
        || !motionwright_sha
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("SFX evidence requires a full 40-digit Git source revision".into());
    }
    let receipt = serde_json::json!({
        "motionwright_sha": motionwright_sha,
        "schema":"motionwright.semantic-sfx-native-technical-evidence/1",
        "project_id":project.id,
        "resource":project.resource_key(),
        "generation":project.generation,
        "revision":project.revision,
        "scene_id":project.scenes[0].id,
        "deliverable_id":project.deliverables[0].id,
        "source_shas":sources.iter().map(|s|s.source_sha256.clone()).collect::<Vec<_>>(),
        "mixed_wav_sha256":mixed.output_sha256,
        "source_count":mixed.original_sources,
        "frames":mixed.rendered_frames,
        "sample_rate":48000,
        "channels":2,
        "sample_peak_pcm16":mixed.sample_peak_pcm16,
        "master_scope":"original WAV native PCM technical mix, no AV mux",
        "rights":"synthetic owner-authored source, not external licensing review",
        "creative_approval":"required"
    });
    fs::write(
        output.join("result.json"),
        serde_json::to_vec_pretty(&receipt)?,
    )?;
    assert_eq!(
        hex::encode(Sha256::digest(&mixed.wav_bytes)),
        receipt["mixed_wav_sha256"].as_str().unwrap()
    );
    println!("{}", serde_json::to_string(&receipt)?);
    Ok(())
}
