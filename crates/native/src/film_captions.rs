//! Explicitly requested, source-bound native subtitle overlay projection.
//! This is NOT a claim of glyph legibility, speech truth or a mastered MP4.
//! Sidecar-only profiles never enter this path.

use super::*;
use motionwright_domain::{AlignmentEvidence, transcript_for_profile};
use semwright_media_time::{Anchor, Cue};
use semwright_motion_authoring::{Caption, TextRun};

const CAPTION_STYLE: &str = "mw-native-caption-v1";
const CAPTION_LAYER: &str = "mw-native-caption-layer";
const MAX_CUES: usize = 96;
const MAX_SHOT_CUES: usize = 32;
// Preserve the existing total native cue budget when a canonical cue needs
// several shot-local realizations. Splitting does not increase authority.
const MAX_NATIVE_FRAGMENTS: usize = MAX_CUES;

#[derive(Debug, Clone)]
struct CaptionFragment {
    segment: usize,
    sequence: usize,
    beat: usize,
    shot: usize,
    cue: usize,
    start: RationalTime,
    end: RationalTime,
    part: Option<usize>,
}

fn caption_time_cmp(left: RationalTime, right: RationalTime) -> std::cmp::Ordering {
    // Validated canonical times have positive denominators. Widen both
    // products before comparing; no float, field order or RationalTime::Ord.
    let left_scaled = i128::from(left.num) * i128::from(right.den);
    let right_scaled = i128::from(right.num) * i128::from(left.den);
    left_scaled.cmp(&right_scaled)
}

/// Derive a complete partition before changing any Film. A native renderer
/// gap, overlapping shot schedule or fractional frame is never filled or
/// rounded. Domain cut/reflow admission has already validated the transcript.
fn native_caption_fragments(
    cues: &[motionwright_domain::TranscriptSegment],
    rate: Rate,
    segments: &[MotionCanvasSegment],
) -> NativeResult<Vec<CaptionFragment>> {
    let mut fragments = Vec::new();
    for (segment_index, segment) in segments.iter().enumerate() {
        let original = realize(&segment.film).map_err(|err| {
            contract("Caption baseline Film has no realizable shot schedule", err)
        })?;
        for (sequence_index, sequence) in segment.film.sequences.iter().enumerate() {
            for (beat_index, beat) in sequence.beats.iter().enumerate() {
                for (shot_index, shot) in beat.shots.iter().enumerate() {
                    let interval = original.schedule.interval(&shot.span_id).map_err(|err| {
                        contract("Native caption shot span was not scheduled", err)
                    })?;
                    let shot_start = add(
                        segment.global_start,
                        interval.start,
                        "Caption shot start overflow",
                    )?;
                    let shot_end = add(
                        segment.global_start,
                        interval.end,
                        "Caption shot end overflow",
                    )?;
                    for (cue_index, cue) in cues.iter().enumerate() {
                        let start = if caption_time_cmp(cue.start, shot_start).is_gt() {
                            cue.start
                        } else {
                            shot_start
                        };
                        let end = if caption_time_cmp(cue.end, shot_end).is_lt() {
                            cue.end
                        } else {
                            shot_end
                        };
                        if !caption_time_cmp(start, end).is_lt() {
                            continue;
                        }
                        if !frame_aligned(start, rate) || !frame_aligned(end, rate) {
                            return Err(unsupported(
                                "Caption shot intersections must be exact output-frame intervals",
                            ));
                        }
                        if fragments.len() >= MAX_NATIVE_FRAGMENTS {
                            return Err(unsupported(
                                "Native burn-in exceeds 96 bounded shot-local caption fragments",
                            ));
                        }
                        fragments.push(CaptionFragment {
                            segment: segment_index,
                            sequence: sequence_index,
                            beat: beat_index,
                            shot: shot_index,
                            cue: cue_index,
                            start,
                            end,
                            part: None,
                        });
                    }
                }
            }
        }
    }
    for (cue_index, cue) in cues.iter().enumerate() {
        let mut positions = fragments
            .iter()
            .enumerate()
            .filter_map(|(index, fragment)| (fragment.cue == cue_index).then_some(index))
            .collect::<Vec<_>>();
        positions.sort_by(|left, right| {
            caption_time_cmp(fragments[*left].start, fragments[*right].start).then(
                caption_time_cmp(fragments[*left].end, fragments[*right].end),
            )
        });
        let mut cursor = cue.start;
        for index in &positions {
            let fragment = &fragments[*index];
            if fragment.start != cursor {
                return Err(unsupported(
                    "Native caption coverage has a missing renderer interval or overlapping shot",
                ));
            }
            cursor = fragment.end;
        }
        if positions.is_empty() || cursor != cue.end {
            return Err(unsupported(
                "Every caption interval needs complete contiguous native Motion Canvas coverage",
            ));
        }
        if positions.len() > 1 {
            for (part, index) in positions.into_iter().enumerate() {
                fragments[index].part = Some(part + 1);
            }
        }
    }
    Ok(fragments)
}

fn frame_aligned(time: RationalTime, rate: Rate) -> bool {
    let numerator = i128::from(time.num) * i128::from(rate.num);
    let denominator = i128::from(time.den) * i128::from(rate.den);
    denominator > 0 && numerator >= 0 && numerator % denominator == 0
}

fn bounded_caption_text(text: &str) -> NativeResult<String> {
    let text = text.trim();
    let lines = text.split('\n').collect::<Vec<_>>();
    if text.is_empty()
        || text.len() > 320
        || lines.len() > 2
        || lines
            .iter()
            .any(|line| line.is_empty() || line.chars().count() > 42)
        || text.chars().any(|c| c.is_control() && c != '\n')
    {
        return Err(unsupported(
            "Native caption burn-in currently requires one or two lines, no more than 42 code points per line, and no control characters; edit the transcript before rendering",
        ));
    }
    Ok(text.to_owned())
}

/// Called only when a saved deliverable explicitly opts into burn-in.
/// The source is the SAME validated, cut-rebased transcript as WebVTT/SRT.
/// This function never rewrites the canonical Project, invents timestamps,
/// acquires effects, or substitutes a renderer.
pub(super) fn attach_native_caption_cues(
    project: &Project,
    profile: &DeliverableProfile,
    rate: Rate,
    segments: &mut [MotionCanvasSegment],
) -> NativeResult<()> {
    if !profile.burn_in_captions {
        return Ok(());
    }
    if !profile.captions {
        return Err(unsupported(
            "Caption burn-in requires captions enabled in the saved profile",
        ));
    }
    let track_id = profile.voice_track_id.ok_or_else(|| {
        unsupported("Native caption burn-in requires one explicitly selected voice track")
    })?;
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == track_id)
        .ok_or_else(|| invalid("The selected caption voice take is absent"))?;
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == track.asset_id)
        .ok_or_else(|| invalid("The selected caption voice asset is absent"))?;
    if asset.content_sha256.as_deref() != Some(track.source_sha256.as_str())
        || !matches!(
            asset.media_type.as_str(),
            "audio/wav" | "audio/wave" | "audio/x-wav"
        )
    {
        return Err(unsupported(
            "Native captions require the actual project-bound measured WAV digest, not unverified voice metadata",
        ));
    }
    let source = Digest::parse(track.source_sha256.clone())
        .map_err(|err| contract("Caption source SHA-256 invalid", err))?;
    let cues = transcript_for_profile(project, profile)
        .map_err(|err| contract("Caption transcript/cut projection is not admissible", err))?;
    if cues.is_empty() || cues.len() > MAX_CUES {
        return Err(unsupported(
            "Native burn-in admits 1–96 explicitly timed transcript cues",
        ));
    }
    let text = cues
        .iter()
        .map(|cue| bounded_caption_text(&cue.text))
        .collect::<NativeResult<Vec<_>>>()?;
    for (index, cue) in cues.iter().enumerate() {
        if cue.voice_track_id != track_id {
            return Err(invalid("Caption source voice identity changed"));
        }
        if !frame_aligned(cue.start, rate) || !frame_aligned(cue.end, rate) {
            return Err(unsupported(
                "Native caption timing must align to exact output-frame boundaries; no silent rounding is permitted",
            ));
        }
        if cue.end > track.measured_duration {
            return Err(unsupported(
                "Caption timing extends beyond the independently measured voice duration",
            ));
        }
        if index > 0 && cues[index - 1].end > cue.start {
            return Err(unsupported(
                "Overlapping native caption cues require an explicit user-authored re-alignment",
            ));
        }
        if let AlignmentEvidence::Measured { source_sha256, .. } = &cue.alignment
            && source_sha256 != &track.source_sha256
        {
            return Err(unsupported(
                "Measured caption alignment refers to a different voice source digest",
            ));
        }
    }

    let fragments = native_caption_fragments(&cues, rate, segments)?;
    for (segment_index, segment) in segments.iter_mut().enumerate() {
        let mut for_segment = 0_usize;
        for (sequence_index, sequence) in segment.film.sequences.iter_mut().enumerate() {
            for (beat_index, beat) in sequence.beats.iter_mut().enumerate() {
                for (shot_index, shot) in beat.shots.iter_mut().enumerate() {
                    for fragment in fragments.iter().filter(|fragment| {
                        (
                            fragment.segment,
                            fragment.sequence,
                            fragment.beat,
                            fragment.shot,
                        ) == (segment_index, sequence_index, beat_index, shot_index)
                    }) {
                        let index = fragment.cue;
                        let cue = &cues[index];
                        if shot.captions.len() >= MAX_SHOT_CUES {
                            return Err(unsupported(
                                "Too many captions in one bounded native shot",
                            ));
                        }
                        // The complete canonical cue UUID remains visible in
                        // every derived native ID. Unsplit cues retain their
                        // byte-identical historical IDs and native projection.
                        let identity = match fragment.part {
                            Some(part) => format!("{}-p{part}", cue.id.simple()),
                            None => cue.id.simple().to_string(),
                        };
                        let subject_id = format!("mw-caption-subject-{identity}");
                        let cue_id = format!("mw-caption-cue-{identity}");
                        let layer_order = shot
                            .layers
                            .iter()
                            .map(|layer| layer.order)
                            .max()
                            .unwrap_or(0)
                            .checked_add(1)
                            .ok_or_else(|| {
                                unsupported("Cannot place caption above maximum native layer")
                            })?;
                        if !shot.layers.iter().any(|layer| layer.id == CAPTION_LAYER) {
                            shot.layers.push(Layer {
                                id: CAPTION_LAYER.to_owned(),
                                order: layer_order,
                                intentional_overlay: true,
                            });
                        }
                        let w = f64::from(segment.film.output.width);
                        let h = f64::from(segment.film.output.height);
                        let safe = segment.film.output.safe_area;
                        let width = (w - 2.0 * safe.left.max(safe.right).max(w * 0.10)).max(0.0);
                        let height = (h * 0.15).min(180.0);
                        if width < 320.0 || height < 45.0 {
                            return Err(unsupported(
                                "Selected output has no bounded native caption safe-area box",
                            ));
                        }
                        let y = h * 0.5 - safe.bottom - height * 0.5 - h * 0.055;
                        let color = segment
                            .film
                            .editorial
                            .colors
                            .get("text")
                            .cloned()
                            .ok_or_else(|| {
                                invalid("Native caption editorial text color is missing")
                            })?;
                        // Motion Canvas keeps Txt layout enabled for native
                        // shaping. A root-level Txt with Fixed geometry can
                        // nevertheless participate in its parent row/column
                        // flow, shifting original Canvas pixels even while
                        // invisible. Isolate Txt inside an absolutely placed,
                        // native non-layout Group instead. This is still a
                        // real editable native Semwright semantic subject.
                        let overlay_id = format!("mw-caption-container-{identity}");
                        shot.subjects.push(Subject {
                            id: overlay_id.clone(),
                            role: "caption-overlay-root".to_owned(),
                            parent: None,
                            layer: CAPTION_LAYER.to_owned(),
                            content: SubjectContent::Group,
                            layout: SpatialIntent::Fixed {
                                position: Point { x: 0.0, y },
                                size: Size { width, height },
                            },
                            initially_visible: true,
                            clip_intentional: false,
                        });
                        shot.subjects.push(Subject {
                            id: subject_id.clone(),
                            role: "caption".to_owned(),
                            parent: Some(overlay_id),
                            layer: CAPTION_LAYER.to_owned(),
                            content: SubjectContent::Text {
                                runs: vec![TextRun {
                                    text: text[index].clone(),
                                    weight: 600,
                                    color: Some(color),
                                    emphasis: false,
                                }],
                                style: CAPTION_STYLE.to_owned(),
                                direction: TextDirection::Auto,
                                language: profile.language.clone(),
                                wrap: true,
                                truncate: false,
                            },
                            layout: SpatialIntent::Fixed {
                                position: Point { x: 0.0, y: 0.0 },
                                size: Size { width, height },
                            },
                            initially_visible: false,
                            clip_intentional: false,
                        });
                        shot.captions.push(Caption {
                            id: format!("mw-caption-{identity}"),
                            subject: subject_id,
                            cue_id: cue_id.clone(),
                            language: profile.language.clone(),
                            text: text[index].clone(),
                        });
                        let start = sub(
                            fragment.start,
                            segment.global_start,
                            "Caption local time underflow",
                        )?;
                        let duration =
                            sub(fragment.end, fragment.start, "Caption interval underflow")?;
                        segment.film.cues.cues.push(Cue {
                            id: cue_id,
                            anchor: Anchor::Absolute { time: start },
                            duration,
                            source: source.clone(),
                            method: match &cue.alignment {
                                AlignmentEvidence::Manual => "manual-timing".to_owned(),
                                AlignmentEvidence::Measured { .. } => "measured-timing".to_owned(),
                                AlignmentEvidence::Unknown => {
                                    return Err(unsupported(
                                        "UNKNOWN caption timing cannot become a visible native cue",
                                    ));
                                }
                            },
                            version: 1,
                            confidence: None,
                        });
                        for_segment += 1;
                    }
                }
            }
        }
        if for_segment > 0 {
            let output = &segment.film.output;
            let size = (f64::from(output.height) / 20.0)
                .min(f64::from(output.width) / 24.0)
                .clamp(24.0, 64.0);
            segment
                .film
                .editorial
                .type_scale
                .insert(CAPTION_STYLE.to_owned(), size);
            let realized = realize(&segment.film).map_err(|err| {
                contract(
                    "Captioned native Film failed authoritative realization",
                    err,
                )
            })?;
            let frames = realized
                .schedule
                .frame_count(&segment.film.output)
                .map_err(|err| contract("Captioned Film frame schedule changed", err))?;
            if frames != segment.frame_count {
                return Err(invalid(
                    "Caption overlay unexpectedly changed native video frame count",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        Asset, Change, NodeStyle, TranscriptSegment, VoiceTrack, caption_sidecar,
    };

    fn t(num: i64, den: i64) -> RationalTime {
        RationalTime::new(num, den).unwrap()
    }

    // These are in-memory source-projection fixtures. No WAV is decoded,
    // no provider runs and no physical voice or native receipt is claimed.
    fn fixture(count: usize, duration: RationalTime) -> (Project, Uuid, FilmBuildOptions) {
        let mut project = Project::new("Caption partition source-only fixture").unwrap();
        for index in 0..count {
            project
                .apply_change(&Change::AddScene {
                    name: format!("Caption source scene {index}"),
                    objective: "Preserve native subtitle continuity".into(),
                    duration_seconds: 1,
                })
                .unwrap();
            let scene_id = project.scenes.last().unwrap().id;
            project
                .apply_change(&Change::SetSceneDuration { scene_id, duration })
                .unwrap();
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::new_v4(),
                        name: "Unchanged native rectangle".into(),
                        kind: "rectangle".into(),
                        parent_id: None,
                        x: 420.0,
                        y: 320.0,
                        width: 200.0,
                        height: 100.0,
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
                    },
                })
                .unwrap();
        }
        let end = project
            .scenes
            .last()
            .unwrap()
            .start
            .checked_add(project.scenes.last().unwrap().duration)
            .unwrap();
        let asset_id = Uuid::new_v4();
        let track_id = Uuid::new_v4();
        project.assets.push(Asset {
            id: asset_id,
            name: "Source-only WAV identity fixture".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: Some("unit-data-not-physical-audio".into()),
        });
        project.audio.voice_tracks.push(VoiceTrack {
            id: track_id,
            asset_id,
            label: "Unit source metadata, no physical measurement".into(),
            sample_rate_hz: 48_000,
            channels: 2,
            measured_duration: end,
            source_sha256: "a".repeat(64),
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
        project.audio.active_voice_track_id = Some(track_id);
        project.audio.transcript.push(TranscriptSegment {
            id: Uuid::new_v4(),
            voice_track_id: track_id,
            start: RationalTime::ZERO,
            end,
            text: "CONTINUOUS SOURCE CAPTION".into(),
            speaker: None,
            alignment: AlignmentEvidence::Manual,
        });
        let profile = project
            .deliverables
            .iter_mut()
            .find(|profile| profile.name == "Master 16:9")
            .unwrap();
        profile.width = 1280;
        profile.height = 720;
        profile.frame_rate = t(30, 1);
        profile.voice_track_id = Some(track_id);
        profile.captions = true;
        profile.burn_in_captions = true;
        let profile_id = profile.id;
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
        project.validate().unwrap();
        (project, profile_id, options)
    }

    #[test]
    fn cross_scene_caption_keeps_source_sidecar_identity_and_exact_coverage() {
        let (mut project, profile, options) = fixture(2, t(1, 1));
        project.audio.transcript[0].start = t(1, 2);
        project.audio.transcript[0].end = t(3, 2);
        let before = project.clone();
        let sidecar = caption_sidecar(&project, profile).unwrap();
        let segments = build_motion_canvas_segments(&project, profile, &options).unwrap();
        assert_eq!(segments.len(), 1);
        let cues = &segments[0].film.cues.cues;
        assert_eq!(cues.len(), 2);
        let source_id = project.audio.transcript[0].id.simple().to_string();
        for (index, start) in [t(1, 2), t(1, 1)].into_iter().enumerate() {
            assert_eq!(
                cues[index].id,
                format!("mw-caption-cue-{source_id}-p{}", index + 1)
            );
            assert_eq!(cues[index].anchor, Anchor::Absolute { time: start });
            assert_eq!(cues[index].duration, t(1, 2));
            assert_eq!(cues[index].source, Digest::parse("a".repeat(64)).unwrap());
        }
        assert_eq!(segments[0].frame_count, 60);
        assert_eq!(project, before);
        assert_eq!(caption_sidecar(&project, profile).unwrap(), sidecar);
    }

    #[test]
    fn crossing_32_plus_1_frame_boundary_rebases_only_native_fragment_time() {
        let (mut project, profile, options) = fixture(33, t(1, 30));
        project.audio.transcript[0].start = t(31, 30);
        let before = project.clone();
        let segments = build_motion_canvas_segments(&project, profile, &options).unwrap();
        assert_eq!(
            segments.iter().map(|s| s.frame_count).collect::<Vec<_>>(),
            vec![32, 1]
        );
        for (index, start) in [t(31, 30), RationalTime::ZERO].into_iter().enumerate() {
            let cues = &segments[index].film.cues.cues;
            assert_eq!(cues.len(), 1);
            assert_eq!(cues[0].anchor, Anchor::Absolute { time: start });
            assert_eq!(cues[0].duration, t(1, 30));
            assert!(cues[0].id.ends_with(&format!("-p{}", index + 1)));
        }
        assert_eq!(project, before);
    }

    #[test]
    fn unfragmented_cue_preserves_legacy_ids_and_timing() {
        let (mut project, profile, options) = fixture(2, t(1, 1));
        project.audio.transcript[0].start = t(15, 30);
        project.audio.transcript[0].end = t(16, 30);
        let segments = build_motion_canvas_segments(&project, profile, &options).unwrap();
        let source_id = project.audio.transcript[0].id.simple().to_string();
        let cues = &segments[0].film.cues.cues;
        assert_eq!(cues.len(), 1);
        assert_eq!(cues[0].id, format!("mw-caption-cue-{source_id}"));
        assert_eq!(cues[0].anchor, Anchor::Absolute { time: t(15, 30) });
        assert_eq!(cues[0].duration, t(1, 30));
        let caption = &segments[0].film.sequences[0].beats[0].shots[0].captions[0];
        assert_eq!(caption.id, format!("mw-caption-{source_id}"));
        assert_eq!(caption.subject, format!("mw-caption-subject-{source_id}"));
        assert_eq!(caption.text, project.audio.transcript[0].text);
    }

    #[test]
    fn foreign_renderer_interval_is_rejected_without_filling_the_caption() {
        let (mut project, profile, options) = fixture(3, t(1, 1));
        project
            .apply_change(&Change::SetSceneRenderer {
                scene_id: project.scenes[1].id,
                renderer: RendererKind::Blender,
            })
            .unwrap();
        let before = project.clone();
        let error = build_motion_canvas_segments(&project, profile, &options).unwrap_err();
        assert!(error.message.contains("coverage"));
        assert_eq!(project, before);
    }

    #[test]
    fn missing_or_overlapping_native_segments_never_become_a_complete_caption() {
        let (mut project, profile, options) = fixture(33, t(1, 30));
        project.audio.transcript[0].start = t(31, 30);
        project
            .deliverables
            .iter_mut()
            .find(|p| p.id == profile)
            .unwrap()
            .burn_in_captions = false;
        let segments = build_motion_canvas_segments(&project, profile, &options).unwrap();
        assert!(
            native_caption_fragments(
                &project.audio.transcript,
                options.frame_rate,
                &segments[..1]
            )
            .is_err()
        );
        let duplicated = [
            segments[0].clone(),
            segments[0].clone(),
            segments[1].clone(),
        ];
        assert!(
            native_caption_fragments(&project.audio.transcript, options.frame_rate, &duplicated)
                .is_err()
        );
    }

    #[test]
    fn fractional_unknown_foreign_source_and_cut_crossing_guards_remain_closed() {
        let (mut project, profile, options) = fixture(2, t(1, 1));
        project.audio.transcript[0].start = t(1, 2);
        project.audio.transcript[0].end = t(3, 2);
        for variant in 0..4 {
            let mut candidate = project.clone();
            match variant {
                0 => candidate.audio.transcript[0].start = t(51, 100),
                1 => candidate.audio.transcript[0].alignment = AlignmentEvidence::Unknown,
                2 => {
                    candidate.audio.transcript[0].alignment = AlignmentEvidence::Measured {
                        engine: "unit-data".into(),
                        source_sha256: "b".repeat(64),
                        confidence_millis: Some(900),
                    }
                }
                _ => {
                    let ids = candidate.scenes.iter().map(|s| s.id).collect();
                    candidate
                        .deliverables
                        .iter_mut()
                        .find(|p| p.id == profile)
                        .unwrap()
                        .included_scene_ids = ids;
                }
            }
            let before = candidate.clone();
            assert!(
                build_motion_canvas_segments(&candidate, profile, &options).is_err(),
                "variant {variant}"
            );
            assert_eq!(candidate, before);
        }
    }

    #[test]
    fn caption_fragment_budget_is_enforced_before_film_mutation() {
        let (project, profile, options) = fixture(97, t(1, 30));
        let before = project.clone();
        let error = build_motion_canvas_segments(&project, profile, &options).unwrap_err();
        assert!(
            error
                .message
                .contains("96 bounded shot-local caption fragments")
        );
        assert_eq!(project, before);
    }
}
