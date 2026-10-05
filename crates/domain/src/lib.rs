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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RationalTime {
    pub num: i64,
    pub den: i64,
}

impl RationalTime {
    pub fn new(num: i64, den: i64) -> Result<Self> {
        if den == 0 {
            return Err(DomainError::Invalid(
                "time denominator must be non-zero".into(),
            ));
        }
        let sign = if den < 0 { -1 } else { 1 };
        let mut n = num.saturating_mul(sign);
        let mut d = den.saturating_mul(sign);
        let g = gcd(n.unsigned_abs(), d as u64).max(1) as i64;
        n /= g;
        d /= g;
        Ok(Self { num: n, den: d })
    }

    pub fn zero() -> Self {
        Self { num: 0, den: 1 }
    }

    pub fn seconds(value: i64) -> Self {
        Self { num: value, den: 1 }
    }

    pub fn is_non_negative(self) -> bool {
        self.num >= 0 && self.den > 0
    }

    pub fn as_f64(self) -> f64 {
        self.num as f64 / self.den as f64
    }
}

fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a
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
}

impl CanvasNode {
    fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid(
                "canvas node name is out of bounds".into(),
            ));
        }
        for value in [
            self.x,
            self.y,
            self.width,
            self.height,
            self.rotation_deg,
            self.opacity,
        ] {
            if !value.is_finite() {
                return Err(DomainError::Invalid(
                    "canvas node contains non-finite geometry".into(),
                ));
            }
        }
        if self.width < 0.0 || self.height < 0.0 || !(0.0..=1.0).contains(&self.opacity) {
            return Err(DomainError::Invalid(
                "canvas node geometry is invalid".into(),
            ));
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
}

impl Scene {
    fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid("scene name is out of bounds".into()));
        }
        if !self.start.is_non_negative() || self.duration.num <= 0 || self.duration.den <= 0 {
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
            if let Some(parent) = node.parent_id {
                if parent == node.id || !ids.contains(&parent) {
                    return Err(DomainError::Invalid(
                        "canvas hierarchy contains an invalid parent".into(),
                    ));
                }
            }
        }
        let mut beat_ids = HashSet::new();
        for beat in &self.beats {
            if !beat_ids.insert(beat.id) || beat.duration.num <= 0 || !beat.start.is_non_negative()
            {
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
            if !marker_ids.insert(marker.id)
                || !marker.at.is_non_negative()
                || marker.label.len() > 160
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
                        RationalTime::new(
                            s.start.num * s.duration.den + s.duration.num * s.start.den,
                            s.start.den * s.duration.den,
                        )
                        .unwrap_or(RationalTime::zero())
                    })
                    .unwrap_or(RationalTime::zero());
                self.scenes.push(Scene {
                    id: Uuid::now_v7(),
                    name: name.clone(),
                    objective: objective.clone(),
                    start,
                    duration: RationalTime::seconds(*duration_seconds),
                    renderer: RendererKind::MotionCanvas,
                    status: SceneStatus::Draft,
                    beats: vec![],
                    nodes: vec![],
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
                    .ok_or_else(|| DomainError::NotFound(resource))?;
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
                    .ok_or_else(|| DomainError::NotFound(resource))?;
                scene.renderer = renderer.clone();
            }
            Change::AddMarker { at, label } => {
                self.ensure_unlocked(&self.resource_key(), &[LockKind::Timing])?;
                if !at.is_non_negative() || label.trim().is_empty() || label.len() > 160 {
                    return Err(DomainError::Invalid("invalid marker".into()));
                }
                self.markers.push(Marker {
                    id: Uuid::now_v7(),
                    at: *at,
                    label: label.clone(),
                });
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
        let mut cursor = RationalTime::zero();
        for scene in &mut self.scenes {
            scene.start = cursor;
            cursor = RationalTime::new(
                cursor.num * scene.duration.den + scene.duration.num * cursor.den,
                cursor.den * scene.duration.den,
            )?;
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
    AddMarker {
        at: RationalTime,
        label: String,
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
        assert_eq!(RationalTime::new(48, 24).unwrap(), RationalTime::seconds(2));
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
        assert_eq!(project.scenes[0].start, RationalTime::zero());
        assert_eq!(project.scenes[1].start, RationalTime::seconds(3));
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
}
