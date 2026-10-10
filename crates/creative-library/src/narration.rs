//! Source-bound, reviewable narration-take snapshots over the EXISTING
//! Motionwright Project.audio voice tracks, transcripts, cues and revisions.
//! No new audio scheduler, ASR guess, paid generation or automatic approval.
use crate::*;
use motionwright_domain::{
    AlignmentEvidence, AudioCue, CueEvidence, Project, RationalTime, TranscriptSegment, VoiceTrack,
};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NarrationSourceSnapshot {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub voice_track: VoiceTrack,
    pub transcript: Vec<TranscriptSegment>,
    pub cues: Vec<AudioCue>,
    pub exact_content_sha256: String,
    pub source_classification: String,
    pub timing_review: String,
    pub human_owner_lock_authenticated: bool,
    pub audio_media_bytes_decoded: bool,
    pub publication_approved: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct NarrationInvalidatedSpan {
    pub source_segment_id: Uuid,
    pub start: RationalTime,
    pub end: RationalTime,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct NarrationReplacementImpact {
    pub schema: String,
    pub previous_project_revision: u64,
    pub next_project_revision: u64,
    pub previous_source_sha256: String,
    pub candidate_source_sha256: String,
    pub changed_segment_ids: Vec<Uuid>,
    pub changed_cue_ids: Vec<Uuid>,
    pub affected_spans: Vec<NarrationInvalidatedSpan>,
    pub voice_media_replaced: bool,
    pub caption_timing_invalidation: String,
    pub cuts_broll_invalidation: String,
    pub sound_mix_invalidation: String,
    pub scene_dependency_validation: String,
    pub origin_locked_source_unchanged: bool,
    pub candidate_committed: bool,
    pub human_owner_approved: bool,
}
fn snapshot_sha(
    project: Uuid,
    generation: Uuid,
    revision: u64,
    track: &VoiceTrack,
    transcript: &[TranscriptSegment],
    cues: &[AudioCue],
) -> Result<String> {
    canonical_digest(&(project, generation, revision, track, transcript, cues))
}
pub fn narration_source_snapshot(project: &Project) -> Result<NarrationSourceSnapshot> {
    project
        .audio
        .validate()
        .map_err(|error| CraftError(error.to_string()))?;
    let track_id = project.audio.active_voice_track_id.ok_or_else(|| {
        CraftError(
            "No active measured source voice; an ASR transcript is not an approved narration take"
                .into(),
        )
    })?;
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|voice| voice.id == track_id)
        .ok_or_else(|| {
            CraftError("Active voice identity does not belong to current project".into())
        })?;
    check(
        valid_sha(&track.source_sha256)
            && project.assets.iter().any(|asset| {
                asset.id == track.asset_id
                    && asset.content_sha256.as_deref() == Some(track.source_sha256.as_str())
                    && asset.media_type.starts_with("audio/")
            }),
        "Narration voice is not attached as an exact source-bound audio asset",
    )?;
    let mut transcript = project
        .audio
        .transcript
        .iter()
        .filter(|entry| entry.voice_track_id == track_id)
        .cloned()
        .collect::<Vec<_>>();
    check(
        !transcript.is_empty() && transcript.len() <= 5000,
        "Narration source needs one to 5000 reviewed transcript segments",
    )?;
    transcript.sort_by(|a, b| a.start.cmp(&b.start).then_with(|| a.id.cmp(&b.id)));
    for segment in &transcript {
        check(
            segment.start >= RationalTime::ZERO && segment.end <= track.measured_duration,
            "Transcript timing escapes the exact measured voice duration",
        )?;
    }
    let mut cues = project
        .audio
        .cues
        .iter()
        .filter(|cue| {
            cue.source_segment_id
                .is_some_and(|id| transcript.iter().any(|segment| segment.id == id))
        })
        .cloned()
        .collect::<Vec<_>>();
    check(cues.len() <= 5000, "Narration cue source budget exceeded")?;
    cues.sort_by(|a, b| a.at.cmp(&b.at).then_with(|| a.id.cmp(&b.id)));
    for cue in &cues {
        check(
            cue.at <= track.measured_duration,
            "Narration cue time exceeds original measured voice duration",
        )?;
    }
    let uncertain = transcript.iter().any(|segment| {
        matches!(&segment.alignment, AlignmentEvidence::Unknown)
            || matches!(
                segment.alignment,
                AlignmentEvidence::Measured {
                    confidence_millis: None | Some(0..=799),
                    ..
                }
            )
    }) || cues
        .iter()
        .any(|cue| matches!(cue.evidence, CueEvidence::Unknown));
    let identity = snapshot_sha(
        project.id,
        project.generation,
        project.revision,
        track,
        &transcript,
        &cues,
    )?;
    Ok(NarrationSourceSnapshot {
        schema: "motionwright.narration-source-snapshot/1".into(),
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        voice_track: track.clone(),
        transcript,
        cues,
        exact_content_sha256: identity,
        source_classification: "EXISTING_PROJECT_AUDIO_NOT_IMPORTED_ASR_TRUTH".into(),
        timing_review: if uncertain {
            "REQUIRES_MANUAL_TIMING_REVIEW"
        } else {
            "SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY"
        }
        .into(),
        human_owner_lock_authenticated: false,
        audio_media_bytes_decoded: false,
        publication_approved: false,
    })
}
fn validate_original(snapshot: &NarrationSourceSnapshot) -> Result<()> {
    check(
        snapshot.schema == "motionwright.narration-source-snapshot/1"
            && !snapshot.human_owner_lock_authenticated
            && !snapshot.publication_approved
            && !snapshot.audio_media_bytes_decoded,
        "Narration source snapshot is not a human-authenticated locked audio take",
    )?;
    check(
        snapshot.exact_content_sha256
            == snapshot_sha(
                snapshot.project_id,
                snapshot.generation,
                snapshot.revision,
                &snapshot.voice_track,
                &snapshot.transcript,
                &snapshot.cues,
            )?,
        "Original narration take fingerprint changed before replacement",
    )?;
    Ok(())
}
pub fn propose_narration_replacement(
    original: &NarrationSourceSnapshot,
    expected_source_sha256: &str,
    next_project: &Project,
) -> Result<NarrationReplacementImpact> {
    validate_original(original)?;
    check(
        valid_sha(expected_source_sha256)
            && original.exact_content_sha256 == expected_source_sha256,
        "Narration source revision changed since owner review",
    )?;
    check(
        original.project_id == next_project.id
            && original.generation == next_project.generation
            && next_project.revision > original.revision,
        "Narration cannot reuse a stale or cross-project revision",
    )?;
    let after = narration_source_snapshot(next_project)?;
    check(
        after.exact_content_sha256 != original.exact_content_sha256,
        "A narrative edit requires new source identity",
    )?;
    let voice_changed = original.voice_track != after.voice_track;
    let old_segments = original
        .transcript
        .iter()
        .map(|s| (s.id, s))
        .collect::<BTreeMap<_, _>>();
    let new_segments = after
        .transcript
        .iter()
        .map(|s| (s.id, s))
        .collect::<BTreeMap<_, _>>();
    let mut changed_ids = BTreeSet::new();
    let mut spans = Vec::new();
    for id in old_segments.keys().chain(new_segments.keys()) {
        if old_segments.get(id) == new_segments.get(id) {
            continue;
        }
        if !changed_ids.insert(*id) {
            continue;
        }
        for reference in [old_segments.get(id).copied(), new_segments.get(id).copied()]
            .into_iter()
            .flatten()
        {
            spans.push(NarrationInvalidatedSpan {
                source_segment_id: *id,
                start: reference.start,
                end: reference.end,
            });
        }
    }
    if voice_changed {
        for segment in original.transcript.iter().chain(after.transcript.iter()) {
            changed_ids.insert(segment.id);
            spans.push(NarrationInvalidatedSpan {
                source_segment_id: segment.id,
                start: segment.start,
                end: segment.end,
            });
        }
    }
    let old_cues = original
        .cues
        .iter()
        .map(|cue| (cue.id, cue))
        .collect::<BTreeMap<_, _>>();
    let new_cues = after
        .cues
        .iter()
        .map(|cue| (cue.id, cue))
        .collect::<BTreeMap<_, _>>();
    let mut cue_ids = BTreeSet::new();
    for id in old_cues.keys().chain(new_cues.keys()) {
        if old_cues.get(id) != new_cues.get(id) {
            cue_ids.insert(*id);
        }
    }
    for cue in original.cues.iter().chain(after.cues.iter()) {
        if cue
            .source_segment_id
            .is_some_and(|id| changed_ids.contains(&id))
        {
            cue_ids.insert(cue.id);
        }
    }
    check(
        voice_changed || !changed_ids.is_empty() || !cue_ids.is_empty(),
        "An unrelated project edit cannot pretend to replace narration",
    )?;
    spans.sort_by(|a, b| {
        a.start
            .cmp(&b.start)
            .then_with(|| a.source_segment_id.cmp(&b.source_segment_id))
    });
    spans.dedup();
    Ok(NarrationReplacementImpact {
        schema: "motionwright.narration-impact-preview/1".into(),
        previous_project_revision: original.revision,
        next_project_revision: next_project.revision,
        previous_source_sha256: original.exact_content_sha256.clone(),
        candidate_source_sha256: after.exact_content_sha256,
        changed_segment_ids: changed_ids.into_iter().collect(),
        changed_cue_ids: cue_ids.into_iter().collect(),
        affected_spans: spans,
        voice_media_replaced: voice_changed,
        caption_timing_invalidation: "CAPTIONS_MUST_REGENERATE_AND_HUMAN_REVIEW".into(),
        cuts_broll_invalidation: "CUTS_AND_BROLL_REQUIRE_REEVALUATION".into(),
        sound_mix_invalidation: if voice_changed {
            "MIX_VOICE_BUS_MUST_REMEASURE"
        } else {
            "MIX_MUST_REVIEW_CUE_AND_TIMING_CONSEQUENCES"
        }
        .into(),
        scene_dependency_validation: "NOT_RUN".into(),
        origin_locked_source_unchanged: true,
        candidate_committed: false,
        human_owner_approved: false,
    })
}
