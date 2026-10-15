//! Original owner-local SFX source admission before AV mux dispatch.
use motionwright_domain::{Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent};
use motionwright_native::{
    production::{MltAudioArtifact, prepare_semantic_sfx_av_audio, verify_semantic_sfx_av_source},
    sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx},
};
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use tempfile::TempDir;
use uuid::Uuid;

fn wav(left_hz: f64, right_hz: f64) -> Vec<u8> {
    let samples = 24_usize * 1600;
    let size = (samples * 4) as u32;
    let mut result = Vec::with_capacity(44 + size as usize);
    result.extend_from_slice(b"RIFF");
    result.extend_from_slice(&(size + 36).to_le_bytes());
    result.extend_from_slice(b"WAVEfmt ");
    result.extend_from_slice(&16_u32.to_le_bytes());
    result.extend_from_slice(&1_u16.to_le_bytes());
    result.extend_from_slice(&2_u16.to_le_bytes());
    result.extend_from_slice(&48_000_u32.to_le_bytes());
    result.extend_from_slice(&192_000_u32.to_le_bytes());
    result.extend_from_slice(&4_u16.to_le_bytes());
    result.extend_from_slice(&16_u16.to_le_bytes());
    result.extend_from_slice(b"data");
    result.extend_from_slice(&size.to_le_bytes());
    for i in 0..samples {
        let at = i as f64 / 48000.0;
        for frequency in [left_hz, right_hz] {
            let sample = (12_000.0 * (at * frequency * std::f64::consts::TAU).sin()).round() as i16;
            result.extend_from_slice(&sample.to_le_bytes());
        }
    }
    result
}

fn insert(
    service: &StudioService,
    mut project: Project,
    dir: &Path,
    name: &str,
    offset: i16,
    tones: (f64, f64),
) -> (Project, VerifiedSfxWav) {
    let source = wav(tones.0, tones.1);
    let path = dir.join(name);
    fs::write(&path, &source).unwrap();
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            name,
            &path,
            format!("Original sample {name}"),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let asset = project.assets.last().unwrap().clone();
    let source_sha256 = asset.content_sha256.unwrap();
    let cue = SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        event: SfxEvent::UiFocus,
        anchor: SfxAnchor::SceneStart,
        offset_frames: offset,
        source_asset_id: asset.id,
        source_sha256: source_sha256.clone(),
        source_frames: 24,
        gain_milli_db: -12_000,
        attack_frames: 3,
        release_frames: 6,
        original_source_attestation: "Owner original numeric PCM test source".into(),
    };
    project = service
        .apply(
            project.id,
            &project.stamp(),
            &format!("cue-{name}"),
            &Change::UpsertSemanticSfx { cue },
        )
        .unwrap()
        .project;
    let bytes = service
        .verified_semantic_sfx_wav(project.id, &project.stamp(), asset.id, &source_sha256)
        .unwrap();
    assert_eq!(bytes, source);
    (
        project,
        VerifiedSfxWav {
            asset_id: asset.id,
            source_sha256,
            bytes,
        },
    )
}

struct Fixture {
    service: StudioService,
    project: Project,
    audio: MltAudioArtifact,
    deliverable: Uuid,
    source_root: TempDir,
}

fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("owner-output");
    fs::create_dir(&root).unwrap();
    let service = StudioService::open(dir.path().join("sfx-av.sqlite3")).unwrap();
    let project = service
        .create_project("Original SFX AV source gate")
        .unwrap();
    let project = service
        .apply(
            project.id,
            &project.stamp(),
            "initial-scene",
            &Change::AddScene {
                name: "Original animated scene".into(),
                objective: "Sample-exact original sound".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let (project, first) = insert(
        &service,
        project,
        dir.path(),
        "first.wav",
        6,
        (440.0, 660.0),
    );
    let (project, second) = insert(
        &service,
        project,
        dir.path(),
        "second.wav",
        20,
        (740.0, 980.0),
    );
    let deliverable = project.deliverables[0].id;
    let mixed = mix_source_bound_semantic_sfx(&project, deliverable, &[first, second], 60).unwrap();
    fs::write(root.join("semantic-sfx-master.wav"), &mixed.wav_bytes).unwrap();
    let audio = MltAudioArtifact {
        relative_path: "semantic-sfx-master.wav".into(),
        sha256: mixed.output_sha256,
        sample_rate: 48_000,
        channels: 2,
    };
    Fixture {
        service,
        project,
        audio,
        deliverable,
        source_root: dir,
    }
}
fn accept(
    f: &Fixture,
) -> semwright_native_sdk::Result<motionwright_native::production::SemanticSfxAvSourceProof> {
    verify_semantic_sfx_av_source(
        &f.service,
        f.project.id,
        &f.project.stamp(),
        f.deliverable,
        60,
        &f.audio,
        &f.source_root.path().join("owner-output"),
    )
}

#[test]
fn original_owner_pcm_matches_two_persisted_assets_and_current_revision() {
    let f = fixture();
    let initial = f.service.project(f.project.id).unwrap();
    let admission = accept(&f).unwrap();
    assert_eq!(admission.project_id, f.project.id);
    assert_eq!(admission.revision, f.project.revision);
    assert_eq!(admission.generation, f.project.generation);
    assert_eq!(admission.master_frames, 60);
    assert_eq!(admission.original_asset_sha256.len(), 2);
    assert_eq!(admission.exact_pcm_sha256, f.audio.sha256);
    assert!(admission.sample_peak_pcm16 > 0);
    assert_eq!(f.service.project(f.project.id).unwrap(), initial);
}

#[test]
fn output_audio_different_from_owner_mix_cannot_enter_mlt_dispatch() {
    let mut f = fixture();
    f.audio.sha256 = "f".repeat(64);
    assert!(accept(&f).is_err());
    let path = f
        .source_root
        .path()
        .join("owner-output/semantic-sfx-master.wav");
    let mut tampered = fs::read(&path).unwrap();
    tampered[100] ^= 1;
    fs::write(&path, &tampered).unwrap();
    f.audio.sha256 = hex::encode(Sha256::digest(&tampered));
    assert!(
        accept(&f).is_err(),
        "even a truthful alternate digest is not source-equivalent"
    );
}

#[test]
fn unauthorized_generation_revision_and_wrong_delivery_are_denied() {
    let mut f = fixture();
    f.project.revision += 1;
    assert!(accept(&f).is_err());
    f.project.revision -= 1;
    f.project.generation = Uuid::new_v4();
    assert!(accept(&f).is_err());
    f.project.generation = f.service.project(f.project.id).unwrap().generation;
    f.deliverable = Uuid::new_v4();
    assert!(accept(&f).is_err());
}

#[test]
fn timing_and_missing_audio_source_reject_without_side_effects() {
    let mut f = fixture();
    f.audio.sample_rate = 44_100;
    assert!(accept(&f).is_err());
    f.audio.sample_rate = 48_000;
    let root = f.source_root.path().join("owner-output");
    assert!(
        verify_semantic_sfx_av_source(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            f.deliverable,
            901,
            &f.audio,
            &root,
        )
        .is_err()
    );
    f.audio.relative_path = "missing.wav".into();
    assert!(accept(&f).is_err());
}

#[cfg(unix)]
#[test]
fn redirected_audio_symlink_is_never_admitted_as_owner_mix() {
    use std::os::unix::fs::symlink;
    let f = fixture();
    let root = f.source_root.path().join("owner-output");
    let path = root.join("semantic-sfx-master.wav");
    fs::rename(&path, root.join("retained.wav")).unwrap();
    symlink(root.join("retained.wav"), &path).unwrap();
    assert!(accept(&f).is_err());
}

fn prepare(
    f: &Fixture,
) -> semwright_native_sdk::Result<motionwright_native::production::PreparedSemanticSfxWav> {
    prepare_semantic_sfx_av_audio(
        &f.service,
        f.project.id,
        &f.project.stamp(),
        f.deliverable,
        60,
        &f.source_root.path().join("owner-output"),
    )
}

#[test]
fn preparation_creates_one_exact_owner_source_and_is_content_addressed() {
    let f = fixture();
    let original = f.service.project(f.project.id).unwrap();
    let first = prepare(&f).unwrap();
    assert_eq!(first.audio.sha256, f.audio.sha256);
    assert_eq!(first.audio.channels, 2);
    assert_eq!(first.audio.sample_rate, 48_000);
    assert_eq!(first.proof.revision, f.project.revision);
    assert_eq!(first.proof.master_frames, 60);
    assert_eq!(first.proof.original_asset_sha256.len(), 2);
    let path = f
        .source_root
        .path()
        .join("owner-output")
        .join(&first.audio.relative_path);
    let expected = fs::read(
        f.source_root
            .path()
            .join("owner-output/semantic-sfx-master.wav"),
    )
    .unwrap();
    assert_eq!(fs::read(&path).unwrap(), expected);
    let again = prepare(&f).unwrap();
    assert_eq!(again.audio.relative_path, first.audio.relative_path);
    assert_eq!(again.audio.sha256, first.audio.sha256);
    assert_eq!(fs::read(&path).unwrap(), expected);
    assert_eq!(f.service.project(f.project.id).unwrap(), original);
}

#[test]
fn prepared_sha_filename_cannot_be_reused_for_alternate_pcm() {
    let f = fixture();
    let prepared = prepare(&f).unwrap();
    let path = f
        .source_root
        .path()
        .join("owner-output")
        .join(&prepared.audio.relative_path);
    let mut tampered = fs::read(&path).unwrap();
    tampered[98] ^= 1;
    fs::write(&path, &tampered).unwrap();
    assert!(
        prepare(&f).is_err(),
        "existing content-addressed filename must not be overwritten"
    );
    assert_eq!(
        fs::read(&path).unwrap(),
        tampered,
        "fail closed; no hidden file repair"
    );
}

#[test]
fn stale_or_wrong_deliverable_cannot_create_a_prepared_audio_artifact() {
    let mut f = fixture();
    let owner_root = f.source_root.path().join("owner-output");
    let files = || fs::read_dir(&owner_root).unwrap().count();
    let start = files();
    f.project.revision += 1;
    assert!(prepare(&f).is_err());
    f.project.revision -= 1;
    f.project.generation = Uuid::new_v4();
    assert!(prepare(&f).is_err());
    f.project.generation = f.service.project(f.project.id).unwrap().generation;
    f.deliverable = Uuid::new_v4();
    assert!(prepare(&f).is_err());
    assert_eq!(files(), start, "no false prepared WAV was emitted");
}

#[test]
fn missing_or_invalid_owner_root_is_rejected_without_output() {
    let f = fixture();
    assert!(
        prepare_semantic_sfx_av_audio(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            f.deliverable,
            60,
            &f.source_root.path().join("missing-owner-root"),
        )
        .is_err()
    );
    let original = f
        .source_root
        .path()
        .join("owner-output/semantic-sfx-master.wav");
    assert!(
        prepare_semantic_sfx_av_audio(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            f.deliverable,
            901,
            &f.source_root.path().join("owner-output"),
        )
        .is_err()
    );
    assert!(original.is_file());
}

#[cfg(unix)]
#[test]
fn symlinked_owner_output_root_is_rejected_before_source_creation() {
    use std::os::unix::fs::symlink;
    let f = fixture();
    let alias = f.source_root.path().join("bad-owner-root");
    symlink(f.source_root.path().join("owner-output"), &alias).unwrap();
    assert!(
        prepare_semantic_sfx_av_audio(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            f.deliverable,
            60,
            &alias,
        )
        .is_err()
    );
}
