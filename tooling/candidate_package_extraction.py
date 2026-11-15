"""One owned Linux package extraction, with retained kernel subtree closure.

The caller supplies existing package-scope admission. This module grants no
Native, application, installer, or arbitrary workflow authority. It reuses the
first-use supervisor's exec barrier, retained pidfds and subreaper waitid
ECHILD proof. macOS and Windows have no admitted implementation here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import sys
import threading
import time

import startup_capture_core as core

SCHEMA = "motionwright.package-extraction-owner/1"
MAX_SECONDS = 90
MAX_STREAM = 8 * 1024 * 1024
MAX_RECORD = 64 * 1024
SUFFIXES = {"request": "request.json", "completion": "completion.json",
            "stdout": "stdout", "stderr": "stderr", "subtree": "subtree.original",
            "binding": "binding.json", "closure": "closure.json"}
IDENTITY_FIELDS = {"pid", "parent_pid", "process_group_id", "session_id", "start_ticks"}


class ExtractionError(RuntimeError):
    def __init__(self, code, *, retained=None):
        super().__init__(code)
        # An unresolved supervisor retains its handles. This is never closure.
        self.retained = retained


def require(value, code):
    if not value:
        raise ExtractionError(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sources():
    return {"owner": sha(Path(__file__).read_bytes()),
            "supervisor": sha(Path(core.__file__).read_bytes())}


@dataclass(frozen=True)
class Budget:
    started_ns: int
    seconds: int = MAX_SECONDS

    def __post_init__(self):
        require(type(self.started_ns) is int and self.started_ns >= 0,
                "extraction_invalid_monotonic_origin")
        require(type(self.seconds) is int and 3 <= self.seconds <= MAX_SECONDS,
                "extraction_timeout_not_bounded")

    @property
    def operation_ns(self):
        return self.started_ns + (self.seconds - 2) * 1_000_000_000

    @property
    def join_ns(self):
        return self.started_ns + (self.seconds - 1) * 1_000_000_000

    @property
    def cleanup_ns(self):
        return self.started_ns + self.seconds * 1_000_000_000

    def record(self):
        return {"started_ns": self.started_ns, "seconds": self.seconds,
                "operation_ns": self.operation_ns, "join_ns": self.join_ns,
                "cleanup_ns": self.cleanup_ns}


def require_supported():
    core._linux()
    require(hasattr(os, "P_PIDFD") and hasattr(os, "waitid")
            and threading.active_count() == 1, "extraction_owned_wait_unavailable")


def request(command, cwd, label):
    require(isinstance(command, list) and 1 <= len(command) <= 16
            and all(isinstance(part, str) and 0 < len(part) <= 4096 and "\0" not in part
                    for part in command), "extraction_command_invalid")
    require(Path(command[0]).is_absolute() and Path(cwd).is_absolute()
            and label == "extract", "extraction_scope_invalid")
    value = {"command": command, "cwd": str(cwd), "label": label, "executed_once": True}
    require(len(encoded(value)) <= MAX_RECORD, "extraction_request_budget")
    return value


def _fields(value, names, code):
    require(isinstance(value, dict) and set(value) == set(names), code)


def _identity(value, *, supervisor=False, cli=False):
    fields = IDENTITY_FIELDS | ({"source_sha256"} if supervisor else set()) | (
        {"parent_start_ticks"} if cli else set())
    _fields(value, fields, "extraction_identity_fields")
    require(all(type(value[key]) is int and value[key] > 0
                for key in fields - {"source_sha256", "parent_pid"})
            and type(value["parent_pid"]) is int and value["parent_pid"] >= 0,
            "extraction_identity_type")
    if supervisor:
        require(isinstance(value["source_sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", value["source_sha256"]),
                "extraction_supervisor_source")


def validate_originals(originals, expected_request, expected_sources):
    """Revalidate retained originals; never turns caller booleans into closure.

    Production originals are written only by run_owned after the independently
    observed pre-exec identities and pidfd wait. The inventory additionally
    binds all raw bytes to its external producer seal and authenticated source.
    """
    require(set(originals) == set(SUFFIXES), "extraction_original_set")
    for key, raw in originals.items():
        limit = MAX_STREAM if key in ("stdout", "stderr") else (
            core.STATUS_LIMIT if key == "subtree" else MAX_RECORD)
        require(isinstance(raw, bytes) and len(raw) <= limit, "extraction_original_budget")
    values = {key: core.decode_frame(originals[key], eof=True, limit=MAX_RECORD)
              for key in ("request", "completion", "binding", "closure")}
    actual_request = values["request"]
    _fields(actual_request, ("command", "cwd", "label", "executed_once"),
            "extraction_request_fields")
    require(actual_request == expected_request and actual_request["executed_once"] is True,
            "extraction_request_changed")
    binding = values["binding"]
    _fields(binding, ("schema", "owner", "supervisor", "cli", "retained_pidfds",
                      "bound_monotonic_ns", "exec_admitted_monotonic_ns"),
            "extraction_binding_fields")
    require(binding["schema"] == SCHEMA + "/binding", "extraction_binding_schema")
    _identity(binding["owner"])
    _identity(binding["supervisor"], supervisor=True)
    _identity(binding["cli"], cli=True)
    _fields(binding["retained_pidfds"], ("supervisor", "cli"), "extraction_pidfd_fields")
    require(all(type(fd) is int and fd >= 0 for fd in binding["retained_pidfds"].values())
            and len(set(binding["retained_pidfds"].values())) == 2,
            "extraction_pidfd_identity")
    require(binding["supervisor"]["parent_pid"] == binding["owner"]["pid"]
            and binding["cli"]["parent_pid"] == binding["supervisor"]["pid"]
            and binding["cli"]["parent_start_ticks"] == binding["supervisor"]["start_ticks"]
            and binding["supervisor"]["source_sha256"] == expected_sources["supervisor"],
            "extraction_parent_or_source_changed")
    closure = values["closure"]
    _fields(closure, ("schema", "budget", "producer_sources", "binding_sha256",
                      "subtree_sha256", "streams", "supervisor_wait",
                      "cli_pidfd_exit_monotonic_ns", "streams_completed_monotonic_ns",
                      "completed_monotonic_ns", "errors"),
            "extraction_closure_fields")
    require(closure["schema"] == SCHEMA + "/closure"
            and closure["producer_sources"] == expected_sources
            and closure["binding_sha256"] == sha(originals["binding"])
            and closure["subtree_sha256"] == sha(originals["subtree"])
            and closure["errors"] == [], "extraction_closure_binding_or_failure")
    b = closure["budget"]
    _fields(b, ("started_ns", "seconds", "operation_ns", "join_ns", "cleanup_ns"),
            "extraction_budget_fields")
    budget = Budget(b["started_ns"], b["seconds"])
    require(b == budget.record()
            and all(type(b[key]) is int for key in b), "extraction_budget_changed")
    times = [binding["bound_monotonic_ns"], binding["exec_admitted_monotonic_ns"],
             closure["cli_pidfd_exit_monotonic_ns"], closure["completed_monotonic_ns"]]
    require(all(type(value) is int for value in times)
            and budget.started_ns <= times[0] <= times[1] <= budget.operation_ns
            and times[1] <= times[2] <= times[3] <= budget.cleanup_ns,
            "extraction_binding_or_exit_deadline")
    joined = closure["streams_completed_monotonic_ns"]
    require(type(joined) is int and times[1] <= joined <= budget.join_ns
            and joined <= times[3], "extraction_stream_join_deadline")
    _fields(closure["streams"], ("stdout", "stderr", "subtree"), "extraction_stream_fields")
    for key, row in closure["streams"].items():
        _fields(row, ("bytes", "sha256", "eof", "truncated"), "extraction_stream_identity")
        require(type(row["bytes"]) is int and row["bytes"] == len(originals[key])
                and row["sha256"] == sha(originals[key]) and row["eof"] is True
                and row["truncated"] is False, "extraction_stream_not_complete")
    wait = closure["supervisor_wait"]
    _fields(wait, ("mechanism", "si_pid", "si_code", "si_status",
                   "pidfd_observed_monotonic_ns", "wait_observed_monotonic_ns"),
            "extraction_wait_fields")
    require(wait["mechanism"] == "waitid_P_PIDFD"
            and type(wait["si_pid"]) is int and wait["si_pid"] == binding["supervisor"]["pid"]
            and type(wait["si_code"]) is int and wait["si_code"] == 1
            and type(wait["si_status"]) is int and wait["si_status"] == 0
            and type(wait["pidfd_observed_monotonic_ns"]) is int
            and type(wait["wait_observed_monotonic_ns"]) is int
            and times[1] <= wait["pidfd_observed_monotonic_ns"]
            <= wait["wait_observed_monotonic_ns"] <= times[3],
            "extraction_supervisor_wait_not_closed")
    parsed = core.validate_transcript(
        originals["subtree"], eof=True, expected_supervisor=binding["supervisor"],
        expected_cli=binding["cli"], deadline_ns=budget.join_ns)
    require(parsed["terminal"]["cli_exit_code"] == 0
            and times[0] >= parsed["launch"]["observed_monotonic_ns"]
            and times[1] <= parsed["terminal"]["completed_monotonic_ns"]
            <= wait["pidfd_observed_monotonic_ns"], "extraction_terminal_order_or_exit")
    completed = values["completion"]
    _fields(completed, set(expected_request) | {
        "returncode", "errors", "stdout_sha256", "stderr_sha256", "closure_sha256"},
        "extraction_completion_fields")
    require(all(completed[key] == value for key, value in expected_request.items())
            and completed["executed_once"] is True
            and type(completed["returncode"]) is int and completed["returncode"] == 0
            and completed["errors"] == []
            and completed["stdout_sha256"] == sha(originals["stdout"])
            and completed["stderr_sha256"] == sha(originals["stderr"])
            and completed["closure_sha256"] == sha(originals["closure"]),
            "extraction_completion_not_successful")
    return parsed


def run_owned(command, cwd, directory, label, *, env=None, stream_limit=MAX_STREAM,
              timeout=MAX_SECONDS):
    """Invoke once; success requires closed descendants, complete originals and waits."""
    require_supported()  # No output or process effects on unsupported platforms.
    expected = request(command, cwd, label)
    require(type(stream_limit) is int and 1 <= stream_limit <= MAX_STREAM,
            "extraction_stream_limit")
    budget = Budget(time.monotonic_ns(), timeout)
    producer_sources = sources()
    directory = Path(directory)
    require(directory.is_absolute() and directory.is_dir(), "extraction_output_directory")
    paths = {key: directory / (label + "." + suffix) for key, suffix in SUFFIXES.items()}
    require(all(not os.path.lexists(path) for path in paths.values()),
            "extraction_originals_already_exist")
    with paths["request"].open("xb") as stream:
        stream.write(encoded(expected))
    fds, supervisor, cli_fd, pump = set(), None, None, None
    binding, wait, cli_exit_ns = None, None, None
    returncode, errors, completed_ns = None, [], None

    def error(code):
        if code not in errors and len(errors) < 16:
            errors.append(code)

    try:
        out_r, out_w = os.pipe()
        fds.update((out_r, out_w))
        err_r, err_w = os.pipe()
        fds.update((err_r, err_w))
        old_cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.chdir(cwd)
            supervisor = core.start_supervisor(
                command, dict(os.environ if env is None else env), out_w, err_w, budget)
        finally:
            os.fchdir(old_cwd)
            os.close(old_cwd)
        fds.update((supervisor.status_fd, supervisor.pidfd))
        for fd in (out_w, err_w):
            fds.remove(fd)
            os.close(fd)
        pump = core.CapturePump(
            {out_r: stream_limit, err_r: stream_limit, supervisor.status_fd: core.STATUS_LIMIT},
            budget)
        owner_identity = core.proc_identity(os.getpid())
        while time.monotonic_ns() < budget.cleanup_ns:
            if pump.pending and time.monotonic_ns() < budget.join_ns:
                try:
                    pump.step()
                except (OSError, core.CaptureError) as exc:
                    error("capture:" + type(exc).__name__)
            if any(value.truncated for value in pump.streams.values()):
                error("original_stream_limit")
            if binding is None and not errors:
                raw = bytes(pump.streams[supervisor.status_fd].data)
                if b"\n" in raw:
                    launch = core.decode_frame(raw.split(b"\n", 1)[0] + b"\n",
                                               eof=True, limit=core.FRAME_LIMIT)
                    require(launch.get("kind") == "LAUNCH_BOUND"
                            and launch.get("schema") == core.SCHEMA
                            and type(launch.get("sequence")) is int and launch["sequence"] == 1,
                            "extraction_launch_frame")
                    observed_supervisor = core.proc_identity(supervisor.pid)
                    observed_supervisor["source_sha256"] = producer_sources["supervisor"]
                    require(observed_supervisor == launch.get("supervisor")
                            and observed_supervisor["start_ticks"] == supervisor.start_ticks
                            and observed_supervisor["parent_pid"] == os.getpid(),
                            "extraction_supervisor_identity")
                    announced = launch.get("cli")
                    _identity(announced, cli=True)
                    require(announced["parent_pid"] == supervisor.pid
                            and announced["parent_start_ticks"] == supervisor.start_ticks
                            and announced["pid"] == announced["session_id"]
                            == announced["process_group_id"], "extraction_cli_parent")
                    cli_fd = os.pidfd_open(announced["pid"])
                    fds.add(cli_fd)
                    observed_cli = core.proc_identity(announced["pid"])
                    observed_cli["parent_start_ticks"] = supervisor.start_ticks
                    require(observed_cli == announced
                            and not select.select([cli_fd, supervisor.pidfd], [], [], 0)[0],
                            "extraction_preexec_identity_changed")
                    binding = {"schema": SCHEMA + "/binding", "owner": owner_identity,
                               "supervisor": observed_supervisor, "cli": observed_cli,
                               "retained_pidfds": {"supervisor": supervisor.pidfd, "cli": cli_fd},
                               "bound_monotonic_ns": time.monotonic_ns(),
                               "exec_admitted_monotonic_ns": None}
                    require(binding["bound_monotonic_ns"] < budget.operation_ns,
                            "extraction_preexec_deadline")
                    supervisor.admit_exec()
                    binding["exec_admitted_monotonic_ns"] = time.monotonic_ns()
                elif len(raw) > core.FRAME_LIMIT:
                    error("launch_frame_budget")
            if errors:
                supervisor.abort()
            if cli_fd is not None and cli_exit_ns is None:
                if select.select([cli_fd], [], [], 0)[0]:
                    cli_exit_ns = time.monotonic_ns()
            if wait is None and select.select([supervisor.pidfd], [], [], 0)[0]:
                pidfd_ns = time.monotonic_ns()
                terminal = os.waitid(os.P_PIDFD, supervisor.pidfd, os.WEXITED | os.WNOHANG)
                if terminal is not None:
                    wait = {"mechanism": "waitid_P_PIDFD", "si_pid": terminal.si_pid,
                            "si_code": terminal.si_code, "si_status": terminal.si_status,
                            "pidfd_observed_monotonic_ns": pidfd_ns,
                            "wait_observed_monotonic_ns": time.monotonic_ns()}
            if wait is not None and not pump.pending:
                break
            if time.monotonic_ns() >= budget.join_ns:
                error("original_join_deadline")
                supervisor.abort()
                # Only the already retained supervisor is waited here. The
                # subreaper owns descendant cleanup; no name/PID-wide signals.
                select.select([supervisor.pidfd], [], [], .005)
        completed_ns = time.monotonic_ns()
        require(wait is not None and not pump.pending and cli_exit_ns is not None
                and binding is not None and binding["exec_admitted_monotonic_ns"] is not None,
                "extraction_originals_or_wait_incomplete")
        pump.require_complete()
        parsed = core.validate_transcript(
            bytes(pump.streams[supervisor.status_fd].data), eof=True,
            expected_supervisor=binding["supervisor"], expected_cli=binding["cli"],
            deadline_ns=budget.join_ns)
        returncode = parsed["terminal"]["cli_exit_code"]
        require(returncode == 0 and wait["si_code"] == os.CLD_EXITED
                and wait["si_status"] == 0 and completed_ns <= budget.cleanup_ns,
                "extraction_terminal_not_successful")
    except BaseException as exc:
        error("operation:" + type(exc).__name__)
        if isinstance(exc, core.RetainedChildError):
            supervisor = exc
        if supervisor is not None and hasattr(supervisor, "abort"):
            supervisor.abort()
        # Continue the original bounded capture and exact-child wait on failure.
        # Cleanup cannot extend the budget or turn a failed extraction into PASS.
        if pump is not None and isinstance(supervisor, core.SupervisorHandle):
            while time.monotonic_ns() < budget.cleanup_ns and (pump.pending or wait is None):
                if pump.pending and time.monotonic_ns() < budget.join_ns:
                    try:
                        pump.step()
                    except (OSError, core.CaptureError):
                        error("failure_capture_incomplete")
                try:
                    if wait is None and select.select([supervisor.pidfd], [], [], 0)[0]:
                        pidfd_ns = time.monotonic_ns()
                        terminal = os.waitid(os.P_PIDFD, supervisor.pidfd, os.WEXITED | os.WNOHANG)
                        if terminal is not None:
                            wait = {"mechanism": "waitid_P_PIDFD", "si_pid": terminal.si_pid,
                                    "si_code": terminal.si_code, "si_status": terminal.si_status,
                                    "pidfd_observed_monotonic_ns": pidfd_ns,
                                    "wait_observed_monotonic_ns": time.monotonic_ns()}
                    if time.monotonic_ns() >= budget.join_ns:
                        select.select([supervisor.pidfd], [], [], .005)
                except OSError:
                    error("failure_wait_unresolved")
                    break
        completed_ns = time.monotonic_ns()
    finally:
        if isinstance(supervisor, core.SupervisorHandle):
            supervisor.abort()
        unresolved = supervisor is not None and wait is None
        protected = {getattr(supervisor, "pidfd", None), getattr(supervisor, "status_fd", None)} if unresolved else set()
        for fd in fds - protected:
            try:
                os.close(fd)
            except OSError:
                error("descriptor_close_failed")

    raw_streams, stream_rows = {}, {}
    for key, fd in (("stdout", locals().get("out_r")), ("stderr", locals().get("err_r")),
                    ("subtree", getattr(supervisor, "status_fd", None))):
        stream = pump.streams.get(fd) if pump is not None else None
        raw_streams[key] = bytes(stream.data) if stream is not None else b""
        stream_rows[key] = {"bytes": len(raw_streams[key]), "sha256": sha(raw_streams[key]),
                            "eof": stream.eof if stream is not None else False,
                            "truncated": stream.truncated if stream is not None else False}
    binding_raw = encoded(binding if binding is not None else {
        "schema": SCHEMA + "/binding", "status": "NOT_BOUND"})
    closure = {"schema": SCHEMA + "/closure", "budget": budget.record(),
               "producer_sources": producer_sources, "binding_sha256": sha(binding_raw),
               "subtree_sha256": sha(raw_streams["subtree"]), "streams": stream_rows,
               "supervisor_wait": wait, "cli_pidfd_exit_monotonic_ns": cli_exit_ns,
               "streams_completed_monotonic_ns": pump.ended_ns if pump is not None else None,
               "completed_monotonic_ns": completed_ns, "errors": errors}
    closure_raw = encoded(closure)
    completion = {**expected, "returncode": returncode, "errors": errors,
                  "stdout_sha256": sha(raw_streams["stdout"]),
                  "stderr_sha256": sha(raw_streams["stderr"]),
                  "closure_sha256": sha(closure_raw)}
    originals = {"request": encoded(expected), **raw_streams, "binding": binding_raw,
                 "closure": closure_raw, "completion": encoded(completion)}
    for key, raw in originals.items():
        if key != "request":
            with paths[key].open("xb") as stream:
                stream.write(raw)
    if errors:
        raise ExtractionError("extraction_failed_originals_retained",
                              retained=supervisor if wait is None else None)
    validate_originals(originals, expected, producer_sources)
    return {"returncode": returncode, "process_closure": "SUBREAPER_ECHILD_AND_RETAINED_PIDFD_WAIT"}
