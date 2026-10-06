mod canvas;
mod creative;
mod delivery;
mod extensions;
mod history;
mod integrations;
pub use canvas::*;
pub use creative::*;
pub use delivery::*;
pub use extensions::*;
pub use history::*;
pub use integrations::*;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, HashSet};
use thiserror::Error;
use uuid::Uuid;

pub const PROJECT_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum DomainError {
    #[error("invalid project: {0}")]
    Invalid(String),
    #[error("resource is locked: {0}")]
    Locked(String),
    #[error("resource not found: {0}")]
    NotFound(String),
}

pub type Result<T> = std::result::Result<T, DomainError>;

pub use semwright_media_time::Rational as RationalTime;

fn whole_seconds(value: i64) -> RationalTime {
    RationalTime { num: value, den: 1 }
}

fn non_negative(value: RationalTime) -> bool {
    value.validate().is_ok() && value >= RationalTime::ZERO
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProjectState {
    Current,
    Stale,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SceneStatus {
    Draft,
    Review,
    Approved,
    NeedsWork,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RendererKind {
    MotionCanvas,
    Mlt,
    Blender,
    ManimCommunity,
    Remotion,
    ManimGl,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LockKind {
    Content,
    Timing,
    Position,
    Style,
    Renderer,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectLock {
    pub id: Uuid,
    pub resource: String,
    pub kind: LockKind,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Beat {
    pub id: Uuid,
    pub label: String,
    pub objective: String,
    pub start: RationalTime,
    pub duration: RationalTime,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanvasNode {
    pub id: Uuid,
    pub name: String,
    pub kind: String,
    pub parent_id: Option<Uuid>,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub rotation_deg: f64,
    pub opacity: f64,
    pub text: Option<String>,
    #[serde(default)]
    pub coordinate_space: CoordinateSpace,
    #[serde(default)]
    pub z_index: i32,
    #[serde(default)]
    pub style: NodeStyle,
    #[serde(default)]
    pub relations: Vec<NodeRelation>,
    #[serde(default)]
    pub property_locks: BTreeSet<NodeProperty>,
    #[serde(default)]
    pub keyframes: Vec<CanvasKeyframe>,
}

impl CanvasNode {
    fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid(
                "canvas node name is out of bounds".into(),
            ));
        }
        if self.kind.trim().is_empty() || self.kind.len() > 128 {
            return Err(DomainError::Invalid(
                "canvas node kind is out of bounds".into(),
            ));
        }
        CanvasTransform {
            x: self.x,
            y: self.y,
            width: self.width,
            height: self.height,
            rotation_deg: self.rotation_deg,
            opacity: self.opacity,
        }
        .validate()?;
        self.style.validate()?;
        if self
            .text
            .as_ref()
            .is_some_and(|value| value.len() > 100_000)
            || self.relations.len() > 128
            || self.property_locks.len() > 8
            || self.keyframes.len() > 128
        {
            return Err(DomainError::Invalid(
                "canvas node content exceeds bounded limits".into(),
            ));
        }
        for (index, keyframe) in self.keyframes.iter().enumerate() {
            keyframe.validate()?;
            if self.keyframes[..index].iter().any(|existing| {
                existing.at == keyframe.at && existing.property == keyframe.property
            }) {
                return Err(DomainError::Invalid(
                    "duplicate canvas keyframe time/property".into(),
                ));
            }
        }
        let mut relation_ids = HashSet::new();
        for relation in &self.relations {
            if relation.target_id == self.id || !relation_ids.insert(relation.id) {
                return Err(DomainError::Invalid(
                    "canvas relation is self-referential or duplicated".into(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scene {
    pub id: Uuid,
    pub name: String,
    pub objective: String,
    pub start: RationalTime,
    pub duration: RationalTime,
    pub renderer: RendererKind,
    pub status: SceneStatus,
    pub beats: Vec<Beat>,
    pub nodes: Vec<CanvasNode>,
    #[serde(default)]
    pub camera: CameraState,
}

impl Scene {
    fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid("scene name is out of bounds".into()));
        }
        if !non_negative(self.start) || self.duration.num <= 0 || self.duration.den <= 0 {
            return Err(DomainError::Invalid(format!(
                "scene {} has invalid time",
                self.id
            )));
        }
        let mut ids = HashSet::new();
        for node in &self.nodes {
            node.validate()?;
            if node
                .keyframes
                .iter()
                .any(|keyframe| keyframe.at >= self.duration)
            {
                return Err(DomainError::Invalid(
                    "canvas keyframe must stay inside the half-open scene interval".into(),
                ));
            }
            if !ids.insert(node.id) {
                return Err(DomainError::Invalid("duplicate canvas node id".into()));
            }
        }
        for node in &self.nodes {
            if let Some(parent) = node.parent_id
                && (parent == node.id || !ids.contains(&parent))
            {
                return Err(DomainError::Invalid(
                    "canvas hierarchy contains an invalid parent".into(),
                ));
            }
            if node
                .relations
                .iter()
                .any(|relation| !ids.contains(&relation.target_id))
            {
                return Err(DomainError::Invalid(
                    "canvas relation target is outside the scene".into(),
                ));
            }

            let mut parent = node.parent_id;
            let mut visited = HashSet::new();
            for _ in 0..self.nodes.len() {
                let Some(parent_id) = parent else {
                    break;
                };
                if !visited.insert(parent_id) {
                    return Err(DomainError::Invalid(
                        "canvas hierarchy contains a cycle".into(),
                    ));
                }
                parent = self
                    .nodes
                    .iter()
                    .find(|candidate| candidate.id == parent_id)
                    .and_then(|candidate| candidate.parent_id);
            }
            if parent.is_some() {
                return Err(DomainError::Invalid(
                    "canvas hierarchy exceeds bounded traversal".into(),
                ));
            }
        }
        self.camera.validate()?;
        let mut beat_ids = HashSet::new();
        for beat in &self.beats {
            if !beat_ids.insert(beat.id) || beat.duration.num <= 0 || !non_negative(beat.start) {
                return Err(DomainError::Invalid("invalid or duplicate beat".into()));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Marker {
    pub id: Uuid,
    pub at: RationalTime,
    pub label: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Asset {
    pub id: Uuid,
    pub name: String,
    pub media_type: String,
    pub content_sha256: Option<String>,
    pub source_revision: Option<String>,
}

impl Asset {
    fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 512 {
            return Err(DomainError::Invalid("asset name is out of bounds".into()));
        }
        if self.media_type.trim().is_empty()
            || self.media_type.len() > 255
            || self.media_type.chars().any(char::is_whitespace)
        {
            return Err(DomainError::Invalid(
                "asset media type is out of bounds".into(),
            ));
        }
        if let Some(digest) = &self.content_sha256
            && (digest.len() != 64
                || !digest
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
        {
            return Err(DomainError::Invalid("asset digest is invalid".into()));
        }
        if self
            .source_revision
            .as_ref()
            .is_some_and(|value| value.len() > 512 || value.chars().any(char::is_control))
        {
            return Err(DomainError::Invalid(
                "asset source revision is out of bounds".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Branch {
    pub id: Uuid,
    pub name: String,
    #[serde(default)]
    pub parent_branch: Option<Uuid>,
    pub base_revision: u64,
    #[serde(default)]
    pub head_revision: u64,
    #[serde(default)]
    pub protected: bool,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeliverableProfile {
    pub id: Uuid,
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub language: String,
    pub captions: bool,
    #[serde(default)]
    pub caption_format: CaptionFormat,
    #[serde(default)]
    pub video_codec: VideoCodec,
    #[serde(default)]
    pub audio_codec: AudioCodec,
    #[serde(default = "default_audio_sample_rate_hz")]
    pub audio_sample_rate_hz: u32,
    #[serde(default)]
    pub brand_profile: Option<String>,
    #[serde(default)]
    pub cut_label: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Project {
    pub schema_version: u32,
    pub id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub title: String,
    pub state: ProjectState,
    pub active_branch: Uuid,
    pub branches: Vec<Branch>,
    #[serde(default)]
    pub branch_workspaces: Vec<BranchWorkspace>,
    #[serde(default)]
    pub reviews: Vec<CreativeReview>,
    #[serde(default)]
    pub merges: Vec<MergeRecord>,
    pub scenes: Vec<Scene>,
    pub markers: Vec<Marker>,
    pub assets: Vec<Asset>,
    pub locks: Vec<ProjectLock>,
    pub deliverables: Vec<DeliverableProfile>,
    #[serde(default)]
    pub brief: Brief,
    #[serde(default)]
    pub narrative: Narrative,
    #[serde(default)]
    pub audio: AudioState,
    #[serde(default)]
    pub visual_language: VisualLanguage,
    #[serde(default)]
    pub proposal_sets: Vec<ProposalSet>,
    #[serde(default)]
    pub model_invocations: Vec<ModelInvocationReceipt>,
    #[serde(default)]
    pub extensions: Vec<ExtensionProfile>,
    #[serde(default)]
    pub handoffs: Vec<HandoffBinding>,
    pub updated_at: DateTime<Utc>,
}

impl Project {
    pub fn new(title: impl Into<String>) -> Result<Self> {
        let title = title.into();
        if title.trim().is_empty() || title.len() > 200 {
            return Err(DomainError::Invalid(
                "project title is out of bounds".into(),
            ));
        }
        let branch_id = Uuid::now_v7();
        let now = Utc::now();
        let project = Self {
            schema_version: PROJECT_SCHEMA_VERSION,
            id: Uuid::now_v7(),
            generation: Uuid::now_v7(),
            revision: 0,
            title,
            state: ProjectState::Current,
            active_branch: branch_id,
            branches: vec![Branch {
                id: branch_id,
                name: "main".into(),
                parent_branch: None,
                base_revision: 0,
                head_revision: 0,
                protected: false,
                created_at: now,
            }],
            branch_workspaces: vec![],
            reviews: vec![],
            merges: vec![],
            scenes: vec![],
            markers: vec![],
            assets: vec![],
            locks: vec![],
            deliverables: vec![
                DeliverableProfile {
                    id: Uuid::now_v7(),
                    name: "Master 16:9".into(),
                    width: 1920,
                    height: 1080,
                    language: "en".into(),
                    captions: true,
                    caption_format: CaptionFormat::WebVtt,
                    video_codec: VideoCodec::H264,
                    audio_codec: AudioCodec::Aac,
                    audio_sample_rate_hz: default_audio_sample_rate_hz(),
                    brand_profile: None,
                    cut_label: None,
                },
                DeliverableProfile {
                    id: Uuid::now_v7(),
                    name: "Vertical 9:16".into(),
                    width: 1080,
                    height: 1920,
                    language: "en".into(),
                    captions: true,
                    caption_format: CaptionFormat::WebVtt,
                    video_codec: VideoCodec::H264,
                    audio_codec: AudioCodec::Aac,
                    audio_sample_rate_hz: default_audio_sample_rate_hz(),
                    brand_profile: None,
                    cut_label: None,
                },
                DeliverableProfile {
                    id: Uuid::now_v7(),
                    name: "Square 1:1".into(),
                    width: 1080,
                    height: 1080,
                    language: "en".into(),
                    captions: true,
                    caption_format: CaptionFormat::WebVtt,
                    video_codec: VideoCodec::H264,
                    audio_codec: AudioCodec::Aac,
                    audio_sample_rate_hz: default_audio_sample_rate_hz(),
                    brand_profile: None,
                    cut_label: None,
                },
            ],
            brief: Brief::default(),
            narrative: Narrative::default(),
            audio: AudioState::default(),
            visual_language: VisualLanguage::default(),
            proposal_sets: vec![],
            model_invocations: vec![],
            extensions: vec![],
            handoffs: vec![],
            updated_at: now,
        };
        project.validate()?;
        Ok(project)
    }

    pub fn resource_key(&self) -> String {
        format!("project:{}", self.id)
    }

    pub fn stamp(&self) -> RevisionStamp {
        RevisionStamp {
            resource: self.resource_key(),
            generation: self.generation,
            revision: self.revision,
        }
    }

    pub fn validate(&self) -> Result<()> {
        if self.schema_version != PROJECT_SCHEMA_VERSION {
            return Err(DomainError::Invalid("unsupported project schema".into()));
        }
        if self.title.trim().is_empty() || self.title.len() > 200 {
            return Err(DomainError::Invalid(
                "project title is out of bounds".into(),
            ));
        }
        let branch_ids: HashSet<_> = self.branches.iter().map(|b| b.id).collect();
        if branch_ids.len() != self.branches.len() || !branch_ids.contains(&self.active_branch) {
            return Err(DomainError::Invalid("invalid branch set".into()));
        }
        self.validate_history()?;
        let mut scene_ids = HashSet::new();
        for scene in &self.scenes {
            scene.validate()?;
            if !scene_ids.insert(scene.id) {
                return Err(DomainError::Invalid("duplicate scene id".into()));
            }
        }
        let mut marker_ids = HashSet::new();
        for marker in &self.markers {
            if !marker_ids.insert(marker.id) || !non_negative(marker.at) || marker.label.len() > 160
            {
                return Err(DomainError::Invalid("invalid marker".into()));
            }
        }
        let mut asset_ids = HashSet::new();
        for asset in &self.assets {
            asset.validate()?;
            if !asset_ids.insert(asset.id) {
                return Err(DomainError::Invalid("duplicate asset id".into()));
            }
        }
        let mut lock_ids = BTreeSet::new();
        for lock in &self.locks {
            if !lock_ids.insert(lock.id) || lock.resource.is_empty() || lock.note.len() > 500 {
                return Err(DomainError::Invalid("invalid lock".into()));
            }
        }
        validate_deliverables(&self.deliverables)?;
        self.brief.validate()?;
        self.narrative.validate()?;
        let claim_ids: HashSet<_> = self.brief.claims.iter().map(|claim| claim.id).collect();
        if self
            .narrative
            .beats
            .iter()
            .flat_map(|beat| beat.claim_ids.iter())
            .any(|claim_id| !claim_ids.contains(claim_id))
        {
            return Err(DomainError::Invalid(
                "narrative beat references an unknown claim".into(),
            ));
        }
        self.audio.validate()?;
        for track in &self.audio.voice_tracks {
            let asset = self
                .assets
                .iter()
                .find(|asset| asset.id == track.asset_id)
                .ok_or_else(|| {
                    DomainError::Invalid("voice track references an unknown asset".into())
                })?;
            if !asset.media_type.starts_with("audio/")
                || asset.content_sha256.as_deref() != Some(track.source_sha256.as_str())
            {
                return Err(DomainError::Invalid(
                    "voice track does not match its exact audio asset".into(),
                ));
            }
        }
        self.visual_language.validate()?;
        if self.proposal_sets.len() > 512 || self.model_invocations.len() > 10_000 {
            return Err(DomainError::Invalid(
                "creative project collection is too large".into(),
            ));
        }
        let mut proposal_set_ids = BTreeSet::new();
        for set in &self.proposal_sets {
            if !proposal_set_ids.insert(set.id) {
                return Err(DomainError::Invalid("duplicate proposal set id".into()));
            }
            self.validate_proposal_set_targets(set)?;
        }
        let mut model_receipt_ids = BTreeSet::new();
        for receipt in &self.model_invocations {
            if !model_receipt_ids.insert(receipt.id) || receipt.base_revision > self.revision {
                return Err(DomainError::Invalid(
                    "invalid model invocation history".into(),
                ));
            }
            receipt.validate()?;
        }
        if self.extensions.len() > 128 {
            return Err(DomainError::Invalid("too many project extensions".into()));
        }
        let mut extension_ids = BTreeSet::new();
        let mut extension_kinds = BTreeSet::new();
        for extension in &self.extensions {
            extension.validate()?;
            if !extension_ids.insert(extension.id) {
                return Err(DomainError::Invalid("duplicate extension id".into()));
            }
            let kind = serde_json::to_string(&extension.kind)
                .map_err(|_| DomainError::Invalid("extension kind is invalid".into()))?;
            if extension.enabled && !extension_kinds.insert(kind) {
                return Err(DomainError::Invalid(
                    "only one enabled extension is allowed per extension kind".into(),
                ));
            }
        }
        if self.handoffs.len() > 1_024 {
            return Err(DomainError::Invalid("too many handoff bindings".into()));
        }
        let mut handoff_ids = BTreeSet::new();
        for binding in &self.handoffs {
            binding.validate()?;
            if !handoff_ids.insert(binding.id) {
                return Err(DomainError::Invalid("duplicate handoff binding id".into()));
            }
            if !self.resource_ref_exists(&binding.local_resource) {
                return Err(DomainError::Invalid(
                    "handoff references an unknown local resource".into(),
                ));
            }
        }
        Ok(())
    }

    fn resource_ref_exists(&self, resource: &str) -> bool {
        if resource == self.resource_key() {
            return true;
        }
        let parsed = |prefix: &str| {
            resource
                .strip_prefix(prefix)
                .and_then(|value| Uuid::parse_str(value).ok())
        };
        if let Some(id) = parsed("scene:") {
            return self.scenes.iter().any(|scene| scene.id == id);
        }
        if let Some(id) = parsed("asset:") {
            return self.assets.iter().any(|asset| asset.id == id);
        }
        if let Some(id) = parsed("marker:") {
            return self.markers.iter().any(|marker| marker.id == id);
        }
        if let Some(id) = parsed("claim:") {
            return self.brief.claims.iter().any(|claim| claim.id == id);
        }
        if let Some(id) = parsed("beat:").or_else(|| parsed("narrative-beat:")) {
            return self.narrative.beats.iter().any(|beat| beat.id == id)
                || self
                    .scenes
                    .iter()
                    .any(|scene| scene.beats.iter().any(|beat| beat.id == id));
        }
        if let Some(id) = parsed("node:").or_else(|| parsed("canvas-node:")) {
            return self
                .scenes
                .iter()
                .any(|scene| scene.nodes.iter().any(|node| node.id == id));
        }
        if let Some(id) = parsed("voice-track:") {
            return self.audio.voice_tracks.iter().any(|track| track.id == id);
        }
        if let Some(id) = parsed("transcript:").or_else(|| parsed("transcript-segment:")) {
            return self.audio.transcript.iter().any(|segment| segment.id == id);
        }
        if let Some(id) = parsed("audio-cue:") {
            return self.audio.cues.iter().any(|cue| cue.id == id);
        }
        if let Some(id) = parsed("deliverable:") {
            return self.deliverables.iter().any(|profile| profile.id == id);
        }
        if let Some(id) = parsed("branch:") {
            return self.branches.iter().any(|branch| branch.id == id);
        }
        if let Some(id) = parsed("review:") {
            return self.reviews.iter().any(|review| review.id == id);
        }
        if let Some(id) = parsed("proposal-set:") {
            return self.proposal_sets.iter().any(|set| set.id == id);
        }
        if let Some(id) = parsed("lock:") {
            return self.locks.iter().any(|lock| lock.id == id);
        }
        false
    }

    fn proposal_scope_allows_scene(&self, scope: &ProposalScope, scene_id: Uuid) -> bool {
        match scope {
            ProposalScope::Project => true,
            ProposalScope::Scene { scene_id: scoped } => *scoped == scene_id,
            ProposalScope::Selection { resource_refs } => {
                let reference = format!("scene:{scene_id}");
                resource_refs.iter().any(|resource| resource == &reference)
            }
        }
    }

    fn proposal_scope_allows_beat(
        &self,
        scope: &ProposalScope,
        beat_id: Uuid,
        owner_scene: Option<Uuid>,
    ) -> bool {
        match scope {
            ProposalScope::Project => true,
            ProposalScope::Scene { scene_id } => owner_scene == Some(*scene_id),
            ProposalScope::Selection { resource_refs } => {
                let short = format!("beat:{beat_id}");
                let narrative = format!("narrative-beat:{beat_id}");
                resource_refs
                    .iter()
                    .any(|resource| resource == &short || resource == &narrative)
            }
        }
    }

    fn validate_proposal_set_targets(&self, proposal_set: &ProposalSet) -> Result<()> {
        proposal_set.validate()?;
        match &proposal_set.scope {
            ProposalScope::Project => {}
            ProposalScope::Scene { scene_id } => {
                if !self.scenes.iter().any(|scene| scene.id == *scene_id) {
                    return Err(DomainError::Invalid(
                        "proposal scope references an unknown scene".into(),
                    ));
                }
            }
            ProposalScope::Selection { resource_refs } => {
                if resource_refs
                    .iter()
                    .any(|resource| !self.resource_ref_exists(resource))
                {
                    return Err(DomainError::Invalid(
                        "proposal scope references an unknown resource".into(),
                    ));
                }
            }
        }

        let project_scene_ids: HashSet<_> = self.scenes.iter().map(|scene| scene.id).collect();
        for proposal in &proposal_set.proposals {
            for edit in &proposal.edits {
                match edit {
                    CreativeEdit::SceneObjective { scene_id, .. } => {
                        if !project_scene_ids.contains(scene_id)
                            || !self.proposal_scope_allows_scene(&proposal_set.scope, *scene_id)
                        {
                            return Err(DomainError::Invalid(
                                "proposal scene edit escapes its validated scope".into(),
                            ));
                        }
                    }
                    CreativeEdit::RendererChoice { scene_id, renderer } => {
                        if !project_scene_ids.contains(scene_id)
                            || !self.proposal_scope_allows_scene(&proposal_set.scope, *scene_id)
                        {
                            return Err(DomainError::Invalid(
                                "proposal renderer edit escapes its validated scope".into(),
                            ));
                        }
                        if !matches!(
                            renderer.as_str(),
                            "motion-canvas"
                                | "mlt"
                                | "blender"
                                | "manim-community"
                                | "remotion"
                                | "manim-gl"
                        ) {
                            return Err(DomainError::Invalid(
                                "proposal renderer choice is unsupported".into(),
                            ));
                        }
                    }
                    CreativeEdit::SceneOrder { scene_ids } => {
                        let candidate: HashSet<_> = scene_ids.iter().copied().collect();
                        if !matches!(&proposal_set.scope, ProposalScope::Project)
                            || candidate.len() != scene_ids.len()
                            || candidate != project_scene_ids
                        {
                            return Err(DomainError::Invalid(
                                "proposal scene order must be a complete project permutation"
                                    .into(),
                            ));
                        }
                    }
                    CreativeEdit::BeatRewrite { beat_id, .. } => {
                        let owner_scene = self
                            .scenes
                            .iter()
                            .find(|scene| scene.beats.iter().any(|beat| beat.id == *beat_id))
                            .map(|scene| scene.id);
                        let exists = owner_scene.is_some()
                            || self.narrative.beats.iter().any(|beat| beat.id == *beat_id);
                        if !exists
                            || !self.proposal_scope_allows_beat(
                                &proposal_set.scope,
                                *beat_id,
                                owner_scene,
                            )
                        {
                            return Err(DomainError::Invalid(
                                "proposal beat edit escapes its validated scope".into(),
                            ));
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn validate_model_invocation_targets(&self, receipt: &ModelInvocationReceipt) -> Result<()> {
        receipt.validate()?;
        if receipt.base_revision != self.revision {
            return Err(DomainError::Invalid(
                "model invocation base must match the current project revision".into(),
            ));
        }
        if receipt
            .resource_refs
            .iter()
            .any(|resource| !self.resource_ref_exists(resource))
        {
            return Err(DomainError::Invalid(
                "model invocation references an unknown resource".into(),
            ));
        }
        Ok(())
    }

    fn ensure_unlocked(&self, resource: &str, kinds: &[LockKind]) -> Result<()> {
        if let Some(lock) = self
            .locks
            .iter()
            .find(|lock| lock.resource == resource && kinds.iter().any(|kind| kind == &lock.kind))
        {
            return Err(DomainError::Locked(format!(
                "{} ({:?})",
                resource, lock.kind
            )));
        }
        Ok(())
    }

    pub fn apply_change(&mut self, change: &Change) -> Result<()> {
        match change {
            Change::RenameProject { title } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                if title.trim().is_empty() || title.len() > 200 {
                    return Err(DomainError::Invalid(
                        "project title is out of bounds".into(),
                    ));
                }
                self.title = title.clone();
            }
            Change::SetBrief {
                objective,
                audience,
                constraints,
                exclusions,
            } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                self.brief.objective = objective.clone();
                self.brief.audience = audience.clone();
                self.brief.constraints = constraints.clone();
                self.brief.exclusions = exclusions.clone();
                self.brief.validate()?;
            }
            Change::AddClaim {
                text,
                source,
                context,
                source_revision,
            } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                self.brief.claims.push(Claim {
                    id: Uuid::now_v7(),
                    text: text.clone(),
                    source: source.clone(),
                    context: context.clone(),
                    source_revision: source_revision.clone(),
                });
                self.brief.validate()?;
            }
            Change::SetNarrativePremise { premise } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                self.narrative.premise = premise.clone();
                self.narrative.validate()?;
            }
            Change::AddNarrativeBeat {
                label,
                objective,
                audience_takeaway,
                claim_ids,
                preferred_duration,
            } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                self.narrative.beats.push(NarrativeBeat {
                    id: Uuid::now_v7(),
                    label: label.clone(),
                    objective: objective.clone(),
                    audience_takeaway: audience_takeaway.clone(),
                    claim_ids: claim_ids.clone(),
                    preferred_duration: *preferred_duration,
                });
            }
            Change::AddScene {
                name,
                objective,
                duration_seconds,
            } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                if *duration_seconds <= 0 || *duration_seconds > 86_400 {
                    return Err(DomainError::Invalid(
                        "scene duration is out of bounds".into(),
                    ));
                }
                let start = self
                    .scenes
                    .last()
                    .map(|s| {
                        s.start
                            .checked_add(s.duration)
                            .map_err(|error| {
                                DomainError::Invalid(format!("scene time overflow: {error}"))
                            })
                            .unwrap_or(RationalTime::ZERO)
                    })
                    .unwrap_or(RationalTime::ZERO);
                self.scenes.push(Scene {
                    id: Uuid::now_v7(),
                    name: name.clone(),
                    objective: objective.clone(),
                    start,
                    duration: whole_seconds(*duration_seconds),
                    renderer: RendererKind::MotionCanvas,
                    status: SceneStatus::Draft,
                    beats: vec![],
                    nodes: vec![],
                    camera: CameraState::default(),
                });
            }
            Change::MoveScene { scene_id, to_index } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content, LockKind::Timing])?;
                let from = self
                    .scenes
                    .iter()
                    .position(|s| s.id == *scene_id)
                    .ok_or_else(|| DomainError::NotFound(resource.clone()))?;
                if *to_index >= self.scenes.len() {
                    return Err(DomainError::Invalid(
                        "scene destination index is out of bounds".into(),
                    ));
                }
                let scene = self.scenes.remove(from);
                self.scenes.insert(*to_index, scene);
                self.reflow_scene_starts()?;
            }
            Change::UpdateSceneObjective {
                scene_id,
                objective,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content])?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|s| s.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                if objective.len() > 4000 {
                    return Err(DomainError::Invalid("scene objective is too long".into()));
                }
                scene.objective = objective.clone();
            }
            Change::SetSceneRenderer { scene_id, renderer } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Renderer])?;
                if !renderer_extension_enabled(renderer, &self.extensions) {
                    return Err(DomainError::Invalid(
                        "renderer requires an explicitly enabled project extension".into(),
                    ));
                }
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|s| s.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                scene.renderer = renderer.clone();
            }
            Change::SetSceneStatus { scene_id, status } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content])?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                scene.status = status.clone();
            }
            Change::SetSceneDuration { scene_id, duration } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Timing])?;
                if duration.num <= 0 || *duration > whole_seconds(86_400) {
                    return Err(DomainError::Invalid(
                        "scene duration is out of bounds".into(),
                    ));
                }
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                if scene
                    .nodes
                    .iter()
                    .flat_map(|node| &node.keyframes)
                    .any(|keyframe| keyframe.at >= *duration)
                {
                    return Err(DomainError::Invalid(
                        "scene duration would strand an authored keyframe".into(),
                    ));
                }
                scene.duration = *duration;
                self.reflow_scene_starts()?;
            }
            Change::AddCanvasNode { scene_id, node } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content, LockKind::Position])?;
                node.validate()?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                if node
                    .keyframes
                    .iter()
                    .any(|keyframe| keyframe.at >= scene.duration)
                {
                    return Err(DomainError::Invalid(
                        "canvas keyframe must stay inside the half-open scene interval".into(),
                    ));
                }
                if scene.nodes.iter().any(|existing| existing.id == node.id) {
                    return Err(DomainError::Invalid(
                        "canvas node identity already exists in scene".into(),
                    ));
                }
                scene.nodes.push(node.clone());
            }
            Change::RemoveCanvasNode { scene_id, node_id } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content, LockKind::Position])?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                if !scene.nodes.iter().any(|node| node.id == *node_id) {
                    return Err(DomainError::NotFound(format!("node:{node_id}")));
                }
                if scene
                    .nodes
                    .iter()
                    .any(|node| node.parent_id == Some(*node_id))
                {
                    return Err(DomainError::Invalid(
                        "canvas node still has children; reparent them before removal".into(),
                    ));
                }
                if scene.nodes.iter().any(|node| {
                    node.relations
                        .iter()
                        .any(|relation| relation.target_id == *node_id)
                }) {
                    return Err(DomainError::Invalid(
                        "canvas node still has incoming relations; remove them before removal"
                            .into(),
                    ));
                }
                scene.nodes.retain(|node| node.id != *node_id);
            }
            Change::TransformCanvasNode {
                scene_id,
                node_id,
                transform,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Position])?;
                transform.validate()?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                let mut affected = Vec::new();
                if node.x != transform.x || node.y != transform.y {
                    affected.push(NodeProperty::Position);
                }
                if node.width != transform.width || node.height != transform.height {
                    affected.push(NodeProperty::Size);
                }
                if node.rotation_deg != transform.rotation_deg {
                    affected.push(NodeProperty::Rotation);
                }
                if node.opacity != transform.opacity {
                    affected.push(NodeProperty::Opacity);
                }
                if property_locked(&node.property_locks, &affected) {
                    return Err(DomainError::Locked(format!(
                        "node:{node_id} has a locked transform property"
                    )));
                }
                node.x = transform.x;
                node.y = transform.y;
                node.width = transform.width;
                node.height = transform.height;
                node.rotation_deg = transform.rotation_deg;
                node.opacity = transform.opacity;
            }
            Change::SetCanvasKeyframe {
                scene_id,
                node_id,
                keyframe,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Position])?;
                keyframe.validate()?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                if keyframe.at >= scene.duration {
                    return Err(DomainError::Invalid(
                        "canvas keyframe must stay inside the half-open scene interval".into(),
                    ));
                }
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                let locked = keyframe.property.node_property();
                if node.property_locks.contains(&locked) {
                    return Err(DomainError::Locked(format!(
                        "node:{node_id} motion property"
                    )));
                }
                if let Some(existing) = node.keyframes.iter_mut().find(|existing| {
                    existing.at == keyframe.at && existing.property == keyframe.property
                }) {
                    *existing = keyframe.clone();
                } else {
                    if node.keyframes.len() >= 128 {
                        return Err(DomainError::Invalid(
                            "canvas keyframe budget exceeded".into(),
                        ));
                    }
                    node.keyframes.push(keyframe.clone());
                }
                node.keyframes.sort_by(|left, right| {
                    left.at
                        .cmp(&right.at)
                        .then(left.property.cmp(&right.property))
                });
            }
            Change::RemoveCanvasKeyframe {
                scene_id,
                node_id,
                at,
                property,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Position])?;
                if at.validate().is_err() || *at < RationalTime::ZERO {
                    return Err(DomainError::Invalid(
                        "canvas keyframe time is invalid".into(),
                    ));
                }
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                if node.property_locks.contains(&property.node_property()) {
                    return Err(DomainError::Locked(format!(
                        "node:{node_id} motion property"
                    )));
                }
                let index = node
                    .keyframes
                    .iter()
                    .position(|keyframe| keyframe.at == *at && keyframe.property == *property)
                    .ok_or_else(|| {
                        DomainError::NotFound(format!(
                            "keyframe:{node_id}:{:?}:{}/{}",
                            property, at.num, at.den
                        ))
                    })?;
                node.keyframes.remove(index);
            }
            Change::UpdateCanvasText {
                scene_id,
                node_id,
                text,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content])?;
                if text.as_ref().is_some_and(|value| value.len() > 100_000) {
                    return Err(DomainError::Invalid("canvas text is too long".into()));
                }
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                if node.property_locks.contains(&NodeProperty::Text) {
                    return Err(DomainError::Locked(format!("node:{node_id} text")));
                }
                node.text = text.clone();
            }
            Change::UpdateCanvasStyle {
                scene_id,
                node_id,
                style,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Style])?;
                style.validate()?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                if node.property_locks.contains(&NodeProperty::Style) {
                    return Err(DomainError::Locked(format!("node:{node_id} style")));
                }
                node.style = style.clone();
            }
            Change::ReparentCanvasNode {
                scene_id,
                node_id,
                parent_id,
                z_index,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content, LockKind::Position])?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                if parent_id.is_some_and(|parent| {
                    parent == *node_id || !scene.nodes.iter().any(|node| node.id == parent)
                }) {
                    return Err(DomainError::Invalid(
                        "canvas parent must be another node in the scene".into(),
                    ));
                }
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                if property_locked(
                    &node.property_locks,
                    &[NodeProperty::Parent, NodeProperty::Order],
                ) {
                    return Err(DomainError::Locked(format!("node:{node_id} hierarchy")));
                }
                node.parent_id = *parent_id;
                node.z_index = *z_index;
            }
            Change::SetCanvasRelations {
                scene_id,
                node_id,
                relations,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Position])?;
                if relations.len() > 128 {
                    return Err(DomainError::Invalid("too many canvas relations".into()));
                }
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                let ids: HashSet<_> = scene.nodes.iter().map(|node| node.id).collect();
                if relations.iter().any(|relation| {
                    relation.target_id == *node_id || !ids.contains(&relation.target_id)
                }) {
                    return Err(DomainError::Invalid(
                        "canvas relation target is invalid".into(),
                    ));
                }
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                if node.property_locks.contains(&NodeProperty::Position) {
                    return Err(DomainError::Locked(format!("node:{node_id} relations")));
                }
                node.relations = relations.clone();
            }
            Change::SetNodePropertyLock {
                scene_id,
                node_id,
                property,
                locked,
            } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Content])?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                let node = scene
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
                if *locked {
                    node.property_locks.insert(*property);
                } else {
                    node.property_locks.remove(property);
                }
            }
            Change::SetCamera { scene_id, camera } => {
                let resource = format!("scene:{scene_id}");
                self.ensure_unlocked(&resource, &[LockKind::Position])?;
                camera.validate()?;
                let scene = self
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == *scene_id)
                    .ok_or(DomainError::NotFound(resource))?;
                scene.camera = camera.clone();
            }
            Change::AddMarker { at, label } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                if !non_negative(*at) || label.trim().is_empty() || label.len() > 160 {
                    return Err(DomainError::Invalid("invalid marker".into()));
                }
                self.markers.push(Marker {
                    id: Uuid::now_v7(),
                    at: *at,
                    label: label.clone(),
                });
            }
            Change::AddAsset { asset } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                asset.validate()?;
                if self.assets.iter().any(|candidate| candidate.id == asset.id) {
                    return Err(DomainError::Invalid("duplicate asset id".into()));
                }
                self.assets.push(asset.clone());
            }
            Change::RemoveAsset { asset_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                if self
                    .audio
                    .voice_tracks
                    .iter()
                    .any(|track| track.asset_id == *asset_id)
                    || self.brief.claims.iter().any(|claim| {
                        matches!(
                            claim.source.as_ref(),
                            Some(SourceReference::Asset { asset_id: referenced }) if referenced == asset_id
                        )
                    })
                {
                    return Err(DomainError::Invalid(
                        "asset is still referenced by project state".into(),
                    ));
                }
                let before = self.assets.len();
                self.assets.retain(|asset| asset.id != *asset_id);
                if self.assets.len() == before {
                    return Err(DomainError::NotFound(format!("asset:{asset_id}")));
                }
            }
            Change::UpsertDeliverable { profile } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                profile.validate()?;
                if let Some(existing) = self
                    .deliverables
                    .iter_mut()
                    .find(|candidate| candidate.id == profile.id)
                {
                    *existing = profile.clone();
                } else {
                    if self.deliverables.len() >= MAX_DELIVERABLE_PROFILES {
                        return Err(DomainError::Invalid("too many deliverable profiles".into()));
                    }
                    self.deliverables.push(profile.clone());
                }
                validate_deliverables(&self.deliverables)?;
            }
            Change::RemoveDeliverable { profile_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                if self.deliverables.len() <= 1 {
                    return Err(DomainError::Invalid(
                        "a project must keep at least one deliverable profile".into(),
                    ));
                }
                if self
                    .reviews
                    .iter()
                    .any(|review| review.anchor.profile_id == Some(*profile_id))
                {
                    return Err(DomainError::Invalid(
                        "deliverable profile is referenced by review history".into(),
                    ));
                }
                let before = self.deliverables.len();
                self.deliverables
                    .retain(|profile| profile.id != *profile_id);
                if before == self.deliverables.len() {
                    return Err(DomainError::NotFound(format!("deliverable:{profile_id}")));
                }
            }
            Change::ImportMeasuredVoice { asset, track } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                asset.validate()?;
                if !asset.media_type.starts_with("audio/")
                    || asset.content_sha256.as_deref() != Some(track.source_sha256.as_str())
                    || track.asset_id != asset.id
                {
                    return Err(DomainError::Invalid(
                        "measured voice must bind to the exact imported audio asset".into(),
                    ));
                }
                if self.assets.iter().any(|candidate| candidate.id == asset.id)
                    || self
                        .audio
                        .voice_tracks
                        .iter()
                        .any(|candidate| candidate.id == track.id)
                {
                    return Err(DomainError::Invalid(
                        "measured voice import contains a duplicate id".into(),
                    ));
                }
                self.assets.push(asset.clone());
                self.audio.voice_tracks.push(track.clone());
                self.audio.active_voice_track_id = Some(track.id);
                self.audio.validate()?;
            }
            Change::AddVoiceTrack { track } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                self.audio.voice_tracks.push(track.clone());
                self.audio.validate()?;
            }
            Change::SetActiveVoiceTrack { track_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                if !self
                    .audio
                    .voice_tracks
                    .iter()
                    .any(|track| track.id == *track_id)
                {
                    return Err(DomainError::NotFound(format!("voice-track:{track_id}")));
                }
                self.audio.active_voice_track_id = Some(*track_id);
            }
            Change::AddTranscriptSegment { segment } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                self.audio.transcript.push(segment.clone());
                self.audio.validate()?;
            }
            Change::UpsertTranscriptSegment { segment } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                if let Some(existing) = self
                    .audio
                    .transcript
                    .iter_mut()
                    .find(|candidate| candidate.id == segment.id)
                {
                    *existing = segment.clone();
                } else {
                    self.audio.transcript.push(segment.clone());
                }
                self.audio.validate()?;
            }
            Change::RemoveTranscriptSegment { segment_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                if self
                    .audio
                    .cues
                    .iter()
                    .any(|cue| cue.source_segment_id == Some(*segment_id))
                {
                    return Err(DomainError::Invalid(
                        "transcript segment is referenced by an audio cue".into(),
                    ));
                }
                let before = self.audio.transcript.len();
                self.audio
                    .transcript
                    .retain(|segment| segment.id != *segment_id);
                if before == self.audio.transcript.len() {
                    return Err(DomainError::NotFound(format!(
                        "transcript-segment:{segment_id}"
                    )));
                }
            }
            Change::AddAudioCue { cue } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                self.audio.cues.push(cue.clone());
                self.audio.validate()?;
            }
            Change::UpsertAudioCue { cue } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                if let Some(existing) = self
                    .audio
                    .cues
                    .iter_mut()
                    .find(|candidate| candidate.id == cue.id)
                {
                    *existing = cue.clone();
                } else {
                    self.audio.cues.push(cue.clone());
                }
                self.audio.validate()?;
            }
            Change::RemoveAudioCue { cue_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                let before = self.audio.cues.len();
                self.audio.cues.retain(|cue| cue.id != *cue_id);
                if before == self.audio.cues.len() {
                    return Err(DomainError::NotFound(format!("audio-cue:{cue_id}")));
                }
            }
            Change::SetMixIntent { mix } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                self.audio.mix = mix.clone();
                self.audio.validate()?;
            }
            Change::SetVisualLanguage { visual_language } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Style])?;
                visual_language.validate()?;
                self.visual_language = visual_language.clone();
            }
            Change::AddProposalSet { proposal_set } => {
                if proposal_set.base_revision != self.revision {
                    return Err(DomainError::Invalid(
                        "proposal set base does not match the current project revision".into(),
                    ));
                }
                self.validate_proposal_set_targets(proposal_set)?;
                self.proposal_sets.push(proposal_set.clone());
            }
            Change::SelectProposal {
                proposal_set_id,
                proposal_id,
            } => {
                let set = self
                    .proposal_sets
                    .iter_mut()
                    .find(|set| set.id == *proposal_set_id)
                    .ok_or_else(|| {
                        DomainError::NotFound(format!("proposal-set:{proposal_set_id}"))
                    })?;
                if set.base_revision.saturating_add(1) != self.revision {
                    return Err(DomainError::Invalid(
                        "proposal set is stale for the current project revision".into(),
                    ));
                }
                if !set
                    .proposals
                    .iter()
                    .any(|proposal| proposal.id == *proposal_id)
                {
                    return Err(DomainError::NotFound(format!("proposal:{proposal_id}")));
                }
                set.selected = Some(*proposal_id);
            }
            Change::UpsertExtension { extension } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                extension.validate()?;
                if !extension.enabled
                    && let Some(renderer) = extension.kind.renderer()
                    && self.scenes.iter().any(|scene| scene.renderer == renderer)
                {
                    return Err(DomainError::Invalid(
                        "extension cannot be disabled while its renderer is in use".into(),
                    ));
                }
                if extension.enabled
                    && self.extensions.iter().any(|candidate| {
                        candidate.id != extension.id
                            && candidate.enabled
                            && candidate.kind == extension.kind
                    })
                {
                    return Err(DomainError::Invalid(
                        "another extension of this kind is already enabled".into(),
                    ));
                }
                if let Some(existing) = self
                    .extensions
                    .iter_mut()
                    .find(|candidate| candidate.id == extension.id)
                {
                    *existing = extension.clone();
                } else {
                    self.extensions.push(extension.clone());
                }
            }
            Change::UpsertHandoff { binding } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                binding.validate()?;
                if !self.resource_ref_exists(&binding.local_resource) {
                    return Err(DomainError::Invalid(
                        "handoff references an unknown local resource".into(),
                    ));
                }
                if let Some(existing) = self
                    .handoffs
                    .iter_mut()
                    .find(|candidate| candidate.id == binding.id)
                {
                    *existing = binding.clone();
                } else {
                    if self.handoffs.len() >= 1_024 {
                        return Err(DomainError::Invalid("too many handoff bindings".into()));
                    }
                    self.handoffs.push(binding.clone());
                }
            }
            Change::RemoveHandoff { binding_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                let before = self.handoffs.len();
                self.handoffs
                    .retain(|candidate| candidate.id != *binding_id);
                if before == self.handoffs.len() {
                    return Err(DomainError::NotFound(format!("handoff:{binding_id}")));
                }
            }
            Change::RemoveExtension { extension_id } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
                let extension = self
                    .extensions
                    .iter()
                    .find(|candidate| candidate.id == *extension_id)
                    .ok_or_else(|| DomainError::NotFound(format!("extension:{extension_id}")))?;
                if let Some(renderer) = extension.kind.renderer()
                    && self.scenes.iter().any(|scene| scene.renderer == renderer)
                {
                    return Err(DomainError::Invalid(
                        "extension cannot be removed while its renderer is in use".into(),
                    ));
                }
                self.extensions
                    .retain(|candidate| candidate.id != *extension_id);
            }
            Change::RecordModelInvocation { receipt } => {
                self.validate_model_invocation_targets(receipt)?;
                if self
                    .model_invocations
                    .iter()
                    .any(|existing| existing.id == receipt.id)
                {
                    return Err(DomainError::Invalid(
                        "duplicate model invocation receipt id".into(),
                    ));
                }
                self.model_invocations.push(receipt.clone());
            }
            Change::CreateBranch { .. }
            | Change::CheckoutBranch { .. }
            | Change::MergeBranch { .. }
            | Change::AddReview { .. }
            | Change::ResolveReview { .. }
            | Change::ReopenReview { .. } => {
                self.apply_history_change(change)?;
            }
            Change::SetLock {
                resource,
                kind,
                note,
            } => {
                if resource.is_empty() || note.len() > 500 {
                    return Err(DomainError::Invalid("invalid lock".into()));
                }
                if !self
                    .locks
                    .iter()
                    .any(|lock| lock.resource == *resource && lock.kind == *kind)
                {
                    self.locks.push(ProjectLock {
                        id: Uuid::now_v7(),
                        resource: resource.clone(),
                        kind: kind.clone(),
                        note: note.clone(),
                    });
                }
            }
            Change::RemoveLock { lock_id } => {
                let before = self.locks.len();
                self.locks.retain(|lock| lock.id != *lock_id);
                if before == self.locks.len() {
                    return Err(DomainError::NotFound(format!("lock:{lock_id}")));
                }
            }
        }
        self.updated_at = Utc::now();
        self.validate()
    }

    fn reflow_scene_starts(&mut self) -> Result<()> {
        let mut cursor = RationalTime::ZERO;
        for scene in &mut self.scenes {
            scene.start = cursor;
            cursor = cursor
                .checked_add(scene.duration)
                .map_err(|error| DomainError::Invalid(format!("scene time overflow: {error}")))?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Change {
    RenameProject {
        title: String,
    },
    SetBrief {
        objective: String,
        audience: String,
        constraints: Vec<String>,
        exclusions: Vec<String>,
    },
    AddClaim {
        text: String,
        source: Option<SourceReference>,
        context: String,
        source_revision: Option<String>,
    },
    SetNarrativePremise {
        premise: String,
    },
    AddNarrativeBeat {
        label: String,
        objective: String,
        audience_takeaway: String,
        claim_ids: Vec<Uuid>,
        preferred_duration: Option<RationalTime>,
    },
    AddScene {
        name: String,
        objective: String,
        duration_seconds: i64,
    },
    MoveScene {
        scene_id: Uuid,
        to_index: usize,
    },
    UpdateSceneObjective {
        scene_id: Uuid,
        objective: String,
    },
    SetSceneRenderer {
        scene_id: Uuid,
        renderer: RendererKind,
    },
    SetSceneStatus {
        scene_id: Uuid,
        status: SceneStatus,
    },
    SetSceneDuration {
        scene_id: Uuid,
        duration: RationalTime,
    },
    AddCanvasNode {
        scene_id: Uuid,
        node: CanvasNode,
    },
    RemoveCanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
    },
    TransformCanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
        transform: CanvasTransform,
    },
    SetCanvasKeyframe {
        scene_id: Uuid,
        node_id: Uuid,
        keyframe: CanvasKeyframe,
    },
    RemoveCanvasKeyframe {
        scene_id: Uuid,
        node_id: Uuid,
        at: RationalTime,
        property: MotionProperty,
    },
    UpdateCanvasText {
        scene_id: Uuid,
        node_id: Uuid,
        text: Option<String>,
    },
    UpdateCanvasStyle {
        scene_id: Uuid,
        node_id: Uuid,
        style: NodeStyle,
    },
    ReparentCanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
        parent_id: Option<Uuid>,
        z_index: i32,
    },
    SetCanvasRelations {
        scene_id: Uuid,
        node_id: Uuid,
        relations: Vec<NodeRelation>,
    },
    SetNodePropertyLock {
        scene_id: Uuid,
        node_id: Uuid,
        property: NodeProperty,
        locked: bool,
    },
    SetCamera {
        scene_id: Uuid,
        camera: CameraState,
    },
    AddMarker {
        at: RationalTime,
        label: String,
    },
    AddAsset {
        asset: Asset,
    },
    RemoveAsset {
        asset_id: Uuid,
    },
    UpsertDeliverable {
        profile: DeliverableProfile,
    },
    RemoveDeliverable {
        profile_id: Uuid,
    },
    ImportMeasuredVoice {
        asset: Asset,
        track: VoiceTrack,
    },
    AddVoiceTrack {
        track: VoiceTrack,
    },
    SetActiveVoiceTrack {
        track_id: Uuid,
    },
    AddTranscriptSegment {
        segment: TranscriptSegment,
    },
    UpsertTranscriptSegment {
        segment: TranscriptSegment,
    },
    RemoveTranscriptSegment {
        segment_id: Uuid,
    },
    AddAudioCue {
        cue: AudioCue,
    },
    UpsertAudioCue {
        cue: AudioCue,
    },
    RemoveAudioCue {
        cue_id: Uuid,
    },
    SetMixIntent {
        mix: MixIntent,
    },
    SetVisualLanguage {
        visual_language: VisualLanguage,
    },
    AddProposalSet {
        proposal_set: ProposalSet,
    },
    SelectProposal {
        proposal_set_id: Uuid,
        proposal_id: Uuid,
    },
    UpsertExtension {
        extension: ExtensionProfile,
    },
    RemoveExtension {
        extension_id: Uuid,
    },
    UpsertHandoff {
        binding: HandoffBinding,
    },
    RemoveHandoff {
        binding_id: Uuid,
    },
    RecordModelInvocation {
        receipt: ModelInvocationReceipt,
    },
    CreateBranch {
        name: String,
    },
    CheckoutBranch {
        branch_id: Uuid,
    },
    MergeBranch {
        source_branch_id: Uuid,
    },
    AddReview {
        kind: ReviewKind,
        resource: String,
        body: String,
        start: Option<RationalTime>,
        end: Option<RationalTime>,
        locale: Option<String>,
        profile_id: Option<Uuid>,
    },
    ResolveReview {
        review_id: Uuid,
        resolution: String,
    },
    ReopenReview {
        review_id: Uuid,
    },
    SetLock {
        resource: String,
        kind: LockKind,
        note: String,
    },
    RemoveLock {
        lock_id: Uuid,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RevisionStamp {
    pub resource: String,
    pub generation: Uuid,
    pub revision: u64,
}

impl From<&Project> for RevisionStamp {
    fn from(project: &Project) -> Self {
        Self {
            resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn canvas_node(name: &str) -> CanvasNode {
        CanvasNode {
            id: Uuid::now_v7(),
            name: name.into(),
            kind: "shape".into(),
            parent_id: None,
            x: 0.0,
            y: 0.0,
            width: 100.0,
            height: 100.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle::default(),
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        }
    }

    #[test]
    fn rational_time_is_reduced() {
        assert_eq!(RationalTime::new(48, 24).unwrap(), whole_seconds(2));
        assert_eq!(
            RationalTime::new(-10, -20).unwrap(),
            RationalTime::new(1, 2).unwrap()
        );
    }

    #[test]
    fn scene_reorder_reflows_time() {
        let mut project = Project::new("Demo").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "A".into(),
                objective: "A".into(),
                duration_seconds: 2,
            })
            .unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "B".into(),
                objective: "B".into(),
                duration_seconds: 3,
            })
            .unwrap();
        let b = project.scenes[1].id;
        project
            .apply_change(&Change::MoveScene {
                scene_id: b,
                to_index: 0,
            })
            .unwrap();
        assert_eq!(project.scenes[0].name, "B");
        assert_eq!(project.scenes[0].start, RationalTime::ZERO);
        assert_eq!(project.scenes[1].start, whole_seconds(3));
    }

    #[test]
    fn locks_survive_and_block_matching_change() {
        let mut project = Project::new("Demo").unwrap();
        project
            .apply_change(&Change::SetLock {
                resource: project.resource_key(),
                kind: LockKind::Content,
                note: "Do not rename".into(),
            })
            .unwrap();
        let error = project
            .apply_change(&Change::RenameProject {
                title: "Other".into(),
            })
            .unwrap_err();
        assert!(matches!(error, DomainError::Locked(_)));
    }
    #[test]
    fn canvas_removal_requires_reference_cleanup() {
        let mut project = Project::new("Canvas").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Scene".into(),
                objective: "edit".into(),
                duration_seconds: 5,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        let parent = canvas_node("Parent");
        let child = canvas_node("Child");
        let parent_id = parent.id;
        let child_id = child.id;
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id,
                node: parent,
            })
            .unwrap();
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id,
                node: child,
            })
            .unwrap();
        project
            .apply_change(&Change::ReparentCanvasNode {
                scene_id,
                node_id: child_id,
                parent_id: Some(parent_id),
                z_index: 2,
            })
            .unwrap();

        let error = project
            .apply_change(&Change::RemoveCanvasNode {
                scene_id,
                node_id: parent_id,
            })
            .unwrap_err();
        assert!(matches!(error, DomainError::Invalid(_)));

        project
            .apply_change(&Change::ReparentCanvasNode {
                scene_id,
                node_id: child_id,
                parent_id: None,
                z_index: 2,
            })
            .unwrap();
        project
            .apply_change(&Change::SetCanvasRelations {
                scene_id,
                node_id: child_id,
                relations: vec![NodeRelation {
                    id: Uuid::now_v7(),
                    kind: RelationKind::Follow,
                    target_id: parent_id,
                }],
            })
            .unwrap();
        assert!(
            project
                .apply_change(&Change::RemoveCanvasNode {
                    scene_id,
                    node_id: parent_id,
                })
                .is_err()
        );

        project
            .apply_change(&Change::SetCanvasRelations {
                scene_id,
                node_id: child_id,
                relations: vec![],
            })
            .unwrap();
        project
            .apply_change(&Change::RemoveCanvasNode {
                scene_id,
                node_id: parent_id,
            })
            .unwrap();
        assert!(
            project.scenes[0]
                .nodes
                .iter()
                .all(|node| node.id != parent_id)
        );
    }

    #[test]
    fn typed_keyframes_replace_sort_and_remove_deterministically() {
        let mut project = Project::new("Motion").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Scene".into(),
                objective: "animate".into(),
                duration_seconds: 5,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        let node = canvas_node("Animated");
        let node_id = node.id;
        project
            .apply_change(&Change::AddCanvasNode { scene_id, node })
            .unwrap();

        for keyframe in [
            CanvasKeyframe {
                at: whole_seconds(3),
                property: MotionProperty::X,
                value: 300.0,
                interpolation: MotionInterpolation::Linear,
            },
            CanvasKeyframe {
                at: whole_seconds(1),
                property: MotionProperty::X,
                value: 100.0,
                interpolation: MotionInterpolation::EaseInOut,
            },
            CanvasKeyframe {
                at: whole_seconds(1),
                property: MotionProperty::X,
                value: 125.0,
                interpolation: MotionInterpolation::Hold,
            },
        ] {
            project
                .apply_change(&Change::SetCanvasKeyframe {
                    scene_id,
                    node_id,
                    keyframe,
                })
                .unwrap();
        }

        let keyframes = &project.scenes[0].nodes[0].keyframes;
        assert_eq!(keyframes.len(), 2);
        assert_eq!(keyframes[0].at, whole_seconds(1));
        assert_eq!(keyframes[0].value, 125.0);
        assert_eq!(keyframes[0].interpolation, MotionInterpolation::Hold);
        assert_eq!(keyframes[1].at, whole_seconds(3));

        project
            .apply_change(&Change::RemoveCanvasKeyframe {
                scene_id,
                node_id,
                at: whole_seconds(1),
                property: MotionProperty::X,
            })
            .unwrap();
        assert_eq!(project.scenes[0].nodes[0].keyframes.len(), 1);
    }

    #[test]
    fn keyframes_respect_property_locks_and_scene_bounds() {
        let mut project = Project::new("Motion locks").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Scene".into(),
                objective: "animate".into(),
                duration_seconds: 5,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        let node = canvas_node("Animated");
        let node_id = node.id;
        project
            .apply_change(&Change::AddCanvasNode { scene_id, node })
            .unwrap();
        project
            .apply_change(&Change::SetNodePropertyLock {
                scene_id,
                node_id,
                property: NodeProperty::Opacity,
                locked: true,
            })
            .unwrap();

        let locked = project
            .apply_change(&Change::SetCanvasKeyframe {
                scene_id,
                node_id,
                keyframe: CanvasKeyframe {
                    at: whole_seconds(1),
                    property: MotionProperty::Opacity,
                    value: 0.5,
                    interpolation: MotionInterpolation::Linear,
                },
            })
            .unwrap_err();
        assert!(matches!(locked, DomainError::Locked(_)));

        let outside = project
            .apply_change(&Change::SetCanvasKeyframe {
                scene_id,
                node_id,
                keyframe: CanvasKeyframe {
                    at: whole_seconds(5),
                    property: MotionProperty::X,
                    value: 100.0,
                    interpolation: MotionInterpolation::Linear,
                },
            })
            .unwrap_err();
        assert!(matches!(outside, DomainError::Invalid(_)));
    }

    #[test]
    fn duration_cannot_strand_existing_keyframes() {
        let mut project = Project::new("Motion duration").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Scene".into(),
                objective: "animate".into(),
                duration_seconds: 5,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        let node = canvas_node("Animated");
        let node_id = node.id;
        project
            .apply_change(&Change::AddCanvasNode { scene_id, node })
            .unwrap();
        project
            .apply_change(&Change::SetCanvasKeyframe {
                scene_id,
                node_id,
                keyframe: CanvasKeyframe {
                    at: whole_seconds(4),
                    property: MotionProperty::Y,
                    value: 220.0,
                    interpolation: MotionInterpolation::EaseInOut,
                },
            })
            .unwrap();

        let error = project
            .apply_change(&Change::SetSceneDuration {
                scene_id,
                duration: whole_seconds(4),
            })
            .unwrap_err();
        assert!(matches!(error, DomainError::Invalid(_)));
        assert_eq!(project.scenes[0].duration, whole_seconds(5));
    }

    #[test]
    fn asset_registration_validates_digest_and_protects_references() {
        let mut project = Project::new("Assets").unwrap();
        let asset_id = Uuid::now_v7();
        project
            .apply_change(&Change::AddAsset {
                asset: Asset {
                    id: asset_id,
                    name: "voice.wav".into(),
                    media_type: "audio/wav".into(),
                    content_sha256: Some("ab".repeat(32)),
                    source_revision: Some("import-r1".into()),
                },
            })
            .unwrap();
        project
            .apply_change(&Change::AddClaim {
                text: "Claim".into(),
                source: Some(SourceReference::Asset { asset_id }),
                context: "asset-backed".into(),
                source_revision: None,
            })
            .unwrap();
        let error = project
            .apply_change(&Change::RemoveAsset { asset_id })
            .unwrap_err();
        assert!(matches!(error, DomainError::Invalid(_)));

        let invalid = Change::AddAsset {
            asset: Asset {
                id: Uuid::now_v7(),
                name: "bad.bin".into(),
                media_type: "application/octet-stream".into(),
                content_sha256: Some("NOT-A-DIGEST".into()),
                source_revision: None,
            },
        };
        assert!(matches!(
            project.apply_change(&invalid),
            Err(DomainError::Invalid(_))
        ));
    }

    #[test]
    fn duration_change_ripples_following_scene_starts() {
        let mut project = Project::new("Ripple").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "A".into(),
                objective: "a".into(),
                duration_seconds: 4,
            })
            .unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "B".into(),
                objective: "b".into(),
                duration_seconds: 6,
            })
            .unwrap();
        let first = project.scenes[0].id;
        project
            .apply_change(&Change::SetSceneDuration {
                scene_id: first,
                duration: whole_seconds(7),
            })
            .unwrap();
        assert_eq!(project.scenes[0].duration, whole_seconds(7));
        assert_eq!(project.scenes[1].start, whole_seconds(7));
    }
}
