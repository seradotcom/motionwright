//! Public saved-mix consumer contracts over real SQLite/CAS inputs.
//!
//! The transport and video carriers below are synthetic test doubles. They do
//! not run a Broker, renderer, encoder or decoder and establish no physical
//! audiovisual quality, listening result, speech or human acceptance.
#![cfg(unix)]

use motionwright_domain::{
    AlignmentEvidence, AudioCodec, BlendMode, CanvasNode, Change, CoordinateSpace,
    DeliveryAudioTargets, DeliveryRecipe, NodeStyle, OriginalMixRecipe, OutputColorSpace,
    OutputContainer, Project, RationalTime, RevisionStamp, TranscriptSegment, VideoCodec,
};
use motionwright_native::{
    assembly::preflight_multi_segment_mlt,
    film::{
        FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
        SceneFilmIntent, build_motion_canvas_segments,
    },
    mlt_edit_plan::mlt_native_video_recipe,
    original_mix::{PreparedOriginalMix, prepare_saved_original_mix},
    production::{
        MltAudioArtifact, MltAvMasterRequest, MltMultisegmentAvMasterRequest,
        MltPreparedMezzanines, MltVerifiedLosslessTimeline, MltVerifiedMezzanine,
        MotionCanvasRenderEvidence, MotionCanvasSegmentEvidence, ProductionConnection,
        ProductionCoordinator,
    },
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_native_sdk::{
    ErrorCode,
    types::{Execution, InvocationProvenance, SourceKind},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};
use tempfile::TempDir;
use uuid::Uuid;

const REQUEST: &str = "saved-mix-consumer";
const AUDIO_FRAMES: u32 = 52_800; // 33 video frames at 30 fps; 1.1 seconds.
const SINGLE_REJECTION: &str =
    "Staged soundtrack differs from the saved source mix or exact visual cut";
const MULTI_REJECTION: &str =
    "Native staged 48k stereo audio does not match the exact selected voice or saved mix";
const CLOCK_REJECTION: &str = "Measured voice and verified multi-segment video cut differ by more than one 48 kHz sample; no silent time-stretch, trim or padding";

#[derive(Clone, Copy)]
enum Route {
    Single,
    Multi,
}

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn permissions(path: &Path, mode: u32) {
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
}

fn wav(level: i16) -> Vec<u8> {
    let size = AUDIO_FRAMES * 4;
    let mut bytes = Vec::with_capacity(44 + size as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + size).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&192_000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&size.to_le_bytes());
    for _ in 0..AUDIO_FRAMES * 2 {
        bytes.extend_from_slice(&level.to_le_bytes());
    }
    bytes
}

fn apply(service: &StudioService, project: &mut Project, key: &str, change: Change) {
    *project = service
        .apply(project.id, &RevisionStamp::from(&*project), key, &change)
        .unwrap()
        .project;
}

fn rectangle() -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Synthetic source rectangle".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 400.0,
        y: 250.0,
        width: 160.0,
        height: 120.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some("#F5F5F2".into()),
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

// The fixed protocol double records every call, checks input file identities
// and produces explicitly synthetic carrier bytes. It never invokes media tools.
const TRANSPORT: &str = r#"#!/usr/bin/env python3
import hashlib, json, pathlib, sys
home = pathlib.Path(__file__).parent
root = home / "output"
command = sys.argv[7]
args = json.loads(sys.argv[9])
record = {"command": command, "args": args}
calls = home / "transport-calls.jsonl"
with calls.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
number = len(calls.read_text(encoding="utf-8").splitlines())
def artifact(name, content):
    path = root / name
    path.write_bytes(content)
    return {"root": "output", "path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
if command == "driver.mlt-video.frames.encode":
    raw = (root / args["manifest_path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == args["expected_manifest_sha256"]
    plan = json.loads(raw)["plan"]
    data = {
        "codec": "ffv1", "container": "matroska",
        "frame_count": plan["frame_count"], "width": plan["width"], "height": plan["height"],
        "fps_num": plan["fps"], "fps_den": plan["fps_denominator"],
        "media": {"video": True, "audio": False},
        "artifact": artifact(args["output_path"], b"synthetic FFV1 contract carrier; not encoded video"),
    }
elif command == "driver.mlt-video.av.mux":
    for kind in ("video", "audio"):
        content = (root / args[kind + "_path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == args[kind + "_sha256"]
    audio = (root / args["audio_path"]).read_bytes()
    data = {
        "profile": "h264-aac-mp4",
        "frame_count": args["frame_count"], "fps_num": args["fps_num"], "fps_den": args["fps_den"],
        "sample_rate": 48000, "channels": 2,
        "audio_sample_frames": (len(audio) - 44) // 4,
        "decoded_audio_sample_frames": (len(audio) - 44) // 4,
        "video_sha256": args["video_sha256"], "audio_sha256": args["audio_sha256"],
        "media": {"video": True, "audio": True, "width": args["width"], "height": args["height"],
                  "frames": args["frame_count"], "codecs": ["h264", "aac"]},
        "decoded_audio_media": {"audio": True, "video": False, "codecs": ["pcm_s16le"]},
        "artifact": artifact(args["output_path"], b"synthetic MP4 contract carrier; not encoded video"),
        "decoded_audio": artifact(args["output_path"] + ".decoded.wav", audio),
    }
elif command == "driver.audio-analysis.artifact.measure":
    # Protocol-only closed response. These fixed numbers are not computed
    # acoustic measurements and must never serve as physical meter evidence.
    raw = (root / args["file_name"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == args["expected_sha256"]
    assert args["layout"] == "stereo" and raw[0:4] == b"RIFF"
    frames = (len(raw) - 44) // 4
    channel = {"peak_millidbfs": -10000, "rms_millidbfs": -20000,
        "dc_offset": 0.01, "finite_samples": frames,
        "nonfinite_samples": 0, "out_of_range_samples": 0}
    data = {
        "artifact": {"file_name": args["file_name"], "sha256": args["expected_sha256"],
                     "bytes": len(raw), "format": "wav"},
        "loudness": {"schema_version": 1, "method": "libebur128", "version": "1.2.6",
            "frames": frames, "sample_rate": 48000, "channels": 2, "layout": "stereo",
            "nonfinite_samples": 0, "integrated_lufs_milli": -23000, "momentary_lufs_milli": -23100,
            "short_term_lufs_milli": None, "loudness_range_milli": None,
            "true_peak_millidbtp": -9000, "sample_peak_millidbfs": -10000,
            "true_peak_oversample": 4, "momentary_window_ms": 400, "short_term_window_ms": 3000,
            "unknown_reason": "one_or_more_windows_have_insufficient_frames"},
        "pcm_statistics": {"schema_version": 2, "method": "semwright-pcm-statistics-v2",
            "sample_rate": 48000, "frames": frames, "channels": [channel, channel],
            "peak_millidbfs": -10000, "rms_millidbfs": -20000,
            "silence_threshold_millidbfs": -90000, "silence_ranges": [],
            "silence_ranges_exhaustive": True, "entirely_silent": False,
            "nonfinite_samples": 0, "out_of_range_samples": 0},
        "exhaustive": True,
    }
    # G0 transport fixture only: configurable observations demonstrate that
    # source values cannot substitute for final decoded metric evidence.
    override = home / "synthetic-final-meter-values.json"
    if args["file_name"].startswith("mw-analysis-decoded-") and override.exists():
        values = json.loads(override.read_text(encoding="utf-8"))
        for key in ("integrated_lufs_milli", "true_peak_millidbtp"):
            if key in values:
                data["loudness"][key] = values[key]
else:
    raise AssertionError("Unexpected transport command: " + command)
execution = json.loads((home / "execution.json").read_text(encoding="utf-8"))
if command == "driver.audio-analysis.artifact.measure":
    execution["backend"] = "driver:audio-analysis"
    execution["provenance"]["provider"] = "driver:audio-analysis"
    execution["provenance"]["execution_provider"] = "driver:audio-analysis"
print(json.dumps({"ok": True, "request_id": "synthetic-ack-" + str(number), "command": command,
                  "data": data, "error": None, "execution": execution, "warnings": []}))
"#;

fn connection(root: &Path, resource: String) -> ProductionConnection {
    let output_root = root.join("output");
    fs::create_dir(&output_root).unwrap();
    permissions(&output_root, 0o700);
    let executable = root.join("synthetic-transport");
    fs::write(&executable, TRANSPORT).unwrap();
    permissions(&executable, 0o700);
    let socket = root.join("unused-broker.sock");
    let session_file = root.join("unused-session.json");
    fs::write(&socket, b"").unwrap();
    fs::write(&session_file, b"{}").unwrap();
    permissions(&socket, 0o600);
    permissions(&session_file, 0o600);
    let execution = Execution {
        backend: "driver:mlt-video".into(),
        duration_ms: 1,
        policy_decision: "allow".into(),
        fallbacks_attempted: vec![],
        provenance: Some(InvocationProvenance {
            provider: "driver:mlt-video".into(),
            source: SourceKind::Driver,
            provider_version: "synthetic-test-double".into(),
            capability_version: "synthetic-test-double".into(),
            descriptor_sha256: "ab".repeat(32),
            untrusted_metadata: false,
            catalog_revision: 1,
            execution_provider: Some("driver:mlt-video".into()),
            provider_generation: Some(1),
        }),
        dry_run: false,
    };
    fs::write(
        root.join("execution.json"),
        serde_json::to_vec(&execution).unwrap(),
    )
    .unwrap();
    let connection = ProductionConnection {
        schema: "motionwright-semwright-connection/1".into(),
        executable_sha256: digest(TRANSPORT.as_bytes()),
        executable,
        socket,
        session_file,
        output_root,
        resource,
    };
    connection.validate().unwrap();
    connection
}

struct Fixture {
    _temp: TempDir,
    root: PathBuf,
    database: PathBuf,
    service: StudioService,
    project: Project,
    options: FilmBuildOptions,
    rendered: MotionCanvasRenderEvidence,
    visual: Option<MltVerifiedLosslessTimeline>,
    connection: ProductionConnection,
    mix: Option<PreparedOriginalMix>,
    audio: MltAudioArtifact,
}

impl Fixture {
    fn new(route: Route, saved: bool, visual_frames: u32) -> Self {
        Self::with_semantic_sfx(route, saved, visual_frames, false)
    }

    fn with_semantic_sfx(
        route: Route,
        saved: bool,
        visual_frames: u32,
        semantic_sfx: bool,
    ) -> Self {
        Self::with_audio_targets(route, saved, visual_frames, semantic_sfx, None)
    }

    fn with_audio_targets(
        route: Route,
        saved: bool,
        visual_frames: u32,
        semantic_sfx: bool,
        targets: Option<DeliveryAudioTargets>,
    ) -> Self {
        let temp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(temp.path()).unwrap();
        permissions(&root, 0o700);
        let database = root.join("studio.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let mut project = service
            .create_project("Synthetic saved-mix consumer")
            .unwrap();
        let count = match route {
            Route::Single => 1,
            Route::Multi => 33,
        };
        assert_eq!(visual_frames % count, 0);
        for number in 0..count {
            apply(
                &service,
                &mut project,
                &format!("scene-{number}"),
                Change::AddScene {
                    name: format!("Source scene {number}"),
                    objective: "Exact source cut without native execution".into(),
                    duration_seconds: 1,
                },
            );
            let scene_id = project.scenes.last().unwrap().id;
            apply(
                &service,
                &mut project,
                &format!("duration-{number}"),
                Change::SetSceneDuration {
                    scene_id,
                    duration: RationalTime::new(i64::from(visual_frames / count), 30).unwrap(),
                },
            );
            apply(
                &service,
                &mut project,
                &format!("node-{number}"),
                Change::AddCanvasNode {
                    scene_id,
                    node: rectangle(),
                },
            );
        }
        let voice_path = root.join("voice.wav");
        fs::write(&voice_path, wav(2_000)).unwrap();
        project = service
            .import_voice_file(
                project.id,
                &RevisionStamp::from(&project),
                "voice",
                &voice_path,
                VoiceImportMetadata {
                    name: "voice.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Synthetic PCM; no speech claim".into(),
                },
            )
            .unwrap()
            .project;
        let voice_id = project.audio.voice_tracks[0].id;
        apply(
            &service,
            &mut project,
            "active",
            Change::SetActiveVoiceTrack { track_id: voice_id },
        );
        let mut assets = Vec::new();
        for (name, level) in [("music", 1_000_i16), ("sfx", 500_i16)]
            .into_iter()
            .filter(|_| saved)
        {
            let path = root.join(format!("{name}.wav"));
            fs::write(&path, wav(level)).unwrap();
            project = service
                .import_asset_file(
                    project.id,
                    &RevisionStamp::from(&project),
                    name,
                    &path,
                    format!("{name}.wav"),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            assets.push(
                project
                    .assets
                    .iter()
                    .find(|asset| asset.name == format!("{name}.wav"))
                    .unwrap()
                    .id,
            );
        }
        let mut profile = project.deliverables[0].clone();
        profile.width = 1920;
        profile.height = 1080;
        profile.frame_rate = RationalTime::new(30, 1).unwrap();
        profile.video_codec = VideoCodec::H264;
        profile.audio_codec = AudioCodec::Aac;
        profile.audio_sample_rate_hz = 48_000;
        profile.container = OutputContainer::Mp4;
        profile.color_space = OutputColorSpace::Rec709;
        profile.language = "en".into();
        profile.captions = false;
        profile.burn_in_captions = false;
        profile.voice_track_id = Some(voice_id);
        let profile_id = profile.id;
        apply(
            &service,
            &mut project,
            "profile",
            Change::UpsertDeliverable { profile },
        );
        apply(
            &service,
            &mut project,
            "manual-window",
            Change::AddTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::now_v7(),
                    voice_track_id: voice_id,
                    start: RationalTime::new(1, 10).unwrap(),
                    end: RationalTime::new(1, 1).unwrap(),
                    text: "Synthetic manual timing; not recognized speech".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            },
        );
        if saved {
            apply(
                &service,
                &mut project,
                "saved-recipe",
                Change::SetDeliveryRecipe {
                    deliverable_profile_id: profile_id,
                    recipe: Some(DeliveryRecipe {
                        audio_targets: None,
                        original_mix: Some(OriginalMixRecipe {
                            music_asset_id: Some(assets[0]),
                            sfx_asset_id: Some(assets[1]),
                            sfx_gain_db: -8.0,
                            duck_attenuation_db: 14.0,
                            duck_attack_samples: 2_400,
                            duck_release_samples: 4_800,
                            voice_envelope: Default::default(),
                            music_envelope: Default::default(),
                            sfx_envelope: Default::default(),
                        }),
                        ..Default::default()
                    }),
                },
            );
        }
        if semantic_sfx {
            let mut bytes = wav(1_000);
            let size = 15_u32 * 1_600 * 4;
            bytes.truncate(44 + size as usize);
            bytes[4..8].copy_from_slice(&(36 + size).to_le_bytes());
            bytes[40..44].copy_from_slice(&size.to_le_bytes());
            let path = root.join("semantic-cue.wav");
            fs::write(&path, &bytes).unwrap();
            project = service
                .import_asset_file(
                    project.id,
                    &project.stamp(),
                    "import-semantic-cue",
                    &path,
                    "semantic-cue.wav".into(),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            let source_asset_id = project
                .assets
                .iter()
                .find(|asset| asset.name == "semantic-cue.wav")
                .unwrap()
                .id;
            let scene_id = project.scenes[0].id;
            apply(
                &service,
                &mut project,
                "author-semantic-cue",
                Change::UpsertSemanticSfx {
                    cue: motionwright_domain::SemanticSfxCue {
                        schema: motionwright_domain::SFX_CUE_SCHEMA.into(),
                        id: Uuid::now_v7(),
                        scene_id,
                        event: motionwright_domain::SfxEvent::SceneReveal,
                        anchor: motionwright_domain::SfxAnchor::SceneStart,
                        offset_frames: 0,
                        source_asset_id,
                        source_sha256: digest(&bytes),
                        source_frames: 15,
                        gain_milli_db: 0,
                        attack_frames: 0,
                        release_frames: 1,
                        tail_policy: motionwright_domain::SfxTailPolicy::ContinueAcrossCuts,
                        original_source_attestation:
                            "Owned synthetic PCM transport fixture, not speech".into(),
                    },
                },
            );
        }
        if let Some(targets) = targets {
            let mut recipe = project
                .production_design
                .delivery_recipes
                .get(&profile_id)
                .cloned()
                .unwrap_or_default();
            recipe.audio_targets = Some(targets);
            apply(
                &service,
                &mut project,
                "save-qc-targets",
                Change::SetDeliveryRecipe {
                    deliverable_profile_id: profile_id,
                    recipe: Some(recipe),
                },
            );
        }
        project.validate().unwrap();
        assert_eq!(project.audio.mix.voice_gain_db, 0.0);
        let mix = saved.then(|| {
            prepare_saved_original_mix(
                &service,
                project.id,
                &RevisionStamp::from(&project),
                profile_id,
            )
            .unwrap()
        });
        let verified_voice = service
            .verified_master_voice(project.id, &RevisionStamp::from(&project), voice_id)
            .unwrap();
        let voice_bytes = fs::read(&verified_voice.path).unwrap();
        assert_eq!(verified_voice.sha256, digest(&voice_bytes));
        assert_eq!(voice_bytes, wav(2_000));
        if let Some(mix) = &mix {
            assert_eq!(mix.plan().sample_frames, u64::from(AUDIO_FRAMES));
            assert_ne!(mix.wav_sha256(), verified_voice.sha256);
            assert!(!mix.evidence().owner_release_approved);
            assert!(!mix.evidence().independently_verified_lufs);
            assert!(!mix.evidence().independently_verified_true_peak);
        }
        let connection = connection(&root, project.resource_key());
        let audio_bytes = mix
            .as_ref()
            .map_or(voice_bytes.as_slice(), PreparedOriginalMix::wav_bytes);
        fs::write(
            connection.output_root.join("selected-audio.wav"),
            audio_bytes,
        )
        .unwrap();
        let audio = MltAudioArtifact {
            relative_path: "selected-audio.wav".into(),
            sha256: digest(audio_bytes),
            sample_rate: 48_000,
            channels: 2,
        };
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let native = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
        assert_eq!(
            native.len(),
            match route {
                Route::Single => 1,
                Route::Multi => 2,
            }
        );
        let segments = native.iter().enumerate().map(|(index, segment)| {
            let directory = format!("render-{index:032x}");
            let folder = connection.output_root.join(&directory);
            fs::create_dir(&folder).unwrap();
            let bytes = serde_json::to_vec(&json!({
                "plan": {
                    "width": 1920, "height": 1080, "fps": 30, "fps_denominator": 1,
                    "frame_count": segment.frame_count, "first_frame": 0,
                    "end_frame_exclusive": segment.frame_count,
                },
                "frames": (0..segment.frame_count).map(|frame| json!({
                    "index": frame, "file": format!("frames/{frame:06}.png"),
                    "sha256": "a".repeat(64), "bytes": 128,
                })).collect::<Vec<_>>(),
            })).unwrap();
            fs::write(folder.join("artifact-manifest.json"), &bytes).unwrap();
            MotionCanvasSegmentEvidence {
                segment_id: segment.id.clone(),
                scene_ids: segment.scene_ids.clone(),
                frame_count: segment.frame_count,
                plan_ref: format!("synthetic-plan-{index}"),
                fingerprint: format!("synthetic-source-{index}"),
                job_ref: format!("synthetic-job-{index}"),
                artifact: json!({
                    "directory": directory,
                    "manifest": format!("{directory}/artifact-manifest.json"),
                    "manifest_sha256": digest(&bytes),
                    "frame_count": segment.frame_count,
                }),
                verification: json!({
                    "report": {"execution_status": "completed", "support_level": "native"},
                    "measurement": {"findings": [], "validation": {"checks": [{"verdict": "PASS"}]}},
                }),
            }
        }).collect();
        let rendered = MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id: profile_id,
            frame_rate: options.frame_rate,
            segments,
        };
        let visual = match route {
            Route::Single => None,
            Route::Multi => {
                let checked = preflight_multi_segment_mlt(
                    &project,
                    profile_id,
                    &options,
                    &rendered,
                    &connection.output_root,
                )
                .unwrap();
                let recipe = mlt_native_video_recipe(&checked).unwrap();
                let media = recipe
                    .clips
                    .iter()
                    .enumerate()
                    .map(|(index, clip)| {
                        let bytes = format!("synthetic verified segment {index}; not encoded FFV1")
                            .into_bytes();
                        fs::write(connection.output_root.join(&clip.ffv1_output_path), &bytes)
                            .unwrap();
                        MltVerifiedMezzanine {
                            segment_id: clip.segment_id.clone(),
                            scene_ids: clip.scene_ids.clone(),
                            output_start_frame: clip.start_frame,
                            frame_count: clip.source_out_exclusive,
                            native_render_job_ref: clip.source_native_job_ref.clone(),
                            source_manifest_sha256: clip.source_manifest_sha256.clone(),
                            artifact_path: clip.ffv1_output_path.clone(),
                            artifact_sha256: digest(&bytes),
                            artifact_bytes: bytes.len() as u64,
                        }
                    })
                    .collect();
                let source = MltPreparedMezzanines {
                    project_resource: project.resource_key(),
                    generation: project.generation,
                    revision: project.revision,
                    deliverable_id: profile_id,
                    input_sha256: recipe.input_sha256.clone(),
                    fps_num: 30,
                    fps_den: 1,
                    total_frames: recipe.total_frames,
                    verified_video_segments: media,
                    evidence_scope: "real-source-ffv1-segments-not-composited".into(),
                };
                let name = format!(
                    "{}-sequence-video-only-ffv1.mkv",
                    recipe.owner_output_namespace
                );
                let bytes = b"synthetic sequence contract carrier; not encoded FFV1";
                fs::write(connection.output_root.join(&name), bytes).unwrap();
                Some(MltVerifiedLosslessTimeline {
                    project_resource: project.resource_key(),
                    generation: project.generation,
                    revision: project.revision,
                    deliverable_id: profile_id,
                    recipe_sha256: recipe.input_sha256,
                    frame_count: recipe.total_frames,
                    native_frame_count_observed: Some(recipe.total_frames),
                    transport_pcm_audio: false,
                    native_render_profile: "lossless-video-only".into(),
                    provider_project_cleanup: "not_requested_requires_foreground_broker_consent"
                        .into(),
                    native_job_ref: "render:synthetic-sequence".into(),
                    provider_revision: "synthetic-test-double".into(),
                    artifact_path: name,
                    artifact_sha256: digest(bytes),
                    artifact_bytes: bytes.len() as u64,
                    source,
                    evidence_scope:
                        "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
                            .into(),
                })
            }
        };
        assert_eq!(service.project(project.id).unwrap(), project);
        assert!(
            service
                .production_receipts(project.id, 100)
                .unwrap()
                .is_empty()
        );
        Self {
            _temp: temp,
            root,
            database,
            service,
            project,
            options,
            rendered,
            visual,
            connection,
            mix,
            audio,
        }
    }

    fn substitute(&mut self, bytes: &[u8]) {
        assert_eq!(bytes.len(), 44 + AUDIO_FRAMES as usize * 4);
        assert_ne!(digest(bytes), self.mix.as_ref().unwrap().wav_sha256());
        fs::write(
            self.connection.output_root.join(&self.audio.relative_path),
            bytes,
        )
        .unwrap();
        self.audio.sha256 = digest(bytes);
    }

    fn calls(&self) -> Vec<Value> {
        let path = self.root.join("transport-calls.jsonl");
        if !path.exists() {
            return Vec::new();
        }
        fs::read_to_string(path)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    fn assert_source_and_receipts_reopen(&self) {
        assert_eq!(self.service.project(self.project.id).unwrap(), self.project);
        let receipts = self
            .service
            .production_receipts(self.project.id, 100)
            .unwrap();
        let reopened = StudioService::open(&self.database).unwrap();
        assert_eq!(reopened.project(self.project.id).unwrap(), self.project);
        assert_eq!(
            reopened.production_receipts(self.project.id, 100).unwrap(),
            receipts
        );
    }

    fn expected_readiness_fingerprint(&self) -> String {
        let visual = self.visual.as_ref().unwrap();
        let track = &self.project.audio.voice_tracks[0];
        let original = digest(
            &serde_json::to_vec(&(
                self.project.resource_key(),
                self.project.generation,
                self.project.revision,
                self.project.deliverables[0].id,
                track.id,
                &track.source_sha256,
                &visual.recipe_sha256,
                visual.frame_count,
                30_u32,
                1_u32,
                &visual.source.verified_video_segments,
            ))
            .unwrap(),
        );
        match &self.mix {
            Some(mix) => digest(
                &serde_json::to_vec(&(
                    "native-saved-original-mix-v1",
                    &original,
                    mix.audio_inputs_sha256(),
                    &mix.evidence().source_plan_sha256,
                    mix.wav_sha256(),
                ))
                .unwrap(),
            ),
            None => original,
        }
    }
}

fn snapshot(root: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
    fn visit(root: &Path, directory: &Path, values: &mut BTreeMap<PathBuf, Option<Vec<u8>>>) {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            let key = path.strip_prefix(root).unwrap().to_path_buf();
            if metadata.is_dir() {
                values.insert(key, None);
                visit(root, &path, values);
            } else {
                assert!(metadata.is_file());
                values.insert(key, Some(fs::read(path).unwrap()));
            }
        }
    }
    let mut values = BTreeMap::new();
    visit(root, root, &mut values);
    values
}

async fn succeeds(f: &Fixture, route: Route) {
    let coordinator = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    let expected = RevisionStamp::from(&f.project);
    match route {
        Route::Single => {
            let result = coordinator
                .assemble_mlt_av_master(
                    f.project.id,
                    &expected,
                    MltAvMasterRequest {
                        request_id: REQUEST,
                        deliverable_id: f.project.deliverables[0].id,
                        motion: &f.rendered,
                        audio: &f.audio,
                        sync: None,
                    },
                )
                .await
                .unwrap();
            assert_eq!(result.source_audio.sha256, f.audio.sha256);
            assert_eq!(result.source_audio.relative_path, f.audio.relative_path);
            assert_eq!(result.frame_count, 33);
        }
        Route::Multi => {
            let result = coordinator
                .assemble_native_mlt_multisegment_av_master(
                    f.project.id,
                    &expected,
                    MltMultisegmentAvMasterRequest {
                        request_id: REQUEST,
                        deliverable_id: f.project.deliverables[0].id,
                        options: &f.options,
                        rendered: &f.rendered,
                        visual: f.visual.as_ref().unwrap(),
                        audio: &f.audio,
                    },
                )
                .await
                .unwrap();
            assert_eq!(result.measured_voice.audio_sha256, f.audio.sha256);
            assert_eq!(
                result.measured_voice.audio_pcm_frames,
                u64::from(AUDIO_FRAMES)
            );
            assert_eq!(
                result.measured_voice.source_fingerprint,
                f.expected_readiness_fingerprint()
            );
            assert_eq!(result.staged_audio.sha256, f.audio.sha256);
            assert_eq!(result.frame_count, 33);
            if f.mix.is_none() {
                assert_eq!(
                    result.measured_voice.evidence_scope,
                    "verified-owner-audio-ffv1-inputs-not-muxed-mp4"
                );
            }
        }
    }
    let calls = f.calls();
    let commands: &[(&str, &str)] = match route {
        Route::Single => &[
            ("driver.mlt-video.frames.encode", "mlt:frames-encode"),
            ("driver.mlt-video.av.mux", "mlt:av-mux"),
        ],
        Route::Multi => &[("driver.mlt-video.av.mux", "mlt:multisegment-av-mux")],
    };
    assert_eq!(calls.len(), commands.len());
    let receipts = f.service.production_receipts(f.project.id, 100).unwrap();
    assert_eq!(receipts.len(), commands.len() * 2);
    for (index, (call, (command, suffix))) in calls.iter().zip(commands).enumerate() {
        assert_eq!(call["command"], *command);
        let request_id = format!("{REQUEST}:{suffix}");
        let request_sha = digest(&serde_json::to_vec(&(*command, &call["args"])).unwrap());
        let selected: Vec<_> = receipts
            .iter()
            .filter(|r| r.request_id == request_id)
            .collect();
        assert_eq!(selected.len(), 2);
        for stage in ["dispatching", "completed"] {
            let row = selected.iter().find(|r| r.stage == stage).unwrap();
            assert_eq!(row.command, *command);
            assert_eq!(row.request_sha256, request_sha);
            assert_eq!(row.generation, f.project.generation);
            assert_eq!(row.revision, f.project.revision);
            if stage == "completed" {
                assert_eq!(
                    row.payload["broker_request_id"],
                    format!("synthetic-ack-{}", index + 1)
                );
            }
        }
    }
    let mux = calls.last().unwrap();
    assert_eq!(mux["args"]["audio_sha256"], f.audio.sha256);
    assert_eq!(mux["args"]["audio_path"], f.audio.relative_path);
    let source_sha = &f.project.audio.voice_tracks[0].source_sha256;
    if let Some(mix) = &f.mix {
        assert_eq!(f.audio.sha256, mix.wav_sha256());
        assert_ne!(&f.audio.sha256, source_sha);
    } else {
        assert_eq!(&f.audio.sha256, source_sha);
    }
    f.assert_source_and_receipts_reopen();
}

async fn rejects(f: &Fixture, route: Route, code: ErrorCode, message: &str) {
    let before_output = snapshot(&f.connection.output_root);
    let before_receipts = f.service.production_receipts(f.project.id, 100).unwrap();
    assert!(before_receipts.is_empty());
    let coordinator = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    let expected = RevisionStamp::from(&f.project);
    let result = match route {
        Route::Single => coordinator
            .assemble_mlt_av_master(
                f.project.id,
                &expected,
                MltAvMasterRequest {
                    request_id: REQUEST,
                    deliverable_id: f.project.deliverables[0].id,
                    motion: &f.rendered,
                    audio: &f.audio,
                    sync: None,
                },
            )
            .await
            .map(|_| ()),
        Route::Multi => coordinator
            .assemble_native_mlt_multisegment_av_master(
                f.project.id,
                &expected,
                MltMultisegmentAvMasterRequest {
                    request_id: REQUEST,
                    deliverable_id: f.project.deliverables[0].id,
                    options: &f.options,
                    rendered: &f.rendered,
                    visual: f.visual.as_ref().unwrap(),
                    audio: &f.audio,
                },
            )
            .await
            .map(|_| ()),
    };
    assert!(
        f.calls().is_empty(),
        "Input rejection reached the provider: {result:?}"
    );
    let error = result.expect_err("Wrong source or clock must reject before dispatch");
    assert_eq!(error.code, code);
    assert_eq!(error.message, message);
    assert!(error.outcome_known);
    assert_eq!(snapshot(&f.connection.output_root), before_output);
    assert_eq!(
        f.service.production_receipts(f.project.id, 100).unwrap(),
        before_receipts
    );
    f.assert_source_and_receipts_reopen();
}

#[tokio::test]
async fn single_saved_mix_supplies_exact_cas_pcm_to_mux() {
    succeeds(&Fixture::new(Route::Single, true, 33), Route::Single).await;
}

#[tokio::test]
async fn multisegment_saved_mix_supplies_exact_cas_pcm_to_mux() {
    succeeds(&Fixture::new(Route::Multi, true, 33), Route::Multi).await;
}

#[tokio::test]
async fn single_voice_only_keeps_existing_master_handoff() {
    succeeds(&Fixture::new(Route::Single, false, 33), Route::Single).await;
}

#[tokio::test]
async fn multisegment_voice_only_keeps_existing_master_handoff() {
    succeeds(&Fixture::new(Route::Multi, false, 33), Route::Multi).await;
}

#[tokio::test]
async fn single_saved_mix_rejects_other_valid_wav_before_provider() {
    for level in [2_000, 777] {
        let mut fixture = Fixture::new(Route::Single, true, 33);
        fixture.substitute(&wav(level));
        rejects(
            &fixture,
            Route::Single,
            ErrorCode::StaleReference,
            SINGLE_REJECTION,
        )
        .await;
    }
}

#[tokio::test]
async fn multisegment_saved_mix_rejects_voice_substitution_before_provider() {
    for level in [2_000, 777] {
        let mut fixture = Fixture::new(Route::Multi, true, 33);
        fixture.substitute(&wav(level));
        rejects(
            &fixture,
            Route::Multi,
            ErrorCode::StaleReference,
            MULTI_REJECTION,
        )
        .await;
    }
}

#[tokio::test]
async fn single_saved_mix_rejects_clock_mismatch_before_provider() {
    for visual_frames in [32, 34] {
        rejects(
            &Fixture::new(Route::Single, true, visual_frames),
            Route::Single,
            ErrorCode::StaleReference,
            SINGLE_REJECTION,
        )
        .await;
    }
}

#[tokio::test]
async fn multisegment_saved_mix_rejects_clock_mismatch_before_provider() {
    rejects(
        &Fixture::new(Route::Multi, true, 66),
        Route::Multi,
        ErrorCode::Unsupported,
        CLOCK_REJECTION,
    )
    .await;
}

#[tokio::test]
async fn single_semantic_sfx_saved_bus_reaches_mux_and_rejects_voice_only_substitution() {
    succeeds(
        &Fixture::with_semantic_sfx(Route::Single, true, 33, true),
        Route::Single,
    )
    .await;
    let mut changed = Fixture::with_semantic_sfx(Route::Single, true, 33, true);
    changed.substitute(&wav(2_000));
    rejects(
        &changed,
        Route::Single,
        ErrorCode::StaleReference,
        SINGLE_REJECTION,
    )
    .await;
}

#[tokio::test]
async fn multisegment_semantic_sfx_saved_bus_reaches_mux_and_rejects_voice_only_substitution() {
    succeeds(
        &Fixture::with_semantic_sfx(Route::Multi, true, 33, true),
        Route::Multi,
    )
    .await;
    let mut changed = Fixture::with_semantic_sfx(Route::Multi, true, 33, true);
    changed.substitute(&wav(2_000));
    rejects(
        &changed,
        Route::Multi,
        ErrorCode::StaleReference,
        MULTI_REJECTION,
    )
    .await;
}

#[tokio::test]
async fn authored_semantic_sfx_without_a_saved_mix_rejects_before_both_providers() {
    rejects(
        &Fixture::with_semantic_sfx(Route::Single, false, 33, true),
        Route::Single,
        ErrorCode::Unsupported,
        "Native master cannot omit authored gain/SFX or claim unmeasured loudness targets",
    )
    .await;
    rejects(&Fixture::with_semantic_sfx(Route::Multi, false, 33, true), Route::Multi,
        ErrorCode::Unsupported, "Native master cannot omit a saved mix or replace authored semantic SFX with a voice-only WAV").await;
}

#[tokio::test]
async fn changed_semantic_cas_source_rejects_before_both_native_providers() {
    for route in [Route::Single, Route::Multi] {
        let fixture = Fixture::with_semantic_sfx(route, true, 33, true);
        let cue = &fixture.project.audio.sfx_cues[0];
        let path = fixture
            .service
            .store()
            .lock()
            .verified_blob_path(&cue.source_sha256)
            .unwrap();
        fs::write(path, b"changed source; not the admitted WAV").unwrap();
        rejects(
            &fixture,
            route,
            ErrorCode::StaleReference,
            "Selected semantic SFX WAV failed current project/CAS verification",
        )
        .await;
    }
}

#[cfg(target_os = "linux")]
async fn measured_result(
    f: &Fixture,
    route: Route,
) -> semwright_native_sdk::Result<
    motionwright_native::production::VerifiedOriginalMixAudioMeasurements,
> {
    let coordinator = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    match route {
        Route::Single => coordinator
            .assemble_mlt_av_master_with_audio_analysis(
                f.project.id,
                &f.project.stamp(),
                MltAvMasterRequest {
                    request_id: REQUEST,
                    deliverable_id: f.project.deliverables[0].id,
                    motion: &f.rendered,
                    audio: &f.audio,
                    sync: None,
                },
            )
            .await
            .map(|result| result.audio_measurements),
        Route::Multi => coordinator
            .assemble_native_mlt_multisegment_av_master_with_audio_analysis(
                f.project.id,
                &f.project.stamp(),
                MltMultisegmentAvMasterRequest {
                    request_id: REQUEST,
                    deliverable_id: f.project.deliverables[0].id,
                    options: &f.options,
                    rendered: &f.rendered,
                    visual: f.visual.as_ref().unwrap(),
                    audio: &f.audio,
                },
            )
            .await
            .map(|result| result.audio_measurements),
    }
}

#[cfg(target_os = "linux")]
async fn measured_succeeds(f: &Fixture, route: Route) {
    let proof = measured_result(f, route).await.unwrap();
    assert_eq!(
        proof.delivery_audio_qc().status(),
        motionwright_native::production::DeliveryAudioQcStatus::NotRequested
    );
    assert_eq!(proof.source().loudness().frames, u64::from(AUDIO_FRAMES));
    assert_eq!(proof.decoded().loudness().frames, u64::from(AUDIO_FRAMES));
    let calls = f.calls();
    let measured: Vec<_> = calls
        .iter()
        .filter(|call| call["command"] == "driver.audio-analysis.artifact.measure")
        .collect();
    assert_eq!(measured.len(), 2);
    assert_ne!(
        measured[0]["args"]["file_name"],
        measured[1]["args"]["file_name"]
    );
    assert_eq!(measured[0]["args"]["expected_sha256"], f.audio.sha256);
    assert_eq!(measured[1]["args"]["expected_sha256"], f.audio.sha256);
    let commands: Vec<_> = calls
        .iter()
        .map(|call| call["command"].as_str().unwrap())
        .collect();
    assert_eq!(
        commands,
        match route {
            Route::Single => vec![
                "driver.audio-analysis.artifact.measure",
                "driver.mlt-video.frames.encode",
                "driver.mlt-video.av.mux",
                "driver.audio-analysis.artifact.measure"
            ],
            Route::Multi => vec![
                "driver.audio-analysis.artifact.measure",
                "driver.mlt-video.av.mux",
                "driver.audio-analysis.artifact.measure"
            ],
        }
    );
    let mux = calls
        .iter()
        .find(|call| call["command"] == "driver.mlt-video.av.mux")
        .unwrap();
    assert_eq!(mux["args"]["audio_sha256"], f.audio.sha256);
    let before = f.service.production_receipts(f.project.id, 100).unwrap();
    let reopened = StudioService::open(&f.database).unwrap();
    let current = ProductionCoordinator::new(reopened.clone(), f.connection.clone()).unwrap();
    proof.verify_current(&current).unwrap();
    assert_eq!(f.calls(), calls);
    assert_eq!(
        reopened.production_receipts(f.project.id, 100).unwrap(),
        before
    );
    assert_eq!(reopened.project(f.project.id).unwrap(), f.project);
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn measured_single_saved_semantic_mix_uses_actual_coordinator_two_phase_handoff() {
    measured_succeeds(
        &Fixture::with_semantic_sfx(Route::Single, true, 33, true),
        Route::Single,
    )
    .await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn measured_multisegment_saved_semantic_mix_uses_actual_coordinator_two_phase_handoff() {
    measured_succeeds(
        &Fixture::with_semantic_sfx(Route::Multi, true, 33, true),
        Route::Multi,
    )
    .await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn measured_voice_only_both_coordinators_preserve_original_wav_without_mix_assets() {
    for route in [Route::Single, Route::Multi] {
        let fixture = Fixture::new(route, false, 33);
        assert_eq!(fixture.project.assets.len(), 1);
        assert!(
            fixture
                .project
                .production_design
                .delivery_recipes
                .is_empty()
        );
        measured_succeeds(&fixture, route).await;
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn measured_consumers_reject_substituted_pcm_and_wrong_clock_before_any_provider() {
    for route in [Route::Single, Route::Multi] {
        for wrong_clock in [false, true] {
            let mut fixture = Fixture::new(route, true, if wrong_clock { 66 } else { 33 });
            if !wrong_clock {
                fixture.substitute(&wav(2_000));
            }
            let before = snapshot(&fixture.connection.output_root);
            let receipts = fixture
                .service
                .production_receipts(fixture.project.id, 100)
                .unwrap();
            assert_eq!(
                measured_result(&fixture, route).await.err().unwrap().code,
                ErrorCode::StaleReference
            );
            assert!(fixture.calls().is_empty());
            assert_eq!(snapshot(&fixture.connection.output_root), before);
            assert_eq!(
                fixture
                    .service
                    .production_receipts(fixture.project.id, 100)
                    .unwrap(),
                receipts
            );
            fixture.assert_source_and_receipts_reopen();
        }
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn profile_targets_reject_both_unmeasured_producers_before_dispatch_and_preserve_journal() {
    for route in [Route::Single, Route::Multi] {
        for saved in [false, true] {
            let fixture = Fixture::with_audio_targets(
                route,
                saved,
                33,
                saved,
                Some(DeliveryAudioTargets {
                    target_lufs: Some(-23.0),
                    target_true_peak_dbtp: Some(-1.0),
                }),
            );
            rejects(&fixture, route, ErrorCode::Unsupported,
                "Delivery audio targets require the measured native master route and final decoded PCM16 QC").await;
        }
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn targeted_voice_only_and_semantic_mix_both_coordinators_use_final_qc_and_reopen_without_dispatch()
 {
    use motionwright_native::production::DeliveryAudioQcStatus;
    for route in [Route::Single, Route::Multi] {
        for saved in [false, true] {
            let targets = DeliveryAudioTargets {
                target_lufs: Some(-23.0),
                target_true_peak_dbtp: Some(-1.0),
            };
            let fixture =
                Fixture::with_audio_targets(route, saved, 33, saved, Some(targets.clone()));
            if !saved {
                assert_eq!(fixture.project.assets.len(), 1);
                assert!(
                    fixture.project.production_design.delivery_recipes
                        [&fixture.project.deliverables[0].id]
                        .original_mix
                        .is_none()
                );
            }
            let proof = measured_result(&fixture, route).await.unwrap();
            assert_eq!(
                proof.delivery_audio_qc().status(),
                DeliveryAudioQcStatus::WithinTargets
            );
            assert_eq!(proof.delivery_audio_qc().targets(), Some(&targets));
            assert_eq!(proof.source().loudness().frames, u64::from(AUDIO_FRAMES));
            assert_eq!(proof.decoded().loudness().frames, u64::from(AUDIO_FRAMES));
            let calls = fixture.calls();
            assert_eq!(
                calls
                    .iter()
                    .filter(|call| call["command"] == "driver.audio-analysis.artifact.measure")
                    .count(),
                2
            );
            assert_eq!(
                calls
                    .iter()
                    .filter(|call| call["command"] == "driver.mlt-video.av.mux")
                    .count(),
                1
            );
            let mux = calls
                .iter()
                .find(|call| call["command"] == "driver.mlt-video.av.mux")
                .unwrap();
            assert_eq!(mux["args"]["audio_sha256"], fixture.audio.sha256);
            let receipts = fixture
                .service
                .production_receipts(fixture.project.id, 100)
                .unwrap();
            let reopened = StudioService::open(&fixture.database).unwrap();
            let current =
                ProductionCoordinator::new(reopened.clone(), fixture.connection.clone()).unwrap();
            proof.verify_current(&current).unwrap();
            assert_eq!(fixture.calls(), calls);
            assert_eq!(
                reopened
                    .production_receipts(fixture.project.id, 100)
                    .unwrap(),
                receipts
            );
            assert_eq!(
                reopened.project(fixture.project.id).unwrap(),
                fixture.project
            );
        }
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn final_qc_outside_or_unavailable_is_preserved_despite_passing_source_measurement() {
    use motionwright_native::production::DeliveryAudioQcStatus::{OutsideTargets, Unavailable};
    for route in [Route::Single, Route::Multi] {
        for (values, expected) in [
            (
                json!({"integrated_lufs_milli": -22799, "true_peak_millidbtp": -1000}),
                OutsideTargets,
            ),
            (
                json!({"integrated_lufs_milli": -23000, "true_peak_millidbtp": -999}),
                OutsideTargets,
            ),
            (
                json!({"integrated_lufs_milli": null, "true_peak_millidbtp": -1000}),
                Unavailable,
            ),
        ] {
            let fixture = Fixture::with_audio_targets(
                route,
                false,
                33,
                false,
                Some(DeliveryAudioTargets {
                    target_lufs: Some(-23.0),
                    target_true_peak_dbtp: Some(-1.0),
                }),
            );
            fs::write(
                fixture.root.join("synthetic-final-meter-values.json"),
                serde_json::to_vec(&values).unwrap(),
            )
            .unwrap();
            let proof = measured_result(&fixture, route).await.unwrap();
            assert_eq!(
                proof.source().loudness().integrated_lufs_milli,
                Some(-23_000)
            );
            assert_eq!(proof.source().loudness().true_peak_millidbtp, Some(-9_000));
            assert_eq!(proof.delivery_audio_qc().status(), expected);
            let calls = fixture.calls();
            let receipts = fixture
                .service
                .production_receipts(fixture.project.id, 100)
                .unwrap();
            let reopened = StudioService::open(&fixture.database).unwrap();
            let coordinator =
                ProductionCoordinator::new(reopened.clone(), fixture.connection.clone()).unwrap();
            proof.verify_current(&coordinator).unwrap();
            assert_eq!(fixture.calls(), calls);
            assert_eq!(
                reopened
                    .production_receipts(fixture.project.id, 100)
                    .unwrap(),
                receipts
            );
            assert_eq!(
                reopened.project(fixture.project.id).unwrap(),
                fixture.project
            );
        }
    }
}
