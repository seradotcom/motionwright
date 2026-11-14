//! Closed, synthetic three-bus fixture for the real saved-mix AV consumer.
//! This does not exercise Tauri OwnerInvocation or grant human/media rights.
use motionwright_domain::{
    AlignmentEvidence, Change, DeliveryRecipe, OriginalMixRecipe, Project, RationalTime,
    RevisionStamp, TranscriptSegment,
};
use motionwright_native::{
    original_mix::prepare_saved_original_mix_with_budget, production::MltAudioArtifact,
};
use motionwright_service::{
    OriginalMixDecodeBudget, StudioService, StudioServiceReader, VoiceImportMetadata,
};
use motionwright_storage::AssetIntegrityStatus;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::Path,
};
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const WAV_BYTES: usize = 384_044;
const MAX_WITNESS_BYTES: usize = 512 * 1024;
const BEFORE: &str = "saved-mix-before.private.json";
const SOURCE: &str = "saved-mix-source.private.json";

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
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

fn write_json(path: &Path, value: &Value) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value)?;
    if bytes.len() > MAX_WITNESS_BYTES {
        return Err("SavedMix source witness exceeded its fixed bound".into());
    }
    write_new(path, &bytes)
}

fn read_json(path: &Path) -> Result<Value> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.file_type().is_symlink() || meta.len() > MAX_WITNESS_BYTES as u64 {
        return Err("SavedMix source witness is not a bounded original file".into());
    }
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn apply(service: &StudioService, project: &mut Project, key: &str, change: Change) -> Result<()> {
    *project = service
        .apply(project.id, &RevisionStamp::from(&*project), key, &change)?
        .project;
    Ok(())
}

fn source_witness(service: &StudioService, project: &Project) -> Result<Value> {
    let handle = service.store();
    let store = handle.lock();
    Ok(json!({
        "schema": "motionwright.saved-mix-fixture-source/1",
        "project": project,
        "history": store.event_records_since(project.id, 0, 64)?,
        "database_identity": store.database_file_identity()?,
    }))
}

pub(super) fn seed(database: &Path, source_dir: &Path, evidence: &Path) -> Result<()> {
    let (service, mut project) = super::seed_project(database)?;
    for bus in ["voice", "music", "sfx"] {
        let name = format!("saved-mix-{bus}.wav");
        let path = source_dir.join(&name);
        let meta = fs::symlink_metadata(&path)?;
        if !meta.is_file() || meta.file_type().is_symlink() || meta.len() != WAV_BYTES as u64 {
            return Err("SavedMix requires three original two-second stereo WAV files".into());
        }
        if bus == "voice" {
            project = service
                .import_voice_file(
                    project.id,
                    &RevisionStamp::from(&project),
                    "saved-mix-import-voice",
                    &path,
                    VoiceImportMetadata {
                        name,
                        media_type: "audio/wav".into(),
                        label: "Synthetic fixture tone, not speech or human consent".into(),
                    },
                )?
                .project;
        } else {
            project = service
                .import_asset_file(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("saved-mix-import-{bus}"),
                    &path,
                    name,
                    "audio/wav".into(),
                )?
                .project;
        }
    }
    let voice_id = project.audio.voice_tracks[0].id;
    apply(
        &service,
        &mut project,
        "saved-mix-active-voice",
        Change::SetActiveVoiceTrack { track_id: voice_id },
    )?;
    let mut profile = project.deliverables[0].clone();
    profile.voice_track_id = Some(voice_id);
    profile.language = "en".into();
    let profile_id = profile.id;
    apply(
        &service,
        &mut project,
        "saved-mix-profile",
        Change::UpsertDeliverable { profile },
    )?;
    apply(
        &service,
        &mut project,
        "saved-mix-authored-window",
        Change::AddTranscriptSegment {
            segment: TranscriptSegment {
                id: Uuid::now_v7(),
                voice_track_id: voice_id,
                start: RationalTime::new(1, 2)?,
                end: RationalTime::new(1, 1)?,
                text: "Synthetic authored tone interval; not recognized speech".into(),
                speaker: None,
                alignment: AlignmentEvidence::Manual,
            },
        },
    )?;
    let asset = |name: &str| {
        project
            .assets
            .iter()
            .find(|asset| asset.name == name)
            .map(|asset| asset.id)
    };
    let recipe = OriginalMixRecipe {
        music_asset_id: asset("saved-mix-music.wav"),
        sfx_asset_id: asset("saved-mix-sfx.wav"),
        sfx_gain_db: -8.0,
        duck_attenuation_db: 14.0,
        duck_attack_samples: 2_400,
        duck_release_samples: 4_800,
        voice_envelope: Default::default(),
        music_envelope: Default::default(),
        sfx_envelope: Default::default(),
    };
    if recipe.music_asset_id.is_none()
        || recipe.sfx_asset_id.is_none()
        || project.audio.mix.voice_gain_db != 0.0
        || project.audio.mix.music_gain_db != -12.0
    {
        return Err("SavedMix fixed source assets or explicit default gains changed".into());
    }
    apply(
        &service,
        &mut project,
        "saved-mix-save-recipe",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(DeliveryRecipe {
                original_mix: Some(recipe),
                ..Default::default()
            }),
        },
    )?;
    let before = source_witness(&service, &project)?;
    write_json(&evidence.join(BEFORE), &before)?;
    service.prepare_read_only_handoff()?;
    println!(
        "{}",
        json!({
            "project_id": project.id, "resource": project.resource_key(),
            "generation": project.generation, "revision": project.revision,
            "scene_id": project.scenes[0].id, "deliverable_id": profile_id,
            "width": project.deliverables[0].width, "height": project.deliverables[0].height,
            "fixture": "saved-mix",
        })
    );
    Ok(())
}

pub(super) fn verify_source(
    service: &StudioService,
    project: &Project,
    evidence: &Path,
) -> Result<()> {
    if source_witness(service, project)? != read_json(&evidence.join(BEFORE))? {
        return Err("SavedMix project, journal or original SQLite identity changed".into());
    }
    Ok(())
}

pub(super) fn prepare(
    service: &StudioService,
    project: &Project,
    output: &Path,
    evidence: &Path,
) -> Result<MltAudioArtifact> {
    verify_source(service, project, evidence)?;
    let profile = project
        .deliverables
        .first()
        .ok_or("SavedMix profile is absent")?;
    let mix = prepare_saved_original_mix_with_budget(
        service,
        project.id,
        &RevisionStamp::from(project),
        profile.id,
        OriginalMixDecodeBudget::FullCut180Seconds,
    )?;
    let plan = mix.plan();
    if project.assets.len() != 3
        || plan.sample_frames != 96_000
        || mix.wav_bytes().len() != WAV_BYTES
        || plan.voice_gain_db != 0.0
        || plan.music_gain_db != -12.0
        || plan.sfx_gain_db != -8.0
        || plan.duck_attenuation_db != 14.0
        || plan.duck_attack_samples != 2_400
        || plan.duck_release_samples != 4_800
        || plan.voice_windows.len() != 1
        || plan.voice_windows[0].start_sample != 24_000
        || plan.voice_windows[0].end_exclusive != 48_000
        || mix.evidence().owner_release_approved
        || mix.evidence().listening_intelligibility_approved
        || mix.evidence().independently_verified_lufs
        || mix.evidence().independently_verified_true_peak
    {
        return Err("SavedMix preparation differs from its closed source fixture".into());
    }
    mix.verify_project(&service.project(project.id)?, profile.id)?;
    let output_meta = fs::symlink_metadata(output)?;
    if !output_meta.is_dir() || output_meta.file_type().is_symlink() {
        return Err("SavedMix output root is not the owned directory".into());
    }
    // Harness staging is create-new only. The product consumer independently
    // rebuilds the mix from this same Store and checks its SHA and exact clock.
    let relative = "saved-mix-original.wav";
    write_new(&output.join(relative), mix.wav_bytes())?;
    write_new(&evidence.join("saved-mix.wav"), mix.wav_bytes())?;
    write_json(
        &evidence.join(SOURCE),
        &json!({
            "schema": "motionwright.saved-mix-prepared-source/1",
            "project_id": project.id, "generation": project.generation,
            "revision": project.revision, "deliverable_id": profile.id,
            "plan": plan, "evidence": mix.evidence(),
            "wav_sha256": mix.wav_sha256(), "audio_inputs_sha256": mix.audio_inputs_sha256(),
            "automatic_retry": false, "tauri_owner_invocation": "NOT_EXERCISED",
        }),
    )?;
    Ok(MltAudioArtifact {
        relative_path: relative.into(),
        sha256: mix.wav_sha256().into(),
        sample_rate: 48_000,
        channels: 2,
    })
}

pub(super) fn readback(database: &Path, evidence: &Path) -> Result<()> {
    let before = read_json(&evidence.join(BEFORE))?;
    let reader = StudioServiceReader::open(database)?;
    let projects = reader.projects(2)?;
    if projects.len() != 1 {
        return Err("SavedMix independent reader requires one original project".into());
    }
    let project = &projects[0];
    let observed = json!({
        "schema": "motionwright.saved-mix-fixture-source/1",
        "project": project,
        "history": reader.project_history(project.id, 0, 64)?,
        "database_identity": reader.database_file_identity()?,
    });
    if before != observed {
        return Err(
            "SavedMix independent readback changed project, history or physical Store".into(),
        );
    }
    let integrity =
        reader.asset_integrity_page(project.id, &RevisionStamp::from(project), None, 16)?;
    if !integrity.complete
        || integrity.next.is_some()
        || integrity.total_assets != 3
        || integrity.items.len() != 3
        || integrity.checked_bytes != 3 * WAV_BYTES as u64
        || integrity
            .items
            .iter()
            .any(|item| item.status != AssetIntegrityStatus::Verified)
    {
        return Err("SavedMix independent reader did not verify all original CAS bytes".into());
    }
    let response = json!({
        "schema": "motionwright.saved-mix-independent-readback/1",
        "project_unchanged": true, "history_unchanged": true, "same_physical_database": true,
        "verified_original_assets": 3, "verified_original_bytes": integrity.checked_bytes,
        "source_witness_sha256": digest(&serde_json::to_vec(&observed)?),
        "writer_handoff_verified": true,
        "reader_authority": "StudioServiceReader",
        "automatic_retry": false, "tauri_owner_invocation": "NOT_EXERCISED",
        "human_approval": false, "release_authorized": false,
    });
    write_json(&evidence.join("saved-mix-readback.json"), &response)?;
    println!("{response}");
    Ok(())
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    fn source_wav(left: i16, right: i16) -> Vec<u8> {
        let size = 96_000_u32 * 4;
        let mut out = Vec::with_capacity(WAV_BYTES);
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + size).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16_u32.to_le_bytes());
        out.extend_from_slice(&1_u16.to_le_bytes());
        out.extend_from_slice(&2_u16.to_le_bytes());
        out.extend_from_slice(&48_000_u32.to_le_bytes());
        out.extend_from_slice(&192_000_u32.to_le_bytes());
        out.extend_from_slice(&4_u16.to_le_bytes());
        out.extend_from_slice(&16_u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&size.to_le_bytes());
        for _ in 0..96_000 {
            out.extend_from_slice(&left.to_le_bytes());
            out.extend_from_slice(&right.to_le_bytes());
        }
        out
    }

    #[test]
    fn saved_fixture_uses_real_cas_factory_and_independent_read_only_store() {
        // Source-only regression. Constant PCM is not an encoded-media oracle.
        let root = tempfile::tempdir().unwrap();
        let evidence = root.path().join("evidence");
        let output = root.path().join("output");
        fs::create_dir(&evidence).unwrap();
        fs::create_dir(&output).unwrap();
        for (bus, left, right) in [
            ("voice", 1_000, 2_000),
            ("music", 3_000, 4_000),
            ("sfx", 500, 700),
        ] {
            write_new(
                &evidence.join(format!("saved-mix-{bus}.wav")),
                &source_wav(left, right),
            )
            .unwrap();
        }
        let database = root.path().join("studio.sqlite3");
        seed(&database, &evidence, &evidence).unwrap();
        let service = StudioService::open(&database).unwrap();
        let project = service.projects(2).unwrap().remove(0);
        let audio = prepare(&service, &project, &output, &evidence).unwrap();
        assert_ne!(audio.sha256, project.audio.voice_tracks[0].source_sha256);
        assert_eq!(
            fs::read(output.join(audio.relative_path)).unwrap(),
            fs::read(evidence.join("saved-mix.wav")).unwrap()
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        service.prepare_read_only_handoff().unwrap();
        drop(service);
        readback(&database, &evidence).unwrap();
        let proof = read_json(&evidence.join("saved-mix-readback.json")).unwrap();
        assert_eq!(proof["same_physical_database"], true);
        assert_eq!(proof["verified_original_assets"], 3);
        assert_eq!(proof["reader_authority"], "StudioServiceReader");
        assert_eq!(proof["release_authorized"], false);
    }
}
