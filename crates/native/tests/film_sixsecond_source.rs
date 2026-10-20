//! Synthetic, source-only Film source-continuity contract for two exact 3s shots.
//!
//! This test is deliberately not the private saved creative project. It does
//! not start a Semwright Broker, verify a font is installed, render native
//! pixels or certify a voice/MP4 master. The selected Ubuntu canonical-film
//! job compiles and runs these tests; no heavy Windows build is needed.

mod film {
    #[cfg(test)]
    pub mod tests {
        use motionwright_domain::{
            AlignmentEvidence, Asset, BlendMode, CanvasNode, Change, CoordinateSpace,
            DeliveryArchetype, DeliveryNarrativeRole, DeliveryRecipe, DeliverySceneIntent,
            NodeStyle, Project, RationalTime, TranscriptSegment, VoiceTrack, caption_sidecar,
        };
        use motionwright_native::film::{
            FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
            SceneFilmIntent, build_motion_canvas_segments,
        };
        use semwright_media_time::{Rate, ResolvedCue};
        use semwright_motion_authoring::{Archetype, NarrativeRole, SubjectContent, realize};
        use std::collections::{BTreeSet, HashSet};
        use uuid::Uuid;

        fn source_node(kind: &str, text: Option<&str>, x: f64, y: f64, order: i32) -> CanvasNode {
            let is_text = kind == "text";
            CanvasNode {
                id: Uuid::new_v4(),
                name: format!("First-party test node {order}"),
                kind: kind.into(),
                parent_id: None,
                x,
                y,
                width: if is_text { 1040.0 } else { 150.0 },
                height: if is_text { 120.0 } else { 150.0 },
                rotation_deg: 0.0,
                opacity: 1.0,
                text: text.map(str::to_owned),
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: order,
                style: NodeStyle {
                    fill: Some(if is_text {
                        "#F5F5F2".into()
                    } else {
                        "#F5B84A".into()
                    }),
                    stroke: None,
                    stroke_width: 0.0,
                    font_family: is_text.then(|| MOTION_CANVAS_FONT_FAMILY.into()),
                    font_size: is_text.then_some(44.0),
                    font_weight: is_text.then_some(700),
                    line_height: is_text.then_some(1.1),
                    blend_mode: BlendMode::Normal,
                },
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            }
        }

        fn saved_synthetic_recipe() -> Project {
            let mut project = Project::new("First-party six-second Film clock").unwrap();
            for label in ["First source scene", "Second source scene"] {
                project
                    .apply_change(&Change::AddScene {
                        name: label.into(),
                        objective: "Keep the original authoring source independent".into(),
                        duration_seconds: 3,
                    })
                    .unwrap();
            }
            let scenes = project.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
            for (i, node) in [
                source_node("text", Some("Source authored title"), 180.0, 140.0, 1),
                source_node("text", Some("Reopened source copy"), 180.0, 330.0, 2),
                source_node("rectangle", None, 1310.0, 360.0, 3),
                source_node("text", Some("Second scene source"), 180.0, 160.0, 1),
            ]
            .into_iter()
            .enumerate()
            {
                let scene_id = if i < 3 { scenes[0] } else { scenes[1] };
                project
                    .apply_change(&Change::AddCanvasNode { scene_id, node })
                    .unwrap();
            }

            let asset_id = Uuid::new_v4();
            let voice_id = Uuid::new_v4();
            let sha = "ab".repeat(32);
            project.assets.push(Asset {
                id: asset_id,
                name: "Synthetic clock-aligned audio source".into(),
                media_type: "audio/wav".into(),
                content_sha256: Some(sha.clone()),
                source_revision: Some("source-only synthetic fixture".into()),
            });
            project.audio.voice_tracks.push(VoiceTrack {
                id: voice_id,
                asset_id,
                label: "Synthetic six-second timed source, not speech".into(),
                sample_rate_hz: 48_000,
                channels: 2,
                measured_duration: RationalTime::new(6, 1).unwrap(),
                source_sha256: sha,
                loudness_lufs: None,
                true_peak_dbfs: None,
            });
            for (start, end, text) in [
                (
                    RationalTime::new(1, 5).unwrap(),
                    RationalTime::new(9, 10).unwrap(),
                    "First authorial overlay",
                ),
                (
                    RationalTime::new(11, 10).unwrap(),
                    RationalTime::new(12, 5).unwrap(),
                    "Second authorial overlay",
                ),
            ] {
                project.audio.transcript.push(TranscriptSegment {
                    id: Uuid::new_v4(),
                    voice_track_id: voice_id,
                    start,
                    end,
                    text: text.into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                });
            }
            let deliverable_id = project.deliverables[0].id;
            let mut output = project.deliverables[0].clone();
            output.captions = true;
            output.burn_in_captions = true;
            output.voice_track_id = Some(voice_id);
            project
                .apply_change(&Change::UpsertDeliverable { profile: output })
                .unwrap();

            project
                .apply_change(&Change::SetDeliveryRecipe {
                    deliverable_profile_id: deliverable_id,
                    recipe: Some(DeliveryRecipe {
                        scene_intents: vec![
                            DeliverySceneIntent {
                                scene_id: scenes[0],
                                role: Some(DeliveryNarrativeRole::Evidence),
                                archetype: Some(DeliveryArchetype::EvidenceFrame),
                            },
                            DeliverySceneIntent {
                                scene_id: scenes[1],
                                role: Some(DeliveryNarrativeRole::Evidence),
                                archetype: Some(DeliveryArchetype::Statement),
                            },
                        ],
                        ..Default::default()
                    }),
                })
                .unwrap();
            project.validate().unwrap();
            project
        }

        fn opts(p: &Project) -> FilmBuildOptions {
            FilmBuildOptions {
                frame_rate: Rate::new(30, 1).unwrap(),
                font_family: MOTION_CANVAS_FONT_FAMILY.into(),
                mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
                scene_intents: vec![
                    SceneFilmIntent {
                        scene_id: p.scenes[0].id,
                        role: NarrativeRole::Evidence,
                        archetype: Archetype::EvidenceFrame,
                    },
                    SceneFilmIntent {
                        scene_id: p.scenes[1].id,
                        role: NarrativeRole::Evidence,
                        archetype: Archetype::Statement,
                    },
                ],
            }
        }

        #[test]
        fn one_film_run_retains_exact_180_frame_two_scene_source_and_order() {
            let project = saved_synthetic_recipe();
            let original_source = serde_json::to_value(&project).unwrap();
            let deliverable = project.deliverables[0].id;
            let segments =
                build_motion_canvas_segments(&project, deliverable, &opts(&project)).unwrap();
            assert_eq!(
                segments.len(),
                1,
                "contiguous Motion Canvas scenes share one Film run"
            );
            let one = &segments[0];
            assert_eq!(one.global_start, RationalTime::ZERO);
            assert_eq!(one.duration, RationalTime::new(6, 1).unwrap());
            assert_eq!(one.frame_count, 180);
            assert_eq!(
                one.scene_ids,
                project.scenes.iter().map(|s| s.id).collect::<Vec<_>>()
            );
            assert_eq!(one.film.sequences.len(), 1);
            let beats = &one.film.sequences[0].beats;
            assert_eq!(beats.len(), 2);
            assert_eq!(beats[0].role, NarrativeRole::Evidence);
            assert_eq!(beats[1].role, NarrativeRole::Evidence);
            assert_eq!(beats[0].shots.len(), 1);
            assert_eq!(beats[1].shots.len(), 1);
            assert_eq!(beats[0].shots[0].archetype, Archetype::EvidenceFrame);
            assert_eq!(beats[1].shots[0].archetype, Archetype::Statement);
            assert_eq!(
                beats[0].shots[0].id,
                format!("shot-{}", project.scenes[0].id.simple())
            );
            assert_eq!(
                beats[1].shots[0].id,
                format!("shot-{}", project.scenes[1].id.simple())
            );
            for (index, beat) in beats.iter().enumerate() {
                let ids = beat.shots[0]
                    .subjects
                    .iter()
                    .map(|s| s.id.as_str())
                    .collect::<HashSet<_>>();
                for node in &project.scenes[index].nodes {
                    assert!(
                        ids.contains(format!("node-{}", node.id.simple()).as_str()),
                        "original editable node {} is not retained in Film",
                        node.id
                    );
                }
            }
            assert_eq!(project.scenes[0].nodes.len(), 3);
            assert_eq!(project.scenes[1].nodes.len(), 1);
            assert_eq!(
                realize(&one.film)
                    .unwrap()
                    .schedule
                    .frame_count(&one.film.output)
                    .unwrap(),
                180
            );
            assert_eq!(serde_json::to_value(&project).unwrap(), original_source);
            assert_eq!(
                project.production_design.delivery_recipes[&deliverable]
                    .scene_intents
                    .len(),
                2,
            );
        }

        #[test]
        fn native_caption_windows_are_exact_and_do_not_bleed_into_second_scene() {
            let project = saved_synthetic_recipe();
            let profile_id = project.deliverables[0].id;
            let segments =
                build_motion_canvas_segments(&project, profile_id, &opts(&project)).unwrap();
            let film = &segments[0].film;
            let beats = &film.sequences[0].beats;
            assert_eq!(film.cues.cues.len(), 2);
            assert_eq!(beats[0].shots[0].captions.len(), 2);
            assert!(beats[1].shots[0].captions.is_empty());
            assert!(
                film.assets.is_empty(),
                "synthetic audio is not a visual asset"
            );
            let resolved = film.cues.resolve().unwrap();
            let mut actual_windows = BTreeSet::new();
            for caption in &beats[0].shots[0].captions {
                let ResolvedCue::Resolved { start, end } = resolved.get(&caption.cue_id).unwrap()
                else {
                    panic!("saved authored caption timing was not resolved");
                };
                assert_eq!((start.num * 30) % start.den, 0);
                assert_eq!((end.num * 30) % end.den, 0);
                actual_windows.insert((start.num * 30 / start.den, end.num * 30 / end.den));
                let text = beats[0].shots[0]
                    .subjects
                    .iter()
                    .find(|subject| subject.id == caption.subject)
                    .unwrap();
                assert!(matches!(text.content, SubjectContent::Text { .. }));
                assert!(!text.initially_visible);
            }
            assert_eq!(actual_windows, BTreeSet::from([(6, 27), (33, 72)]));
            let sidecar = caption_sidecar(&project, profile_id).unwrap();
            assert_eq!(sidecar.cue_count, 2);
            assert!(sidecar.body.contains("00:00:00.200 --> 00:00:00.900"));
            assert!(sidecar.body.contains("00:00:01.100 --> 00:00:02.400"));
            assert!(!sidecar.body.contains("00:00:03."));
        }

        #[test]
        fn authored_caption_source_mutation_changes_cues_without_changing_timeline() {
            let mut p = saved_synthetic_recipe();
            let profile_id = p.deliverables[0].id;
            let source_before = serde_json::to_value(&p).unwrap();
            let old_authored_scenes = p.scenes.clone();
            let old = build_motion_canvas_segments(&p, profile_id, &opts(&p)).unwrap();
            p.audio.transcript[0].text = "Altered authorial overlay".into();
            let new = build_motion_canvas_segments(&p, profile_id, &opts(&p)).unwrap();
            assert_eq!(
                new[0].film.cues.cues, old[0].film.cues.cues,
                "a caption text change must not silently retime a saved cue"
            );
            let old_caption = &old[0].film.sequences[0].beats[0].shots[0].captions[0];
            let new_caption = &new[0].film.sequences[0].beats[0].shots[0].captions[0];
            assert_ne!(new_caption.text, old_caption.text);
            assert_eq!(new_caption.text, "Altered authorial overlay");
            assert_eq!(new[0].frame_count, old[0].frame_count);
            assert_eq!(new[0].scene_ids, old[0].scene_ids);
            assert_ne!(serde_json::to_value(&p).unwrap(), source_before);
            assert_eq!(
                p.scenes, old_authored_scenes,
                "caption edits must not mutate Canvas"
            );
        }
    }
}
