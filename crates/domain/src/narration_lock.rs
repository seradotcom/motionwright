//! Persistent, revision-bound user-recorded narration content decision.
//!
//! Uses the project's existing active measured voice, transcript, associated
//! cues and exact asset digest. Recording a user decision is NOT identity
//! authentication, recording rights, creative quality or publication approval.
use crate::*;
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrationTakeLock {
    pub voice_track_id: Uuid,
    pub voice_asset_id: Uuid,
    pub voice_asset_sha256: String,
    pub source_content_sha256: String,
    pub source_project_revision: u64,
    pub recorded_reviewer: String,
    pub recorded_reason: String,
    /// The existing Studio service records a project change but is not a
    /// licensed e-signature or independent identity-proof authority.
    pub reviewer_identity_authenticated: bool,
    pub media_bytes_independently_decoded: bool,
    pub artistic_quality_approved: bool,
    pub publication_approved: bool,
}
fn sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn review_text(value: &str, budget: usize) -> bool {
    !value.trim().is_empty()
        && value.len() <= budget
        && !value.chars().any(|ch| ch.is_control() && ch != '\n')
}
/// Stable digest over only the active voice source, its words and cues.
/// Source ordering does not influence content identity; no clock is inferred.
pub fn measured_narration_content(
    audio: &AudioState,
    assets: &[Asset],
) -> Result<(Uuid, Uuid, String, String)> {
    audio.validate()?;
    let voice = audio.active_voice_track_id.ok_or_else(|| {
        DomainError::Invalid(
            "Narration content decision requires an explicitly active measured voice".into(),
        )
    })?;
    let track = audio
        .voice_tracks
        .iter()
        .find(|item| item.id == voice)
        .ok_or_else(|| DomainError::NotFound("active measured narration track".into()))?;
    let asset = assets
        .iter()
        .find(|item| item.id == track.asset_id)
        .ok_or_else(|| DomainError::Invalid("Active narration asset is missing".into()))?;
    let source_sha = asset.content_sha256.as_deref().ok_or_else(|| {
        DomainError::Invalid("Narration must have an exact imported asset SHA-256".into())
    })?;
    if !sha(source_sha)
        || source_sha != track.source_sha256
        || !asset.media_type.starts_with("audio/")
    {
        return Err(DomainError::Invalid(
            "Narration audio must be sourced from the exact active measured asset".into(),
        ));
    }
    let mut transcript = audio
        .transcript
        .iter()
        .filter(|item| item.voice_track_id == voice)
        .collect::<Vec<_>>();
    if transcript.is_empty() || transcript.len() > 5000 {
        return Err(DomainError::Invalid(
            "Narration source requires 1..=5000 retained transcript segments".into(),
        ));
    }
    transcript.sort_by_key(|item| item.id);
    for segment in &transcript {
        if segment.start < RationalTime::ZERO
            || segment.end > track.measured_duration
            || !matches!(
                segment.alignment,
                AlignmentEvidence::Manual
                    | AlignmentEvidence::Measured {
                        confidence_millis: Some(800..=1000),
                        ..
                    }
            )
        {
            return Err(DomainError::Invalid(
                "Narration approval requires owner-reviewed or >=0.800 measured alignment, not unknown ASR timing".into()
            ));
        }
    }
    let ids = transcript
        .iter()
        .map(|item| item.id)
        .collect::<std::collections::BTreeSet<_>>();
    let mut cues = audio
        .cues
        .iter()
        .filter(|item| item.source_segment_id.is_some_and(|id| ids.contains(&id)))
        .collect::<Vec<_>>();
    if cues.len() > 5000
        || cues.iter().any(|cue| {
            cue.at > track.measured_duration || matches!(cue.evidence, CueEvidence::Unknown)
        })
    {
        return Err(DomainError::Invalid(
            "Narration cannot lock unknown or out-of-bounds source cue timing".into(),
        ));
    }
    cues.sort_by_key(|cue| cue.id);
    let encoded = serde_json::to_vec(&(track, source_sha, &transcript, &cues))
        .map_err(|e| DomainError::Invalid(format!("Cannot fingerprint narrated source: {e}")))?;
    let digest = hex::encode(Sha256::digest(encoded));
    Ok((track.id, track.asset_id, source_sha.to_owned(), digest))
}
impl NarrationTakeLock {
    pub fn record(
        audio: &AudioState,
        assets: &[Asset],
        current_revision: u64,
        expected_sha: &str,
        reviewer: &str,
        reason: &str,
    ) -> Result<Self> {
        let (voice_track_id, voice_asset_id, voice_asset_sha256, source_content_sha256) =
            measured_narration_content(audio, assets)?;
        if !sha(expected_sha) || source_content_sha256 != expected_sha {
            return Err(DomainError::Invalid(
                "Cannot record stale or unverified original narration take".into(),
            ));
        }
        if !review_text(reviewer, 200) || !review_text(reason, 2000) {
            return Err(DomainError::Invalid(
                "Narration lock needs recorded reviewer and substantive owner reason".into(),
            ));
        }
        Ok(Self {
            voice_track_id,
            voice_asset_id,
            voice_asset_sha256,
            source_content_sha256,
            source_project_revision: current_revision,
            recorded_reviewer: reviewer.into(),
            recorded_reason: reason.into(),
            reviewer_identity_authenticated: false,
            media_bytes_independently_decoded: false,
            artistic_quality_approved: false,
            publication_approved: false,
        })
    }
    pub fn validate(&self, audio: &AudioState, assets: &[Asset]) -> Result<()> {
        if !sha(&self.voice_asset_sha256)
            || !sha(&self.source_content_sha256)
            || !review_text(&self.recorded_reviewer, 200)
            || !review_text(&self.recorded_reason, 2000)
            || self.reviewer_identity_authenticated
            || self.media_bytes_independently_decoded
            || self.artistic_quality_approved
            || self.publication_approved
        {
            return Err(DomainError::Invalid(
                "Recorded narration lock is malformed or falsely asserts external acceptance"
                    .into(),
            ));
        }
        let (track, asset, asset_sha, content_sha) = measured_narration_content(audio, assets)?;
        if self.voice_track_id != track
            || self.voice_asset_id != asset
            || self.voice_asset_sha256 != asset_sha
            || self.source_content_sha256 != content_sha
        {
            return Err(DomainError::Locked(
                "A narration take changed after its recorded source approval".into(),
            ));
        }
        Ok(())
    }
}
