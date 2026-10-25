//! A public report derived only from the current opaque decoded measurement.
//! The durable QC/1 and full private measurement receipts are unchanged.
use super::*;

#[derive(Debug, Clone, Serialize)]
struct MeasurementMethod {
    engine: String,
    version: String,
    producer_schema_version: u32,
}

#[derive(Debug, Clone, Serialize)]
struct SampleInterval {
    start_sample_frame: u64,
    end_sample_frame_exclusive: u64,
}

#[derive(Debug, Clone, Serialize)]
struct DecodedInput {
    format: &'static str,
    sha256: String,
    sample_rate_hz: u32,
    channels: u16,
    channel_layout: String,
    sample_interval: SampleInterval,
}

#[derive(Debug, Clone, Serialize)]
struct MeasurementWindows {
    integrated_loudness: &'static str,
    momentary_window_ms: u32,
    short_term_window_ms: u32,
    momentary_and_short_term_position: &'static str,
    loudness_range: &'static str,
    true_peak: &'static str,
    sample_peak: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct MeasuredLevels {
    integrated_lufs_milli: Option<i32>,
    momentary_lufs_milli: Option<i32>,
    short_term_lufs_milli: Option<i32>,
    loudness_range_milli: Option<u32>,
    true_peak_millidbtp: Option<i32>,
    sample_peak_millidbfs: Option<i32>,
}

#[derive(Debug, Clone, Serialize)]
struct ReportedUnits {
    integrated_lufs_milli: &'static str,
    momentary_lufs_milli: &'static str,
    short_term_lufs_milli: &'static str,
    loudness_range_milli: &'static str,
    true_peak_millidbtp: &'static str,
    sample_peak_millidbfs: &'static str,
    target_lufs_milli: &'static str,
    target_true_peak_millidbtp: &'static str,
    lufs_delta_milli: &'static str,
    lufs_tolerance_milli: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct PublishedResolution {
    numerator: u16,
    denominator: u16,
    rounding_authority: &'static str,
}

/// An additive IPC report; it does not replace durable QC/1 or mint authority.
/// No public fields, constructor, or Deserialize accept caller-authored metadata.
#[derive(Debug, Clone, Serialize)]
pub struct VerifiedDeliveryAudioQcReport {
    schema: &'static str,
    qc: VerifiedDeliveryAudioQc,
    measurement_method: MeasurementMethod,
    decoded_input: DecodedInput,
    measurement_windows: MeasurementWindows,
    true_peak_oversample: u16,
    measured_levels: MeasuredLevels,
    units: ReportedUnits,
    published_resolution: PublishedResolution,
    master_artifact_sha256: String,
    evidence_scope: &'static str,
}

pub(super) fn build_report(
    qc: &VerifiedDeliveryAudioQc,
    decoded: &VerifiedAudioMeasurement,
) -> NativeResult<VerifiedDeliveryAudioQcReport> {
    if decoded.context.phase != MeasurementPhase::DecodedMltMaster {
        return Err(stale(
            "Audio report requires the measured final decoded master",
        ));
    }
    let parent = decoded
        .context
        .native_parent
        .as_ref()
        .ok_or_else(|| stale("Audio report has no verified native master parent"))?;
    let (sha256, bytes, frames) = decoded.context.input()?;
    // Keep the actual decoded interval (including any codec tail), never the
    // requested video/source length. No integer may lose precision over IPC.
    if frames == 0
        || frames > 9_007_199_254_740_991
        || bytes > MAX_ANALYSIS_BYTES
        || !is_sha256(sha256)
        || !is_sha256(&parent.master.sha256)
        || parent.source_audio_sha256 != decoded.context.source.wav_sha256
        || parent.request_id
            != format!(
                "{}{}",
                decoded.context.source.namespace()?,
                decoded.context.source.lane.mux_suffix()
            )
    {
        return Err(stale("Audio report does not match its exact native parent"));
    }
    // Reuse the pinned provider validator, including method/version/windows,
    // independent PCM statistics and nullable values. This is no new analyzer.
    let (loudness, _) = validate_analysis(
        &decoded.context,
        &json!({
            "artifact": {
                "file_name": decoded.context.file_name()?,
                "sha256": sha256,
                "bytes": bytes,
                "format": "wav",
            },
            "loudness": &decoded.loudness,
            "pcm_statistics": &decoded.pcm_statistics,
            "exhaustive": decoded.exhaustive,
        }),
    )?;
    let current_qc = evaluate_delivery_audio_qc(qc.targets(), decoded)?;
    if digest(&current_qc)? != digest(qc)? {
        return Err(stale(
            "Audio report QC is not bound to this decoded measurement",
        ));
    }
    Ok(VerifiedDeliveryAudioQcReport {
        schema: "motionwright.delivery-audio-qc-report/2",
        qc: qc.clone(),
        measurement_method: MeasurementMethod {
            engine: loudness.method.clone(),
            version: loudness.version.clone(),
            producer_schema_version: loudness.schema_version,
        },
        decoded_input: DecodedInput {
            format: "wav_pcm_s16le",
            sha256: sha256.to_owned(),
            sample_rate_hz: loudness.sample_rate,
            channels: loudness.channels,
            channel_layout: loudness.layout.clone(),
            sample_interval: SampleInterval {
                start_sample_frame: 0,
                end_sample_frame_exclusive: loudness.frames,
            },
        },
        measurement_windows: MeasurementWindows {
            integrated_loudness: "complete_decoded_input_with_provider_gating",
            momentary_window_ms: loudness.momentary_window_ms,
            short_term_window_ms: loudness.short_term_window_ms,
            momentary_and_short_term_position: "ending_at_decoded_input_end",
            loudness_range: "complete_decoded_input_with_provider_gating",
            true_peak: "complete_decoded_input",
            sample_peak: "complete_decoded_input",
        },
        true_peak_oversample: loudness.true_peak_oversample,
        measured_levels: MeasuredLevels {
            integrated_lufs_milli: loudness.integrated_lufs_milli,
            momentary_lufs_milli: loudness.momentary_lufs_milli,
            short_term_lufs_milli: loudness.short_term_lufs_milli,
            loudness_range_milli: loudness.loudness_range_milli,
            true_peak_millidbtp: loudness.true_peak_millidbtp,
            sample_peak_millidbfs: loudness.sample_peak_millidbfs,
        },
        units: ReportedUnits {
            integrated_lufs_milli: "LUFS",
            momentary_lufs_milli: "LUFS",
            short_term_lufs_milli: "LUFS",
            loudness_range_milli: "LU",
            true_peak_millidbtp: "dBTP",
            sample_peak_millidbfs: "dBFS",
            target_lufs_milli: "LUFS",
            target_true_peak_millidbtp: "dBTP",
            lufs_delta_milli: "LU",
            lufs_tolerance_milli: "LU",
        },
        published_resolution: PublishedResolution {
            numerator: 1,
            denominator: 1000,
            rounding_authority: "identified_provider_integer_milli_output",
        },
        master_artifact_sha256: parent.master.sha256.clone(),
        evidence_scope: "measured-final-pcm-numeric-report-not-normalization-not-intelligibility-not-human-acceptance",
    })
}
