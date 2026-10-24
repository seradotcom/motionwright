//! Atomic installation of a fully resolved, qualified voice alignment.
//!
//! These serializable values are journal data, not a capability. Only the
//! private native caller may construct this Change from an authenticated,
//! qualified observation; generic adapters must reject the entire variant.
use crate::{
    AlignmentEvidence, AudioCue, CueEvidence, DomainError, LockKind, Project, RationalTime, Result,
    TranscriptSegment,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use uuid::Uuid;

pub const VOICE_ALIGNMENT_PROFILE_V1: &str = "acc007-en-ctc-1";
pub const MAX_VOICE_ALIGNMENT_TRANSCRIPT_VERSION: u64 = 9_007_199_254_740_991;

/// An integrity reference to the actual durable observation. Its presence
/// never authenticates a native invocation or grants permission to edit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiceAlignmentReceiptRef {
    pub id: Uuid,
    pub request_id: String,
    pub request_sha256: String,
    pub command: String,
    /// motionwright_storage::voice_alignment_receipt_sha256_v1 of the actual
    /// receipt returned after its successful durable append.
    pub receipt_sha256: String,
}

/// Initial add-only profile. IDs and the whole payload are retained by the
/// caller for exact idempotency; the domain generates no identities or times.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiceAlignmentApplication {
    pub profile: String,
    pub voice_track_id: Uuid,
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub transcript_version: u64,
    pub transcript_text: String,
    pub transcript_sha256: String,
    pub receipt: VoiceAlignmentReceiptRef,
    pub segments: Vec<TranscriptSegment>,
    pub cues: Vec<AudioCue>,
}

fn invalid(message: &str) -> DomainError {
    DomainError::Invalid(format!("voice alignment: {message}"))
}

fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn token(value: &str) -> bool {
    !value.is_empty() && value.len() <= 256 && !value.chars().any(char::is_control)
}

fn word(value: &str) -> bool {
    !value.is_empty()
        && value
            .split('\'')
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_alphabetic()))
}

impl VoiceAlignmentApplication {
    // Structural consistency only. Authentication and qualification belong to
    // the private native boundary; this validator cannot create that authority.
    fn validate_for(&self, project: &Project) -> Result<()> {
        if self.profile != VOICE_ALIGNMENT_PROFILE_V1
            || !(1..=MAX_VOICE_ALIGNMENT_TRANSCRIPT_VERSION).contains(&self.transcript_version)
            || self.transcript_text.is_empty()
            || self.transcript_text.len() > 1024
            || !digest(&self.transcript_sha256)
            || hex::encode(Sha256::digest(self.transcript_text.as_bytes()))
                != self.transcript_sha256
        {
            return Err(invalid(
                "unsupported profile or changed transcript snapshot",
            ));
        }
        let words: Vec<&str> = self.transcript_text.split(' ').collect();
        if words.len() > 128
            || words.iter().any(|value| !word(value))
            || self.segments.len() != words.len()
            || self.cues.len() > self.segments.len()
        {
            return Err(invalid("the complete exact word roster is required"));
        }
        if self.receipt.id.is_nil()
            || !token(&self.receipt.request_id)
            || !token(&self.receipt.command)
            || !digest(&self.receipt.request_sha256)
            || !digest(&self.receipt.receipt_sha256)
            || !digest(&self.source_sha256)
        {
            return Err(invalid("invalid durable observation reference"));
        }
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == self.voice_track_id)
            .ok_or_else(|| invalid("selected voice does not exist"))?;
        if project.audio.active_voice_track_id != Some(self.voice_track_id)
            || track.asset_id != self.asset_id
            || track.source_sha256 != self.source_sha256
            || track.measured_duration.validate().is_err()
            || !project.assets.iter().any(|asset| {
                asset.id == self.asset_id
                    && asset.media_type.starts_with("audio/")
                    && asset.content_sha256.as_deref() == Some(self.source_sha256.as_str())
            })
        {
            return Err(invalid(
                "selected voice and exact source asset must still agree",
            ));
        }
        if project
            .audio
            .transcript
            .iter()
            .any(|segment| segment.voice_track_id == self.voice_track_id)
        {
            return Err(invalid(
                "this add-only profile cannot replace an existing transcript",
            ));
        }
        let mut segment_ids: HashSet<Uuid> = project
            .audio
            .transcript
            .iter()
            .map(|segment| segment.id)
            .collect();
        let mut previous_end = RationalTime::ZERO;
        let mut observed_engine: Option<&str> = None;
        for (segment, expected_word) in self.segments.iter().zip(words) {
            if segment.id.is_nil()
                || !segment_ids.insert(segment.id)
                || segment.voice_track_id != self.voice_track_id
                || segment.text != expected_word
                || segment.speaker.is_some()
                || segment.start.validate().is_err()
                || segment.end.validate().is_err()
                || segment.start < previous_end
                || segment.end <= segment.start
                || segment.end > track.measured_duration
            {
                return Err(invalid(
                    "word identity, order or resolved interval is invalid",
                ));
            }
            match &segment.alignment {
                AlignmentEvidence::Measured {
                    engine,
                    source_sha256,
                    confidence_millis: None,
                } if token(engine) && source_sha256 == &self.source_sha256 => {
                    if observed_engine.is_some_and(|prior| prior != engine.as_str()) {
                        return Err(invalid("one qualified result cannot mix alignment engines"));
                    }
                    observed_engine = Some(engine);
                }
                _ => {
                    return Err(invalid(
                        "every word needs qualified source evidence without invented confidence",
                    ));
                }
            }
            previous_end = segment.end;
        }
        let mut cue_ids: HashSet<Uuid> = project.audio.cues.iter().map(|cue| cue.id).collect();
        let mut cued_segments = HashSet::new();
        for cue in &self.cues {
            let segment = self
                .segments
                .iter()
                .find(|segment| Some(segment.id) == cue.source_segment_id)
                .ok_or_else(|| invalid("a new cue must reference a new word in this result"))?;
            if cue.id.is_nil()
                || !cue_ids.insert(cue.id)
                || !cued_segments.insert(segment.id)
                || cue.at.validate().is_err()
                || cue.at != segment.start
                || cue.evidence != CueEvidence::TranscriptAligned
            {
                return Err(invalid(
                    "cue identity, ownership or exact word start is invalid",
                ));
            }
        }
        Ok(())
    }
}

impl Project {
    pub(crate) fn apply_voice_alignment_in_place(
        &mut self,
        application: &VoiceAlignmentApplication,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
        application.validate_for(self)?;
        self.audio
            .transcript
            .extend(application.segments.iter().cloned());
        self.audio.cues.extend(application.cues.iter().cloned());
        // The enclosing clone/validate/assign path also validates all existing
        // dependencies. No rejected Change may leave a partially installed set.
        self.audio.validate()
    }
}
