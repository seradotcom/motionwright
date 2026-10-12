"""Measured stage costs for TWO independent source-equivalent Semwright renders.

Pure receipt validation: no render, file upload, system probe, Broker calls,
job retry or capability admission. Do not interpret sequential run-time
differences as causal cache savings, or source parity as human approval.
"""
from __future__ import annotations

import re

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
DIRECT_STAGES = ("plan", "apply", "start", "terminal_wait", "result", "verify")
MAX_STAGE_US = 1_800_000_000  # 30 min per operation; CI native job is bounded.
FRAMES_PER_LEG = 60


class NativeStageError(ValueError):
    """Fixed taxonomy: no private or untrusted CLI messages in diagnostics."""


def need(condition: bool, message: str) -> None:
    if not condition:
        raise NativeStageError(message)


def elapsed_us(start_ns: int, end_ns: int) -> int:
    """Round UP fractional microseconds; fail closed for clock reversal."""
    need(type(start_ns) is int and type(end_ns) is int
         and start_ns >= 0 and end_ns >= start_ns, "non_monotonic_clock")
    value = (end_ns - start_ns + 999) // 1000
    need(value <= MAX_STAGE_US, "unbounded_stage_duration")
    return value


def valid_us(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_STAGE_US


def assemble_native_stage_report(
    *, source_sha: str, semwright_sha: str, film_sha256: str,
    mediated_job: str, direct_is_separate_job: bool,
    mediated_micros: int, manifest_review_micros: int,
    direct_micros: int, parity_micros: int,
    direct_stage_micros: dict[str, int],
    frame_comparison: dict,
) -> dict:
    need(isinstance(source_sha, str) and SHA40.fullmatch(source_sha) is not None,
         "source_sha_invalid")
    need(isinstance(semwright_sha, str) and SHA40.fullmatch(semwright_sha) is not None,
         "semwright_sha_invalid")
    need(isinstance(film_sha256, str) and SHA64.fullmatch(film_sha256) is not None,
         "film_identity_invalid")
    need(isinstance(mediated_job, str) and 0 < len(mediated_job) < 300,
         "mediated_job_missing")
    need(direct_is_separate_job is True, "native_job_identity_not_independent")
    for value in [mediated_micros, manifest_review_micros, direct_micros, parity_micros]:
        need(valid_us(value), "native_phase_measurement_invalid")
    need(isinstance(direct_stage_micros, dict)
         and set(direct_stage_micros) == set(DIRECT_STAGES)
         and all(valid_us(value) for value in direct_stage_micros.values()),
         "direct_phase_measurement_invalid")
    need(sum(direct_stage_micros.values()) <= direct_micros + len(DIRECT_STAGES),
         "direct_phase_measurement_exceeds_total")
    need(isinstance(frame_comparison, dict)
         and frame_comparison.get("frame_count") == FRAMES_PER_LEG
         and type(frame_comparison.get("frame_count")) is int
         and frame_comparison.get("all_frames_decoded_rgba_pixel_identical") is True
         and frame_comparison.get("png_container_mismatch_count") in range(61)
         and type(frame_comparison.get("png_container_mismatch_count")) is int,
         "frame_parity_unverified")
    ordered = frame_comparison.get("ordered_rgba_frame_digests_sha256")
    need(isinstance(ordered, str) and SHA64.fullmatch(ordered) is not None,
         "frame_digest_unverified")
    # Never use a numeric speedup derived from sequential runs. Their first
    # run may warm the browser/cache, and the mediation includes source work.
    return {
        "schema": "motionwright.native-render-stage-cost/1",
        "source_sha": source_sha,
        "semwright_sha": semwright_sha,
        "canonical_film_sha256": film_sha256,
        "status": "INDEPENDENT_NATIVE_RENDER_PHASES_MEASURED",
        "run_design": "same_film_sequential_nonrandomized_two_distinct_jobs",
        "verified_frames_per_leg": FRAMES_PER_LEG,
        "verified_rgba_frames_across_both_legs": FRAMES_PER_LEG * 2,
        "ordered_rgba_frame_digests_sha256": ordered,
        "observations_micros": {
            "mediated_native_render": mediated_micros,
            "mediated_native_manifest_and_roi_readback": manifest_review_micros,
            "direct_broker_total": direct_micros,
            "decoded_rgba_parity_verification": parity_micros,
        },
        "direct_broker_substage_micros": {
            phase: direct_stage_micros[phase] for phase in DIRECT_STAGES
        },
        "mediated_render_scope": "Motionwright_StudioService_Semwright_Native_SDK_Broker_DriverHost",
        "direct_render_scope": "same_Semwright_Broker_DriverHost_without_Motionwright_mediation",
        "comparison": {
            "source_films_exactly_identical": True,
            "independent_native_jobs": True,
            "decoded_rgba_all_frames_exactly_equal": True,
            "cross_run_wallclock_speedup_claim": "NOT_COMPARABLE_SEQUENTIAL_RUNS",
            "cache_savings": None,
            "saved_native_renderer_frames": None,
            "actual_human_creative_approval": "NOT_RUN",
            "provider_cold_vs_warm_experiment": "NOT_RUN",
            "arbitrary_renderer_support": "NOT_PROVEN",
        },
        "limitations": [
            "Stage durations are monotonic wall-clock observations, not CPU-only work.",
            "The direct second render follows the mediated first render and may reuse warmed provider state.",
            "Frame parity proves exactly this same-source 60-frame Film, not all renderer properties.",
            "Timing does not authorize new jobs, retries, grants, cache savings, human acceptance or release readiness.",
        ],
        "v05_requirement_complete": False,
        "release_ready": False,
    }
