//! Admission/identity tests only. These do not claim measured LUFS, decoded
//! media, executed MLT, editable reopen or a production receipt.
use motionwright_native::production::audio_mastering::*;
use semwright_native_sdk::ErrorCode;
use uuid::Uuid;

fn binding() -> MasteringBinding {
    MasteringBinding {
        project_id: Uuid::from_u128(1),
        generation: Uuid::from_u128(2),
        revision: 7,
        deliverable_id: Uuid::from_u128(3),
        canonical_dependency_sha256: "a".repeat(64),
        source_plan_sha256: "b".repeat(64),
        source_readiness_sha256: "c".repeat(64),
        premix_wav_sha256: "d".repeat(64),
        premix_pcm_sha256: "e".repeat(64),
        clock: MasteringClock {
            video_frames: 60,
            fps_num: 30,
            fps_den: 1,
            sample_frames: 96_000,
        },
    }
}
fn measurement(source: &MasteringBinding) -> AudioMeasurement {
    AudioMeasurement {
        schema: MEASURE_SCHEMA.into(),
        method: MEASURE_METHOD.into(),
        role: AudioMeasureRole::CanonicalPremix,
        source_binding_sha256: source.sha256().unwrap(),
        input_sha256: source.premix_wav_sha256.clone(),
        decoded_f64le_sha256: "f".repeat(64),
        decoded_sample_frames: 96_000,
        logical_sample_frames: 96_000,
        declared_decoder_suffix_budget_samples: 0,
        sample_rate: 48_000,
        channels: 2,
        integrated_lufs: Some(-20.0),
        programme_true_peak_dbtp_upper: Some(-8.0),
        physical_true_peak_dbtp_upper: Some(-8.0),
        integrated_uncertainty_lu: INTEGRATED_UNCERTAINTY_LU,
        true_peak_uncertainty_db: TRUE_PEAK_UNCERTAINTY_DB,
        programme_interval: [0, 96_000],
        peak_flush_zero_samples: 4_800,
        complete_input_decoded: true,
        pins: AudioMeasurePins {
            worker_sha256: "1".repeat(64),
            python_sha256: "2".repeat(64),
            ffmpeg_sha256: "3".repeat(64),
            ffprobe_sha256: "4".repeat(64),
            owner_runtime_profile_sha256: "5".repeat(64),
            meter_validation_sha256: "6".repeat(64),
        },
    }
}
fn plan(
    source: MasteringBinding,
    measure: AudioMeasurement,
    profile: MasteringProfile,
) -> Result<AudioMasteringPlan, semwright_native_sdk::Error> {
    let custody = MasteringSourceCustody::OwnerSealedReadOnly {
        owner_receipt_sha256: "7".repeat(64),
        source_sha256: source.premix_wav_sha256.clone(),
    };
    plan_audio_mastering(source, profile, measure, "8".repeat(64), custody)
}

#[test]
fn same_premix_has_two_explicit_plans_without_replacing_source_identity() {
    let source = binding();
    let original = serde_json::to_vec(&source).unwrap();
    let observation = measurement(&source);
    let a = plan(
        source.clone(),
        observation.clone(),
        MasteringProfile::Speech16,
    )
    .unwrap();
    let b = plan(source.clone(), observation, MasteringProfile::Programme23).unwrap();
    assert_eq!(a.gain_milli_db(), 4_000);
    assert_eq!(b.gain_milli_db(), -3_000);
    assert_ne!(a.sha256().unwrap(), b.sha256().unwrap());
    assert_ne!(a.output_name().unwrap(), b.output_name().unwrap());
    assert_eq!(serde_json::to_vec(a.binding()).unwrap(), original);
    assert_eq!(serde_json::to_vec(b.binding()).unwrap(), original);
}

#[test]
fn only_paired_finite_saved_targets_select_an_admitted_profile() {
    assert_eq!(
        MasteringProfile::from_saved_targets(None, None).unwrap(),
        None
    );
    for profile in [MasteringProfile::Speech16, MasteringProfile::Programme23] {
        let (l, p) = profile.targets();
        assert_eq!(
            MasteringProfile::from_saved_targets(Some(l), Some(p)).unwrap(),
            Some(profile)
        );
    }
    for targets in [
        (Some(-16.0), None),
        (None, Some(-1.0)),
        (Some(-14.0), Some(-1.0)),
        (Some(f64::NAN), Some(-1.0)),
        (Some(-16.0), Some(f64::INFINITY)),
    ] {
        assert_eq!(
            MasteringProfile::from_saved_targets(targets.0, targets.1)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
    }
}

#[test]
fn incompatible_peak_rejects_before_a_gain_can_be_dispatched() {
    let source = binding();
    let mut report = measurement(&source);
    report.programme_true_peak_dbtp_upper = Some(-4.0);
    report.physical_true_peak_dbtp_upper = Some(-4.0);
    let error = plan(source, report, MasteringProfile::Speech16).unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    assert!(error.outcome_known);
    assert!(error.message.contains("dynamics"));
}

#[test]
fn gain_admission_counts_both_logical_and_physical_peak_bounds() {
    for high_field in [
        "programme_true_peak_dbtp_upper",
        "physical_true_peak_dbtp_upper",
    ] {
        let source = binding();
        let mut value = serde_json::to_value(measurement(&source)).unwrap();
        value[high_field] = serde_json::json!(-4.0);
        let report = serde_json::from_value(value).unwrap();
        assert_eq!(
            plan(source, report, MasteringProfile::Speech16)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
    }
}

#[test]
fn native_gain_range_is_rejected_without_clamping() {
    for integrated in [-100.0, 50.0] {
        let source = binding();
        let mut report = measurement(&source);
        report.integrated_lufs = Some(integrated);
        report.programme_true_peak_dbtp_upper = Some(-120.0);
        report.physical_true_peak_dbtp_upper = Some(-120.0);
        assert!(
            plan(source, report, MasteringProfile::Speech16)
                .unwrap_err()
                .message
                .contains("gain")
        );
    }
}

#[test]
fn missing_or_nonfinite_measurement_never_becomes_a_target_value() {
    for field in [
        "integrated_lufs",
        "programme_true_peak_dbtp_upper",
        "physical_true_peak_dbtp_upper",
    ] {
        for state in [
            serde_json::Value::Null,
            serde_json::json!("-20"),
            serde_json::json!(true),
        ] {
            let source = binding();
            let mut data = serde_json::to_value(measurement(&source)).unwrap();
            data[field] = state;
            if let Ok(report) = serde_json::from_value::<AudioMeasurement>(data) {
                assert!(plan(source, report, MasteringProfile::Speech16).is_err());
            }
        }
    }
    let source = binding();
    let mut report = measurement(&source);
    report.integrated_lufs = Some(f64::NAN);
    assert!(plan(source, report, MasteringProfile::Speech16).is_err());
}

#[test]
fn clock_requires_exact_samples_and_a_complete_integrated_window() {
    let mut clock = binding().clock;
    clock.sample_frames -= 1;
    assert!(clock.validate().is_err());
    clock = MasteringClock {
        video_frames: 1,
        fps_num: 30,
        fps_den: 1,
        sample_frames: 1_600,
    };
    assert!(clock.validate().is_err());
    clock = MasteringClock {
        video_frames: 451,
        fps_num: 30,
        fps_den: 1,
        sample_frames: 721_600,
    };
    assert!(clock.validate().is_err());
    clock = MasteringClock {
        video_frames: 12,
        fps_num: 30,
        fps_den: 1,
        sample_frames: 19_200,
    };
    assert!(clock.validate().is_ok());
    clock = MasteringClock {
        video_frames: 5,
        fps_num: 30_000,
        fps_den: 1_001,
        sample_frames: 8_008,
    };
    assert!(
        clock.validate().is_err(),
        "rational rate is not rounded to satisfy the minimum window"
    );
}

#[test]
fn measurement_cannot_cross_a_project_generation_revision_or_source() {
    let original = binding();
    for field in [
        "generation",
        "revision",
        "deliverable_id",
        "premix_wav_sha256",
        "premix_pcm_sha256",
        "source_plan_sha256",
    ] {
        let mut data = serde_json::to_value(&original).unwrap();
        data[field] = match field {
            "revision" => serde_json::json!(8),
            "generation" | "deliverable_id" => serde_json::json!(Uuid::from_u128(99)),
            _ => serde_json::json!("9".repeat(64)),
        };
        let changed = serde_json::from_value(data).unwrap();
        assert!(
            plan(changed, measurement(&original), MasteringProfile::Speech16).is_err(),
            "{field}"
        );
    }
}

#[test]
fn wrong_method_partial_decode_and_unexplained_padding_are_rejected() {
    let source = binding();
    for field in [
        "method",
        "sample_rate",
        "channels",
        "logical_sample_frames",
        "decoded_sample_frames",
        "complete_input_decoded",
        "peak_flush_zero_samples",
        "integrated_uncertainty_lu",
        "true_peak_uncertainty_db",
    ] {
        let mut data = serde_json::to_value(measurement(&source)).unwrap();
        data[field] = match field {
            "method" => serde_json::json!("sample_peak_dbfs"),
            "complete_input_decoded" => serde_json::json!(false),
            "integrated_uncertainty_lu" | "true_peak_uncertainty_db" => serde_json::json!(0.0),
            _ => serde_json::json!(1),
        };
        let report = serde_json::from_value(data).unwrap();
        assert!(
            plan(source.clone(), report, MasteringProfile::Speech16).is_err(),
            "{field}"
        );
    }
}

#[test]
fn custody_has_to_pin_the_premix_not_a_voice_or_post_gain_wav() {
    let source = binding();
    let report = measurement(&source);
    let error = plan_audio_mastering(
        source,
        MasteringProfile::Speech16,
        report,
        "8".repeat(64),
        MasteringSourceCustody::OwnerSealedReadOnly {
            owner_receipt_sha256: "7".repeat(64),
            source_sha256: "0".repeat(64),
        },
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
}

#[test]
fn actual_target_check_counts_uncertainty_and_the_physical_tail() {
    let source = binding();
    let mut actual = measurement(&source);
    let plan = plan(source, actual.clone(), MasteringProfile::Speech16).unwrap();
    actual.role = AudioMeasureRole::FinalAac;
    actual.input_sha256 = "9".repeat(64);
    actual.integrated_lufs = Some(-16.0);
    actual.programme_true_peak_dbtp_upper = Some(-1.5);
    actual.physical_true_peak_dbtp_upper = Some(-1.4);
    assert!(
        plan.verify_measured_target(&actual, &"9".repeat(64), AudioMeasureRole::FinalAac)
            .is_ok()
    );
    actual.integrated_lufs = Some(-15.59);
    assert!(
        plan.verify_measured_target(&actual, &"9".repeat(64), AudioMeasureRole::FinalAac)
            .is_err()
    );
    actual.integrated_lufs = Some(-16.0);
    actual.physical_true_peak_dbtp_upper = Some(-0.99);
    assert!(
        plan.verify_measured_target(&actual, &"9".repeat(64), AudioMeasureRole::FinalAac)
            .is_err()
    );
}

#[test]
fn final_measurement_cannot_authorize_a_premix_or_mismatched_pins() {
    let source = binding();
    let mut report = measurement(&source);
    let plan = plan(source, report.clone(), MasteringProfile::Speech16).unwrap();
    assert!(
        plan.verify_measured_target(
            &report,
            &report.input_sha256,
            AudioMeasureRole::CanonicalPremix
        )
        .is_err()
    );
    report.role = AudioMeasureRole::MasteredWav;
    report.pins.ffmpeg_sha256 = "9".repeat(64);
    assert!(
        plan.verify_measured_target(&report, &report.input_sha256, AudioMeasureRole::MasteredWav)
            .is_err()
    );
}

fn sdk54_audio_asset_page(revision: &str) -> serde_json::Value {
    // Complete wire shape from SDK54 app.rs::asset_dto and import_asset.
    // Authored protocol fixture only; no native probe or original receipt.
    serde_json::json!({
        "revision": revision,
        "items": [{
            "reference": "current-audio-reference",
            "id": "asset-1",
            "name": "Canonical premix",
            "kind": "audio",
            "resource": "media:canonical-premix.wav",
            "resource_status": "scoped_unopened",
            "frames": 60,
            "service": "avformat",
            "original": null,
            "proxy": null,
            "opaque": false
        }],
        "total": 1,
        "cursor": null
    })
}

fn observe_audio_asset(page: &serde_json::Value) -> Result<String, semwright_native_sdk::Error> {
    validate_mastering_asset_page(
        page,
        &"a".repeat(64),
        &binding().clock,
        "canonical-premix.wav",
    )
}

#[test]
fn sdk54_imported_audio_dto_passes_without_the_file_input_category_alias() {
    let page = sdk54_audio_asset_page(&"a".repeat(64));
    let original = page.clone();
    assert_eq!(observe_audio_asset(&page).unwrap(), "asset-1");
    assert_eq!(page, original);
}

#[test]
fn file_video_image_and_color_dtos_cannot_replace_the_imported_audio_kind() {
    for kind in ["file", "video", "image", "color", ""] {
        let mut page = sdk54_audio_asset_page(&"a".repeat(64));
        page["items"][0]["kind"] = serde_json::json!(kind);
        let original = page.clone();
        let error = observe_audio_asset(&page).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{kind}");
        assert!(error.outcome_known);
        assert_eq!(page, original);
    }
}

#[test]
fn imported_audio_service_clock_and_opaque_status_are_not_metadata_only() {
    for (field, replacement) in [
        ("service", serde_json::json!("pixbuf")),
        ("frames", serde_json::json!(59)),
        ("frames", serde_json::json!(61)),
        ("frames", serde_json::json!("60")),
        ("frames", serde_json::json!(true)),
        ("opaque", serde_json::json!(true)),
    ] {
        let mut page = sdk54_audio_asset_page(&"a".repeat(64));
        page["items"][0][field] = replacement;
        let error = observe_audio_asset(&page).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{field}");
        assert!(error.outcome_known);
    }
}

#[test]
fn a_complete_audio_dto_cannot_hide_resource_drift_proxy_or_missing_fields() {
    for (field, replacement) in [
        ("resource", "media:different.wav"),
        ("resource", "output:canonical-premix.wav"),
        ("resource_status", "resolved"),
        ("original", "media:original.wav"),
        ("proxy", "media:proxy.wav"),
    ] {
        let mut page = sdk54_audio_asset_page(&"a".repeat(64));
        page["items"][0][field] = serde_json::json!(replacement);
        assert_eq!(
            observe_audio_asset(&page).unwrap_err().code,
            ErrorCode::Conflict
        );
    }
    for field in [
        "reference",
        "id",
        "name",
        "kind",
        "resource",
        "resource_status",
        "frames",
        "service",
        "original",
        "proxy",
        "opaque",
    ] {
        let mut page = sdk54_audio_asset_page(&"a".repeat(64));
        page["items"][0].as_object_mut().unwrap().remove(field);
        let error = observe_audio_asset(&page).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{field}");
        assert!(error.outcome_known);
    }
}

#[test]
fn an_audio_asset_page_must_be_unique_complete_and_at_the_current_native_revision() {
    for change in 0..5 {
        let mut page = sdk54_audio_asset_page(&"a".repeat(64));
        match change {
            0 => page["revision"] = serde_json::json!("b".repeat(64)),
            1 => page["cursor"] = serde_json::json!("next-page"),
            2 => page["total"] = serde_json::json!(2),
            3 => {
                let other = page["items"][0].clone();
                page["items"].as_array_mut().unwrap().push(other);
                page["total"] = serde_json::json!(2);
            }
            _ => {
                page["items"] = serde_json::json!([]);
                page["total"] = serde_json::json!(0);
            }
        }
        let error = observe_audio_asset(&page).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.outcome_known);
    }
}

#[test]
fn reopened_audio_uses_the_new_current_reference_and_preserves_semantic_identity() {
    let first_revision = "a".repeat(64);
    let reopened_revision = "b".repeat(64);
    let first = sdk54_audio_asset_page(&first_revision);
    let mut reopened = sdk54_audio_asset_page(&reopened_revision);
    reopened["items"][0]["reference"] = serde_json::json!("reopened-audio-reference");
    let clock = binding().clock;
    let first_id =
        validate_mastering_asset_page(&first, &first_revision, &clock, "canonical-premix.wav")
            .unwrap();
    let reopened_id = validate_mastering_asset_page(
        &reopened,
        &reopened_revision,
        &clock,
        "canonical-premix.wav",
    )
    .unwrap();
    assert_eq!(first_id, reopened_id);
    assert!(
        validate_mastering_asset_page(&reopened, &first_revision, &clock, "canonical-premix.wav")
            .is_err()
    );
}
