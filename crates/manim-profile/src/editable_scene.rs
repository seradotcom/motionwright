//! Versioned, inert text/shape source for the original Manim Community Host.
//! A source carrier confers no native authority. Only this trusted compiler may
//! produce a program; imported scene.py is compared as data and never executed.
use super::*;

pub const PROFILE_ID: &str = "mw-manim-editable-text-v3";
pub const COMPILER_ID: &str = "motionwright.manim.rust.editable_text.v3.1";
pub const MAX_FONT_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_BUNDLE_BYTES: usize = 34 * 1024 * 1024;
pub const MAX_NATIVE_READBACK_BYTES: usize = 1024 * 1024;
pub const FONTCONFIG: &str = "<?xml version=\"1.0\"?>\n<fontconfig><dir prefix=\"relative\">.</dir><cachedir prefix=\"relative\">cache</cachedir><match target=\"pattern\"><edit name=\"dpi\" mode=\"assign\"><double>96</double></edit></match></fontconfig>\n";

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum ProfileId {
    #[serde(rename = "mw-manim-editable-text-v3")]
    EditableText,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FontFormat {
    Ttf,
    Otf,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FontSource {
    pub asset_id: Uuid,
    pub family: String,
    pub sha256: String,
    pub format: FontFormat,
}
impl FontSource {
    pub fn basename(&self) -> String {
        format!(
            "{}.{}",
            self.sha256,
            match self.format {
                FontFormat::Ttf => "ttf",
                FontFormat::Otf => "otf",
            }
        )
    }
    pub fn archive_path(&self) -> String {
        format!("fonts/{}", self.basename())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    Shape,
    Rectangle,
    Circle,
    Text,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Typography {
    pub text: String,
    pub font_asset_id: Uuid,
    pub font_size_px: f64,
    pub weight: u16,
    pub line_height: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Node {
    pub node_id: Uuid,
    pub display_name: String,
    pub kind: NodeKind,
    pub canonical_ordinal: u32,
    pub z_index: i32,
    pub x_px: f64,
    pub y_px: f64,
    pub width_px: f64,
    pub height_px: f64,
    pub rotation_deg: f64,
    pub opacity: f64,
    pub fill_hex: String,
    pub typography: Option<Typography>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Framing {
    Replan,
    Crop,
}

#[derive(Debug, Clone, Copy)]
struct Projection {
    sx: f64,
    sy: f64,
    object: f64,
    ox: f64,
    oy: f64,
    pixels_per_world: f64,
    frame_width: f64,
}
impl Projection {
    fn new(plan: &Plan) -> Self {
        let w = f64::from(plan.render_profile.width);
        let h = f64::from(plan.render_profile.height);
        let sx = w / f64::from(plan.canvas.width);
        let sy = h / f64::from(plan.canvas.height);
        let (sx, sy, object, ox, oy) = match plan.framing {
            Framing::Replan => (sx, sy, sx.min(sy), 0.0, 0.0),
            Framing::Crop => {
                let scale = sx.max(sy);
                (
                    scale,
                    scale,
                    scale,
                    (w - f64::from(plan.canvas.width) * scale) / 2.0,
                    (h - f64::from(plan.canvas.height) * scale) / 2.0,
                )
            }
        };
        Self {
            sx,
            sy,
            object,
            ox,
            oy,
            pixels_per_world: h / 8.0,
            frame_width: 8.0 * w / h,
        }
    }
    fn center(self, plan: &Plan, node: &Node) -> [f64; 3] {
        [
            ((node.x_px + node.width_px / 2.0) * self.sx + self.ox
                - f64::from(plan.render_profile.width) / 2.0)
                / self.pixels_per_world,
            (f64::from(plan.render_profile.height) / 2.0
                - (node.y_px + node.height_px / 2.0) * self.sy
                - self.oy)
                / self.pixels_per_world,
            0.0,
        ]
    }
    fn length(self, pixels: f64) -> f64 {
        pixels * self.object / self.pixels_per_world
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    pub schema_version: u16,
    pub profile_id: ProfileId,
    pub output_role: ManimOutputRoleV2,
    pub binding: ManimSceneBindingV2,
    pub clock: ManimFrameClockV2,
    pub render_profile: ManimRenderProfile,
    pub canvas: ManimCanvasV2,
    pub framing: Framing,
    pub safe_margin: f64,
    pub background: ManimMatteV2,
    pub fonts: Vec<FontSource>,
    pub nodes: Vec<Node>,
}

fn digest_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn family_valid(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.trim() == value
        && !value.chars().any(char::is_control)
}

pub fn validate_plan(plan: &Plan) -> Result<()> {
    plan.clock.validate()?;
    plan.render_profile.validate()?;
    color_hex(&plan.background.color)?;
    if plan.schema_version != 3
        || plan.canvas != ManimCanvasV2::default()
        || plan.background.opacity != 1
        || !plan.safe_margin.is_finite()
        || !(0.0..=0.25).contains(&plan.safe_margin)
        || !plan.render_profile.width.is_multiple_of(2)
        || !plan.render_profile.height.is_multiple_of(2)
        || i64::from(plan.render_profile.frame_rate) != plan.clock.frame_rate.num
    {
        return Err(profile_invalid("editable_render_profile_unsupported"));
    }
    if [
        plan.binding.project_id,
        plan.binding.generation,
        plan.binding.scene_id,
        plan.binding.deliverable_id,
    ]
    .iter()
    .any(Uuid::is_nil)
    {
        return Err(profile_invalid("editable_source_identity_invalid"));
    }
    if plan.fonts.len() > 2 || plan.nodes.is_empty() || plan.nodes.len() > 128 {
        return Err(profile_invalid("editable_source_budget"));
    }
    let mut assets = BTreeSet::new();
    let mut families = BTreeSet::new();
    let mut digests = BTreeSet::new();
    for font in &plan.fonts {
        if font.asset_id.is_nil()
            || !assets.insert(font.asset_id)
            || !family_valid(&font.family)
            || !families.insert(font.family.to_lowercase())
            || !digest_valid(&font.sha256)
            || !digests.insert(font.sha256.clone())
        {
            return Err(profile_invalid("editable_font_identity_invalid"));
        }
    }
    let projection = Projection::new(plan);
    let mut ids = BTreeSet::new();
    let mut used_fonts = BTreeSet::new();
    for (ordinal, node) in plan.nodes.iter().enumerate() {
        if node.node_id.is_nil()
            || !ids.insert(node.node_id)
            || node.canonical_ordinal as usize != ordinal
            || node.display_name.is_empty()
            || node.display_name.len() > 160
            || node.display_name.contains('\0')
            || !node.rotation_deg.is_finite()
            || node.rotation_deg.abs() > 3600.0
            || !node.opacity.is_finite()
            || !(0.0..=1.0).contains(&node.opacity)
        {
            return Err(profile_invalid(
                "editable_node_identity_or_transform_invalid",
            ));
        }
        color_hex(&node.fill_hex)?;
        positive_dimension(projection.length(node.width_px))?;
        positive_dimension(projection.length(node.height_px))?;
        let center = projection.center(plan, node);
        finite_coord(center[0])?;
        finite_coord(center[1])?;
        if plan.framing == Framing::Replan {
            let (sin, cos) = node.rotation_deg.to_radians().sin_cos();
            let half_w =
                (node.width_px * cos.abs() + node.height_px * sin.abs()) * projection.object / 2.0;
            let half_h =
                (node.width_px * sin.abs() + node.height_px * cos.abs()) * projection.object / 2.0;
            if center[0].abs() * projection.pixels_per_world + half_w
                > f64::from(plan.render_profile.width) * (0.5 - plan.safe_margin) + 1e-7
                || center[1].abs() * projection.pixels_per_world + half_h
                    > f64::from(plan.render_profile.height) * (0.5 - plan.safe_margin) + 1e-7
            {
                return Err(profile_invalid(
                    "editable_replan_transformed_bounds_outside_safe_area",
                ));
            }
        }
        if node.kind == NodeKind::Circle && node.width_px != node.height_px {
            return Err(profile_invalid("editable_circle_is_not_an_ellipse"));
        }
        match (&node.kind, &node.typography) {
            (NodeKind::Text, Some(text)) => {
                if !assets.contains(&text.font_asset_id)
                    || text.text.is_empty()
                    || text.text.len() > 16 * 1024
                    || text.text.chars().any(|c| c.is_control() && c != '\n')
                    || text.text.split('\n').any(|line| line.trim().is_empty())
                    || text.text.split('\n').count() > 16
                    || node.width_px > 1920.0
                    || node.height_px > 1080.0
                    || !text.font_size_px.is_finite()
                    || !(4.0..=512.0).contains(&text.font_size_px)
                    || !matches!(text.weight, 400 | 700)
                    || !text.line_height.is_finite()
                    || !(0.5..=4.0).contains(&text.line_height)
                {
                    return Err(profile_invalid(
                        "editable_plain_text_typography_unsupported",
                    ));
                }
                used_fonts.insert(text.font_asset_id);
            }
            (NodeKind::Text, None) | (_, Some(_)) => {
                return Err(profile_invalid("editable_text_shape_mismatch"));
            }
            (_, None) => {}
        }
    }
    if used_fonts != assets {
        return Err(profile_invalid("editable_unused_or_missing_font_asset"));
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FontMetadata {
    pub sha256: String,
    pub bytes: u64,
    pub family_names: Vec<String>,
    pub weight_min: u16,
    pub weight_max: u16,
    pub embedding_rights: u16,
    pub glyph_count: u16,
    pub required_codepoints: Vec<u32>,
}

/// Independent OpenType admission from the actual bytes. No shell, Pango call,
/// font installation, native receipt or authority is created by this parser.
pub fn inspect_font(plan: &Plan, font: &FontSource, bytes: &[u8]) -> Result<FontMetadata> {
    validate_plan(plan)?;
    if !plan.fonts.contains(font)
        || bytes.is_empty()
        || bytes.len() > MAX_FONT_BYTES
        || source_digest(bytes) != font.sha256
        || match font.format {
            FontFormat::Ttf => !bytes.starts_with(&[0, 1, 0, 0]),
            FontFormat::Otf => !bytes.starts_with(b"OTTO"),
        }
    {
        return Err(profile_invalid("editable_font_bytes_or_identity_mismatch"));
    }
    use ttf_parser::{Face, Permissions, Tag, name_id};
    let face = Face::parse(bytes, 0).map_err(|_| profile_invalid("editable_font_parse_failed"))?;
    if face.is_italic()
        || face.is_oblique()
        || (face.tables().glyf.is_none()
            && face.tables().cff.is_none()
            && face.tables().cff2.is_none())
        || !matches!(
            face.permissions(),
            Some(Permissions::Installable | Permissions::Editable)
        )
        || !face.is_outline_embedding_allowed()
        || face.names().len() > 256
    {
        return Err(profile_invalid(
            "editable_font_face_or_embedding_unsupported",
        ));
    }
    let mut families = BTreeSet::new();
    for name in face.names() {
        if matches!(name.name_id, name_id::FAMILY | name_id::TYPOGRAPHIC_FAMILY)
            && name.name.len() <= 1024
            && let Some(value) = name.to_string()
            && family_valid(&value)
        {
            families.insert(value);
        }
    }
    if !families.contains(&font.family) || families.len() > 16 {
        return Err(profile_invalid("editable_font_family_not_observed"));
    }
    let axes = face.variation_axes();
    if axes.len() > 1
        || axes
            .into_iter()
            .any(|axis| axis.tag != Tag::from_bytes(b"wght"))
    {
        return Err(profile_invalid("editable_font_variation_unsupported"));
    }
    let (weight_min, weight_max) = if let Some(axis) = axes.into_iter().next() {
        if !axis.min_value.is_finite()
            || !axis.max_value.is_finite()
            || axis.min_value < 1.0
            || axis.max_value > 1000.0
            || axis.min_value > axis.max_value
        {
            return Err(profile_invalid("editable_font_weight_axis_invalid"));
        }
        (axis.min_value.ceil() as u16, axis.max_value.floor() as u16)
    } else {
        let weight = face.weight().to_number();
        (weight, weight)
    };
    if weight_min > weight_max {
        return Err(profile_invalid("editable_font_weight_axis_empty"));
    }
    let mut points = BTreeSet::new();
    for node in &plan.nodes {
        if let Some(text) = &node.typography
            && text.font_asset_id == font.asset_id
        {
            if text.weight < weight_min || text.weight > weight_max {
                return Err(profile_invalid("editable_font_requested_weight_absent"));
            }
            points.extend(text.text.chars().filter(|cp| *cp != '\n').map(u32::from));
        }
    }
    if points.len() > 4096 {
        return Err(profile_invalid("editable_font_glyph_budget"));
    }
    for cp in &points {
        let index = char::from_u32(*cp).and_then(|cp| face.glyph_index(cp));
        if !index.is_some_and(|id| id.0 > 0 && id.0 < face.number_of_glyphs()) {
            return Err(profile_invalid("editable_font_requested_glyph_absent"));
        }
    }
    let os2 = face
        .raw_face()
        .table(Tag::from_bytes(b"OS/2"))
        .and_then(|table| table.get(8..10))
        .ok_or_else(|| profile_invalid("editable_font_embedding_unobserved"))?;
    let embedding_rights = u16::from_be_bytes([os2[0], os2[1]]);
    if !matches!(embedding_rights & 0x000f, 0 | 8) || embedding_rights & 0x0200 != 0 {
        return Err(profile_invalid("editable_font_embedding_restricted"));
    }
    Ok(FontMetadata {
        sha256: source_digest(bytes),
        bytes: bytes.len() as u64,
        family_names: families.into_iter().collect(),
        weight_min,
        weight_max,
        embedding_rights,
        glyph_count: face.number_of_glyphs(),
        required_codepoints: points.into_iter().collect(),
    })
}

pub fn inspect_fonts(plan: &Plan, fonts: &BTreeMap<String, Vec<u8>>) -> Result<Vec<FontMetadata>> {
    validate_plan(plan)?;
    if fonts.len() != plan.fonts.len() {
        return Err(profile_invalid("editable_font_inventory_mismatch"));
    }
    plan.fonts
        .iter()
        .map(|font| {
            let bytes = fonts
                .get(&font.archive_path())
                .ok_or_else(|| profile_invalid("editable_font_source_missing"))?;
            inspect_font(plan, font, bytes)
        })
        .collect()
}

pub fn compile(plan: &Plan) -> Result<String> {
    validate_plan(plan)?;
    let payload = source_json(plan)?;
    let payload =
        std::str::from_utf8(&payload).map_err(|_| profile_invalid("editable_source_encoding"))?;
    let wait = frozen_wait_v2(plan.clock.frame_count, plan.render_profile.frame_rate)?;
    // Insert untrusted data last: marker-like user text cannot be rewritten
    // by later template substitutions or break the quoted JSON literal.
    let source = include_str!("editable_scene.py")
        .replace(
            "__FONTCONFIG_SHA_LITERAL__",
            &py_string(&source_digest(FONTCONFIG.as_bytes()))?,
        )
        .replace("__WAIT_HEX_LITERAL__", &py_string(&wait.seconds_hex)?)
        .replace("__PLAN_JSON_LITERAL__", &py_string(payload)?);
    if source.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(profile_invalid("editable_program_budget"));
    }
    Ok(source)
}

#[derive(Debug, Clone, PartialEq)]
pub struct Package {
    pub plan: Plan,
    pub entries: BTreeMap<String, Vec<u8>>,
    pub closure: ManimSourceClosureV1,
}

pub fn source_package(plan: &Plan, fonts: &BTreeMap<String, Vec<u8>>) -> Result<Package> {
    let metadata = inspect_fonts(plan, fonts)?;
    let mut entries = fonts.clone();
    entries.extend(BTreeMap::from([
        ("plan.json".to_owned(), source_json(plan)?),
        ("scene.py".to_owned(), compile(plan)?.into_bytes()),
        ("fonts.conf".to_owned(), FONTCONFIG.as_bytes().to_vec()),
        ("render-config.json".to_owned(), source_json(&serde_json::json!({
            "schema_version": 1, "profile_id": PROFILE_ID, "compiler_id": COMPILER_ID,
            "manim_version_requirement": "0.21.0", "scene_class": SCENE_CLASS, "renderer": "cairo",
            "render_profile": plan.render_profile, "frame_width": Projection::new(plan).frame_width, "frame_height": 8,
            "framing": plan.framing, "safe_margin": plan.safe_margin, "font_size_projection": "canonical_px*object_scale*72/pixels_per_world",
            "background": plan.background, "frozen_wait": frozen_wait_v2(plan.clock.frame_count, plan.render_profile.frame_rate)?,
            "fonts_only_fontconfig_sha256": source_digest(FONTCONFIG.as_bytes()),
            "text_layout": "unwrapped_centered_lines_explicit_baseline_advance",
            "tex_enabled": false, "fallback_enabled": false, "native_execution_attested": false,
        }))?),
        ("assets.lock.json".to_owned(), source_json(&serde_json::json!({
            "schema_version": 1, "profile_id": PROFILE_ID, "fonts": plan.fonts, "metadata": metadata,
            "fonts_are_data_not_authority": true, "runtime_fallback": "deny",
        }))?),
    ]));
    let rows = entries
        .iter()
        .map(|(path, bytes)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: bytes.len() as u64,
            sha256: source_digest(bytes),
        })
        .collect::<Vec<_>>();
    let plan_sha256 = source_digest(&entries["plan.json"]);
    let source_sha256 = source_digest(&entries["scene.py"]);
    let config_sha256 = source_digest(&entries["render-config.json"]);
    let assets_lock_sha256 = source_digest(&entries["assets.lock.json"]);
    let manifest = source_json(&serde_json::json!({
        "schema_version":1, "bundle_kind":"motionwright.manim.editable_source", "compiler_id":COMPILER_ID,
        "encoding_id":"serde-json-pretty-rational-strings-zip9-stored-v1", "entries":rows,
        "plan_sha256":plan_sha256, "source_sha256":source_sha256, "config_sha256":config_sha256,
        "losses":["generated_python_edits_outside_typed_plan_not_importable", "binary64_geometry_projection",
            "opaque_matte_no_output_alpha", "plain_pango_text_no_tex_or_markup", "no_automatic_line_wrap",
            "native_font_metrics_and_antialiasing_differ_from_browser_text", "no_audio_or_captions"],
        "native_render_verified":false, "native_reopen_verified":false, "canonical_import_applied":false,
    }))?;
    let manifest_sha256 = source_digest(&manifest);
    entries.insert("manifest.json".into(), manifest);
    let closure = ManimSourceClosureV1 {
        plan_schema_version: 3,
        compiler_contract_id: COMPILER_ID.into(),
        plan_sha256,
        source_sha256,
        config_sha256,
        assets_lock_sha256,
        manifest_sha256,
        entries: entries
            .iter()
            .map(|(path, bytes)| ManimSourceEntryV1 {
                path: path.clone(),
                bytes: bytes.len() as u64,
                sha256: source_digest(bytes),
            })
            .collect(),
    };
    Ok(Package {
        plan: plan.clone(),
        entries,
        closure,
    })
}

fn encode(entries: &BTreeMap<String, Vec<u8>>) -> Result<Vec<u8>> {
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Stored)
        .last_modified_time(zip::DateTime::default())
        .unix_permissions(0o600);
    for (path, bytes) in entries {
        zip.start_file(path, options)
            .map_err(|_| profile_invalid("editable_archive_invalid"))?;
        zip.write_all(bytes)
            .map_err(|_| profile_invalid("editable_archive_invalid"))?;
    }
    let bytes = zip
        .finish()
        .map_err(|_| profile_invalid("editable_archive_invalid"))?
        .into_inner();
    if bytes.len() > MAX_BUNDLE_BYTES {
        return Err(profile_invalid("editable_archive_budget"));
    }
    Ok(bytes)
}
pub fn source_bundle(plan: &Plan, fonts: &BTreeMap<String, Vec<u8>>) -> Result<Vec<u8>> {
    encode(&source_package(plan, fonts)?.entries)
}

pub fn inspect_source_bundle(bytes: &[u8]) -> Result<Package> {
    if bytes.is_empty() || bytes.len() > MAX_BUNDLE_BYTES {
        return Err(profile_invalid("editable_archive_budget"));
    }
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| profile_invalid("editable_archive_invalid"))?;
    if !(6..=8).contains(&zip.len()) || !zip.comment().is_empty() {
        return Err(profile_invalid("editable_archive_inventory"));
    }
    let mut entries = BTreeMap::new();
    let mut total = 0usize;
    for i in 0..zip.len() {
        let mut file = zip
            .by_index(i)
            .map_err(|_| profile_invalid("editable_archive_invalid"))?;
        let name = file
            .name()
            .map_err(|_| profile_invalid("editable_archive_name"))?
            .into_owned();
        let is_font = name.strip_prefix("fonts/").is_some_and(|name| {
            name.strip_suffix(".ttf")
                .or_else(|| name.strip_suffix(".otf"))
                .is_some_and(digest_valid)
        });
        let allowed = is_font
            || [
                "plan.json",
                "scene.py",
                "fonts.conf",
                "render-config.json",
                "assets.lock.json",
                "manifest.json",
            ]
            .contains(&name.as_str());
        let maximum = if is_font {
            MAX_FONT_BYTES
        } else {
            MAX_SOURCE_ENTRY_BYTES_V2
        };
        if !allowed
            || entries.contains_key(&name)
            || !file.is_file()
            || file.is_symlink()
            || file.encrypted()
            || file.compression() != zip::CompressionMethod::Stored
            || !file.comment().is_empty()
            || file.size() == 0
            || file.size() > maximum as u64
        {
            return Err(profile_invalid("editable_archive_entry_invalid"));
        }
        let length = file.size() as usize;
        total = total
            .checked_add(length)
            .ok_or_else(|| profile_invalid("editable_archive_budget"))?;
        if total > MAX_BUNDLE_BYTES {
            return Err(profile_invalid("editable_archive_budget"));
        }
        let mut data = Vec::with_capacity(length);
        file.by_ref()
            .take(maximum as u64 + 1)
            .read_to_end(&mut data)
            .map_err(|_| profile_invalid("editable_archive_checksum"))?;
        if data.len() != length {
            return Err(profile_invalid("editable_archive_size"));
        }
        entries.insert(name, data);
    }
    let plan: Plan = serde_json::from_slice(
        entries
            .get("plan.json")
            .ok_or_else(|| profile_invalid("editable_plan_missing"))?,
    )
    .map_err(|_| profile_invalid("editable_plan_invalid"))?;
    let fonts = entries
        .iter()
        .filter(|(name, _)| name.starts_with("fonts/"))
        .map(|(name, data)| (name.clone(), data.clone()))
        .collect();
    let expected = source_package(&plan, &fonts)?;
    if entries != expected.entries || encode(&entries)? != bytes {
        return Err(profile_invalid(
            "editable_source_projection_or_manifest_mismatch",
        ));
    }
    Ok(expected)
}

pub fn rebind_source(
    origin: &Package,
    current: &Plan,
    current_fonts: &BTreeMap<String, Vec<u8>>,
) -> Result<Package> {
    validate_plan(&origin.plan)?;
    validate_plan(current)?;
    let mut rebound = origin.plan.clone();
    rebound.binding.generation = current.binding.generation;
    rebound.binding.revision = current.binding.revision;
    if rebound != *current {
        return Err(profile_invalid("editable_semantic_rebind_mismatch"));
    }
    let package = source_package(current, current_fonts)?;
    for font in &current.fonts {
        if origin.entries.get(&font.archive_path()) != package.entries.get(&font.archive_path()) {
            return Err(profile_invalid("editable_rebind_font_bytes_changed"));
        }
    }
    Ok(package)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeFont {
    pub sha256: String,
    pub bytes: u64,
    pub registered: bool,
    pub native_family_present: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeTextLine {
    pub primitive: String,
    pub original_text: String,
    pub family: String,
    pub weight: String,
    pub font_size_before_transform: f64,
    pub local_center: [f64; 3],
    pub local_width: f64,
    pub local_height: f64,
    pub glyph_count: usize,
    pub fill_hex: String,
    pub fill_opacity: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeObject {
    pub name: String,
    pub primitive: String,
    pub center: [f64; 3],
    pub layout_corners: [[f64; 3]; 4],
    pub x_axis: [f64; 3],
    pub y_axis: [f64; 3],
    pub z_index: f64,
    pub fill_hex: String,
    pub fill_opacity: f64,
    pub stroke_width: f64,
    pub stroke_opacity: f64,
    pub lines: Vec<NativeTextLine>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NativeReadback {
    pub schema_version: u16,
    pub profile_id: ProfileId,
    pub scene_class: String,
    pub source_file_sha256: String,
    pub fontconfig_sha256: String,
    pub camera: ManimNativeCameraReadbackV1,
    pub fonts: Vec<NativeFont>,
    pub objects: Vec<NativeObject>,
}

/// This inspects a native process sidecar. Unit fixtures validate admission only;
/// they never establish execution, decoded pixels, font fidelity or acceptance.
pub fn inspect_native_readback(plan: &Plan, bytes: &[u8]) -> Result<NativeReadback> {
    validate_plan(plan)?;
    if bytes.is_empty() || bytes.len() > MAX_NATIVE_READBACK_BYTES {
        return Err(profile_invalid("editable_native_readback_budget"));
    }
    let actual: NativeReadback = serde_json::from_slice(bytes)
        .map_err(|_| profile_invalid("editable_native_readback_invalid"))?;
    let near = |a: f64, b: f64| a.is_finite() && (a - b).abs() <= 1.0e-7;
    let vector = |a: &[f64; 3], b: &[f64; 3]| a.iter().zip(b).all(|(a, b)| near(*a, *b));
    let camera = &actual.camera;
    let projection = Projection::new(plan);
    if actual.schema_version != 1
        || actual.profile_id != plan.profile_id
        || actual.scene_class != SCENE_CLASS
        || actual.source_file_sha256 != source_digest(compile(plan)?.as_bytes())
        || actual.fontconfig_sha256 != source_digest(FONTCONFIG.as_bytes())
        || camera.pixel_width != plan.render_profile.width
        || camera.pixel_height != plan.render_profile.height
        || !near(camera.frame_rate, f64::from(plan.render_profile.frame_rate))
        || !near(camera.frame_width, projection.frame_width)
        || !near(camera.frame_height, 8.0)
        || !vector(&camera.frame_center, &[0.0, 0.0, 0.0])
        || !camera
            .background_hex
            .eq_ignore_ascii_case(&plan.background.color)
        || !near(camera.background_opacity, 1.0)
        || actual.fonts.len() != plan.fonts.len()
        || actual.objects.len() != plan.nodes.len()
    {
        return Err(profile_invalid(
            "editable_native_header_camera_or_inventory_mismatch",
        ));
    }
    for (font, expected) in actual.fonts.iter().zip(&plan.fonts) {
        if font.sha256 != expected.sha256
            || font.bytes == 0
            || font.bytes > MAX_FONT_BYTES as u64
            || !font.registered
            || !font.native_family_present
        {
            return Err(profile_invalid(
                "editable_native_font_registration_unobserved",
            ));
        }
    }
    let mut ordered = plan.nodes.iter().collect::<Vec<_>>();
    ordered.sort_by_key(|node| (node.z_index, node.canonical_ordinal));
    for (node, expected) in actual.objects.iter().zip(ordered) {
        let theta = -expected.rotation_deg.to_radians();
        let (sin, cos) = theta.sin_cos();
        let x = [cos, sin, 0.0];
        let y = [-sin, cos, 0.0];
        let center = projection.center(plan, expected);
        let corners = [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)].map(|(sx, sy)| {
            [
                center[0]
                    + sx * projection.length(expected.width_px) / 2.0 * x[0]
                    + sy * projection.length(expected.height_px) / 2.0 * y[0],
                center[1]
                    + sx * projection.length(expected.width_px) / 2.0 * x[1]
                    + sy * projection.length(expected.height_px) / 2.0 * y[1],
                0.0,
            ]
        });
        let expected_primitive = match expected.kind {
            NodeKind::Text => "VGroup",
            NodeKind::Circle => "Circle",
            _ => "Rectangle",
        };
        if node.name != format!("mw_{}", expected.node_id.simple())
            || node.primitive != expected_primitive
            || !vector(&node.center, &center)
            || !vector(&node.x_axis, &x)
            || !vector(&node.y_axis, &y)
            || !near(node.z_index, f64::from(expected.z_index))
            || !node.fill_hex.eq_ignore_ascii_case(&expected.fill_hex)
            || !near(node.fill_opacity, expected.opacity)
            || !near(node.stroke_width, 0.0)
            || !near(node.stroke_opacity, 0.0)
        {
            return Err(profile_invalid("editable_native_object_property_mismatch"));
        }
        let mut seen = BTreeSet::new();
        for corner in &node.layout_corners {
            let index = corners
                .iter()
                .position(|expected| vector(corner, expected))
                .ok_or_else(|| profile_invalid("editable_native_layout_corner_mismatch"))?;
            if !seen.insert(index) {
                return Err(profile_invalid("editable_native_duplicate_layout_corner"));
            }
        }
        if let Some(text) = &expected.typography {
            let font = plan
                .fonts
                .iter()
                .find(|font| font.asset_id == text.font_asset_id)
                .ok_or_else(|| profile_invalid("editable_native_font_missing"))?;
            let lines = text.text.split('\n').collect::<Vec<_>>();
            if lines.len() != node.lines.len() {
                return Err(profile_invalid("editable_native_text_line_inventory"));
            }
            for (index, (line, expected_line)) in node.lines.iter().zip(&lines).enumerate() {
                // Pinned Manim 0.21: Pango points=size/4.8, 96/72 px per point,
                // SVG scale=0.05 world units/px. Thus one em=size/72 world units.
                let native_font_size = projection.length(text.font_size_px) * 72.0;
                let center_y = ((lines.len() - 1) as f64 / 2.0 - index as f64)
                    * projection.length(text.font_size_px * text.line_height);
                if line.primitive != "MotionwrightPlainText"
                    || line.original_text != *expected_line
                    || line.family != font.family
                    || line.weight != if text.weight == 700 { "BOLD" } else { "NORMAL" }
                    || !near(line.font_size_before_transform, native_font_size)
                    || !vector(&line.local_center, &[0.0, center_y, 0.0])
                    || !line.local_width.is_finite()
                    || line.local_width <= 0.0
                    || line.local_width > projection.length(expected.width_px) + 1.0e-7
                    || !line.local_height.is_finite()
                    || line.local_height <= 0.0
                    || line.local_height / 2.0 + center_y.abs()
                        > projection.length(expected.height_px) / 2.0 + 1.0e-7
                    || line.glyph_count == 0
                    || line.glyph_count > 65536
                    || !line.fill_hex.eq_ignore_ascii_case(&expected.fill_hex)
                    || !near(line.fill_opacity, expected.opacity)
                {
                    return Err(profile_invalid(
                        "editable_native_text_property_or_layout_mismatch",
                    ));
                }
            }
        } else if !node.lines.is_empty() {
            return Err(profile_invalid("editable_native_shape_has_text"));
        }
    }
    Ok(actual)
}

#[cfg(test)]
mod tests {
    use super::*;
    const FONT: &[u8] = include_bytes!("../tests/fixtures/Montserrat-variable.ttf");
    fn plan() -> Plan {
        serde_json::from_value(serde_json::json!({
            "schema_version":3,"profile_id":"mw-manim-editable-text-v3",
            "output_role":"scene_video_contribution_without_audio_or_captions",
            "binding":{"project_id":Uuid::from_u128(1),"generation":Uuid::from_u128(2),"revision":7,
                "scene_id":Uuid::from_u128(3),"deliverable_id":Uuid::from_u128(4)},
            "clock":{"timeline_start":{"num":"1","den":"2"},"duration":{"num":"4","den":"1"},
                "frame_rate":{"num":"30","den":"1"},"project_start_frame":15,"project_end_frame_exclusive":135,"local_start_frame":0,"frame_count":120},
            "render_profile":{"width":960,"height":540,"frame_rate":30},
            "canvas":{"width":1920,"height":1080,"coordinate_space":"project_pixels"},"framing":"replan","safe_margin":0.05,
            "background":{"kind":"solid","color":"#153047","opacity":1},
            "fonts":[{"asset_id":Uuid::from_u128(5),"family":"Montserrat","sha256":source_digest(FONT),"format":"ttf"}],
            "nodes":[{"node_id":Uuid::from_u128(6),"display_name":"Editable title","kind":"text","canonical_ordinal":0,"z_index":3,
                "x_px":660.0,"y_px":390.0,"width_px":600.0,"height_px":300.0,"rotation_deg":17.0,"opacity":0.65,"fill_hex":"#EAC454",
                "typography":{"text":"Native text\nSecond line","font_asset_id":Uuid::from_u128(5),"font_size_px":64.0,"weight":700,"line_height":1.2}}]
        })).unwrap()
    }
    fn fonts(plan: &Plan) -> BTreeMap<String, Vec<u8>> {
        BTreeMap::from([(plan.fonts[0].archive_path(), FONT.to_vec())])
    }
    #[test]
    fn actual_opentype_identity_weight_glyphs_and_editable_permission_are_required() {
        let plan = plan();
        let face = inspect_font(&plan, &plan.fonts[0], FONT).unwrap();
        assert_eq!(
            face.sha256,
            "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7"
        );
        assert_eq!(face.bytes, 744936);
        assert!(face.family_names.contains(&"Montserrat".into()));
        assert_eq!((face.weight_min, face.weight_max), (100, 900));
        assert_eq!(face.embedding_rights, 0);
        assert!(face.required_codepoints.contains(&u32::from('N')));
        let mut wrong = plan.clone();
        wrong.fonts[0].family = "Absent family".into();
        assert!(inspect_font(&wrong, &wrong.fonts[0], FONT).is_err());
        let mut missing = plan.clone();
        missing.nodes[0].typography.as_mut().unwrap().text = "\u{10ffff}".into();
        assert!(inspect_font(&missing, &missing.fonts[0], FONT).is_err());
        assert!(inspect_font(&plan, &plan.fonts[0], b"not a font").is_err());
    }
    #[test]
    fn actual_os2_restricted_embedding_cannot_be_laundered_by_a_new_digest() {
        let mut bytes = FONT.to_vec();
        let tables = u16::from_be_bytes([bytes[4], bytes[5]]) as usize;
        let row = (0..tables)
            .map(|i| 12 + i * 16)
            .find(|offset| &bytes[*offset..*offset + 4] == b"OS/2")
            .unwrap();
        let offset = u32::from_be_bytes(bytes[row + 8..row + 12].try_into().unwrap()) as usize;
        bytes[offset + 8..offset + 10].copy_from_slice(&2u16.to_be_bytes());
        let mut plan = plan();
        plan.fonts[0].sha256 = source_digest(&bytes);
        assert!(inspect_font(&plan, &plan.fonts[0], &bytes).is_err());
    }
    #[test]
    fn no_silent_clamp_empty_line_or_missing_font_admission() {
        for case in [
            "size",
            "weight",
            "line_height",
            "blank_line",
            "control",
            "missing_font",
            "unused_font",
            "blend_field",
        ] {
            let mut value = serde_json::to_value(plan()).unwrap();
            match case {
                "size" => {
                    value["nodes"][0]["typography"]["font_size_px"] = serde_json::json!(513.0)
                }
                "weight" => value["nodes"][0]["typography"]["weight"] = serde_json::json!(600),
                "line_height" => {
                    value["nodes"][0]["typography"]["line_height"] = serde_json::json!(0.1)
                }
                "blank_line" => {
                    value["nodes"][0]["typography"]["text"] = serde_json::json!("a\n \nb")
                }
                "control" => value["nodes"][0]["typography"]["text"] = serde_json::json!("a\tb"),
                "missing_font" => value["fonts"] = serde_json::json!([]),
                "unused_font" => {
                    let mut font = value["fonts"][0].clone();
                    font["asset_id"] = serde_json::json!(Uuid::from_u128(55));
                    font["family"] = serde_json::json!("Another");
                    font["sha256"] = serde_json::json!("a".repeat(64));
                    value["fonts"].as_array_mut().unwrap().push(font);
                }
                "blend_field" => value["nodes"][0]["blend_mode"] = serde_json::json!("screen"),
                _ => unreachable!(),
            }
            assert!(
                serde_json::from_value::<Plan>(value)
                    .map(|p| validate_plan(&p).is_err())
                    .unwrap_or(true),
                "{case}"
            );
        }
    }
    #[test]
    fn source_is_canonical_inert_and_template_markers_inside_text_remain_data() {
        let mut plan = plan();
        plan.nodes[0].typography.as_mut().unwrap().text =
            "__WAIT_HEX_LITERAL__ __FONTCONFIG_SHA_LITERAL__ __PLAN_JSON_LITERAL__ \"quoted\""
                .into();
        let source = compile(&plan).unwrap();
        assert!(
            source
                .contains("__WAIT_HEX_LITERAL__ __FONTCONFIG_SHA_LITERAL__ __PLAN_JSON_LITERAL__")
        );
        assert!(source.contains("pango_width=-1"));
        assert!(!source.contains("eval("));
        assert!(!source.contains("subprocess"));
        let bundle = source_bundle(&plan, &fonts(&plan)).unwrap();
        let actual = inspect_source_bundle(&bundle).unwrap();
        assert_eq!(actual.plan, plan);
        assert_eq!(actual.entries["scene.py"], source.as_bytes());
        let mut corrupt = actual.entries.clone();
        corrupt
            .get_mut("scene.py")
            .unwrap()
            .extend_from_slice(b"\nprint('untrusted')\n");
        assert!(inspect_source_bundle(&encode(&corrupt).unwrap()).is_err());
        let mut extra = actual.entries;
        extra.insert("../runtime.session".into(), b"foreign".to_vec());
        assert!(inspect_source_bundle(&encode(&extra).unwrap()).is_err());
    }
    #[test]
    fn source_rebind_changes_only_current_generation_revision_and_keeps_font_bytes() {
        let plan = plan();
        let fonts = fonts(&plan);
        let package = source_package(&plan, &fonts).unwrap();
        let mut current = plan.clone();
        current.binding.generation = Uuid::from_u128(88);
        current.binding.revision += 2;
        assert_eq!(
            rebind_source(&package, &current, &fonts).unwrap().plan,
            current
        );
        current.nodes[0].rotation_deg += 1.0;
        assert!(rebind_source(&package, &current, &fonts).is_err());
        let mut wrong = fonts;
        wrong.values_mut().next().unwrap()[0] = 255;
        assert!(rebind_source(&package, &plan, &wrong).is_err());
    }
    #[test]
    fn three_output_aspects_project_em_and_geometry_from_actual_pixel_density() {
        for (width, height, em_world) in [
            (960, 540, 64.0 / 135.0),
            (540, 960, 0.15),
            (720, 720, 64.0 / 240.0),
        ] {
            let mut plan = plan();
            plan.render_profile.width = width;
            plan.render_profile.height = height;
            validate_plan(&plan).unwrap();
            let projection = Projection::new(&plan);
            assert!((projection.pixels_per_world - f64::from(height) / 8.0).abs() < 1e-12);
            assert!((projection.length(64.0) - em_world).abs() < 1e-12);
            assert!(
                (projection.frame_width - 8.0 * f64::from(width) / f64::from(height)).abs() < 1e-12
            );
            assert_eq!(projection.center(&plan, &plan.nodes[0]), [0.0, 0.0, 0.0]);
        }
        let mut plan = plan();
        plan.nodes[0].x_px = -800.0;
        assert!(validate_plan(&plan).is_err());
        plan.framing = Framing::Crop;
        assert!(validate_plan(&plan).is_ok());
    }
    // This JSON is an adversarial inspector fixture, explicitly not a native receipt.
    fn shape_readback(plan: &Plan) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "schema_version":1,"profile_id":"mw-manim-editable-text-v3","scene_class":"MotionwrightScene",
            "source_file_sha256":source_digest(compile(plan).unwrap().as_bytes()),"fontconfig_sha256":source_digest(FONTCONFIG.as_bytes()),
            "camera":{"pixel_width":960,"pixel_height":540,"frame_rate":30.0,"frame_width":128.0/9.0,"frame_height":8.0,
                "frame_center":[0.0,0.0,0.0],"background_hex":"#153047","background_opacity":1.0},"fonts":[],
            "objects":[{"name":format!("mw_{}",Uuid::from_u128(6).simple()),"primitive":"Rectangle","center":[0.0,0.0,0.0],
                "layout_corners":[[-2.0,-1.0,0.0],[-2.0,1.0,0.0],[2.0,-1.0,0.0],[2.0,1.0,0.0]],
                "x_axis":[1.0,0.0,0.0],"y_axis":[0.0,1.0,0.0],"z_index":3.0,"fill_hex":"#EAC454","fill_opacity":0.65,
                "stroke_width":0.0,"stroke_opacity":0.0,"lines":[]}]})).unwrap()
    }
    #[test]
    fn altered_native_geometry_alpha_source_hash_or_inventory_fails_inspector() {
        let mut plan = plan();
        plan.fonts.clear();
        let node = &mut plan.nodes[0];
        node.kind = NodeKind::Rectangle;
        node.typography = None;
        node.width_px = 540.0;
        node.height_px = 270.0;
        node.x_px = 690.0;
        node.y_px = 405.0;
        node.rotation_deg = 0.0;
        let bytes = shape_readback(&plan);
        assert!(inspect_native_readback(&plan, &bytes).is_ok());
        for case in ["source", "camera", "opacity", "rotation", "corner", "extra"] {
            let mut observed: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
            match case {
                "source" => observed["source_file_sha256"] = serde_json::json!("0".repeat(64)),
                "camera" => observed["camera"]["frame_height"] = serde_json::json!(9.0),
                "opacity" => observed["objects"][0]["fill_opacity"] = serde_json::json!(1.0),
                "rotation" => {
                    observed["objects"][0]["x_axis"] = serde_json::json!([-1.0, 0.0, 0.0])
                }
                "corner" => {
                    observed["objects"][0]["layout_corners"][3] =
                        serde_json::json!([2.0, -1.0, 0.0])
                }
                "extra" => {
                    let obj = observed["objects"][0].clone();
                    observed["objects"].as_array_mut().unwrap().push(obj);
                }
                _ => unreachable!(),
            }
            assert!(
                inspect_native_readback(&plan, &serde_json::to_vec(&observed).unwrap()).is_err(),
                "{case}"
            );
        }
    }
    #[test]
    fn editable_bundle_carries_exact_fontconfig_and_rejects_missing_or_changed_config() {
        let plan = plan();
        let bytes = source_bundle(&plan, &fonts(&plan)).unwrap();
        let original = inspect_source_bundle(&bytes).unwrap();
        assert_eq!(original.entries.len(), 7);
        assert_eq!(original.entries["fonts.conf"], FONTCONFIG.as_bytes());
        assert!(original.closure.entries.iter().any(|entry| {
            entry.path == "fonts.conf"
                && entry.bytes == FONTCONFIG.len() as u64
                && entry.sha256 == source_digest(FONTCONFIG.as_bytes())
        }));
        let mut absent = original.entries.clone();
        absent.remove("fonts.conf");
        assert!(inspect_source_bundle(&encode(&absent).unwrap()).is_err());
        let mut altered = original.entries;
        altered.insert(
            "fonts.conf".into(),
            b"<?xml version=\"1.0\"?><fontconfig><dir>/usr/share/fonts</dir></fontconfig>".to_vec(),
        );
        assert!(inspect_source_bundle(&encode(&altered).unwrap()).is_err());
        assert_eq!(inspect_source_bundle(&bytes).unwrap().plan, plan);
    }
}
