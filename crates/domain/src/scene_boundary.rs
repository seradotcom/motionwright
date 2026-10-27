//! Canonical, persistent scene-boundary authorship. These values are neither
//! native observations nor grants. Shape validation preserves incomplete and
//! historical source; execution resolves current references separately.
use crate::*;
use semwright_media_time::Rate;
use serde::de::{self, SeqAccess, Visitor};
use sha2::{Digest, Sha256};
use std::{fmt, io};

pub const SCENE_BOUNDARY_VERSION: u32 = 1;
pub const MAX_SCENE_BOUNDARIES_PER_COLLECTION: usize = 4_096;
pub const MAX_SCENE_BOUNDARY_COLLECTION_BYTES: usize = 8 * 1024 * 1024;
const DEPENDENCIES_PREFIX: &[u8] = b"motionwright-scene-boundary-dependencies/1";
const BOUND_EXIT_PREFIX: &[u8] = b"motionwright-scene-boundary-bound-exit/1";

fn invalid(message: impl Into<String>) -> DomainError {
    DomainError::Invalid(format!("scene boundary: {}", message.into()))
}

fn validate_digest(value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid("digest must be 64 lowercase hexadecimal bytes"));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SceneBoundaryNarrativeRole {
    Hook,
    Problem,
    Mechanism,
    Evidence,
    Comparison,
    Reveal,
    Payoff,
    Cta,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SceneBoundaryArchetype {
    Statement,
    SplitExplanation,
    ArchitectureReveal,
    Comparison,
    Metric,
    Timeline,
    CodeFocus,
    DiagramBuild,
    ObjectSpotlight,
    EvidenceFrame,
    ProductProof,
    Endcard,
}

/// Exact authored options for this scene. Native consumers compare/convert
/// these closed values against the actual FilmBuildOptions they use.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneBoundaryFilmOptions {
    pub frame_rate: Rate,
    pub font_family: String,
    pub mono_font_family: String,
    pub role: SceneBoundaryNarrativeRole,
    pub archetype: SceneBoundaryArchetype,
}

impl SceneBoundaryFilmOptions {
    pub fn validate(&self) -> Result<()> {
        self.frame_rate
            .validate()
            .map_err(|error| invalid(error.to_string()))?;
        for family in [&self.font_family, &self.mono_font_family] {
            if family.trim().is_empty() || family.chars().any(char::is_control) {
                return Err(invalid(
                    "font family is empty or contains control characters",
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneBoundarySource {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub branch_id: Uuid,
    /// The pre-change revision at explicit binding, never a currentness token.
    pub authored_revision: u64,
    pub deliverable_profile_id: Uuid,
    pub film_options: SceneBoundaryFilmOptions,
    pub dependencies_sha256: String,
}

impl SceneBoundarySource {
    fn validate_shape(&self) -> Result<()> {
        self.film_options.validate()?;
        validate_digest(&self.dependencies_sha256)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneBoundaryAsset {
    pub asset_id: Uuid,
    pub content_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneBoundaryEndpoint {
    #[serde(default)]
    pub nodes: Vec<CanvasNode>,
    #[serde(default)]
    pub camera: Option<CameraState>,
    #[serde(default)]
    pub assets: Vec<SceneBoundaryAsset>,
}

impl SceneBoundaryEndpoint {
    fn validate_shape(&self, complete: bool) -> Result<()> {
        let mut nodes = BTreeMap::new();
        for node in &self.nodes {
            node.validate()?;
            if nodes.insert(node.id, node).is_some() {
                return Err(invalid("duplicate endpoint node identity"));
            }
        }
        if complete {
            if self.camera.is_none() {
                return Err(invalid("Explicit endpoint requires a camera"));
            }
            // Intrinsic closure only. No current Project reference is required
            // to reopen or import a complete historical snapshot.
            for node in &self.nodes {
                if node.parent_id.is_some_and(|id| !nodes.contains_key(&id))
                    || node
                        .relations
                        .iter()
                        .any(|relation| !nodes.contains_key(&relation.target_id))
                {
                    return Err(invalid("Explicit endpoint has an unclosed node reference"));
                }
                let mut visited = BTreeSet::new();
                let mut parent = node.parent_id;
                while let Some(id) = parent {
                    if id == node.id || !visited.insert(id) || visited.len() > nodes.len() {
                        return Err(invalid("Explicit endpoint hierarchy contains a cycle"));
                    }
                    parent = nodes.get(&id).and_then(|node| node.parent_id);
                }
            }
        }
        if let Some(camera) = &self.camera {
            camera.validate()?;
        }
        let mut assets = BTreeSet::new();
        for asset in &self.assets {
            validate_digest(&asset.content_sha256)?;
            if !assets.insert(asset.asset_id) {
                return Err(invalid("duplicate endpoint asset identity"));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneBoundaryAppearance {
    pub predecessor_node_id: Uuid,
    pub successor_node_id: Uuid,
}

/// Optional selectors are useful only for recoverable Draft source. Explicit
/// declarations require all three, even when the inherited subset is empty.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum SceneBoundaryInput {
    SelfContained,
    Predecessor {
        #[serde(default)]
        scene_id: Option<Uuid>,
        #[serde(default)]
        boundary_id: Option<Uuid>,
        #[serde(default)]
        exit_sha256: Option<String>,
        #[serde(default)]
        appearances: Vec<SceneBoundaryAppearance>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum SceneBoundaryDefinition {
    DraftIncomplete {
        #[serde(default)]
        source: Option<SceneBoundarySource>,
        #[serde(default)]
        entry: Option<SceneBoundaryEndpoint>,
        #[serde(default)]
        exit: Option<SceneBoundaryEndpoint>,
    },
    Explicit {
        source: SceneBoundarySource,
        entry: SceneBoundaryEndpoint,
        exit: SceneBoundaryEndpoint,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneBoundary {
    pub version: u32,
    pub id: Uuid,
    pub scene_id: Uuid,
    pub input: SceneBoundaryInput,
    pub definition: SceneBoundaryDefinition,
}

impl SceneBoundary {
    pub fn source(&self) -> Option<&SceneBoundarySource> {
        match &self.definition {
            SceneBoundaryDefinition::DraftIncomplete { source, .. } => source.as_ref(),
            SceneBoundaryDefinition::Explicit { source, .. } => Some(source),
        }
    }

    /// One target invariant for visible branch copies and the existing Store's
    /// historical Change records. This cannot discover removed identities by
    /// itself; durable history admission remains after Store receipt replay.
    pub fn require_same_target_as(&self, previous: &SceneBoundary) -> Result<()> {
        if self.id != previous.id || self.scene_id != previous.scene_id {
            return Err(invalid("existing identity cannot change its scene"));
        }
        if let Some(previous_source) = previous.source()
            && self.source().is_none_or(|source| {
                source.deliverable_profile_id != previous_source.deliverable_profile_id
            })
        {
            return Err(invalid(
                "bound identity must retain its source/profile target",
            ));
        }
        Ok(())
    }

    pub fn validate_shape(&self) -> Result<()> {
        if self.version != SCENE_BOUNDARY_VERSION {
            return Err(invalid("unsupported declaration version"));
        }
        let complete = matches!(&self.definition, SceneBoundaryDefinition::Explicit { .. });
        match &self.definition {
            SceneBoundaryDefinition::DraftIncomplete {
                source,
                entry,
                exit,
            } => {
                if let Some(source) = source {
                    source.validate_shape()?;
                }
                for endpoint in [entry, exit].into_iter().flatten() {
                    endpoint.validate_shape(false)?;
                }
            }
            SceneBoundaryDefinition::Explicit {
                source,
                entry,
                exit,
            } => {
                source.validate_shape()?;
                entry.validate_shape(true)?;
                exit.validate_shape(true)?;
            }
        }
        if let SceneBoundaryInput::Predecessor {
            scene_id,
            boundary_id,
            exit_sha256,
            appearances,
        } = &self.input
        {
            if complete && (scene_id.is_none() || boundary_id.is_none() || exit_sha256.is_none()) {
                return Err(invalid("Explicit predecessor selectors are incomplete"));
            }
            if let Some(digest) = exit_sha256 {
                validate_digest(digest)?;
            }
            let mut predecessor_nodes = BTreeSet::new();
            let mut successor_nodes = BTreeSet::new();
            for appearance in appearances {
                if !predecessor_nodes.insert(appearance.predecessor_node_id)
                    || !successor_nodes.insert(appearance.successor_node_id)
                {
                    return Err(invalid("inherited appearance map is not injective"));
                }
            }
            if let SceneBoundaryDefinition::Explicit { entry, .. } = &self.definition {
                let nodes = entry
                    .nodes
                    .iter()
                    .map(|node| node.id)
                    .collect::<BTreeSet<_>>();
                if successor_nodes.iter().any(|id| !nodes.contains(id)) {
                    return Err(invalid(
                        "inherited successor node is absent from Explicit entry",
                    ));
                }
            }
        }
        Ok(())
    }

    /// Identity of the full persisted Explicit declaration, including source,
    /// entry, exit and predecessor. This pure digest asserts no currentness.
    pub fn bound_exit_sha256(&self) -> Result<String> {
        if !matches!(&self.definition, SceneBoundaryDefinition::Explicit { .. }) {
            return Err(invalid("bound exit requires an Explicit predecessor"));
        }
        self.validate_shape()?;
        canonical_sha256(BOUND_EXIT_PREFIX, self)
    }
}

fn canonical_sha256<T: Serialize>(prefix: &[u8], value: &T) -> Result<String> {
    let bytes = serde_json::to_vec(value).map_err(|error| invalid(error.to_string()))?;
    let mut digest = Sha256::new();
    digest.update(prefix);
    digest.update(bytes);
    Ok(hex::encode(digest.finalize()))
}

pub(crate) fn deserialize_scene_boundaries<'de, D>(
    deserializer: D,
) -> std::result::Result<Vec<SceneBoundary>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    struct BoundariesVisitor;
    impl<'de> Visitor<'de> for BoundariesVisitor {
        type Value = Vec<SceneBoundary>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("at most 4096 typed scene boundaries")
        }

        fn visit_seq<A>(self, mut entries: A) -> std::result::Result<Self::Value, A::Error>
        where
            A: SeqAccess<'de>,
        {
            let mut result = Vec::new();
            while let Some(boundary) = entries.next_element::<SceneBoundary>()? {
                if result.len() >= MAX_SCENE_BOUNDARIES_PER_COLLECTION {
                    return Err(de::Error::custom("too many scene boundaries"));
                }
                result.push(boundary);
            }
            Ok(result)
        }
    }
    deserializer.deserialize_seq(BoundariesVisitor)
}

impl ProductionDesign {
    /// Only saved shape and per-collection uniqueness. Stale or missing
    /// external references remain recoverable in current and branch states.
    pub fn validate_scene_boundaries(&self) -> Result<()> {
        if self.scene_boundaries.len() > MAX_SCENE_BOUNDARIES_PER_COLLECTION {
            return Err(invalid("declaration collection exceeds 4096 entries"));
        }
        let mut identities = BTreeSet::new();
        let mut sourced_targets = BTreeSet::new();
        let mut unsourced_scenes = BTreeSet::new();
        for boundary in &self.scene_boundaries {
            boundary.validate_shape()?;
            if !identities.insert(boundary.id) {
                return Err(invalid("duplicate declaration identity"));
            }
            match boundary.source() {
                Some(source) => {
                    if !sourced_targets.insert((boundary.scene_id, source.deliverable_profile_id)) {
                        return Err(invalid("duplicate scene/profile declaration"));
                    }
                }
                None => {
                    if !unsourced_scenes.insert(boundary.scene_id) {
                        return Err(invalid("duplicate unsourced Draft for scene"));
                    }
                }
            }
        }
        Ok(())
    }
}

struct CollectionByteCounter(usize);

impl io::Write for CollectionByteCounter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let next = self
            .0
            .checked_add(bytes.len())
            .ok_or_else(|| io::Error::other("scene boundary canonical collection size overflow"))?;
        if next > MAX_SCENE_BOUNDARY_COLLECTION_BYTES {
            return Err(io::Error::other(
                "scene boundary canonical collections exceed 8 MiB",
            ));
        }
        self.0 = next;
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// References only. This is not serializable authority, a rendered pose,
/// native readiness, an Asset byte grant or a statement of exact continuity.
#[derive(Debug)]
pub struct ResolvedSceneBoundary<'a> {
    pub boundary: &'a SceneBoundary,
    /// Nearest predecessor first, following the explicit dependency chain.
    pub predecessors: Vec<&'a SceneBoundary>,
    /// Actual registered Assets referenced by complete snapshots, UUID sorted.
    pub assets: Vec<&'a Asset>,
}

impl Project {
    fn scene_boundary_collections(&self) -> impl Iterator<Item = &[SceneBoundary]> {
        std::iter::once(self.production_design.scene_boundaries.as_slice()).chain(
            self.branch_workspaces.iter().flat_map(|workspace| {
                [
                    workspace
                        .base_state
                        .production_design
                        .scene_boundaries
                        .as_slice(),
                    workspace
                        .current_state
                        .production_design
                        .scene_boundaries
                        .as_slice(),
                ]
            }),
        )
    }

    pub(crate) fn has_scene_boundary_state(&self) -> bool {
        self.scene_boundary_collections()
            .any(|values| !values.is_empty())
    }

    pub(crate) fn validate_scene_boundary_budget(&self) -> Result<()> {
        let mut counter = CollectionByteCounter(0);
        for boundaries in self.scene_boundary_collections() {
            if boundaries.len() > MAX_SCENE_BOUNDARIES_PER_COLLECTION {
                return Err(invalid("declaration collection exceeds 4096 entries"));
            }
            // Identical canonical typed JSON byte count to to_vec, without an
            // aggregate allocation. Empty collections each contribute `[]`.
            serde_json::to_writer(&mut counter, boundaries)
                .map_err(|error| invalid(error.to_string()))?;
        }
        Ok(())
    }

    /// Capture actual pre-change source even when no destination declaration
    /// exists or its saved definition is incomplete. No source is rebound here.
    pub fn capture_scene_boundary_source(
        &self,
        scene_id: Uuid,
        deliverable_profile_id: Uuid,
        film_options: &SceneBoundaryFilmOptions,
    ) -> Result<SceneBoundarySource> {
        self.validate()?;
        film_options.validate()?;
        if !self.scenes.iter().any(|scene| scene.id == scene_id) {
            return Err(DomainError::NotFound(format!("scene:{scene_id}")));
        }
        let mut profile = self
            .deliverables
            .iter()
            .find(|profile| profile.id == deliverable_profile_id)
            .ok_or_else(|| DomainError::NotFound(format!("deliverable:{deliverable_profile_id}")))?
            .clone();
        // Selection alone must not change the scene's conservative baseline.
        profile.included_scene_ids.clear();
        let mut production_design = self.production_design.clone();
        production_design.scene_boundaries.clear();
        let mut assets = self.assets.iter().collect::<Vec<_>>();
        assets.sort_by_key(|asset| asset.id);
        #[derive(Serialize)]
        struct Dependencies<'a> {
            project_id: Uuid,
            generation: Uuid,
            active_branch: Uuid,
            scene_id: Uuid,
            deliverable_profile_id: Uuid,
            scenes: &'a [Scene],
            deliverable_profile: &'a DeliverableProfile,
            brief: &'a Brief,
            narrative: &'a Narrative,
            audio: &'a AudioState,
            visual_language: &'a VisualLanguage,
            production_design: &'a ProductionDesign,
            assets: Vec<&'a Asset>,
            film_options: &'a SceneBoundaryFilmOptions,
        }
        let dependencies = Dependencies {
            project_id: self.id,
            generation: self.generation,
            active_branch: self.active_branch,
            scene_id,
            deliverable_profile_id,
            scenes: &self.scenes,
            deliverable_profile: &profile,
            brief: &self.brief,
            narrative: &self.narrative,
            audio: &self.audio,
            visual_language: &self.visual_language,
            production_design: &production_design,
            assets,
            film_options,
        };
        Ok(SceneBoundarySource {
            project_id: self.id,
            generation: self.generation,
            branch_id: self.active_branch,
            authored_revision: self.revision,
            deliverable_profile_id,
            film_options: film_options.clone(),
            dependencies_sha256: canonical_sha256(DEPENDENCIES_PREFIX, &dependencies)?,
        })
    }

    fn ensure_scene_boundary_unlocked(&self, scene_id: Uuid) -> Result<()> {
        let kinds = [
            LockKind::Content,
            LockKind::Timing,
            LockKind::Position,
            LockKind::Style,
            LockKind::Renderer,
        ];
        self.ensure_unlocked(&self.resource_key(), &kinds)?;
        self.ensure_unlocked(&format!("scene:{scene_id}"), &kinds)
    }

    pub(crate) fn upsert_scene_boundary(&mut self, boundary: &SceneBoundary) -> Result<()> {
        boundary.validate_shape()?;
        self.ensure_scene_boundary_unlocked(boundary.scene_id)?;
        // Current and retained branch copies are evidence of an already bound
        // target. They are not merged into one shape-validation collection.
        for previous in self
            .scene_boundary_collections()
            .flatten()
            .filter(|previous| previous.id == boundary.id)
        {
            boundary.require_same_target_as(previous)?;
        }
        let previous = self
            .production_design
            .scene_boundaries
            .iter()
            .find(|previous| previous.id == boundary.id);
        if let Some(source) = boundary.source()
            && previous.and_then(SceneBoundary::source) != Some(source)
        {
            // Only new/changed source is an explicit binding. Unchanged old
            // provenance stays recoverable during Draft edits after import.
            let captured = self.capture_scene_boundary_source(
                boundary.scene_id,
                source.deliverable_profile_id,
                &source.film_options,
            )?;
            if source != &captured {
                return Err(invalid(
                    "new source binding must equal the canonical pre-change capture",
                ));
            }
        }
        if let Some(index) = self
            .production_design
            .scene_boundaries
            .iter()
            .position(|previous| previous.id == boundary.id)
        {
            self.production_design.scene_boundaries[index] = boundary.clone();
        } else {
            self.production_design
                .scene_boundaries
                .push(boundary.clone());
        }
        self.production_design.validate_scene_boundaries()?;
        self.validate_scene_boundary_budget()
    }

    pub(crate) fn remove_scene_boundary(&mut self, boundary_id: Uuid) -> Result<()> {
        let boundary = self
            .production_design
            .scene_boundaries
            .iter()
            .find(|boundary| boundary.id == boundary_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene-boundary:{boundary_id}")))?;
        self.ensure_scene_boundary_unlocked(boundary.scene_id)?;
        self.production_design
            .scene_boundaries
            .retain(|boundary| boundary.id != boundary_id);
        Ok(())
    }

    /// Select before checking freshness. No declaration is historical None;
    /// an incomplete, foreign, stale or wrong-profile declaration is an error.
    pub fn resolve_scene_boundary_for_execution(
        &self,
        scene_id: Uuid,
        deliverable_profile_id: Uuid,
        film_options: &SceneBoundaryFilmOptions,
    ) -> Result<Option<ResolvedSceneBoundary<'_>>> {
        self.validate_scene_boundary_budget()?;
        self.production_design.validate_scene_boundaries()?;
        let all = &self.production_design.scene_boundaries;
        let declared = all
            .iter()
            .filter(|boundary| boundary.scene_id == scene_id)
            .collect::<Vec<_>>();
        if declared.is_empty() {
            return Ok(None);
        }
        if declared.iter().any(|boundary| boundary.source().is_none()) {
            return Err(invalid(
                "unsourced Draft blocks new execution for every scene profile",
            ));
        }
        let matching = declared
            .into_iter()
            .filter(|boundary| {
                boundary
                    .source()
                    .is_some_and(|source| source.deliverable_profile_id == deliverable_profile_id)
            })
            .collect::<Vec<_>>();
        if matching.len() != 1 {
            return Err(invalid(
                "declared scene requires exactly one matching profile",
            ));
        }
        let selected = matching[0];
        let by_id = all
            .iter()
            .map(|boundary| (boundary.id, boundary))
            .collect::<BTreeMap<_, _>>();
        let mut visited = BTreeSet::new();
        let mut chain = Vec::new();
        let mut current = selected;
        loop {
            if !visited.insert(current.id) || visited.len() > all.len() {
                return Err(invalid(
                    "predecessor chain is cyclic or exceeds its declaration bound",
                ));
            }
            if !matches!(
                &current.definition,
                SceneBoundaryDefinition::Explicit { .. }
            ) {
                return Err(invalid(
                    "execution requires an Explicit boundary and predecessors",
                ));
            }
            chain.push(current);
            match &current.input {
                SceneBoundaryInput::SelfContained => break,
                SceneBoundaryInput::Predecessor {
                    scene_id,
                    boundary_id,
                    ..
                } => {
                    let predecessor_id =
                        boundary_id.ok_or_else(|| invalid("predecessor identity is incomplete"))?;
                    current = by_id
                        .get(&predecessor_id)
                        .copied()
                        .ok_or_else(|| invalid("declared predecessor is absent"))?;
                    if Some(current.scene_id) != *scene_id {
                        return Err(invalid("predecessor identity belongs to another scene"));
                    }
                }
            }
        }
        let mut assets = BTreeMap::new();
        for boundary in &chain {
            let SceneBoundaryDefinition::Explicit {
                source,
                entry,
                exit,
            } = &boundary.definition
            else {
                return Err(invalid("execution requires an Explicit declaration"));
            };
            if source.deliverable_profile_id != deliverable_profile_id
                || (boundary.id == selected.id && &source.film_options != film_options)
            {
                return Err(invalid("declared profile or actual Film options differ"));
            }
            let captured = self.capture_scene_boundary_source(
                boundary.scene_id,
                deliverable_profile_id,
                &source.film_options,
            )?;
            if source.project_id != captured.project_id
                || source.generation != captured.generation
                || source.branch_id != captured.branch_id
                || source.dependencies_sha256 != captured.dependencies_sha256
            {
                return Err(invalid(
                    "declared source is foreign or stale; explicitly rebind it",
                ));
            }
            // authored_revision is diagnostic provenance. Do not compare it
            // with a later global Project revision, including a future test.
            let scene = self
                .scenes
                .iter()
                .find(|scene| scene.id == boundary.scene_id)
                .ok_or_else(|| invalid("declared scene is absent"))?;
            for endpoint in [entry, exit] {
                let actual_ids = scene
                    .nodes
                    .iter()
                    .map(|node| node.id)
                    .collect::<BTreeSet<_>>();
                let retained_ids = endpoint
                    .nodes
                    .iter()
                    .map(|node| node.id)
                    .collect::<BTreeSet<_>>();
                if actual_ids != retained_ids
                    || endpoint.nodes.iter().any(|node| {
                        node.keyframes
                            .iter()
                            .any(|frame| frame.at >= scene.duration)
                    })
                {
                    return Err(invalid(
                        "Explicit snapshot does not retain the complete current scene roster",
                    ));
                }
                for reference in &endpoint.assets {
                    let asset = self
                        .assets
                        .iter()
                        .find(|asset| asset.id == reference.asset_id)
                        .ok_or_else(|| invalid("endpoint references an absent registered Asset"))?;
                    if asset.content_sha256.as_deref() != Some(reference.content_sha256.as_str()) {
                        return Err(invalid(
                            "endpoint Asset differs from its registered source digest",
                        ));
                    }
                    assets.insert(asset.id, asset);
                }
            }
            if let SceneBoundaryInput::Predecessor {
                boundary_id,
                exit_sha256,
                appearances,
                ..
            } = &boundary.input
            {
                let id =
                    boundary_id.ok_or_else(|| invalid("predecessor identity is incomplete"))?;
                let predecessor = by_id
                    .get(&id)
                    .copied()
                    .ok_or_else(|| invalid("declared predecessor is absent"))?;
                if exit_sha256.as_deref() != Some(predecessor.bound_exit_sha256()?.as_str()) {
                    return Err(invalid(
                        "predecessor full Explicit bound-exit identity changed",
                    ));
                }
                let SceneBoundaryDefinition::Explicit {
                    exit: predecessor_exit,
                    ..
                } = &predecessor.definition
                else {
                    return Err(invalid("predecessor must be Explicit"));
                };
                for appearance in appearances {
                    if !predecessor_exit
                        .nodes
                        .iter()
                        .any(|node| node.id == appearance.predecessor_node_id)
                        || !entry
                            .nodes
                            .iter()
                            .any(|node| node.id == appearance.successor_node_id)
                    {
                        return Err(invalid("inherited appearance endpoints are missing"));
                    }
                }
            }
        }
        Ok(Some(ResolvedSceneBoundary {
            boundary: selected,
            predecessors: chain.into_iter().skip(1).collect(),
            assets: assets.into_values().collect(),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn commit(project: &mut Project, change: Change) {
        let next = project.revision + 1;
        project.apply_change(&change).unwrap();
        project.commit_revision(next, &change).unwrap();
    }

    fn fixture() -> Project {
        let mut project = Project::new("Canonical boundary source").unwrap();
        for name in ["Predecessor", "Successor"] {
            commit(
                &mut project,
                Change::AddScene {
                    name: name.into(),
                    objective: "Original authored scene".into(),
                    duration_seconds: 2,
                },
            );
        }
        project
    }

    fn options() -> SceneBoundaryFilmOptions {
        SceneBoundaryFilmOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            role: SceneBoundaryNarrativeRole::Mechanism,
            archetype: SceneBoundaryArchetype::Statement,
        }
    }

    fn node(name: &str) -> CanvasNode {
        CanvasNode {
            id: Uuid::new_v4(),
            name: name.into(),
            kind: "rect".into(),
            parent_id: None,
            x: 31.0,
            y: 47.0,
            width: 125.0,
            height: 75.0,
            rotation_deg: 12.0,
            opacity: 0.7,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 2,
            style: NodeStyle {
                fill: Some("#f05a32".into()),
                ..NodeStyle::default()
            },
            relations: Vec::new(),
            property_locks: BTreeSet::new(),
            keyframes: Vec::new(),
        }
    }

    fn explicit(project: &Project, scene_id: Uuid) -> SceneBoundary {
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .unwrap();
        let endpoint = SceneBoundaryEndpoint {
            nodes: scene.nodes.clone(),
            camera: Some(scene.camera.clone()),
            assets: Vec::new(),
        };
        SceneBoundary {
            version: 1,
            id: Uuid::new_v4(),
            scene_id,
            input: SceneBoundaryInput::SelfContained,
            definition: SceneBoundaryDefinition::Explicit {
                source: project
                    .capture_scene_boundary_source(scene_id, project.deliverables[0].id, &options())
                    .unwrap(),
                entry: endpoint.clone(),
                exit: endpoint,
            },
        }
    }

    fn draft(scene_id: Uuid) -> SceneBoundary {
        SceneBoundary {
            version: 1,
            id: Uuid::new_v4(),
            scene_id,
            input: SceneBoundaryInput::Predecessor {
                scene_id: None,
                boundary_id: None,
                exit_sha256: None,
                appearances: Vec::new(),
            },
            definition: SceneBoundaryDefinition::DraftIncomplete {
                source: None,
                entry: None,
                exit: None,
            },
        }
    }

    fn source_mut(boundary: &mut SceneBoundary) -> &mut SceneBoundarySource {
        match &mut boundary.definition {
            SceneBoundaryDefinition::Explicit { source, .. }
            | SceneBoundaryDefinition::DraftIncomplete {
                source: Some(source),
                ..
            } => source,
            _ => panic!("source fixture requires a retained binding"),
        }
    }

    fn resolve<'a>(
        project: &'a Project,
        boundary: &SceneBoundary,
    ) -> Result<Option<ResolvedSceneBoundary<'a>>> {
        project.resolve_scene_boundary_for_execution(
            boundary.scene_id,
            boundary.source().unwrap().deliverable_profile_id,
            &boundary.source().unwrap().film_options,
        )
    }

    #[test]
    fn legacy_absence_and_empty_current_and_workspace_collections_preserve_wire() {
        let mut project = fixture();
        commit(
            &mut project,
            Change::CreateBranch {
                name: "Retained branch".into(),
            },
        );
        let original = serde_json::to_value(&project).unwrap();
        assert!(
            original["production_design"]
                .get("scene_boundaries")
                .is_none()
        );
        let mut with_empty = original.clone();
        with_empty["production_design"]["scene_boundaries"] = json!([]);
        for workspace in with_empty["branch_workspaces"].as_array_mut().unwrap() {
            workspace["base_state"]["production_design"]["scene_boundaries"] = json!([]);
            workspace["current_state"]["production_design"]["scene_boundaries"] = json!([]);
        }
        let restored: Project = serde_json::from_value(with_empty).unwrap();
        restored.validate().unwrap();
        assert_eq!(serde_json::to_value(&restored).unwrap(), original);
        assert!(
            restored
                .resolve_scene_boundary_for_execution(
                    restored.scenes[0].id,
                    restored.deliverables[0].id,
                    &options(),
                )
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn draft_wire_is_typed_partial_recoverable_and_never_implicit_self_contained() {
        let mut project = fixture();
        let boundary = draft(project.scenes[0].id);
        let mut wire = serde_json::to_value(&boundary).unwrap();
        wire["definition"].as_object_mut().unwrap().remove("source");
        wire["input"].as_object_mut().unwrap().remove("boundary_id");
        let restored: SceneBoundary = serde_json::from_value(wire).unwrap();
        assert_eq!(restored, boundary);
        assert!(serde_json::to_value(&restored).unwrap()["definition"]["source"].is_null());
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(restored),
            },
        );
        let reopened: Project =
            serde_json::from_slice(&serde_json::to_vec(&project).unwrap()).unwrap();
        reopened.validate().unwrap();
        for profile in &reopened.deliverables {
            assert!(
                reopened
                    .resolve_scene_boundary_for_execution(boundary.scene_id, profile.id, &options())
                    .is_err()
            );
        }
        let mut forged = serde_json::to_value(&boundary).unwrap();
        forged["definition"]["ready"] = json!(true);
        assert!(serde_json::from_value::<SceneBoundary>(forged).is_err());
    }

    #[test]
    fn explicit_intrinsic_closure_is_independent_of_current_project_references() {
        let mut project = fixture();
        let mut boundary = explicit(&project, project.scenes[0].id);
        boundary.scene_id = Uuid::new_v4();
        source_mut(&mut boundary).project_id = Uuid::new_v4();
        source_mut(&mut boundary).deliverable_profile_id = Uuid::new_v4();
        project
            .production_design
            .scene_boundaries
            .push(boundary.clone());
        project.validate().unwrap();
        assert!(resolve(&project, &boundary).is_err());
        let mut child = node("Partial child");
        child.parent_id = Some(Uuid::new_v4());
        let endpoint = SceneBoundaryEndpoint {
            nodes: vec![child],
            camera: Some(CameraState::default()),
            assets: Vec::new(),
        };
        endpoint.validate_shape(false).unwrap();
        assert!(endpoint.validate_shape(true).is_err());
        let mut malformed = endpoint;
        malformed.nodes[0].x = f64::NAN;
        assert!(malformed.validate_shape(false).is_err());
    }

    #[test]
    fn binding_at_n_survives_n_plus_one_and_later_diagnostic_revisions() {
        let mut project = fixture();
        let boundary = explicit(&project, project.scenes[0].id);
        let authored = project.revision;
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(boundary.clone()),
            },
        );
        assert_eq!(project.revision, authored + 1);
        assert_eq!(
            project.production_design.scene_boundaries[0]
                .source()
                .unwrap()
                .authored_revision,
            authored
        );
        assert!(resolve(&project, &boundary).unwrap().is_some());
        commit(
            &mut project,
            Change::RenameProject {
                title: "Diagnostic metadata only".into(),
            },
        );
        assert!(resolve(&project, &boundary).unwrap().is_some());
        // A retained diagnostic value never becomes a hidden current/future-N
        // freshness rule. New binding below still enforces the pre-change N.
        source_mut(&mut project.production_design.scene_boundaries[0]).authored_revision = u64::MAX;
        project.validate().unwrap();
        assert!(resolve(&project, &boundary).unwrap().is_some());
    }

    #[test]
    fn new_binding_requires_actual_prechange_capture_and_refusal_is_atomic() {
        let project = fixture();
        let boundary = explicit(&project, project.scenes[0].id);
        for field in 0..5 {
            let mut rejected = boundary.clone();
            let source = source_mut(&mut rejected);
            match field {
                0 => source.authored_revision += 1,
                1 => source.generation = Uuid::new_v4(),
                2 => source.branch_id = Uuid::new_v4(),
                3 => source.dependencies_sha256 = "ab".repeat(32),
                _ => source.project_id = Uuid::new_v4(),
            }
            let mut candidate = project.clone();
            let before = serde_json::to_vec(&candidate).unwrap();
            assert!(
                candidate
                    .apply_change(&Change::UpsertSceneBoundary {
                        boundary: Box::new(rejected)
                    })
                    .is_err()
            );
            assert_eq!(serde_json::to_vec(&candidate).unwrap(), before);
        }
    }

    #[test]
    fn dependencies_preserve_scene_order_and_include_other_scene_and_exact_options() {
        let project = fixture();
        let id = project.scenes[0].id;
        let profile = project.deliverables[0].id;
        let original = project
            .capture_scene_boundary_source(id, profile, &options())
            .unwrap();
        let mut changed = project.clone();
        changed.scenes[1].objective.push_str(" changed");
        assert_ne!(
            changed
                .capture_scene_boundary_source(id, profile, &options())
                .unwrap()
                .dependencies_sha256,
            original.dependencies_sha256
        );
        changed = project.clone();
        changed.scenes.swap(0, 1);
        assert_ne!(
            changed
                .capture_scene_boundary_source(id, profile, &options())
                .unwrap()
                .dependencies_sha256,
            original.dependencies_sha256
        );
        let mut new_options = options();
        new_options.role = SceneBoundaryNarrativeRole::Payoff;
        assert_ne!(
            project
                .capture_scene_boundary_source(id, profile, &new_options)
                .unwrap()
                .dependencies_sha256,
            original.dependencies_sha256
        );
        new_options = options();
        new_options.mono_font_family.push_str(" changed");
        assert_ne!(
            project
                .capture_scene_boundary_source(id, profile, &new_options)
                .unwrap()
                .dependencies_sha256,
            original.dependencies_sha256
        );
    }

    #[test]
    fn dependencies_clear_only_selection_and_boundaries_and_sort_assets_by_uuid() {
        let mut project = fixture();
        project.assets = [1, 2]
            .into_iter()
            .map(|ordinal| Asset {
                id: Uuid::from_u128(ordinal),
                name: format!("Asset {ordinal}"),
                media_type: "image/png".into(),
                content_sha256: Some(format!("{ordinal:064x}")),
                source_revision: None,
            })
            .collect();
        let scene = project.scenes[0].id;
        let profile = project.deliverables[0].id;
        let original = project
            .capture_scene_boundary_source(scene, profile, &options())
            .unwrap()
            .dependencies_sha256;
        project.assets.reverse();
        project.deliverables[0].included_scene_ids = vec![project.scenes[1].id];
        project
            .production_design
            .scene_boundaries
            .push(draft(scene));
        assert_eq!(
            project
                .capture_scene_boundary_source(scene, profile, &options())
                .unwrap()
                .dependencies_sha256,
            original
        );
        project.deliverables[0]
            .adaptation_notes
            .push("Profile change".into());
        assert_ne!(
            project
                .capture_scene_boundary_source(scene, profile, &options())
                .unwrap()
                .dependencies_sha256,
            original
        );
        project.deliverables[0].adaptation_notes.clear();
        project.assets[0].source_revision = Some("different registered source".into());
        assert_ne!(
            project
                .capture_scene_boundary_source(scene, profile, &options())
                .unwrap()
                .dependencies_sha256,
            original
        );
    }

    #[test]
    fn full_explicit_bound_exit_changes_when_non_exit_authorship_changes() {
        let project = fixture();
        let boundary = explicit(&project, project.scenes[0].id);
        let original = boundary.bound_exit_sha256().unwrap();
        let mut changed = boundary.clone();
        source_mut(&mut changed).authored_revision += 1;
        assert_ne!(changed.bound_exit_sha256().unwrap(), original);
        changed = boundary.clone();
        let SceneBoundaryDefinition::Explicit { entry, .. } = &mut changed.definition else {
            panic!("Explicit")
        };
        entry.camera.as_mut().unwrap().center_x += 7.0;
        assert_ne!(changed.bound_exit_sha256().unwrap(), original);
        changed = boundary;
        changed.input = SceneBoundaryInput::Predecessor {
            scene_id: Some(Uuid::new_v4()),
            boundary_id: Some(Uuid::new_v4()),
            exit_sha256: Some("cd".repeat(32)),
            appearances: Vec::new(),
        };
        assert_ne!(changed.bound_exit_sha256().unwrap(), original);
        assert!(draft(project.scenes[0].id).bound_exit_sha256().is_err());
    }

    #[test]
    fn selector_keeps_stale_foreign_incomplete_and_missing_profile_distinct_from_legacy() {
        let mut project = fixture();
        let boundary = explicit(&project, project.scenes[0].id);
        project
            .production_design
            .scene_boundaries
            .push(boundary.clone());
        let other_profile = project.deliverables[1].id;
        assert!(
            project
                .resolve_scene_boundary_for_execution(boundary.scene_id, other_profile, &options())
                .is_err()
        );
        for field in 0..3 {
            let mut stale = project.clone();
            let source = source_mut(&mut stale.production_design.scene_boundaries[0]);
            match field {
                0 => source.generation = Uuid::new_v4(),
                1 => source.project_id = Uuid::new_v4(),
                _ => source.dependencies_sha256 = "de".repeat(32),
            }
            stale.validate().unwrap();
            assert!(resolve(&stale, &boundary).is_err());
        }
        project
            .production_design
            .scene_boundaries
            .push(draft(boundary.scene_id));
        project.validate().unwrap();
        assert!(resolve(&project, &boundary).is_err());
        let mut duplicate = boundary.clone();
        duplicate.id = Uuid::new_v4();
        project.production_design.scene_boundaries = vec![boundary, duplicate];
        assert!(project.validate().is_err());
    }

    #[test]
    fn stable_target_latch_blocks_profile_erasure_and_retargeting_but_keeps_stale_draft() {
        let mut project = fixture();
        let boundary = explicit(&project, project.scenes[0].id);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(boundary.clone()),
            },
        );
        let changed_scene = project.scenes[1].id;
        commit(
            &mut project,
            Change::UpdateSceneObjective {
                scene_id: changed_scene,
                objective: "Changed conservative dependency".into(),
            },
        );
        assert!(resolve(&project, &boundary).is_err());
        let retained = boundary.source().unwrap().clone();
        let mut converted = boundary.clone();
        converted.definition = SceneBoundaryDefinition::DraftIncomplete {
            source: Some(retained.clone()),
            entry: None,
            exit: None,
        };
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(converted.clone()),
            },
        );
        assert_eq!(
            project.production_design.scene_boundaries[0].source(),
            Some(&retained)
        );
        for route in 0..3 {
            let mut changed = converted.clone();
            match route {
                0 => changed.scene_id = project.scenes[1].id,
                1 => {
                    let SceneBoundaryDefinition::DraftIncomplete { source, .. } =
                        &mut changed.definition
                    else {
                        panic!("Draft")
                    };
                    *source = None;
                }
                _ => source_mut(&mut changed).deliverable_profile_id = project.deliverables[1].id,
            }
            let before = serde_json::to_vec(&project).unwrap();
            assert!(
                project
                    .apply_change(&Change::UpsertSceneBoundary {
                        boundary: Box::new(changed)
                    })
                    .is_err()
            );
            assert_eq!(serde_json::to_vec(&project).unwrap(), before);
        }
        let rebound = project
            .capture_scene_boundary_source(
                converted.scene_id,
                retained.deliverable_profile_id,
                &retained.film_options,
            )
            .unwrap();
        source_mut(&mut converted).clone_from(&rebound);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(converted),
            },
        );
    }

    #[test]
    fn unsourced_draft_can_bind_once_and_remove_uses_existing_project_and_scene_locks() {
        let mut project = fixture();
        let first = draft(project.scenes[0].id);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(first.clone()),
            },
        );
        let mut bound = explicit(&project, first.scene_id);
        bound.id = first.id;
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(bound.clone()),
            },
        );
        assert!(resolve(&project, &bound).unwrap().is_some());
        for resource in [project.resource_key(), format!("scene:{}", bound.scene_id)] {
            let mut locked = project.clone();
            commit(
                &mut locked,
                Change::SetLock {
                    resource,
                    kind: LockKind::Content,
                    note: "Retain continuity source".into(),
                },
            );
            let before = serde_json::to_vec(&locked).unwrap();
            assert!(
                locked
                    .apply_change(&Change::RemoveSceneBoundary {
                        boundary_id: bound.id
                    })
                    .is_err()
            );
            assert_eq!(serde_json::to_vec(&locked).unwrap(), before);
        }
        commit(
            &mut project,
            Change::RemoveSceneBoundary {
                boundary_id: bound.id,
            },
        );
        assert!(resolve(&project, &bound).unwrap().is_none());
    }

    #[test]
    fn removed_identity_requires_existing_store_history_for_the_durable_target_latch() {
        let mut project = fixture();
        let original = explicit(&project, project.scenes[0].id);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(original.clone()),
            },
        );
        commit(
            &mut project,
            Change::RemoveSceneBoundary {
                boundary_id: original.id,
            },
        );
        assert!(project.branch_workspaces.is_empty());
        assert!(
            project
                .scene_boundary_collections()
                .all(|items| items.is_empty())
        );
        // Ordinary commit does not invent a hidden in-Project tombstone. The
        // existing Store event must supply `original` to this same predicate
        // after its persisted request-replay branch, before a new mutation.
        let mut retarget = explicit(&project, project.scenes[1].id);
        retarget.id = original.id;
        assert!(retarget.require_same_target_as(&original).is_err());
        retarget = explicit(&project, original.scene_id);
        retarget.id = original.id;
        assert!(retarget.require_same_target_as(&original).is_ok());
        retarget = draft(original.scene_id);
        retarget.id = original.id;
        assert!(retarget.require_same_target_as(&original).is_err());
        let fresh = explicit(&project, project.scenes[1].id);
        assert_ne!(fresh.id, original.id);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(fresh),
            },
        );
    }

    #[test]
    fn predecessor_resolves_off_selection_and_maps_inherited_subset_by_explicit_ids() {
        let mut project = fixture();
        let previous_scene = project.scenes[0].id;
        let next_scene = project.scenes[1].id;
        let a = node("Inherited origin");
        let mut b = a.clone();
        b.id = Uuid::new_v4();
        b.name = "Authored successor".into();
        let c = node("New successor appearance");
        commit(
            &mut project,
            Change::AddCanvasNode {
                scene_id: previous_scene,
                node: a.clone(),
            },
        );
        commit(
            &mut project,
            Change::AddCanvasNode {
                scene_id: next_scene,
                node: b.clone(),
            },
        );
        commit(
            &mut project,
            Change::AddCanvasNode {
                scene_id: next_scene,
                node: c,
            },
        );
        let predecessor = explicit(&project, previous_scene);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(predecessor.clone()),
            },
        );
        let mut successor = explicit(&project, next_scene);
        successor.input = SceneBoundaryInput::Predecessor {
            scene_id: Some(previous_scene),
            boundary_id: Some(predecessor.id),
            exit_sha256: Some(predecessor.bound_exit_sha256().unwrap()),
            appearances: vec![SceneBoundaryAppearance {
                predecessor_node_id: a.id,
                successor_node_id: b.id,
            }],
        };
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(successor.clone()),
            },
        );
        let mut selected = project.deliverables[0].clone();
        selected.included_scene_ids = vec![next_scene];
        commit(
            &mut project,
            Change::UpsertDeliverable { profile: selected },
        );
        let resolved = resolve(&project, &successor).unwrap().unwrap();
        assert_eq!(
            resolved
                .predecessors
                .iter()
                .map(|source| source.id)
                .collect::<Vec<_>>(),
            vec![predecessor.id]
        );
        assert_ne!(a.id, b.id);
        assert!(resolved.assets.is_empty());
        let mut empty_map = project.clone();
        let SceneBoundaryInput::Predecessor { appearances, .. } =
            &mut empty_map.production_design.scene_boundaries[1].input
        else {
            panic!("Predecessor")
        };
        appearances.clear();
        assert_eq!(
            resolve(&empty_map, &successor)
                .unwrap()
                .unwrap()
                .predecessors
                .len(),
            1
        );
        let mut absent_predecessor_node = project.clone();
        let SceneBoundaryInput::Predecessor { appearances, .. } =
            &mut absent_predecessor_node.production_design.scene_boundaries[1].input
        else {
            panic!("Predecessor")
        };
        appearances[0].predecessor_node_id = Uuid::new_v4();
        absent_predecessor_node.validate().unwrap();
        assert!(resolve(&absent_predecessor_node, &successor).is_err());
    }

    #[test]
    fn empty_map_does_not_cancel_missing_stale_or_cyclic_predecessor_dependency() {
        let mut project = fixture();
        let predecessor = explicit(&project, project.scenes[0].id);
        let mut successor = explicit(&project, project.scenes[1].id);
        successor.input = SceneBoundaryInput::Predecessor {
            scene_id: Some(predecessor.scene_id),
            boundary_id: Some(predecessor.id),
            exit_sha256: Some(predecessor.bound_exit_sha256().unwrap()),
            appearances: Vec::new(),
        };
        project.production_design.scene_boundaries = vec![successor.clone()];
        assert!(resolve(&project, &successor).is_err());
        project
            .production_design
            .scene_boundaries
            .push(predecessor.clone());
        assert!(resolve(&project, &successor).unwrap().is_some());
        source_mut(&mut project.production_design.scene_boundaries[1]).authored_revision += 1;
        assert!(
            resolve(&project, &successor).is_err(),
            "full declaration identity must change although the old exit pixels did not"
        );
        project.production_design.scene_boundaries[1] = predecessor;
        project.production_design.scene_boundaries[1].input = SceneBoundaryInput::Predecessor {
            scene_id: Some(successor.scene_id),
            boundary_id: Some(successor.id),
            exit_sha256: Some("ef".repeat(32)),
            appearances: Vec::new(),
        };
        let error = resolve(&project, &successor).unwrap_err();
        assert!(error.to_string().contains("cyclic"));
        project.production_design.scene_boundaries[0].input = SceneBoundaryInput::Predecessor {
            scene_id: Some(successor.scene_id),
            boundary_id: Some(successor.id),
            exit_sha256: Some("ef".repeat(32)),
            appearances: Vec::new(),
        };
        assert!(
            resolve(&project, &successor)
                .unwrap_err()
                .to_string()
                .contains("cyclic")
        );
    }

    #[test]
    fn appearance_mapping_requires_injection_and_both_real_endpoint_identities() {
        let mut project = fixture();
        let scene = project.scenes[0].id;
        let n = node("Actual current node");
        commit(
            &mut project,
            Change::AddCanvasNode {
                scene_id: scene,
                node: n.clone(),
            },
        );
        let mut boundary = explicit(&project, scene);
        let appearance = SceneBoundaryAppearance {
            predecessor_node_id: Uuid::new_v4(),
            successor_node_id: n.id,
        };
        boundary.input = SceneBoundaryInput::Predecessor {
            scene_id: Some(project.scenes[1].id),
            boundary_id: Some(Uuid::new_v4()),
            exit_sha256: Some("11".repeat(32)),
            appearances: vec![appearance.clone(), appearance],
        };
        assert!(boundary.validate_shape().is_err());
        let SceneBoundaryInput::Predecessor { appearances, .. } = &mut boundary.input else {
            panic!("Predecessor")
        };
        appearances.pop();
        appearances[0].successor_node_id = Uuid::new_v4();
        assert!(boundary.validate_shape().is_err());
        boundary.input = SceneBoundaryInput::SelfContained;
        let SceneBoundaryDefinition::Explicit { entry, .. } = &mut boundary.definition else {
            panic!("Explicit")
        };
        entry.nodes.clear();
        boundary.validate_shape().unwrap();
        project
            .production_design
            .scene_boundaries
            .push(boundary.clone());
        assert!(
            resolve(&project, &boundary).is_err(),
            "shape-valid incomplete current roster is not executable"
        );
    }

    #[test]
    fn registered_assets_are_resolved_deduplicated_and_endpoint_only_digests_are_not_assets() {
        let mut project = fixture();
        let asset = Asset {
            id: Uuid::new_v4(),
            name: "Original".into(),
            media_type: "image/png".into(),
            content_sha256: Some("12".repeat(32)),
            source_revision: None,
        };
        project.assets.push(asset.clone());
        let mut boundary = explicit(&project, project.scenes[0].id);
        let SceneBoundaryDefinition::Explicit { entry, exit, .. } = &mut boundary.definition else {
            panic!("Explicit")
        };
        let reference = SceneBoundaryAsset {
            asset_id: asset.id,
            content_sha256: asset.content_sha256.clone().unwrap(),
        };
        entry.assets.push(reference.clone());
        exit.assets.push(reference);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(boundary.clone()),
            },
        );
        let resolved = resolve(&project, &boundary).unwrap().unwrap();
        assert_eq!(resolved.assets.len(), 1);
        assert!(std::ptr::eq(resolved.assets[0], &project.assets[0]));
        let mut wrong = project.clone();
        let SceneBoundaryDefinition::Explicit { entry, .. } =
            &mut wrong.production_design.scene_boundaries[0].definition
        else {
            panic!("Explicit")
        };
        entry.assets[0].content_sha256 = "34".repeat(32);
        wrong.validate().unwrap();
        assert!(resolve(&wrong, &boundary).is_err());
        let mut absent = project;
        absent.assets.clear();
        absent.validate().unwrap();
        assert!(resolve(&absent, &boundary).is_err());
        let captured = absent
            .capture_scene_boundary_source(
                boundary.scene_id,
                boundary.source().unwrap().deliverable_profile_id,
                &options(),
            )
            .unwrap();
        *source_mut(&mut absent.production_design.scene_boundaries[0]) = captured;
        assert!(
            resolve(&absent, &boundary)
                .unwrap_err()
                .to_string()
                .contains("absent registered Asset")
        );
    }

    #[test]
    fn branch_and_import_identity_changes_preserve_source_and_require_explicit_rebind() {
        let mut project = fixture();
        let boundary = explicit(&project, project.scenes[0].id);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(boundary.clone()),
            },
        );
        let original = serde_json::to_value(&project.production_design.scene_boundaries).unwrap();
        commit(
            &mut project,
            Change::CreateBranch {
                name: "Independent branch".into(),
            },
        );
        let branch_id = project.branches.last().unwrap().id;
        commit(&mut project, Change::CheckoutBranch { branch_id });
        assert_eq!(
            serde_json::to_value(&project.production_design.scene_boundaries).unwrap(),
            original
        );
        assert!(resolve(&project, &boundary).is_err());
        // Domain import semantics: identity changes do not rewrite retained
        // authorship. Actual portable Store replay is a separate Service test.
        project.generation = Uuid::new_v4();
        project.validate().unwrap();
        assert_eq!(
            serde_json::to_value(&project.production_design.scene_boundaries).unwrap(),
            original
        );
        assert!(resolve(&project, &boundary).is_err());
    }

    #[test]
    fn per_collection_limit_and_workspace_aggregate_limit_reject_atomically() {
        let mut maximum = fixture();
        maximum.production_design.scene_boundaries = (0..MAX_SCENE_BOUNDARIES_PER_COLLECTION)
            .map(|_| draft(Uuid::new_v4()))
            .collect();
        maximum.validate().unwrap();
        let before = serde_json::to_vec(&maximum).unwrap();
        assert!(
            maximum
                .apply_change(&Change::UpsertSceneBoundary {
                    boundary: Box::new(draft(Uuid::new_v4()))
                })
                .is_err()
        );
        assert_eq!(serde_json::to_vec(&maximum).unwrap(), before);
        maximum
            .production_design
            .scene_boundaries
            .push(draft(Uuid::new_v4()));
        assert!(serde_json::from_slice::<Project>(&serde_json::to_vec(&maximum).unwrap()).is_err());

        let mut project = fixture();
        let mut large_options = options();
        large_options.font_family = "f".repeat(3 * 1024 * 1024);
        let scene_id = project.scenes[0].id;
        let source = project
            .capture_scene_boundary_source(scene_id, project.deliverables[0].id, &large_options)
            .unwrap();
        let mut boundary = draft(scene_id);
        boundary.definition = SceneBoundaryDefinition::DraftIncomplete {
            source: Some(source),
            entry: None,
            exit: None,
        };
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(boundary),
            },
        );
        let before = serde_json::to_vec(&project).unwrap();
        assert!(
            project
                .apply_change(&Change::CreateBranch {
                    name: "Would duplicate three-MiB source snapshots".into()
                })
                .is_err()
        );
        assert_eq!(serde_json::to_vec(&project).unwrap(), before);
        assert!(project.branch_workspaces.is_empty());
    }

    #[test]
    fn canonical_collection_budget_counts_exact_current_and_workspace_json_bytes() {
        let mut project = fixture();
        let boundary = draft(project.scenes[0].id);
        commit(
            &mut project,
            Change::UpsertSceneBoundary {
                boundary: Box::new(boundary),
            },
        );
        commit(
            &mut project,
            Change::CreateBranch {
                name: "Copies are independently counted".into(),
            },
        );
        let mut counted = CollectionByteCounter(0);
        let mut independently_encoded = 0;
        for collection in project.scene_boundary_collections() {
            independently_encoded += serde_json::to_vec(collection).unwrap().len();
            serde_json::to_writer(&mut counted, collection).unwrap();
        }
        assert_eq!(counted.0, independently_encoded);
        assert!(project.scene_boundary_collections().count() > 1);
        let mut exact = CollectionByteCounter(MAX_SCENE_BOUNDARY_COLLECTION_BYTES - 2);
        serde_json::to_writer(&mut exact, &Vec::<SceneBoundary>::new()).unwrap();
        assert_eq!(exact.0, MAX_SCENE_BOUNDARY_COLLECTION_BYTES);
        assert!(serde_json::to_writer(&mut exact, &Vec::<SceneBoundary>::new()).is_err());
    }
}
