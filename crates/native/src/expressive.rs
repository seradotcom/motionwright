//! Lossless admission of a deliberately bounded editable entrance grammar.
//! Unsupported keyframes remain errors; this is not a universal keyframe translator.
use motionwright_domain::{
    CanvasNode, DeliverableProfile, DomainError, FramingStrategy, MotionInterpolation,
    MotionProperty, Project, RationalTime, Scene, merge_component_nodes, realize_product_hero,
};
use semwright_native_sdk::{Error, ErrorCode, Result};

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct EntryMotion {
    pub start: RationalTime,
    pub end: RationalTime,
    pub offset_y: f64,
    pub rotation_deg: f64,
}
fn unsupported(detail: &str) -> Error {
    Error::new(
        ErrorCode::Unsupported,
        format!(
            "Authored motion keyframes cannot be preserved by the admitted entrance grammar: {detail}"
        ),
    )
}

pub(crate) fn entry_motion(node: &CanvasNode) -> Result<Option<EntryMotion>> {
    if node.keyframes.is_empty() {
        return Ok(None);
    }
    if node.parent_id.is_some() || node.rotation_deg != 0.0 || node.opacity != 1.0 {
        return Err(unsupported(
            "parented motion or non-neutral final rotation/opacity",
        ));
    }
    if node.keyframes.iter().any(|key| {
        !matches!(
            key.property,
            MotionProperty::Y | MotionProperty::Opacity | MotionProperty::RotationDeg
        )
    }) {
        return Err(unsupported(
            "only synchronized Y/opacity and optional rotation entrances are admitted",
        ));
    }
    let property = |kind| {
        let mut keys = node
            .keyframes
            .iter()
            .filter(|key| key.property == kind)
            .collect::<Vec<_>>();
        keys.sort_by_key(|key| key.at);
        keys
    };
    let y = property(MotionProperty::Y);
    let opacity = property(MotionProperty::Opacity);
    let rotation = property(MotionProperty::RotationDeg);
    if !(2..=3).contains(&y.len())
        || opacity.len() != y.len()
        || (!rotation.is_empty() && rotation.len() != y.len())
    {
        return Err(unsupported(
            "entrance needs a zero-time start, optional hold and one ending key on each channel",
        ));
    }
    let end = y[y.len() - 1].at;
    let start = y[y.len() - 2].at;
    if y[0].at != RationalTime::ZERO || start >= end {
        return Err(unsupported("invalid entrance interval"));
    }
    for keys in [&y, &opacity]
        .into_iter()
        .chain((!rotation.is_empty()).then_some(&rotation))
    {
        for (index, key) in keys.iter().enumerate() {
            if key.at != y[index].at
                || !key.value.is_finite()
                || (index + 1 == keys.len()
                    && key.interpolation != MotionInterpolation::EaseOutCubic)
                || (index + 1 < keys.len()
                    && (key.interpolation != MotionInterpolation::Hold
                        || key.value != keys[0].value))
            {
                return Err(unsupported(
                    "channels are not synchronized hold/out-cubic keys",
                ));
            }
        }
    }
    if y[y.len() - 1].value != node.y
        || opacity[0].value != 0.0
        || opacity[opacity.len() - 1].value != 1.0
        || (!rotation.is_empty() && rotation[rotation.len() - 1].value != 0.0)
    {
        return Err(unsupported(
            "entrance terminal values differ from the persistent node layout",
        ));
    }
    Ok(Some(EntryMotion {
        start,
        end,
        offset_y: y[0].value - node.y,
        rotation_deg: rotation.first().map_or(0.0, |key| key.value),
    }))
}

fn domain(error: DomainError) -> Error {
    Error::new(ErrorCode::Unsupported, error.to_string())
}

/// Reflow generated fields only. Human overrides merge against the saved baseline;
/// an ambiguous position/copy change blocks this variant instead of overwriting it.
pub(crate) fn responsive_scene(
    project: &Project,
    scene: &Scene,
    profile: &DeliverableProfile,
) -> Result<Scene> {
    let mut output = scene.clone();
    if profile.framing_strategy != FramingStrategy::Replan {
        return Ok(output);
    }
    let Some(hero) = project
        .production_design
        .heroes
        .iter()
        .find(|hero| hero.scene_id == scene.id)
    else {
        return Ok(output);
    };
    if profile.width == 1920 && profile.height == 1080 {
        return Ok(output);
    }
    let mut incoming = realize_product_hero(hero.id, &hero.config, profile.width, profile.height)
        .map_err(domain)?;
    let sx = f64::from(profile.width) / 1920.0;
    let sy = f64::from(profile.height) / 1080.0;
    let size_scale = sx.min(sy);
    // Invert the existing Film projection, so its transform stays authoritative.
    for node in &mut incoming {
        let destination_y = node.y;
        let center_x = node.x + node.width / 2.0;
        let center_y = node.y + node.height / 2.0;
        node.width /= size_scale;
        node.height /= size_scale;
        node.x = center_x / sx - node.width / 2.0;
        node.y = center_y / sy - node.height / 2.0;
        if let Some(size) = &mut node.style.font_size {
            *size /= size_scale;
        }
        node.style.stroke_width /= size_scale;
        for key in &mut node.keyframes {
            if key.property == MotionProperty::Y {
                key.value = node.y + (key.value - destination_y) / sy;
            }
        }
    }
    output.nodes =
        merge_component_nodes(&hero.baseline, &scene.nodes, &incoming).map_err(domain)?;
    Ok(output)
}
