use crate::{DomainError, RationalTime, Result, non_negative};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct Brief {
    pub objective: String,
    pub audience: String,
    pub constraints: Vec<String>,
    pub exclusions: Vec<String>,
    pub claims: Vec<Claim>,
}

impl Brief {
    pub fn validate(&self) -> Result<()> {
        bounded_text(&self.objective, 8_000, "brief objective")?;
        bounded_text(&self.audience, 2_000, "brief audience")?;
        bounded_list(&self.constraints, 128, 1_000, "brief constraints")?;
        bounded_list(&self.exclusions, 128, 1_000, "brief exclusions")?;
        if self.claims.len() > 512 {
            return Err(DomainError::Invalid("too many claims".into()));
        }
        let mut ids = HashSet::new();
        for claim in &self.claims {
            claim.validate()?;
            if !ids.insert(claim.id) {
                return Err(DomainError::Invalid("duplicate claim id".into()));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Claim {
    pub id: Uuid,
    pub text: String,
    pub source: Option<SourceReference>,
    pub context: String,
    pub source_revision: Option<String>,
}

impl Claim {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.text, 4_000, "claim text")?;
        bounded_text(&self.context, 4_000, "claim context")?;
        if let Some(source) = &self.source {
            source.validate()?;
        }
        if self
            .source_revision
            .as_ref()
            .is_some_and(|value| value.len() > 256)
        {
            return Err(DomainError::Invalid(
                "claim source revision is too long".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum SourceReference {
    Asset { asset_id: Uuid },
    Url { url: String },
    ProjectRevision { revision: u64 },
    ManualNote { label: String },
}

impl SourceReference {
    fn validate(&self) -> Result<()> {
        match self {
            Self::Asset { .. } | Self::ProjectRevision { .. } => Ok(()),
            Self::Url { url } => nonempty_bounded_text(url, 2_048, "source url"),
            Self::ManualNote { label } => nonempty_bounded_text(label, 512, "source note"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct Narrative {
    pub premise: String,
    pub beats: Vec<NarrativeBeat>,
    pub protected_sections: Vec<ProtectedSection>,
}

impl Narrative {
    pub fn validate(&self) -> Result<()> {
        bounded_text(&self.premise, 12_000, "narrative premise")?;
        if self.beats.len() > 2_048 || self.protected_sections.len() > 512 {
            return Err(DomainError::Invalid(
                "narrative collection is too large".into(),
            ));
        }
        let mut ids = HashSet::new();
        for beat in &self.beats {
            beat.validate()?;
            if !ids.insert(beat.id) {
                return Err(DomainError::Invalid("duplicate narrative beat".into()));
            }
        }
        for section in &self.protected_sections {
            section.validate()?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeBeat {
    pub id: Uuid,
    pub label: String,
    pub objective: String,
    pub audience_takeaway: String,
    pub claim_ids: Vec<Uuid>,
    pub preferred_duration: Option<RationalTime>,
}

impl NarrativeBeat {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.label, 256, "beat label")?;
        nonempty_bounded_text(&self.objective, 4_000, "beat objective")?;
        bounded_text(&self.audience_takeaway, 2_000, "beat audience takeaway")?;
        if self.claim_ids.len() > 128 {
            return Err(DomainError::Invalid("too many claims on a beat".into()));
        }
        if let Some(duration) = self.preferred_duration
            && (!non_negative(duration) || duration.num <= 0)
        {
            return Err(DomainError::Invalid(
                "invalid preferred beat duration".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProtectedSection {
    pub beat_id: Uuid,
    pub reason: String,
}

impl ProtectedSection {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.reason, 1_000, "protected-section reason")
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct AudioState {
    pub voice_tracks: Vec<VoiceTrack>,
    pub transcript: Vec<TranscriptSegment>,
    pub cues: Vec<AudioCue>,
    pub mix: MixIntent,
}

impl AudioState {
    pub fn validate(&self) -> Result<()> {
        if self.voice_tracks.len() > 64
            || self.transcript.len() > 50_000
            || self.cues.len() > 20_000
        {
            return Err(DomainError::Invalid("audio collection is too large".into()));
        }
        let mut ids = HashSet::new();
        for track in &self.voice_tracks {
            track.validate()?;
            if !ids.insert(track.id) {
                return Err(DomainError::Invalid("duplicate voice track id".into()));
            }
        }
        let track_ids = ids.clone();
        ids.clear();
        for segment in &self.transcript {
            segment.validate()?;
            if !track_ids.contains(&segment.voice_track_id) {
                return Err(DomainError::Invalid(
                    "transcript segment references an unknown voice track".into(),
                ));
            }
            if !ids.insert(segment.id) {
                return Err(DomainError::Invalid(
                    "duplicate transcript segment id".into(),
                ));
            }
        }
        let segment_ids = ids.clone();
        ids.clear();
        for cue in &self.cues {
            cue.validate()?;
            if cue
                .source_segment_id
                .is_some_and(|id| !segment_ids.contains(&id))
            {
                return Err(DomainError::Invalid(
                    "audio cue references an unknown transcript segment".into(),
                ));
            }
            if !ids.insert(cue.id) {
                return Err(DomainError::Invalid("duplicate audio cue id".into()));
            }
        }
        self.mix.validate()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiceTrack {
    pub id: Uuid,
    pub asset_id: Uuid,
    pub label: String,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub measured_duration: RationalTime,
    pub source_sha256: String,
    pub loudness_lufs: Option<f32>,
    pub true_peak_dbfs: Option<f32>,
}

impl VoiceTrack {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.label, 256, "voice label")?;
        if !matches!(self.sample_rate_hz, 44_100 | 48_000 | 96_000)
            || self.channels == 0
            || self.channels > 32
            || !non_negative(self.measured_duration)
            || self.measured_duration.num <= 0
            || !sha256(&self.source_sha256)
        {
            return Err(DomainError::Invalid("invalid measured voice track".into()));
        }
        if self.loudness_lufs.is_some_and(|value| !value.is_finite())
            || self.true_peak_dbfs.is_some_and(|value| !value.is_finite())
        {
            return Err(DomainError::Invalid("invalid audio measurement".into()));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TranscriptSegment {
    pub id: Uuid,
    pub voice_track_id: Uuid,
    pub start: RationalTime,
    pub end: RationalTime,
    pub text: String,
    pub speaker: Option<String>,
    pub alignment: AlignmentEvidence,
}

impl TranscriptSegment {
    fn validate(&self) -> Result<()> {
        if !non_negative(self.start) || self.end <= self.start {
            return Err(DomainError::Invalid("invalid transcript interval".into()));
        }
        nonempty_bounded_text(&self.text, 8_000, "transcript text")?;
        if self.speaker.as_ref().is_some_and(|value| value.len() > 256) {
            return Err(DomainError::Invalid("speaker label is too long".into()));
        }
        self.alignment.validate()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum AlignmentEvidence {
    Manual,
    Measured {
        engine: String,
        source_sha256: String,
        confidence_millis: Option<u16>,
    },
    Unknown,
}

impl AlignmentEvidence {
    fn validate(&self) -> Result<()> {
        if let Self::Measured {
            engine,
            source_sha256,
            confidence_millis,
        } = self
        {
            nonempty_bounded_text(engine, 256, "alignment engine")?;
            if !sha256(source_sha256) || confidence_millis.is_some_and(|value| value > 1_000) {
                return Err(DomainError::Invalid("invalid alignment evidence".into()));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioCue {
    pub id: Uuid,
    pub label: String,
    pub at: RationalTime,
    pub source_segment_id: Option<Uuid>,
    pub evidence: CueEvidence,
}

impl AudioCue {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.label, 512, "audio cue label")?;
        if !non_negative(self.at) {
            return Err(DomainError::Invalid("negative audio cue".into()));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CueEvidence {
    Manual,
    TranscriptAligned,
    Measured,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MixIntent {
    pub voice_gain_db: f32,
    pub music_gain_db: f32,
    pub target_lufs: Option<f32>,
    pub target_true_peak_dbfs: Option<f32>,
}

impl Default for MixIntent {
    fn default() -> Self {
        Self {
            voice_gain_db: 0.0,
            music_gain_db: -12.0,
            target_lufs: None,
            target_true_peak_dbfs: None,
        }
    }
}

impl MixIntent {
    fn validate(&self) -> Result<()> {
        for value in [self.voice_gain_db, self.music_gain_db] {
            if !value.is_finite() || !(-120.0..=24.0).contains(&value) {
                return Err(DomainError::Invalid("mix gain is out of bounds".into()));
            }
        }
        if self.target_lufs.is_some_and(|value| !value.is_finite())
            || self
                .target_true_peak_dbfs
                .is_some_and(|value| !value.is_finite())
        {
            return Err(DomainError::Invalid("mix target is invalid".into()));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VisualLanguage {
    pub version: u32,
    pub name: String,
    pub palette: Vec<VisualToken>,
    pub type_tokens: Vec<VisualToken>,
    pub motion_grammar: Vec<MotionVerb>,
    pub anti_slop_rules: Vec<String>,
    pub fonts: Vec<FontRight>,
}

impl Default for VisualLanguage {
    fn default() -> Self {
        Self {
            version: 1,
            name: "Cut Room Ledger".into(),
            palette: vec![],
            type_tokens: vec![],
            motion_grammar: vec![],
            anti_slop_rules: vec![],
            fonts: vec![],
        }
    }
}

impl VisualLanguage {
    pub fn validate(&self) -> Result<()> {
        if self.version == 0 || self.version > 1_000 {
            return Err(DomainError::Invalid(
                "visual-language version is invalid".into(),
            ));
        }
        nonempty_bounded_text(&self.name, 256, "visual-language name")?;
        if self.palette.len() > 256
            || self.type_tokens.len() > 256
            || self.motion_grammar.len() > 128
            || self.anti_slop_rules.len() > 512
            || self.fonts.len() > 128
        {
            return Err(DomainError::Invalid(
                "visual-language collection is too large".into(),
            ));
        }
        for token in self.palette.iter().chain(&self.type_tokens) {
            token.validate()?;
        }
        for verb in &self.motion_grammar {
            verb.validate()?;
        }
        bounded_list(&self.anti_slop_rules, 512, 1_000, "anti-slop rules")?;
        for font in &self.fonts {
            font.validate()?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VisualToken {
    pub name: String,
    pub value: String,
}

impl VisualToken {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.name, 128, "token name")?;
        nonempty_bounded_text(&self.value, 1_024, "token value")
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionVerb {
    pub name: String,
    pub meaning: String,
    pub duration_ms: u32,
    pub reduced_motion: ReducedMotionBehavior,
}

impl MotionVerb {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.name, 128, "motion verb")?;
        nonempty_bounded_text(&self.meaning, 1_000, "motion meaning")?;
        if self.duration_ms > 120_000 {
            return Err(DomainError::Invalid("motion duration is too large".into()));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReducedMotionBehavior {
    Instant,
    FadeOnly,
    StaticEquivalent,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FontRight {
    pub family: String,
    pub source: String,
    pub rights_status: RightsStatus,
}

impl FontRight {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.family, 256, "font family")?;
        bounded_text(&self.source, 2_048, "font source")
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RightsStatus {
    Cleared,
    Restricted,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProposalSet {
    pub id: Uuid,
    pub base_revision: u64,
    pub scope: ProposalScope,
    pub search_budget: SearchBudget,
    pub proposals: Vec<Proposal>,
    pub selected: Option<Uuid>,
}

impl ProposalSet {
    pub fn validate(&self) -> Result<()> {
        if self.proposals.len() < 2 || self.proposals.len() > 12 {
            return Err(DomainError::Invalid(
                "proposal set must contain 2..=12 alternatives".into(),
            ));
        }
        let mut ids = HashSet::new();
        for proposal in &self.proposals {
            proposal.validate()?;
            if !ids.insert(proposal.id) {
                return Err(DomainError::Invalid("duplicate proposal id".into()));
            }
        }
        if self.selected.is_some_and(|id| !ids.contains(&id)) {
            return Err(DomainError::Invalid(
                "selected proposal is not in its set".into(),
            ));
        }
        self.search_budget.validate()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ProposalScope {
    Project,
    Scene { scene_id: Uuid },
    Selection { resource_refs: Vec<String> },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchBudget {
    pub candidates: u16,
    pub model_calls: u16,
    pub max_tokens: u32,
}

impl SearchBudget {
    fn validate(&self) -> Result<()> {
        if self.candidates < 2
            || self.candidates > 64
            || self.model_calls > 128
            || self.max_tokens > 2_000_000
        {
            return Err(DomainError::Invalid(
                "creative search budget is out of bounds".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub id: Uuid,
    pub title: String,
    pub rationale: String,
    pub structure: Vec<String>,
    pub edits: Vec<CreativeEdit>,
}

impl Proposal {
    fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.title, 256, "proposal title")?;
        bounded_text(&self.rationale, 4_000, "proposal rationale")?;
        bounded_list(&self.structure, 128, 1_000, "proposal structure")?;
        if self.edits.is_empty() || self.edits.len() > 256 {
            return Err(DomainError::Invalid(
                "proposal edit count is out of bounds".into(),
            ));
        }
        for edit in &self.edits {
            edit.validate()?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CreativeEdit {
    SceneObjective { scene_id: Uuid, objective: String },
    SceneOrder { scene_ids: Vec<Uuid> },
    BeatRewrite { beat_id: Uuid, objective: String },
    RendererChoice { scene_id: Uuid, renderer: String },
}

impl CreativeEdit {
    fn validate(&self) -> Result<()> {
        match self {
            Self::SceneObjective { objective, .. } | Self::BeatRewrite { objective, .. } => {
                nonempty_bounded_text(objective, 4_000, "proposal edit text")
            }
            Self::SceneOrder { scene_ids } => {
                if scene_ids.is_empty() || scene_ids.len() > 2_048 {
                    Err(DomainError::Invalid(
                        "proposal scene order is out of bounds".into(),
                    ))
                } else {
                    Ok(())
                }
            }
            Self::RendererChoice { renderer, .. } => {
                nonempty_bounded_text(renderer, 128, "renderer choice")
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelInvocationReceipt {
    pub id: Uuid,
    pub provider: String,
    pub model: String,
    pub provider_version: Option<String>,
    pub base_revision: u64,
    pub resource_refs: Vec<String>,
    pub data_classes: Vec<DataClass>,
    pub budget: InvocationBudget,
    pub outcome: InvocationOutcome,
}

impl ModelInvocationReceipt {
    pub fn validate(&self) -> Result<()> {
        nonempty_bounded_text(&self.provider, 256, "provider")?;
        nonempty_bounded_text(&self.model, 256, "model")?;
        if self
            .provider_version
            .as_ref()
            .is_some_and(|value| value.len() > 256)
            || self.resource_refs.len() > 512
            || self.data_classes.len() > 32
        {
            return Err(DomainError::Invalid(
                "model invocation receipt is too large".into(),
            ));
        }
        self.budget.validate()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DataClass {
    Metadata,
    Text,
    Frame,
    Audio,
    SourceCode,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InvocationBudget {
    pub max_calls: u16,
    pub max_tokens: u32,
    pub max_cost_microunits: Option<u64>,
}

impl InvocationBudget {
    fn validate(&self) -> Result<()> {
        if self.max_calls == 0 || self.max_calls > 128 || self.max_tokens > 2_000_000 {
            return Err(DomainError::Invalid(
                "model invocation budget is out of bounds".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InvocationOutcome {
    Succeeded,
    Failed,
    Cancelled,
    Unknown,
}

fn sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn bounded_text(value: &str, max: usize, field: &str) -> Result<()> {
    if value.len() > max {
        Err(DomainError::Invalid(format!("{field} is too long")))
    } else {
        Ok(())
    }
}

fn nonempty_bounded_text(value: &str, max: usize, field: &str) -> Result<()> {
    if value.trim().is_empty() || value.len() > max {
        Err(DomainError::Invalid(format!(
            "{field} is empty or too long"
        )))
    } else {
        Ok(())
    }
}

fn bounded_list(values: &[String], max_items: usize, max_item: usize, field: &str) -> Result<()> {
    if values.len() > max_items || values.iter().any(|value| value.len() > max_item) {
        Err(DomainError::Invalid(format!("{field} is out of bounds")))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measured_audio_rejects_fabricated_hash_shape() {
        let state = AudioState {
            voice_tracks: vec![VoiceTrack {
                id: Uuid::now_v7(),
                asset_id: Uuid::now_v7(),
                label: "Voice".into(),
                sample_rate_hz: 48_000,
                channels: 1,
                measured_duration: RationalTime::new(10, 1).unwrap(),
                source_sha256: "not-evidence".into(),
                loudness_lufs: None,
                true_peak_dbfs: None,
            }],
            ..Default::default()
        };
        assert!(state.validate().is_err());
    }

    #[test]
    fn proposal_set_requires_real_divergence_count() {
        let set = ProposalSet {
            id: Uuid::now_v7(),
            base_revision: 0,
            scope: ProposalScope::Project,
            search_budget: SearchBudget {
                candidates: 1,
                model_calls: 1,
                max_tokens: 1_000,
            },
            proposals: vec![],
            selected: None,
        };
        assert!(set.validate().is_err());
    }
}
