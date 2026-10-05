mod canvas;
mod creative;
pub use canvas::*;
pub use creative::*;

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
}

impl CanvasNode {
    fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid(
                "canvas node name is out of bounds".into(),
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
        {
            return Err(DomainError::Invalid(
                "canvas node content exceeds bounded limits".into(),
            ));
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
pub struct Asset {
    pub id: Uuid,
    pub name: String,
    pub media_type: String,
    pub content_sha256: Option<String>,
    pub source_revision: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Branch {
    pub id: Uuid,
    pub name: String,
    pub base_revision: u64,
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
                base_revision: 0,
                created_at: now,
            }],
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
                },
                DeliverableProfile {
                    id: Uuid::now_v7(),
                    name: "Vertical 9:16".into(),
                    width: 1080,
                    height: 1920,
                    language: "en".into(),
                    captions: true,
                },
                DeliverableProfile {
                    id: Uuid::now_v7(),
                    name: "Square 1:1".into(),
                    width: 1080,
                    height: 1080,
                    language: "en".into(),
                    captions: true,
                },
            ],
            brief: Brief::default(),
            narrative: Narrative::default(),
            audio: AudioState::default(),
            visual_language: VisualLanguage::default(),
            proposal_sets: vec![],
            model_invocations: vec![],
            updated_at: now,
        };
        project.validate()?;
        Ok(project)
    }

    pub fn resource_key(&self) -> String {
        format!("project:{}", self.id)
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
        let mut lock_ids = BTreeSet::new();
        for lock in &self.locks {
            if !lock_ids.insert(lock.id) || lock.resource.is_empty() || lock.note.len() > 500 {
                return Err(DomainError::Invalid("invalid lock".into()));
            }
        }
        for profile in &self.deliverables {
            if profile.width == 0
                || profile.height == 0
                || profile.width > 16384
                || profile.height > 16384
            {
                return Err(DomainError::Invalid(
                    "invalid deliverable dimensions".into(),
                ));
            }
        }
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
        self.visual_language.validate()?;
        if self.proposal_sets.len() > 512 || self.model_invocations.len() > 10_000 {
            return Err(DomainError::Invalid(
                "creative project collection is too large".into(),
            ));
        }
        for set in &self.proposal_sets {
            set.validate()?;
        }
        for receipt in &self.model_invocations {
            receipt.validate()?;
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
                if scene.nodes.iter().any(|existing| existing.id == node.id) {
                    return Err(DomainError::Invalid(
                        "canvas node identity already exists in scene".into(),
                    ));
                }
                scene.nodes.push(node.clone());
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
            Change::AddVoiceTrack { track } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                self.audio.voice_tracks.push(track.clone());
                self.audio.validate()?;
            }
            Change::AddTranscriptSegment { segment } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Content, LockKind::Timing])?;
                self.audio.transcript.push(segment.clone());
                self.audio.validate()?;
            }
            Change::AddAudioCue { cue } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                self.audio.cues.push(cue.clone());
                self.audio.validate()?;
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
                proposal_set.validate()?;
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
            Change::RecordModelInvocation { receipt } => {
                if receipt.base_revision > self.revision {
                    return Err(DomainError::Invalid(
                        "model invocation references a future project revision".into(),
                    ));
                }
                receipt.validate()?;
                self.model_invocations.push(receipt.clone());
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
    TransformCanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
        transform: CanvasTransform,
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
    AddVoiceTrack {
        track: VoiceTrack,
    },
    AddTranscriptSegment {
        segment: TranscriptSegment,
    },
    AddAudioCue {
        cue: AudioCue,
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
    RecordModelInvocation {
        receipt: ModelInvocationReceipt,
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
