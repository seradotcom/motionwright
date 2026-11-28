use super::{add, contract, segment_end, sub, uid, unsupported};
use motionwright_domain::{
    AlignmentEvidence, AudioCue, CueEvidence, Project, Scene, TranscriptSegment,
};
use semwright_media_time::{Anchor, Cue, CueGraph, Rate, Rational, Round};
use semwright_native_sdk::Result as NativeResult;
use semwright_semantic_composition::canonical_digest;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const MAX_FILM_AUDIO_CUES: usize = 512;

/// A source-bound projection for timing review. This reports the sample grid
/// of the Film plan, separately from native observations of rendered frames.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum MotionCanvasAudioCueAlignment {
    Resolved {
        cue_id: Uuid,
        scene_id: Uuid,
        authored_at: Rational,
        segment_at: Rational,
        frame: u64,
        residual: Rational,
    },
    Unknown {
        cue_id: Uuid,
        scene_id: Uuid,
    },
}

fn cue_evidence(
    project: &Project,
    cue: &AudioCue,
    segment: Option<&TranscriptSegment>,
) -> Option<(&'static str, Option<u16>)> {
    match cue.evidence {
        CueEvidence::Manual => return Some(("motionwright-manual-cue", None)),
        CueEvidence::Unknown => return None,
        CueEvidence::Measured | CueEvidence::TranscriptAligned => {}
    }
    let segment = segment.filter(|segment| segment.start <= cue.at && cue.at < segment.end)?;
    match &segment.alignment {
        AlignmentEvidence::Manual if cue.evidence == CueEvidence::TranscriptAligned => {
            Some(("motionwright-manual-transcript-cue", None))
        }
        AlignmentEvidence::Measured {
            source_sha256,
            confidence_millis,
            ..
        } => {
            let track = project.audio.voice_tracks.iter().find(|track| {
                track.id == segment.voice_track_id && &track.source_sha256 == source_sha256
            })?;
            project.assets.iter().find(|asset| {
                asset.id == track.asset_id && asset.content_sha256.as_ref() == Some(source_sha256)
            })?;
            Some((
                "motionwright-measured-transcript-cue",
                confidence_millis.map(|value| value * 10),
            ))
        }
        _ => None,
    }
}

pub(super) fn project_audio_cues(
    project: &Project,
    scenes: &[Scene],
    segment_start: Rational,
    duration: Rational,
    rate: Rate,
) -> NativeResult<(CueGraph, Vec<MotionCanvasAudioCueAlignment>)> {
    let mut projected = CueGraph {
        version: 1,
        cues: Vec::new(),
    };
    let mut alignment = Vec::new();
    let count = rate
        .quantize(duration, Round::NearestAway)
        .map_err(|error| contract("Motion Canvas cue frame grid is invalid", error))?;
    if count.index <= 0 || count.error != Rational::ZERO {
        return Err(unsupported(
            "Motion Canvas cue timing requires an exact segment frame grid",
        ));
    }

    for cue in &project.audio.cues {
        let previous = project
            .scenes
            .partition_point(|scene| scene.start <= cue.at);
        let Some(index) = previous.checked_sub(1) else {
            continue;
        };
        let source_scene = &project.scenes[index];
        if cue.at >= segment_end(source_scene)? {
            continue;
        }
        let Some(scene) = scenes.iter().find(|scene| scene.id == source_scene.id) else {
            continue;
        };
        if alignment.len() >= MAX_FILM_AUDIO_CUES {
            return Err(unsupported(
                "Motion Canvas audio cue review exceeds 512 cues in one Film",
            ));
        }

        let transcript = cue.source_segment_id.and_then(|id| {
            project
                .audio
                .transcript
                .iter()
                .find(|segment| segment.id == id)
        });
        let Some((method, confidence)) = cue_evidence(project, cue, transcript) else {
            // The pinned driver's unresolved-cues rule is aggregate. Keep a
            // note of unknown alignment without adding a fabricated Native time.
            alignment.push(MotionCanvasAudioCueAlignment::Unknown {
                cue_id: cue.id,
                scene_id: scene.id,
            });
            continue;
        };

        let source_offset = sub(
            cue.at,
            source_scene.start,
            "Audio cue source offset overflow",
        )?;
        let scene_offset = sub(
            scene.start,
            segment_start,
            "Audio cue scene offset overflow",
        )?;
        let local_time = add(scene_offset, source_offset, "Audio cue Film time overflow")?;
        let snapped = rate
            .quantize(local_time, Round::NearestAway)
            .map_err(|error| contract("Motion Canvas cue frame mapping failed", error))?;
        let index = snapped.index.min(count.index - 1);
        let frame = u64::try_from(index)
            .map_err(|_| unsupported("Motion Canvas cue fell outside the Film"))?;
        let residual = sub(
            rate.at(index)
                .map_err(|error| contract("Audio cue frame time is invalid", error))?,
            local_time,
            "Audio cue frame residual overflow",
        )?;
        let source = canonical_digest(&(cue, transcript))
            .map_err(|error| contract("Authored audio cue identity is invalid", error))?;
        projected.cues.push(Cue {
            id: uid("audio-cue", cue.id),
            anchor: Anchor::Absolute { time: local_time },
            duration: Rational::ZERO,
            source,
            method: method.into(),
            version: 1,
            confidence,
        });
        alignment.push(MotionCanvasAudioCueAlignment::Resolved {
            cue_id: cue.id,
            scene_id: scene.id,
            authored_at: cue.at,
            segment_at: local_time,
            frame,
            residual,
        });
    }
    Ok((projected, alignment))
}
