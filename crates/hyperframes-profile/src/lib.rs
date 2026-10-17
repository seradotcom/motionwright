//! A finite, resource-closed HyperFrames source profile.
//!
//! The HTML is native source, retained byte-for-byte. Its accompanying document
//! is an admission witness, not a Project or an authorization. Admission checks
//! that both describe the same finite HTML grammar before Producer can run.
//! HyperFrames remains responsible for compilation, timing, capture and encode.
//! Importing arbitrary HTML/JS, font fallback equivalence and raster fidelity
//! are not established by this source check.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;

pub use semwright_media_time::Rational;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

pub const PROFILE_ID: &str = "motionwright.hyperframes.finite-source/1";
pub const HYPERFRAMES_REVISION: &str = "fa9366915620ea9baa1d03b0eb44caaca013dc21";
pub const HYPERFRAMES_VERSION: &str = "0.8.167";
pub const ENTRY_FILE: &str = "index.html";
pub const MAX_CARRIER_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_HTML_BYTES: usize = 256 * 1024;
pub const MAX_RESOURCE_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_TOTAL_RESOURCE_BYTES: usize = 12 * 1024 * 1024;
pub const MAX_RESOURCES: usize = 32;
pub const MAX_LAYERS: usize = 64;
pub const MAX_FRAMES: u32 = 180;
pub const MAX_CAPTURE_PIXELS: u64 = 64 * 1024 * 1024;

#[derive(Debug, Clone, Error, PartialEq, Eq)]
pub enum SourceError {
    #[error("HyperFrames source is invalid: {0}")]
    Invalid(&'static str),
    #[error("HyperFrames source exceeds the finite profile: {0}")]
    Limit(&'static str),
    #[error("HyperFrames source property is unsupported: {0}")]
    Unsupported(&'static str),
    #[error("HyperFrames source binding changed: {0}")]
    Binding(&'static str),
}

pub type Result<T> = std::result::Result<T, SourceError>;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceClock {
    pub fps: Rational,
    pub frame_count: u32,
}

impl SourceClock {
    pub fn validate(self) -> Result<()> {
        self.fps
            .validate()
            .map_err(|_| SourceError::Invalid("fps"))?;
        if !matches!(
            (self.fps.num, self.fps.den),
            (24 | 25 | 30 | 50 | 60, 1) | (24000 | 30000 | 60000, 1001)
        ) {
            return Err(SourceError::Unsupported("frame rate"));
        }
        if self.frame_count == 0 || self.frame_count > MAX_FRAMES {
            return Err(SourceError::Limit("frame count"));
        }
        if i128::from(self.frame_count) * i128::from(self.fps.den) > 6 * i128::from(self.fps.num) {
            return Err(SourceError::Limit("six second duration"));
        }
        Ok(())
    }

    pub fn frame_time(self, frame: u32) -> Result<Rational> {
        self.validate()?;
        if frame > self.frame_count {
            return Err(SourceError::Invalid("frame outside source"));
        }
        Rational::new(i64::from(frame) * self.fps.den, self.fps.num)
            .map_err(|_| SourceError::Invalid("frame time"))
    }

    pub fn duration(self) -> Result<Rational> {
        self.frame_time(self.frame_count)
    }
}

/// Integer fixed-point values avoid unbounded or non-finite CSS/JSON numbers.
/// Translation is in pixels; scale and opacity use thousandths, rotation uses
/// thousandths of a degree. The transform origin is the layer's top-left corner.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct LayerTransform {
    pub x: i32,
    pub y: i32,
    pub rotation_mdeg: i32,
    pub scale_x_milli: u32,
    pub scale_y_milli: u32,
    pub opacity_milli: u32,
}

impl Default for LayerTransform {
    fn default() -> Self {
        Self {
            x: 0,
            y: 0,
            rotation_mdeg: 0,
            scale_x_milli: 1000,
            scale_y_milli: 1000,
            opacity_milli: 1000,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum LayerContent {
    Rectangle {
        color: String,
        radius: u32,
    },
    Text {
        text: String,
        color: String,
        font_sha256: String,
        font_size: u32,
    },
    Image {
        asset_sha256: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceLayer {
    pub id: Uuid,
    /// Half-open range in the source clock, never seconds from a caller.
    pub start_frame: u32,
    pub end_frame: u32,
    pub width: u32,
    pub height: u32,
    pub transform: LayerTransform,
    pub content: LayerContent,
}

/// Source-only controls. This type does not replace Motionwright's Project.
/// Layer order is paint order. Hierarchy, animation curves, camera, masks,
/// audio/video, arbitrary CSS and arbitrary script require another profile.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceDocument {
    pub clock: SourceClock,
    pub width: u32,
    pub height: u32,
    pub background: String,
    pub layers: Vec<SourceLayer>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    Png,
    Woff2,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceResource {
    pub kind: ResourceKind,
    pub sha256: String,
    /// A carrier encoding, not a filename or a URL. Decoded bytes are preserved.
    pub bytes_hex: String,
}

impl SourceResource {
    pub fn from_bytes(kind: ResourceKind, bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > MAX_RESOURCE_BYTES {
            return Err(SourceError::Limit("resource bytes"));
        }
        check_resource(kind, bytes)?;
        Ok(Self {
            kind,
            sha256: sha256(bytes),
            bytes_hex: hex::encode(bytes),
        })
    }

    pub fn relative_path(&self) -> Result<String> {
        require_sha256(&self.sha256)?;
        let extension = match self.kind {
            ResourceKind::Png => "png",
            ResourceKind::Woff2 => "woff2",
        };
        Ok(format!("assets/{}.{}", self.sha256, extension))
    }

    fn decode(&self) -> Result<Vec<u8>> {
        require_sha256(&self.sha256)?;
        if self.bytes_hex.is_empty()
            || self.bytes_hex.len() > 2 * MAX_RESOURCE_BYTES
            || !self.bytes_hex.len().is_multiple_of(2)
        {
            return Err(SourceError::Limit("resource encoding"));
        }
        if !self
            .bytes_hex
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(SourceError::Invalid("resource encoding"));
        }
        let bytes =
            hex::decode(&self.bytes_hex).map_err(|_| SourceError::Invalid("resource encoding"))?;
        if sha256(&bytes) != self.sha256 {
            return Err(SourceError::Binding("resource digest"));
        }
        check_resource(self.kind, &bytes)?;
        Ok(bytes)
    }
}

/// Portable source with a deliberately closed grammar. Neither the witness nor
/// the embedded HTML is trusted on its own; every import checks their equality.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceCarrier {
    pub schema: String,
    pub producer_revision: String,
    pub document: SourceDocument,
    pub html: String,
    pub resources: Vec<SourceResource>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceReadiness {
    pub profile: String,
    pub producer_revision: String,
    pub carrier_sha256: String,
    pub html_sha256: String,
    pub resource_sha256: Vec<String>,
    pub clock: SourceClock,
    pub width: u32,
    pub height: u32,
    pub capture_pixels: u64,
    pub source_bytes: u64,
    pub evidence_scope: String,
    pub native_render_verified: bool,
    pub glyph_fidelity_verified: bool,
    pub editable_roundtrip_verified: bool,
}

/// Constructible only by admission. No Deserialize and no permission fields.
#[derive(Debug, Clone)]
pub struct AdmittedSource {
    carrier: SourceCarrier,
    original_bytes: Vec<u8>,
    resources: BTreeMap<String, Vec<u8>>,
    readiness: SourceReadiness,
}

impl AdmittedSource {
    pub fn document(&self) -> &SourceDocument {
        &self.carrier.document
    }
    pub fn html(&self) -> &str {
        &self.carrier.html
    }
    pub fn resources(&self) -> &BTreeMap<String, Vec<u8>> {
        &self.resources
    }
    pub fn readiness(&self) -> &SourceReadiness {
        &self.readiness
    }
    /// Export retains the admitted carrier bytes, including its original JSON
    /// formatting. A caller cannot substitute newly normalized bytes unnoticed.
    pub fn export_bytes(&self) -> &[u8] {
        &self.original_bytes
    }
}

pub fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

pub fn require_sha256(value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(SourceError::Invalid("canonical SHA-256"));
    }
    Ok(())
}

pub fn author_source(
    document: SourceDocument,
    resources: Vec<SourceResource>,
) -> Result<SourceCarrier> {
    let html = finite_html(&document, &resources)?;
    Ok(SourceCarrier {
        schema: PROFILE_ID.into(),
        producer_revision: HYPERFRAMES_REVISION.into(),
        document,
        html,
        resources,
    })
}

pub fn carrier_bytes(carrier: &SourceCarrier) -> Result<Vec<u8>> {
    validate_carrier(carrier)?;
    let mut bytes =
        serde_json::to_vec_pretty(carrier).map_err(|_| SourceError::Invalid("carrier JSON"))?;
    bytes.push(b'\n');
    if bytes.len() > MAX_CARRIER_BYTES {
        return Err(SourceError::Limit("carrier bytes"));
    }
    Ok(bytes)
}

pub fn admit_source(bytes: &[u8], expected_sha256: &str) -> Result<AdmittedSource> {
    if bytes.is_empty() || bytes.len() > MAX_CARRIER_BYTES {
        return Err(SourceError::Limit("carrier bytes"));
    }
    require_sha256(expected_sha256)?;
    if sha256(bytes) != expected_sha256 {
        return Err(SourceError::Binding("carrier digest"));
    }
    let carrier: SourceCarrier =
        serde_json::from_slice(bytes).map_err(|_| SourceError::Invalid("closed carrier JSON"))?;
    let resources = validate_carrier(&carrier)?;
    let document = &carrier.document;
    let readiness = SourceReadiness {
        profile: PROFILE_ID.into(),
        producer_revision: HYPERFRAMES_REVISION.into(),
        carrier_sha256: expected_sha256.into(),
        html_sha256: sha256(carrier.html.as_bytes()),
        resource_sha256: carrier.resources.iter().map(|r| r.sha256.clone()).collect(),
        clock: document.clock,
        width: document.width,
        height: document.height,
        capture_pixels: u64::from(document.width)
            * u64::from(document.height)
            * u64::from(document.clock.frame_count),
        source_bytes: (carrier.html.len() + resources.values().map(Vec::len).sum::<usize>()) as u64,
        evidence_scope: "FINITE_SOURCE_AND_RESOURCE_BINDINGS_ONLY".into(),
        native_render_verified: false,
        glyph_fidelity_verified: false,
        editable_roundtrip_verified: false,
    };
    Ok(AdmittedSource {
        carrier,
        original_bytes: bytes.to_vec(),
        resources,
        readiness,
    })
}

fn validate_carrier(carrier: &SourceCarrier) -> Result<BTreeMap<String, Vec<u8>>> {
    if carrier.schema != PROFILE_ID || carrier.producer_revision != HYPERFRAMES_REVISION {
        return Err(SourceError::Unsupported(
            "source profile or Producer revision",
        ));
    }
    if carrier.html.len() > MAX_HTML_BYTES {
        return Err(SourceError::Limit("HTML bytes"));
    }
    if finite_html(&carrier.document, &carrier.resources)? != carrier.html {
        return Err(SourceError::Unsupported(
            "HTML outside the finite source grammar",
        ));
    }
    resource_closure(&carrier.resources)
}

fn resource_closure(resources: &[SourceResource]) -> Result<BTreeMap<String, Vec<u8>>> {
    if resources.len() > MAX_RESOURCES {
        return Err(SourceError::Limit("resource count"));
    }
    let mut total = 0usize;
    let mut result = BTreeMap::new();
    let mut digests = BTreeSet::new();
    for resource in resources {
        let bytes = resource.decode()?;
        total = total
            .checked_add(bytes.len())
            .ok_or(SourceError::Limit("resource total"))?;
        if total > MAX_TOTAL_RESOURCE_BYTES {
            return Err(SourceError::Limit("resource total"));
        }
        if !digests.insert(resource.sha256.as_str()) {
            return Err(SourceError::Invalid("duplicate resource digest"));
        }
        result.insert(resource.relative_path()?, bytes);
    }
    Ok(result)
}

pub fn validate_document(document: &SourceDocument, resources: &[SourceResource]) -> Result<()> {
    document.clock.validate()?;
    if !(64..=1920).contains(&document.width)
        || !(64..=1080).contains(&document.height)
        || !document.width.is_multiple_of(2)
        || !document.height.is_multiple_of(2)
    {
        return Err(SourceError::Unsupported("even SDR raster geometry"));
    }
    let pixels = u64::from(document.width)
        * u64::from(document.height)
        * u64::from(document.clock.frame_count);
    if pixels > MAX_CAPTURE_PIXELS {
        return Err(SourceError::Limit("aggregate capture pixels"));
    }
    color(&document.background)?;
    if document.layers.is_empty() || document.layers.len() > MAX_LAYERS {
        return Err(SourceError::Limit("layer count"));
    }
    let closure = resource_closure(resources)?;
    let mut used = BTreeSet::new();
    let mut ids = BTreeSet::new();
    for layer in &document.layers {
        if layer.id.is_nil() || !ids.insert(layer.id) {
            return Err(SourceError::Invalid("layer identity"));
        }
        if layer.start_frame >= layer.end_frame || layer.end_frame > document.clock.frame_count {
            return Err(SourceError::Invalid("half-open layer range"));
        }
        if layer.width == 0 || layer.height == 0 || layer.width > 3840 || layer.height > 2160 {
            return Err(SourceError::Limit("layer geometry"));
        }
        let t = layer.transform;
        if t.x.unsigned_abs() > 3840
            || t.y.unsigned_abs() > 2160
            || t.rotation_mdeg.unsigned_abs() > 360_000
            || !(1..=4000).contains(&t.scale_x_milli)
            || !(1..=4000).contains(&t.scale_y_milli)
            || t.opacity_milli > 1000
        {
            return Err(SourceError::Unsupported("bounded planar transform"));
        }
        match &layer.content {
            LayerContent::Rectangle {
                color: fill,
                radius,
            } => {
                color(fill)?;
                if *radius > layer.width.min(layer.height) / 2 {
                    return Err(SourceError::Invalid("corner radius"));
                }
            }
            LayerContent::Text {
                text,
                color: fill,
                font_sha256,
                font_size,
            } => {
                color(fill)?;
                if text.is_empty()
                    || text.len() > 4096
                    || text.chars().any(|c| c.is_control() && c != '\n')
                    || !(8..=256).contains(font_size)
                {
                    return Err(SourceError::Unsupported("bounded text"));
                }
                require_sha256(font_sha256)?;
                let path = format!("assets/{font_sha256}.woff2");
                if !closure.contains_key(&path) {
                    return Err(SourceError::Binding("text font resource"));
                }
                used.insert(path);
            }
            LayerContent::Image { asset_sha256 } => {
                require_sha256(asset_sha256)?;
                let path = format!("assets/{asset_sha256}.png");
                if !closure.contains_key(&path) {
                    return Err(SourceError::Binding("image resource"));
                }
                used.insert(path);
            }
        }
    }
    if used.len() != closure.len() || !closure.keys().all(|key| used.contains(key)) {
        return Err(SourceError::Invalid("unused or unclosed resource"));
    }
    Ok(())
}

/// Authoring a small native HTML vocabulary is not a renderer. There are no
/// loops, expressions, scripts, remote references, CSS animations or runtime
/// duration discovery in this source profile. HyperFrames owns timed visibility.
pub fn finite_html(document: &SourceDocument, resources: &[SourceResource]) -> Result<String> {
    validate_document(document, resources)?;
    let mut html = String::from(
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"><style>\nhtml,body{margin:0;padding:0;overflow:hidden;}\n.mw-layer{position:absolute;left:0;top:0;box-sizing:border-box;transform-origin:0 0;overflow:hidden;}\n",
    );
    let mut fonts: Vec<_> = resources
        .iter()
        .filter(|r| r.kind == ResourceKind::Woff2)
        .collect();
    fonts.sort_by(|a, b| a.sha256.cmp(&b.sha256));
    for font in fonts {
        writeln!(html, "@font-face{{font-family:MWF{};src:url(\"{}\") format(\"woff2\");font-weight:400;font-style:normal;font-display:block;}}", font.sha256, font.relative_path()?).expect("write to String");
    }
    writeln!(html, "</style></head><body><div id=\"mw-root\" data-composition-id=\"main\" data-width=\"{}\" data-height=\"{}\" data-duration=\"{}\" style=\"position:relative;width:{}px;height:{}px;overflow:hidden;background:{};\">", document.width, document.height, seconds(document.clock.duration()?), document.width, document.height, document.background).expect("write to String");
    for layer in &document.layers {
        let t = layer.transform;
        let start = document.clock.frame_time(layer.start_frame)?;
        let duration = document
            .clock
            .frame_time(layer.end_frame - layer.start_frame)?;
        write!(html, "<div class=\"mw-layer\" id=\"mw-{}\" data-start=\"{}\" data-duration=\"{}\" style=\"width:{}px;height:{}px;transform:translate({}px,{}px) rotate({}deg) scale({},{});opacity:{};", layer.id, seconds(start), seconds(duration), layer.width, layer.height, t.x, t.y, milli(i64::from(t.rotation_mdeg)), milli(i64::from(t.scale_x_milli)), milli(i64::from(t.scale_y_milli)), milli(i64::from(t.opacity_milli))).expect("write to String");
        match &layer.content {
            LayerContent::Rectangle { color, radius } => {
                writeln!(
                    html,
                    "background:{color};border-radius:{radius}px;\"></div>"
                )
                .expect("write to String");
            }
            LayerContent::Text {
                text,
                color,
                font_sha256,
                font_size,
            } => {
                writeln!(html, "color:{color};font-family:MWF{font_sha256};font-size:{font_size}px;font-weight:400;font-style:normal;font-synthesis:none;line-height:1;white-space:pre-wrap;\">{}</div>", escape_html(text)).expect("write to String");
            }
            LayerContent::Image { asset_sha256 } => {
                writeln!(html, "\"><img alt=\"\" src=\"assets/{asset_sha256}.png\" width=\"{}\" height=\"{}\" style=\"display:block;width:100%;height:100%;object-fit:fill;\"></div>", layer.width, layer.height).expect("write to String");
            }
        }
    }
    html.push_str("</div></body></html>\n");
    if html.len() > MAX_HTML_BYTES {
        return Err(SourceError::Limit("HTML bytes"));
    }
    Ok(html)
}

fn milli(value: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let absolute = value.unsigned_abs();
    format!("{sign}{}.{:03}", absolute / 1000, absolute % 1000)
}

fn seconds(value: Rational) -> String {
    // Conversion is exclusively at the native HTML boundary. The exact clock
    // remains retained and the whole-medium inspector must check it unchanged.
    format!("{:.12}", value.num as f64 / value.den as f64)
}

fn escape_html(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn color(value: &str) -> Result<()> {
    if value.len() != 7
        || !value.starts_with('#')
        || !value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
    {
        return Err(SourceError::Invalid("explicit RGB color"));
    }
    Ok(())
}

fn check_resource(kind: ResourceKind, bytes: &[u8]) -> Result<()> {
    match kind {
        ResourceKind::Png => {
            if bytes.len() < 45 || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
                return Err(SourceError::Invalid("PNG resource header"));
            }
            let mut offset = 8usize;
            let mut chunks = 0usize;
            let mut saw_idat = false;
            loop {
                if chunks >= 1024 || offset.checked_add(12).is_none_or(|end| end > bytes.len()) {
                    return Err(SourceError::Invalid("bounded PNG chunks"));
                }
                let len =
                    u32::from_be_bytes(bytes[offset..offset + 4].try_into().expect("four bytes"))
                        as usize;
                let end = offset
                    .checked_add(12)
                    .and_then(|v| v.checked_add(len))
                    .filter(|v| *v <= bytes.len())
                    .ok_or(SourceError::Invalid("PNG chunk size"))?;
                let tag = &bytes[offset + 4..offset + 8];
                if chunks == 0 {
                    if tag != b"IHDR" || len != 13 {
                        return Err(SourceError::Invalid("PNG IHDR"));
                    }
                    let width = u32::from_be_bytes(
                        bytes[offset + 8..offset + 12]
                            .try_into()
                            .expect("four bytes"),
                    );
                    let height = u32::from_be_bytes(
                        bytes[offset + 12..offset + 16]
                            .try_into()
                            .expect("four bytes"),
                    );
                    if width == 0
                        || height == 0
                        || width > 1920
                        || height > 1080
                        || bytes[offset + 16] != 8
                        || ![2, 6].contains(&bytes[offset + 17])
                        || bytes[offset + 18..offset + 21] != [0, 0, 0]
                    {
                        return Err(SourceError::Unsupported("bounded non-interlaced RGB PNG"));
                    }
                } else if tag == b"IHDR" {
                    return Err(SourceError::Invalid("duplicate PNG IHDR"));
                }
                if matches!(tag, b"acTL" | b"fcTL" | b"fdAT" | b"iCCP" | b"eXIf") {
                    return Err(SourceError::Unsupported("animated or implicit-color PNG"));
                }
                if tag == b"IDAT" {
                    saw_idat = true;
                }
                if tag == b"IEND" {
                    if len != 0 || end != bytes.len() || !saw_idat {
                        return Err(SourceError::Invalid("PNG end"));
                    }
                    return Ok(());
                }
                offset = end;
                chunks += 1;
            }
        }
        ResourceKind::Woff2 => {
            if bytes.len() < 48 || &bytes[..4] != b"wOF2" {
                return Err(SourceError::Invalid("WOFF2 resource header"));
            }
            let declared =
                u32::from_be_bytes(bytes[8..12].try_into().expect("four bytes")) as usize;
            let tables = u16::from_be_bytes(bytes[12..14].try_into().expect("two bytes"));
            let sfnt = u32::from_be_bytes(bytes[16..20].try_into().expect("four bytes"));
            if declared != bytes.len()
                || !(1..=256).contains(&tables)
                || sfnt == 0
                || sfnt > 16 * 1024 * 1024
                || bytes[14..16] != [0, 0]
            {
                return Err(SourceError::Invalid("bounded WOFF2 header"));
            }
            // This does not decode Brotli, prove glyph coverage or grant font
            // rights. Those remain source admission dependencies and readback
            // obligations; SourceReadiness never claims glyph fidelity.
            Ok(())
        }
    }
}
