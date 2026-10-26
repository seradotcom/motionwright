//! Trusted source preparation and bounded editable closure for canonical Graph.
//! This adds no executor, admission factory or Project mutation authority.
use crate::semantic_graph::{ManimSemanticGraphPlan, exact_frame};
use crate::semantic_graph_text::{
    GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256, appearance_frames, arrowhead, rounded_rectangle_radius,
    text_blocks,
};
use crate::{
    MAX_SOURCE_BUNDLE_BYTES_V2, MAX_SOURCE_ENTRY_BYTES_V2, ManimProfileError, ManimSourceClosureV1,
    ManimSourceEntryV1, Result,
};
use motionwright_domain::{GraphAppearance, GraphContent, SEMANTIC_GRAPH_PROFILE};
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Cursor, Read};
use uuid::Uuid;

pub const GRAPH_COMPILER_V1: &str = "motionwright.manim.rust.semantic_graph.v1.0";
pub const GRAPH_SOURCE_MIME_V1: &str =
    "application/vnd.motionwright.manim-semantic-graph-source+zip";
pub const MAX_GRAPH_READBACK_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_GRAPH_READBACK_FILES: usize = 4096;

fn reject(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct GraphBuiltinAssetPin {
    pub relative_path: &'static str,
    pub sha256: &'static str,
    pub size_bytes: u64,
}

// Unconverted original bytes from the two package-lock-pinned 5.3.0 packages.
// These source digests do not attest a runtime, face opening or profile grant.
const BUILTIN_ASSETS: [GraphBuiltinAssetPin; 9] = [
    GraphBuiltinAssetPin {
        relative_path: "instrument-sans/LICENSE",
        sha256: "c27a3c53c3beed7f5c26853afa15991478ff7145d3754a36b0382f84e10c0d03",
        size_bytes: 4538,
    },
    GraphBuiltinAssetPin {
        relative_path: "instrument-sans/files/instrument-sans-latin-ext-wght-normal.woff2",
        sha256: "c4fcfea41f2c1cfeea9211fa43679845454a1d0e0d7e95e069c7e73c4ae302d2",
        size_bytes: 11144,
    },
    GraphBuiltinAssetPin {
        relative_path: "instrument-sans/files/instrument-sans-latin-wght-normal.woff2",
        sha256: "2ee17598a98d8a59e4df8152d015bec9ab8e4d5672cc0ab42bef806b568e3971",
        size_bytes: 30092,
    },
    GraphBuiltinAssetPin {
        relative_path: "ibm-plex-mono/LICENSE",
        sha256: "23b0a9d0c6d3f140a0b77e483c5cfa6bba574325ef5cb189ed9f2fec4884533f",
        size_bytes: 5299,
    },
    GraphBuiltinAssetPin {
        relative_path: "ibm-plex-mono/files/ibm-plex-mono-cyrillic-ext-400-normal.woff2",
        sha256: "f8c22ec1804bd6966b70f43ac7c327025645b4c09597117fca2a5a6c750106fe",
        size_bytes: 6912,
    },
    GraphBuiltinAssetPin {
        relative_path: "ibm-plex-mono/files/ibm-plex-mono-cyrillic-400-normal.woff2",
        sha256: "7635422bd10ddbbcb0e0caa1154b928298f5c8319b8c00b30978d4395f4783ce",
        size_bytes: 8356,
    },
    GraphBuiltinAssetPin {
        relative_path: "ibm-plex-mono/files/ibm-plex-mono-vietnamese-400-normal.woff2",
        sha256: "62632b6375305e70ddcafdd895218e6ae41a184fcd280a69c739460604ca00a9",
        size_bytes: 5868,
    },
    GraphBuiltinAssetPin {
        relative_path: "ibm-plex-mono/files/ibm-plex-mono-latin-ext-400-normal.woff2",
        sha256: "6bc0f226a5b7884a8170e3f62c63d7675609d4631bdc5931b5cdab81821f00eb",
        size_bytes: 13348,
    },
    GraphBuiltinAssetPin {
        relative_path: "ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2",
        sha256: "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
        size_bytes: 14708,
    },
];

pub fn graph_builtin_asset_pins() -> &'static [GraphBuiltinAssetPin] {
    &BUILTIN_ASSETS
}

fn helper_sources() -> [(&'static str, &'static [u8]); 6] {
    [
        (
            "manim_bundled_font_intake",
            include_bytes!("../../../tooling/manim_bundled_font_intake.py"),
        ),
        (
            "manim_graph_font_probe",
            include_bytes!("../../../tooling/manim_graph_font_probe.py"),
        ),
        (
            "manim_graph_vector_text",
            include_bytes!("../../../tooling/manim_graph_vector_text.py"),
        ),
        (
            "manim_graph_svg_import",
            include_bytes!("../../../tooling/manim_graph_svg_import.py"),
        ),
        (
            "manim_graph_text_layout",
            include_bytes!("../../../tooling/manim_graph_text_layout.py"),
        ),
        (
            "manim_graph_runtime",
            include_bytes!("../../../tooling/manim_graph_runtime.py"),
        ),
    ]
}

pub(crate) fn graph_helper_pins() -> Vec<Value> {
    helper_sources()
        .iter()
        .map(|(module, bytes)| {
            json!({
                "module": module,
                "relative_path": format!("graph-profile-v1/helpers/{module}.py"),
                "sha256": super::source_digest(bytes),
                "size_bytes": bytes.len(),
            })
        })
        .collect()
}

pub(crate) fn graph_font_lock() -> Value {
    let packages = [
        (
            "instrument-sans",
            "@fontsource-variable/instrument-sans",
            "Instrument Sans Variable",
            "Instrument Sans",
            "b227c74ad6c1bd5448d9ab4cc03662c083fcb814f450c712e93bd7091e9bbb89",
        ),
        (
            "ibm-plex-mono",
            "@fontsource/ibm-plex-mono",
            "IBM Plex Mono",
            "IBM Plex Mono",
            "60d3c0cfa549cb06fcb8867ee83f994d78e212abfac3bef3275436acb5c4d029",
        ),
    ]
    .iter()
    .map(|(prefix, package, editorial, native, archive_sha)| {
        let prefix = format!("{prefix}/");
        let rows = BUILTIN_ASSETS
            .iter()
            .filter(|item| item.relative_path.starts_with(&prefix));
        let license = rows
            .clone()
            .find(|item| item.relative_path.ends_with("/LICENSE"))
            .expect("fixed font table contains one license per package");
        let fonts = rows
            .filter(|item| item.relative_path.ends_with(".woff2"))
            .collect::<Vec<_>>();
        json!({
            "package": package, "version": "5.3.0", "editorial_family": editorial,
            "expected_native_family": native, "weight": 400, "style": "normal",
            "fallback": "forbidden", "archive_sha256": archive_sha,
            "license": license, "font_files": fonts,
        })
    })
    .collect::<Vec<_>>();
    json!({
        "schema": "motionwright.manim.graph-font-lock/1",
        "contract_sha256": GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
        "profile_origin_sdk_pin": "54d3459a051adbce4cae9f60c0dea8f60c824be6",
        "observed_sdk_commit": "def9c35e38432704cbb45d9ba2c3683a8dd373ba",
        "observed_package_lock_sha256": "ad80a11a188c70ea8bd28e1fd194d871e9f22479d20c29bb0ead0406dde890b8",
        "packages": packages,
        "native_font_support_attested": false,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GraphReadbackPathSpecV1 {
    pub required: BTreeSet<String>,
    pub allowed: BTreeSet<String>,
}

/// Paths are generated from typed source, not read from untrusted observations.
/// Optional line/composite SVGs depend on real Pango ink; the complete inspector
/// narrows the observed subset after checking each original byte descriptor.
pub fn graph_native_readback_paths_v1(
    plan: &ManimSemanticGraphPlan,
) -> Result<GraphReadbackPathSpecV1> {
    let blocks = text_blocks(plan)?;
    let mut required = BTreeSet::from(["header.json".into(), "frames.jsonl".into()]);
    let mut allowed = required.clone();
    for block in blocks {
        let lines = block.text.split('\n').count();
        if block.text.contains(['\r', '\t'])
            || (lines as f64) * (1.2 * plan.source.style.font_size) > block.authored_box.height
        {
            return Err(reject("graph_literal_text_or_line_height_unsupported"));
        }
        let evidence = format!("labels/text_block_{:04}.json", block.ordinal);
        required.insert(evidence.clone());
        allowed.insert(evidence);
        allowed.insert(format!("labels/label_{:04}.svg", block.ordinal));
        for line in 0..lines {
            allowed.insert(format!("labels/source_{:04}_{line:04}.svg", block.ordinal));
            if allowed.len() > MAX_GRAPH_READBACK_FILES {
                return Err(reject("graph_readback_file_count_budget"));
            }
        }
    }
    Ok(GraphReadbackPathSpecV1 { required, allowed })
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PreparedGraphProgramV1 {
    pub compiler_id: &'static str,
    pub plan_sha256: String,
    pub source_sha256: String,
    pub plan_document: Vec<u8>,
    pub python_source: String,
    pub native_execution_performed: bool,
    pub runtime_sample_pts_verified: bool,
}

fn edge_appearances(plan: &ManimSemanticGraphPlan) -> BTreeMap<Uuid, &GraphAppearance> {
    match &plan.source.content {
        GraphContent::CausalDiagram { edges, .. } => edges
            .iter()
            .map(|edge| (edge.id, &edge.appearance))
            .collect(),
        GraphContent::SystemFlow { edges, .. } => edges
            .iter()
            .map(|edge| (edge.id, &edge.appearance))
            .collect(),
    }
}

/// Internal derived data only. The editable source is still SemanticGraph.
pub(crate) fn graph_native_payload(plan: &ManimSemanticGraphPlan) -> Result<Value> {
    plan.validate()?;
    graph_native_readback_paths_v1(plan)?;
    let fps = plan.render_profile.frame_rate;
    let document = plan.source_document()?;
    let sample = plan.sample(0)?;
    let mut vertices = Vec::new();
    for vertex in plan.source.content.vertices() {
        vertices.push(json!({
            "id": vertex.id, "shape": vertex.shape, "authored_box": vertex.r#box,
            "corner_radius": rounded_rectangle_radius(&vertex.r#box),
            "appearance": appearance_frames(&vertex.appearance, fps)?,
        }));
    }
    let appearances = edge_appearances(plan);
    let mut edges = Vec::new();
    for edge in sample.edges {
        let appearance = appearances
            .get(&edge.id)
            .ok_or_else(|| reject("graph_edge_appearance_missing"))?;
        edges.push(json!({
            "id": edge.id, "points": edge.points,
            "arrowhead": arrowhead(&edge.points, plan.source.style.edge_width)?,
            "appearance": appearance_frames(appearance, fps)?,
        }));
    }
    let mut projected_events = Vec::new();
    if let GraphContent::SystemFlow { events, .. } = &plan.source.content {
        for event in events {
            let first = exact_frame(event.at, fps)?;
            let count = exact_frame(event.duration, fps)?;
            let end = first
                .checked_add(count)
                .ok_or_else(|| reject("graph_event_frame_overflow"))?;
            projected_events.push(json!({
                "id": event.id, "edge_id": event.edge_id,
                "start_frame": first, "end_frame_exclusive": end,
            }));
        }
    }
    Ok(json!({
        "schema": "motionwright.manim.graph-native-payload/1",
        "profile_id": SEMANTIC_GRAPH_PROFILE,
        "contract_sha256": GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
        "plan_sha256": super::source_digest(&document),
        "execution_snapshot": plan.execution_snapshot,
        "graph_id": plan.source.id, "scene_id": plan.source.scene_id,
        "clock": {
            "scene_frame_count": plan.scene_clock.frame_count,
            "project_start_frame": plan.scene_clock.project_start_frame,
            "graph_start_frame": exact_frame(plan.source.time_range.start, fps)?,
            "graph_frame_count": exact_frame(plan.source.time_range.duration, fps)?,
        },
        "render_profile": plan.render_profile,
        "scene_matte": plan.scene_matte.color,
        "motion": plan.source.motion, "style": plan.source.style,
        "vertices": vertices, "edges": edges, "events": projected_events,
        "text_blocks": text_blocks(plan)?,
        "typography": plan.typography_requirement(),
        "font_lock": graph_font_lock(), "helper_pins": graph_helper_pins(),
    }))
}

pub fn compile_graph_program_source_only(
    plan: &ManimSemanticGraphPlan,
) -> Result<PreparedGraphProgramV1> {
    let payload = graph_native_payload(plan)?;
    let plan_document = plan.source_document()?;
    let plan_sha256 = super::source_digest(&plan_document);
    let data =
        serde_json::to_string(&payload).map_err(|_| reject("graph_payload_json_encoding"))?;
    let quoted =
        serde_json::to_string(&data).map_err(|_| reject("graph_python_string_encoding"))?;
    let python_source = format!(
        "# Generated by {GRAPH_COMPILER_V1}\n\
         # Canonical Graph data remains editable; imported Python is inert.\n\
         _DATA = {quoted}\n\
         {}",
        concat!(
            include_str!("semantic_graph_helper_loader.py"),
            include_str!("semantic_graph_loader.py"),
        ),
    );
    if python_source.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(reject("graph_generated_source_budget"));
    }
    Ok(PreparedGraphProgramV1 {
        compiler_id: GRAPH_COMPILER_V1,
        plan_sha256,
        source_sha256: super::source_digest(python_source.as_bytes()),
        plan_document,
        python_source,
        native_execution_performed: false,
        runtime_sample_pts_verified: false,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct ManimGraphSourcePackageV1 {
    pub plan: ManimSemanticGraphPlan,
    pub entries: BTreeMap<String, Vec<u8>>,
    pub closure: ManimSourceClosureV1,
}

fn source_rows(entries: &BTreeMap<String, Vec<u8>>) -> Vec<ManimSourceEntryV1> {
    entries
        .iter()
        .map(|(path, bytes)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: bytes.len() as u64,
            sha256: super::source_digest(bytes),
        })
        .collect()
}

fn verify_assets(assets: &BTreeMap<String, Vec<u8>>) -> Result<()> {
    if assets.len() != BUILTIN_ASSETS.len() {
        return Err(reject("graph_builtin_asset_set"));
    }
    for pin in BUILTIN_ASSETS {
        let bytes = assets
            .get(pin.relative_path)
            .ok_or_else(|| reject("graph_builtin_asset_missing"))?;
        if bytes.len() as u64 != pin.size_bytes || super::source_digest(bytes) != pin.sha256 {
            return Err(reject("graph_builtin_asset_bytes_changed"));
        }
    }
    Ok(())
}

/// Inert bytes only: all font files and licenses must equal the pinned originals.
/// No extraction, converter, font installation, network or filesystem permission.
pub fn graph_source_package_v1(
    plan: &ManimSemanticGraphPlan,
    assets: &BTreeMap<String, Vec<u8>>,
) -> Result<ManimGraphSourcePackageV1> {
    verify_assets(assets)?;
    let prepared = compile_graph_program_source_only(plan)?;
    let config = super::source_json(&json!({
        "schema_version": 1, "profile_id": SEMANTIC_GRAPH_PROFILE,
        "compiler_id": GRAPH_COMPILER_V1, "scene_class": "MotionwrightScene",
        "manim_version_requirement": "0.21.0", "renderer": "cairo",
        "render_profile": plan.render_profile, "clock": plan.scene_clock,
        "camera_units": "one_native_unit_per_authored_pixel",
        "cairo_line_width_multiple": 1, "disable_caching": true,
        "output_role": "opaque_scene_video_without_audio_or_captions",
        "text_geometry_contract_sha256": GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
        "native_render_verified": false, "native_reopen_verified": false,
        "profile_admitted": false,
    }))?;
    let assets_lock = super::source_json(&json!({
        "schema_version": 1, "profile_id": SEMANTIC_GRAPH_PROFILE,
        "fonts": graph_font_lock(), "helper_modules": graph_helper_pins(),
        "derived_evidence_byte_limit": MAX_GRAPH_READBACK_BYTES,
        "derived_evidence_file_limit": MAX_GRAPH_READBACK_FILES,
        "native_dependency_admission": "REQUIRES_AUTHENTIC_CURRENT_OWNER_BINDING",
    }))?;
    let mut entries = BTreeMap::from([
        ("plan.json".into(), prepared.plan_document),
        ("scene.py".into(), prepared.python_source.into_bytes()),
        ("render-config.json".into(), config),
        ("assets.lock.json".into(), assets_lock),
    ]);
    for (module, bytes) in helper_sources() {
        entries.insert(
            format!("graph-profile-v1/helpers/{module}.py"),
            bytes.to_vec(),
        );
    }
    for (path, bytes) in assets {
        entries.insert(format!("graph-profile-v1/fonts/{path}"), bytes.clone());
    }
    if entries
        .values()
        .any(|bytes| bytes.is_empty() || bytes.len() > MAX_SOURCE_ENTRY_BYTES_V2)
    {
        return Err(reject("graph_source_entry_budget"));
    }
    let plan_sha256 = prepared.plan_sha256;
    let source_sha256 = prepared.source_sha256;
    let config_sha256 = super::source_digest(&entries["render-config.json"]);
    let assets_lock_sha256 = super::source_digest(&entries["assets.lock.json"]);
    let manifest = super::source_json(&json!({
        "schema_version": 1, "bundle_kind": "motionwright.manim.semantic_graph.editable_source",
        "mime_type": GRAPH_SOURCE_MIME_V1, "profile_id": SEMANTIC_GRAPH_PROFILE,
        "compiler_id": GRAPH_COMPILER_V1,
        "encoding_id": "serde-json-pretty-rational-strings-zip9-stored-v1",
        "plan_sha256": plan_sha256, "source_sha256": source_sha256,
        "config_sha256": config_sha256, "assets_lock_sha256": assets_lock_sha256,
        "entries": source_rows(&entries),
        "canonical_editable_source": "plan.json/source",
        "native_reopen": "same pinned Manim runtime, fixed helper bytes and bundled fonts",
        "import_policy": "typed plan validated and complete package regenerated; imported Python never executed",
        "unsupported": ["auto_layout", "text_wrap_or_fit", "font_fallback", "audio", "captions"],
        "native_render_verified": false, "native_reopen_verified": false,
        "canonical_import_applied": false, "profile_admitted": false,
    }))?;
    let manifest_sha256 = super::source_digest(&manifest);
    entries.insert("manifest.json".into(), manifest);
    let closure = ManimSourceClosureV1 {
        plan_schema_version: 1,
        compiler_contract_id: GRAPH_COMPILER_V1.into(),
        plan_sha256,
        source_sha256,
        config_sha256,
        assets_lock_sha256,
        manifest_sha256,
        entries: source_rows(&entries),
    };
    super::encode_source_entries(&entries)?;
    Ok(ManimGraphSourcePackageV1 {
        plan: plan.clone(),
        entries,
        closure,
    })
}

pub fn graph_source_bundle_v1(
    plan: &ManimSemanticGraphPlan,
    assets: &BTreeMap<String, Vec<u8>>,
) -> Result<Vec<u8>> {
    super::encode_source_entries(&graph_source_package_v1(plan, assets)?.entries)
}

pub fn inspect_graph_source_bundle_v1(bytes: &[u8]) -> Result<ManimGraphSourcePackageV1> {
    if bytes.is_empty() || bytes.len() > MAX_SOURCE_BUNDLE_BYTES_V2 {
        return Err(reject("graph_source_archive_budget"));
    }
    let mut allowed = BTreeSet::from([
        "assets.lock.json".to_owned(),
        "manifest.json".into(),
        "plan.json".into(),
        "render-config.json".into(),
        "scene.py".into(),
    ]);
    for (module, _) in helper_sources() {
        allowed.insert(format!("graph-profile-v1/helpers/{module}.py"));
    }
    for pin in BUILTIN_ASSETS {
        allowed.insert(format!("graph-profile-v1/fonts/{}", pin.relative_path));
    }
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|_| reject("graph_source_archive_invalid"))?;
    if archive.len() != allowed.len() || !archive.comment().is_empty() {
        return Err(reject("graph_source_archive_entry_set"));
    }
    let mut entries = BTreeMap::new();
    let mut total = 0_usize;
    for index in 0..archive.len() {
        let mut file = archive
            .by_index(index)
            .map_err(|_| reject("graph_source_archive_invalid"))?;
        let name = file
            .name()
            .map_err(|_| reject("graph_source_archive_name"))?
            .into_owned();
        if !allowed.contains(&name)
            || entries.contains_key(&name)
            || !file.is_file()
            || file.is_symlink()
            || file.encrypted()
            || file.compression() != zip::CompressionMethod::Stored
            || !file.comment().is_empty()
            || file.size() == 0
            || file.size() > MAX_SOURCE_ENTRY_BYTES_V2 as u64
        {
            return Err(reject("graph_source_archive_entry"));
        }
        let expected = file.size() as usize;
        total = total
            .checked_add(expected)
            .ok_or_else(|| reject("graph_source_archive_budget"))?;
        if total > MAX_SOURCE_BUNDLE_BYTES_V2 {
            return Err(reject("graph_source_archive_budget"));
        }
        let mut entry = Vec::with_capacity(expected);
        file.by_ref()
            .take(MAX_SOURCE_ENTRY_BYTES_V2 as u64 + 1)
            .read_to_end(&mut entry)
            .map_err(|_| reject("graph_source_archive_checksum"))?;
        if entry.len() != expected {
            return Err(reject("graph_source_archive_size"));
        }
        entries.insert(name, entry);
    }
    let plan = ManimSemanticGraphPlan::inspect_source_document(&entries["plan.json"])?;
    let assets = BUILTIN_ASSETS
        .iter()
        .map(|pin| {
            let key = format!("graph-profile-v1/fonts/{}", pin.relative_path);
            (pin.relative_path.to_owned(), entries[&key].clone())
        })
        .collect::<BTreeMap<_, _>>();
    let expected = graph_source_package_v1(&plan, &assets)?;
    if entries != expected.entries || super::encode_source_entries(&entries)? != bytes {
        return Err(reject(
            "graph_source_archive_projection_or_canonical_bytes_mismatch",
        ));
    }
    Ok(expected)
}

/// Context-only source rebinding never edits historical SemanticGraph authorship.
pub fn rebind_graph_source_v1(
    origin: &ManimSemanticGraphPlan,
    current: &ManimSemanticGraphPlan,
    assets: &BTreeMap<String, Vec<u8>>,
) -> Result<ManimGraphSourcePackageV1> {
    origin.validate()?;
    current.validate()?;
    if origin.execution_snapshot.project_id != current.execution_snapshot.project_id {
        return Err(reject("graph_source_cross_project_rebind"));
    }
    let mut rebound = origin.clone();
    rebound.execution_snapshot = current.execution_snapshot.clone();
    if rebound != *current {
        return Err(reject("graph_source_semantic_rebind_mismatch"));
    }
    graph_source_package_v1(current, assets)
}

#[cfg(test)]
#[path = "semantic_graph_source_tests.rs"]
mod tests;
