//! Synthetic G0 receipt/source fixtures only. No meter, Broker, encoder,
//! native renderer, or speech is run by these tests.
use super::*;
use semwright_native_sdk::types::InvocationProvenance;

fn source_fixture() -> SourceBinding {
    SourceBinding {
        schema: "motionwright.original-mix-analysis-source/1",
        caller_request_id: "synthetic-custody-case".into(),
        lane: AudioAnalysisLane::SingleSegment,
        project_id: Uuid::from_u128(1),
        project_resource: format!("project:{}", Uuid::from_u128(1)),
        generation: Uuid::from_u128(2),
        revision: 7,
        deliverable_id: Uuid::from_u128(3),
        connection_identity: "c".repeat(64),
        audio_inputs_sha256: "a".repeat(64),
        source_plan_sha256: "b".repeat(64),
        measured_voice_source: None,
        wav_sha256: "d".repeat(64),
        wav_bytes: 384_044,
        sample_frames: 96_000,
        sample_rate: 48_000,
        channels: 2,
    }
}

fn parent_fixture(source: &SourceBinding, decoded_frames: u64) -> NativeParent {
    NativeParent {
        receipt_id: Uuid::from_u128(4),
        request_id: format!(
            "{}{}",
            source.namespace().unwrap(),
            source.lane.mux_suffix()
        ),
        request_sha256: "e".repeat(64),
        result_sha256: "f".repeat(64),
        master: ArtifactIdentity {
            relative_path: expected_master_path(source).unwrap(),
            sha256: "1".repeat(64),
            bytes: 2048,
        },
        decoded: ArtifactIdentity {
            relative_path: "synthetic-decoded.wav".into(),
            sha256: "2".repeat(64),
            bytes: 44 + decoded_frames * 4,
        },
        decoded_sample_frames: decoded_frames,
        source_audio_sha256: source.wav_sha256.clone(),
        source_video_sha256: "3".repeat(64),
        video_frames: 60,
        fps_num: 30,
        fps_den: 1,
    }
}

fn response_fixture(context: &MeasurementContext) -> Value {
    let (sha, bytes, frames) = context.input().unwrap();
    let channel = json!({
        "peak_millidbfs": -10000, "rms_millidbfs": -20000,
        "dc_offset": 0.01, "finite_samples": frames,
        "nonfinite_samples": 0, "out_of_range_samples": 0,
    });
    json!({
        "artifact": {
            "file_name": context.file_name().unwrap(), "sha256": sha,
            "bytes": bytes, "format": "wav",
        },
        "loudness": {
            "schema_version": 1, "method": "libebur128", "version": "1.2.6",
            "frames": frames, "sample_rate": 48000, "channels": 2, "layout": "stereo",
            "nonfinite_samples": 0,
            "integrated_lufs_milli": if frames >= 19200 { Some(-23000) } else { None },
            "momentary_lufs_milli": if frames >= 19200 { Some(-23100) } else { None },
            "short_term_lufs_milli": if frames >= 144000 { Some(-23200) } else { None },
            "loudness_range_milli": if frames >= 144000 { Some(2000) } else { None },
            "true_peak_millidbtp": -9000, "sample_peak_millidbfs": -10000,
            "true_peak_oversample": 4, "momentary_window_ms": 400,
            "short_term_window_ms": 3000,
            "unknown_reason": if frames < 144000 {
                Some("one_or_more_windows_have_insufficient_frames")
            } else { None },
        },
        "pcm_statistics": {
            "schema_version": 2, "method": "semwright-pcm-statistics-v2",
            "sample_rate": 48000, "frames": frames,
            "channels": [channel.clone(), channel],
            "peak_millidbfs": -10000, "rms_millidbfs": -20000,
            "silence_threshold_millidbfs": -90000,
            "silence_ranges": [], "silence_ranges_exhaustive": true,
            "entirely_silent": false, "nonfinite_samples": 0,
            "out_of_range_samples": 0,
        },
        "exhaustive": true,
    })
}

fn receipt_fixture(
    source: &SourceBinding,
    request_id: &str,
    command: &str,
    request_sha256: String,
    data: Value,
    provider: &str,
) -> ProductionReceipt {
    let execution = Execution {
        backend: provider.into(),
        duration_ms: 1,
        policy_decision: "allow".into(),
        fallbacks_attempted: vec![],
        provenance: Some(InvocationProvenance {
            provider: provider.into(),
            source: SourceKind::Driver,
            provider_version: "synthetic-g0-no-native-runtime".into(),
            capability_version: "1".into(),
            descriptor_sha256: "a1".repeat(32),
            untrusted_metadata: false,
            catalog_revision: 1,
            execution_provider: Some(provider.into()),
            provider_generation: Some(2),
        }),
        dry_run: false,
    };
    ProductionReceipt {
        id: Uuid::new_v4(),
        project_id: source.project_id,
        generation: source.generation,
        revision: source.revision,
        request_id: request_id.into(),
        request_sha256,
        command: command.into(),
        stage: "completed".into(),
        payload: json!({
            "result": {
                "data": data, "execution": execution, "warnings": [],
                "provenance_checked": true,
            },
            "broker_request_id": format!("synthetic-g0-{request_id}"),
            "job_ref": null,
        }),
        created_at: "2026-10-21T00:00:00Z".into(),
    }
}

fn measurement_receipt(context: &MeasurementContext) -> ProductionReceipt {
    receipt_fixture(
        &context.source,
        &context.request_id().unwrap(),
        MEASURE_COMMAND,
        request_digest(MEASURE_COMMAND, &context.args().unwrap()).unwrap(),
        response_fixture(context),
        "driver:audio-analysis",
    )
}

#[test]
fn context_keeps_every_sha_bit_within_existing_namespace_and_commits_authority() {
    let source = source_fixture();
    let namespace = source.namespace().unwrap();
    assert_eq!(namespace.len(), 54);
    assert!(namespace.bytes().all(|c| c.is_ascii_alphanumeric()));
    // Independently calculated with Python hashlib+RFC4648 base32 over this
    // exact ordered serde JSON fixture. This is not a media measurement.
    assert_eq!(
        namespace,
        "manu2c4a3lsiild5oyksigc543vyjiveziseaobmaim7p5baysmqaq"
    );
    let mut variants = Vec::new();
    let mut changed = source.clone();
    changed.revision += 1;
    variants.push(changed);
    let mut changed = source.clone();
    changed.generation = Uuid::from_u128(9);
    variants.push(changed);
    let mut changed = source.clone();
    changed.deliverable_id = Uuid::from_u128(9);
    variants.push(changed);
    let mut changed = source.clone();
    changed.connection_identity = "9".repeat(64);
    variants.push(changed);
    let mut changed = source.clone();
    changed.audio_inputs_sha256 = "9".repeat(64);
    variants.push(changed);
    let mut changed = source.clone();
    changed.source_plan_sha256 = "9".repeat(64);
    variants.push(changed);
    let mut changed = source.clone();
    changed.wav_sha256 = "9".repeat(64);
    variants.push(changed);
    let mut changed = source.clone();
    changed.lane = AudioAnalysisLane::Multisegment;
    variants.push(changed);
    for variant in variants {
        assert_ne!(namespace, variant.namespace().unwrap());
    }
}

#[test]
fn closed_response_rejects_wrong_artifact_clock_meter_and_missing_availability() {
    let context = source_fixture().source_context();
    let valid = response_fixture(&context);
    assert!(validate_analysis(&context, &valid).is_ok());
    for (pointer, value) in [
        ("/artifact/file_name", json!("foreign.wav")),
        ("/artifact/sha256", json!("0".repeat(64))),
        ("/artifact/bytes", json!(384043)),
        ("/artifact/format", json!("flac")),
        ("/exhaustive", json!(false)),
        ("/loudness/schema_version", json!(2)),
        ("/loudness/method", json!("rms-is-not-lufs")),
        ("/loudness/version", json!("unfixed")),
        ("/loudness/sample_rate", json!(44100)),
        ("/loudness/channels", json!(1)),
        ("/loudness/layout", json!("mono")),
        ("/loudness/frames", json!(96001)),
        ("/loudness/true_peak_oversample", json!(1)),
    ] {
        let mut bad = valid.clone();
        *bad.pointer_mut(pointer).unwrap() = value;
        assert!(validate_analysis(&context, &bad).is_err(), "{pointer}");
    }
    let mut missing = valid.clone();
    missing["loudness"]
        .as_object_mut()
        .unwrap()
        .remove("integrated_lufs_milli");
    assert!(validate_analysis(&context, &missing).is_err());
    let mut unknown = valid.clone();
    unknown["invented_mastering_approval"] = json!(true);
    assert!(validate_analysis(&context, &unknown).is_err());
}

#[test]
fn wav_statistics_require_exact_public_schema_clock_and_pcm16_counts() {
    let context = source_fixture().source_context();
    let valid = response_fixture(&context);
    for (pointer, value) in [
        ("/pcm_statistics", Value::Null),
        ("/pcm_statistics/schema_version", json!(1)),
        ("/pcm_statistics/method", json!("other")),
        ("/pcm_statistics/sample_rate", json!(44100)),
        ("/pcm_statistics/frames", json!(96001)),
        ("/pcm_statistics/nonfinite_samples", json!(1)),
        ("/pcm_statistics/out_of_range_samples", json!(1)),
        ("/pcm_statistics/channels/0/finite_samples", json!(95999)),
        ("/pcm_statistics/channels/0/nonfinite_samples", json!(1)),
        ("/pcm_statistics/channels/0/out_of_range_samples", json!(1)),
        ("/pcm_statistics/channels/0/dc_offset", Value::Null),
        ("/pcm_statistics/entirely_silent", json!(true)),
        (
            "/pcm_statistics/silence_ranges",
            json!([{"start_frame": 10, "end_frame": 96001}]),
        ),
    ] {
        let mut bad = valid.clone();
        *bad.pointer_mut(pointer).unwrap() = value;
        assert!(validate_analysis(&context, &bad).is_err(), "{pointer}");
    }
    let mut partial_ranges = valid.clone();
    partial_ranges["pcm_statistics"]["silence_ranges_exhaustive"] = json!(false);
    let (_, statistics) = validate_analysis(&context, &partial_ranges).unwrap();
    assert!(!statistics.silence_ranges_exhaustive);
}

#[test]
fn null_metrics_and_exact_short_window_boundaries_are_preserved_without_target_claims() {
    for frames in [4800_u64, 19199, 19200, 48000, 143999, 144000] {
        let mut context = source_fixture().source_context();
        context.source.sample_frames = frames;
        context.source.wav_bytes = 44 + frames * 4;
        let data = response_fixture(&context);
        let (loudness, _) = validate_analysis(&context, &data).unwrap();
        assert_eq!(loudness.integrated_lufs_milli.is_some(), frames >= 19200);
        assert_eq!(loudness.short_term_lufs_milli.is_some(), frames >= 144000);
        if frames < 144000 {
            let mut false_window = data.clone();
            false_window["loudness"]["short_term_lufs_milli"] = json!(-23000);
            assert!(validate_analysis(&context, &false_window).is_err());
        }
    }
    let mut long_context = source_fixture().source_context();
    long_context.source.sample_frames = 144000;
    long_context.source.wav_bytes = 576044;
    let mut unavailable = response_fixture(&long_context);
    for key in [
        "integrated_lufs_milli",
        "momentary_lufs_milli",
        "short_term_lufs_milli",
    ] {
        unavailable["loudness"][key] = Value::Null;
    }
    unavailable["loudness"]["unknown_reason"] = Value::Null;
    let (loudness, _) = validate_analysis(&long_context, &unavailable).unwrap();
    assert_eq!(loudness.integrated_lufs_milli, None);
    assert_eq!(loudness.unknown_reason, None);
}

#[test]
fn completed_measurement_rejects_foreign_receipts_and_nonexecuted_authority() {
    let context = source_fixture().source_context();
    let valid = measurement_receipt(&context);
    assert!(proof_from_receipt(context.clone(), &valid).is_ok());
    let mut cases = Vec::new();
    let mut r = valid.clone();
    r.project_id = Uuid::from_u128(88);
    cases.push(r);
    let mut r = valid.clone();
    r.generation = Uuid::from_u128(88);
    cases.push(r);
    let mut r = valid.clone();
    r.revision += 1;
    cases.push(r);
    let mut r = valid.clone();
    r.request_id.push_str("-foreign");
    cases.push(r);
    let mut r = valid.clone();
    r.request_sha256 = "9".repeat(64);
    cases.push(r);
    let mut r = valid.clone();
    r.command = MUX_COMMAND.into();
    cases.push(r);
    let mut r = valid.clone();
    r.stage = "outcome_unknown".into();
    cases.push(r);
    let mut r = valid.clone();
    r.payload["result"]["provenance_checked"] = json!(false);
    cases.push(r);
    let mut r = valid.clone();
    r.payload["result"]["execution"]["dry_run"] = json!(true);
    cases.push(r);
    let mut r = valid.clone();
    r.payload["result"]["execution"]["provenance"]["provider"] = json!("driver:other");
    cases.push(r);
    let mut r = valid.clone();
    r.payload["result"]["execution"]["provenance"]["provider_generation"] = Value::Null;
    cases.push(r);
    let mut r = valid.clone();
    r.payload["result"]["execution"]["provenance"]["source"] = json!("builtin");
    cases.push(r);
    for receipt in cases {
        assert!(proof_from_receipt(context.clone(), &receipt).is_err());
    }
}

#[test]
fn identical_waveforms_do_not_allow_source_measurement_to_substitute_for_decoded() {
    let source = source_fixture();
    let context = source.source_context();
    let receipt = measurement_receipt(&context);
    let mut parent = parent_fixture(&source, source.sample_frames);
    parent.decoded.sha256 = source.wav_sha256.clone();
    parent.decoded.bytes = source.wav_bytes;
    let decoded = MeasurementContext {
        schema: "motionwright.audio-measurement-context/1",
        source,
        phase: MeasurementPhase::DecodedMltMaster,
        native_parent: Some(parent),
    };
    assert_ne!(context.file_name().unwrap(), decoded.file_name().unwrap());
    assert_ne!(context.request_id().unwrap(), decoded.request_id().unwrap());
    assert!(proof_from_receipt(decoded, &receipt).is_err());
}

#[test]
fn final_measurement_keeps_actual_decoded_count_instead_of_source_count() {
    let source = source_fixture();
    let context = MeasurementContext {
        schema: "motionwright.audio-measurement-context/1",
        native_parent: Some(parent_fixture(&source, 96128)),
        source,
        phase: MeasurementPhase::DecodedMltMaster,
    };
    let valid = response_fixture(&context);
    assert_eq!(validate_analysis(&context, &valid).unwrap().0.frames, 96128);
    let mut trimmed = valid;
    trimmed["loudness"]["frames"] = json!(96000);
    trimmed["pcm_statistics"]["frames"] = json!(96000);
    assert!(validate_analysis(&context, &trimmed).is_err());
}

#[test]
fn exact_mute_silence_remains_null_loudness_and_is_not_a_passing_target() {
    let source = source_fixture();
    let context = MeasurementContext {
        schema: "motionwright.audio-measurement-context/1",
        native_parent: Some(parent_fixture(&source, source.sample_frames)),
        source,
        phase: MeasurementPhase::DecodedMltMaster,
    };
    let mut silent = response_fixture(&context);
    for key in [
        "integrated_lufs_milli",
        "momentary_lufs_milli",
        "short_term_lufs_milli",
        "loudness_range_milli",
        "true_peak_millidbtp",
        "sample_peak_millidbfs",
    ] {
        silent["loudness"][key] = Value::Null;
    }
    silent["loudness"]["unknown_reason"] = json!("silence");
    silent["pcm_statistics"]["entirely_silent"] = json!(true);
    for key in ["peak_millidbfs", "rms_millidbfs"] {
        silent["pcm_statistics"][key] = Value::Null;
        for index in 0..2 {
            silent["pcm_statistics"]["channels"][index][key] = Value::Null;
        }
    }
    for index in 0..2 {
        silent["pcm_statistics"]["channels"][index]["dc_offset"] = json!(0.0);
    }
    let (loudness, statistics) = validate_analysis(&context, &silent).unwrap();
    assert_eq!(loudness.integrated_lufs_milli, None);
    assert_eq!(loudness.true_peak_millidbtp, None);
    assert!(statistics.entirely_silent);
    let mut receipt = measurement_receipt(&context);
    receipt.payload["result"]["data"] = silent;
    let proof = proof_from_receipt(context, &receipt).unwrap();
    let targets = DeliveryAudioTargets {
        target_lufs: Some(-23.0),
        target_true_peak_dbtp: Some(-1.0),
    };
    assert_eq!(
        evaluate_delivery_audio_qc(Some(&targets), &proof)
            .unwrap()
            .status(),
        DeliveryAudioQcStatus::Unavailable
    );
    assert_eq!(
        evaluate_delivery_audio_qc(None, &proof).unwrap().status(),
        DeliveryAudioQcStatus::NotRequested
    );
}

#[test]
fn staged_inputs_are_exact_create_new_and_conflicts_preserve_original_bytes() {
    let root = tempfile::tempdir().unwrap();
    let bytes = b"synthetic staging custody bytes, not audio";
    let mut context = source_fixture().source_context();
    context.source.wav_sha256 = hex::encode(Sha256::digest(bytes));
    context.source.wav_bytes = bytes.len() as u64;
    stage_exact_input(root.path(), &context, bytes).unwrap();
    stage_exact_input(root.path(), &context, bytes).unwrap();
    let path = root.path().join(context.file_name().unwrap());
    assert_eq!(fs::read(&path).unwrap(), bytes);
    fs::write(&path, b"partial original attempt").unwrap();
    assert!(stage_exact_input(root.path(), &context, bytes).is_err());
    assert_eq!(fs::read(&path).unwrap(), b"partial original attempt");
}

#[cfg(unix)]
#[test]
fn staged_inputs_refuse_symlinks_and_hardlinks_before_public_meter_dispatch() {
    use std::os::unix::fs::symlink;
    let root = tempfile::tempdir().unwrap();
    let bytes = b"synthetic linked custody bytes";
    let mut context = source_fixture().source_context();
    context.source.wav_sha256 = hex::encode(Sha256::digest(bytes));
    context.source.wav_bytes = bytes.len() as u64;
    let target = root.path().join("owner-source.wav");
    fs::write(&target, bytes).unwrap();
    let stage = root.path().join(context.file_name().unwrap());
    symlink(&target, &stage).unwrap();
    assert!(stage_exact_input(root.path(), &context, bytes).is_err());
    fs::remove_file(&stage).unwrap();
    fs::hard_link(&target, &stage).unwrap();
    assert!(stage_exact_input(root.path(), &context, bytes).is_err());
    assert_eq!(fs::read(&target).unwrap(), bytes);
}

#[test]
fn shared_mux_file_check_rejects_false_bytecount_even_when_sha_is_correct() {
    let root = tempfile::tempdir().unwrap();
    let bytes = b"synthetic decoded WAV custody, no codec executed";
    fs::write(root.path().join("decoded.wav"), bytes).unwrap();
    let artifact = json!({
        "root": "output", "path": "decoded.wav",
        "sha256": hex::encode(Sha256::digest(bytes)), "bytes": bytes.len(),
    });
    super::super::mlt_multisegment_av::verify_mux_artifact_file(root.path(), &artifact).unwrap();
    for declared in [bytes.len() - 1, bytes.len() + 1] {
        let mut bad = artifact.clone();
        bad["bytes"] = json!(declared);
        assert!(
            super::super::mlt_multisegment_av::verify_mux_artifact_file(root.path(), &bad).is_err()
        );
    }
}

fn decoded_proof_with_metrics(lufs: Option<i32>, peak: Option<i32>) -> VerifiedAudioMeasurement {
    let source = source_fixture();
    let context = MeasurementContext {
        schema: "motionwright.audio-measurement-context/1",
        native_parent: Some(parent_fixture(&source, 96_128)),
        source,
        phase: MeasurementPhase::DecodedMltMaster,
    };
    let mut receipt = measurement_receipt(&context);
    receipt.payload["result"]["data"]["loudness"]["integrated_lufs_milli"] = json!(lufs);
    receipt.payload["result"]["data"]["loudness"]["true_peak_millidbtp"] = json!(peak);
    proof_from_receipt(context, &receipt).unwrap()
}

#[test]
fn qc_lufs_199_200_201_milli_boundaries_are_inclusive_and_use_wide_exact_subtraction() {
    use DeliveryAudioQcStatus::{OutsideTargets, WithinTargets};
    let targets = DeliveryAudioTargets {
        target_lufs: Some(-23.0),
        target_true_peak_dbtp: None,
    };
    for delta in [-201_i32, -200, -199, 0, 199, 200, 201] {
        let proof = decoded_proof_with_metrics(Some(-23_000 + delta), Some(-9_000));
        let qc = evaluate_delivery_audio_qc(Some(&targets), &proof).unwrap();
        assert_eq!(qc.lufs_delta_milli, Some(i64::from(delta)));
        assert_eq!(
            qc.status(),
            if delta.abs() <= 200 {
                WithinTargets
            } else {
                OutsideTargets
            }
        );
        assert_eq!(qc.true_peak_status(), DeliveryAudioQcStatus::NotRequested);
    }
    for target in [i32::MIN, i32::MAX] {
        let targets = DeliveryAudioTargets {
            target_lufs: Some(f64::from(target) / 1000.0),
            target_true_peak_dbtp: None,
        };
        let proof = decoded_proof_with_metrics(Some(-23_000), None);
        let qc = evaluate_delivery_audio_qc(Some(&targets), &proof).unwrap();
        assert_eq!(qc.lufs_delta_milli, Some(-23_000_i64 - i64::from(target)));
        assert_eq!(qc.status(), OutsideTargets);
    }
}

#[test]
fn qc_true_peak_ceiling_uses_exact_public_milli_with_no_epsilon() {
    let targets = DeliveryAudioTargets {
        target_lufs: None,
        target_true_peak_dbtp: Some(-1.0),
    };
    for peak in [-1_001, -1_000, -999] {
        let proof = decoded_proof_with_metrics(None, Some(peak));
        let qc = evaluate_delivery_audio_qc(Some(&targets), &proof).unwrap();
        assert_eq!(qc.target_true_peak_millidbtp, Some(-1_000));
        assert_eq!(
            qc.status(),
            if peak <= -1_000 {
                DeliveryAudioQcStatus::WithinTargets
            } else {
                DeliveryAudioQcStatus::OutsideTargets
            }
        );
        assert_eq!(qc.lufs_status(), DeliveryAudioQcStatus::NotRequested);
    }
}

#[test]
fn qc_null_metrics_are_unavailable_and_missing_or_unrequested_values_never_mint_a_pass() {
    use DeliveryAudioQcStatus::{NotRequested, Unavailable, WithinTargets};
    let targets = DeliveryAudioTargets {
        target_lufs: Some(-23.0),
        target_true_peak_dbtp: Some(-1.0),
    };
    for (lufs, peak) in [(None, Some(-1000)), (Some(-23000), None), (None, None)] {
        let proof = decoded_proof_with_metrics(lufs, peak);
        let qc = evaluate_delivery_audio_qc(Some(&targets), &proof).unwrap();
        assert_eq!(qc.status(), Unavailable);
        assert_eq!(
            qc.lufs_status(),
            if lufs.is_none() {
                Unavailable
            } else {
                WithinTargets
            }
        );
        assert_eq!(
            qc.true_peak_status(),
            if peak.is_none() {
                Unavailable
            } else {
                WithinTargets
            }
        );
        assert_eq!(
            evaluate_delivery_audio_qc(None, &proof).unwrap().status(),
            NotRequested
        );
    }
    let context = source_fixture().source_context();
    let mut receipt = measurement_receipt(&context);
    receipt.payload["result"]["data"]["loudness"]
        .as_object_mut()
        .unwrap()
        .remove("integrated_lufs_milli");
    assert!(proof_from_receipt(context, &receipt).is_err());
    // A missing public availability field is malformed evidence, so it cannot
    // produce a proof. The UI presents unavailable instead of manufacturing QC.
    let observed = decoded_proof_with_metrics(Some(-23_000), Some(-1_000));
    assert_eq!(
        evaluate_delivery_audio_qc(None, &observed)
            .unwrap()
            .status(),
        NotRequested
    );
}

#[test]
fn qc_requires_final_phase_and_cannot_substitute_a_passing_source_or_short_term_level() {
    let targets = DeliveryAudioTargets {
        target_lufs: Some(-23.0),
        target_true_peak_dbtp: Some(-1.0),
    };
    let context = source_fixture().source_context();
    let source = proof_from_receipt(context.clone(), &measurement_receipt(&context)).unwrap();
    assert_eq!(source.loudness().integrated_lufs_milli, Some(-23_000));
    assert!(evaluate_delivery_audio_qc(Some(&targets), &source).is_err());
    let decoded = decoded_proof_with_metrics(Some(-22_799), Some(-1_000));
    assert!(decoded.loudness().short_term_lufs_milli.is_none());
    let joined = joined_measurements(source, decoded, Some(&targets)).unwrap();
    assert_eq!(
        joined.delivery_audio_qc().status(),
        DeliveryAudioQcStatus::OutsideTargets
    );
    assert_eq!(joined.delivery_audio_qc().decoded_sample_frames, 96_128);
    assert_eq!(
        joined.delivery_audio_qc().evaluated_phase,
        MeasurementPhase::DecodedMltMaster
    );
    assert_eq!(
        joined.delivery_audio_qc().measurement_proof_sha256,
        digest(joined.decoded()).unwrap()
    );
    let no_short_term = decoded_proof_with_metrics(Some(-23_000), Some(-1_000));
    assert!(no_short_term.loudness().unknown_reason.is_some());
    assert_eq!(
        evaluate_delivery_audio_qc(Some(&targets), &no_short_term)
            .unwrap()
            .status(),
        DeliveryAudioQcStatus::WithinTargets
    );
}

#[test]
fn private_audio_selector_is_closed_bounded_and_phase_specific() {
    let source = source_fixture();
    let source_context = source.source_context();
    let decoded_context = MeasurementContext {
        schema: "motionwright.audio-measurement-context/1",
        source: source.clone(),
        phase: MeasurementPhase::DecodedMltMaster,
        native_parent: Some(parent_fixture(&source, 96_128)),
    };
    let source_hint = AudioReceiptContext::from_context(&source_context).unwrap();
    let decoded_hint = AudioReceiptContext::from_context(&decoded_context).unwrap();
    assert_ne!(
        source_hint.measurement_context_sha256,
        decoded_hint.measurement_context_sha256
    );
    let mut receipt = measurement_receipt(&decoded_context);
    assert!(
        AudioReceiptContext::from_receipt(&receipt)
            .unwrap()
            .is_none()
    );
    receipt.payload["audio_reopen"] = decoded_hint.payload();
    assert_eq!(
        AudioReceiptContext::from_receipt(&receipt).unwrap(),
        Some(decoded_hint)
    );
    proof_from_receipt(decoded_context.clone(), &receipt).unwrap();
    receipt.payload["audio_reopen"] = source_hint.payload();
    assert!(proof_from_receipt(decoded_context.clone(), &receipt).is_err());
    let valid = AudioReceiptContext::from_context(&decoded_context)
        .unwrap()
        .payload();
    for (field, value) in [
        ("schema", json!("motionwright.audio-receipt-context/2")),
        ("caller_request_id", json!("x".repeat(97))),
        ("caller_request_id", json!("bad\nrequest")),
        ("deliverable_id", json!("not-a-uuid")),
        ("lane", json!("other")),
        ("measurement_context_sha256", json!("bad")),
        ("unreviewed", json!("extra")),
    ] {
        let mut bad = valid.clone();
        bad[field] = value;
        receipt.payload["audio_reopen"] = bad;
        assert!(
            AudioReceiptContext::from_receipt(&receipt).is_err(),
            "{field}"
        );
    }
    for value in [Value::Null, json!(true), json!([]), json!({})] {
        receipt.payload["audio_reopen"] = value;
        assert!(AudioReceiptContext::from_receipt(&receipt).is_err());
    }
}

#[cfg(target_os = "linux")]
mod persisted {
    use super::*;
    use motionwright_domain::{
        AlignmentEvidence, Change, DeliveryRecipe, OriginalMixRecipe, RationalTime,
        TranscriptSegment,
    };
    use motionwright_service::{StudioService, VoiceImportMetadata};
    use motionwright_storage::ProductionReceiptInput;
    use std::sync::atomic::Ordering;

    fn wav(frames: u32, sample: i16) -> Vec<u8> {
        let data_bytes = frames * 4;
        let mut out = Vec::new();
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + data_bytes).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16_u32.to_le_bytes());
        out.extend_from_slice(&1_u16.to_le_bytes());
        out.extend_from_slice(&2_u16.to_le_bytes());
        out.extend_from_slice(&48_000_u32.to_le_bytes());
        out.extend_from_slice(&192_000_u32.to_le_bytes());
        out.extend_from_slice(&4_u16.to_le_bytes());
        out.extend_from_slice(&16_u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&data_bytes.to_le_bytes());
        for _ in 0..frames * 2 {
            out.extend_from_slice(&sample.to_le_bytes());
        }
        out
    }

    fn apply(service: &StudioService, project: &mut Project, id: &str, change: Change) {
        *project = service
            .apply(project.id, &RevisionStamp::from(&*project), id, &change)
            .unwrap()
            .project;
    }

    fn fixture() -> (
        tempfile::TempDir,
        StudioService,
        Project,
        ProductionCoordinator,
    ) {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let mut project = service
            .create_project("Synthetic audio receipt custody; no native effect")
            .unwrap();
        apply(
            &service,
            &mut project,
            "scene",
            Change::AddScene {
                name: "Synthetic source".into(),
                objective: "Receipt/source binding".into(),
                duration_seconds: 2,
            },
        );
        let voice_file = root.path().join("synthetic-voice.wav");
        fs::write(&voice_file, wav(96000, 2000)).unwrap();
        project = service
            .import_voice_file(
                project.id,
                &RevisionStamp::from(&project),
                "voice",
                &voice_file,
                VoiceImportMetadata {
                    name: "synthetic-voice.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Synthetic PCM; not speech or listening evidence".into(),
                },
            )
            .unwrap()
            .project;
        let voice_id = project.audio.voice_tracks[0].id;
        apply(
            &service,
            &mut project,
            "active",
            Change::SetActiveVoiceTrack { track_id: voice_id },
        );
        let mut assets = Vec::new();
        for (name, sample) in [("music", 10000_i16), ("sfx", 1000_i16)] {
            let file = root.path().join(format!("{name}.wav"));
            fs::write(&file, wav(96000, sample)).unwrap();
            project = service
                .import_asset_file(
                    project.id,
                    &RevisionStamp::from(&project),
                    name,
                    &file,
                    format!("{name}.wav"),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            assets.push(
                project
                    .assets
                    .iter()
                    .find(|a| a.name == format!("{name}.wav"))
                    .unwrap()
                    .id,
            );
        }
        let mut profile = project.deliverables[0].clone();
        profile.voice_track_id = Some(voice_id);
        profile.language = "en".into();
        let profile_id = profile.id;
        apply(
            &service,
            &mut project,
            "profile",
            Change::UpsertDeliverable { profile },
        );
        apply(
            &service,
            &mut project,
            "manual-window",
            Change::AddTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::new_v4(),
                    voice_track_id: voice_id,
                    start: RationalTime::new(1, 2).unwrap(),
                    end: RationalTime::new(1, 1).unwrap(),
                    text: "Synthetic authored interval; not recognized speech".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            },
        );
        // The pre-extension neutral wire fixture works with the ratified
        // serde-default envelope fields without duplicating their initializer.
        let original_mix: OriginalMixRecipe = serde_json::from_value(json!({
            "music_asset_id": assets[0], "sfx_asset_id": assets[1],
            "sfx_gain_db": -8.0, "duck_attenuation_db": 14.0,
            "duck_attack_samples": 2400, "duck_release_samples": 4800,
        }))
        .unwrap();
        apply(
            &service,
            &mut project,
            "save-mix",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: Some(DeliveryRecipe {
                    original_mix: Some(original_mix),
                    ..Default::default()
                }),
            },
        );
        let executable = root.path().join("never-executed-native-client");
        let session_file = root.path().join("synthetic-session");
        let socket = root.path().join("synthetic-socket");
        let output_root = root.path().join("owner-output");
        fs::write(&executable, b"G0 fixture, never execute").unwrap();
        fs::write(&session_file, b"G0 fixture, no credentials").unwrap();
        fs::write(&socket, b"G0 fixture, no Broker").unwrap();
        fs::create_dir(&output_root).unwrap();
        let connection = super::super::super::ProductionConnection {
            schema: super::super::super::CONNECTION_SCHEMA.into(),
            executable_sha256: hex::encode(Sha256::digest(fs::read(&executable).unwrap())),
            executable,
            session_file,
            socket,
            output_root,
            resource: project.resource_key(),
        };
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        (root, service, project, coordinator)
    }

    fn persist_terminal(
        service: &StudioService,
        source: &SourceBinding,
        receipt: &ProductionReceipt,
        stage: &str,
    ) -> ProductionReceipt {
        let mut dispatch_payload = json!({
            "mutation": receipt.command == MUX_COMMAND,
            "connection": source.connection_identity, "job_ref": null,
        });
        if let Some(context) = receipt.payload.get("audio_reopen") {
            dispatch_payload["audio_reopen"] = context.clone();
        }
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: receipt.project_id,
                generation: receipt.generation,
                revision: receipt.revision,
                request_id: receipt.request_id.clone(),
                request_sha256: receipt.request_sha256.clone(),
                command: receipt.command.clone(),
                stage: "dispatching".into(),
                payload: dispatch_payload,
            })
            .unwrap();
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: receipt.project_id,
                generation: receipt.generation,
                revision: receipt.revision,
                request_id: receipt.request_id.clone(),
                request_sha256: receipt.request_sha256.clone(),
                command: receipt.command.clone(),
                stage: stage.into(),
                payload: receipt.payload.clone(),
            })
            .unwrap()
    }

    fn seed_completed_chain(
        service: &StudioService,
        project: &Project,
        coordinator: &ProductionCoordinator,
    ) -> SourceBinding {
        seed_chain_with_metadata(service, project, coordinator, |_| None)
    }

    fn seed_recoverable_chain(
        service: &StudioService,
        project: &Project,
        coordinator: &ProductionCoordinator,
    ) -> SourceBinding {
        seed_chain_with_metadata(service, project, coordinator, |context| {
            Some(
                AudioReceiptContext::from_context(context)
                    .unwrap()
                    .payload(),
            )
        })
    }

    fn seed_chain_with_metadata(
        service: &StudioService,
        project: &Project,
        coordinator: &ProductionCoordinator,
        metadata: impl FnMut(&MeasurementContext) -> Option<Value>,
    ) -> SourceBinding {
        seed_chain_for_lane(
            service,
            project,
            coordinator,
            AudioAnalysisLane::SingleSegment,
            metadata,
        )
    }

    fn seed_chain_for_lane(
        service: &StudioService,
        project: &Project,
        coordinator: &ProductionCoordinator,
        lane: AudioAnalysisLane,
        mut metadata: impl FnMut(&MeasurementContext) -> Option<Value>,
    ) -> SourceBinding {
        let (_, mix, source) = coordinator
            .current_audio_source(
                project.id,
                &RevisionStamp::from(project),
                "synthetic-g0-chain",
                project.deliverables[0].id,
                lane,
            )
            .unwrap();
        let output_root = &coordinator.client.connection().output_root;
        let source_context = source.source_context();
        stage_exact_input(output_root, &source_context, mix.wav_bytes()).unwrap();
        let mut source_receipt = measurement_receipt(&source_context);
        if let Some(value) = metadata(&source_context) {
            source_receipt.payload["audio_reopen"] = value;
        }
        persist_terminal(service, &source, &source_receipt, "completed");

        let decoded = wav(96128, 3000);
        let master = b"G0 synthetic MP4 custody bytes; not rendered media";
        let master_path = expected_master_path(&source).unwrap();
        fs::write(output_root.join(&master_path), master).unwrap();
        fs::write(output_root.join("synthetic-decoded.wav"), &decoded).unwrap();
        let profile = &project.deliverables[0];
        let data = json!({
            "profile": "h264-aac-mp4", "frame_count": 60,
            "fps_num": 30, "fps_den": 1, "sample_rate": 48000, "channels": 2,
            "audio_sample_frames": 96128, "decoded_audio_sample_frames": 96128,
            "media": {
                "video": true, "audio": true, "width": profile.width, "height": profile.height,
                "frames": 60, "codecs": ["h264", "aac"],
            },
            "decoded_audio_media": {"video": false, "audio": true, "codecs": ["pcm_s16le"]},
            "artifact": {
                "root": "output", "path": master_path,
                "sha256": hex::encode(Sha256::digest(master)), "bytes": master.len(),
            },
            "decoded_audio": {
                "root": "output", "path": "synthetic-decoded.wav",
                "sha256": hex::encode(Sha256::digest(&decoded)), "bytes": decoded.len(),
            },
            "video_sha256": "9".repeat(64), "audio_sha256": source.wav_sha256,
        });
        let args = json!({
            "video_root": "output", "video_path": "synthetic-source-video.mkv",
            "video_sha256": "9".repeat(64), "audio_root": "output",
            "audio_path": "synthetic-source-audio.wav", "audio_sha256": source.wav_sha256,
            "width": profile.width, "height": profile.height,
            "fps_num": 30, "fps_den": 1, "frame_count": 60,
            "sample_rate": 48000, "channels": 2, "profile": "h264-aac-mp4",
            "output_path": expected_master_path(&source).unwrap(), "max_bytes": MLT_MASTER_MAX_BYTES,
        });
        let parent_receipt = receipt_fixture(
            &source,
            &format!(
                "{}{}",
                source.namespace().unwrap(),
                source.lane.mux_suffix()
            ),
            MUX_COMMAND,
            request_digest(MUX_COMMAND, &args).unwrap(),
            data,
            "driver:mlt-video",
        );
        let persisted = persist_terminal(service, &source, &parent_receipt, "completed");
        let parent = native_parent(coordinator, project, &source, &persisted).unwrap();
        let decoded_context = MeasurementContext {
            schema: "motionwright.audio-measurement-context/1",
            source: source.clone(),
            phase: MeasurementPhase::DecodedMltMaster,
            native_parent: Some(parent),
        };
        stage_exact_input(output_root, &decoded_context, &decoded).unwrap();
        let mut decoded_receipt = measurement_receipt(&decoded_context);
        if let Some(value) = metadata(&decoded_context) {
            decoded_receipt.payload["audio_reopen"] = value;
        }
        persist_terminal(service, &source, &decoded_receipt, "completed");
        source
    }

    #[test]
    fn sqlite_close_reopen_reconstructs_exact_two_phase_proof_without_dispatch_or_writes() {
        let (root, service, project, coordinator) = fixture();
        let source = seed_completed_chain(&service, &project, &coordinator);
        let before = coordinator
            .reopen_original_mix_audio_measurements(
                project.id,
                &project.stamp(),
                &source.caller_request_id,
                source.deliverable_id,
                source.lane,
            )
            .unwrap();
        assert_eq!(before.source().loudness().frames, 96000);
        assert_eq!(before.decoded().loudness().frames, 96128);
        let receipts = service.production_receipts(project.id, 256).unwrap();
        assert_eq!(receipts.len(), 6);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        let connection = coordinator.client.connection().clone();
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let current = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let after = current
            .reopen_original_mix_audio_measurements(
                project.id,
                &project.stamp(),
                &source.caller_request_id,
                source.deliverable_id,
                source.lane,
            )
            .unwrap();
        assert_eq!(digest(&before).unwrap(), digest(&after).unwrap());
        after.verify_current(&current).unwrap();
        assert_eq!(
            reopened.production_receipts(project.id, 256).unwrap(),
            receipts
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert_eq!(current.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn changed_master_decoded_stage_or_source_revision_cannot_reuse_completed_proof() {
        let (_root, service, mut project, coordinator) = fixture();
        let source = seed_completed_chain(&service, &project, &coordinator);
        let proof = coordinator
            .reopen_original_mix_audio_measurements(
                project.id,
                &project.stamp(),
                &source.caller_request_id,
                source.deliverable_id,
                source.lane,
            )
            .unwrap();
        let output_root = &coordinator.client.connection().output_root;
        let parent = proof.decoded.context.native_parent.as_ref().unwrap();
        for relative in [
            expected_master_path(&source).unwrap(),
            parent.decoded.relative_path.clone(),
            proof.decoded.context.file_name().unwrap(),
            proof.source.context.file_name().unwrap(),
        ] {
            let path = output_root.join(relative);
            let original = fs::read(&path).unwrap();
            let mut changed = original.clone();
            changed[0] ^= 1;
            fs::write(&path, changed).unwrap();
            assert!(proof.verify_current(&coordinator).is_err());
            fs::write(&path, original).unwrap();
        }
        let receipts = service.production_receipts(project.id, 256).unwrap();
        let mut recipe = project.production_design.delivery_recipes[&source.deliverable_id].clone();
        recipe.original_mix.as_mut().unwrap().duck_attenuation_db = 6.0;
        apply(
            &service,
            &mut project,
            "edit-source-mix",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: source.deliverable_id,
                recipe: Some(recipe),
            },
        );
        assert!(proof.verify_current(&coordinator).is_err());
        assert!(
            coordinator
                .reopen_original_mix_audio_measurements(
                    project.id,
                    &project.stamp(),
                    &source.caller_request_id,
                    source.deliverable_id,
                    source.lane,
                )
                .is_err()
        );
        assert_eq!(
            service.production_receipts(project.id, 256).unwrap(),
            receipts
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn requested_targets_and_unknown_receipts_never_reach_native_client() {
        let (_root, service, mut project, coordinator) = fixture();
        let (_, mix, source) = coordinator
            .current_audio_source(
                project.id,
                &project.stamp(),
                "unknown-read-fixture",
                project.deliverables[0].id,
                AudioAnalysisLane::SingleSegment,
            )
            .unwrap();
        let context = source.source_context();
        stage_exact_input(
            &coordinator.client.connection().output_root,
            &context,
            mix.wav_bytes(),
        )
        .unwrap();
        persist_terminal(
            &service,
            &source,
            &measurement_receipt(&context),
            "outcome_unknown",
        );
        assert!(
            coordinator
                .measure_original_mix_source(
                    project.id,
                    &project.stamp(),
                    &source.caller_request_id,
                    source.deliverable_id,
                    source.lane,
                )
                .await
                .is_err()
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);

        for (id, lufs, peak) in [
            ("lufs-target", Some(-23.0), None),
            ("peak-target", None, Some(-1.0)),
        ] {
            let mut mix = project.audio.mix.clone();
            mix.target_lufs = lufs;
            mix.target_true_peak_dbfs = peak;
            apply(&service, &mut project, id, Change::SetMixIntent { mix });
            let error = coordinator
                .measure_original_mix_source(
                    project.id,
                    &project.stamp(),
                    id,
                    source.deliverable_id,
                    source.lane,
                )
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
        }
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn voice_only_source_and_completed_chain_reopen_without_music_sfx_or_a_new_asset() {
        let (root, service, mut project, coordinator) = fixture();
        let profile_id = project.deliverables[0].id;
        apply(
            &service,
            &mut project,
            "remove-mix-for-voice-only",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: None,
            },
        );
        let before = project.clone();
        let (_, input, binding) = coordinator
            .current_audio_source(
                project.id,
                &project.stamp(),
                "voice-source",
                profile_id,
                AudioAnalysisLane::SingleSegment,
            )
            .unwrap();
        let voice = &project.audio.voice_tracks[0];
        assert_eq!(input.wav_bytes(), wav(96_000, 2_000));
        assert_eq!(binding.wav_sha256, voice.source_sha256);
        assert_eq!(
            binding
                .measured_voice_source
                .as_ref()
                .unwrap()
                .voice_track_id,
            voice.id
        );
        assert_eq!(
            binding.source_context().phase,
            MeasurementPhase::PreparedMeasuredVoice
        );
        let chain = seed_completed_chain(&service, &project, &coordinator);
        let proof = coordinator
            .reopen_original_mix_audio_measurements(
                project.id,
                &project.stamp(),
                &chain.caller_request_id,
                profile_id,
                AudioAnalysisLane::SingleSegment,
            )
            .unwrap();
        assert_eq!(
            proof.source.context.phase,
            MeasurementPhase::PreparedMeasuredVoice
        );
        let receipts = service.production_receipts(project.id, 256).unwrap();
        let connection = coordinator.client.connection().clone();
        assert_eq!(service.project(project.id).unwrap(), before);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let current = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        proof.verify_current(&current).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), before);
        assert_eq!(
            reopened.production_receipts(project.id, 256).unwrap(),
            receipts
        );
        assert_eq!(current.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn voice_only_measurement_does_not_grant_a_partial_cut_or_drop_authored_gain() {
        let (_root, service, mut project, coordinator) = fixture();
        let profile_id = project.deliverables[0].id;
        apply(
            &service,
            &mut project,
            "remove-mix",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: None,
            },
        );
        let mut mix = project.audio.mix.clone();
        mix.voice_gain_db = -3.0;
        apply(
            &service,
            &mut project,
            "authored-voice-gain",
            Change::SetMixIntent { mix: mix.clone() },
        );
        assert_eq!(
            coordinator
                .current_audio_source(
                    project.id,
                    &project.stamp(),
                    "unsupported-gain",
                    profile_id,
                    AudioAnalysisLane::SingleSegment
                )
                .err()
                .unwrap()
                .code,
            ErrorCode::Unsupported
        );
        mix.voice_gain_db = 0.0;
        apply(
            &service,
            &mut project,
            "restore-neutral-voice",
            Change::SetMixIntent { mix },
        );
        apply(
            &service,
            &mut project,
            "new-scene",
            Change::AddScene {
                name: "Additional authored scene".into(),
                objective: "Reject unbound selection".into(),
                duration_seconds: 1,
            },
        );
        let mut profile = project.deliverables[0].clone();
        profile.included_scene_ids = vec![project.scenes[1].id];
        apply(
            &service,
            &mut project,
            "partial-cut",
            Change::UpsertDeliverable { profile },
        );
        assert_eq!(
            coordinator
                .current_audio_source(
                    project.id,
                    &project.stamp(),
                    "unsupported-cut",
                    profile_id,
                    AudioAnalysisLane::SingleSegment
                )
                .err()
                .unwrap()
                .code,
            ErrorCode::Unsupported
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn native_parent_checks_actual_complete_pcm16_representation_and_decoded_frame_count() {
        let (_root, service, project, coordinator) = fixture();
        let source = seed_completed_chain(&service, &project, &coordinator);
        let parent_id = format!(
            "{}{}",
            source.namespace().unwrap(),
            source.lane.mux_suffix()
        );
        let receipt = service
            .latest_production_receipt(project.id, &parent_id)
            .unwrap()
            .unwrap();
        let output = &coordinator.client.connection().output_root;
        let original = fs::read(output.join("synthetic-decoded.wav")).unwrap();
        for (format, bits) in [(3_u16, 32_u16), (1_u16, 24_u16)] {
            let mut changed = original.clone();
            changed[20..22].copy_from_slice(&format.to_le_bytes());
            changed[28..32].copy_from_slice(&(48_000 * 2 * u32::from(bits) / 8).to_le_bytes());
            changed[32..34].copy_from_slice(&(2 * bits / 8).to_le_bytes());
            changed[34..36].copy_from_slice(&bits.to_le_bytes());
            fs::write(output.join("synthetic-decoded.wav"), &changed).unwrap();
            let mut false_representation = receipt.clone();
            false_representation.payload["result"]["data"]["decoded_audio"]["sha256"] =
                json!(hex::encode(Sha256::digest(&changed)));
            assert!(native_parent(&coordinator, &project, &source, &false_representation).is_err());
        }
        fs::write(output.join("synthetic-decoded.wav"), &original).unwrap();
        let mut false_count = receipt.clone();
        false_count.payload["result"]["data"]["decoded_audio_sample_frames"] = json!(96_129);
        assert!(native_parent(&coordinator, &project, &source, &false_count).is_err());
        let actual = native_parent(&coordinator, &project, &source, &receipt).unwrap();
        assert_eq!(actual.decoded_sample_frames, 96_128);
        assert_eq!(actual.decoded.bytes, original.len() as u64);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn unknown_mux_parent_or_foreign_profile_cannot_be_promoted_by_readback() {
        let (_root, service, project, coordinator) = fixture();
        let source = seed_completed_chain(&service, &project, &coordinator);
        let parent_id = format!(
            "{}{}",
            source.namespace().unwrap(),
            source.lane.mux_suffix()
        );
        let original = service
            .latest_production_receipt(project.id, &parent_id)
            .unwrap()
            .unwrap();
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: original.project_id,
                generation: original.generation,
                revision: original.revision,
                request_id: original.request_id.clone(),
                request_sha256: original.request_sha256.clone(),
                command: original.command.clone(),
                stage: "outcome_unknown".into(),
                payload: json!({"error":"synthetic unresolved receipt"}),
            })
            .unwrap();
        let before = service.production_receipts(project.id, 256).unwrap();
        assert!(
            coordinator
                .reopen_original_mix_audio_measurements(
                    project.id,
                    &project.stamp(),
                    &source.caller_request_id,
                    source.deliverable_id,
                    source.lane,
                )
                .is_err()
        );
        assert!(
            coordinator
                .reopen_original_mix_audio_measurements(
                    project.id,
                    &project.stamp(),
                    &source.caller_request_id,
                    Uuid::from_u128(99),
                    source.lane,
                )
                .is_err()
        );
        assert_eq!(
            service.production_receipts(project.id, 256).unwrap(),
            before
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn profile_targets_reopen_numeric_qc_and_edits_invalidate_original_receipt_authority() {
        let (root, service, mut project, coordinator) = fixture();
        let profile = project.deliverables[0].id;
        let mut recipe = project.production_design.delivery_recipes[&profile].clone();
        recipe.audio_targets = Some(DeliveryAudioTargets {
            target_lufs: Some(-23.0),
            target_true_peak_dbtp: Some(-1.0),
        });
        apply(
            &service,
            &mut project,
            "save-profile-qc",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile,
                recipe: Some(recipe.clone()),
            },
        );
        let binding = seed_completed_chain(&service, &project, &coordinator);
        let proof = coordinator
            .reopen_original_mix_audio_measurements(
                project.id,
                &project.stamp(),
                &binding.caller_request_id,
                profile,
                binding.lane,
            )
            .unwrap();
        assert_eq!(
            proof.delivery_audio_qc().status(),
            DeliveryAudioQcStatus::WithinTargets
        );
        let connection = coordinator.client.connection().clone();
        let receipts = service.production_receipts(project.id, 256).unwrap();
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let current = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        proof.verify_current(&current).unwrap();
        assert_eq!(current.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            reopened.production_receipts(project.id, 256).unwrap(),
            receipts
        );
        recipe.audio_targets.as_mut().unwrap().target_lufs = Some(-14.0);
        apply(
            &reopened,
            &mut project,
            "edit-profile-qc",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile,
                recipe: Some(recipe),
            },
        );
        assert_eq!(
            proof.verify_current(&current).unwrap_err().code,
            ErrorCode::StaleReference
        );
        assert!(
            current
                .reopen_original_mix_audio_measurements(
                    project.id,
                    &project.stamp(),
                    &binding.caller_request_id,
                    profile,
                    binding.lane,
                )
                .is_err()
        );
        assert_eq!(current.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            reopened.production_receipts(project.id, 256).unwrap(),
            receipts
        );
    }

    #[test]
    fn targeted_producer_requires_exact_current_opaque_source_receipt_and_rejects_foreign_or_unknown()
     {
        let (_root, service, mut project, coordinator) = fixture();
        let profile = project.deliverables[0].id;
        let mut recipe = project.production_design.delivery_recipes[&profile].clone();
        recipe.audio_targets = Some(DeliveryAudioTargets {
            target_lufs: None,
            target_true_peak_dbtp: Some(-1.0),
        });
        apply(
            &service,
            &mut project,
            "targeted-source",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile,
                recipe: Some(recipe),
            },
        );
        let binding = seed_completed_chain(&service, &project, &coordinator);
        let proof = coordinator
            .read_current_measurement(binding.source_context())
            .unwrap();
        let namespace = binding.namespace().unwrap();
        admit_profile_audio_targets(
            &coordinator,
            &project,
            profile,
            &namespace,
            binding.lane,
            Some(&proof),
        )
        .unwrap();
        assert_eq!(
            admit_profile_audio_targets(
                &coordinator,
                &project,
                profile,
                &namespace,
                binding.lane,
                None
            )
            .unwrap_err()
            .code,
            ErrorCode::Unsupported
        );
        for (request, lane) in [
            ("another-source-request", binding.lane),
            (&*namespace, AudioAnalysisLane::Multisegment),
        ] {
            assert_eq!(
                admit_profile_audio_targets(
                    &coordinator,
                    &project,
                    profile,
                    request,
                    lane,
                    Some(&proof)
                )
                .unwrap_err()
                .code,
                ErrorCode::StaleReference
            );
        }
        let mut forged_metric = proof.clone();
        forged_metric.loudness.integrated_lufs_milli = Some(-14_000);
        assert_eq!(
            admit_profile_audio_targets(
                &coordinator,
                &project,
                profile,
                &namespace,
                binding.lane,
                Some(&forged_metric)
            )
            .unwrap_err()
            .code,
            ErrorCode::StaleReference
        );
        let (_, bytes, unknown_source) = coordinator
            .current_audio_source(
                project.id,
                &project.stamp(),
                "unknown-target-source",
                profile,
                binding.lane,
            )
            .unwrap();
        let context = unknown_source.source_context();
        stage_exact_input(
            &coordinator.client.connection().output_root,
            &context,
            bytes.wav_bytes(),
        )
        .unwrap();
        let receipt = measurement_receipt(&context);
        let unpersisted = proof_from_receipt(context, &receipt).unwrap();
        persist_terminal(&service, &unknown_source, &receipt, "outcome_unknown");
        assert!(
            admit_profile_audio_targets(
                &coordinator,
                &project,
                profile,
                &unknown_source.namespace().unwrap(),
                binding.lane,
                Some(&unpersisted)
            )
            .is_err()
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }
    mod report_reopen;
}

mod receipt_history;
mod report;
