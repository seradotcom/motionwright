//! Closed observation records emitted by the fixed in-process Graph runtime.
//! These records contain no admission, execution or Project mutation authority.
use motionwright_domain::{GraphBox, GraphSourceSnapshot};
use serde::{Deserialize, Deserializer, Serialize};
use uuid::Uuid;

fn present_nullable<'de, D, T>(deserializer: D) -> std::result::Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct Camera {
    pub pixel_width: u32,
    pub pixel_height: u32,
    pub frame_width: f64,
    pub frame_height: f64,
    pub frame_rate: f64,
    pub frame_center: [f64; 3],
    pub cairo_line_width_multiple: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct Helper {
    pub module: String,
    pub relative_path: String,
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct Asset {
    pub relative_path: String,
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum Owner {
    Vertex { id: Uuid },
    Edge { id: Uuid },
    Disclosure,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TextReference {
    pub ordinal: u16,
    pub owner: Owner,
    pub file_name: String,
    pub size_bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Geometry {
    pub name: String,
    pub native_class: String,
    pub point_count: u32,
    pub point_encoding: String,
    pub points_sha256: String,
    #[serde(deserialize_with = "present_nullable")]
    pub control_point_min: Option<[f64; 3]>,
    #[serde(deserialize_with = "present_nullable")]
    pub control_point_max: Option<[f64; 3]>,
    pub control_bounds_are_glyph_ink_bounds: bool,
    pub z_index: f64,
    pub members_with_points: u32,
    pub paint_mode: String,
    pub paint_color: String,
    pub stroke_width: f64,
    #[serde(deserialize_with = "present_nullable")]
    pub stroke_join: Option<String>,
    #[serde(deserialize_with = "present_nullable")]
    pub stroke_cap: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Header {
    pub schema: String,
    pub profile_id: String,
    pub plan_sha256: String,
    pub generated_source_sha256: String,
    pub execution_snapshot: GraphSourceSnapshot,
    pub graph_id: Uuid,
    pub scene_id: Uuid,
    pub camera: Camera,
    pub helper_modules: Vec<Helper>,
    pub font_files: Vec<Asset>,
    pub text_blocks: Vec<TextReference>,
    pub objects: Vec<Geometry>,
    pub derived_byte_limit: u64,
    pub one_svg_parser_temporary_byte_limit: u64,
    pub point_limit: u32,
    pub decoded_video_verified: bool,
    pub native_reopen_verified: bool,
    pub profile_admitted: bool,
    pub human_acceptance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct RawDescriptor {
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct FontRun {
    pub offset: usize,
    pub length: usize,
    pub num_chars: usize,
    pub family: String,
    pub weight: u32,
    pub style: u32,
    pub face_index: u32,
    pub font_relative_path: String,
    pub font_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct NativeLayout {
    pub unknown_glyph_count: u32,
    pub ink_pango_units: [i32; 4],
    pub logical_pango_units: [i32; 4],
    pub baseline_pango_units: i32,
    pub line_count: u32,
    pub is_wrapped: bool,
    pub is_ellipsized: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Placement {
    pub line_start_x_px: f64,
    pub advance_px: f64,
    pub baseline_px: f64,
    pub layout_origin_px: [f64; 2],
    pub line_height_px: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Vector {
    pub schema: String,
    pub status: String,
    pub text_sha256: String,
    pub text_bytes: u64,
    pub runs: Vec<FontRun>,
    pub unknown_glyph_count: u32,
    pub fallback_allowed: bool,
    pub logical_bounds_px: [f64; 4],
    pub outline_bounds_px: [f64; 4],
    pub baseline_px: f64,
    pub layout_origin_px: [f64; 2],
    pub units_per_pixel: u32,
    pub svg_sha256: String,
    pub svg_bytes: u64,
    pub svg_outline_paths: u32,
    pub cairo_version: String,
    pub pango_version: String,
    pub fontconfig_version: u32,
    pub manim_import_verified: bool,
    pub manim_frame_verified: bool,
    pub profile_admitted: bool,
    pub human_acceptance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Line {
    pub index: usize,
    pub text: String,
    pub text_sha256: String,
    pub text_bytes: u64,
    pub placement: Placement,
    #[serde(deserialize_with = "present_nullable")]
    pub native_ink_bounds_px: Option<[f64; 4]>,
    pub has_ink: bool,
    pub runs: Vec<FontRun>,
    #[serde(deserialize_with = "present_nullable")]
    pub vector: Option<Vector>,
    pub native_layout: NativeLayout,
    pub pango_version: String,
    pub fontconfig_version: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Layout {
    pub schema: String,
    pub status: String,
    pub contract_sha256: String,
    pub resource: String,
    pub text_sha256: String,
    pub text_bytes: u64,
    pub literal_line_count: usize,
    pub font_size_px: f64,
    pub editorial_family: String,
    pub native_family: String,
    pub weight: u32,
    pub authored_box: GraphBox,
    pub lines: Vec<Line>,
    #[serde(deserialize_with = "present_nullable")]
    pub svg_sha256: Option<String>,
    pub svg_bytes: u64,
    pub original_line_svgs: Vec<RawDescriptor>,
    pub automatic_wrap: bool,
    pub automatic_scale: bool,
    pub clipping: bool,
    pub fallback_allowed: bool,
    pub manim_frame_verified: bool,
    pub native_reopen_verified: bool,
    pub profile_admitted: bool,
    pub human_acceptance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Imported {
    pub schema: String,
    pub manim_version: String,
    pub svg_sha256: String,
    pub svg_bytes: u64,
    pub private_svg_name: String,
    pub origin_reflection: [f64; 3],
    pub viewport_translation: [f64; 3],
    pub scale: f64,
    pub automatic_centering: bool,
    pub automatic_fit: bool,
    pub point_count: u32,
    pub point_encoding: String,
    pub points_sha256: String,
    pub control_point_min: [f64; 3],
    pub control_point_max: [f64; 3],
    pub control_bounds_are_glyph_ink_bounds: bool,
    pub manim_frame_verified: bool,
    pub native_reopen_verified: bool,
    pub profile_admitted: bool,
    pub human_acceptance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TextBlock {
    pub ordinal: u16,
    pub owner: Owner,
    pub layout: Layout,
    #[serde(deserialize_with = "present_nullable")]
    pub manim_import: Option<Imported>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Frame {
    pub record_type: String,
    pub scene_frame: u32,
    pub project_frame: u64,
    #[serde(deserialize_with = "present_nullable")]
    pub graph_local_frame: Option<u32>,
    pub callback_alpha: f64,
    pub camera: Camera,
    pub background_hex: String,
    pub background_opacity: f64,
    pub paint_opacity: Vec<Option<f64>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Terminal {
    pub record_type: String,
    pub sample_count: u32,
    pub static_geometry_reobserved_unchanged: bool,
    pub plan_sha256: String,
    pub terminal_editing_scene_frame: u32,
    pub decoded_video_verified: bool,
    pub native_reopen_verified: bool,
}
