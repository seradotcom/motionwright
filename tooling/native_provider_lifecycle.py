"""Bounded, secrets-free process lifecycle evidence for disposable CI Driver Hosts.

Only process *status* is read, never argv, environment, cwd, file descriptors,
logs, sockets, tokens, or arbitrary user files. This does not modify or retry a
Semwright provider. It cannot attribute the cause of a process disappearance.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from typing import Any

ALLOWED_ROLES = frozenset({
    "semwrightd", "semwright-sandbo", "semwright-motio",
    "semwright-mlt-v", "semwright", "node", "firefox", "bwrap",
    "melt", "ffmpeg", "ffprobe", "sh", "bash", "python3",
})
MAX_PROCS = 64
MAX_SAMPLES = 720
MAX_STATUS_BYTES = 16384


def parse_status(content: str) -> dict[str, Any]:
    """Parse only bounded Linux /proc/<pid>/status fields."""
    fields: dict[str, str] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name in {"Name", "PPid", "State", "VmRSS", "VmHWM"}:
            fields[name] = value.strip()
    role = fields.get("Name", "")
    role = role if role in ALLOWED_ROLES else "other"
    ppid = fields.get("PPid", "")
    state = fields.get("State", "")
    def kib(key: str) -> int | None:
        matched = re.fullmatch(r"([0-9]+) kB", fields.get(key, ""))
        return int(matched.group(1)) if matched else None
    return {
        "role": role,
        "ppid": int(ppid) if ppid.isdigit() else -1,
        "state": state[:1] if state[:1] in "RSDTZIX" else "?",
        "rss_kib": kib("VmRSS"),
        "peak_rss_kib": kib("VmHWM"),
    }


def tree_snapshot(root_pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    """Resolve only the daemon's descendants from a bounded /proc listing."""
    if root_pid <= 0:
        return {"root_observed": False, "truncated": False, "processes": []}
    processes = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return {"root_observed": False, "truncated": False, "processes": []}
    for path in entries:
        if not path.name.isdecimal():
            continue
        try:
            # Never read a root-owned proc text file larger than a small cap.
            with (path / "status").open("rb") as handle:
                data = handle.read(MAX_STATUS_BYTES + 1)
            if len(data) > MAX_STATUS_BYTES:
                continue
            processes[int(path.name)] = parse_status(data.decode("ascii", "replace"))
        except (OSError, ValueError):
            continue

    seen: set[int] = set()
    active: set[int] = {root_pid}
    while active and len(seen) <= MAX_PROCS:
        pid = active.pop()
        if pid in seen or pid not in processes:
            continue
        seen.add(pid)
        active.update(
            child_pid for child_pid, info in processes.items()
            if info["ppid"] == pid and child_pid not in seen
        )
    items = [{"pid": pid, **processes[pid]} for pid in sorted(seen)[:MAX_PROCS]]
    return {
        "root_observed": root_pid in processes,
        "truncated": len(seen) > MAX_PROCS or bool(active),
        "processes": items,
    }


def memory_events(cgroup_root: Path = Path("/sys/fs/cgroup")) -> dict[str, int | None]:
    """Read numeric cgroup-v2 counters without exposing cgroup paths."""
    result: dict[str, int | None] = {
        "current_bytes": None,
        "peak_bytes": None,
        "limit_bytes": None,
        "oom": None,
        "oom_kill": None,
    }
    for filename, output in (
        ("memory.current", "current_bytes"),
        ("memory.peak", "peak_bytes"),
        ("memory.max", "limit_bytes"),
    ):
        try:
            value = (cgroup_root / filename).read_text(encoding="ascii").strip()
            if value.isdecimal():
                result[output] = int(value)
        except OSError:
            continue
    try:
        for line in (cgroup_root / "memory.events").read_text(encoding="ascii").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in {"oom", "oom_kill"} and parts[1].isdecimal():
                result[parts[0]] = int(parts[1])
    except OSError:
        pass
    return result


def summarize_render_receipts(receipts: object) -> dict[str, Any]:
    """Minimal privacy-preserving failure timeline from application receipts.

    Never serializes job references, request IDs, payload JSON, full provider
    messages, filesystem paths or session material into the incident summary.
    """
    if not isinstance(receipts, list):
        raise ValueError("Motionwright failure diagnostic lacks a receipt list")
    ordered: list[tuple[str, str, str, bool | None, str]] = []
    started_refs: set[str] = set()
    terminal_refs: set[str] = set()
    unresolved_refs: set[str] = set()

    def owned_job_reference(payload: dict) -> str | None:
        result = payload.get("result")
        data = result.get("data") if isinstance(result, dict) else None
        candidate = payload.get("job_ref") or (
            data.get("job_ref") if isinstance(data, dict) else None
        )
        # Identifier is used solely for in-memory equality. Never emit it.
        return candidate if isinstance(candidate, str) and 0 < len(candidate) <= 512 else None

    for entry in receipts[:512]:
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        if command not in {
            "driver.motion-canvas.render.start",
            "driver.motion-canvas.render.status",
            "driver.motion-canvas.render.result",
        }:
            continue
        stage = entry.get("stage")
        if stage not in {"dispatching", "completed", "failed_known", "outcome_unknown"}:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        job_ref = owned_job_reference(payload)
        if command.endswith(".start") and stage == "completed" and job_ref:
            started_refs.add(job_ref)
        elif command.endswith(".result") and stage == "completed" and job_ref:
            wrapped = payload.get("result")
            data = wrapped.get("data") if isinstance(wrapped, dict) else None
            if isinstance(data, dict) and data.get("state") == "succeeded" and data.get("artifact"):
                terminal_refs.add(job_ref)
        elif command.endswith(".status") and stage in {"outcome_unknown", "failed_known"} and job_ref:
            unresolved_refs.add(job_ref)
        error = payload.get("error")
        if not isinstance(error, dict):
            error = {}
        code = error.get("code", "")
        if code not in {"Timeout", "Unavailable", "BackendFailed", "Cancelled",
                        "Conflict", "Internal", "StaleReference", "PermissionDenied"}:
            code = "other" if code else "none"
        known = error.get("outcome_known")
        if not isinstance(known, bool):
            known = None
        timestamp = entry.get("created_at")
        # Timestamps are bounded to prevent embedding unexpected provider text.
        timestamp = timestamp if isinstance(timestamp, str) and re.fullmatch(
            r"[0-9TZ:\-.+]{10,40}", timestamp,
        ) else "unknown"
        ordered.append((command.rsplit(".", 1)[1], stage, code, known, timestamp))
    ordered.sort(key=lambda row: row[4])
    events = [
        {"command": command, "stage": stage, "error_code": code,
         "read_outcome_known": known, "time_utc": timestamp}
        for command, stage, code, known, timestamp in ordered
    ]
    starts = [row for row in ordered if row[0] == "start" and row[1] == "dispatching"]
    statuses = [row for row in ordered if row[0] == "status"]
    # Only a terminal receipt for the SAME acknowledged native job may
    # resolve an earlier unknown status. An unrelated segment's result cannot
    # upgrade another render to SUCCESS.
    result_confirmed = bool(started_refs) and started_refs.issubset(terminal_refs)
    unknown = bool(started_refs & (unresolved_refs - terminal_refs))
    # An inactive provider *after* a timeout is diagnostically different
    # from an unrelated earlier Unavailable or a confirmed terminal result.
    loss_pattern = any(
        earlier[2] == "Timeout" and later[2] == "Unavailable"
        for index, earlier in enumerate(statuses)
        for later in statuses[index + 1:]
    )
    return {
        "schema": "motionwright-ci-render-status-reconciliation/1",
        "read_only": True,
        "render_start_dispatch_count": len(starts),
        "restart_identity_verified": False,
        "result_observed_completed": result_confirmed,
        "render_outcome": (
            "unconfirmed" if unknown else
            "confirmed_result_receipt" if result_confirmed else "not_proven"
        ),
        "timeout_then_provider_unavailable_observed": loss_pattern,
        "status_events": [row for row in events if row["command"] == "status"],
    }


class ProviderLifecycleSampler:
    """Low-frequency passive sample of CI daemon descendants and memory use."""

    def __init__(self, daemon: Any, evidence_path: Path, interval: float = 2.0):
        self.daemon = daemon
        self.evidence_path = evidence_path
        self.interval = max(0.25, interval)
        self.started = time.monotonic()
        self.samples: list[dict[str, Any]] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._sample_loop, name="native-provider-observer", daemon=True)
        self.before = memory_events()
        self.finished = False

    def start(self) -> None:
        self.thread.start()

    def _sample_loop(self) -> None:
        while not self.stop_event.is_set() and len(self.samples) < MAX_SAMPLES:
            state = tree_snapshot(self.daemon.pid)
            self.samples.append({
                "elapsed_ms": int((time.monotonic() - self.started) * 1000),
                **state,
                "cgroup": memory_events(),
            })
            if self.daemon.poll() is not None:
                break
            self.stop_event.wait(self.interval)

    def stop(self) -> dict[str, Any]:
        if self.finished:
            raise RuntimeError("Lifecycle sampler may only be finalized once")
        self.finished = True
        self.stop_event.set()
        self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            raise RuntimeError("Lifecycle sampler did not stop before teardown")
        exit_code = self.daemon.poll()
        roles = sorted({
            process["role"] for sample in self.samples for process in sample["processes"]
        })
        peak = max((
            process.get("peak_rss_kib") or process.get("rss_kib") or 0
            for sample in self.samples for process in sample["processes"]
        ), default=0)
        baseline_oom = self.before.get("oom_kill")
        last_oom = memory_events().get("oom_kill")
        result = {
            "schema": "motionwright-ci-provider-lifecycle/1",
            "authority": "passive-ci-process-status-not-driver-evidence",
            "sample_count": len(self.samples),
            "observed_roles": roles,
            "peak_descendant_rss_kib": peak,
            "daemon_exit_before_owner_cleanup": exit_code,
            "daemon_exit_signal": -exit_code if exit_code is not None and exit_code < 0 else None,
            "cgroup_oom_kills_delta": (
                last_oom - baseline_oom
                if isinstance(baseline_oom, int) and isinstance(last_oom, int)
                   and last_oom >= baseline_oom else None
            ),
            "samples": self.samples,
        }
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with self.evidence_path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
        self.evidence_path.chmod(0o600)
        return result
