//! MW05-E07-01: closed, source-owned, editable 2D ProductStage grammar.
//!
//! This is NOT a device model, product capture, licensed UI asset or 3D rig.
//! Only original user-attested semantic graphics can be realized in v1.
//! Generic neutral frames and explicit synthetic disclosure are mandatory.
//! No arbitrary CSS/HTML/SVG, shaders, URLs, code or external media is admitted.
use crate::*;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

pub const PRODUCT_STAGE_VERSION: u32 = 1;
pub const PRODUCT_STAGE_DISCLOSURE: &str = "ORIGINAL UI GRAPHIC / NOT A PRODUCT CAPTURE";
pub const STAGE_ACCENT_ROLES: [&str; 4] = [
    "chrome_indicator",
    "card_highlight",
    "action_button",
    "focus_marker",
];
pub const STAGE_ROLES: [&str; 14] = [
    "frame",
    "screen",
    "chrome",
    "chrome_indicator",
    "screen_heading",
    "screen_detail",
    "card",
    "card_highlight",
    "action_button",
    "action_text",
    "focus_marker",
    "focus_caption",
    "screen_caption",
    "disclosure",
];

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StageFrame {
    #[default]
    Browser,
    FlatPanel,
    GenericHandset,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductStageConfig {
    pub frame: StageFrame,
    pub screen_heading: String,
    pub screen_detail: String,
    pub action_label: String,
    /// Human declaration, not a verified license. Must be explicit and bounded.
    pub author_attestation: String,
    pub screen_surface: String,
    pub foreground: String,
    pub accent: String,
    /// Focus is in thousandths relative to *screen*, not absolute stage pixels.
    pub focus_x_permille: u16,
    pub focus_y_permille: u16,
    pub animate_focus: bool,
}
impl Default for ProductStageConfig {
    fn default() -> Self {
        Self {
            frame: StageFrame::Browser,
            screen_heading: "One clear workflow".into(),
            screen_detail: "Author a useful interface study".into(),
            action_label: "Explore".into(),
            author_attestation: String::new(),
            screen_surface: "#132431".into(),
            foreground: "#F2F4F3".into(),
            accent: "#86C8D1".into(),
            focus_x_permille: 510,
            focus_y_permille: 620,
            animate_focus: false,
        }
    }
}
impl ProductStageConfig {
    pub fn validate(&self) -> Result<()> {
        for (label, text, max) in [
            ("screen heading", &self.screen_heading, 40),
            ("screen detail", &self.screen_detail, 80),
            ("action label", &self.action_label, 20),
            ("source rights attestation", &self.author_attestation, 160),
        ] {
            if text.trim().is_empty()
                || text.chars().count() > max
                || text.chars().any(char::is_control)
            {
                return Err(DomainError::Invalid(format!(
                    "ProductStage {label} requires bounded, original authored content"
                )));
            }
        }
        for color in [&self.screen_surface, &self.foreground, &self.accent] {
            if color.len() != 7
                || !color.starts_with('#')
                || !color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
            {
                return Err(DomainError::Invalid(
                    "ProductStage colors require #RRGGBB".into(),
                ));
            }
        }
        if !(150..=850).contains(&self.focus_x_permille)
            || !(200..=800).contains(&self.focus_y_permille)
        {
            return Err(DomainError::Invalid(
                "ProductStage focus must remain within authorized screen bounds".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductStageInstance {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub component_version: u32,
    pub config: ProductStageConfig,
    /// Generated source for three-way merge. Human edits remain in Scene.nodes.
    pub baseline: Vec<CanvasNode>,
}
impl ProductStageInstance {
    pub fn validate(&self, scenes: &[Scene]) -> Result<()> {
        self.config.validate()?;
        if self.component_version != PRODUCT_STAGE_VERSION {
            return Err(DomainError::Invalid(
                "Unsupported ProductStage component version".into(),
            ));
        }
        if self.baseline != realize_product_stage(self.id, &self.config, 1920, 1080)? {
            return Err(DomainError::Invalid(
                "ProductStage baseline is not a deterministic authored realization".into(),
            ));
        }
        let scene = scenes
            .iter()
            .find(|scene| scene.id == self.scene_id)
            .ok_or_else(|| {
                DomainError::Invalid("ProductStage references an absent scene".into())
            })?;
        if scene.renderer != RendererKind::MotionCanvas {
            return Err(DomainError::Invalid(
                "ProductStage requires a Motion Canvas scene".into(),
            ));
        }
        let disclosure_id = stage_node_id(self.id, "disclosure");
        let disclosure = scene
            .nodes
            .iter()
            .find(|node| node.id == disclosure_id)
            .ok_or_else(|| {
                DomainError::Invalid("ProductStage safety disclosure was removed".into())
            })?;
        if disclosure.kind != "text" || disclosure.text.as_deref() != Some(PRODUCT_STAGE_DISCLOSURE)
        {
            return Err(DomainError::Invalid(
                "ProductStage synthetic disclosure cannot be removed or replaced while bound"
                    .into(),
            ));
        }
        for baseline in &self.baseline {
            if !scene.nodes.iter().any(|node| node.id == baseline.id) {
                return Err(DomainError::Invalid(
                    "ProductStage node was deleted; detach before removing semantic source".into(),
                ));
            }
        }
        Ok(())
    }
}
pub fn stage_node_id(instance: Uuid, role: &str) -> Uuid {
    let mut hasher = Sha256::new();
    hasher.update(b"motionwright.product-stage.v1\0");
    hasher.update(instance.as_bytes());
    hasher.update(role.as_bytes());
    let bytes: [u8; 32] = hasher.finalize().into();
    let mut id = [0_u8; 16];
    id.copy_from_slice(&bytes[..16]);
    id[6] = (id[6] & 0x0f) | 0x80;
    id[8] = (id[8] & 0x3f) | 0x80;
    Uuid::from_bytes(id)
}
#[derive(Clone, Copy)]
struct Rect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}
impl Rect {
    fn sub(&self, left: f64, top: f64, width: f64, height: f64) -> Rect {
        Rect {
            x: self.x + self.w * left,
            y: self.y + self.h * top,
            w: self.w * width,
            h: self.h * height,
        }
    }
}
/// Three deliberately unbranded flat frame silhouettes. These are not
/// third-party hardware models, product UI captures, or perspective meshes.
fn geometry(frame: StageFrame, width: u32, height: u32) -> (Rect, Rect) {
    let w = f64::from(width);
    let h = f64::from(height);
    let portrait = height > width;
    let square = height == width;
    let bounds = match (frame, portrait, square) {
        (StageFrame::GenericHandset, true, _) => Rect {
            x: w * 0.17,
            y: h * 0.08,
            w: w * 0.66,
            h: h * 0.835,
        },
        (StageFrame::GenericHandset, _, true) => Rect {
            x: w * 0.275,
            y: h * 0.07,
            w: w * 0.45,
            h: h * 0.85,
        },
        (StageFrame::GenericHandset, _, _) => Rect {
            x: w * 0.365,
            y: h * 0.085,
            w: w * 0.27,
            h: h * 0.83,
        },
        (_, true, _) => Rect {
            x: w * 0.075,
            y: h * 0.175,
            w: w * 0.85,
            h: h * 0.63,
        },
        (_, _, true) => Rect {
            x: w * 0.09,
            y: h * 0.18,
            w: w * 0.82,
            h: h * 0.64,
        },
        (_, _, _) => Rect {
            x: w * 0.15,
            y: h * 0.14,
            w: w * 0.70,
            h: h * 0.72,
        },
    };
    let screen = match frame {
        StageFrame::Browser => bounds.sub(0.035, 0.10, 0.93, 0.855),
        StageFrame::FlatPanel => bounds.sub(0.035, 0.053, 0.93, 0.894),
        StageFrame::GenericHandset => bounds.sub(0.075, 0.075, 0.85, 0.855),
    };
    (bounds, screen)
}
fn static_node(
    id: Uuid,
    role: &str,
    kind: &str,
    rect: Rect,
    z: i32,
    style: (&str, Option<&str>, f64, u16),
) -> CanvasNode {
    let (color, text, font, weight) = style;
    let is_text = kind == "text";
    CanvasNode {
        id: stage_node_id(id, role),
        name: format!("ProductStage / {role}"),
        kind: kind.into(),
        parent_id: None,
        x: rect.x,
        y: rect.y,
        width: rect.w,
        height: rect.h,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: text.map(str::to_owned),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: z,
        style: NodeStyle {
            fill: Some(color.into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: if is_text {
                Some("Instrument Sans Variable".into())
            } else {
                None
            },
            font_size: if is_text { Some(font) } else { None },
            font_weight: if is_text { Some(weight) } else { None },
            line_height: if is_text { Some(1.12) } else { None },
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn entrance_keys(node: &CanvasNode) -> Vec<CanvasKeyframe> {
    let mut out = Vec::with_capacity(4);
    for (property, initial, terminal) in [
        (MotionProperty::Y, node.y + 16.0, node.y),
        (MotionProperty::Opacity, 0.0, 1.0),
    ] {
        out.push(CanvasKeyframe {
            at: RationalTime::ZERO,
            property,
            value: initial,
            interpolation: MotionInterpolation::Hold,
        });
        out.push(CanvasKeyframe {
            at: RationalTime::new(9, 30).expect("exact pinned frame"),
            property,
            value: terminal,
            interpolation: MotionInterpolation::EaseOutCubic,
        });
    }
    out
}
pub fn realize_product_stage(
    id: Uuid,
    config: &ProductStageConfig,
    width: u32,
    height: u32,
) -> Result<Vec<CanvasNode>> {
    config.validate()?;
    if !(320..=7680).contains(&width) || !(320..=7680).contains(&height) {
        return Err(DomainError::Invalid(
            "ProductStage output dimensions out of bounds".into(),
        ));
    }
    let w = f64::from(width);
    let h = f64::from(height);
    let (frame, screen) = geometry(config.frame, width, height);
    let scale = w.min(h) / 1080.0;
    let focus_x = screen.x + screen.w * f64::from(config.focus_x_permille) / 1000.0;
    let focus_y = screen.y + screen.h * f64::from(config.focus_y_permille) / 1000.0;
    let focus_size = 18.0 * scale;
    let callout_x = (focus_x + 24.0 * scale).min(screen.x + screen.w * 0.84);
    let caption = match config.frame {
        StageFrame::Browser => "GENERIC BROWSER",
        StageFrame::FlatPanel => "GENERIC PANEL",
        StageFrame::GenericHandset => "GENERIC HANDSET",
    };
    let text_font = (32.0 * scale).min(screen.w * 0.052).max(10.0);
    let detail_font = (20.0 * scale).min(screen.w * 0.033).max(8.0);
    let action_font = (18.0 * scale).min(screen.w * 0.039).max(8.0);
    let mut nodes = vec![
        static_node(
            id,
            "frame",
            "rectangle",
            frame,
            5,
            ("#223542", None, 0.0, 400),
        ),
        static_node(
            id,
            "screen",
            "rectangle",
            screen,
            6,
            (&config.screen_surface, None, 0.0, 400),
        ),
        static_node(
            id,
            "chrome",
            "rectangle",
            screen.sub(0.0, 0.0, 1.0, 0.062),
            7,
            ("#2B4552", None, 0.0, 400),
        ),
        static_node(
            id,
            "chrome_indicator",
            "circle",
            screen.sub(0.035, 0.018, 0.015, 0.028),
            8,
            (&config.accent, None, 0.0, 400),
        ),
        static_node(
            id,
            "screen_heading",
            "text",
            screen.sub(0.08, 0.14, 0.84, 0.11),
            9,
            (
                &config.foreground,
                Some(&config.screen_heading),
                text_font,
                600,
            ),
        ),
        static_node(
            id,
            "screen_detail",
            "text",
            screen.sub(0.08, 0.27, 0.84, 0.09),
            10,
            (
                &config.foreground,
                Some(&config.screen_detail),
                detail_font,
                400,
            ),
        ),
        static_node(
            id,
            "card",
            "rectangle",
            screen.sub(0.08, 0.41, 0.84, 0.27),
            11,
            ("#29404B", None, 0.0, 400),
        ),
        static_node(
            id,
            "card_highlight",
            "rectangle",
            screen.sub(0.115, 0.465, 0.23, 0.115),
            12,
            (&config.accent, None, 0.0, 400),
        ),
        static_node(
            id,
            "action_button",
            "rectangle",
            screen.sub(0.08, 0.77, 0.36, 0.10),
            13,
            (&config.accent, None, 0.0, 400),
        ),
        static_node(
            id,
            "action_text",
            "text",
            screen.sub(0.10, 0.79, 0.29, 0.055),
            14,
            ("#132431", Some(&config.action_label), action_font, 600),
        ),
        static_node(
            id,
            "focus_marker",
            "circle",
            Rect {
                x: focus_x - focus_size / 2.0,
                y: focus_y - focus_size / 2.0,
                w: focus_size,
                h: focus_size,
            },
            15,
            (&config.accent, None, 0.0, 400),
        ),
        static_node(
            id,
            "focus_caption",
            "text",
            Rect {
                x: callout_x,
                y: focus_y - 11.0 * scale,
                w: screen.w * 0.12,
                h: 26.0 * scale,
            },
            16,
            (&config.foreground, Some("FOCUS"), 12.0 * scale, 600),
        ),
        static_node(
            id,
            "screen_caption",
            "text",
            screen.sub(0.08, 0.91, 0.82, 0.04),
            17,
            (&config.foreground, Some(caption), 12.0 * scale, 500),
        ),
        static_node(
            id,
            "disclosure",
            "text",
            Rect {
                x: w * 0.11,
                y: h * 0.905,
                w: w * 0.78,
                h: 24.0 * scale,
            },
            18,
            (
                &config.foreground,
                Some(PRODUCT_STAGE_DISCLOSURE),
                14.0 * scale,
                400,
            ),
        ),
    ];
    if config.animate_focus {
        for role in ["focus_marker", "focus_caption", "card_highlight"] {
            let target = stage_node_id(id, role);
            let node = nodes
                .iter_mut()
                .find(|node| node.id == target)
                .expect("fixed stage roles");
            node.keyframes = entrance_keys(node);
        }
    }
    // Every generated subject must stay within the native stage safe-area
    // budget, not merely within the device. No silent clipping or cropping.
    // One line per first-party screen text role in the v1 grammar. Fail
    // closed when replacing copy would overflow its anchor rather than
    // silently shrink, crop or shift the device/camera.
    for node in nodes.iter().filter(|node| node.kind == "text") {
        let text = node.text.as_deref().unwrap_or_default();
        let font = node.style.font_size.unwrap_or(0.0);
        if (text.chars().count() as f64) * font * 0.64 > node.width * 0.95 {
            return Err(DomainError::Invalid(format!(
                "ProductStage {} copy does not fit its screen anchor; shorten the original content",
                node.name
            )));
        }
    }
    if nodes.iter().any(|node| {
        node.x < w * 0.04
            || node.y < h * 0.04
            || node.x + node.width > w * 0.96
            || node.y + node.height > h * 0.95
            || node.width <= 0.0
            || node.height <= 0.0
    }) {
        return Err(DomainError::Invalid(
            "ProductStage stage subjects exceed native safe area".into(),
        ));
    }
    assert_eq!(nodes.len(), STAGE_ROLES.len());
    Ok(nodes)
}
impl Project {
    pub(crate) fn upsert_product_stage(
        &mut self,
        id: Uuid,
        scene_id: Uuid,
        config: &ProductStageConfig,
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
        if scene.renderer != RendererKind::MotionCanvas
            || scene.duration < RationalTime::new(2, 1).expect("exact two-second stage duration")
        {
            return Err(DomainError::Invalid(
                "ProductStage needs a Motion Canvas scene of at least two seconds".into(),
            ));
        }
        let baseline = realize_product_stage(id, config, 1920, 1080)?;
        let nodes = if let Some(existing) = self
            .production_design
            .stages
            .iter()
            .find(|stage| stage.id == id)
        {
            if existing.scene_id != scene_id {
                return Err(DomainError::Invalid(
                    "ProductStage instance cannot move scenes via update".into(),
                ));
            }
            merge_component_nodes(&existing.baseline, &scene.nodes, &baseline)?
        } else {
            if self.production_design.stages.len() >= 16
                || self
                    .production_design
                    .stages
                    .iter()
                    .any(|s| s.scene_id == scene_id)
                || self
                    .production_design
                    .heroes
                    .iter()
                    .any(|h| h.scene_id == scene_id)
            {
                return Err(DomainError::Invalid(
                    "ProductStage budget or first-party component collision".into(),
                ));
            }
            if scene
                .nodes
                .iter()
                .any(|node| baseline.iter().any(|new| node.id == new.id))
            {
                return Err(DomainError::Invalid(
                    "ProductStage source node identity collision".into(),
                ));
            }
            let mut nodes = scene.nodes.clone();
            nodes.extend(baseline.clone());
            nodes
        };
        let mut next = self.clone();
        next.scenes[index].nodes = nodes;
        next.scenes[index].status = SceneStatus::Draft;
        let stage = ProductStageInstance {
            id,
            scene_id,
            component_version: PRODUCT_STAGE_VERSION,
            config: config.clone(),
            baseline,
        };
        if let Some(existing) = next
            .production_design
            .stages
            .iter_mut()
            .find(|item| item.id == id)
        {
            *existing = stage;
        } else {
            next.production_design.stages.push(stage);
        }
        next.validate()?;
        *self = next;
        Ok(())
    }
    pub(crate) fn detach_product_stage(&mut self, id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let stage = self
            .production_design
            .stages
            .iter()
            .find(|stage| stage.id == id)
            .ok_or_else(|| DomainError::NotFound(format!("ProductStage:{id}")))?;
        self.ensure_unlocked(&format!("scene:{}", stage.scene_id), &[LockKind::Content])?;
        if self
            .production_design
            .stage_capture_cards
            .iter()
            .any(|card| card.stage_id == id)
        {
            return Err(DomainError::Invalid(
                "Detach the first-party source card before releasing its original ProductStage"
                    .into(),
            ));
        }
        self.production_design.stages.retain(|stage| stage.id != id);
        Ok(()) // stage Canvas nodes and their human edits remain intact
    }
}
