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

    let mut assigned = vec![false; cues.len()];
    for segment in segments.iter_mut() {
        let original = realize(&segment.film).map_err(|err| {
            contract("Caption baseline Film has no realizable shot schedule", err)
        })?;
        let mut for_segment = 0_usize;
        for sequence in &mut segment.film.sequences {
            for beat in &mut sequence.beats {
                for shot in &mut beat.shots {
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
                    for (index, cue) in cues.iter().enumerate() {
                        if cue.start < shot_start || cue.end > shot_end {
                            continue;
                        }
                        if assigned[index] {
                            return Err(invalid("Native caption was assigned to multiple shots"));
                        }
                        if shot.captions.len() >= MAX_SHOT_CUES {
                            return Err(unsupported(
                                "Too many captions in one bounded native shot",
                            ));
                        }
                        let subject_id = format!("mw-caption-subject-{}", cue.id.simple());
                        let cue_id = format!("mw-caption-cue-{}", cue.id.simple());
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
                        let overlay_id = format!("mw-caption-container-{}", cue.id.simple());
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
                                line_height: None,
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
                            id: format!("mw-caption-{}", cue.id.simple()),
                            subject: subject_id,
                            cue_id: cue_id.clone(),
                            language: profile.language.clone(),
                            text: text[index].clone(),
                        });
                        let start = sub(
                            cue.start,
                            segment.global_start,
                            "Caption local time underflow",
                        )?;
                        let duration = sub(cue.end, cue.start, "Caption interval underflow")?;
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
                        assigned[index] = true;
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
    if assigned.iter().any(|assigned| !assigned) {
        return Err(unsupported(
            "Every burn-in caption must fit within one native Motion Canvas shot; a missing renderer, beat boundary, or cross-scene cue requires explicit user review",
        ));
    }
    Ok(())
}
