//! Renderer-native creative documents are retained alongside the editorial scene.
//! They are not lowered to Film, and importing one never installs a renderer or grants file access.
use crate::*;
use hyperframes_profile::{HyperframesDocument, NodeField, Property};
pub use motionwright_hyperframes_profile as hyperframes_profile;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
#[serde(deny_unknown_fields)]
pub struct CreativeWorkspace {
    #[serde(default)]
    pub native_scenes: Vec<NativeSceneDocument>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeSceneDocument {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub profile_id: Uuid,
    pub label: String,
    pub source: NativeSceneSource,
    /// Explicit linkage to an immutable imported capsule; not a source-execution capability.
    pub source_capsule_id: Option<Uuid>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(
    tag = "renderer",
    content = "document",
    rename_all = "kebab-case",
    deny_unknown_fields
)]
pub enum NativeSceneSource {
    Hyperframes(HyperframesDocument),
}
impl NativeSceneSource {
    pub fn source_digest(&self) -> Result<String> {
        match self {
            Self::Hyperframes(doc) => hyperframes_profile::source_digest(doc)
                .map_err(|e| DomainError::Invalid(e.to_string())),
        }
    }
    pub fn renderer(&self) -> RendererKind {
        match self {
            Self::Hyperframes(_) => RendererKind::Hyperframes,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CreativeWorkspaceEdit {
    SetNativeProtection {
        id: Uuid,
        node_id: Uuid,
        protection: NativeProtection,
        locked: bool,
        expected_source_sha256: String,
        rationale: String,
    },
    UpsertNativeScene {
        scene: NativeSceneDocument,
        expected_source_sha256: Option<String>,
    },
    RemoveNativeScene {
        id: Uuid,
        expected_source_sha256: String,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum NativeProtection {
    Property { property: Property },
    Field { field: NodeField },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeSceneDifference {
    pub document_id: Uuid,
    pub source_sha256: Option<String>,
    pub proposed_source_sha256: Option<String>,
    pub scene_id: Uuid,
    pub profile_id: Uuid,
    pub added_nodes: Vec<Uuid>,
    pub removed_nodes: Vec<Uuid>,
    pub changed_nodes: Vec<Uuid>,
    pub camera_changed: bool,
    pub canvas_changed: bool,
    pub asset_dependencies_changed: bool,
    pub dirty_start: RationalTime,
    pub dirty_end: RationalTime,
    pub evidence_level: String,
}
impl NativeSceneDocument {
    pub fn validate(&self, project: &Project) -> Result<()> {
        self.validate_in(
            &project.scenes,
            &project.assets,
            &project.deliverables,
            &project.production_design.capsules,
        )
    }
    pub fn validate_in(
        &self,
        scenes: &[Scene],
        assets: &[Asset],
        profiles: &[DeliverableProfile],
        capsules: &[NativeCapsule],
    ) -> Result<()> {
        design_text(&self.label, 256, "native document label")?;
        let _scene = scenes
            .iter()
            .find(|scene| scene.id == self.scene_id)
            .ok_or_else(|| DomainError::NotFound("native scene binding".into()))?;
        let _profile = profiles
            .iter()
            .find(|profile| profile.id == self.profile_id)
            .ok_or_else(|| DomainError::NotFound("native output profile binding".into()))?;
        if let Some(id) = self.source_capsule_id
            && !capsules
                .iter()
                .any(|capsule| capsule.id == id && capsule.scene_id == self.scene_id)
        {
            return Err(DomainError::Invalid(
                "native source capsule is absent or belongs to another scene".into(),
            ));
        }
        match &self.source {
            NativeSceneSource::Hyperframes(doc) => {
                hyperframes_profile::validate_document(doc)
                    .map_err(|e| DomainError::Invalid(e.to_string()))?;
                for asset in &doc.assets {
                    if !assets.iter().any(|entry| {
                        entry.id == asset.id
                            && entry.content_sha256.as_deref() == Some(&asset.sha256)
                    }) {
                        return Err(DomainError::Invalid("native document refers to an asset not imported into this project with the same digest".into()));
                    }
                }
            }
        }
        Ok(())
    }
}

fn locked_native_change(old: &NativeSceneSource, new: &NativeSceneSource) -> Result<()> {
    let (NativeSceneSource::Hyperframes(old), NativeSceneSource::Hyperframes(new)) = (old, new);
    for before in &old.nodes {
        if before.locked_properties.is_empty() && before.locked_fields.is_empty() {
            continue;
        }
        let after = new
            .nodes
            .iter()
            .find(|node| node.id == before.id)
            .ok_or_else(|| {
                DomainError::Locked(format!(
                    "native object {} cannot be removed while properties are locked",
                    before.id
                ))
            })?;
        for field in &before.locked_fields {
            let changed = match field {
                NodeField::Content => before.content != after.content,
                NodeField::Appearance => {
                    before.blend != after.blend
                        || before.clip != after.clip
                        || before.effects != after.effects
                }
                NodeField::Parent => before.parent_id != after.parent_id,
                NodeField::Keyframes => before.keyframes != after.keyframes,
                NodeField::Metadata => before.name != after.name,
            };
            if changed || !after.locked_fields.contains(field) {
                return Err(DomainError::Locked(format!(
                    "native node {} field {:?}",
                    before.id, field
                )));
            }
        }
        for property in &before.locked_properties {
            let pose = match property {
                Property::X => before.pose.x != after.pose.x,
                Property::Y => before.pose.y != after.pose.y,
                Property::Width => before.pose.width != after.pose.width,
                Property::Height => before.pose.height != after.pose.height,
                Property::ScaleX => before.pose.scale_x != after.pose.scale_x,
                Property::ScaleY => before.pose.scale_y != after.pose.scale_y,
                Property::Rotation => before.pose.rotation != after.pose.rotation,
                Property::Opacity => before.pose.opacity != after.pose.opacity,
                Property::Blur => before.effects.blur != after.effects.blur,
                Property::ClipTop
                | Property::ClipRight
                | Property::ClipBottom
                | Property::ClipLeft => before.clip != after.clip,
            };
            let motion = before
                .keyframes
                .iter()
                .filter(|k| k.property == *property)
                .ne(after.keyframes.iter().filter(|k| k.property == *property));
            if pose || motion || !after.locked_properties.contains(property) {
                return Err(DomainError::Locked(format!(
                    "native node {} property {:?}; unlock explicitly before replacing this document",
                    before.id, property
                )));
            }
        }
    }
    Ok(())
}
impl CreativeWorkspace {
    pub fn is_empty(&self) -> bool {
        self.native_scenes.is_empty()
    }
    pub fn validate(&self, project: &Project) -> Result<()> {
        self.validate_in(
            &project.scenes,
            &project.assets,
            &project.deliverables,
            &project.production_design.capsules,
        )
    }
    pub fn validate_in(
        &self,
        scenes: &[Scene],
        assets: &[Asset],
        profiles: &[DeliverableProfile],
        capsules: &[NativeCapsule],
    ) -> Result<()> {
        if self.native_scenes.len() > 128 {
            return Err(DomainError::Invalid(
                "native creative document budget exceeded".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        let mut bindings = BTreeSet::new();
        for document in &self.native_scenes {
            document.validate_in(scenes, assets, profiles, capsules)?;
            if !ids.insert(document.id)
                || !bindings.insert((document.scene_id, document.profile_id))
            {
                return Err(DomainError::Invalid(
                    "native document identity or scene/profile binding is duplicated".into(),
                ));
            }
        }
        Ok(())
    }
}
impl Project {
    fn apply_workspace_edit_inner(&mut self, edit: &CreativeWorkspaceEdit) -> Result<()> {
        self.ensure_unlocked(
            &self.resource_key(),
            &[
                LockKind::Content,
                LockKind::Position,
                LockKind::Style,
                LockKind::Timing,
            ],
        )?;
        match edit {
            CreativeWorkspaceEdit::SetNativeProtection {
                id,
                node_id,
                protection,
                locked,
                expected_source_sha256,
                rationale,
            } => {
                design_text(rationale, 2000, "native protection rationale")?;
                let existing = self
                    .production_design
                    .workspace
                    .native_scenes
                    .iter()
                    .find(|doc| doc.id == *id)
                    .ok_or_else(|| DomainError::NotFound("native document".into()))?;
                self.ensure_unlocked(
                    &format!("scene:{}", existing.scene_id),
                    &[LockKind::Content],
                )?;
                if existing.source.source_digest()? != *expected_source_sha256 {
                    return Err(DomainError::Invalid(
                        "native source precondition is stale".into(),
                    ));
                }
                let existing = self
                    .production_design
                    .workspace
                    .native_scenes
                    .iter_mut()
                    .find(|doc| doc.id == *id)
                    .expect("validated native document");
                let NativeSceneSource::Hyperframes(document) = &mut existing.source;
                let node = document
                    .nodes
                    .iter_mut()
                    .find(|node| node.id == *node_id)
                    .ok_or_else(|| DomainError::NotFound("native node".into()))?;
                match protection {
                    NativeProtection::Property { property } => {
                        node.locked_properties.retain(|p| p != property);
                        if *locked {
                            node.locked_properties.push(*property);
                            node.locked_properties.sort();
                        }
                    }
                    NativeProtection::Field { field } => {
                        node.locked_fields.retain(|p| p != field);
                        if *locked {
                            node.locked_fields.push(*field);
                            node.locked_fields.sort();
                        }
                    }
                }
            }
            CreativeWorkspaceEdit::UpsertNativeScene {
                scene,
                expected_source_sha256,
            } => {
                scene.validate(self)?;
                self.ensure_unlocked(
                    &format!("scene:{}", scene.scene_id),
                    &[
                        LockKind::Content,
                        LockKind::Position,
                        LockKind::Style,
                        LockKind::Timing,
                        LockKind::Renderer,
                    ],
                )?;
                let docs = &mut self.production_design.workspace.native_scenes;
                if let Some(old) = docs.iter_mut().find(|document| document.id == scene.id) {
                    if expected_source_sha256.as_deref() != Some(&old.source.source_digest()?) {
                        return Err(DomainError::Invalid("native source precondition is stale; observe and review this document again".into()));
                    }
                    if old.scene_id != scene.scene_id || old.profile_id != scene.profile_id {
                        return Err(DomainError::Invalid(
                            "native document binding cannot be changed by replacement".into(),
                        ));
                    }
                    locked_native_change(&old.source, &scene.source)?;
                    *old = scene.clone();
                } else {
                    if expected_source_sha256.is_some() {
                        return Err(DomainError::Invalid(
                            "native source expected an existing document but none exists".into(),
                        ));
                    }
                    if docs.len() >= 128
                        || docs.iter().any(|doc| {
                            doc.scene_id == scene.scene_id && doc.profile_id == scene.profile_id
                        })
                    {
                        return Err(DomainError::Invalid(
                            "native document budget or scene/profile binding conflict".into(),
                        ));
                    }
                    docs.push(scene.clone());
                }
                self.scenes
                    .iter_mut()
                    .find(|s| s.id == scene.scene_id)
                    .expect("validated scene")
                    .status = SceneStatus::Draft;
            }
            CreativeWorkspaceEdit::RemoveNativeScene {
                id,
                expected_source_sha256,
            } => {
                let old = self
                    .production_design
                    .workspace
                    .native_scenes
                    .iter()
                    .find(|doc| doc.id == *id)
                    .ok_or_else(|| DomainError::NotFound("native document".into()))?;
                self.ensure_unlocked(
                    &format!("scene:{}", old.scene_id),
                    &[LockKind::Content, LockKind::Renderer],
                )?;
                if old.source.source_digest()? != *expected_source_sha256 {
                    return Err(DomainError::Invalid(
                        "native source precondition is stale".into(),
                    ));
                }
                let NativeSceneSource::Hyperframes(doc) = &old.source;
                if doc.nodes.iter().any(|node| {
                    !node.locked_properties.is_empty() || !node.locked_fields.is_empty()
                }) {
                    return Err(DomainError::Locked(
                        "native document contains protected properties".into(),
                    ));
                }
                if self.scenes.iter().any(|scene| {
                    scene.id == old.scene_id && scene.renderer == old.source.renderer()
                }) {
                    return Err(DomainError::Invalid(
                        "select another renderer before removing the active native realization"
                            .into(),
                    ));
                }
                self.production_design
                    .workspace
                    .native_scenes
                    .retain(|doc| doc.id != *id);
            }
        }
        Ok(())
    }
    pub(crate) fn edit_creative_workspace(&mut self, edit: &CreativeWorkspaceEdit) -> Result<()> {
        let mut candidate = self.clone();
        candidate.apply_workspace_edit_inner(edit)?;
        candidate.validate()?;
        *self = candidate;
        Ok(())
    }
    pub fn preview_creative_workspace(
        &self,
        edit: &CreativeWorkspaceEdit,
    ) -> Result<NativeSceneDifference> {
        self.validate()?;
        let mut candidate = self.clone();
        candidate.schema_version = PROJECT_SCHEMA_VERSION;
        candidate.apply_workspace_edit_inner(edit)?;
        candidate.validate()?;
        let id = match edit {
            CreativeWorkspaceEdit::UpsertNativeScene { scene, .. } => scene.id,
            CreativeWorkspaceEdit::RemoveNativeScene { id, .. } => *id,
            CreativeWorkspaceEdit::SetNativeProtection { id, .. } => *id,
        };
        let old = self
            .production_design
            .workspace
            .native_scenes
            .iter()
            .find(|doc| doc.id == id);
        let new = candidate
            .production_design
            .workspace
            .native_scenes
            .iter()
            .find(|doc| doc.id == id);
        let reference = new.or(old).expect("edit created or removed a document");
        let nodes = |doc: Option<&NativeSceneDocument>| {
            doc.map(|doc| match &doc.source {
                NativeSceneSource::Hyperframes(doc) => doc
                    .nodes
                    .iter()
                    .map(|node| (node.id, node.clone()))
                    .collect::<BTreeMap<_, _>>(),
            })
            .unwrap_or_default()
        };
        let before = nodes(old);
        let after = nodes(new);
        let scene = self
            .scenes
            .iter()
            .find(|s| s.id == reference.scene_id)
            .expect("validated scene");
        let (camera, canvas, assets) = match (old, new) {
            (Some(old), Some(new)) => {
                let (NativeSceneSource::Hyperframes(a), NativeSceneSource::Hyperframes(b)) =
                    (&old.source, &new.source);
                (
                    a.camera != b.camera,
                    a.canvas != b.canvas,
                    a.assets != b.assets,
                )
            }
            _ => (true, true, true),
        };
        Ok(NativeSceneDifference {
            document_id: id,
            source_sha256: old.map(|doc| doc.source.source_digest()).transpose()?,
            proposed_source_sha256: new.map(|doc| doc.source.source_digest()).transpose()?,
            scene_id: reference.scene_id,
            profile_id: reference.profile_id,
            added_nodes: after
                .keys()
                .filter(|id| !before.contains_key(id))
                .copied()
                .collect(),
            removed_nodes: before
                .keys()
                .filter(|id| !after.contains_key(id))
                .copied()
                .collect(),
            changed_nodes: after
                .iter()
                .filter_map(|(id, node)| before.get(id).filter(|old| *old != node).map(|_| *id))
                .collect(),
            camera_changed: camera,
            canvas_changed: canvas,
            asset_dependencies_changed: assets,
            dirty_start: scene.start,
            dirty_end: scene
                .start
                .checked_add(scene.duration)
                .map_err(|e| DomainError::Invalid(e.to_string()))?,
            evidence_level: "semantic_scope_preview_not_a_native_pixel_equivalence_verdict".into(),
        })
    }
}
