//! Read-only interpretation of saved Canvas opacity keys. This is not a
//! second animation document; Native, camera admission and UI retain keys.
use crate::{
    CanvasNode, DomainError, MotionInterpolation, MotionProperty, RationalTime, Result, Scene,
};

pub const MAX_NATIVE_OPACITY_KEYS: usize = 8;

#[derive(Debug, Clone)]
pub struct CanvasOpacitySpan {
    pub start: RationalTime,
    pub end: RationalTime,
    pub from: f64,
    pub to: f64,
    pub curve: MotionInterpolation,
}

/// Exact binary-opacity intervals use existing FadeIn/FadeOut primitives.
/// Repeated equal endpoints hold their literal value without a Native write.
/// Hold jumps and partial-alpha fades need a different renderer contract.
pub fn exact_canvas_opacity_spans(
    node: &CanvasNode,
    scene: &Scene,
    fps: RationalTime,
) -> Result<Option<Vec<CanvasOpacitySpan>>> {
    let mut keys = node
        .keyframes
        .iter()
        .filter(|key| key.property == MotionProperty::Opacity)
        .collect::<Vec<_>>();
    if keys.is_empty() {
        return Ok(None);
    }
    let fail = || {
        DomainError::Invalid(format!(
            "Canvas node {} requires 2 to 8 exact frame-aligned binary-opacity keys; partial alpha and held jumps are unsupported",
            node.id
        ))
    };
    if !(2..=MAX_NATIVE_OPACITY_KEYS).contains(&keys.len())
        || node.opacity != 1.0
        || fps.validate().is_err()
        || fps.num <= 0
        || fps.den <= 0
    {
        return Err(fail());
    }
    for key in &keys {
        key.validate()?;
    }
    keys.sort_by_key(|key| key.at);
    if keys[0].at != RationalTime::ZERO || keys[0].interpolation != MotionInterpolation::Linear {
        return Err(fail());
    }
    for (index, key) in keys.iter().enumerate() {
        let numerator = i128::from(key.at.num) * i128::from(fps.num);
        let denominator = i128::from(key.at.den) * i128::from(fps.den);
        if key.at >= scene.duration
            || denominator <= 0
            || numerator < 0
            || numerator % denominator != 0
            || numerator / denominator > 36000
            || (key.value != 0.0 && key.value != 1.0)
            || (index > 0 && key.at <= keys[index - 1].at)
            || !matches!(
                key.interpolation,
                MotionInterpolation::Linear
                    | MotionInterpolation::EaseOutCubic
                    | MotionInterpolation::Hold
            )
        {
            return Err(fail());
        }
    }
    let mut spans = Vec::new();
    for pair in keys.windows(2) {
        let (from, to) = (pair[0], pair[1]);
        if from.value == to.value {
            continue;
        }
        if to.interpolation == MotionInterpolation::Hold {
            return Err(fail());
        }
        spans.push(CanvasOpacitySpan {
            start: from.at,
            end: to.at,
            from: from.value,
            to: to.value,
            curve: to.interpolation,
        });
    }
    Ok(Some(spans))
}
