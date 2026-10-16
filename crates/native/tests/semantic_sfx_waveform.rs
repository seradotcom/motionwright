//! Exact original imported WAV waveform evidence from owner StudioService CAS.
use motionwright_domain::{Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent};
use motionwright_native::sfx_waveform::inspect_owner_semantic_sfx_waveform;
use motionwright_service::StudioService;
use std::{fs, path::Path};
use tempfile::TempDir;
use uuid::Uuid;

const ORIGINAL_FRAMES: usize = 3;

fn original_wav(path: &Path) -> Vec<u8> {
    let samples = ORIGINAL_FRAMES * 1600;
    let pcm_bytes = (samples * 4) as u32;
    let mut wav = Vec::with_capacity(44 + pcm_bytes as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(pcm_bytes + 36).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&48_000_u32.to_le_bytes());
    wav.extend_from_slice(&192_000_u32.to_le_bytes());
    wav.extend_from_slice(&4_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&pcm_bytes.to_le_bytes());
    for index in 0..samples {
        let frame = index / 1600;
        let (left, right) = [(1200_i16, 600_i16), (3000, 1500), (900, 1800)][frame];
        let sign = if index % 2 == 0 { 1 } else { -1 };
        wav.extend_from_slice(&(left * sign).to_le_bytes());
        wav.extend_from_slice(&(right * sign).to_le_bytes());
    }
    fs::write(path, &wav).unwrap();
    wav
}

struct Fixture {
    _temp: TempDir,
    service: StudioService,
    project: Project,
    cue_id: Uuid,
    original: Vec<u8>,
}
fn owner_fixture() -> Fixture {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("owner-waveform.sqlite3")).unwrap();
    let mut project = service
        .create_project("Owner original SFX waveform")
        .unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "original-scene",
            &Change::AddScene {
                name: "Original sound scene".into(),
                objective: "Source sample visualization".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let path = temp.path().join("owner-original.wav");
    let original = original_wav(&path);
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "Owner original WAV",
            &path,
            "Numerical original source, not creative approval".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let asset = project.assets.last().unwrap();
    let cue_id = Uuid::new_v4();
    let cue = SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id: cue_id,
        scene_id: project.scenes[0].id,
        event: SfxEvent::UiFocus,
        anchor: SfxAnchor::SceneStart,
        offset_frames: 4,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        source_frames: ORIGINAL_FRAMES as u16,
        gain_milli_db: -12000,
        attack_frames: 0,
        release_frames: 1,
        original_source_attestation: "Owner-authored waveform sample".into(),
    };
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "source-bound-cue",
            &Change::UpsertSemanticSfx { cue },
        )
        .unwrap()
        .project;
    Fixture {
        _temp: temp,
        service,
        project,
        cue_id,
        original,
    }
}

#[test]
fn original_cas_stereo_extrema_are_exact_and_source_project_unchanged() {
    let f = owner_fixture();
    let saved = f.service.project(f.project.id).unwrap();
    let cue = &f.project.audio.sfx_cues[0];
    let original = f
        .service
        .verified_semantic_sfx_wav(
            f.project.id,
            &f.project.stamp(),
            cue.source_asset_id,
            &cue.source_sha256,
        )
        .unwrap();
    assert_eq!(original, f.original);
    let view = inspect_owner_semantic_sfx_waveform(&f.project, f.cue_id, &original).unwrap();
    assert_eq!(view.project_id, f.project.id);
    assert_eq!(view.generation, f.project.generation);
    assert_eq!(view.revision, f.project.revision);
    assert_eq!(view.cue_id, f.cue_id);
    assert_eq!(view.source_sha256, cue.source_sha256);
    assert_eq!(view.sample_rate_hz, 48000);
    assert_eq!(view.channels, 2);
    assert_eq!(view.samples_per_peak, 1600);
    assert_eq!(view.source_frames, 3);
    assert_eq!(view.left_min_pcm16, vec![-1200, -3000, -900]);
    assert_eq!(view.left_max_pcm16, vec![1200, 3000, 900]);
    assert_eq!(view.right_min_pcm16, vec![-600, -1500, -1800]);
    assert_eq!(view.right_max_pcm16, vec![600, 1500, 1800]);
    assert_eq!(view.sample_peak_pcm16, 3000);
    assert_eq!(view.measured_sha256.len(), 64);
    assert!(view.evidence_scope.ends_with("NOT-listening-or-master"));
    assert_eq!(
        inspect_owner_semantic_sfx_waveform(&f.project, f.cue_id, &original).unwrap(),
        view
    );
    assert_eq!(f.service.project(f.project.id).unwrap(), saved);
}

#[test]
fn rehashed_foreign_source_wrong_riff_and_truncated_pcm_fail_closed() {
    let f = owner_fixture();
    let mut alternate = f.original.clone();
    alternate[50] ^= 1;
    assert!(inspect_owner_semantic_sfx_waveform(&f.project, f.cue_id, &alternate).is_err());
    alternate = f.original.clone();
    alternate[8] = b'Q';
    assert!(inspect_owner_semantic_sfx_waveform(&f.project, f.cue_id, &alternate).is_err());
    assert!(inspect_owner_semantic_sfx_waveform(&f.project, f.cue_id, &f.original[..50]).is_err());
    assert!(
        inspect_owner_semantic_sfx_waveform(
            &f.project,
            f.cue_id,
            &[f.original.clone(), vec![0; 4],].concat()
        )
        .is_err()
    );
}

#[test]
fn foreign_cue_generation_and_modified_source_metadata_never_qualify() {
    let f = owner_fixture();
    assert!(inspect_owner_semantic_sfx_waveform(&f.project, Uuid::new_v4(), &f.original).is_err());
    let mut changed = f.project.clone();
    changed.audio.sfx_cues[0].source_frames = 2;
    assert!(inspect_owner_semantic_sfx_waveform(&changed, f.cue_id, &f.original).is_err());
    changed = f.project.clone();
    changed.audio.sfx_cues[0].source_sha256 = "f".repeat(64);
    assert!(inspect_owner_semantic_sfx_waveform(&changed, f.cue_id, &f.original).is_err());
    changed = f.project.clone();
    changed.audio.sfx_cues[0].source_asset_id = Uuid::new_v4();
    assert!(inspect_owner_semantic_sfx_waveform(&changed, f.cue_id, &f.original).is_err());
}
