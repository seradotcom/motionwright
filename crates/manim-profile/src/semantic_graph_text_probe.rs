//! Exact proposed-text observations through the existing closed Manim runner.
//! Requests and observations are data, never a profile admission or Store lease.
use crate::semantic_graph::ManimSemanticGraphPlan;
use crate::semantic_graph_profile_probe::{
    GRAPH_PROFILE_PROBE_FONT_SIZE, GRAPH_PROFILE_PROBE_SAMPLE, GraphObservedAssetV1,
    GraphObservedFontRunV1, GraphObservedHelperV1,
};
use crate::semantic_graph_source::{graph_builtin_asset_pins, graph_font_lock, graph_helper_pins};
use crate::semantic_graph_text::{GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256, text_blocks};
use crate::{ManimProfileError, Result};
use motionwright_domain::{
    GraphFontFamily, SEMANTIC_GRAPH_MAX_EDGES, SEMANTIC_GRAPH_MAX_EVENTS,
    SEMANTIC_GRAPH_MAX_VERTICES, SEMANTIC_GRAPH_PROFILE,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

// Existing Host stdin, runner capture, native Graph-label and Pango text bounds.
pub const MAX_GRAPH_TEXT_PROBE_INPUT_BYTES: usize = 64 * 1024;
pub const MAX_GRAPH_TEXT_PROBE_OUTPUT_BYTES: usize = 64 * 1024;
// Vertex labels + base edge intervals + two boundaries per event + disclosure.
const MAX_BLOCKS: usize =
    SEMANTIC_GRAPH_MAX_VERTICES + SEMANTIC_GRAPH_MAX_EDGES + 2 * SEMANTIC_GRAPH_MAX_EVENTS + 1;
const MAX_TEXT_BYTES: usize = 16 * 1024;
const REQUEST_SCHEMA: &str = "motionwright.manim.graph-text-probe-request/1";
const OBSERVATION_SCHEMA: &str = "motionwright.manim.graph-text-probe-observation/1";

fn reject(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn native_family(family: GraphFontFamily) -> &'static str {
    match family {
        GraphFontFamily::InstrumentSansVariable => "Instrument Sans",
        GraphFontFamily::IbmPlexMono => "IBM Plex Mono",
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct TextBlock {
    ordinal: u16,
    text: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphTextProbePurposeV1 {
    InspectAuthoring,
    Upsert,
}

/// Constructed from the typed source plan or parsed at the sealed runner boundary.
/// A valid value is still untrusted data and carries no native permission.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphTextProbeRequestV1 {
    schema: String,
    host_request_id: String,
    purpose: GraphTextProbePurposeV1,
    source_binding_sha256: String,
    editorial_family: GraphFontFamily,
    font_size_px: f64,
    blocks: Vec<TextBlock>,
}

impl GraphTextProbeRequestV1 {
    /// Fits the 30-second operation step of the owner's 45-second recipe:
    /// two sequential availability tools or one proposed-source tool.
    pub fn host_timeout(&self) -> std::time::Duration {
        std::time::Duration::from_secs(match self.purpose {
            GraphTextProbePurposeV1::InspectAuthoring => 10,
            GraphTextProbePurposeV1::Upsert => 20,
        })
    }

    /// Reserve the last second of the Host window for original terminal
    /// capture and parsing. The deadline cannot be supplied by operation JSON.
    pub fn child_timeout(&self) -> std::time::Duration {
        self.host_timeout() - std::time::Duration::from_secs(1)
    }

    fn validate(&self) -> Result<()> {
        if self.schema != REQUEST_SCHEMA
            || self.host_request_id.is_empty()
            || self.host_request_id.len() > 256
            || self.host_request_id.chars().any(char::is_control)
            || !digest(&self.source_binding_sha256)
            || !self.font_size_px.is_finite()
            || !(4.0..=512.0).contains(&self.font_size_px)
            || self.blocks.is_empty()
            || self.blocks.len() > MAX_BLOCKS
        {
            return Err(reject("graph_text_probe_request_contract"));
        }
        for (index, block) in self.blocks.iter().enumerate() {
            if usize::from(block.ordinal) != index
                || block.text.trim().is_empty()
                || block.text.len() > MAX_TEXT_BYTES
                || block
                    .text
                    .chars()
                    .any(|ch| (ch <= '\u{1f}' && ch != '\n') || ch == '\u{7f}')
            {
                return Err(reject("graph_text_probe_block_contract"));
            }
        }
        if self.purpose == GraphTextProbePurposeV1::InspectAuthoring
            && (self.font_size_px != f64::from(GRAPH_PROFILE_PROBE_FONT_SIZE)
                || self.blocks.len() != 1
                || self.blocks[0].text != GRAPH_PROFILE_PROBE_SAMPLE)
        {
            return Err(reject("graph_text_probe_authoring_control_only"));
        }
        Ok(())
    }

    /// Canonical bytes bind the caller, runner, original stdin and observation.
    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let bytes =
            serde_json::to_vec(self).map_err(|_| reject("graph_text_probe_request_encoding"))?;
        if bytes.len() > MAX_GRAPH_TEXT_PROBE_INPUT_BYTES {
            return Err(reject("graph_text_probe_input_budget"));
        }
        Ok(bytes)
    }

    pub fn request_sha256(&self) -> Result<String> {
        Ok(crate::source_digest(&self.encode()?))
    }

    /// One original Host invocation/purpose gets one custody reservation even
    /// if a conflicting retry changes its text, source plan or serialization.
    pub fn attempt_sha256(&self) -> Result<String> {
        self.validate()?;
        let identity = serde_json::to_vec(&(
            "motionwright.graph-text-probe-attempt/1",
            &self.host_request_id,
            self.purpose,
            (self.purpose == GraphTextProbePurposeV1::InspectAuthoring)
                .then_some(self.editorial_family),
        ))
        .map_err(|_| reject("graph_text_probe_attempt_encoding"))?;
        Ok(crate::source_digest(&identity))
    }
}

/// The mounted provider supplies the genuine Host request identity. Neither
/// this source-only builder nor the serialized identity establishes authority.
pub fn graph_text_probe_request_v1(
    plan: &ManimSemanticGraphPlan,
    host_request_id: &str,
    purpose: GraphTextProbePurposeV1,
) -> Result<GraphTextProbeRequestV1> {
    let blocks = text_blocks(plan)?
        .into_iter()
        .map(|block| TextBlock {
            ordinal: block.ordinal,
            text: block.text,
        })
        .collect();
    let request = GraphTextProbeRequestV1 {
        schema: REQUEST_SCHEMA.into(),
        host_request_id: host_request_id.into(),
        purpose,
        source_binding_sha256: crate::source_digest(&plan.source_document()?),
        editorial_family: plan.source.style.font_family,
        font_size_px: plan.source.style.font_size,
        blocks,
    };
    request.encode()?;
    Ok(request)
}

/// Two fixed availability controls for a real current authoring context. The
/// mounted provider computes the binding digest from its Project and request;
/// no fake Graph or source-plan document is created. Run the pair sequentially.
pub fn graph_authoring_context_probe_requests_v1(
    source_binding_sha256: &str,
    host_request_id: &str,
) -> Result<[GraphTextProbeRequestV1; 2]> {
    let requests = [
        GraphFontFamily::InstrumentSansVariable,
        GraphFontFamily::IbmPlexMono,
    ]
    .map(|family| GraphTextProbeRequestV1 {
        schema: REQUEST_SCHEMA.into(),
        host_request_id: host_request_id.into(),
        purpose: GraphTextProbePurposeV1::InspectAuthoring,
        source_binding_sha256: source_binding_sha256.into(),
        editorial_family: family,
        font_size_px: f64::from(GRAPH_PROFILE_PROBE_FONT_SIZE),
        blocks: vec![TextBlock {
            ordinal: 0,
            text: GRAPH_PROFILE_PROBE_SAMPLE.into(),
        }],
    });
    for request in &requests {
        request.encode()?;
    }
    Ok(requests)
}

pub fn parse_graph_text_probe_request_v1(bytes: &[u8]) -> Result<GraphTextProbeRequestV1> {
    if bytes.is_empty() || bytes.len() > MAX_GRAPH_TEXT_PROBE_INPUT_BYTES {
        return Err(reject("graph_text_probe_input_budget"));
    }
    let request: GraphTextProbeRequestV1 =
        serde_json::from_slice(bytes).map_err(|_| reject("graph_text_probe_request_shape"))?;
    request.encode()?;
    Ok(request)
}

/// Fixed code plus compiler constants only. Proposed text travels on stdin.
pub fn graph_text_probe_program_v1() -> Result<String> {
    let data = serde_json::to_string(&json!({
        "helper_pins": graph_helper_pins(), "font_lock": graph_font_lock(),
        "max_input_bytes": MAX_GRAPH_TEXT_PROBE_INPUT_BYTES,
        "max_output_bytes": MAX_GRAPH_TEXT_PROBE_OUTPUT_BYTES,
        "max_blocks": MAX_BLOCKS,
        "availability_sample": GRAPH_PROFILE_PROBE_SAMPLE,
        "availability_font_size_px": GRAPH_PROFILE_PROBE_FONT_SIZE,
    }))
    .map_err(|_| reject("graph_text_probe_constants_encoding"))?;
    let quoted =
        serde_json::to_string(&data).map_err(|_| reject("graph_text_probe_python_encoding"))?;
    // The existing loader imports Manim. Check the real module origin before it
    // can load an ambient installation; no request chooses a module or a path.
    Ok(format!(
        "import sys\nsys.path[:] = [entry for entry in sys.path if entry]\n\
         import importlib.util\nfrom pathlib import Path\n\
         if len(sys.argv) != 2:\n    raise ValueError('graph_text_probe_exact_arguments')\n\
         _packages = (Path(sys.argv[1]) / 'site-packages').resolve(strict=True)\n\
         _spec = importlib.util.find_spec('manim')\n\
         if _spec is None or _spec.origin is None:\n    raise ValueError('graph_text_probe_manim_absent')\n\
         Path(_spec.origin).resolve(strict=True).relative_to(_packages)\n\
         _DATA = {quoted}\n{}\n{}",
        include_str!("semantic_graph_helper_loader.py"),
        include_str!("semantic_graph_text_probe.py"),
    ))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphTextBlockObservationV1 {
    pub ordinal: u16,
    pub text_sha256: String,
    pub text_bytes: usize,
    pub unknown_glyph_count: u32,
    pub runs: Vec<GraphObservedFontRunV1>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManimGraphTextProbeObservationV1 {
    pub schema: String,
    pub host_request_id: String,
    pub purpose: GraphTextProbePurposeV1,
    pub profile_id: String,
    pub contract_sha256: String,
    pub source_binding_sha256: String,
    pub request_sha256: String,
    pub editorial_family: GraphFontFamily,
    pub native_family: String,
    pub font_size_px: f64,
    pub manim_version: String,
    pub pango_version: String,
    pub fontconfig_version: u32,
    pub helper_pins: [GraphObservedHelperV1; 6],
    pub asset_pins: [GraphObservedAssetV1; 9],
    pub blocks: Vec<GraphTextBlockObservationV1>,
    pub glyph_coverage_verified: bool,
    pub coverage_scope: String,
    pub future_label_coverage_verified: bool,
    pub box_fit_verified: bool,
    pub render_performed: bool,
    pub scene_mutation_performed: bool,
    pub profile_admitted: bool,
}

/// Validates exact bounded observations. Parsing alone never proves Host use.
pub fn parse_graph_text_probe_observation_v1(
    bytes: &[u8],
    request: &GraphTextProbeRequestV1,
) -> Result<ManimGraphTextProbeObservationV1> {
    request.validate()?;
    if bytes.is_empty() || bytes.len() > MAX_GRAPH_TEXT_PROBE_OUTPUT_BYTES {
        return Err(reject("graph_text_probe_output_budget"));
    }
    let value: ManimGraphTextProbeObservationV1 =
        serde_json::from_slice(bytes).map_err(|_| reject("graph_text_probe_observation_shape"))?;
    let expected_coverage = match request.purpose {
        GraphTextProbePurposeV1::InspectAuthoring => "fixed_ascii_control_only",
        GraphTextProbePurposeV1::Upsert => "exact_proposed_graph_text_only",
    };
    if value.schema != OBSERVATION_SCHEMA
        || value.host_request_id != request.host_request_id
        || value.purpose != request.purpose
        || value.profile_id != SEMANTIC_GRAPH_PROFILE
        || value.contract_sha256 != GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256
        || value.source_binding_sha256 != request.source_binding_sha256
        || value.request_sha256 != request.request_sha256()?
        || value.editorial_family != request.editorial_family
        || value.native_family != native_family(request.editorial_family)
        || value.font_size_px != request.font_size_px
        || value.manim_version != "0.21.0"
        || value.pango_version.is_empty()
        || value.pango_version.len() > 80
        || value
            .pango_version
            .chars()
            .any(|ch| ch.is_control() || ch.is_whitespace())
        || value.fontconfig_version == 0
        || value.blocks.len() != request.blocks.len()
        || !value.glyph_coverage_verified
        || value.coverage_scope != expected_coverage
        || value.future_label_coverage_verified
        || value.box_fit_verified
        || value.render_performed
        || value.scene_mutation_performed
        || value.profile_admitted
    {
        return Err(reject("graph_text_probe_observation_contract"));
    }
    if serde_json::to_value(&value.helper_pins)
        .map_err(|_| reject("graph_text_probe_helper_encoding"))?
        != Value::Array(graph_helper_pins())
        || serde_json::to_value(&value.asset_pins)
            .map_err(|_| reject("graph_text_probe_asset_encoding"))?
            != serde_json::to_value(graph_builtin_asset_pins())
                .map_err(|_| reject("graph_text_probe_asset_encoding"))?
    {
        return Err(reject("graph_text_probe_fixed_closure"));
    }
    let lock = graph_font_lock();
    let family = serde_json::to_value(request.editorial_family)
        .map_err(|_| reject("graph_text_probe_family_encoding"))?;
    let package = lock["packages"]
        .as_array()
        .and_then(|packages| {
            packages
                .iter()
                .find(|package| package["editorial_family"] == family)
        })
        .ok_or_else(|| reject("graph_text_probe_family_contract"))?;
    let fonts = package["font_files"]
        .as_array()
        .ok_or_else(|| reject("graph_text_probe_font_contract"))?;
    for (observed, source) in value.blocks.iter().zip(&request.blocks) {
        if observed.ordinal != source.ordinal
            || observed.text_sha256 != crate::source_digest(source.text.as_bytes())
            || observed.text_bytes != source.text.len()
            || observed.unknown_glyph_count != 0
            || observed.runs.is_empty()
            || observed.runs.len() > 4096
        {
            return Err(reject("graph_text_probe_block_observation"));
        }
        let mut covered = vec![false; source.text.len()];
        for run in &observed.runs {
            let end = run
                .offset
                .checked_add(run.length)
                .ok_or_else(|| reject("graph_text_probe_run_range"))?;
            if run.length == 0
                || end > source.text.len()
                || !source.text.is_char_boundary(run.offset)
                || !source.text.is_char_boundary(end)
                || run.family != native_family(request.editorial_family)
                || run.weight != 400
                || run.style != 0
                || run.face_index != 0
                || !fonts.iter().any(|font| {
                    font["relative_path"].as_str() == Some(run.font_relative_path.as_str())
                        && font["sha256"].as_str() == Some(run.font_sha256.as_str())
                })
                || source.text[run.offset..end].chars().count() != run.num_chars
                || covered[run.offset..end].iter().any(|seen| *seen)
            {
                return Err(reject("graph_text_probe_run_observation"));
            }
            covered[run.offset..end].fill(true);
        }
        if covered
            .iter()
            .zip(source.text.bytes())
            .any(|(seen, byte)| !*seen && byte != b'\n')
        {
            return Err(reject("graph_text_probe_incomplete_text"));
        }
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> GraphTextProbeRequestV1 {
        GraphTextProbeRequestV1 {
            schema: REQUEST_SCHEMA.into(),
            host_request_id: "fixture-original-host-call".into(),
            purpose: GraphTextProbePurposeV1::Upsert,
            source_binding_sha256: "a".repeat(64),
            editorial_family: GraphFontFamily::InstrumentSansVariable,
            font_size_px: 24.0,
            blocks: vec![TextBlock {
                ordinal: 0,
                text: "Á → B\nC".into(),
            }],
        }
    }

    // Pure parser fixture only; these bytes claim no runtime observation.
    fn mock_observation(request: &GraphTextProbeRequestV1) -> Value {
        let text = &request.blocks[0].text;
        let lock = graph_font_lock();
        let font = &lock["packages"][0]["font_files"][1];
        json!({
            "schema":OBSERVATION_SCHEMA,"host_request_id":request.host_request_id,
            "purpose":request.purpose,"profile_id":SEMANTIC_GRAPH_PROFILE,
            "contract_sha256":GRAPH_TEXT_GEOMETRY_CONTRACT_SHA256,
            "source_binding_sha256":request.source_binding_sha256,"request_sha256":request.request_sha256().unwrap(),
            "editorial_family":request.editorial_family,"native_family":"Instrument Sans",
            "font_size_px":24.0,"manim_version":"0.21.0","pango_version":"mock-1",
            "fontconfig_version":1,"helper_pins":graph_helper_pins(),"asset_pins":graph_builtin_asset_pins(),
            "blocks":[{"ordinal":0,"text_sha256":crate::source_digest(text.as_bytes()),
                "text_bytes":text.len(),"unknown_glyph_count":0,
                "runs":[{"offset":0,"length":text.len(),"num_chars":text.chars().count(),
                    "family":"Instrument Sans","weight":400,"style":0,"face_index":0,
                    "font_relative_path":font["relative_path"],"font_sha256":font["sha256"]}]}],
            "glyph_coverage_verified":true,"coverage_scope":"exact_proposed_graph_text_only",
            "future_label_coverage_verified":false,"box_fit_verified":false,"render_performed":false,
            "scene_mutation_performed":false,"profile_admitted":false,
        })
    }

    #[test]
    fn changed_data_and_serialization_cannot_remint_the_original_probe_attempt() {
        let original = request();
        let mut changed = original.clone();
        changed.source_binding_sha256 = "b".repeat(64);
        changed.blocks[0].text = "changed exact proposal".into();
        assert_eq!(
            original.attempt_sha256().unwrap(),
            changed.attempt_sha256().unwrap()
        );
        assert_ne!(
            original.request_sha256().unwrap(),
            changed.request_sha256().unwrap()
        );
        let pretty = serde_json::to_vec_pretty(&original).unwrap();
        assert_eq!(
            parse_graph_text_probe_request_v1(&pretty)
                .unwrap()
                .encode()
                .unwrap(),
            original.encode().unwrap()
        );
        changed.host_request_id = "independent-read-host-call".into();
        assert_ne!(
            original.attempt_sha256().unwrap(),
            changed.attempt_sha256().unwrap()
        );
    }

    #[test]
    fn proposal_data_cannot_select_runtime_code_paths_or_authority() {
        let original = serde_json::to_value(request()).unwrap();
        for key in [
            "python",
            "source",
            "root",
            "mount",
            "ready",
            "profile_admitted",
        ] {
            let mut value = original.clone();
            value[key] = json!("untrusted");
            assert!(
                parse_graph_text_probe_request_v1(&serde_json::to_vec(&value).unwrap()).is_err()
            );
        }
        for text in ["", "\t", "a\0b"] {
            let mut changed = request();
            changed.blocks[0].text = text.into();
            assert!(changed.encode().is_err());
        }
        let mut changed = request();
        changed.blocks[0].ordinal = 1;
        assert!(changed.encode().is_err());
        changed = request();
        changed.blocks[0].text = "x".repeat(MAX_TEXT_BYTES + 1);
        assert!(changed.encode().is_err());
    }

    #[test]
    fn authoring_availability_is_two_fixed_controls_without_a_graph() {
        let requests =
            graph_authoring_context_probe_requests_v1(&"a".repeat(64), "context-read-call")
                .unwrap();
        assert_eq!(requests[0].blocks[0].text, GRAPH_PROFILE_PROBE_SAMPLE);
        assert_eq!(requests[1].blocks[0].text, GRAPH_PROFILE_PROBE_SAMPLE);
        assert_ne!(
            requests[0].attempt_sha256().unwrap(),
            requests[1].attempt_sha256().unwrap()
        );
        let mut changed = requests[0].clone();
        changed.blocks[0].text = "arbitrary proposed label".into();
        assert!(changed.encode().is_err());
        changed = requests[0].clone();
        changed.font_size_px = 23.0;
        assert!(changed.encode().is_err());
        changed = requests[0].clone();
        changed.blocks.push(changed.blocks[0].clone());
        assert!(changed.encode().is_err());
    }

    #[test]
    fn purpose_selects_the_closed_outer_and_child_deadlines() {
        let availability =
            graph_authoring_context_probe_requests_v1(&"a".repeat(64), "context-read-call")
                .unwrap();
        for request in availability {
            assert_eq!(request.host_timeout(), std::time::Duration::from_secs(10));
            assert_eq!(request.child_timeout(), std::time::Duration::from_secs(9));
        }
        let proposed = request();
        assert_eq!(proposed.host_timeout(), std::time::Duration::from_secs(20));
        assert_eq!(proposed.child_timeout(), std::time::Duration::from_secs(19));
        let mut forged = serde_json::to_value(proposed).unwrap();
        forged["timeout_ms"] = json!(300000);
        assert!(parse_graph_text_probe_request_v1(&serde_json::to_vec(&forged).unwrap()).is_err());
    }

    #[test]
    fn exact_utf8_coverage_pins_and_original_request_are_required() {
        let request = request();
        let original = mock_observation(&request);
        assert!(
            parse_graph_text_probe_observation_v1(
                &serde_json::to_vec(&original).unwrap(),
                &request
            )
            .is_ok()
        );
        for (pointer, replacement) in [
            ("/request_sha256", json!("b".repeat(64))),
            ("/source_binding_sha256", json!("b".repeat(64))),
            ("/host_request_id", json!("foreign-call")),
            ("/profile_admitted", json!(true)),
            ("/blocks/0/unknown_glyph_count", json!(1)),
            ("/blocks/0/runs/0/font_sha256", json!("b".repeat(64))),
            ("/blocks/0/runs/0/offset", json!(1)),
            ("/blocks/0/runs/0/num_chars", json!(1)),
        ] {
            let mut value = original.clone();
            *value.pointer_mut(pointer).unwrap() = replacement;
            assert!(
                parse_graph_text_probe_observation_v1(
                    &serde_json::to_vec(&value).unwrap(),
                    &request
                )
                .is_err(),
                "{pointer}"
            );
        }
        let mut omitted = original;
        omitted["blocks"] = json!([]);
        assert!(
            parse_graph_text_probe_observation_v1(&serde_json::to_vec(&omitted).unwrap(), &request)
                .is_err()
        );
    }
}
