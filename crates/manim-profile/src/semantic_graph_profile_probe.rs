//! Fixed Graph runtime observations. No Graph, admission or mutation authority.
use crate::semantic_graph_source::{graph_builtin_asset_pins, graph_font_lock, graph_helper_pins};
use crate::semantic_graph_text::GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256;
use crate::{ManimProfileError, Result};
use motionwright_domain::SEMANTIC_GRAPH_PROFILE;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub const GRAPH_PROFILE_PROBE_SAMPLE: &str = "Motionwright Graph 0123456789";
pub const GRAPH_PROFILE_PROBE_FONT_SIZE: u32 = 24;
pub const MAX_GRAPH_PROFILE_PROBE_BYTES: usize = 32 * 1024;
const SCHEMA: &str = "motionwright.manim.fixed-graph-profile-observation/1";
const COVERAGE_SCOPE: &str = "fixed_ascii_sample_only";

fn reject(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphObservedAssetV1 {
    pub relative_path: String,
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphObservedHelperV1 {
    pub module: String,
    pub relative_path: String,
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphObservedFontRunV1 {
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
pub struct GraphObservedFontFamilyV1 {
    pub editorial_family: String,
    pub native_family: String,
    pub unknown_glyph_count: u32,
    pub pango_version: String,
    pub fontconfig_version: u32,
    pub runs: Vec<GraphObservedFontRunV1>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManimFixedGraphProfileObservationV1 {
    pub schema: String,
    pub profile_id: String,
    pub contract_sha256: String,
    pub manim_version: String,
    pub sample_text: String,
    pub sample_font_size_px: u32,
    pub coverage_scope: String,
    pub helper_pins: [GraphObservedHelperV1; 6],
    pub asset_pins: [GraphObservedAssetV1; 9],
    pub families: [GraphObservedFontFamilyV1; 2],
    pub future_label_coverage_verified: bool,
    pub render_performed: bool,
    pub scene_mutation_performed: bool,
    pub profile_admitted: bool,
}

/// Compiler-owned code and constants only: no scene/Graph input, arbitrary
/// text, request-selected family, Python, admission or permission.
pub fn fixed_graph_profile_probe_program_v1() -> Result<String> {
    let data = serde_json::to_string(&json!({
        "helper_pins": graph_helper_pins(),
        "font_lock": graph_font_lock(),
        "sample_text": GRAPH_PROFILE_PROBE_SAMPLE,
        "sample_font_size_px": GRAPH_PROFILE_PROBE_FONT_SIZE,
        "max_output_bytes": MAX_GRAPH_PROFILE_PROBE_BYTES,
    }))
    .map_err(|_| reject("graph_profile_probe_json_encoding"))?;
    let quoted =
        serde_json::to_string(&data).map_err(|_| reject("graph_profile_probe_python_encoding"))?;
    Ok(format!(
        "import sys\nsys.path[:] = [entry for entry in sys.path if entry]\n_DATA = {quoted}\n{}\n{}",
        include_str!("semantic_graph_helper_loader.py"),
        include_str!("semantic_graph_profile_probe.py"),
    ))
}

/// Validates bounded observations from the existing Host runner. This parser
/// does not establish that a caller actually used Host or holds a live lease.
pub fn parse_fixed_graph_profile_observation_v1(
    stdout: &[u8],
) -> Result<ManimFixedGraphProfileObservationV1> {
    if stdout.is_empty() || stdout.len() > MAX_GRAPH_PROFILE_PROBE_BYTES {
        return Err(reject("graph_profile_probe_output_budget"));
    }
    let value: ManimFixedGraphProfileObservationV1 =
        serde_json::from_slice(stdout).map_err(|_| reject("graph_profile_probe_output_shape"))?;
    if value.schema != SCHEMA
        || value.profile_id != SEMANTIC_GRAPH_PROFILE
        || value.contract_sha256 != GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256
        || value.manim_version != "0.21.0"
        || value.sample_text != GRAPH_PROFILE_PROBE_SAMPLE
        || value.sample_font_size_px != GRAPH_PROFILE_PROBE_FONT_SIZE
        || value.coverage_scope != COVERAGE_SCOPE
        || value.future_label_coverage_verified
        || value.render_performed
        || value.scene_mutation_performed
        || value.profile_admitted
    {
        return Err(reject("graph_profile_probe_contract"));
    }
    if serde_json::to_value(&value.helper_pins)
        .map_err(|_| reject("graph_profile_probe_helper_encoding"))?
        != Value::Array(graph_helper_pins())
        || serde_json::to_value(&value.asset_pins)
            .map_err(|_| reject("graph_profile_probe_asset_encoding"))?
            != serde_json::to_value(graph_builtin_asset_pins())
                .map_err(|_| reject("graph_profile_probe_pin_encoding"))?
    {
        return Err(reject("graph_profile_probe_fixed_closure"));
    }
    let lock = graph_font_lock();
    let packages = lock["packages"]
        .as_array()
        .ok_or_else(|| reject("graph_profile_probe_package_contract"))?;
    if packages.len() != value.families.len() {
        return Err(reject("graph_profile_probe_family_inventory"));
    }
    for (family, package) in value.families.iter().zip(packages) {
        if package["editorial_family"].as_str() != Some(family.editorial_family.as_str())
            || package["expected_native_family"].as_str() != Some(family.native_family.as_str())
            || family.unknown_glyph_count != 0
            || family.fontconfig_version == 0
            || family.pango_version.is_empty()
            || family.pango_version.len() > 80
            || family
                .pango_version
                .chars()
                .any(|ch| ch.is_control() || ch.is_whitespace())
            || family.runs.is_empty()
            || family.runs.len() > GRAPH_PROFILE_PROBE_SAMPLE.len()
        {
            return Err(reject("graph_profile_probe_family_observation"));
        }
        let allowed = package["font_files"]
            .as_array()
            .ok_or_else(|| reject("graph_profile_probe_font_contract"))?;
        let mut covered = [false; GRAPH_PROFILE_PROBE_SAMPLE.len()];
        for run in &family.runs {
            let end = run
                .offset
                .checked_add(run.length)
                .ok_or_else(|| reject("graph_profile_probe_run_overflow"))?;
            if run.family != family.native_family
                || run.weight != 400
                || run.style != 0
                || run.face_index != 0
                || run.length == 0
                || run.num_chars != run.length
                || end > covered.len()
                || !allowed.iter().any(|pin| {
                    pin["relative_path"].as_str() == Some(run.font_relative_path.as_str())
                        && pin["sha256"].as_str() == Some(run.font_sha256.as_str())
                })
            {
                return Err(reject("graph_profile_probe_run_binding"));
            }
            for seen in &mut covered[run.offset..end] {
                if *seen {
                    return Err(reject("graph_profile_probe_run_overlap"));
                }
                *seen = true;
            }
        }
        if covered.iter().any(|seen| !seen) {
            return Err(reject("graph_profile_probe_sample_not_fully_itemized"));
        }
    }
    Ok(value)
}

pub fn fixed_graph_profile_observation_schema_v1() -> Value {
    let run = json!({
        "type":"object", "additionalProperties":false,
        "properties":{
            "offset":{"type":"integer","minimum":0,"maximum":GRAPH_PROFILE_PROBE_SAMPLE.len()},
            "length":{"type":"integer","minimum":1,"maximum":GRAPH_PROFILE_PROBE_SAMPLE.len()},
            "num_chars":{"type":"integer","minimum":1,"maximum":GRAPH_PROFILE_PROBE_SAMPLE.len()},
            "family":{"enum":["Instrument Sans","IBM Plex Mono"]},
            "weight":{"const":400}, "style":{"const":0}, "face_index":{"const":0},
            "font_relative_path":{"type":"string","minLength":1,"maxLength":160},
            "font_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
        },
        "required":["offset","length","num_chars","family","weight","style","face_index","font_relative_path","font_sha256"],
    });
    let family = json!({
        "type":"object", "additionalProperties":false,
        "properties":{
            "editorial_family":{"enum":["Instrument Sans Variable","IBM Plex Mono"]},
            "native_family":{"enum":["Instrument Sans","IBM Plex Mono"]},
            "unknown_glyph_count":{"const":0},
            "pango_version":{"type":"string","minLength":1,"maxLength":80},
            "fontconfig_version":{"type":"integer","minimum":1,"maximum":u32::MAX},
            "runs":{"type":"array","minItems":1,"maxItems":GRAPH_PROFILE_PROBE_SAMPLE.len(),"items":run},
        },
        "required":["editorial_family","native_family","unknown_glyph_count","pango_version","fontconfig_version","runs"],
    });
    json!({
        "type":"object", "additionalProperties":false,
        "properties":{
            "schema":{"const":SCHEMA}, "profile_id":{"const":SEMANTIC_GRAPH_PROFILE},
            "contract_sha256":{"const":GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256},
            "manim_version":{"const":"0.21.0"},
            "sample_text":{"const":GRAPH_PROFILE_PROBE_SAMPLE},
            "sample_font_size_px":{"const":GRAPH_PROFILE_PROBE_FONT_SIZE},
            "coverage_scope":{"const":COVERAGE_SCOPE},
            "helper_pins":{"const":graph_helper_pins()},
            "asset_pins":{"const":graph_builtin_asset_pins()},
            "families":{"type":"array","minItems":2,"maxItems":2,"items":family},
            "future_label_coverage_verified":{"const":false},
            "render_performed":{"const":false}, "scene_mutation_performed":{"const":false},
            "profile_admitted":{"const":false},
        },
        "required":["schema","profile_id","contract_sha256","manim_version","sample_text","sample_font_size_px","coverage_scope","helper_pins","asset_pins","families","future_label_coverage_verified","render_performed","scene_mutation_performed","profile_admitted"],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // Parser-only mock. These bytes are never evidence of native execution.
    fn mock_observation() -> Value {
        let lock = graph_font_lock();
        let families = lock["packages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|package| {
                let font = package["font_files"].as_array().unwrap().last().unwrap();
                json!({
                    "editorial_family":package["editorial_family"],
                    "native_family":package["expected_native_family"],
                    "unknown_glyph_count":0, "pango_version":"mock-1", "fontconfig_version":1,
                    "runs":[{
                        "offset":0, "length":GRAPH_PROFILE_PROBE_SAMPLE.len(),
                        "num_chars":GRAPH_PROFILE_PROBE_SAMPLE.len(),
                        "family":package["expected_native_family"], "weight":400,
                        "style":0, "face_index":0,
                        "font_relative_path":font["relative_path"], "font_sha256":font["sha256"],
                    }],
                })
            })
            .collect::<Vec<_>>();
        json!({
            "schema":SCHEMA, "profile_id":SEMANTIC_GRAPH_PROFILE,
            "contract_sha256":GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
            "manim_version":"0.21.0", "sample_text":GRAPH_PROFILE_PROBE_SAMPLE,
            "sample_font_size_px":GRAPH_PROFILE_PROBE_FONT_SIZE,
            "coverage_scope":COVERAGE_SCOPE, "helper_pins":graph_helper_pins(),
            "asset_pins":graph_builtin_asset_pins(), "families":families,
            "future_label_coverage_verified":false, "render_performed":false,
            "scene_mutation_performed":false, "profile_admitted":false,
        })
    }

    fn accepted(value: &Value) -> bool {
        parse_fixed_graph_profile_observation_v1(&serde_json::to_vec(value).unwrap()).is_ok()
    }

    #[test]
    fn fixed_profile_parser_accepts_only_bounded_mock_observation_scope() {
        let value = mock_observation();
        let parsed =
            parse_fixed_graph_profile_observation_v1(&serde_json::to_vec(&value).unwrap()).unwrap();
        assert_eq!(parsed.families.len(), 2);
        assert_eq!(parsed.asset_pins.len(), 9);
        assert!(!parsed.profile_admitted && !parsed.future_label_coverage_verified);
    }

    #[test]
    fn fixed_profile_parser_rejects_changed_contract_or_promoted_claims() {
        for (path, change) in [
            ("/schema", json!("other")),
            ("/profile_id", json!("other")),
            ("/contract_sha256", json!("0".repeat(64))),
            ("/manim_version", json!("0.20.0")),
            ("/sample_text", json!("A future label")),
            ("/sample_font_size_px", json!(25)),
            ("/coverage_scope", json!("all_labels")),
            ("/future_label_coverage_verified", json!(true)),
            ("/render_performed", json!(true)),
            ("/scene_mutation_performed", json!(true)),
            ("/profile_admitted", json!(true)),
        ] {
            let mut value = mock_observation();
            *value.pointer_mut(path).unwrap() = change;
            assert!(!accepted(&value), "{path}");
        }
    }

    #[test]
    fn fixed_profile_parser_rejects_changed_or_incomplete_current_closure() {
        for path in ["/helper_pins/0/sha256", "/asset_pins/0/sha256"] {
            let mut value = mock_observation();
            *value.pointer_mut(path).unwrap() = json!("0".repeat(64));
            assert!(!accepted(&value), "{path}");
        }
        for name in ["helper_pins", "asset_pins", "families"] {
            let mut value = mock_observation();
            value[name].as_array_mut().unwrap().pop();
            assert!(!accepted(&value), "{name}");
        }
        let mut reordered = mock_observation();
        reordered["helper_pins"].as_array_mut().unwrap().swap(0, 1);
        assert!(!accepted(&reordered));
    }

    #[test]
    fn fixed_profile_parser_rejects_native_face_and_glyph_substitution() {
        for (path, change) in [
            ("/families/0/native_family", json!("Fallback")),
            ("/families/0/unknown_glyph_count", json!(1)),
            ("/families/0/fontconfig_version", json!(0)),
            ("/families/0/pango_version", json!("")),
            ("/families/0/runs/0/family", json!("IBM Plex Mono")),
            ("/families/0/runs/0/weight", json!(700)),
            ("/families/0/runs/0/style", json!(1)),
            ("/families/0/runs/0/face_index", json!(1)),
            (
                "/families/0/runs/0/font_relative_path",
                json!("instrument-sans/LICENSE"),
            ),
            ("/families/0/runs/0/font_sha256", json!("0".repeat(64))),
        ] {
            let mut value = mock_observation();
            *value.pointer_mut(path).unwrap() = change;
            assert!(!accepted(&value), "{path}");
        }
    }

    #[test]
    fn fixed_profile_parser_requires_exact_nonoverlapping_sample_coverage() {
        let mut overlap = mock_observation();
        let duplicate = overlap["families"][0]["runs"][0].clone();
        overlap["families"][0]["runs"]
            .as_array_mut()
            .unwrap()
            .push(duplicate);
        assert!(!accepted(&overlap));
        let mut gap = mock_observation();
        gap["families"][0]["runs"][0]["length"] = json!(GRAPH_PROFILE_PROBE_SAMPLE.len() - 1);
        gap["families"][0]["runs"][0]["num_chars"] = json!(GRAPH_PROFILE_PROBE_SAMPLE.len() - 1);
        assert!(!accepted(&gap));
        let mut overflow = mock_observation();
        overflow["families"][0]["runs"][0]["offset"] = json!(usize::MAX);
        assert!(!accepted(&overflow));
        let mut outside = mock_observation();
        outside["families"][0]["runs"][0]["offset"] = json!(1);
        assert!(!accepted(&outside));
    }

    #[test]
    fn fixed_profile_parser_rejects_oversized_malformed_duplicate_and_unknown_fields() {
        for raw in [
            Vec::new(),
            b"{".to_vec(),
            vec![b' '; MAX_GRAPH_PROFILE_PROBE_BYTES + 1],
        ] {
            assert!(parse_fixed_graph_profile_observation_v1(&raw).is_err());
        }
        let mut extra = mock_observation();
        extra["authority"] = json!("ready");
        assert!(!accepted(&extra));
        let mut nested = mock_observation();
        nested["families"][0]["runs"][0]["fallback_allowed"] = json!(false);
        assert!(!accepted(&nested));
        let encoded = serde_json::to_string(&mock_observation()).unwrap();
        let duplicate = encoded.replacen('{', "{\"schema\":\"duplicate\",", 1);
        assert!(parse_fixed_graph_profile_observation_v1(duplicate.as_bytes()).is_err());
    }
}
