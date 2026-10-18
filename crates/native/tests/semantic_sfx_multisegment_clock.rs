//! Original generated stereo WAVs against a typed two-clip 30-fps source clock.
//! These real SQLite/CAS preflight tests do not invoke a native renderer or mux.
use motionwright_domain::{Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent};
use motionwright_native::{
    mlt_edit_plan::{MltNativeSegmentEdit, MltNativeVideoRecipe, MltOwnedVideoProfile},
    production::{
        MltAudioArtifact, SemanticSfxAvSourceProof, preflight_multisegment_original_sfx_clock,
        prepare_semantic_sfx_av_audio, verify_semantic_sfx_av_source,
    },
};
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use tempfile::TempDir;
use uuid::Uuid;

fn original_wav(path: &Path, hz: u32) {
    let frames = 24_usize * 1600;
    let data_size = u32::try_from(frames * 4).unwrap();
    let mut wav = Vec::with_capacity(frames * 4 + 44);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(data_size + 36).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&2u16.to_le_bytes());
    wav.extend_from_slice(&48_000u32.to_le_bytes());
    wav.extend_from_slice(&192_000u32.to_le_bytes());
    wav.extend_from_slice(&4u16.to_le_bytes());
    wav.extend_from_slice(&16u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_size.to_le_bytes());
    for i in 0..frames {
        for f in [hz, hz + 120] {
            let value = (12_000.0
                * (i as f64 * std::f64::consts::TAU * f64::from(f) / 48_000.0).sin())
            .round() as i16;
            wav.extend_from_slice(&value.to_le_bytes());
        }
    }
    fs::write(path, wav).unwrap();
}

struct Source {
    project: Project,
    recipe: MltNativeVideoRecipe,
    wav: MltAudioArtifact,
    proof: SemanticSfxAvSourceProof,
    owner: TempDir,
    service: StudioService,
}

fn source() -> Source {
    let owner = tempfile::tempdir().unwrap();
    let root = owner.path().join("owner-output");
    fs::create_dir(&root).unwrap();
    let service = StudioService::open(owner.path().join("two-scene.sqlite3")).unwrap();
    let mut project = service.create_project("Original multisegment SFX").unwrap();
    for (number, name) in ["Focus source scene", "Confirmation source scene"]
        .into_iter()
        .enumerate()
    {
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("source-scene-{}", number),
                &Change::AddScene {
                    name: name.into(),
                    objective: "Original edited audio clock".into(),
                    duration_seconds: 6,
                },
            )
            .unwrap()
            .project;
    }
    for (i, scene_id, hz) in [
        (0_usize, project.scenes[0].id, 440_u32),
        (1_usize, project.scenes[1].id, 740_u32),
    ] {
        let name = format!("owner-original-{i}.wav");
        let path = owner.path().join(&name);
        original_wav(&path, hz);
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                &name,
                &path,
                "Owner-generated stereo calibration tone".into(),
                "audio/wav".into(),
            )
            .unwrap()
            .project;
        let asset = project.assets.last().unwrap();
        let cue = SemanticSfxCue {
            schema: SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id,
            event: SfxEvent::UiFocus,
            anchor: SfxAnchor::SceneStart,
            offset_frames: if i == 0 { 6 } else { 20 },
            source_asset_id: asset.id,
            source_sha256: asset.content_sha256.clone().unwrap(),
            source_frames: 24,
            gain_milli_db: -12_000,
            attack_frames: 3,
            release_frames: 6,
            original_source_attestation: "Owner-created source only; not licensed media".into(),
        };
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("original-sfx-{i}"),
                &Change::UpsertSemanticSfx { cue },
            )
            .unwrap()
            .project;
    }
    let deliverable = project.deliverables[0].id;
    let prepared = prepare_semantic_sfx_av_audio(
        &service,
        project.id,
        &project.stamp(),
        deliverable,
        360,
        &root,
    )
    .unwrap();
    let clips = project
        .scenes
        .iter()
        .enumerate()
        .map(|(index, scene)| MltNativeSegmentEdit {
            segment_id: format!("owner-segment-{index}"),
            scene_ids: vec![scene.id],
            source_native_job_ref: format!("owner-native-render-{index}"),
            source_manifest_path: format!("render-{}{}", index + 1, "0".repeat(31))
                + "/artifact-manifest.json",
            source_manifest_sha256: "e".repeat(64),
            ffv1_output_path: format!("owner-s{index:02}-ffv1.mkv"),
            ffv1_max_bytes: 64 * 1024 * 1024,
            edit_asset_name: format!("Original {index}"),
            edit_clip_name: format!("Owner cut {index}"),
            start_frame: index as u64 * 180,
            source_in: 0,
            source_out_exclusive: 180,
        })
        .collect();
    let recipe = MltNativeVideoRecipe {
        project_resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
        deliverable_id: deliverable,
        input_sha256: "f".repeat(64),
        owner_output_namespace: "mw-mlt-test".into(),
        provider_profile: MltOwnedVideoProfile {
            width: 1920,
            height: 1080,
            fps_num: 30,
            fps_den: 1,
            progressive: true,
            sample_aspect_num: 1,
            sample_aspect_den: 1,
            display_aspect_num: 16,
            display_aspect_den: 9,
            colorspace: 709,
            audio_channels: 2,
        },
        sequence_name: "Original two-scene source".into(),
        video_track_name: "Owner native video-only edit".into(),
        clips,
        total_frames: 360,
        lossless_sequence_path: "source-verified-ffv1.mkv".into(),
        lossless_sequence_profile: "lossless".into(),
        final_mux_profile: "h264-aac-mp4".into(),
        scope: "owner-source-bound-mlt-semantic-edit-recipe-not-rendered".into(),
    };
    Source {
        project,
        recipe,
        wav: prepared.audio,
        proof: prepared.proof,
        owner,
        service,
    }
}

fn preflight(
    f: &Source,
) -> semwright_native_sdk::Result<
    motionwright_native::production::MltMultisegmentOriginalSfxReadiness,
> {
    preflight_multisegment_original_sfx_clock(&f.project, &f.recipe, &f.wav, &f.proof)
}

#[test]
fn real_owner_wav_cas_and_two_source_clips_preserve_all_360_frames() {
    let f = source();
    let before = f.project.clone();
    let ready = preflight(&f).unwrap();
    assert_eq!(ready.project_id, f.project.id);
    assert_eq!(ready.total_frames, 360);
    assert_eq!(ready.total_samples, 576_000);
    assert_eq!(
        ready.ordered_scene_ids,
        f.project.scenes.iter().map(|s| s.id).collect::<Vec<_>>()
    );
    assert_eq!(ready.original_cue_ids.len(), 2);
    assert_eq!(ready.original_asset_sha256, f.proof.original_asset_sha256);
    assert_eq!(ready.exact_mix_pcm_sha256, f.wav.sha256);
    assert!(ready.evidence_scope.ends_with("-NOT-native-mux"));
    assert_eq!(
        f.project, before,
        "Read-only source admission must not edit scenes or cues"
    );
    let wav = fs::read(
        f.owner
            .path()
            .join("owner-output")
            .join(&f.wav.relative_path),
    )
    .unwrap();
    assert_eq!(hex::encode(Sha256::digest(&wav)), f.wav.sha256);
    assert_eq!(wav.len(), 44 + 576_000 * 4);
    assert_eq!(&wav[..4], b"RIFF");
    assert_eq!(&wav[8..12], b"WAVE");
    assert_eq!(&wav[36..40], b"data");
    assert_eq!(
        u32::from_le_bytes(wav[40..44].try_into().unwrap()),
        576_000 * 4
    );
    let pcm = &wav[44..];
    let window = |start: usize, end: usize| &pcm[start * 1600 * 4..end * 1600 * 4];
    // Independent physical windows, including silence across the scene boundary.
    // These bytes come from the existing mixer; this test implements no mixer.
    for (start, end) in [(0, 6), (30, 200), (224, 360)] {
        assert!(window(start, end).iter().all(|byte| *byte == 0));
    }
    for (start, end) in [(9, 24), (203, 218)] {
        assert!(window(start, end).iter().any(|byte| *byte != 0));
    }
    let peak = pcm
        .as_chunks::<2>()
        .0
        .iter()
        .map(|sample| i16::from_le_bytes([sample[0], sample[1]]).unsigned_abs())
        .max()
        .unwrap();
    assert!(
        peak > 0 && peak < 4096,
        "Both admitted sources use -12 dB gain"
    );
    assert!(
        pcm.as_chunks::<4>()
            .0
            .iter()
            .any(|sample| sample[..2] != sample[2..])
    );
    assert!(f.owner.path().join("two-scene.sqlite3").is_file());
}

#[test]
fn reordered_trimmed_or_shifted_video_clips_fail_before_native_mux() {
    let mut f = source();
    let correct = f.recipe.clone();
    f.recipe.clips.swap(0, 1);
    assert!(preflight(&f).is_err());
    f.recipe = correct.clone();
    f.recipe.clips[1].start_frame = 179;
    assert!(preflight(&f).is_err());
    f.recipe = correct.clone();
    f.recipe.clips[1].source_in = 1;
    assert!(preflight(&f).is_err());
    f.recipe = correct.clone();
    f.recipe.clips[1].source_out_exclusive = 179;
    assert!(preflight(&f).is_err());
    f.recipe = correct.clone();
    f.recipe.clips.pop();
    assert!(preflight(&f).is_err());
    f.recipe = correct;
    f.recipe.total_frames = 359;
    assert!(preflight(&f).is_err());
}

#[test]
fn changed_owner_revision_audio_digest_or_original_source_cannot_pass() {
    let mut f = source();
    let correct = f.project.clone();
    f.project.revision += 1;
    assert!(preflight(&f).is_err());
    f.project = correct.clone();
    f.recipe.generation = Uuid::new_v4();
    assert!(preflight(&f).is_err());
    f.recipe.generation = f.project.generation;
    f.wav.sha256 = "a".repeat(64);
    assert!(preflight(&f).is_err());
    f.wav.sha256 = f.proof.exact_pcm_sha256.clone();
    f.proof.original_asset_sha256.reverse();
    assert!(preflight(&f).is_err());
    f.proof.original_asset_sha256.reverse();
    f.project.audio.sfx_cues[0].source_sha256 = "0".repeat(64);
    assert!(preflight(&f).is_err());

    // The exact same coherent metadata cannot admit changed physical WAV bytes.
    f.project = correct;
    let output = f.owner.path().join("owner-output");
    let path = output.join(&f.wav.relative_path);
    let mut bytes = fs::read(&path).unwrap();
    bytes[44 + 9600 * 4] ^= 1;
    fs::write(path, bytes).unwrap();
    assert!(
        verify_semantic_sfx_av_source(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            f.recipe.deliverable_id,
            f.recipe.total_frames,
            &f.wav,
            &output,
        )
        .is_err()
    );
}

#[test]
fn arbitrary_voice_reordered_partial_scenes_or_inexact_frame_rate_fail_closed() {
    let mut f = source();
    let correct = f.project.clone();
    f.project.deliverables[0].voice_track_id = Some(Uuid::new_v4());
    assert!(preflight(&f).is_err());
    f.project = correct.clone();
    f.project.deliverables[0].included_scene_ids = vec![f.project.scenes[0].id];
    assert!(preflight(&f).is_err());
    f.project = correct.clone();
    f.project.scenes[1].start = motionwright_domain::RationalTime::new(61, 10).unwrap();
    assert!(preflight(&f).is_err());
    f.project = correct.clone();
    f.recipe.provider_profile.fps_num = 29;
    assert!(preflight(&f).is_err());
    f.recipe.provider_profile.fps_num = 30;
    f.project.scenes.swap(0, 1);
    assert!(preflight(&f).is_err());
}
