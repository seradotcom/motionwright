use super::*;
use std::collections::{BTreeSet, HashSet};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct BranchState {
    #[serde(default)]
    pub scenes: Vec<Scene>,
    #[serde(default)]
    pub markers: Vec<Marker>,
    #[serde(default)]
    pub locks: Vec<ProjectLock>,
    #[serde(default)]
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
}

impl BranchState {
    fn validate(&self, assets: &[Asset]) -> Result<()> {
        let mut scene_ids = HashSet::new();
        for scene in &self.scenes {
            scene.validate()?;
            if !scene_ids.insert(scene.id) {
                return Err(DomainError::Invalid(
                    "duplicate scene id in branch state".into(),
                ));
            }
        }

        let mut marker_ids = HashSet::new();
        for marker in &self.markers {
            if !marker_ids.insert(marker.id) || !non_negative(marker.at) || marker.label.len() > 160
            {
                return Err(DomainError::Invalid(
                    "invalid marker in branch state".into(),
                ));
            }
        }

        let mut lock_ids = BTreeSet::new();
        for lock in &self.locks {
            if !lock_ids.insert(lock.id) || lock.resource.is_empty() || lock.note.len() > 500 {
                return Err(DomainError::Invalid("invalid lock in branch state".into()));
            }
        }

        for profile in &self.deliverables {
            if profile.width == 0
                || profile.height == 0
                || profile.width > 16_384
                || profile.height > 16_384
                || profile.name.trim().is_empty()
                || profile.language.trim().is_empty()
            {
                return Err(DomainError::Invalid(
                    "invalid deliverable in branch state".into(),
                ));
            }
        }

        self.brief.validate()?;
        self.narrative.validate()?;
        self.audio.validate()?;
        self.visual_language.validate()?;

        if self.proposal_sets.len() > 512 || self.model_invocations.len() > 10_000 {
            return Err(DomainError::Invalid(
                "branch creative collection is too large".into(),
            ));
        }
        for set in &self.proposal_sets {
            set.validate()?;
        }
        for receipt in &self.model_invocations {
            receipt.validate()?;
        }

        let asset_ids: HashSet<_> = assets.iter().map(|asset| asset.id).collect();
        for claim in &self.brief.claims {
            if let Some(SourceReference::Asset { asset_id }) = &claim.source {
                if !asset_ids.contains(asset_id) {
                    return Err(DomainError::Invalid(
                        "branch claim references an unknown asset".into(),
                    ));
                }
            }
        }
        for track in &self.audio.voice_tracks {
            let asset = assets
                .iter()
                .find(|asset| asset.id == track.asset_id)
                .ok_or_else(|| {
                    DomainError::Invalid("branch voice track references an unknown asset".into())
                })?;
            if !asset.media_type.starts_with("audio/")
                || asset.content_sha256.as_deref() != Some(track.source_sha256.as_str())
            {
                return Err(DomainError::Invalid(
                    "branch voice track does not match its audio asset".into(),
                ));
            }
        }
        Ok(())
    }

    fn merge(
        base: &Self,
        target: &Self,
        source: &Self,
    ) -> std::result::Result<Self, Vec<&'static str>> {
        fn field<T: Clone + PartialEq>(
            name: &'static str,
            base: &T,
            target: &T,
            source: &T,
            conflicts: &mut Vec<&'static str>,
        ) -> T {
            if source == base || source == target {
                target.clone()
            } else if target == base {
                source.clone()
            } else {
                conflicts.push(name);
                target.clone()
            }
        }

        let mut conflicts = Vec::new();
        let merged = Self {
            scenes: field(
                "scenes",
                &base.scenes,
                &target.scenes,
                &source.scenes,
                &mut conflicts,
            ),
            markers: field(
                "markers",
                &base.markers,
                &target.markers,
                &source.markers,
                &mut conflicts,
            ),
            locks: field(
                "locks",
                &base.locks,
                &target.locks,
                &source.locks,
                &mut conflicts,
            ),
            deliverables: field(
                "deliverables",
                &base.deliverables,
                &target.deliverables,
                &source.deliverables,
                &mut conflicts,
            ),
            brief: field(
                "brief",
                &base.brief,
                &target.brief,
                &source.brief,
                &mut conflicts,
            ),
            narrative: field(
                "narrative",
                &base.narrative,
                &target.narrative,
                &source.narrative,
                &mut conflicts,
            ),
            audio: field(
                "audio",
                &base.audio,
                &target.audio,
                &source.audio,
                &mut conflicts,
            ),
            visual_language: field(
                "visual_language",
                &base.visual_language,
                &target.visual_language,
                &source.visual_language,
                &mut conflicts,
            ),
            proposal_sets: field(
                "proposal_sets",
                &base.proposal_sets,
                &target.proposal_sets,
                &source.proposal_sets,
                &mut conflicts,
            ),
            model_invocations: field(
                "model_invocations",
                &base.model_invocations,
                &target.model_invocations,
                &source.model_invocations,
                &mut conflicts,
            ),
        };
        if conflicts.is_empty() {
            Ok(merged)
        } else {
            Err(conflicts)
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BranchWorkspace {
    pub branch_id: Uuid,
    pub base_revision: u64,
    pub base_state: BranchState,
    pub current_state: BranchState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewKind {
    Technical,
    Creative,
    Editorial,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewStatus {
    Open,
    Resolved,
    Dismissed,
    NeedsRecheck,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReviewAnchor {
    pub resource: String,
    pub branch_id: Uuid,
    pub revision: u64,
    pub start: Option<RationalTime>,
    pub end: Option<RationalTime>,
    pub locale: Option<String>,
    pub profile_id: Option<Uuid>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativeReview {
    pub id: Uuid,
    pub kind: ReviewKind,
    pub anchor: ReviewAnchor,
    pub body: String,
    pub status: ReviewStatus,
    pub resolution: Option<String>,
    pub created_at: DateTime<Utc>,
    pub resolved_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MergeRecord {
    pub id: Uuid,
    pub source_branch: Uuid,
    pub target_branch: Uuid,
    pub base_revision: u64,
    pub committed_revision: Option<u64>,
    pub merged_at: DateTime<Utc>,
}

impl CreativeReview {
    fn validate(&self, project: &Project) -> Result<()> {
        if self.body.trim().is_empty()
            || self.body.len() > 8_000
            || self.anchor.resource.trim().is_empty()
            || self.anchor.resource.len() > 512
            || self.anchor.revision > project.revision
            || !project
                .branches
                .iter()
                .any(|branch| branch.id == self.anchor.branch_id)
            || self
                .anchor
                .locale
                .as_ref()
                .is_some_and(|value| value.len() > 64 || value.chars().any(char::is_control))
            || self
                .resolution
                .as_ref()
                .is_some_and(|value| value.len() > 4_000)
        {
            return Err(DomainError::Invalid("invalid creative review".into()));
        }
        if let Some(profile_id) = self.anchor.profile_id {
            if !project
                .deliverables
                .iter()
                .any(|profile| profile.id == profile_id)
            {
                return Err(DomainError::Invalid(
                    "review references an unknown deliverable profile".into(),
                ));
            }
        }
        match (self.anchor.start, self.anchor.end) {
            (Some(start), Some(end))
                if non_negative(start) && non_negative(end) && end >= start => {}
            (None, None) => {}
            (Some(start), None) if non_negative(start) => {}
            _ => return Err(DomainError::Invalid("review time anchor is invalid".into())),
        }
        Ok(())
    }
}

impl Project {
    fn capture_branch_state(&self) -> BranchState {
        BranchState {
            scenes: self.scenes.clone(),
            markers: self.markers.clone(),
            locks: self.locks.clone(),
            deliverables: self.deliverables.clone(),
            brief: self.brief.clone(),
            narrative: self.narrative.clone(),
            audio: self.audio.clone(),
            visual_language: self.visual_language.clone(),
            proposal_sets: self.proposal_sets.clone(),
            model_invocations: self.model_invocations.clone(),
        }
    }

    fn restore_branch_state(&mut self, state: &BranchState) {
        self.scenes = state.scenes.clone();
        self.markers = state.markers.clone();
        self.locks = state.locks.clone();
        self.deliverables = state.deliverables.clone();
        self.brief = state.brief.clone();
        self.narrative = state.narrative.clone();
        self.audio = state.audio.clone();
        self.visual_language = state.visual_language.clone();
        self.proposal_sets = state.proposal_sets.clone();
        self.model_invocations = state.model_invocations.clone();
    }

    fn save_active_workspace(&mut self) -> Result<()> {
        let state = self.capture_branch_state();
        state.validate(&self.assets)?;
        let active = self.active_branch;
        let base_revision = self
            .branches
            .iter()
            .find(|branch| branch.id == active)
            .ok_or_else(|| DomainError::Invalid("active branch is missing".into()))?
            .base_revision;
        if let Some(workspace) = self
            .branch_workspaces
            .iter_mut()
            .find(|workspace| workspace.branch_id == active)
        {
            workspace.current_state = state;
        } else {
            self.branch_workspaces.push(BranchWorkspace {
                branch_id: active,
                base_revision,
                base_state: state.clone(),
                current_state: state,
            });
        }
        Ok(())
    }

    pub(crate) fn validate_history(&self) -> Result<()> {
        if self.branches.len() > 256
            || self.branch_workspaces.len() > 256
            || self.reviews.len() > 10_000
            || self.merges.len() > 10_000
        {
            return Err(DomainError::Invalid(
                "history collection is too large".into(),
            ));
        }

        let mut names = HashSet::new();
        let branch_ids: HashSet<_> = self.branches.iter().map(|branch| branch.id).collect();
        for branch in &self.branches {
            if branch.name.trim().is_empty()
                || branch.name.len() > 120
                || branch.name.chars().any(char::is_control)
                || !names.insert(branch.name.to_ascii_lowercase())
                || branch.base_revision > branch.head_revision
                || branch.head_revision > self.revision
                || branch
                    .parent_branch
                    .is_some_and(|parent| !branch_ids.contains(&parent))
            {
                return Err(DomainError::Invalid("invalid branch history".into()));
            }
        }

        let mut workspace_ids = HashSet::new();
        for workspace in &self.branch_workspaces {
            if !branch_ids.contains(&workspace.branch_id)
                || !workspace_ids.insert(workspace.branch_id)
                || workspace.base_revision > self.revision
            {
                return Err(DomainError::Invalid("invalid branch workspace".into()));
            }
            workspace.base_state.validate(&self.assets)?;
            workspace.current_state.validate(&self.assets)?;
        }

        let mut review_ids = HashSet::new();
        for review in &self.reviews {
            if !review_ids.insert(review.id) {
                return Err(DomainError::Invalid("duplicate review id".into()));
            }
            review.validate(self)?;
        }

        let mut merge_ids = HashSet::new();
        for merge in &self.merges {
            if !merge_ids.insert(merge.id)
                || !branch_ids.contains(&merge.source_branch)
                || !branch_ids.contains(&merge.target_branch)
                || merge.source_branch == merge.target_branch
                || merge.base_revision > self.revision
                || merge
                    .committed_revision
                    .is_some_and(|revision| revision > self.revision)
            {
                return Err(DomainError::Invalid("invalid merge record".into()));
            }
        }
        Ok(())
    }

    pub(crate) fn apply_history_change(&mut self, change: &Change) -> Result<()> {
        match change {
            Change::CreateBranch { name } => {
                if name.trim().is_empty()
                    || name.len() > 120
                    || name.chars().any(char::is_control)
                    || self
                        .branches
                        .iter()
                        .any(|branch| branch.name.eq_ignore_ascii_case(name))
                {
                    return Err(DomainError::Invalid(
                        "branch name is invalid or already used".into(),
                    ));
                }
                if self.branches.len() >= 256 {
                    return Err(DomainError::Invalid("branch limit reached".into()));
                }
                self.save_active_workspace()?;
                let branch_id = Uuid::now_v7();
                let state = self.capture_branch_state();
                self.branches.push(Branch {
                    id: branch_id,
                    name: name.clone(),
                    parent_branch: Some(self.active_branch),
                    base_revision: self.revision,
                    head_revision: self.revision,
                    protected: false,
                    created_at: Utc::now(),
                });
                self.branch_workspaces.push(BranchWorkspace {
                    branch_id,
                    base_revision: self.revision,
                    base_state: state.clone(),
                    current_state: state,
                });
            }
            Change::CheckoutBranch { branch_id } => {
                if *branch_id == self.active_branch {
                    return Ok(());
                }
                self.save_active_workspace()?;
                let state = self
                    .branch_workspaces
                    .iter()
                    .find(|workspace| workspace.branch_id == *branch_id)
                    .map(|workspace| workspace.current_state.clone())
                    .ok_or_else(|| DomainError::NotFound(format!("branch:{branch_id}")))?;
                state.validate(&self.assets)?;
                self.restore_branch_state(&state);
                self.active_branch = *branch_id;
            }
            Change::MergeBranch { source_branch_id } => {
                if *source_branch_id == self.active_branch {
                    return Err(DomainError::Invalid(
                        "cannot merge a branch into itself".into(),
                    ));
                }
                self.save_active_workspace()?;
                let source_branch = self
                    .branches
                    .iter()
                    .find(|branch| branch.id == *source_branch_id)
                    .cloned()
                    .ok_or_else(|| DomainError::NotFound(format!("branch:{source_branch_id}")))?;
                if source_branch.parent_branch != Some(self.active_branch) {
                    return Err(DomainError::Invalid(
                        "first-party merge currently requires the source branch to descend directly from the active target".into(),
                    ));
                }
                let source_workspace = self
                    .branch_workspaces
                    .iter()
                    .find(|workspace| workspace.branch_id == *source_branch_id)
                    .cloned()
                    .ok_or_else(|| DomainError::NotFound(format!("branch:{source_branch_id}")))?;
                let target = self.capture_branch_state();
                let merged = BranchState::merge(
                    &source_workspace.base_state,
                    &target,
                    &source_workspace.current_state,
                )
                .map_err(|conflicts| {
                    DomainError::Invalid(format!(
                        "semantic merge conflict in: {}",
                        conflicts.join(", ")
                    ))
                })?;
                merged.validate(&self.assets)?;
                self.restore_branch_state(&merged);
                self.merges.push(MergeRecord {
                    id: Uuid::now_v7(),
                    source_branch: *source_branch_id,
                    target_branch: self.active_branch,
                    base_revision: source_workspace.base_revision,
                    committed_revision: None,
                    merged_at: Utc::now(),
                });
            }
            Change::AddReview {
                kind,
                resource,
                body,
                start,
                end,
                locale,
                profile_id,
            } => {
                let review = CreativeReview {
                    id: Uuid::now_v7(),
                    kind: kind.clone(),
                    anchor: ReviewAnchor {
                        resource: resource.clone(),
                        branch_id: self.active_branch,
                        revision: self.revision,
                        start: *start,
                        end: *end,
                        locale: locale.clone(),
                        profile_id: *profile_id,
                    },
                    body: body.clone(),
                    status: ReviewStatus::Open,
                    resolution: None,
                    created_at: Utc::now(),
                    resolved_at: None,
                };
                review.validate(self)?;
                self.reviews.push(review);
            }
            Change::ResolveReview {
                review_id,
                resolution,
            } => {
                if resolution.trim().is_empty() || resolution.len() > 4_000 {
                    return Err(DomainError::Invalid(
                        "review resolution is out of bounds".into(),
                    ));
                }
                let review = self
                    .reviews
                    .iter_mut()
                    .find(|review| review.id == *review_id)
                    .ok_or_else(|| DomainError::NotFound(format!("review:{review_id}")))?;
                if matches!(review.status, ReviewStatus::Dismissed) {
                    return Err(DomainError::Invalid(
                        "dismissed review cannot be resolved".into(),
                    ));
                }
                review.status = ReviewStatus::Resolved;
                review.resolution = Some(resolution.clone());
                review.resolved_at = Some(Utc::now());
            }
            Change::ReopenReview { review_id } => {
                let review = self
                    .reviews
                    .iter_mut()
                    .find(|review| review.id == *review_id)
                    .ok_or_else(|| DomainError::NotFound(format!("review:{review_id}")))?;
                review.status = ReviewStatus::NeedsRecheck;
                review.resolution = None;
                review.resolved_at = None;
            }
            _ => {
                return Err(DomainError::Invalid(
                    "change is not a history/review operation".into(),
                ));
            }
        }
        Ok(())
    }

    pub fn commit_revision(&mut self, next_revision: u64, change: &Change) -> Result<()> {
        if next_revision != self.revision.saturating_add(1) {
            return Err(DomainError::Invalid(
                "non-sequential project revision".into(),
            ));
        }
        self.revision = next_revision;
        let active_branch = self.active_branch;
        let branch = self
            .branches
            .iter_mut()
            .find(|branch| branch.id == active_branch)
            .ok_or_else(|| DomainError::Invalid("active branch is missing".into()))?;
        branch.head_revision = next_revision;

        if matches!(change, Change::MergeBranch { .. }) {
            if let Some(record) = self.merges.iter_mut().rev().find(|record| {
                record.target_branch == active_branch && record.committed_revision.is_none()
            }) {
                record.committed_revision = Some(next_revision);
            }
        }

        let preserves_review_state = matches!(
            change,
            Change::CreateBranch { .. }
                | Change::CheckoutBranch { .. }
                | Change::AddReview { .. }
                | Change::ResolveReview { .. }
                | Change::ReopenReview { .. }
                | Change::SetLock { .. }
                | Change::RemoveLock { .. }
        );
        if !preserves_review_state {
            for review in &mut self.reviews {
                if review.anchor.branch_id == active_branch
                    && review.anchor.revision < next_revision
                    && review.status == ReviewStatus::Resolved
                {
                    review.status = ReviewStatus::NeedsRecheck;
                    review.resolution = None;
                    review.resolved_at = None;
                }
            }
        }
        self.validate()
    }
}
