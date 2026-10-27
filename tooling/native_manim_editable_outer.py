#!/usr/bin/env python3
"""One original monotonic budget and direct-process custody for editable Manim.

The clock is created before the native job's first preparation command. Every
workflow join and all four product phases inherit that record; none creates a
new allowance. This controller is not a native-result oracle.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

TOTAL_SECONDS = 2520
TERMINAL_SECONDS = 30
SEAL_SECONDS = 120
UPLOAD_SECONDS = 180
WORK_SECONDS = TOTAL_SECONDS - TERMINAL_SECONDS - SEAL_SECONDS - UPLOAD_SECONDS
NANOSECOND = 1_000_000_000
BODY_MARKER = "# MANIM_EDITABLE_OUTER_ORIGINAL_BODY\n"
STREAM_LIMIT = 4 * 1024 * 1024
WRAPPER_TERM_GRACE = 20
DIRECT_TERM_GRACE = 5
DIRECT_KILL_GRACE = 5
PRIVATE_ERROR_LIMIT = 64 * 1024
_OUTER_LAUNCH_CRITICAL = 0
_OUTER_CANCEL_SIGNAL = None
_OUTER_CANCEL_DELIVERED = False
STEPS = {
    "install-custody": "work", "validate-editable": "work", "resolve-pin": "work",
    "verify-sources": "work", "install-system": "work", "provision-runtime": "work",
    "sandbox-profile": "work", "build-sdk": "work", "build-product": "work",
    "rust-oracles": "work", "native-wrapper": "work",
    "seal-outer": "seal", "public-safety": "seal", "verify-cycle": "seal",
}
ACTIONS = {
    "sdk-checkout": ("work", 120), "toolchain": ("work", 300),
    "rust-upload": ("work", 60),
    "public-upload": ("upload", 60), "private-upload": ("upload", 120),
}


def raise_deferred_workflow_cancellation():
    """A latched cancellation blocks every later original launch in this owner."""
    global _OUTER_CANCEL_DELIVERED
    if _OUTER_CANCEL_SIGNAL is not None:
        _OUTER_CANCEL_DELIVERED = True
        raise InterruptedError("original workflow cancellation signal " + str(_OUTER_CANCEL_SIGNAL))


def workflow_cancelled(signum, _frame):
    """Defer delivery until the returned original handle has been published."""
    global _OUTER_CANCEL_SIGNAL
    # The first latch is immutable. Repetition must not deliver cancellation
    # during publication or while an earlier error is leaving that guard.
    if _OUTER_CANCEL_SIGNAL is not None:
        return
    _OUTER_CANCEL_SIGNAL = signum
    if _OUTER_LAUNCH_CRITICAL or _OUTER_CANCEL_DELIVERED:
        return
    raise_deferred_workflow_cancellation()


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def original(path, limit=64 * 1024):
    from mlt_official_runtime_intake import capture_regular
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink()
            and path.resolve(strict=True) == path
            and all(not parent.is_symlink() for parent in path.parents),
            "editable_outer_noncanonical_original")
    return capture_regular(path, limit)[0]


def new_bytes(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb", buffering=0) as stream:
        require(stream.write(raw) == len(raw), "editable_outer_short_write")
        stream.flush()
        os.fsync(stream.fileno())


def new_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    new_bytes(path, raw)
    return sha(raw)


def private_exception(error):
    """Bound the original exception/chain/traceback without reading source files."""
    remaining, truncated, seen = 8192, False, set()
    def text(value, cap):
        nonlocal remaining, truncated
        raw = str(value).encode("utf-8", errors="replace")
        kept = raw[:min(cap, remaining)]
        remaining -= len(kept)
        truncated = truncated or len(kept) != len(raw)
        return kept.decode("utf-8", errors="replace")
    def capture(current, depth):
        nonlocal truncated
        if current is None:
            return None
        if id(current) in seen or depth == 4:
            truncated = True
            return {"chain_not_expanded": True}
        seen.add(id(current))
        try:
            message = text(current, 4096)
        except BaseException as formatting:
            message = "<message unavailable: " + type(formatting).__name__ + ">"
        frames, tb = [], current.__traceback__
        while tb is not None and len(frames) < 24:
            frames.append({"file": text(tb.tb_frame.f_code.co_filename, 512),
                           "function": text(tb.tb_frame.f_code.co_name, 128), "line": tb.tb_lineno})
            tb = tb.tb_next
        truncated = truncated or tb is not None
        return {"type": text(type(current).__name__, 128),
                "module": text(type(current).__module__, 256), "message": message,
                "traceback": frames, "traceback_truncated": tb is not None,
                "suppress_context": current.__suppress_context__,
                "cause": capture(current.__cause__, depth + 1),
                "context": (capture(current.__context__, depth + 1)
                            if current.__context__ is not current.__cause__ else None)}
    value = capture(error, 0)
    return {"original": value, "text_truncated": truncated,
            "text_budget_bytes": 8192, "automatic_retry": False}


def retain_wrapper_error(deadline, error, stage):
    """The outer already owns this private folder, including before phase custody."""
    folder = deadline.root / "work/native-wrapper"
    require(folder.is_dir() and folder.resolve(strict=True) == folder,
            "editable_wrapper_original_launch_folder_required")
    envelope = {"schema": "motionwright.manim.editable.wrapper-error/1",
                "context": deadline.context, "origin": deadline.binding(),
                "pid": os.getpid(), "stage": stage, "error": private_exception(error),
                "native_result_claim": "NOT_ASSESSED", "automatic_retry": False}
    raw = (json.dumps(envelope, indent=2, sort_keys=True) + "\n").encode()
    require(len(raw) <= PRIVATE_ERROR_LIMIT, "editable_wrapper_error_envelope_bound")
    error.original_private_envelope = envelope
    try:
        new_bytes(folder / "wrapper-error.json", raw)
    except BaseException as secondary:
        error.wrapper_error_custody_error = secondary
        # The partial first file remains original. One closed successor retains
        # the primary object and identifies the custody failure without replay.
        fallback = {**envelope, "first_custody_error": private_exception(secondary)}
        try:
            raw = (json.dumps(fallback, indent=2, sort_keys=True) + "\n").encode()
            require(len(raw) <= 2 * PRIVATE_ERROR_LIMIT, "editable_wrapper_error_successor_bound")
            new_bytes(folder / "wrapper-error-successor.json", raw)
        except BaseException as final:
            error.wrapper_error_successor_error = final
    return envelope


def current_context():
    repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_JOB") == "native-manim-roundtrip"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and sys.platform == "linux", "editable_outer_original_hosted_job_required")
    pin = json.loads(original(repo / "SOURCE_LOCK.json"))["dependencies"]["semwright"]["revision"]
    context = {"source_sha": os.environ["GITHUB_SHA"], "semwright_sha": pin,
               "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
    require(all(re.fullmatch(r"[0-9a-f]{40}", context[key]) for key in ("source_sha", "semwright_sha"))
            and re.fullmatch(r"[1-9][0-9]*", context["run_id"]) is not None,
            "editable_outer_invalid_source_context")
    return context


def control_root(temporary, current):
    return Path(temporary) / ("manim-editable-clock-" + current["source_sha"])


def boot_identity():
    with Path("/proc/sys/kernel/random/boot_id").open("rb") as stream:
        raw = stream.read(129)
    value = raw.decode("ascii").strip()
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is not None,
            "editable_outer_boot_identity_missing")
    return value


def origin_record(context, started_ns, boot_id):
    require(type(started_ns) is int and started_ns > 0 and isinstance(boot_id, str) and boot_id,
            "editable_outer_invalid_origin")
    return {"schema": "motionwright.manim.editable.outer-deadline/1", "context": dict(context),
            "clock": "time.monotonic_ns", "boot_id": boot_id,
            "origin_monotonic_ns": started_ns, "total_seconds": TOTAL_SECONDS,
            "work_deadline_ns": started_ns + WORK_SECONDS * NANOSECOND,
            "terminal_deadline_ns": started_ns + (WORK_SECONDS + TERMINAL_SECONDS) * NANOSECOND,
            "seal_deadline_ns": started_ns + (TOTAL_SECONDS - UPLOAD_SECONDS) * NANOSECOND,
            "deadline_ns": started_ns + TOTAL_SECONDS * NANOSECOND,
            "terminal_reserve_seconds": TERMINAL_SECONDS, "seal_reserve_seconds": SEAL_SECONDS,
            "upload_reserve_seconds": UPLOAD_SECONDS, "automatic_retry": False}


class DeadlineExceeded(TimeoutError):
    outcome_known = True


class OriginalCommandError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        self.outcome_known = receipt.get("status") != "UNKNOWN"
        super().__init__("editable_outer_original_command_" + receipt["status"].lower())


class OuterDeadline:
    def __init__(self, record, context, boot_id, *, origin_sha256, root, clock=None):
        require(type(record) is dict, "editable_outer_origin_object_required")
        expected = origin_record(context, record.get("origin_monotonic_ns"), boot_id)
        require(record == expected and record.get("automatic_retry") is False
                and all(type(record.get(key)) is int for key in expected
                        if key.endswith("_ns") or key.endswith("_seconds")),
                "editable_outer_origin_context_or_limits_changed")
        require(re.fullmatch(r"[0-9a-f]{64}", origin_sha256) is not None,
                "editable_outer_origin_digest_missing")
        self.record, self.context = dict(record), dict(context)
        self.origin_sha256, self.root = origin_sha256, Path(root)
        self.clock = time.monotonic_ns if clock is None else clock

    def remaining(self, stage="work", cap=None):
        key = {"work": "work_deadline_ns", "terminal": "terminal_deadline_ns",
               "seal": "seal_deadline_ns", "upload": "deadline_ns"}.get(stage)
        require(key is not None, "editable_outer_unknown_budget_stage")
        now = self.clock()
        require(type(now) is int and now >= self.record["origin_monotonic_ns"],
                "editable_outer_clock_reversed")
        remaining = (self.record[key] - now) / NANOSECOND
        if remaining <= 0:
            raise DeadlineExceeded("editable_outer_" + stage + "_deadline_exhausted")
        if cap is not None:
            require(type(cap) in (int, float) and math.isfinite(cap) and cap > 0,
                    "editable_outer_invalid_child_cap")
            remaining = min(remaining, cap)
        return remaining

    def admit_work(self):
        require(not (self.root / "work/stopped.json").exists(), "editable_outer_original_stop_no_replay")
        return self.remaining("work")

    def binding(self):
        return {"origin_sha256": self.origin_sha256,
                "origin_monotonic_ns": self.record["origin_monotonic_ns"],
                "deadline_ns": self.record["deadline_ns"],
                "work_deadline_ns": self.record["work_deadline_ns"]}


def load_deadline():
    context = current_context()
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    root = control_root(temporary, context)
    path = root / "origin.json"
    require(os.environ.get("MANIM_EDITABLE_OUTER_ORIGIN") == str(path),
            "editable_outer_original_clock_required")
    raw = original(path)
    expected = os.environ.get("MANIM_EDITABLE_OUTER_SHA256", "")
    require(sha(raw) == expected, "editable_outer_original_clock_changed")
    boot_id = boot_identity()
    return OuterDeadline(json.loads(raw), context, boot_id, origin_sha256=expected, root=root)


def initialize(started_ns):
    context = current_context()
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    root = control_root(temporary, context)
    require(not root.exists() and not root.is_symlink(), "editable_outer_origin_cannot_restart")
    boot_id = boot_identity()
    record = origin_record(context, started_ns, boot_id)
    root.mkdir(mode=0o700)
    (root / "work").mkdir(mode=0o700)
    (root / "closing").mkdir(mode=0o700)
    path = root / "origin.json"
    digest = new_json(path, record)
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as stream:
        stream.write("MANIM_EDITABLE_OUTER_ORIGIN=" + str(path) + "\n")
        stream.write("MANIM_EDITABLE_OUTER_SHA256=" + digest + "\n")
    return {"status": "ORIGINAL_CLOCK_CREATED", "origin_sha256": digest,
            "native_effect_invocations": 0, "automatic_retry": False}


def stop_original(deadline, label, status):
    path = deadline.root / "work/stopped.json"
    if not path.exists():
        new_json(path, {"schema": "motionwright.manim.editable.outer-stop/1", "label": label,
                       "status": status, "origin": deadline.binding(), "automatic_retry": False})


def terminate_original(process, deadline, stage, *, killpg=None, term_grace=DIRECT_TERM_GRACE):
    killpg = os.killpg if killpg is None else killpg
    errors, code = [], None
    end_stage = "terminal" if stage == "work" else "seal"
    for sig, cap in ((signal.SIGTERM, term_grace), (signal.SIGKILL, DIRECT_KILL_GRACE)):
        # Only this owner waits/reaps this original. A known/reaped original's
        # former process-group id must never receive another signal.
        try:
            observed = getattr(process, "returncode", None)
            if type(observed) is not int and callable(getattr(process, "poll", None)):
                observed = process.poll()
        except BaseException as error:
            errors.append(type(error).__name__)
            observed = getattr(process, "returncode", None)
            return observed if type(observed) is int else None, errors
        if type(observed) is int:
            return observed, errors
        try:
            allowance = deadline.remaining(end_stage, cap)
        except BaseException as error:
            errors.append(type(error).__name__)
            break
        try:
            killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(type(error).__name__)
        try:
            code = process.wait(timeout=allowance)
            require(type(code) is int, "editable_outer_unknown_terminal_code")
            break
        except BaseException as error:
            errors.append(type(error).__name__)
    return code, errors


def run_command(deadline, label, argv, cwd, *, popen=None, killpg=None):
    global _OUTER_LAUNCH_CRITICAL, _OUTER_CANCEL_DELIVERED
    raise_deferred_workflow_cancellation()
    require(label in STEPS and isinstance(argv, list) and argv
            and all(isinstance(value, str) and value and "\0" not in value for value in argv),
            "editable_outer_unknown_command")
    stage = STEPS[label]
    if stage == "work":
        deadline.admit_work()
    allowance = deadline.remaining(stage, SEAL_SECONDS if stage == "seal" else None)
    if stage == "seal":
        allowance -= 10  # Bounded direct-process terminal observation stays inside the seal reserve.
        if allowance <= 0:
            raise DeadlineExceeded("editable_outer_seal_terminal_reserve_unavailable")
    folder = deadline.root / ("work" if stage == "work" else "closing") / label
    folder.mkdir(mode=0o700, exist_ok=False)
    temporary = deadline.root.parent
    stdout_path = (temporary / "manim-editable-cycle-wrapper.log" if label == "native-wrapper"
                   else folder / "stdout.log")
    stderr_path = (temporary / "manim-editable-cycle-wrapper.stderr.log" if label == "native-wrapper"
                   else folder / "stderr.log")
    intent = {"schema": "motionwright.manim.editable.outer-launch-intent/1", "label": label,
              "context": deadline.context, "origin": deadline.binding(), "argv": argv,
              "cwd": str(cwd), "created_utc": utc(), "launch_requested": False,
              "execution": "NOT_STARTED", "automatic_retry": False}
    intent_sha = new_json(folder / "intent.json", intent)
    process, code, caught, terminated = None, None, None, False
    cleanup_errors, streams = [], []
    started_ns = None
    try:
        for path in (stdout_path, stderr_path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            streams.append(os.fdopen(fd, "wb", buffering=0))
        deadline.remaining(stage)
        new_json(folder / "launch-requested.json", {"intent_sha256": intent_sha,
                 "requested_monotonic_ns": deadline.clock(), "automatic_retry": False})
        spawn = subprocess.Popen if popen is None else popen
        _OUTER_LAUNCH_CRITICAL += 1
        try:
            # Recheck after entering the guard. A signal already latched before
            # this original attempt must not authorize a new process.
            raise_deferred_workflow_cancellation()
            process = spawn(argv, cwd=cwd, env=dict(os.environ), stdin=subprocess.DEVNULL,
                            stdout=streams[0], stderr=streams[1], start_new_session=True)
        except BaseException:
            # Capture error precedence while delivery is still guarded. Even a
            # first signal arriving during unwind must not replace this error.
            _OUTER_CANCEL_DELIVERED = True
            raise
        finally:
            _OUTER_LAUNCH_CRITICAL -= 1
        # Deliver only with process retained and the existing cleanup try active.
        # Never raise from the finally: a spawn exception remains the first error.
        raise_deferred_workflow_cancellation()
        require(type(process.pid) is int and process.pid > 0, "editable_outer_missing_original_pid")
        started_ns = deadline.clock()
        new_json(folder / "start.json", {"schema": "motionwright.manim.editable.outer-launch-start/1",
                 "context": deadline.context, "origin": deadline.binding(), "intent_sha256": intent_sha,
                 "pid": process.pid, "started_monotonic_ns": started_ns, "started_utc": utc(),
                 "automatic_retry": False})
        allowance = deadline.remaining(stage, SEAL_SECONDS if stage == "seal" else None)
        if stage == "seal":
            allowance -= 10
        if allowance <= 0:
            raise DeadlineExceeded("editable_outer_remaining_before_wait_exhausted")
        code = process.wait(timeout=allowance)
        require(type(code) is int, "editable_outer_missing_original_exit")
        try:
            deadline.remaining(stage)
        except DeadlineExceeded:
            cleanup_errors.append("DEADLINE_EXHAUSTED_AFTER_ORIGINAL_EXIT")
    except BaseException as error:
        caught = error
        if _OUTER_CANCEL_SIGNAL is not None:
            # A spawn error can precede delivery of the latched signal. Preserve
            # that first error; repeated signals cannot replace it during cleanup.
            _OUTER_CANCEL_DELIVERED = True
        # A direct exit already observed by this wait is final. Its former PGID
        # must never receive a later cancellation signal after possible reuse.
        if process is not None and type(code) is not int:
            terminated = True
            observed, failures = terminate_original(process, deadline, stage, killpg=killpg,
                term_grace=WRAPPER_TERM_GRACE if label == "native-wrapper" else DIRECT_TERM_GRACE)
            if type(observed) is int:
                code = observed
            cleanup_errors.extend(failures)
    finally:
        for stream in streams:
            try:
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
            try:
                stream.close()
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
    requested = (folder / "launch-requested.json").exists()
    status = ("UNKNOWN" if caught is not None and requested else
              "NOT_STARTED" if process is None else "EXITED" if type(code) is int else "UNKNOWN")
    observed_streams = {}
    for name, path in (("stdout", stdout_path), ("stderr", stderr_path)):
        try:
            raw = original(path, STREAM_LIMIT)
            observed_streams[name] = {"bytes": len(raw), "sha256": sha(raw)}
        except BaseException as error:
            cleanup_errors.append(name + ":" + type(error).__name__)
    wrapper_error, wrapper_error_type = None, None
    if label == "native-wrapper":
        for name, limit in (("wrapper-error.json", PRIVATE_ERROR_LIMIT),
                            ("wrapper-error-successor.json", 2 * PRIVATE_ERROR_LIMIT)):
            path = folder / name
            if not path.exists():
                continue
            try:
                raw = original(path, limit)
                value = json.loads(raw)
                require(value.get("schema") == "motionwright.manim.editable.wrapper-error/1"
                        and value.get("context") == deadline.context and value.get("origin") == deadline.binding()
                        and process is not None and value.get("pid") == process.pid
                        and value.get("automatic_retry") is False, "editable_wrapper_error_original_binding")
                wrapper_error_type = value["error"]["original"]["type"]
                require(type(wrapper_error_type) is str, "editable_wrapper_error_type")
                wrapper_error = {"path": name, "bytes": len(raw), "sha256": sha(raw), "stage": value["stage"]}
                break
            except BaseException as error:
                cleanup_errors.append(name + ":" + type(error).__name__)
    receipt = {"schema": "motionwright.manim.editable.outer-launch-terminal/1", "label": label,
               "context": deadline.context, "origin": deadline.binding(), "intent_sha256": intent_sha,
               "status": status, "pid": process.pid if process is not None else None,
               "started_monotonic_ns": started_ns, "finished_monotonic_ns": deadline.clock(),
               "finished_utc": utc(), "launch_requested": requested,
               "process_exit_known": type(code) is int, "original_exit": code,
               "termination_requested": terminated,
               "original_error": type(caught).__name__ if caught else wrapper_error_type,
               "wrapper_error": wrapper_error,
               "secondary_errors": cleanup_errors, "streams": observed_streams,
               "native_result_claim": "UNKNOWN" if status == "UNKNOWN" or terminated else "NOT_ASSESSED",
               "automatic_retry": False}
    try:
        new_json(folder / "terminal.json", receipt)
    except BaseException as error:
        # Preserve the primary exception and observation even if every later custody
        # attempt fails. Existing partial terminal/fallback bytes are never replaced.
        error.original_terminal = receipt
        error.secondary_custody_errors = []
        try:
            stop_original(deadline, label, "INCOMPLETE_TERMINAL")
        except BaseException as secondary:
            error.secondary_custody_errors.append({"stage": "stop-original",
                                                   "error": type(secondary).__name__})
        try:
            new_json(folder / "terminal-write-error.json", {"original_terminal": receipt,
                     "write_error": type(error).__name__, "automatic_retry": False,
                     "secondary_errors": list(error.secondary_custody_errors)})
        except BaseException as secondary:
            error.secondary_custody_errors.append({"stage": "terminal-write-error",
                                                   "error": type(secondary).__name__})
        error.add_note(json.dumps({"status": status, "original_exit": code,
                       "process_exit_known": type(code) is int, "automatic_retry": False,
                       "secondary_custody_errors": error.secondary_custody_errors}, sort_keys=True))
        raise
    if status != "EXITED" or code != 0 or cleanup_errors or wrapper_error is not None:
        failure = OriginalCommandError(receipt)
        try:
            stop_original(deadline, label, status if status != "EXITED" else "KNOWN_FAILURE")
        except BaseException as secondary:
            failure.secondary_custody_errors = [{"stage": "stop-original",
                                                 "error": type(secondary).__name__}]
            failure.add_note(json.dumps({"original_exit": code, "automatic_retry": False,
                             "secondary_custody_errors": failure.secondary_custody_errors}, sort_keys=True))
        if caught is not None:
            raise failure from caught
        raise failure
    return receipt


def workflow_step(deadline, label, script):
    raw = original(script, 1024 * 1024)
    marker = BODY_MARKER.encode()
    require(raw.count(marker) == 1, "editable_outer_workflow_body_marker")
    body = raw.split(marker, 1)[1]
    require(body.strip(), "editable_outer_empty_workflow_body")
    scripts = deadline.root / "scripts"
    scripts.mkdir(mode=0o700, exist_ok=True)
    target = scripts / (label + ".sh")
    new_bytes(target, body)
    return run_command(deadline, label,
                       ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(target)],
                       Path.cwd().resolve(strict=True))


def admit_action(deadline, label):
    require(label in ACTIONS, "editable_outer_unknown_action")
    stage, cap = ACTIONS[label]
    if stage == "work" and label != "rust-upload":
        deadline.admit_work()
    remaining = deadline.remaining(stage)
    minutes = math.floor(min(cap, remaining - 5) / 60)
    if minutes < 1:
        raise DeadlineExceeded("editable_outer_action_reserve_unavailable")
    folder = deadline.root / ("work" if stage == "work" else "closing") / ("action-" + label)
    folder.mkdir(mode=0o700, exist_ok=False)
    value = {"schema": "motionwright.manim.editable.outer-action-admission/1", "label": label,
             "context": deadline.context, "origin": deadline.binding(),
             "admitted_monotonic_ns": deadline.clock(), "timeout_minutes": minutes,
             "remaining_seconds_observed": remaining, "actual_action_result": "NOT_OBSERVED",
             "automatic_retry": False}
    new_json(folder / "intent.json", value)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        stream.write("minutes=" + str(minutes) + "\n")
    return value


def verify_launcher_originals(root, current):
    """Require an original known-zero direct wrapper; never infer native success."""
    temporary = root.parent
    control = control_root(temporary, current)
    launch = control / "work/native-wrapper"
    origin_raw = original(control / "origin.json")
    origin = json.loads(origin_raw)
    require(origin.get("context") == current, "editable_outer_launcher_source_changed")
    deadline = OuterDeadline(origin, current, origin.get("boot_id"), origin_sha256=sha(origin_raw), root=control)
    intent_raw = original(launch / "intent.json")
    intent, requested, start, terminal = (json.loads(raw) for raw in (
        intent_raw, original(launch / "launch-requested.json"),
        original(launch / "start.json"), original(launch / "terminal.json")))
    expected_argv = [str(Path(intent["cwd"]) / "tooling/native_manim_e2e.py")]
    require(intent.get("schema") == "motionwright.manim.editable.outer-launch-intent/1"
            and intent.get("label") == "native-wrapper" and intent.get("context") == current
            and intent.get("origin") == deadline.binding() and intent.get("automatic_retry") is False
            and intent.get("execution") == "NOT_STARTED" and intent.get("launch_requested") is False
            and intent.get("argv", [None])[0] == str(temporary / "manim-custody-venv/bin/python")
            and intent.get("argv", [])[1:3] == ["-B", expected_argv[0]]
            and intent["argv"][3:] == ["--scenario", "editable-source-cycle", "--evidence-root", str(root),
                                     "--sealed-cycle-root", str(temporary / ("manim-editable-sealed-" + current["source_sha"]))],
            "editable_outer_original_launcher_intent")
    require(requested.get("intent_sha256") == sha(intent_raw) and requested.get("automatic_retry") is False
            and type(requested.get("requested_monotonic_ns")) is int,
            "editable_outer_original_launcher_request")
    require(start.get("schema") == "motionwright.manim.editable.outer-launch-start/1"
            and start.get("context") == current and start.get("origin") == deadline.binding()
            and start.get("intent_sha256") == sha(intent_raw)
            and type(start.get("pid")) is int and start["pid"] > 0
            and type(start.get("started_monotonic_ns")) is int
            and requested["requested_monotonic_ns"] <= start["started_monotonic_ns"],
            "editable_outer_original_launcher_start")
    require(terminal.get("schema") == "motionwright.manim.editable.outer-launch-terminal/1"
            and terminal.get("context") == current and terminal.get("origin") == deadline.binding()
            and terminal.get("intent_sha256") == sha(intent_raw) and terminal.get("pid") == start["pid"]
            and terminal.get("status") == "EXITED" and terminal.get("process_exit_known") is True
            and type(terminal.get("original_exit")) is int and terminal["original_exit"] == 0
            and terminal.get("termination_requested") is False and terminal.get("launch_requested") is True
            and terminal.get("original_error") is None and terminal.get("secondary_errors") == []
            and terminal.get("native_result_claim") == "NOT_ASSESSED"
            and terminal.get("automatic_retry") is False
            and terminal.get("started_monotonic_ns") == start.get("started_monotonic_ns")
            and type(terminal.get("finished_monotonic_ns")) is int
            and origin["origin_monotonic_ns"] <= start["started_monotonic_ns"] <= terminal["finished_monotonic_ns"]
                <= origin["work_deadline_ns"], "editable_outer_original_launcher_not_known_success")
    for name, suffix in (("stdout", ".log"), ("stderr", ".stderr.log")):
        raw = original(temporary / ("manim-editable-cycle-wrapper" + suffix), STREAM_LIMIT)
        require(terminal.get("streams", {}).get(name) == {"bytes": len(raw), "sha256": sha(raw)},
                "editable_outer_original_launcher_stream_changed")
    return terminal


def main(argv=None):
    origin = time.monotonic_ns()  # Captured before controlled preparation or source/bootstrap reads.
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("initialize")
    step = sub.add_parser("workflow-step")
    step.add_argument("--label", choices=tuple(STEPS), required=True)
    step.add_argument("--script", type=Path, required=True)
    action = sub.add_parser("admit-action")
    action.add_argument("--label", choices=tuple(ACTIONS), required=True)
    sub.add_parser("launch-native")
    args = parser.parse_args(argv)
    if args.command == "initialize":
        result = initialize(origin)
    else:
        deadline = load_deadline()
        if args.command == "workflow-step":
            result = workflow_step(deadline, args.label, args.script.resolve(strict=True))
        elif args.command == "admit-action":
            result = admit_action(deadline, args.label)
        else:
            temporary = deadline.root.parent
            repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
            interpreter = temporary / "manim-custody-venv/bin/python"
            result = run_command(deadline, "native-wrapper", [str(interpreter), "-B",
                str(repo / "tooling/native_manim_e2e.py"), "--scenario", "editable-source-cycle",
                "--evidence-root", str(temporary / ("manim-editable-private-" + deadline.context["source_sha"])),
                "--sealed-cycle-root", str(temporary / ("manim-editable-sealed-" + deadline.context["source_sha"]))], repo)
    print(json.dumps({"status": result.get("status", "ADMITTED_WITH_REMAINING_BUDGET"), "automatic_retry": False}))
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, workflow_cancelled)
    signal.signal(signal.SIGINT, workflow_cancelled)
    try:
        raise SystemExit(main())
    except (OriginalCommandError, DeadlineExceeded, InterruptedError) as error:
        print(json.dumps({"status": "UNKNOWN" if not getattr(error, "outcome_known", False) else "FAIL",
                          "error": type(error).__name__, "automatic_retry": False}), file=sys.stderr)
        raise SystemExit(1)
