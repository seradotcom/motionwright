use super::*;
use crate::registration::OwnerReadbackInstallation;

fn request() -> CanonicalMixInspectionRequest {
    CanonicalMixInspectionRequest {
        schema: REQUEST_SCHEMA.into(),
        project_id: Uuid::from_u128(1),
        generation: Uuid::from_u128(2),
        revision: 7,
        deliverable_id: Uuid::from_u128(3),
        canonical_dependency_sha256: "1".repeat(64),
        canonical_film_options_sha256: "2".repeat(64),
        canonical_motion_source_sha256: "3".repeat(64),
        source_plan_sha256: "4".repeat(64),
        native_request_id: format!("mxav-{}", "5".repeat(36)),
        original_mux_receipt_id: Uuid::from_u128(4),
        original_mux_request_sha256: "6".repeat(64),
        reference_wav_sha256: "7".repeat(64),
        master_sha256: "8".repeat(64),
        clock: InspectionClock {
            frames: 60,
            width: 64,
            height: 64,
            fps_num: 30,
            fps_den: 1,
        },
        tool_pins: ToolPins {
            python_sha256: "9".repeat(64),
            ffmpeg_sha256: "a".repeat(64),
            ffprobe_sha256: "b".repeat(64),
        },
        owner_runtime_profile_sha256: "c".repeat(64),
    }
}

/// Synthetic shape fixture only. It does not establish native or media PASS.
fn worker_response(input: &CanonicalMixInspectionRequest, decoded_suffix: u64) -> Value {
    let samples = input.clock.sample_frames().unwrap();
    let mut alignments = Vec::new();
    for channel in 0..2 {
        alignments.push(json!({"channel":channel, "best_lag_samples":0,
            "zero_lag_margin":0.1, "reference_zero_lag_margin":0.1,
            "searched_lag_samples":1600, "fingerprint_positions":8192,
            "search_interval_samples":[-1600,1600], "absolute_delay_absence_proven":false,
            "aligned_or_shifted_output":false}));
    }
    let clock = json!({"video_frames":input.clock.frames, "audio_frames":94,
        "decoded_audio_samples_from_frames":samples + decoded_suffix,
        "audio_duration_samples":samples, "first_video_pts":0, "first_audio_pts":0,
        "video_fps":"30", "audio_timebase":"1/48000", "exact_contiguous_frame_timestamps":true,
        "video_packets":60, "audio_packets":95, "priming_skip_samples":1024,
        "discard_padding_samples":0, "final_audio_packet_duration_samples":768,
        "audio_packet_presentation_end_samples":samples,
        "declared_decoder_suffix_budget_samples":256, "packet_priming_and_presentation_checked":true});
    let limits = json!({"minimum_correlation":0.97,"maximum_relative_error":0.25,
        "minimum_gain":0.80,"maximum_gain":1.20,"maximum_silent_sample":128,
        "minimum_lag_margin":0.00001,"block_samples":2048,"diagnostic_lag_samples":1600,
        "short_window_samples":64,"minimum_short_window_rms":128.0,
        "minimum_short_window_gain":0.5,"maximum_short_window_gain":2.5});
    let content = json!({"reference_sample_frames":samples, "decoded_sample_frames":samples + decoded_suffix,
        "decoded_access_unit_suffix_samples":decoded_suffix, "reference_pcm_sha256":"d".repeat(64),
        "decoded_pcm_sha256":"e".repeat(64), "logical_interval":[0,samples],
        "all_logical_samples_compared":true, "alignment":alignments, "limits":limits,
        "compared_windows":94, "compared_short_energy_windows":5998});
    let report = json!({"schema":"motionwright.native-original-mix-readback/1",
        "technical_content_readback":"PASS", "reference_wav_sha256":input.reference_wav_sha256,
        "final_mp4_sha256":input.master_sha256, "clock":clock, "content":content,
        "listening_approval":false, "rights_approval":false, "editable_source_reopen_verified":false,
        "independent_loudness_verified":false, "complete_reader_report_sha256":"f".repeat(64),
        "complete_reader_report_bytes":750_000, "complete_reader_report_retained":false});
    json!({"schema":RESULT_SCHEMA, "binding_sha256":input.binding_sha256().unwrap(),
        "reader_sha256":READER_SHA256, "tool_pins":input.tool_pins,
        "status":"PASS", "report":report})
}

fn output(value: &Value) -> ToolExecutionOutput {
    ToolExecutionOutput {
        exit_code: 0,
        stdout: serde_json::to_vec(value).unwrap(),
        stderr: vec![],
    }
}

#[tokio::test]
async fn json_and_descriptor_cannot_create_host_authority() {
    let mut driver = NativeReadbackDriver;
    let descriptor = descriptor_digest(&inspection_capability().descriptor).unwrap();
    for value in [
        serde_json::to_value(request()).unwrap(),
        worker_response(&request(), 256),
    ] {
        let error = driver
            .execute(INSPECT_COMMAND, &descriptor, value)
            .await
            .unwrap_err();
        assert!(matches!(error.code, ErrorCode::PermissionDenied));
        assert!(error.outcome_known);
    }
}

#[test]
fn no_paths_argv_verdicts_or_replacement_ids_are_request_fields() {
    let original = serde_json::to_value(request()).unwrap();
    for (key, value) in [
        ("reference_path", json!("../other.wav")),
        ("master_path", json!("/private/master.mp4")),
        ("python", json!("python")),
        ("argv", json!(["-c", "print('PASS')"])),
        ("limits", json!({})),
        ("technical_content_readback", json!("PASS")),
        ("host_job", json!({"id":"forged"})),
    ] {
        let mut changed = original.clone();
        changed[key] = value;
        assert!(parse_request(changed).is_err(), "{key}");
    }
    let mut changed = request();
    changed.native_request_id = "mxav-../foreign".into();
    assert!(changed.validate().is_err());
}

#[test]
fn exact_clock_and_geometry_are_checked_before_a_host_program_is_built() {
    let valid = request();
    assert_eq!(valid.clock.sample_frames().unwrap(), 96_000);
    for (frames, width, fps_num, fps_den) in [
        (0, 64, 30, 1),
        (60, 4097, 30, 1),
        (1, 64, 7, 1),
        (451, 64, 30, 1),
        (60, 64, 0, 1),
        (60, 64, 30, 0),
    ] {
        let mut changed = valid.clone();
        changed.clock.frames = frames;
        changed.clock.width = width;
        changed.clock.fps_num = fps_num;
        changed.clock.fps_den = fps_den;
        assert!(worker_stdin(&changed).is_err());
    }
    let mut value = serde_json::to_value(valid).unwrap();
    value["clock"]["frames"] = json!(true);
    assert!(parse_request(value).is_err());
}

#[test]
fn current_identity_and_every_pin_change_the_inspection_binding() {
    let original = request();
    let expected = original.binding_sha256().unwrap();
    for key in [
        "generation",
        "original_mux_receipt_id",
        "source_plan_sha256",
        "canonical_dependency_sha256",
        "canonical_film_options_sha256",
        "canonical_motion_source_sha256",
        "reference_wav_sha256",
        "master_sha256",
        "owner_runtime_profile_sha256",
    ] {
        let mut value = serde_json::to_value(&original).unwrap();
        value[key] = if key == "generation" || key == "original_mux_receipt_id" {
            json!(Uuid::from_u128(77))
        } else {
            json!("0".repeat(64))
        };
        let changed = parse_request(value).unwrap();
        assert_ne!(expected, changed.binding_sha256().unwrap(), "{key}");
    }
    let mut changed = original.clone();
    changed.tool_pins.ffmpeg_sha256 = "0".repeat(64);
    assert_ne!(expected, changed.binding_sha256().unwrap());
    changed.revision += 1;
    assert_ne!(
        original.media_names().unwrap(),
        changed.media_names().unwrap()
    );
}

#[test]
fn fixed_program_uses_the_exact_reader_and_root_only_typed_arguments() {
    let input = request();
    let stdin = worker_stdin(&input).unwrap();
    assert!(stdin.len() < MAX_STDIN_BYTES);
    let text = String::from_utf8(stdin).unwrap();
    let encoded = text
        .rsplit_once("raise SystemExit(main(\"")
        .unwrap()
        .1
        .strip_suffix("\"))\n")
        .unwrap();
    let packet: Value = serde_json::from_slice(&BASE64.decode(encoded).unwrap()).unwrap();
    assert_eq!(
        packet["reader_source"].as_str().unwrap().as_bytes(),
        READER_SOURCE.as_bytes()
    );
    assert_eq!(sha256(READER_SOURCE.as_bytes()), READER_SHA256);
    assert_eq!(packet["binding_sha256"], input.binding_sha256().unwrap());
    assert_eq!(
        packet["binding_json"],
        serde_json::to_string(&input).unwrap()
    );
    let args = worker_args();
    semwright_driver_sdk::validate_runtime_tool_args(&args).unwrap();
    assert_eq!(args.len(), 6);
    assert_eq!(args[0], RuntimeToolArg::Literal { value: "-I".into() });
    assert_eq!(args[1], RuntimeToolArg::Literal { value: "-B".into() });
    assert_eq!(args[2], RuntimeToolArg::Literal { value: "-".into() });
    assert_eq!(
        args[3],
        RuntimeToolArg::MountPath {
            mount: MEDIA_MOUNT.into(),
            relative: String::new()
        }
    );
    assert_eq!(
        args[4],
        RuntimeToolArg::ToolPath {
            tool: FFMPEG_TOOL.into()
        }
    );
    assert_eq!(
        args[5],
        RuntimeToolArg::ToolPath {
            tool: FFPROBE_TOOL.into()
        }
    );
}

#[test]
fn closed_transport_allows_both_reader_approved_decoder_suffix_shapes() {
    // The existing reader allows a decoder to expose either the logical end
    // or the packet-declared suffix. The wrapper must not invent equality to
    // the maximum padding budget and reject one supported shape.
    for suffix in [0, 256] {
        let input = request();
        let result =
            validate_worker_output(&input, output(&worker_response(&input, suffix))).unwrap();
        assert_eq!(result["report"]["complete_reader_report_retained"], false);
        assert!(result.get("host_job").is_none()); // Only real SDK start supplies it.
        assert!(result.get("export_token").is_none());
    }
}

#[test]
fn foreign_or_incomplete_worker_json_never_becomes_an_observation() {
    let input = request();
    let valid = worker_response(&input, 256);
    for (pointer, changed) in [
        ("/binding_sha256", json!("0".repeat(64))),
        ("/reader_sha256", json!("0".repeat(64))),
        ("/tool_pins/ffmpeg_sha256", json!("0".repeat(64))),
        ("/report/reference_wav_sha256", json!("0".repeat(64))),
        ("/report/final_mp4_sha256", json!("0".repeat(64))),
        ("/report/clock/video_frames", json!(59)),
        ("/report/clock/audio_duration_samples", json!(95_999)),
        (
            "/report/clock/declared_decoder_suffix_budget_samples",
            json!(255),
        ),
        ("/report/content/all_logical_samples_compared", json!(false)),
        ("/report/content/decoded_sample_frames", json!(96_255)),
        ("/report/listening_approval", json!(true)),
        ("/report/rights_approval", json!(true)),
        ("/report/complete_reader_report_retained", json!(true)),
        (
            "/report/complete_reader_report_bytes",
            json!(16 * 1024 * 1024 + 1),
        ),
        (
            "/report/content/alignment/0/absolute_delay_absence_proven",
            json!(true),
        ),
    ] {
        let mut value = valid.clone();
        *value.pointer_mut(pointer).unwrap() = changed;
        assert!(
            validate_worker_output(&input, output(&value)).is_err(),
            "{pointer}"
        );
    }
    for key in ["export_token", "approved", "diagnostic_path"] {
        let mut value = valid.clone();
        value["report"][key] = json!(true);
        assert!(validate_worker_output(&input, output(&value)).is_err());
    }
    let mut noisy = output(&valid);
    noisy.stderr = b"a private decoder path".to_vec();
    let error = validate_worker_output(&input, noisy).unwrap_err();
    assert!(!error.message.contains("private"));
    let oversized = ToolExecutionOutput {
        exit_code: 0,
        stdout: vec![b' '; MAX_RESULT_BYTES + 1],
        stderr: vec![],
    };
    assert!(validate_worker_output(&input, oversized).is_err());
}

#[test]
fn original_host_unknown_and_acknowledged_job_are_retained_without_retry_signal() {
    let input = request();
    let job = RuntimeToolJob {
        id: "host-job-00001".into(),
    };
    job.validate().unwrap();
    let original = Error::new(
        ErrorCode::Timeout,
        "Original Host job outcome remains unknown",
    )
    .uncertain()
    .recipe_progress(0, "original wait");
    let retained = retain_job_error(original.clone(), &input, Some(&job));
    assert!(matches!(retained.code, ErrorCode::Timeout));
    assert!(!retained.outcome_known);
    assert_eq!(retained.message, original.message);
    assert_eq!(
        serde_json::to_value(retained.progress).unwrap(),
        serde_json::to_value(original.progress).unwrap()
    );
    assert!(
        retained
            .candidates
            .contains(&"motionwright-readback-host-job:host-job-00001".into())
    );
    assert!(
        !retained
            .candidates
            .iter()
            .any(|item| item.contains("retry"))
    );
    let before_ack = retain_job_error(
        Error::new(ErrorCode::Unavailable, "No start acknowledgement").uncertain(),
        &input,
        None,
    );
    assert!(!before_ack.outcome_known);
    assert!(
        !before_ack
            .candidates
            .iter()
            .any(|item| item.starts_with("motionwright-readback-host-job:"))
    );
}

#[test]
fn registration_requires_owner_pins_and_exposes_no_write_or_network_grant() {
    let executable = std::env::current_exe().unwrap();
    let installation =
        OwnerReadbackInstallation::new(executable, "d".repeat(64), request().tool_pins).unwrap();
    let manifest = installation.manifest().unwrap();
    let descriptor = inspection_capability();
    descriptor
        .validate_for(&manifest.identity().unwrap())
        .unwrap();
    assert!(!manifest.network);
    assert!(manifest.secrets.is_empty());
    assert!(manifest.mounts.iter().all(|mount| mount.read_only));
    assert!(
        manifest
            .mounts
            .iter()
            .find(|mount| mount.root == MEDIA_MOUNT)
            .is_some_and(|mount| !mount.execute)
    );
    assert_eq!(manifest.tools[0].dependencies, [FFMPEG_TOOL, FFPROBE_TOOL]);
    assert_eq!(descriptor.descriptor.requires, [PROVIDER_ID]);
    assert!(!descriptor.descriptor.risk.mutates());
    assert!(!descriptor.descriptor.dry_run);
    assert_eq!(manifest.request_timeout_ms, DRIVER_REQUEST_TIMEOUT_MS);
    assert!(HOST_JOB_TIMEOUT_MS < manifest.request_timeout_ms);
    assert!(descriptor.descriptor.timeout_ms < REQUIRED_PRODUCTION_DEADLINE_SECS * 1000);
    assert_eq!(installation.proposed_manifest_sha256().unwrap().len(), 64);
    let mut pins = request().tool_pins;
    pins.python_sha256 = "A".repeat(64);
    assert!(
        OwnerReadbackInstallation::new(std::env::current_exe().unwrap(), "d".repeat(64), pins)
            .is_err()
    );
}
