use crate::{CanvasNode, DomainError, RationalTime, Result, Scene};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum CoordinateSpace {
    #[default]
    ProjectPixels,
    Normalized,
    SceneLocal,
}

/// Intent for one reparent operation. Absence retains the historical world pose.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReparentCoordinatePolicy {
    World,
    Local,
}

impl<'de> Deserialize<'de> for ReparentCoordinatePolicy {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        // The new wire is a string, not the alternate externally tagged map
        // representation an enum deserializer could otherwise accept.
        let value = String::deserialize(deserializer)?;
        match value.as_str() {
            "world" => Ok(Self::World),
            "local" => Ok(Self::Local),
            other => Err(serde::de::Error::unknown_variant(
                other,
                &["world", "local"],
            )),
        }
    }
}

// `default` on the containing field handles absence. A present member must be
// the enum itself: decoding Option here would incorrectly accept explicit null.
pub(crate) fn deserialize_reparent_coordinate_policy<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<ReparentCoordinatePolicy>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    ReparentCoordinatePolicy::deserialize(deserializer).map(Some)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeProperty {
    Position,
    Size,
    Rotation,
    Opacity,
    Text,
    Style,
    Parent,
    Order,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionProperty {
    X,
    Y,
    Width,
    Height,
    RotationDeg,
    Opacity,
}

impl MotionProperty {
    pub fn node_property(self) -> NodeProperty {
        match self {
            Self::X | Self::Y => NodeProperty::Position,
            Self::Width | Self::Height => NodeProperty::Size,
            Self::RotationDeg => NodeProperty::Rotation,
            Self::Opacity => NodeProperty::Opacity,
        }
    }

    pub fn validate_value(self, value: f64) -> Result<()> {
        if !value.is_finite()
            || matches!(self, Self::Width | Self::Height) && value < 0.0
            || self == Self::Opacity && !(0.0..=1.0).contains(&value)
        {
            return Err(DomainError::Invalid(
                "canvas keyframe value is out of bounds".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionInterpolation {
    EaseOutCubic,
    Hold,
    Linear,
    EaseInOut,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanvasKeyframe {
    pub at: RationalTime,
    pub property: MotionProperty,
    pub value: f64,
    pub interpolation: MotionInterpolation,
}

impl CanvasKeyframe {
    pub fn validate(&self) -> Result<()> {
        if self.at.validate().is_err() || self.at < RationalTime::ZERO {
            return Err(DomainError::Invalid(
                "canvas keyframe time is invalid".into(),
            ));
        }
        self.property.validate_value(self.value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum BlendMode {
    #[default]
    Normal,
    Multiply,
    Screen,
    Add,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeStyle {
    pub fill: Option<String>,
    pub stroke: Option<String>,
    pub stroke_width: f64,
    pub font_family: Option<String>,
    pub font_size: Option<f64>,
    pub font_weight: Option<u16>,
    pub line_height: Option<f64>,
    pub blend_mode: BlendMode,
}

impl Default for NodeStyle {
    fn default() -> Self {
        Self {
            fill: None,
            stroke: None,
            stroke_width: 0.0,
            font_family: None,
            font_size: None,
            font_weight: None,
            line_height: None,
            blend_mode: BlendMode::Normal,
        }
    }
}

impl NodeStyle {
    pub fn validate(&self) -> Result<()> {
        if !self.stroke_width.is_finite() || !(0.0..=1_000.0).contains(&self.stroke_width) {
            return Err(DomainError::Invalid(
                "canvas stroke width is invalid".into(),
            ));
        }
        if self
            .font_size
            .is_some_and(|value| !value.is_finite() || !(1.0..=2_048.0).contains(&value))
            || self
                .line_height
                .is_some_and(|value| !value.is_finite() || !(0.1..=20.0).contains(&value))
            || self
                .font_weight
                .is_some_and(|value| !(1..=1_000).contains(&value))
        {
            return Err(DomainError::Invalid("canvas typography is invalid".into()));
        }
        for value in [
            self.fill.as_deref(),
            self.stroke.as_deref(),
            self.font_family.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            if value.len() > 512 {
                return Err(DomainError::Invalid(
                    "canvas style value is too long".into(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationKind {
    AlignLeft,
    AlignCenterX,
    AlignRight,
    AlignTop,
    AlignCenterY,
    AlignBottom,
    Follow,
    Attach,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeRelation {
    pub id: Uuid,
    pub kind: RelationKind,
    pub target_id: Uuid,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanvasTransform {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub rotation_deg: f64,
    pub opacity: f64,
}

impl CanvasTransform {
    pub fn validate(self) -> Result<()> {
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
                    "canvas transform contains a non-finite number".into(),
                ));
            }
        }
        if self.width < 0.0 || self.height < 0.0 || !(0.0..=1.0).contains(&self.opacity) {
            return Err(DomainError::Invalid(
                "canvas transform is out of bounds".into(),
            ));
        }
        Ok(())
    }
}

fn node_transform(node: &CanvasNode) -> CanvasTransform {
    CanvasTransform {
        x: node.x,
        y: node.y,
        width: node.width,
        height: node.height,
        rotation_deg: node.rotation_deg,
        opacity: node.opacity,
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
struct ParentFrame {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    rotation_deg: f64,
}

impl ParentFrame {
    fn from_parent(parent: Option<&CanvasNode>) -> Self {
        match parent {
            Some(parent) => Self {
                x: parent.x,
                y: parent.y,
                width: parent.width,
                height: parent.height,
                rotation_deg: parent.rotation_deg,
            },
            None => Self {
                x: 0.0,
                y: 0.0,
                width: 0.0,
                height: 0.0,
                rotation_deg: 0.0,
            },
        }
    }

    fn same_transform(self, other: Self) -> bool {
        // Match Studio's actual sameReparentFrame: with zero rotation the
        // central pivot cancels, so dimensions do not change the translation.
        // The zero-translation case also equals the synthetic root identity.
        self.x == other.x
            && self.y == other.y
            && self.rotation_deg == other.rotation_deg
            && (self.rotation_deg == 0.0
                || (self.width == other.width && self.height == other.height))
    }

    fn inverse(self, x: f64, y: f64) -> Result<(f64, f64)> {
        let center = finite_pair(self.x + self.width / 2.0, self.y + self.height / 2.0)?;
        let point = rotate_pair(x - center.0, y - center.1, -self.rotation_deg)?;
        finite_pair(point.0 + self.width / 2.0, point.1 + self.height / 2.0)
    }

    fn apply(self, x: f64, y: f64) -> Result<(f64, f64)> {
        let point = rotate_pair(
            x - self.width / 2.0,
            y - self.height / 2.0,
            self.rotation_deg,
        )?;
        let center = finite_pair(self.x + self.width / 2.0, self.y + self.height / 2.0)?;
        finite_pair(center.0 + point.0, center.1 + point.1)
    }
}

fn finite_pair(x: f64, y: f64) -> Result<(f64, f64)> {
    if !x.is_finite() || !y.is_finite() {
        return Err(DomainError::Invalid(
            "local reparent frame exceeds finite canvas coordinates".into(),
        ));
    }
    Ok((x, y))
}

fn rotate_pair(x: f64, y: f64, rotation_deg: f64) -> Result<(f64, f64)> {
    finite_pair(x, y)?;
    // Preserve the authored Studio helper's arithmetic order. libm/JavaScript
    // runtime equivalence still requires execution; this is no ACK tolerance.
    let radians = rotation_deg * (std::f64::consts::PI / 180.0);
    let cos = radians.cos();
    let sin = radians.sin();
    finite_pair(cos * x - sin * y, sin * x + cos * y)
}

fn require_local_frame(node: &CanvasNode) -> Result<()> {
    if node.coordinate_space != CoordinateSpace::ProjectPixels
        || node.keyframes.iter().any(|keyframe| {
            matches!(
                keyframe.property,
                MotionProperty::X
                    | MotionProperty::Y
                    | MotionProperty::Width
                    | MotionProperty::Height
                    | MotionProperty::RotationDeg
            )
        })
    {
        return Err(DomainError::Invalid(
            "local reparent requires project_pixels and no geometric motion on child or parents"
                .into(),
        ));
    }
    node_transform(node).validate()
}

fn reparent_transform(
    node: &CanvasNode,
    old_parent: Option<&CanvasNode>,
    new_parent: Option<&CanvasNode>,
    preserve: Option<ReparentCoordinatePolicy>,
) -> Result<CanvasTransform> {
    let original = node_transform(node);
    if preserve != Some(ReparentCoordinatePolicy::Local) {
        return Ok(original);
    }
    require_local_frame(node)?;
    for parent in [old_parent, new_parent].into_iter().flatten() {
        require_local_frame(parent)?;
    }
    let old_frame = ParentFrame::from_parent(old_parent);
    let new_frame = ParentFrame::from_parent(new_parent);
    // Equal frames are exactly the identity. Avoid numerical drift and false
    // property-lock failures without interpreting an ancestor or the camera.
    if old_frame.same_transform(new_frame) {
        return Ok(original);
    }
    let center = finite_pair(node.x + node.width / 2.0, node.y + node.height / 2.0)?;
    let local = if old_parent.is_some() {
        old_frame.inverse(center.0, center.1)?
    } else {
        center
    };
    let moved = if new_parent.is_some() {
        new_frame.apply(local.0, local.1)?
    } else {
        local
    };
    let result = CanvasTransform {
        x: moved.0 - node.width / 2.0,
        y: moved.1 - node.height / 2.0,
        rotation_deg: node.rotation_deg + (new_frame.rotation_deg - old_frame.rotation_deg),
        ..original
    };
    result.validate()?;
    Ok(result)
}

/// Prepare every hierarchy, coordinate and node-lock check before mutation.
/// The Project owns scene locks and its existing transactional validation.
pub(crate) fn prepare_canvas_reparent(
    scene: &Scene,
    node_id: Uuid,
    parent_id: Option<Uuid>,
    preserve: Option<ReparentCoordinatePolicy>,
) -> Result<(usize, CanvasTransform)> {
    if parent_id.is_some_and(|parent| {
        parent == node_id || !scene.nodes.iter().any(|node| node.id == parent)
    }) {
        return Err(DomainError::Invalid(
            "canvas parent must be another node in the scene".into(),
        ));
    }
    let index = scene
        .nodes
        .iter()
        .position(|node| node.id == node_id)
        .ok_or_else(|| DomainError::NotFound(format!("node:{node_id}")))?;
    let node = &scene.nodes[index];
    if property_locked(
        &node.property_locks,
        &[NodeProperty::Parent, NodeProperty::Order],
    ) {
        return Err(DomainError::Locked(format!("node:{node_id} hierarchy")));
    }
    let mut ancestor = parent_id;
    let mut visited = BTreeSet::new();
    for _ in 0..scene.nodes.len() {
        let Some(id) = ancestor else {
            break;
        };
        if id == node_id || !visited.insert(id) {
            return Err(DomainError::Invalid(
                "canvas hierarchy contains a cycle".into(),
            ));
        }
        ancestor = scene
            .nodes
            .iter()
            .find(|candidate| candidate.id == id)
            .ok_or_else(|| {
                DomainError::Invalid("canvas hierarchy contains an invalid parent".into())
            })?
            .parent_id;
    }
    if ancestor.is_some() {
        return Err(DomainError::Invalid(
            "canvas hierarchy exceeds bounded traversal".into(),
        ));
    }
    let old_parent = node
        .parent_id
        .map(|id| {
            scene
                .nodes
                .iter()
                .find(|candidate| candidate.id == id)
                .ok_or_else(|| {
                    DomainError::Invalid("canvas hierarchy contains an invalid parent".into())
                })
        })
        .transpose()?;
    let new_parent =
        parent_id.and_then(|id| scene.nodes.iter().find(|candidate| candidate.id == id));
    let transform = reparent_transform(node, old_parent, new_parent, preserve)?;
    if (transform.x != node.x || transform.y != node.y)
        && node.property_locks.contains(&NodeProperty::Position)
    {
        return Err(DomainError::Locked(format!("node:{node_id} position")));
    }
    if transform.rotation_deg != node.rotation_deg
        && node.property_locks.contains(&NodeProperty::Rotation)
    {
        return Err(DomainError::Locked(format!("node:{node_id} rotation")));
    }
    Ok((index, transform))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CameraState {
    pub center_x: f64,
    pub center_y: f64,
    pub zoom: f64,
    pub rotation_deg: f64,
    pub safe_margin: f64,
}

impl Default for CameraState {
    fn default() -> Self {
        Self {
            center_x: 960.0,
            center_y: 540.0,
            zoom: 1.0,
            rotation_deg: 0.0,
            safe_margin: 0.05,
        }
    }
}

impl CameraState {
    pub fn validate(&self) -> Result<()> {
        for value in [
            self.center_x,
            self.center_y,
            self.zoom,
            self.rotation_deg,
            self.safe_margin,
        ] {
            if !value.is_finite() {
                return Err(DomainError::Invalid(
                    "camera contains a non-finite number".into(),
                ));
            }
        }
        if !(0.01..=100.0).contains(&self.zoom) || !(0.0..=0.49).contains(&self.safe_margin) {
            return Err(DomainError::Invalid("camera is out of bounds".into()));
        }
        Ok(())
    }
}

pub fn property_locked(locks: &BTreeSet<NodeProperty>, properties: &[NodeProperty]) -> bool {
    properties.iter().any(|property| locks.contains(property))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Change, LockKind, Project, ProjectLock};

    #[test]
    fn transform_rejects_non_finite_values() {
        let transform = CanvasTransform {
            x: f64::NAN,
            y: 0.0,
            width: 1.0,
            height: 1.0,
            rotation_deg: 0.0,
            opacity: 1.0,
        };
        assert!(transform.validate().is_err());
    }

    #[test]
    fn property_lock_is_explicit() {
        let locks = BTreeSet::from([NodeProperty::Text]);
        assert!(property_locked(&locks, &[NodeProperty::Text]));
        assert!(!property_locked(&locks, &[NodeProperty::Position]));
    }

    fn node(name: &str, x: f64, y: f64, width: f64, height: f64) -> CanvasNode {
        CanvasNode {
            id: Uuid::now_v7(),
            name: name.into(),
            kind: "shape".into(),
            parent_id: None,
            x,
            y,
            width,
            height,
            rotation_deg: 0.0,
            opacity: 0.75,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle::default(),
            relations: Vec::new(),
            property_locks: BTreeSet::new(),
            keyframes: Vec::new(),
        }
    }

    fn fixture() -> (Project, Uuid, Uuid, Uuid, Uuid) {
        let mut project = Project::new("Explicit Canvas reparent source").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Canvas".into(),
                objective: "Preserve authored coordinates".into(),
                duration_seconds: 5,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        let old = node("Old parent", 100.0, 200.0, 80.0, 40.0);
        let new = node("New parent", 400.0, 100.0, 120.0, 60.0);
        let mut child = node("Child", 120.0, 230.0, 20.0, 10.0);
        child.parent_id = Some(old.id);
        child.rotation_deg = 15.0;
        let ids = (scene_id, old.id, new.id, child.id);
        for node in [old, new, child] {
            project
                .apply_change(&Change::AddCanvasNode { scene_id, node })
                .unwrap();
        }
        (project, ids.0, ids.1, ids.2, ids.3)
    }

    fn node_mut(project: &mut Project, id: Uuid) -> &mut CanvasNode {
        project.scenes[0]
            .nodes
            .iter_mut()
            .find(|node| node.id == id)
            .unwrap()
    }

    fn saved_node(project: &Project, id: Uuid) -> &CanvasNode {
        project.scenes[0]
            .nodes
            .iter()
            .find(|node| node.id == id)
            .unwrap()
    }

    fn change(
        scene_id: Uuid,
        node_id: Uuid,
        parent_id: Option<Uuid>,
        preserve: Option<ReparentCoordinatePolicy>,
    ) -> Change {
        Change::ReparentCanvasNode {
            scene_id,
            node_id,
            parent_id,
            z_index: 7,
            preserve,
        }
    }

    fn close(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() < 1.0e-10,
            "{actual} != {expected}"
        );
    }

    #[test]
    fn reparent_absent_wire_keeps_exact_legacy_bytes_and_present_null_is_rejected() {
        let scene_id = Uuid::from_u128(1);
        let node_id = Uuid::from_u128(2);
        let legacy = format!(
            "{{\"type\":\"reparent_canvas_node\",\"scene_id\":\"{scene_id}\",\"node_id\":\"{node_id}\",\"parent_id\":null,\"z_index\":7}}"
        );
        let decoded: Change = serde_json::from_str(&legacy).unwrap();
        assert_eq!(decoded, change(scene_id, node_id, None, None));
        assert_eq!(serde_json::to_string(&decoded).unwrap(), legacy);
        for malformed in [
            serde_json::Value::Null,
            serde_json::json!("camera"),
            serde_json::json!(false),
            serde_json::json!({"kind": "local"}),
            serde_json::json!({"world": null}),
            serde_json::json!({"local": null}),
            serde_json::json!([]),
        ] {
            let mut wire: serde_json::Value = serde_json::from_str(&legacy).unwrap();
            wire["preserve"] = malformed;
            assert!(serde_json::from_value::<Change>(wire).is_err());
        }
        for policy in [
            ReparentCoordinatePolicy::World,
            ReparentCoordinatePolicy::Local,
        ] {
            let explicit = change(scene_id, node_id, None, Some(policy));
            let wire = serde_json::to_value(&explicit).unwrap();
            assert!(wire["preserve"].is_string());
            assert_eq!(serde_json::from_value::<Change>(wire).unwrap(), explicit);
        }
    }

    #[test]
    fn historical_and_world_reparent_preserve_exact_pose_spaces_and_channels() {
        let (mut project, scene_id, old_id, new_id, child_id) = fixture();
        for id in [old_id, new_id, child_id] {
            let node = node_mut(&mut project, id);
            node.coordinate_space = CoordinateSpace::SceneLocal;
            node.keyframes.push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::X,
                value: 12.5,
                interpolation: MotionInterpolation::Linear,
            });
        }
        node_mut(&mut project, child_id).property_locks =
            BTreeSet::from([NodeProperty::Position, NodeProperty::Rotation]);
        project.validate().unwrap();
        let original = saved_node(&project, child_id).clone();
        for preserve in [None, Some(ReparentCoordinatePolicy::World)] {
            let mut candidate = project.clone();
            candidate
                .apply_change(&change(scene_id, child_id, Some(new_id), preserve))
                .unwrap();
            let mut expected = original.clone();
            expected.parent_id = Some(new_id);
            expected.z_index = 7;
            assert_eq!(saved_node(&candidate, child_id), &expected);
        }
    }

    #[test]
    fn local_reparent_uses_child_center_and_both_central_pivot_rotations() {
        let (mut project, scene_id, old_id, new_id, child_id) = fixture();
        node_mut(&mut project, old_id).rotation_deg = 90.0;
        node_mut(&mut project, new_id).rotation_deg = -90.0;
        let grandparent = node("Uncomposed ancestor", 700.0, 900.0, 600.0, 400.0);
        let grandparent_id = grandparent.id;
        project.scenes[0].nodes.push(grandparent);
        node_mut(&mut project, old_id).parent_id = Some(grandparent_id);
        node_mut(&mut project, new_id).parent_id = Some(grandparent_id);
        node_mut(&mut project, grandparent_id).rotation_deg = 73.0;
        project.scenes[0].camera.rotation_deg = 34.0;
        project.scenes[0].camera.zoom = 3.0;
        project.validate().unwrap();
        let original = saved_node(&project, child_id).clone();
        project
            .apply_change(&change(
                scene_id,
                child_id,
                Some(new_id),
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        let child = saved_node(&project, child_id);
        close(child.x, 450.0);
        close(child.y, 130.0);
        close(child.rotation_deg, -165.0);
        assert_eq!(
            (child.width, child.height, child.opacity),
            (20.0, 10.0, 0.75)
        );
        assert_eq!(child.keyframes, original.keyframes);
        assert_eq!(child.parent_id, Some(new_id));
        assert_eq!(child.z_index, 7);
    }

    #[test]
    fn local_root_is_identity_and_same_frame_is_exact_even_with_pose_locks() {
        let (mut project, scene_id, old_id, _, child_id) = fixture();
        let original = saved_node(&project, child_id).clone();
        node_mut(&mut project, child_id).property_locks =
            BTreeSet::from([NodeProperty::Position, NodeProperty::Rotation]);
        let locked_pose = node_transform(saved_node(&project, child_id));
        project
            .apply_change(&change(
                scene_id,
                child_id,
                Some(old_id),
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        assert_eq!(node_transform(saved_node(&project, child_id)), locked_pose);
        node_mut(&mut project, child_id).property_locks.clear();
        project
            .apply_change(&change(
                scene_id,
                child_id,
                None,
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        let child = saved_node(&project, child_id);
        assert_eq!((child.x, child.y, child.rotation_deg), (20.0, 30.0, 15.0));
        project
            .apply_change(&change(
                scene_id,
                child_id,
                Some(old_id),
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        assert_eq!(
            node_transform(saved_node(&project, child_id)),
            node_transform(&original)
        );

        // Studio treats equal unrotated translations as the same rigid frame,
        // even when their pivot sizes differ enough to lose precision if used.
        let (mut equal, scene_id, old_id, new_id, child_id) = fixture();
        let expected = node_transform(saved_node(&equal, child_id));
        let old = saved_node(&equal, old_id).clone();
        let new = node_mut(&mut equal, new_id);
        new.x = old.x;
        new.y = old.y;
        new.width = f64::MAX;
        new.height = f64::MAX;
        node_mut(&mut equal, child_id).property_locks =
            BTreeSet::from([NodeProperty::Position, NodeProperty::Rotation]);
        equal.validate().unwrap();
        equal
            .apply_change(&change(
                scene_id,
                child_id,
                Some(new_id),
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        assert_eq!(node_transform(saved_node(&equal, child_id)), expected);

        // A direct parent at zero translation/rotation equals root regardless
        // of its nonzero dimensions; exact locked coordinates must stay exact.
        node_mut(&mut equal, new_id).x = 0.0;
        node_mut(&mut equal, new_id).y = 0.0;
        equal
            .apply_change(&change(
                scene_id,
                child_id,
                None,
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        assert_eq!(node_transform(saved_node(&equal, child_id)), expected);
    }

    #[test]
    fn local_reparent_rejects_each_uninterpreted_space_and_geometric_motion_without_mutation() {
        let (project, scene_id, old_id, new_id, child_id) = fixture();
        for id in [child_id, old_id, new_id] {
            for coordinate_space in [CoordinateSpace::Normalized, CoordinateSpace::SceneLocal] {
                let mut candidate = project.clone();
                node_mut(&mut candidate, id).coordinate_space = coordinate_space;
                candidate.validate().unwrap();
                let before = candidate.clone();
                assert!(
                    candidate
                        .apply_change(&change(
                            scene_id,
                            child_id,
                            Some(new_id),
                            Some(ReparentCoordinatePolicy::Local)
                        ))
                        .is_err()
                );
                assert_eq!(candidate, before);
            }
            for property in [
                MotionProperty::X,
                MotionProperty::Y,
                MotionProperty::Width,
                MotionProperty::Height,
                MotionProperty::RotationDeg,
            ] {
                let mut candidate = project.clone();
                node_mut(&mut candidate, id).keyframes.push(CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property,
                    value: 12.0,
                    interpolation: MotionInterpolation::Hold,
                });
                candidate.validate().unwrap();
                let before = candidate.clone();
                assert!(
                    candidate
                        .apply_change(&change(
                            scene_id,
                            child_id,
                            Some(new_id),
                            Some(ReparentCoordinatePolicy::Local)
                        ))
                        .is_err()
                );
                assert_eq!(candidate, before);
            }
        }
        let mut opacity_only = project;
        for id in [child_id, old_id, new_id] {
            node_mut(&mut opacity_only, id)
                .keyframes
                .push(CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property: MotionProperty::Opacity,
                    value: 0.5,
                    interpolation: MotionInterpolation::EaseInOut,
                });
        }
        let before = opacity_only.scenes[0].nodes.clone();
        opacity_only
            .apply_change(&change(
                scene_id,
                child_id,
                Some(new_id),
                Some(ReparentCoordinatePolicy::Local),
            ))
            .unwrap();
        for original in before {
            assert_eq!(
                saved_node(&opacity_only, original.id).keyframes,
                original.keyframes
            );
        }
    }

    #[test]
    fn local_pose_locks_only_reject_a_property_that_actually_changes() {
        let (project, scene_id, old_id, new_id, child_id) = fixture();
        let operation = change(
            scene_id,
            child_id,
            Some(new_id),
            Some(ReparentCoordinatePolicy::Local),
        );
        let mut position_locked = project.clone();
        node_mut(&mut position_locked, child_id)
            .property_locks
            .insert(NodeProperty::Position);
        let before = position_locked.clone();
        assert!(matches!(
            position_locked.apply_change(&operation),
            Err(DomainError::Locked(_))
        ));
        assert_eq!(position_locked, before);
        let mut rotation_locked = project.clone();
        node_mut(&mut rotation_locked, child_id)
            .property_locks
            .insert(NodeProperty::Rotation);
        rotation_locked.apply_change(&operation).unwrap();
        assert_eq!(saved_node(&rotation_locked, child_id).rotation_deg, 15.0);
        let mut rotation_changed = project.clone();
        node_mut(&mut rotation_changed, new_id).rotation_deg = 90.0;
        node_mut(&mut rotation_changed, child_id)
            .property_locks
            .insert(NodeProperty::Rotation);
        let before = rotation_changed.clone();
        assert!(matches!(
            rotation_changed.apply_change(&operation),
            Err(DomainError::Locked(_))
        ));
        assert_eq!(rotation_changed, before);
        let mut rotation_only = project;
        for id in [old_id, new_id] {
            let parent = node_mut(&mut rotation_only, id);
            parent.x = 0.0;
            parent.y = 0.0;
            parent.width = 0.0;
            parent.height = 0.0;
        }
        node_mut(&mut rotation_only, new_id).rotation_deg = 90.0;
        let child = node_mut(&mut rotation_only, child_id);
        child.x = -10.0;
        child.y = -5.0;
        child.property_locks.insert(NodeProperty::Position);
        rotation_only.apply_change(&operation).unwrap();
        let child = saved_node(&rotation_only, child_id);
        assert_eq!((child.x, child.y, child.rotation_deg), (-10.0, -5.0, 105.0));
    }

    #[test]
    fn reparent_keeps_parent_order_scene_locks_and_cycles_atomic() {
        let (project, scene_id, old_id, new_id, child_id) = fixture();
        for preserve in [
            None,
            Some(ReparentCoordinatePolicy::World),
            Some(ReparentCoordinatePolicy::Local),
        ] {
            for property in [NodeProperty::Parent, NodeProperty::Order] {
                let mut candidate = project.clone();
                node_mut(&mut candidate, child_id)
                    .property_locks
                    .insert(property);
                let before = candidate.clone();
                assert!(matches!(
                    candidate.apply_change(&change(scene_id, child_id, Some(new_id), preserve)),
                    Err(DomainError::Locked(_))
                ));
                assert_eq!(candidate, before);
            }
            for kind in [LockKind::Content, LockKind::Position] {
                let mut candidate = project.clone();
                candidate.locks.push(ProjectLock {
                    id: Uuid::now_v7(),
                    resource: format!("scene:{scene_id}"),
                    kind,
                    note: "Existing scene lock".into(),
                });
                let before = candidate.clone();
                assert!(matches!(
                    candidate.apply_change(&change(scene_id, child_id, Some(new_id), preserve)),
                    Err(DomainError::Locked(_))
                ));
                assert_eq!(candidate, before);
            }
            for (node_id, parent_id) in [
                (old_id, Some(child_id)),
                (child_id, Some(child_id)),
                (child_id, Some(Uuid::now_v7())),
            ] {
                let mut candidate = project.clone();
                assert!(
                    candidate
                        .apply_change(&change(scene_id, node_id, parent_id, preserve))
                        .is_err()
                );
                assert_eq!(candidate, project);
            }
        }
    }

    #[test]
    fn local_intermediate_overflow_rejects_without_clamp_or_partial_hierarchy_change() {
        let (mut project, scene_id, _, new_id, child_id) = fixture();
        let parent = node_mut(&mut project, new_id);
        parent.x = f64::MAX;
        parent.width = f64::MAX;
        project.validate().unwrap();
        let before = project.clone();
        assert!(
            project
                .apply_change(&change(
                    scene_id,
                    child_id,
                    Some(new_id),
                    Some(ReparentCoordinatePolicy::Local)
                ))
                .is_err()
        );
        assert_eq!(project, before);
    }
}
