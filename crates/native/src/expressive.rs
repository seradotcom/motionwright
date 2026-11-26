//! Lossless admission of a deliberately bounded editable entrance grammar.
//! Unsupported keyframes remain errors; this is not a universal keyframe translator.
use motionwright_domain::{
    CanvasNode, DeliverableProfile, DomainError, FieldDistribution, FramingStrategy, HeroLayout,
    MotionInterpolation, MotionProperty, Project, RationalTime, Scene, hero_node_id,
    merge_component_nodes, realize_procedural_field_for_output, realize_product_hero,
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
    if node.parent_id.is_some()
        || (node.rotation_deg != 0.0 && !motionwright_domain::native_constant_shape_rotation(node))
        || node.opacity != 1.0
    {
        return Err(unsupported(
            "parented motion, unsupported orientation or non-neutral final opacity",
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

/// Layout-only line breaks may replace spaces, never other authored characters.
fn same_variant_copy(raw: &str, wrapped: &str) -> bool {
    !wrapped.split('\n').any(|line| line.trim().is_empty())
        && raw.replace('\n', " ") == wrapped.replace('\n', " ")
}

fn fit_variant_hero_text(node: &mut CanvasNode, layout: HeroLayout, headline: bool) -> Result<()> {
    let fail = || {
        domain(DomainError::Invalid(format!(
            "Delivery Hero {} copy exceeds its responsive text budget; author a shorter variant or intentional line break",
            node.name
        )))
    };
    let text = node.text.as_deref().ok_or_else(fail)?;
    let font = node.style.font_size.ok_or_else(fail)?;
    let advance = font * node.style.line_height.unwrap_or(1.12);
    if ![font, advance, node.width, node.height]
        .iter()
        .all(|v| v.is_finite() && *v > 0.0)
    {
        return Err(fail());
    }
    // Maintain the generator's semantic line ceilings. Further split its
    // lines only when the actual retained font/box requires it.
    let role_limit = if headline {
        if layout == HeroLayout::EditorialQuote {
            4
        } else {
            3
        }
    } else if matches!(
        layout,
        HeroLayout::TypographyFocus | HeroLayout::EditorialQuote
    ) {
        3
    } else {
        4
    };
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        let mut line = String::new();
        for word in paragraph.split(' ') {
            let count = word.chars().count();
            if count == 0 || count as f64 * font * 0.56 > node.width * 0.96 {
                return Err(fail());
            }
            if !line.is_empty()
                && (line.chars().count() + 1 + count) as f64 * font * 0.56 > node.width * 0.96
            {
                lines.push(std::mem::take(&mut line));
            }
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(word);
        }
        lines.push(line);
    }
    if lines.is_empty()
        || lines.len() > role_limit
        || advance * (lines.len() - 1) as f64 + font * 1.4 > node.height
    {
        return Err(fail());
    }
    let fitted = lines.join("\n");
    if !same_variant_copy(text, &fitted) {
        return Err(fail());
    }
    node.text = Some(fitted);
    Ok(())
}

/// Reflow generated fields only. Human overrides merge against the saved baseline;
/// an ambiguous position/copy change blocks this variant instead of overwriting it.
pub(crate) fn responsive_scene(
    project: &Project,
    scene: &Scene,
    profile: &DeliverableProfile,
) -> Result<Scene> {
    motionwright_domain::validate_delivery_text_sources_for_scene(project, scene, profile)
        .map_err(domain)?;
    let mut output = scene.clone();
    let replan = profile.framing_strategy == FramingStrategy::Replan;
    let resized = replan && (profile.width != 1920 || profile.height != 1080);
    let mut wrapped_overrides = std::collections::BTreeMap::new();
    if replan
        && let Some(hero) = project
            .production_design
            .heroes
            .iter()
            .find(|hero| hero.scene_id == scene.id)
    {
        let mut config = hero.config.clone();
        let mut selected = std::collections::BTreeSet::new();
        // Free editorial copy uses the aspect grammar in all eight families.
        // Data-bound values, protected disclosures and the source step index
        // are literal; a responsive generator must not replace those values.
        for (role, copy) in [
            ("headline", &mut config.headline),
            ("body", &mut config.body),
        ] {
            let id = hero_node_id(hero.id, role);
            if let Some((literal, _)) =
                motionwright_domain::protected_delivery_text(project, scene, id)
            {
                *copy = literal.to_owned();
            } else if let Some(raw) = profile.text_overrides.get(&id) {
                *copy = raw.clone();
                selected.insert(id);
            }
        }
        if resized || !selected.is_empty() {
            let mut incoming =
                realize_product_hero(hero.id, &config, profile.width, profile.height)
                    .map_err(domain)?;
            for node in &mut incoming {
                if motionwright_domain::protected_delivery_text(project, scene, node.id).is_some() {
                    // Preserve current source text through the field-level merge,
                    // even if a regenerated layout would wrap the old baseline.
                    node.text = hero
                        .baseline
                        .iter()
                        .find(|base| base.id == node.id)
                        .ok_or_else(|| {
                            domain(DomainError::Invalid(
                                "Protected Hero baseline identity missing".into(),
                            ))
                        })?
                        .text
                        .clone();
                } else if selected.contains(&node.id) {
                    let raw = &profile.text_overrides[&node.id];
                    let wrapped = node.text.as_ref().filter(|text| same_variant_copy(raw, text))
                    .ok_or_else(|| domain(DomainError::Invalid("Delivery Hero copy cannot be wrapped without altering authored characters or whitespace".into())))?;
                    wrapped_overrides.insert(
                        node.id,
                        (
                            wrapped.clone(),
                            config.layout,
                            node.id == hero_node_id(hero.id, "headline"),
                        ),
                    );
                    // Explicit local text has precedence over the source text only.
                    // Geometry, style and locks still use the three-way merge.
                    node.text = hero
                        .baseline
                        .iter()
                        .find(|base| base.id == node.id)
                        .ok_or_else(|| {
                            domain(DomainError::Invalid(
                                "Delivery Hero baseline identity missing".into(),
                            ))
                        })?
                        .text
                        .clone();
                }
            }
            let sx = f64::from(profile.width) / 1920.0;
            let sy = f64::from(profile.height) / 1080.0;
            let size_scale = sx.min(sy);
            // Invert the existing Film projection, so its transform stays authoritative.
            for node in &mut incoming {
                if !resized {
                    continue;
                }
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
                merge_component_nodes(&hero.baseline, &output.nodes, &incoming).map_err(domain)?;
        }
    }
    if resized
        && let Some(field) = project
            .production_design
            .procedural_fields
            .iter()
            .find(|field| {
                field.scene_id == scene.id
                    && field.config.distribution == FieldDistribution::AdaptiveGrid
            })
    {
        let incoming = realize_procedural_field_for_output(
            field.id,
            &field.config,
            profile.width,
            profile.height,
        )
        .map_err(domain)?;
        output.nodes =
            merge_component_nodes(&field.baseline, &output.nodes, &incoming).map_err(domain)?;
    }
    if let Some(board) = project
        .production_design
        .capture_boards
        .iter()
        .find(|board| board.scene_id == scene.id)
    {
        board.validate_scene(project, scene).map_err(domain)?;
        if resized {
            let mut incoming = board
                .realize(project, profile.width, profile.height)
                .map_err(domain)?;
            let sx = f64::from(profile.width) / 1920.0;
            let sy = f64::from(profile.height) / 1080.0;
            let scale = sx.min(sy);
            for node in &mut incoming {
                let cx = node.x + node.width / 2.0;
                let cy = node.y + node.height / 2.0;
                node.width /= scale;
                node.height /= scale;
                node.x = cx / sx - node.width / 2.0;
                node.y = cy / sy - node.height / 2.0;
                if let Some(font) = &mut node.style.font_size {
                    *font /= scale;
                }
                node.property_locks = scene
                    .nodes
                    .iter()
                    .find(|source| source.id == node.id)
                    .expect("validated original board identity")
                    .property_locks
                    .clone();
            }
            output.nodes = incoming;
        }
    }
    // Resolve effective copy once. Do not reapply the unwrapped delivery map.
    for node in &mut output.nodes {
        if let Some((copy, layout, headline)) = wrapped_overrides.get(&node.id) {
            node.text = Some(copy.clone());
            fit_variant_hero_text(node, *layout, *headline)?;
        } else if let Some(copy) = profile.text_overrides.get(&node.id)
            && node.kind == "text"
        {
            node.text = Some(copy.clone());
        }
        if let Some((literal, reason)) =
            motionwright_domain::protected_delivery_text(project, scene, node.id)
            && node.text.as_deref() != Some(literal)
        {
            return Err(domain(DomainError::Invalid(format!(
                "{reason}: {}",
                node.name
            ))));
        }
    }
    Ok(output)
}
