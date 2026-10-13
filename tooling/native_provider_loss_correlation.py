"""Source- and schema-bound passive CI provider-loss correlation.

Never an upstream Driver Host root-cause verdict, cleanup permission, restart
instruction or installed-owner acceptance result. PID/PPID, native job refs,
paths, argv, environment, sockets and private process material stay excluded.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "motionwright.v01-provider-loss-correlation/1"
AUTHORITY = "passive-ci-process-correlation-not-driver-host-causation"
ALLOWED_ROLES = frozenset({
    "semwrightd", "semwright-sandb", "semwright-motio",
    "semwright-mlt-v", "semwright", "node", "firefox",
    "bwrap", "melt", "ffmpeg", "ffprobe", "sh", "bash", "python3", "other",
})
MAX_SAMPLES = 720
MAX_PROCS = 64
MAX_ELAPSED_MS = 10_800_000


class CorrelationEvidenceError(ValueError):
    pass


def require(value: bool, reason: str) -> None:
    if not value:
        raise CorrelationEvidenceError(reason)


def inspect_provider_lifetime(lifecycle: dict[str, Any],
                              reconciliation: dict[str, Any] | None) -> dict[str, Any]:
    require(isinstance(lifecycle, dict), "invalid_lifecycle")
    require(lifecycle.get("schema") == "motionwright-ci-provider-lifecycle/1"
            and lifecycle.get("authority") ==
            "passive-ci-process-status-not-driver-evidence", "invalid_source_schema")
    samples = lifecycle.get("samples")
    require(isinstance(samples, list) and 1 <= len(samples) <= MAX_SAMPLES,
            "invalid_sample_window")
    require(type(lifecycle.get("sample_count")) is int
            and lifecycle["sample_count"] == len(samples), "inconsistent_sample_count")
    previous_ms = -1
    observed_daemon = 0
    truncated_samples = 0
    bwrap_counts = []
    provider_counts = []
    complete_samples = []
    for sample in samples:
        require(isinstance(sample, dict), "invalid_sample")
        elapsed = sample.get("elapsed_ms")
        require(type(elapsed) is int and previous_ms <= elapsed <= MAX_ELAPSED_MS,
                "invalid_sample_order")
        previous_ms = elapsed
        require(type(sample.get("root_observed")) is bool
                and type(sample.get("truncated")) is bool, "invalid_sample_status")
        processes = sample.get("processes")
        require(isinstance(processes, list) and len(processes) <= MAX_PROCS,
                "unbounded_process_sample")
        observed_daemon += int(sample["root_observed"])
        truncated_samples += int(sample["truncated"])
        complete_samples.append(sample["root_observed"] and not sample["truncated"])
        roles = []
        for process in processes:
            require(isinstance(process, dict), "invalid_process_sample")
            role = process.get("role")
            require(isinstance(role, str) and role in ALLOWED_ROLES,
                    "unexpected_native_process_role")
            roles.append(role)
        bwrap_counts.append(roles.count("bwrap"))
        provider_counts.append(sum(roles.count(role) for role in (
            "semwright-sandb", "semwright-motio", "semwright-mlt-v",
        )))
    daemon_exit = lifecycle.get("daemon_exit_before_owner_cleanup")
    require(daemon_exit is None or type(daemon_exit) is int, "invalid_daemon_exit")
    oom_delta = lifecycle.get("cgroup_oom_kills_delta")
    require(oom_delta is None or
            (type(oom_delta) is int and 0 <= oom_delta <= 1_000_000),
            "invalid_oom_delta")
    require(reconciliation is None or isinstance(reconciliation, dict),
            "invalid_status_evidence")
    if reconciliation is not None:
        require(reconciliation.get("schema") ==
                "motionwright-ci-render-status-reconciliation/1",
                "invalid_status_schema")
    pattern = (
        reconciliation is not None
        and reconciliation.get("timeout_then_provider_unavailable_observed") is True
        and reconciliation.get("render_outcome") == "unconfirmed"
        and reconciliation.get("read_only") is True
    )
    # A missing daemon or truncated process tree cannot prove any child was
    # absent. Compare counts only across consecutive, complete observations;
    # never infer provider exit or upstream root cause from process counts.
    complete_pairs = [
        i for i in range(1, len(samples))
        if complete_samples[i - 1] and complete_samples[i]
    ]
    peak_bwrap = max(bwrap_counts)
    drops = sum(1 for i in complete_pairs
                if bwrap_counts[i] < bwrap_counts[i - 1])
    provider_drops = sum(1 for i in complete_pairs
                         if provider_counts[i] < provider_counts[i - 1])
    return {
        "schema": SCHEMA,
        "authority": AUTHORITY,
        "render_outcome_unknown_after_status_loss": bool(pattern),
        "read_only_timeout_then_unavailable_observed": bool(pattern),
        "daemon_exit_before_owner_cleanup_observed": daemon_exit is not None,
        "daemon_observed_in_all_samples": observed_daemon == len(samples),
        "samples_bounded": len(samples),
        "samples_with_missing_daemon": len(samples)-observed_daemon,
        "samples_with_truncated_process_tree": truncated_samples,
        "complete_process_samples": sum(complete_samples),
        "consecutive_complete_sample_pairs": len(complete_pairs),
        "incomparable_sample_pairs": len(samples) - 1 - len(complete_pairs),
        "bwrap_process_count_peak": peak_bwrap,
        "bwrap_process_count_final": (
            bwrap_counts[-1] if complete_samples[-1] else None
        ),
        "bwrap_count_decreases_observed": drops,
        "provider_named_process_count_decreases_observed": provider_drops,
        "oom_kills_delta_observed": oom_delta,
        "oom_cause_established": False,
        "provider_generation_exit_cause_established": False,
        "specific_provider_process_exit_verified": False,
        "native_job_terminal_result_verified_by_this_report": False,
        "automatic_native_mutation_retry": False,
        "operator_owner_reconciliation_required_if_unknown": bool(pattern),
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_ready": False,
    }
