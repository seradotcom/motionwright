//! Internal renderer-native HTML composition profile. Not a universal creative IR.
//! Only explicit typed data crosses the executable boundary; source text is never code.
mod html;
mod validate;
pub use html::{compile_html, source_digest};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;
pub use validate::{validate_document, validate_plan};

pub const PROFILE_VERSION: u32 = 1;
pub const HYPERFRAMES_VERSION: &str = "0.8.143";
pub const GSAP_VERSION: &str = "3.15.0";
pub const PLAYWRIGHT_VERSION: &str = "1.55.1";
pub const MAX_NODES: usize = 256;
pub const MAX_KEYFRAMES: usize = 8192;
pub const MAX_ASSETS: usize = 64;
pub const MAX_FRAMES: u32 = 3600;
pub const MAX_DOCUMENT_BYTES: usize = 2 * 1024 * 1024;

#[derive(Debug, Error, Clone, PartialEq)]
#[error("{0}")]
pub struct ProfileError(pub String);
pub type Result<T> = std::result::Result<T, ProfileError>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HyperframesPlan {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub document: HyperframesDocument,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HyperframesDocument {
    pub version: u32,
    pub canvas: Canvas,
    #[serde(default)]
    pub camera: Camera,
    pub nodes: Vec<Node>,
    #[serde(default)]
    pub assets: Vec<Asset>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FrameRate {
    pub num: u32,
    pub den: u32,
}
impl FrameRate {
    pub fn seconds(self, frame: u32) -> f64 {
        f64::from(frame) * f64::from(self.den) / f64::from(self.num)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Canvas {
    pub width: u32,
    pub height: u32,
    pub rate: FrameRate,
    pub frames: u32,
    /// None explicitly requests transparent RGBA PNG, never an alpha-less MP4.
    pub background: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Node {
    pub id: Uuid,
    pub name: String,
    pub parent_id: Option<Uuid>,
    pub pose: Pose,
    pub content: Content,
    #[serde(default)]
    pub blend: Blend,
    #[serde(default)]
    pub clip: Clip,
    #[serde(default)]
    pub effects: Effects,
    #[serde(default)]
    pub keyframes: Vec<Keyframe>,
    #[serde(default)]
    pub locked_properties: Vec<Property>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub scale_x: f64,
    pub scale_y: f64,
    pub rotation: f64,
    pub opacity: f64,
    pub z_index: i32,
}
impl Default for Pose {
    fn default() -> Self {
        Self {
            x: 0.0,
            y: 0.0,
            width: 100.0,
            height: 100.0,
            scale_x: 1.0,
            scale_y: 1.0,
            rotation: 0.0,
            opacity: 1.0,
            z_index: 0,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Content {
    Group,
    Text {
        runs: Vec<TextRun>,
        font: Font,
        size: f64,
        line_height: f64,
        align: TextAlign,
    },
    Rectangle {
        fill: String,
        stroke: Option<String>,
        stroke_width: f64,
        radius: f64,
    },
    Ellipse {
        fill: String,
        stroke: Option<String>,
        stroke_width: f64,
    },
    Path {
        points: Vec<Point>,
        closed: bool,
        fill: Option<String>,
        stroke: String,
        stroke_width: f64,
    },
    Image {
        asset_id: Uuid,
        fit: Fit,
    },
    Video {
        asset_id: Uuid,
        fit: Fit,
        source_start_frame: u32,
        source_rate: FrameRate,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TextRun {
    pub text: String,
    pub color: String,
    pub weight: u16,
    pub italic: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Font {
    Sans,
    Mono,
    Asset { asset_id: Uuid, family: String },
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TextAlign {
    Left,
    Center,
    Right,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Fit {
    Contain,
    Cover,
    Stretch,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Blend {
    #[default]
    Normal,
    Multiply,
    Screen,
    Overlay,
    Difference,
    Lighten,
    Darken,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Clip {
    #[default]
    None,
    Inset {
        top: f64,
        right: f64,
        bottom: f64,
        left: f64,
        radius: f64,
    },
    Circle {
        radius: f64,
        center_x: f64,
        center_y: f64,
    },
    Polygon {
        points: Vec<Point>,
    },
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Effects {
    pub blur: f64,
    pub shadow: Option<Shadow>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Shadow {
    pub x: f64,
    pub y: f64,
    pub blur: f64,
    pub color: String,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum Property {
    X,
    Y,
    Width,
    Height,
    ScaleX,
    ScaleY,
    Rotation,
    Opacity,
    Blur,
    ClipTop,
    ClipRight,
    ClipBottom,
    ClipLeft,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Keyframe {
    pub frame: u32,
    pub property: Property,
    pub value: f64,
    pub curve: Curve,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Curve {
    Hold,
    Linear,
    EaseOutCubic,
    EaseInOut,
    CubicBezier { x1: f64, y1: f64, x2: f64, y2: f64 },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Camera {
    pub x: f64,
    pub y: f64,
    pub zoom: f64,
    pub rotation: f64,
    #[serde(default)]
    pub keyframes: Vec<Keyframe>,
}
impl Default for Camera {
    fn default() -> Self {
        Self {
            x: 0.0,
            y: 0.0,
            zoom: 1.0,
            rotation: 0.0,
            keyframes: vec![],
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Asset {
    pub id: Uuid,
    pub sha256: String,
    pub kind: AssetKind,
    /// A required owner declaration; the renderer does not infer or grant rights.
    pub rights: AssetRights,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AssetKind {
    Png,
    Jpeg,
    Mp4,
    Woff2,
}
impl AssetKind {
    pub fn extension(self) -> &'static str {
        match self {
            Self::Png => "png",
            Self::Jpeg => "jpg",
            Self::Mp4 => "mp4",
            Self::Woff2 => "woff2",
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AssetRights {
    pub owner: String,
    pub license: String,
    pub attribution: String,
    pub use_authorized: bool,
    pub redistribute: bool,
}
