//! First-party component realization. This is application data, not executable source.
use crate::*;
use sha2::{Digest, Sha256};

pub const HERO_VERSION: u32 = 1;
pub const HERO_ROLES: [&str; 6] = [
    "eyebrow",
    "headline",
    "body",
    "wordmark",
    "rule",
    "disclosure",
];

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HeroConfig {
    pub eyebrow: String,
    pub headline: String,
    pub body: String,
    pub wordmark: String,
    pub foreground: String,
    pub accent: String,
    pub motion: bool,
}
impl Default for HeroConfig {
    fn default() -> Self {
        Self {
            eyebrow: "MOTIONWRIGHT / CREATIVE PRODUCTION".into(),
            headline: "Make the work.\nKeep the craft.".into(),
            body: "A composition you can direct, revise and keep editing.".into(),
            wordmark: "Mw".into(),
            foreground: "#F2F4F3".into(),
            accent: "#A5C8DF".into(),
            motion: true,
        }
    }
}
impl HeroConfig {
    pub fn validate(&self) -> Result<()> {
        for (label, text, max) in [
            ("eyebrow", &self.eyebrow, 48),
            ("headline", &self.headline, 64),
            ("body", &self.body, 150),
            ("wordmark", &self.wordmark, 8),
        ] {
            if text.trim().is_empty()
                || text.chars().count() > max
                || text.chars().any(|c| c.is_control() && c != '\n')
            {
                return Err(DomainError::Invalid(format!(
                    "hero {label}: empty, control character or layout budget exceeded"
                )));
            }
        }
        for color in [&self.foreground, &self.accent] {
            if color.len() != 7
                || !color.starts_with('#')
                || !color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
            {
                return Err(DomainError::Invalid("hero colors must be #RRGGBB".into()));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductHeroInstance {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub component_version: u32,
    pub config: HeroConfig,
    /// Previous generated values are the merge base; human values live on the scene nodes.
    pub baseline: Vec<CanvasNode>,
}

pub fn hero_node_id(instance: Uuid, role: &str) -> Uuid {
    let mut hash = Sha256::new();
    hash.update(b"motionwright.product-hero.v1\0");
    hash.update(instance.as_bytes());
    hash.update(role.as_bytes());
    let bytes: [u8; 32] = hash.finalize().into();
    let mut id = [0; 16];
    id.copy_from_slice(&bytes[..16]);
    // RFC variant + name-derived version; IDs are not random execution authority.
    id[6] = (id[6] & 0x0f) | 0x80;
    id[8] = (id[8] & 0x3f) | 0x80;
    Uuid::from_bytes(id)
}

fn wrap_copy(text: &str, columns: usize, max_lines: usize) -> Result<String> {
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        let mut line = String::new();
        for word in paragraph.split_whitespace() {
            if word.chars().count() > columns {
                return Err(DomainError::Invalid(
                    "hero copy contains a word wider than the layout; insert an intentional break"
                        .into(),
                ));
            }
            if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > columns {
                lines.push(std::mem::take(&mut line));
            }
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(word);
        }
        lines.push(line);
    }
    if lines.len() > max_lines {
        return Err(DomainError::Invalid(
            "hero copy exceeds this aspect's line budget; shorten or author another variant".into(),
        ));
    }
    Ok(lines.join("\n"))
}

fn entry_keys(
    node: &CanvasNode,
    start_ms: i64,
    end_ms: i64,
    offset: f64,
    rotation: f64,
) -> Vec<CanvasKeyframe> {
    let mut keys = Vec::new();
    for (property, initial, terminal) in [
        (MotionProperty::Y, node.y + offset, node.y),
        (MotionProperty::Opacity, 0.0, 1.0),
        (MotionProperty::RotationDeg, rotation, 0.0),
    ] {
        if property == MotionProperty::RotationDeg && rotation == 0.0 {
            continue;
        }
        keys.push(CanvasKeyframe {
            at: RationalTime::ZERO,
            property,
            value: initial,
            interpolation: MotionInterpolation::Hold,
        });
        if start_ms > 0 {
            keys.push(CanvasKeyframe {
                at: RationalTime::new(start_ms, 1000).expect("bounded milliseconds"),
                property,
                value: initial,
                interpolation: MotionInterpolation::Hold,
            });
        }
        keys.push(CanvasKeyframe {
            at: RationalTime::new(end_ms, 1000).expect("bounded milliseconds"),
            property,
            value: terminal,
            interpolation: MotionInterpolation::EaseOutCubic,
        });
    }
    keys
}

/// Pixel-space realization for one output, deliberately reflowed rather than cropped.
/// Font geometry is a conservative layout budget, not a claim of native glyph measurement.
pub fn realize_product_hero(
    id: Uuid,
    config: &HeroConfig,
    width: u32,
    height: u32,
) -> Result<Vec<CanvasNode>> {
    config.validate()?;
    if !(320..=7680).contains(&width) || !(320..=7680).contains(&height) {
        return Err(DomainError::Invalid(
            "hero output dimensions are out of bounds".into(),
        ));
    }
    let w = f64::from(width);
    let h = f64::from(height);
    let portrait = height > width;
    let square = height == width;
    let scale = w.min(h) / 1080.0;
    let left = w * 0.09;
    let (headline_y, body_y, mark_x, mark_y, mark_w, mark_h) = if portrait {
        (h * 0.15, h * 0.39, w * 0.16, h * 0.56, w * 0.68, h * 0.26)
    } else if square {
        (h * 0.16, h * 0.43, w * 0.51, h * 0.60, w * 0.38, h * 0.25)
    } else {
        (h * 0.28, h * 0.62, w * 0.64, h * 0.26, w * 0.27, h * 0.40)
    };
    let headline = wrap_copy(
        &config.headline,
        if portrait {
            19
        } else if square {
            22
        } else {
            20
        },
        3,
    )?;
    let body = wrap_copy(
        &config.body,
        if portrait {
            33
        } else if square {
            38
        } else {
            40
        },
        4,
    )?;
    let text_width = if portrait || square {
        w * 0.82
    } else {
        w * 0.49
    };
    let entries = [
        (
            "eyebrow",
            config.eyebrow.clone(),
            left,
            h * 0.085,
            w * 0.80,
            40.0 * scale,
            23.0 * scale,
            500,
            0,
            500,
            14.0 * scale,
            0.0,
        ),
        (
            "headline",
            headline,
            left,
            headline_y,
            text_width,
            if portrait { h * 0.22 } else { h * 0.30 },
            if square { 68.0 } else { 82.0 } * scale,
            600,
            120,
            1060,
            54.0 * scale,
            0.0,
        ),
        (
            "body",
            body,
            left,
            body_y,
            text_width,
            if portrait { h * 0.13 } else { h * 0.17 },
            30.0 * scale,
            400,
            460,
            1260,
            24.0 * scale,
            0.0,
        ),
        (
            "wordmark",
            config.wordmark.clone(),
            mark_x,
            mark_y,
            mark_w,
            mark_h,
            if portrait {
                246.0
            } else if square {
                186.0
            } else {
                276.0
            } * scale,
            600,
            160,
            1460,
            86.0 * scale,
            -5.0,
        ),
        (
            "rule",
            String::new(),
            left,
            h * 0.87,
            if portrait { w * 0.18 } else { w * 0.09 },
            3.0 * scale,
            0.0,
            400,
            600,
            1360,
            10.0 * scale,
            0.0,
        ),
        (
            "disclosure",
            "GRAPHIC STUDY / NOT A PRODUCT CAPTURE".into(),
            left,
            h * 0.905,
            w * 0.82,
            28.0 * scale,
            17.0 * scale,
            400,
            740,
            1500,
            10.0 * scale,
            0.0,
        ),
    ];
    let mut nodes = Vec::new();
    for (
        index,
        (role, text, x, y, width, height, font_size, weight, start, end, offset, rotation),
    ) in entries.into_iter().enumerate()
    {
        let is_text = role != "rule";
        let mut node = CanvasNode {
            id: hero_node_id(id, role),
            name: format!("ProductHeroReveal / {role}"),
            kind: if is_text { "text" } else { "rectangle" }.into(),
            parent_id: None,
            x,
            y,
            width,
            height,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: is_text.then_some(text),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 10 + index as i32,
            style: NodeStyle {
                fill: Some(if role == "wordmark" || role == "rule" {
                    config.accent.clone()
                } else {
                    config.foreground.clone()
                }),
                font_family: is_text.then(|| "Instrument Sans Variable".into()),
                font_size: is_text.then_some(font_size),
                font_weight: is_text.then_some(weight),
                line_height: is_text.then_some(1.12),
                ..Default::default()
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        if config.motion {
            node.keyframes = entry_keys(&node, start, end, offset, rotation);
        }
        node.validate()?;
        nodes.push(node);
    }
    Ok(nodes)
}

/// Merge at field/property granularity. A changed human value is never silently
/// overwritten, including a deleted node. Ambiguous changes abort the whole update.
pub fn merge_component_nodes(
    base: &[CanvasNode],
    current: &[CanvasNode],
    incoming: &[CanvasNode],
) -> Result<Vec<CanvasNode>> {
    if base.len() != incoming.len() {
        return Err(DomainError::Invalid(
            "component topology migration requires an explicit migration".into(),
        ));
    }
    let mut result = current.to_vec();
    for (old, new) in base.iter().zip(incoming) {
        if old.id != new.id {
            return Err(DomainError::Invalid("component identity changed".into()));
        }
        let live = result
            .iter_mut()
            .find(|node| node.id == old.id)
            .ok_or_else(|| {
                DomainError::Invalid(format!(
                    "component node {} was deleted; detach or restore it before updating",
                    old.id
                ))
            })?;
        fn merge_value(
            base: &serde_json::Value,
            current: &serde_json::Value,
            incoming: &serde_json::Value,
            path: &str,
        ) -> Result<serde_json::Value> {
            if incoming == base || incoming == current {
                return Ok(current.clone());
            }
            if current == base {
                return Ok(incoming.clone());
            }
            if let (Some(b), Some(c), Some(n)) =
                (base.as_object(), current.as_object(), incoming.as_object())
            {
                let mut result = c.clone();
                for (key, value) in n {
                    result.insert(
                        key.clone(),
                        merge_value(
                            b.get(key).unwrap_or(&serde_json::Value::Null),
                            c.get(key).unwrap_or(&serde_json::Value::Null),
                            value,
                            &format!("{path}.{key}"),
                        )?,
                    );
                }
                return Ok(serde_json::Value::Object(result));
            }
            Err(DomainError::Invalid(format!(
                "component override conflict at {path}; human value retained, no changes committed"
            )))
        }
        let before =
            serde_json::to_value(&*live).map_err(|e| DomainError::Invalid(e.to_string()))?;
        let merged = merge_value(
            &serde_json::to_value(old).unwrap(),
            &before,
            &serde_json::to_value(new).unwrap(),
            &old.name,
        )?;
        let candidate: CanvasNode =
            serde_json::from_value(merged).map_err(|e| DomainError::Invalid(e.to_string()))?;
        for property in &live.property_locks {
            let changed = match property {
                NodeProperty::Position => live.x != candidate.x || live.y != candidate.y,
                NodeProperty::Size => {
                    live.width != candidate.width || live.height != candidate.height
                }
                NodeProperty::Rotation => live.rotation_deg != candidate.rotation_deg,
                NodeProperty::Opacity => live.opacity != candidate.opacity,
                NodeProperty::Text => live.text != candidate.text,
                NodeProperty::Style => live.style != candidate.style,
                NodeProperty::Parent => live.parent_id != candidate.parent_id,
                NodeProperty::Order => live.z_index != candidate.z_index,
            };
            let motion_changed = live
                .keyframes
                .iter()
                .filter(|k| k.property.node_property() == *property)
                .ne(candidate
                    .keyframes
                    .iter()
                    .filter(|k| k.property.node_property() == *property));
            if changed || motion_changed {
                return Err(DomainError::Locked(format!(
                    "component node {} property {property:?}",
                    live.id
                )));
            }
        }
        *live = candidate;
    }
    Ok(result)
}

impl ProductHeroInstance {
    pub fn validate(&self) -> Result<()> {
        if self.component_version != HERO_VERSION
            || self.baseline != realize_product_hero(self.id, &self.config, 1920, 1080)?
        {
            return Err(DomainError::Invalid(
                "component baseline does not match its versioned deterministic realization".into(),
            ));
        }
        Ok(())
    }
}

impl Project {
    pub(crate) fn upsert_product_hero(
        &mut self,
        id: Uuid,
        scene_id: Uuid,
        config: &HeroConfig,
    ) -> Result<()> {
        self.ensure_unlocked(
            &self.resource_key(),
            &[
                LockKind::Content,
                LockKind::Position,
                LockKind::Style,
                LockKind::Timing,
            ],
        )?;
        self.ensure_unlocked(
            &format!("scene:{scene_id}"),
            &[
                LockKind::Content,
                LockKind::Position,
                LockKind::Style,
                LockKind::Timing,
                LockKind::Renderer,
            ],
        )?;
        let index = self
            .scenes
            .iter()
            .position(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        let scene = &self.scenes[index];
        if scene.renderer != RendererKind::MotionCanvas || scene.duration < whole_seconds(2) {
            return Err(DomainError::Invalid(
                "ProductHeroReveal requires a Motion Canvas scene of at least two seconds".into(),
            ));
        }
        let baseline = realize_product_hero(id, config, 1920, 1080)?;
        let nodes = if let Some(existing) = self
            .production_design
            .heroes
            .iter()
            .find(|hero| hero.id == id)
        {
            if existing.scene_id != scene_id {
                return Err(DomainError::Invalid(
                    "component cannot move scenes through an update".into(),
                ));
            }
            merge_component_nodes(&existing.baseline, &scene.nodes, &baseline)?
        } else {
            if self.production_design.heroes.len() >= 64
                || self
                    .production_design
                    .heroes
                    .iter()
                    .any(|hero| hero.scene_id == scene_id)
            {
                return Err(DomainError::Invalid(
                    "one hero per scene; at most 64 component instances".into(),
                ));
            }
            let mut nodes = scene.nodes.clone();
            if nodes
                .iter()
                .any(|node| baseline.iter().any(|new| new.id == node.id))
            {
                return Err(DomainError::Invalid(
                    "component node identity collision".into(),
                ));
            }
            nodes.extend(baseline.clone());
            nodes
        };
        let mut candidate = self.clone();
        candidate.scenes[index].nodes = nodes;
        candidate.scenes[index].status = SceneStatus::Draft;
        let hero = ProductHeroInstance {
            id,
            scene_id,
            component_version: HERO_VERSION,
            config: config.clone(),
            baseline,
        };
        if let Some(existing) = candidate
            .production_design
            .heroes
            .iter_mut()
            .find(|hero| hero.id == id)
        {
            *existing = hero;
        } else {
            candidate.production_design.heroes.push(hero);
        }
        candidate.validate()?;
        *self = candidate;
        Ok(())
    }
}
