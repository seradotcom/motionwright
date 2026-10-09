use crate::{DomainError, RationalTime, Result};
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
}
