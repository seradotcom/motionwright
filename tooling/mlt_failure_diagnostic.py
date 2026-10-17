"""Sanitize exact-source native render failure receipts for CI-only evidence.

Never upload stderr, provider JSON, private file paths, local stack frames,
source-media names or arbitrary renderer text. The only public information is
a small, explicitly allowlisted observation reason and bounded counters.
This module does not change render outcomes, approvals, grants, or retry jobs.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

OUTPUT_DIR = re.compile(r"^render-[a-f0-9]{32}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
MAX_RECEIPT_BYTES = 64 * 1024
MAX_RENDER_DIRECTORIES = 64
MAX_RECEIPTS = 4
MAX_NATIVE_OBSERVATION_BYTES = 64 * 1024 * 1024
MAX_NATIVE_FRAMES = 36_000
REASON_MARKERS = {
    "native observation count incomplete": "observation_count_incomplete",
    "native observation/frame mismatch": "observation_frame_mismatch",
    "native observation byte budget exceeded": "observation_byte_budget",
    "native font readiness observation incomplete": "font_readiness_incomplete",
    "native font readiness receipt exceeds bounds": "font_receipt_budget",
    "native observation receipt mismatch": "observation_receipt_mismatch",
    "duplicate frame payload": "duplicate_frame",
    "invalid frame payload": "invalid_frame_payload",
    "invalid png payload": "invalid_png_payload",
    "native failure output alias": "failure_output_alias",
}

def _bounded_frame_metrics(render_directory: Path) -> dict:
    """Read only numeric frame counts, never frame bytes or private source data."""
    observed = {"exported_png_count": None, "observation_line_count": None,
                "first_png_frame": None, "last_png_frame": None}
    frames = render_directory / "frames"
    if not frames.is_symlink() and frames.is_dir():
        numbers = []
        for offset, file in enumerate(frames.iterdir()):
            if offset > MAX_NATIVE_FRAMES:
                return observed
            if file.is_symlink() or not file.is_file():
                continue
            if len(file.name) == 10 and file.name.endswith(".png") and file.name[:6].isascii() and file.name[:6].isdigit():
                numbers.append(int(file.name[:6]))
        observed["exported_png_count"] = len(numbers)
        if numbers:
            observed["first_png_frame"] = min(numbers)
            observed["last_png_frame"] = max(numbers)
    rows = render_directory / "native-observations.ndjson"
    if not rows.is_symlink() and rows.is_file() and 0 < rows.stat().st_size <= MAX_NATIVE_OBSERVATION_BYTES:
        lines = 0
        total = 0
        with rows.open("rb") as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                total += len(chunk)
                if total > MAX_NATIVE_OBSERVATION_BYTES:
                    return observed
                lines += chunk.count(b"\n")
                if lines > MAX_NATIVE_FRAMES:
                    return observed
        observed["observation_line_count"] = lines
    return observed


def safe_observation_diagnostic(owner_output_root: Path) -> dict:
    """Return a tiny fixed-vocabulary report, never any untrusted input text."""
    report = {
        "schema": "motionwright-native-render-observation/1",
        "status": "unclassified",
        "reason": "unknown",
        "receipts_examined": 0,
        "raw_driver_text_exposed": False,
        "source_mutated": False,
    }
    try:
        if owner_output_root.is_symlink() or not owner_output_root.is_dir():
            return report
        for offset, entry in enumerate(owner_output_root.iterdir()):
            if offset >= MAX_RENDER_DIRECTORIES:
                return report
            if not OUTPUT_DIR.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
                continue
            receipt = entry / "native-failure-receipt.json"
            if receipt.is_symlink() or not receipt.is_file():
                continue
            if not (1 <= receipt.stat().st_size <= MAX_RECEIPT_BYTES):
                continue
            if report["receipts_examined"] >= MAX_RECEIPTS:
                return report
            report["receipts_examined"] += 1
            data = json.loads(receipt.read_bytes())
            if (not isinstance(data, dict)
                or data.get("version") != 2
                or data.get("ok") is not False
                or data.get("error_class") != "observation"
                or not isinstance(data.get("render_input_digest"), str)
                or not DIGEST.fullmatch(data["render_input_digest"])
                or not isinstance(data.get("local_stack_only"), str)
                or len(data["local_stack_only"]) > 16_384):
                continue
            # The first line can contain private paths, names, JS stack details.
            # Compare it in memory against only allowlisted static substrings.
            # NEVER add the original first line or any arbitrary substring to
            # the returned structure.
            first_line = data["local_stack_only"].split("\n", 1)[0].lower()
            for substring, safe_reason in REASON_MARKERS.items():
                if substring in first_line:
                    report["status"] = "identified"
                    report["reason"] = safe_reason
                    if safe_reason == "observation_count_incomplete":
                        report.update(_bounded_frame_metrics(entry))
                    return report
            report["status"] = "observation_receipt_present"
    except (OSError, ValueError, TypeError, UnicodeError):
        report["status"] = "unclassified"
        report["reason"] = "unknown"
    return report

# Error codes from the pinned canonical reader, never decoder output or paths.
ORIGINAL_MIX_FAILURE_FILE = "native-original-mix-readback-failure.json"
ORIGINAL_MIX_FAILURE_LIMIT = 64 * 1024
ORIGINAL_MIX_READBACK_REASONS = frozenset({
    "audio_access_unit_suffix_budget",
    "audio_alignment_not_uniquely_at_zero",
    "audio_duration_clock_mismatch",
    "audio_frame_and_packet_pts_disagree",
    "audio_frame_and_packet_samples_disagree",
    "audio_frame_without_pts",
    "audio_frame_without_sample_count",
    "audio_packet_decode_timestamp",
    "audio_packet_discard_duration_mismatch",
    "audio_packet_duration_mismatch",
    "audio_packet_presentation_end_mismatch",
    "audio_packet_timestamp_discontinuity",
    "audio_packet_without_dts",
    "audio_packet_without_duration",
    "audio_packet_without_pts",
    "audio_profile_mismatch",
    "audio_skip_discard_budget",
    "audio_stream_identity",
    "audio_timestamp_discontinuity",
    "decoded_audio_frame_clock_mismatch",
    "decoder_failed",
    "decoder_job_assignment_failed",
    "decoder_job_close_failed",
    "decoder_job_configuration_failed",
    "decoder_job_creation_failed",
    "decoder_job_process_access_failed",
    "decoder_job_termination_failed",
    "decoder_output_budget",
    "decoder_output_limit_exceeded",
    "decoder_pipe_probe_failed",
    "decoder_reap_timeout",
    "decoder_reported_media_errors",
    "decoder_timeout",
    "decoder_timeout_budget",
    "duplicate_audio_skip_metadata",
    "exact_av_streams_required",
    "fractional_audio_sample_clock",
    "geometry_budget",
    "invalid_audio_discard_padding",
    "invalid_audio_packet_side_data",
    "invalid_audio_skip_samples",
    "invalid_audio_timebase",
    "invalid_block_size",
    "invalid_correlation_limit",
    "invalid_declared_decoder_suffix",
    "invalid_decoder_json",
    "invalid_error_limit",
    "invalid_expected_digest",
    "invalid_gain_limits",
    "invalid_lag_budget",
    "invalid_lag_margin",
    "invalid_media_clock",
    "invalid_packet_json",
    "invalid_sample_rate",
    "invalid_short_window_gain",
    "invalid_short_window_rms",
    "invalid_short_window_size",
    "invalid_silence_limit",
    "invalid_video_best_effort_pts",
    "invalid_video_rate",
    "invalid_video_timebase",
    "local_audio_content_correlation",
    "local_audio_content_error",
    "local_audio_gain",
    "media_byte_budget",
    "media_changed_during_hash",
    "media_changed_during_readback",
    "media_stream_identity",
    "media_timebase_mismatch",
    "missing_audio_duration",
    "missing_audio_start",
    "missing_audio_stream_index",
    "missing_decoded_frames",
    "missing_media_packets",
    "missing_video_duration",
    "missing_video_frame_count",
    "missing_video_start",
    "missing_video_stream_index",
    "noninitial_audio_skip_samples",
    "nonterminal_audio_discard_padding",
    "nonzero_av_start_timestamp",
    "packet_stream_identity",
    "partial_stereo_sample",
    "physical_audio_frame_count",
    "physical_packet_count",
    "physical_pcm_and_frame_count_disagree",
    "physical_video_frame_count",
    "reference_has_no_timing_signature",
    "reference_must_be_original_pcm16_stereo_48khz",
    "reference_sample_budget",
    "reference_sample_clock_mismatch",
    "reference_timing_signature_ambiguous",
    "reference_wav_invalid",
    "reference_wav_physically_truncated",
    "regular_media_file_required",
    "sample_budget",
    "short_window_audio_gain",
    "source_or_master_digest_mismatch",
    "undeclared_decoder_suffix_or_loss",
    "undeclared_or_incoherent_audio_priming",
    "unexpected_audio_in_silent_window",
    "unexpected_decoded_frame",
    "video_duration_clock_mismatch",
    "video_frame_without_pts",
    "video_packet_clock_mismatch",
    "video_packet_duration_mismatch",
    "video_packet_without_duration",
    "video_packet_without_pts",
    "video_profile_mismatch",
    "video_stream_identity",
    "video_timestamp_discontinuity",
})


def safe_original_mix_readback_diagnostic(reason: object) -> dict:
    """Project only a canonical reader code; this does not rerun or accept media."""
    admitted = type(reason) is str and reason in ORIGINAL_MIX_READBACK_REASONS
    return {
        "schema": "motionwright.native-original-mix-readback-failure/1",
        "technical_content_readback": "FAIL",
        "reason": reason if admitted else "reader_refused",
        "reason_identified": admitted,
        "complete_master_readback": "NOT_VERIFIED",
        "input_stability_after_failure": "NOT_VERIFIED",
        "raw_decoder_text_exposed": False,
    }


def readback_run_identity(environment) -> dict | None:
    """Bind a retained failure to this Actions run and attempt, without coercion."""
    identity = {}
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        value = environment.get(name)
        if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
            return None
        identity[name.lower()] = value
    return identity
