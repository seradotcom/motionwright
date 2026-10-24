//! Bounded static size influence for the existing editable procedural generator.
//! No runtime randomness, timeline synthesis or additional rendering capability.
use crate::{DomainError, ProceduralConfig, Result};
use serde::{Deserialize, Deserializer, Serialize};

pub const PROCEDURAL_INFLUENCE_VERSION: u32 = 2;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProceduralInfluence {
    pub center_x: u16,
    pub center_y: u16,
    pub radius: u16,
    pub target_size: u16,
}

/// Missing means legacy behavior. An explicit null or partial object is invalid.
pub(crate) fn deserialize_optional_influence<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<ProceduralInfluence>, D::Error>
where
    D: Deserializer<'de>,
{
    ProceduralInfluence::deserialize(deserializer).map(Some)
}

impl ProceduralInfluence {
    pub fn validate(&self, config: &ProceduralConfig) -> Result<()> {
        if self.center_x < config.origin_x
            || u32::from(self.center_x) > u32::from(config.origin_x) + u32::from(config.area_width)
            || self.center_y < config.origin_y
            || u32::from(self.center_y) > u32::from(config.origin_y) + u32::from(config.area_height)
        {
            return Err(DomainError::Invalid(
                "influence center must be inside the authored field".into(),
            ));
        }
        if self.radius == 0 || self.radius > 1920 {
            return Err(DomainError::Invalid(
                "influence radius must be between 1 and 1920 pixels".into(),
            ));
        }
        if self.target_size < 4 || self.target_size > 128 {
            return Err(DomainError::Invalid(
                "influence target size must be between 4 and 128 pixels".into(),
            ));
        }
        Ok(())
    }
}

pub fn procedural_generator_version(config: &ProceduralConfig) -> u32 {
    if config.influence.is_some() {
        PROCEDURAL_INFLUENCE_VERSION
    } else {
        crate::PROCEDURAL_VERSION
    }
}

/// All distance and interpolation arithmetic uses bounded integers. Coordinates
/// become exact halves only after preserving the original item's center.
pub(crate) fn procedural_influenced_geometry(
    x: u32,
    y: u32,
    config: &ProceduralConfig,
) -> Result<(f64, f64, u32)> {
    let Some(influence) = &config.influence else {
        return Ok((f64::from(x), f64::from(y), u32::from(config.size)));
    };
    let dx = 2 * i64::from(x) + i64::from(config.size) - 2 * i64::from(influence.center_x);
    let dy = 2 * i64::from(y) + i64::from(config.size) - 2 * i64::from(influence.center_y);
    let radius_squared = (2 * i64::from(influence.radius)).pow(2);
    let distance_squared = dx * dx + dy * dy;
    if distance_squared >= radius_squared {
        return Ok((f64::from(x), f64::from(y), u32::from(config.size)));
    }
    let size = i64::from(config.size)
        + (i64::from(influence.target_size) - i64::from(config.size))
            * (radius_squared - distance_squared)
            / radius_squared;
    let left2 = 2 * i64::from(x) + i64::from(config.size) - size;
    let top2 = 2 * i64::from(y) + i64::from(config.size) - size;
    if left2 < 2 * i64::from(config.origin_x)
        || top2 < 2 * i64::from(config.origin_y)
        || left2 + 2 * size > 2 * (i64::from(config.origin_x) + i64::from(config.area_width))
        || top2 + 2 * size > 2 * (i64::from(config.origin_y) + i64::from(config.area_height))
    {
        return Err(DomainError::Invalid(
            "influenced object exceeds the authored field; reduce target size or radius, or move the influence center"
                .into(),
        ));
    }
    Ok((left2 as f64 / 2.0, top2 as f64 / 2.0, size as u32))
}
