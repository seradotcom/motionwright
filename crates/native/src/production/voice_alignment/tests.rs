use super::evidence;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
fn fixture(kind: &str) -> Value {
    let b: &[u8] = match kind {
        "high" => include_bytes!("fixtures/high.envelope.json"),
        "low" => include_bytes!("fixtures/low.envelope.json"),
        "disagree" => include_bytes!("fixtures/disagree.envelope.json"),
        _ => panic!("unknown synthetic fixture"),
    };
    serde_json::from_slice(b).unwrap()
}
fn request() -> Value {
    serde_json::from_slice(include_bytes!("fixtures/request.json")).unwrap()
}
fn source() -> evidence::SourceEvidence {
    evidence::inspect_source(include_bytes!("fixtures/source.wav")).unwrap()
}
fn blob(b: &[u8], name: Option<&str>) -> Value {
    let mut v = json!({"encoding":"base64","data":STANDARD.encode(b),"sha256":evidence::digest(b),"size_bytes":b.len()});
    if let Some(n) = name {
        v["name"] = json!(n);
    }
    v
}
fn refresh_candidate(e: &mut Value) {
    let mut b = serde_json::to_vec(&e["candidate"]).unwrap();
    b.push(b'\n');
    e["candidate_file"] = blob(&b, Some("result.json"));
    e["worker_summary"]["result_sha256"] = json!(evidence::digest(&b));
}
fn refresh_execution(e: &mut Value) {
    let mut b = serde_json::to_vec(&e["execution"]).unwrap();
    b.push(b'\n');
    e["execution_file"] = blob(&b, Some("execution.json"));
    e["worker_summary"]["execution_sha256"] = json!(evidence::digest(&b));
}
fn replace_inline(e: &mut Value, name: &str, b: &[u8]) {
    e["inline_artifacts"][name] = blob(b, None);
    let d = json!({"sha256":evidence::digest(b),"size_bytes":b.len()});
    e["retained_artifacts"][name] = d.clone();
    e["candidate"]["artifacts"][name] = d;
    refresh_candidate(e);
}
fn assert_rejected(e: &Value) {
    assert!(evidence::validate(&request(), &source(), e).is_err());
}
#[test]
fn synthetic_oracle_recomputes_source_samples_and_complete_ctc_grid() {
    let e = fixture("high");
    let c = evidence::validate(&request(), &source(), &e).unwrap();
    assert_eq!(c.unresolved_word_count, 0);
    assert_eq!(c.words.len(), 2);
    assert_eq!(c.words[0].text, "A");
    assert_eq!(c.words[0].frames, Some((1, 3)));
    assert_eq!(c.words[1].text, "B");
    assert_eq!(c.words[1].frames, Some((6, 8)));
    assert_eq!(e["backend_authority"], false);
    assert_eq!(e["native_execution_attested"], false);
}
#[test]
fn synthetic_low_score_preserves_unknown_without_interval() {
    let c = evidence::validate(&request(), &source(), &fixture("low")).unwrap();
    assert_eq!(c.unresolved_word_count, 1);
    assert_eq!(c.words[0].frames, Some((1, 3)));
    assert_eq!(c.words[1].frames, None);
}
#[test]
fn synthetic_greedy_disagreement_preserves_all_unknown() {
    let c = evidence::validate(&request(), &source(), &fixture("disagree")).unwrap();
    assert_eq!(c.unresolved_word_count, 2);
    assert!(c.words.iter().all(|w| w.frames.is_none()));
}
#[test]
fn request_canonical_bytes_match_python_oracle() {
    let b = include_bytes!("fixtures/request.json");
    assert_eq!(
        evidence::canonical_request(&request()).unwrap(),
        b[..b.len() - 1]
    );
}
#[test]
fn same_duration_cannot_replace_source_hash() {
    let mut b = include_bytes!("fixtures/source.wav").to_vec();
    b[44] ^= 1;
    let s = evidence::inspect_source(&b).unwrap();
    assert!(evidence::validate(&request(), &s, &fixture("high")).is_err());
}
#[test]
fn zero_source_and_truncation_are_rejected() {
    let b = include_bytes!("fixtures/source.wav");
    let mut z = b.to_vec();
    z[44..].fill(0);
    assert!(evidence::inspect_source(&z).is_err());
    assert!(evidence::inspect_source(&b[..b.len() - 1]).is_err());
}
#[test]
fn source_rate_is_not_silently_resampled() {
    let mut b = include_bytes!("fixtures/source.wav").to_vec();
    b[24..28].copy_from_slice(&48000_u32.to_le_bytes());
    b[28..32].copy_from_slice(&96000_u32.to_le_bytes());
    assert!(evidence::inspect_source(&b).is_err());
}
#[test]
fn changed_generation_or_version_fails_after_resigning() {
    for (k, v) in [
        (
            "project_generation",
            json!("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ),
        ("transcript_version", json!(2)),
    ] {
        let mut e = fixture("high");
        e["candidate"]["binding"][k] = v;
        refresh_candidate(&mut e);
        assert_rejected(&e);
    }
}
#[test]
fn request_text_version_and_boolean_integer_rejected() {
    for v in [json!(2), json!(true)] {
        let mut r = request();
        r["transcript"]["version"] = v;
        assert!(evidence::validate(&r, &source(), &fixture("high")).is_err());
    }
    let mut r = request();
    r["transcript"]["text"] = json!("A C");
    r["transcript"]["sha256"] = json!(evidence::digest(b"A C"));
    assert!(evidence::validate(&r, &source(), &fixture("high")).is_err());
}
#[test]
fn missing_duplicate_or_reordered_words_rejected() {
    for case in 0..3 {
        let mut e = fixture("high");
        let w = e["candidate"]["words"].as_array_mut().unwrap();
        match case {
            0 => {
                w.pop();
            }
            1 => {
                w.push(w[0].clone());
            }
            _ => {
                w.swap(0, 1);
            }
        }
        refresh_candidate(&mut e);
        assert_rejected(&e);
    }
}
#[test]
fn interval_and_clock_changes_rejected_after_resigning() {
    let mut e = fixture("high");
    e["candidate"]["words"][0]["interval"]["start"]["numerator"] = json!(2);
    refresh_candidate(&mut e);
    assert_rejected(&e);
    let mut e = fixture("high");
    e["candidate"]["frame_clock"]["stride_samples"] = json!(321);
    refresh_candidate(&mut e);
    assert_rejected(&e);
}
#[test]
fn unknown_word_cannot_acquire_fabricated_interval() {
    let mut e = fixture("low");
    e["candidate"]["words"][1]["interval"] =
        fixture("high")["candidate"]["words"][1]["interval"].clone();
    refresh_candidate(&mut e);
    assert_rejected(&e);
}
#[test]
fn invented_confidence_and_self_reported_authority_rejected() {
    let mut e = fixture("high");
    e["candidate"]["words"][0]["confidence_millis"] = json!(990);
    refresh_candidate(&mut e);
    assert_rejected(&e);
    let mut e = fixture("high");
    e["backend_authority"] = json!(true);
    assert_rejected(&e);
}
#[test]
fn nan_emissions_rejected_after_all_hashes_updated() {
    let mut e = fixture("high");
    let n = "emissions.f32le";
    let mut b = STANDARD
        .decode(e["inline_artifacts"][n]["data"].as_str().unwrap())
        .unwrap();
    b[..4].copy_from_slice(&f32::NAN.to_le_bytes());
    replace_inline(&mut e, n, &b);
    assert_rejected(&e);
}
#[test]
fn invalid_path_and_selected_score_not_hidden_by_hashes() {
    for (n, replacement) in [
        ("ctc-path.i32le", (-1_i32).to_le_bytes()),
        ("ctc-logprobs.f32le", (-7.0_f32).to_le_bytes()),
    ] {
        let mut e = fixture("high");
        let mut b = STANDARD
            .decode(e["inline_artifacts"][n]["data"].as_str().unwrap())
            .unwrap();
        b[..4].copy_from_slice(&replacement);
        replace_inline(&mut e, n, &b);
        assert_rejected(&e);
    }
}
#[test]
fn changed_execution_version_rejected_after_new_hash() {
    let mut e = fixture("high");
    e["execution"]["observed_torchaudio_version"] = json!("2.9.0+cpu");
    refresh_execution(&mut e);
    assert_rejected(&e);
}
#[test]
fn duplicate_raw_keys_and_embedded_disagreement_rejected() {
    let mut e = fixture("high");
    let b = STANDARD
        .decode(e["candidate_file"]["data"].as_str().unwrap())
        .unwrap();
    assert_eq!(b[0], b'{');
    let mut d = b"{\"schema\":\"forged\",".to_vec();
    d.extend_from_slice(&b[1..]);
    e["candidate_file"] = blob(&d, Some("result.json"));
    e["worker_summary"]["result_sha256"] = json!(evidence::digest(&d));
    assert_rejected(&e);
    let mut e = fixture("high");
    e["candidate"]["words"][0]["mean_emission_probability"] = json!(0.9);
    assert_rejected(&e);
}
#[test]
fn raw_file_caps_apply_before_json_parsing() {
    for (k, n, size) in [
        ("candidate_file", "result.json", 32769),
        ("execution_file", "execution.json", 8193),
    ] {
        let mut e = fixture("high");
        e[k] = blob(&vec![b' '; size], Some(n));
        assert_eq!(
            evidence::validate(&request(), &source(), &e).err(),
            Some("integer outside profile")
        );
    }
}
#[test]
fn carrier_verifies_original_stdout_not_float_reserialization() {
    let b = include_bytes!("fixtures/high.envelope.json");
    let carrier = blob(b, None);
    let d = evidence::decode_runtime_output(&carrier).unwrap();
    assert_eq!(d.sha256, evidence::digest(b));
    assert_eq!(d.size_bytes, b.len());
    assert_eq!(d.envelope, fixture("high"));
    let mut changed = carrier;
    let mut altered = b.to_vec();
    altered[0] = b' ';
    changed["data"] = json!(STANDARD.encode(&altered));
    assert!(evidence::decode_runtime_output(&changed).is_err());
}
#[test]
fn carrier_cap_and_closed_descriptor_enforced() {
    assert!(evidence::decode_runtime_output(&blob(&vec![b' '; 245761], None)).is_err());
    let mut carrier = blob(include_bytes!("fixtures/high.envelope.json"), None);
    carrier["authority"] = json!(true);
    assert!(evidence::decode_runtime_output(&carrier).is_err());
}
