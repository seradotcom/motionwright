//! Negative coverage of the public measured-voice master request.
//!
//! This fixture has no Broker, renderer or native media producer. Its transport
//! executable records any unexpected invocation and exits immediately.
//! Unproduced carriers must not be inspected before the saved narration guard.
//! Exact diagnostics prevent an unrelated artifact rejection from passing.
//! Synthetic PCM admission does not establish speech or successful alignment.

#![cfg(unix)]

mod production {
    mod tests {
        use motionwright_domain::{
            AlignmentEvidence, AudioCodec, BlendMode, CanvasNode, Change, CoordinateSpace,
            NodeStyle, OutputColorSpace, OutputContainer, Project, RationalTime, RevisionStamp,
            TranscriptSegment, VideoCodec,
        };
        use motionwright_native::{
            film::{
                FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
                SceneFilmIntent, build_motion_canvas_segments,
            },
            production::{
                MltAudioArtifact, MltMultisegmentAvMasterRequest, MltPreparedMezzanines,
                MltVerifiedLosslessTimeline, MotionCanvasRenderEvidence, ProductionConnection,
                ProductionCoordinator,
            },
        };
        use motionwright_service::{StudioService, VoiceImportMetadata};
        use semwright_media_time::Rate;
        use semwright_motion_authoring::{Archetype, NarrativeRole};
        use semwright_native_sdk::ErrorCode;
        use sha2::{Digest, Sha256};
        use std::{
            collections::{BTreeMap, BTreeSet},
            fs,
            os::unix::fs::PermissionsExt,
            path::{Path, PathBuf},
        };
        use uuid::Uuid;

        #[derive(Clone, Copy)]
        enum Case {
            MissingVoice,
            UnknownAlignment,
        }

        impl Case {
            fn request_id(self) -> &'static str {
                match self {
                    Self::MissingVoice => "master-missing-voice",
                    Self::UnknownAlignment => "master-unknown-alignment",
                }
            }

            fn expected_message(self) -> &'static str {
                match self {
                    Self::MissingVoice => {
                        "Native caption burn-in requires one explicitly selected voice track"
                    }
                    Self::UnknownAlignment => {
                        "UNKNOWN caption timing cannot become a visible native cue"
                    }
                }
            }
        }

        fn time(num: i64) -> RationalTime {
            RationalTime { num, den: 1 }
        }

        fn digest(bytes: &[u8]) -> String {
            hex::encode(Sha256::digest(bytes))
        }

        fn apply(service: &StudioService, project: &Project, key: &str, change: Change) -> Project {
            service
                .apply(project.id, &RevisionStamp::from(project), key, &change)
                .unwrap()
                .project
        }

        fn permissions(path: &Path, mode: u32) {
            fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
        }

        fn measured_pcm_fixture() -> Vec<u8> {
            // Complete stereo PCM16, two seconds at 48 kHz. Deliberate silence;
            // this fixture makes no speech or successful-alignment claim.
            const FRAMES: u32 = 96_000;
            const CHANNELS: u16 = 2;
            const BYTES_PER_FRAME: u16 = CHANNELS * 2;
            const DATA_BYTES: u32 = FRAMES * BYTES_PER_FRAME as u32;

            let mut bytes = Vec::with_capacity(44 + DATA_BYTES as usize);
            bytes.extend_from_slice(b"RIFF");
            bytes.extend_from_slice(&(36 + DATA_BYTES).to_le_bytes());
            bytes.extend_from_slice(b"WAVEfmt ");
            bytes.extend_from_slice(&16_u32.to_le_bytes());
            bytes.extend_from_slice(&1_u16.to_le_bytes());
            bytes.extend_from_slice(&CHANNELS.to_le_bytes());
            bytes.extend_from_slice(&48_000_u32.to_le_bytes());
            bytes.extend_from_slice(&(48_000_u32 * u32::from(BYTES_PER_FRAME)).to_le_bytes());
            bytes.extend_from_slice(&BYTES_PER_FRAME.to_le_bytes());
            bytes.extend_from_slice(&16_u16.to_le_bytes());
            bytes.extend_from_slice(b"data");
            bytes.extend_from_slice(&DATA_BYTES.to_le_bytes());
            bytes.resize(44 + DATA_BYTES as usize, 0);
            bytes
        }

        fn project_fixture(service: &StudioService, root: &Path, case: Case) -> Project {
            let mut project = service.create_project("Master admission fixture").unwrap();

            project = apply(
                service,
                &project,
                "fixture-brief",
                Change::SetBrief {
                    objective: "Source text requests a narrated master; its timing must not \
                                become measured narration without admitted source evidence."
                        .into(),
                    audience: "Boundary test".into(),
                    constraints: vec![],
                    exclusions: vec![],
                },
            );
            project = apply(
                service,
                &project,
                "fixture-scene",
                Change::AddScene {
                    name: "Source text".into(),
                    objective: "Preserve unresolved narration intent".into(),
                    duration_seconds: 2,
                },
            );
            let scene_id = project.scenes[0].id;
            project = apply(
                service,
                &project,
                "fixture-text",
                Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::now_v7(),
                        name: "Source text".into(),
                        kind: "text".into(),
                        parent_id: None,
                        x: 240.0,
                        y: 320.0,
                        width: 960.0,
                        height: 160.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: Some("Source text".into()),
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#F5F5F2".into()),
                            stroke: None,
                            stroke_width: 0.0,
                            font_family: Some(MOTION_CANVAS_FONT_FAMILY.into()),
                            font_size: Some(64.0),
                            font_weight: Some(700),
                            line_height: Some(1.05),
                            blend_mode: BlendMode::Normal,
                        },
                        relations: vec![],
                        property_locks: BTreeSet::new(),
                        keyframes: vec![],
                    },
                },
            );

            if matches!(case, Case::UnknownAlignment) {
                let source_path = root.join("admitted-pcm.wav");
                let source_bytes = measured_pcm_fixture();
                fs::write(&source_path, &source_bytes).unwrap();
                project = service
                    .import_voice_file(
                        project.id,
                        &RevisionStamp::from(&project),
                        "fixture-admit-pcm",
                        &source_path,
                        VoiceImportMetadata {
                            name: "admitted-pcm.wav".into(),
                            label: "Synthetic PCM boundary fixture; no speech claim".into(),
                            media_type: "audio/wav".into(),
                        },
                    )
                    .unwrap()
                    .project;
                let voice_track_id = project.audio.active_voice_track_id.unwrap();
                let track = project
                    .audio
                    .voice_tracks
                    .iter()
                    .find(|track| track.id == voice_track_id)
                    .unwrap();
                assert_eq!(track.sample_rate_hz, 48_000);
                assert_eq!(track.channels, 2);
                assert_eq!(track.measured_duration, time(2));
                assert_eq!(track.source_sha256, digest(&source_bytes));
                project = apply(
                    service,
                    &project,
                    "fixture-unresolved-words",
                    Change::AddTranscriptSegment {
                        segment: TranscriptSegment {
                            id: Uuid::now_v7(),
                            voice_track_id,
                            // Candidate interval remains UNKNOWN; no aligner runs.
                            start: time(0),
                            end: time(1),
                            text: "Timing remains unresolved.".into(),
                            speaker: None,
                            alignment: AlignmentEvidence::Unknown,
                        },
                    },
                );
            } else {
                assert!(project.audio.voice_tracks.is_empty());
                assert!(project.audio.active_voice_track_id.is_none());
                assert!(project.audio.transcript.is_empty());
                assert!(project.assets.is_empty());
            }

            let mut profile = project.deliverables[0].clone();
            profile.width = 1920;
            profile.height = 1080;
            profile.frame_rate = RationalTime { num: 30, den: 1 };
            profile.video_codec = VideoCodec::H264;
            profile.audio_codec = AudioCodec::Aac;
            profile.audio_sample_rate_hz = 48_000;
            profile.container = OutputContainer::Mp4;
            profile.color_space = OutputColorSpace::Rec709;
            profile.captions = true;
            profile.burn_in_captions = true;
            profile.voice_track_id = project.audio.active_voice_track_id;
            project = apply(
                service,
                &project,
                "fixture-captioned-deliverable",
                Change::UpsertDeliverable { profile },
            );
            project.validate().unwrap();
            match case {
                Case::MissingVoice => {
                    assert!(project.deliverables[0].voice_track_id.is_none());
                    assert!(project.audio.voice_tracks.is_empty());
                }
                Case::UnknownAlignment => {
                    assert_eq!(project.audio.transcript.len(), 1);
                    assert!(matches!(
                        project.audio.transcript[0].alignment,
                        AlignmentEvidence::Unknown
                    ));
                    assert_eq!(
                        project.deliverables[0].voice_track_id,
                        project.audio.active_voice_track_id
                    );
                }
            }
            project
        }

        fn connection_fixture(root: &Path, resource: String) -> (ProductionConnection, PathBuf) {
            let executable = root.join("unexpected-transport");
            let socket = root.join("unused-broker.sock");
            let session_file = root.join("unused-session.json");
            let output_root = root.join("output");
            let invocations = root.join("unexpected-transport-invocations");
            fs::create_dir(&output_root).unwrap();
            fs::write(&socket, b"").unwrap();
            fs::write(&session_file, b"{}").unwrap();
            fs::write(
                output_root.join("preserved-output-slot.bin"),
                b"Existing output slot must remain byte-identical.\n",
            )
            .unwrap();

            // The validator accepts an existing socket path. No listener,
            // Broker session or native runtime is created.
            let escaped_counter = invocations.to_string_lossy().replace('\'', "'\\''");
            let script =
                format!("#!/bin/sh\nprintf 'invoked\\n' >> '{escaped_counter}'\nexit 97\n");
            fs::write(&executable, script.as_bytes()).unwrap();
            permissions(&executable, 0o700);
            permissions(&socket, 0o600);
            permissions(&session_file, 0o600);
            permissions(&output_root, 0o700);
            (
                ProductionConnection {
                    schema: "motionwright-semwright-connection/1".into(),
                    executable_sha256: digest(script.as_bytes()),
                    executable,
                    socket,
                    session_file,
                    output_root,
                    resource,
                },
                invocations,
            )
        }

        fn output_snapshot(root: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
            fn visit(
                root: &Path,
                directory: &Path,
                snapshot: &mut BTreeMap<PathBuf, Option<Vec<u8>>>,
            ) {
                for entry in fs::read_dir(directory).unwrap() {
                    let entry = entry.unwrap();
                    let path = entry.path();
                    let kind = entry.file_type().unwrap();
                    assert!(!kind.is_symlink(), "Unexpected output symlink: {path:?}");
                    let relative = path.strip_prefix(root).unwrap().to_path_buf();
                    if kind.is_dir() {
                        snapshot.insert(relative, None);
                        visit(root, &path, snapshot);
                    } else {
                        assert!(kind.is_file(), "Unexpected output entry: {path:?}");
                        snapshot.insert(relative, Some(fs::read(path).unwrap()));
                    }
                }
            }
            let mut snapshot = BTreeMap::new();
            visit(root, root, &mut snapshot);
            snapshot
        }

        async fn assert_master_rejects_unresolved_narration(case: Case) {
            let temp = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(temp.path()).unwrap();
            permissions(&root, 0o700);
            let db = root.join("studio.sqlite3");
            let service = StudioService::open(&db).unwrap();
            let project = project_fixture(&service, &root, case);
            let deliverable_id = project.deliverables[0].id;
            let rate = Rate::new(30, 1).unwrap();
            let options = FilmBuildOptions {
                frame_rate: rate,
                font_family: MOTION_CANVAS_FONT_FAMILY.into(),
                mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
                scene_intents: vec![SceneFilmIntent {
                    scene_id: project.scenes[0].id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                }],
            };

            // Positive source-only control: valid visual Film without burn-in.
            // It does not alter the saved project or resolve the declared words.
            let mut visual_only = project.clone();
            visual_only.deliverables[0].burn_in_captions = false;
            let baseline =
                build_motion_canvas_segments(&visual_only, deliverable_id, &options).unwrap();
            assert_eq!(baseline.len(), 1);
            assert_eq!(baseline[0].frame_count, 60);
            assert_eq!(baseline[0].scene_ids, vec![project.scenes[0].id]);

            let (connection, invocations) = connection_fixture(&root, project.resource_key());
            connection.validate().unwrap();
            let output_root = connection.output_root.clone();
            let before_outputs = output_snapshot(&output_root);
            let before_receipts = service.production_receipts(project.id, 100).unwrap();
            assert!(before_receipts.is_empty());
            let rendered = MotionCanvasRenderEvidence {
                project_resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
                deliverable_id,
                frame_rate: rate,
                segments: vec![],
            };
            // Explicitly unproduced carriers. The exact narration error must
            // precede inspection of these placeholders.
            let visual = MltVerifiedLosslessTimeline {
                project_resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
                deliverable_id,
                recipe_sha256: String::new(),
                frame_count: 0,
                native_frame_count_observed: None,
                transport_pcm_audio: false,
                native_render_profile: "unproduced-test-carrier".into(),
                provider_project_cleanup: "not-invoked".into(),
                native_job_ref: String::new(),
                provider_revision: String::new(),
                artifact_path: "must-not-be-read.mkv".into(),
                artifact_sha256: String::new(),
                artifact_bytes: 0,
                source: MltPreparedMezzanines {
                    project_resource: project.resource_key(),
                    generation: project.generation,
                    revision: project.revision,
                    deliverable_id,
                    input_sha256: String::new(),
                    fps_num: 30,
                    fps_den: 1,
                    total_frames: 0,
                    verified_video_segments: vec![],
                    evidence_scope: "unproduced-test-carrier".into(),
                },
                evidence_scope: "unproduced-test-carrier".into(),
            };
            let audio = MltAudioArtifact {
                relative_path: "must-not-be-read.wav".into(),
                sha256: String::new(),
                sample_rate: 48_000,
                channels: 2,
            };
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let result = coordinator
                .assemble_native_mlt_multisegment_av_master(
                    project.id,
                    &RevisionStamp::from(&project),
                    MltMultisegmentAvMasterRequest {
                        request_id: case.request_id(),
                        deliverable_id,
                        options: &options,
                        rendered: &rendered,
                        visual: &visual,
                        audio: &audio,
                    },
                )
                .await;
            assert!(
                !invocations.exists(),
                "Unresolved narration reached the transport: {result:?}"
            );
            assert_eq!(
                service.production_receipts(project.id, 100).unwrap(),
                before_receipts,
                "Rejection must precede even the dispatching receipt"
            );
            assert_eq!(
                output_snapshot(&output_root),
                before_outputs,
                "The public request changed the output tree"
            );
            assert_eq!(
                service.project(project.id).unwrap(),
                project,
                "The public request changed saved source semantics"
            );
            let error = result.expect_err("Unresolved narration must not yield master evidence");
            assert_eq!(error.code, ErrorCode::Unsupported);
            assert_eq!(error.message, case.expected_message());
            assert!(
                error.outcome_known,
                "A semantic rejection before dispatch has a known no-effect outcome"
            );

            let reopened = StudioService::open(&db).unwrap();
            assert_eq!(reopened.project(project.id).unwrap(), project);
            assert_eq!(
                reopened.production_receipts(project.id, 100).unwrap(),
                before_receipts
            );
        }

        #[tokio::test]
        async fn master_missing_voice_rejects_before_dispatch_or_output_change() {
            assert_master_rejects_unresolved_narration(Case::MissingVoice).await;
        }

        #[tokio::test]
        async fn master_unknown_alignment_rejects_before_dispatch_or_output_change() {
            assert_master_rejects_unresolved_narration(Case::UnknownAlignment).await;
        }
    }
}
