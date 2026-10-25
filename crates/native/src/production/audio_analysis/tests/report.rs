//! Synthetic source/receipt contract tests, with the existing proof factory.
//! No meter/native/media/listening observation is manufactured by these cases.
use super::*;

fn targets() -> DeliveryAudioTargets {
    DeliveryAudioTargets {
        target_lufs: Some(-23.0),
        target_true_peak_dbtp: Some(-1.0),
    }
}

fn report(decoded: &VerifiedAudioMeasurement) -> NativeResult<VerifiedDeliveryAudioQcReport> {
    let qc = evaluate_delivery_audio_qc(Some(&targets()), decoded)?;
    qc_report::build_report(&qc, decoded)
}

#[test]
fn report_binds_actual_decoded_interval_method_master_and_milli_units() {
    let decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1_000));
    let value = serde_json::to_value(report(&decoded).unwrap()).unwrap();
    assert_eq!(value["schema"], "motionwright.delivery-audio-qc-report/2");
    assert_eq!(value["qc"]["status"], "within_targets");
    assert_eq!(
        value["measurement_method"],
        json!({"engine":"libebur128","version":"1.2.6","producer_schema_version":1})
    );
    assert_eq!(
        value["decoded_input"]["sample_interval"],
        json!({"start_sample_frame":0,"end_sample_frame_exclusive":96_128})
    );
    assert_eq!(value["decoded_input"]["sample_rate_hz"], 48_000);
    assert_eq!(value["decoded_input"]["channels"], 2);
    assert_eq!(value["decoded_input"]["channel_layout"], "stereo");
    assert_eq!(value["decoded_input"]["format"], "wav_pcm_s16le");
    assert_eq!(value["decoded_input"]["sha256"], "2".repeat(64));
    assert_eq!(value["master_artifact_sha256"], "1".repeat(64));
    assert_eq!(value["measurement_windows"]["momentary_window_ms"], 400);
    assert_eq!(value["measurement_windows"]["short_term_window_ms"], 3000);
    assert_eq!(value["true_peak_oversample"], 4);
    assert_eq!(value["measured_levels"]["integrated_lufs_milli"], -23_000);
    assert_eq!(value["measured_levels"]["true_peak_millidbtp"], -1000);
    assert_eq!(value["units"]["integrated_lufs_milli"], "LUFS");
    assert_eq!(value["units"]["lufs_delta_milli"], "LU");
    assert_eq!(value["units"]["true_peak_millidbtp"], "dBTP");
    assert_eq!(value["units"]["sample_peak_millidbfs"], "dBFS");
    assert_eq!(value["published_resolution"]["numerator"], 1);
    assert_eq!(value["published_resolution"]["denominator"], 1000);
}

#[test]
fn source_measurement_cannot_be_projected_as_final_report() {
    let decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1_000));
    let qc = evaluate_delivery_audio_qc(Some(&targets()), &decoded).unwrap();
    let source = source_fixture().source_context();
    let proof = proof_from_receipt(source.clone(), &measurement_receipt(&source)).unwrap();
    assert!(qc_report::build_report(&qc, &proof).is_err());
}

#[test]
fn qc_from_another_decoded_parent_is_refused_even_when_levels_match() {
    let original = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    let qc = evaluate_delivery_audio_qc(Some(&targets()), &original).unwrap();
    let mut other = original.clone();
    other.context.native_parent.as_mut().unwrap().master.sha256 = "8".repeat(64);
    assert_eq!(
        other.loudness.integrated_lufs_milli,
        original.loudness.integrated_lufs_milli
    );
    assert!(qc_report::build_report(&qc, &other).is_err());
}

#[test]
fn stale_qc_metric_cannot_be_promoted_by_accurate_method_metadata() {
    let decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    let mut qc = evaluate_delivery_audio_qc(Some(&targets()), &decoded).unwrap();
    qc.measured_integrated_lufs_milli = Some(-18_000);
    assert!(qc_report::build_report(&qc, &decoded).is_err());
}

#[test]
fn report_reuses_existing_validator_for_version_rate_layout_windows_and_oversampling() {
    let original = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    for key in [
        "method",
        "version",
        "rate",
        "layout",
        "momentary",
        "short_term",
        "oversample",
    ] {
        let mut decoded = original.clone();
        match key {
            "method" => decoded.loudness.method = "sample-peak-is-not-loudness".into(),
            "version" => decoded.loudness.version = "1.2.5".into(),
            "rate" => decoded.loudness.sample_rate = 44_100,
            "layout" => decoded.loudness.layout = "mono".into(),
            "momentary" => decoded.loudness.momentary_window_ms = 399,
            "short_term" => decoded.loudness.short_term_window_ms = 2999,
            "oversample" => decoded.loudness.true_peak_oversample = 1,
            _ => unreachable!(),
        }
        assert!(report(&decoded).is_err(), "invalid {key} passed");
    }
}

#[test]
fn decoded_interval_is_not_trimmed_to_the_requested_source_frame_count() {
    let mut decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    decoded
        .context
        .native_parent
        .as_mut()
        .unwrap()
        .decoded_sample_frames = decoded.context.source.sample_frames;
    assert!(report(&decoded).is_err());
}

#[test]
fn report_rejects_json_unsafe_frame_interval_without_rounding() {
    let mut decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    decoded
        .context
        .native_parent
        .as_mut()
        .unwrap()
        .decoded_sample_frames = 9_007_199_254_740_992;
    assert!(report(&decoded).is_err());
}

#[test]
fn unavailable_lufs_true_peak_and_long_windows_remain_explicit_null() {
    let decoded = decoded_proof_with_metrics(None, None);
    let value = serde_json::to_value(report(&decoded).unwrap()).unwrap();
    assert_eq!(value["qc"]["status"], "unavailable");
    assert!(value["measured_levels"]["integrated_lufs_milli"].is_null());
    assert!(value["measured_levels"]["true_peak_millidbtp"].is_null());
    assert!(value["measured_levels"]["short_term_lufs_milli"].is_null());
    assert!(value["measured_levels"]["loudness_range_milli"].is_null());
    assert_eq!(
        value["decoded_input"]["sample_interval"]["end_sample_frame_exclusive"],
        96_128
    );
    assert_eq!(value["measurement_method"]["engine"], "libebur128");
}

#[test]
fn sample_peak_keeps_its_own_value_and_dbfs_unit() {
    let decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    let value = serde_json::to_value(report(&decoded).unwrap()).unwrap();
    assert_eq!(value["measured_levels"]["true_peak_millidbtp"], -1000);
    assert_eq!(value["measured_levels"]["sample_peak_millidbfs"], -10_000);
    assert_ne!(
        value["measured_levels"]["sample_peak_millidbfs"],
        value["measured_levels"]["true_peak_millidbtp"]
    );
    assert_ne!(
        value["units"]["sample_peak_millidbfs"],
        value["units"]["true_peak_millidbtp"]
    );
}

#[test]
fn report_preserves_durable_qc_one_exactly_and_changes_no_proof() {
    let decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    let qc = evaluate_delivery_audio_qc(Some(&targets()), &decoded).unwrap();
    let before_qc = serde_json::to_vec(&qc).unwrap();
    let before_proof = serde_json::to_vec(&decoded).unwrap();
    let value = serde_json::to_value(qc_report::build_report(&qc, &decoded).unwrap()).unwrap();
    assert_eq!(value["qc"], serde_json::to_value(&qc).unwrap());
    assert_eq!(serde_json::to_vec(&qc).unwrap(), before_qc);
    assert_eq!(serde_json::to_vec(&decoded).unwrap(), before_proof);
    assert_eq!(qc.schema, "motionwright.delivery-audio-qc/1");
}

#[test]
fn private_paths_receipts_and_freeform_reason_never_cross_public_report() {
    let mut decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    decoded.receipt.provenance["private_debug_marker"] =
        json!("PRIVATE_RAW_CONTEXT_MUST_STAY_LOCAL");
    decoded.loudness.unknown_reason = Some("/private/source/secret-reason.txt".into());
    let value = serde_json::to_value(report(&decoded).unwrap()).unwrap();
    let text = serde_json::to_string(&value).unwrap();
    for forbidden in [
        "PRIVATE_RAW_CONTEXT_MUST_STAY_LOCAL",
        "/private/source/secret-reason.txt",
        "relative_path",
        "broker_request_id",
        "connection_identity",
        "provenance",
        "source_plan_sha256",
    ] {
        assert!(
            !text.contains(forbidden),
            "public report leaked {forbidden}"
        );
    }
    let mut keys: Vec<_> = value
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort_unstable();
    assert_eq!(
        keys,
        vec![
            "decoded_input",
            "evidence_scope",
            "master_artifact_sha256",
            "measured_levels",
            "measurement_method",
            "measurement_windows",
            "published_resolution",
            "qc",
            "schema",
            "true_peak_oversample",
            "units"
        ]
    );
}

#[test]
fn no_requested_target_stays_not_requested_even_when_measurement_exists() {
    let decoded = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    let qc = evaluate_delivery_audio_qc(None, &decoded).unwrap();
    let value = serde_json::to_value(qc_report::build_report(&qc, &decoded).unwrap()).unwrap();
    assert_eq!(value["qc"]["status"], "not_requested");
    assert!(value["qc"]["targets"].is_null());
    assert!(value["qc"]["target_lufs_milli"].is_null());
    assert!(value["qc"]["target_true_peak_millidbtp"].is_null());
    assert_eq!(value["measured_levels"]["integrated_lufs_milli"], -23_000);
}

#[test]
fn missing_parent_and_invalid_master_digest_are_refused() {
    let original = decoded_proof_with_metrics(Some(-23_000), Some(-1000));
    let qc = evaluate_delivery_audio_qc(Some(&targets()), &original).unwrap();
    let mut missing = original.clone();
    missing.context.native_parent = None;
    assert!(qc_report::build_report(&qc, &missing).is_err());
    let mut invalid = original;
    invalid
        .context
        .native_parent
        .as_mut()
        .unwrap()
        .master
        .sha256 = "not-a-digest".into();
    assert!(report(&invalid).is_err());
}
