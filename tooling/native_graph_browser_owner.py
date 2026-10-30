#!/usr/bin/env python3
"""Per-case real Native Host custody for Studio Graph browser checks.

Only this helper's owned child process groups are stopped. Every command is a
single attempt with create-new original stdout/stderr/intent/terminal files.
Database, CAS and owner evidence are never removed. A timeout remains UNKNOWN.
The browser transport must close its own StudioService peers before asking this
owner to close and must combine both sets of terminal receipts before capture.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath


SCHEMA = "motionwright.native.graph-browser-owner/1"
LABEL = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RESOURCE = re.compile(r"project:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
CORE_PROJECT = re.compile(r"prj_[0-9a-f]{32}\Z")
MAX_CLIENTS = 64
MAX_CONTROL_BYTES = 1024
_CANCELLATION_CRITICAL = 0
_DEFERRED_SIGNALS = []
_OBSERVED_SIGNALS = []
GRAPH_HELPERS = frozenset(("manim_bundled_font_intake", "manim_graph_font_probe",
                         "manim_graph_vector_text", "manim_graph_svg_import",
                         "manim_graph_text_layout", "manim_graph_runtime"))


class GraphOwnerFailure(ValueError):
    pass


def enter_cancellation_boundary():
    global _CANCELLATION_CRITICAL
    _CANCELLATION_CRITICAL += 1


def leave_cancellation_boundary(*, propagate=True):
    global _CANCELLATION_CRITICAL
    _CANCELLATION_CRITICAL -= 1
    if _CANCELLATION_CRITICAL == 0 and _DEFERRED_SIGNALS:
        signum = _DEFERRED_SIGNALS.pop(0)
        _DEFERRED_SIGNALS.clear()
        if propagate:
            raise GraphOwnerFailure("owner-interrupted-unknown-no-retry:" + str(signum))


def raise_deferred_cancellation():
    if _CANCELLATION_CRITICAL == 0 and _DEFERRED_SIGNALS:
        signum = _DEFERRED_SIGNALS.pop(0)
        _DEFERRED_SIGNALS.clear()
        raise GraphOwnerFailure("owner-interrupted-unknown-no-retry:" + str(signum))


def require(condition, code):
    if not condition:
        raise GraphOwnerFailure(code)


def strict_json(raw):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate-json-key")
            result[key] = value
        return result
    def reject_constant(_value):
        raise GraphOwnerFailure("nonfinite-json")
    return json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def new_bytes(path, raw):
    path = Path(path)
    require(path.is_absolute() and path.parent.is_dir(), "original-parent-not-ready")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def new_json(path, value):
    new_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + "\n").encode("utf-8"))


def private_directory(path):
    path = Path(path)
    require(path.is_absolute() and not path.exists(), "fresh-private-directory-required")
    require(path.parent.is_dir() and path.parent.resolve(strict=True) == path.parent,
            "private-parent-not-canonical")
    path.mkdir(mode=0o700)
    require(stat.S_IMODE(path.stat().st_mode) == 0o700, "private-directory-mode")
    return path


def file_identity(path):
    path = Path(path)
    info = path.lstat()
    require(path.is_absolute() and path.resolve(strict=True) == path
            and stat.S_ISREG(info.st_mode) and not path.is_symlink(), "noncanonical-file")
    sha256 = digest(path)
    after = path.lstat()
    require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "file-changed-during-identity-observation")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "bytes": info.st_size, "sha256": sha256}


def group_observation(pgid):
    """Observe the owned Linux process group; zombies cannot retain writers."""
    require(sys.platform == "linux" and type(pgid) is int and pgid > 1,
            "owned-linux-group-required")
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return {"pgid": pgid, "group_exists": False, "members": [], "unknown": [],
                "writers_closed_within_owned_group": True}
    except PermissionError:
        return {"pgid": pgid, "group_exists": None, "members": [],
                "unknown": ["process-group-permission"], "writers_closed_within_owned_group": False}
    # Some diagnostic sandboxes expose /proc from a different PID namespace.
    # Never attribute an unrelated visible PID to an owned child group.
    try:
        same_namespace = int(Path("/proc/self/stat").read_text().split(" ", 1)[0]) == os.getpid()
    except (OSError, ValueError):
        same_namespace = False
    if not same_namespace:
        return {"pgid": pgid, "group_exists": True, "members": [],
                "unknown": ["proc-pid-namespace-unproved"], "writers_closed_within_owned_group": False}
    members, unknown = [], []
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        count += 1
        if count > 8192:
            unknown.append("process-observation-budget")
            break
        try:
            raw = (entry / "stat").read_text()
            suffix = raw[raw.rfind(")") + 2:].split()
            require(len(suffix) >= 20, "process-stat-shape")
            if int(suffix[2]) == pgid:
                members.append({"pid": int(entry.name), "state": suffix[0],
                                "parent_pid": int(suffix[1]), "start_ticks": int(suffix[19])})
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError):
            unknown.append(entry.name)
    if not members:
        # A group may disappear while /proc is scanned. Confirm that fact with
        # the same namespace-local operation; an empty observation alone is
        # never proof that an existing group has no writers.
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return {"pgid": pgid, "group_exists": False, "members": [], "unknown": unknown,
                    "writers_closed_within_owned_group": True}
        except PermissionError:
            unknown.append("process-group-permission-after-scan")
        else:
            unknown.append("owned-group-present-but-unobserved")
    return {"pgid": pgid, "group_exists": True, "members": members, "unknown": unknown,
            "writers_closed_within_owned_group": bool(members) and not unknown
            and all(row["state"] == "Z" for row in members)}


class OwnedChild:
    """A latched child attempt; terminal custody never retries its command."""
    def __init__(self, label, argv, env, evidence, *, cwd=None):
        require(LABEL.fullmatch(label) is not None and argv, "owned-command-shape")
        self.label, self.argv, self.evidence = label, list(argv), Path(evidence)
        self.child = None
        self.closed = False
        self.terminal = None
        self.custody_failure = None
        self.custody_cleanup = {"complete": False, "attempts": []}
        self.stdout = self.evidence / (label + ".stdout.original")
        self.stderr = self.evidence / (label + ".stderr.original")
        new_json(self.evidence / (label + ".intent.json"), {
            "schema": SCHEMA, "label": label, "argv": self.argv,
            "cwd": str(cwd) if cwd is not None else None,
            "automatic_retry": False, "owned_process_group": True,
        })
        deadline = time.monotonic() + max(0, (_OWNER_SPEC["limits"]["deadline_unix_ms"] - int(time.time() * 1000)) / 1000)
        self.out = OriginalPipeCapture(self.stdout, _OWNER_SPEC["limits"]["max_stream_bytes"], deadline)
        try:
            self.err = OriginalPipeCapture(self.stderr, _OWNER_SPEC["limits"]["max_stream_bytes"], deadline)
            self.child = subprocess.Popen(self.argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            self.out.bind(self.child.stdout)
            self.err.bind(self.child.stderr)
        except BaseException as error:
            started = self.child is not None
            cleanup_errors = []
            setattr(error, "graph_process_started", started)
            if started:
                self.custody_failure = "raw-bind-after-child-start"
                try:
                    self.child.terminate()
                    try:
                        self.child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        self.child.wait(timeout=1)
                except BaseException as cleanup:
                    cleanup_errors.append(type(cleanup).__name__)
            for stream in (self.out, getattr(self, "err", None)):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException as cleanup:
                        cleanup_errors.append(type(cleanup).__name__)
            self.closed = True
            self.terminal = {"schema": SCHEMA, "label": label, "status": "UNKNOWN" if started else "NOT_RUN",
                             "error_class": type(error).__name__, "original_error_repr": repr(error),
                             "automatic_retry": False, "process_started": started,
                             "cleanup_errors": cleanup_errors,
                             "exit_code": self.child.poll() if started else None,
                             "writers_closed_within_owned_group": not started}
            try:
                new_json(self.evidence / (label + ".terminal.json"), self.terminal)
            except BaseException as custody:
                cleanup_errors.append(type(custody).__name__)
            raise

    def finish(self, *, timeout, stop=False, reason="command-terminal"):
        if self.custody_failure is not None:
            # A failure forbids positive replay, but must not prevent bounded
            # cleanup of an owned writer that a previous cleanup did not close.
            self.cleanup_after_custody_failure()
            raise GraphOwnerFailure("child-original-custody-failed-no-positive-cache")
        if self.closed:
            return self.terminal
        try:
            return self._finish_original(timeout=timeout, stop=stop, reason=reason)
        except BaseException as error:
            enter_cancellation_boundary()
            # A complete-looking in-memory receipt must never outlive failure
            # to preserve its originals. Latch before bounded owned cleanup;
            # subsequent finish calls fail rather than returning a success.
            try:
                self.custody_failure = type(error).__name__
                self.closed = True
                self.terminal = None
                self.cleanup_after_custody_failure()
            finally:
                # Preserve the original exception after bounded cleanup. A
                # second signal remains observed, never aborts owned cleanup,
                # and can never turn this latched failure into a positive.
                leave_cancellation_boundary(propagate=False)
            raise

    def cleanup_after_custody_failure(self):
        enter_cancellation_boundary()
        try:
            if self.custody_cleanup["complete"] or len(self.custody_cleanup["attempts"]) >= 2:
                return self.custody_cleanup
            attempt = {"index": len(self.custody_cleanup["attempts"]) + 1,
                       "original_command_replayed": False, "errors": []}
            self.custody_cleanup["attempts"].append(attempt)
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                attempt["errors"].append("owned-group-stop:" + type(error).__name__)
            try:
                attempt["observed_exit_code"] = self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                attempt["observed_exit_code"] = None
                attempt["errors"].append("owned-child-wait-deadline")
            for stream in (self.out, self.err):
                if not stream.closed:
                    try:
                        stream.close()
                    except OSError as error:
                        attempt["errors"].append("original-stream-close:" + type(error).__name__)
            try:
                observed = group_observation(self.child.pid)
            except BaseException as error:
                observed = {"pgid": self.child.pid, "group_exists": None, "members": [],
                            "unknown": ["cleanup-observation:" + type(error).__name__],
                            "writers_closed_within_owned_group": False}
            attempt["group_observation"] = observed
            attempt["original_streams_closed"] = self.out.closed and self.err.closed
            self.custody_cleanup["complete"] = (attempt["observed_exit_code"] is not None
                                                   and observed["writers_closed_within_owned_group"]
                                                   and attempt["original_streams_closed"])
            try:
                new_json(self.evidence / (self.label + ".custody-cleanup-" + str(attempt["index"]) + ".json"),
                         {"schema": SCHEMA, "label": self.label, "status": "CUSTODY_FAILED",
                          "cleanup_observation": attempt, "cleanup_complete": self.custody_cleanup["complete"],
                          "positive_original_receipt_available": False, "automatic_retry": False})
            except BaseException as error:
                attempt["errors"].append("cleanup-receipt-custody:" + type(error).__name__)
            return self.custody_cleanup
        finally:
            leave_cancellation_boundary(propagate=False)

    def _finish_original(self, *, timeout, stop, reason):
        timed_out = forced = False
        termination_requested = False
        if stop and self.child.poll() is None:
            termination_requested = True
            self.child.terminate()
        try:
            code = self.child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = forced = True
            # The command may already have committed. Killing only the owned
            # group limits further effects; it never makes the outcome known.
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                code = self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
        deadline = time.monotonic() + 2
        observed = group_observation(self.child.pid)
        while not observed["writers_closed_within_owned_group"] and time.monotonic() < deadline:
            time.sleep(0.05)
            observed = group_observation(self.child.pid)
        descendant_cleanup = False
        if observed.get("group_exists") is True and not observed["writers_closed_within_owned_group"]:
            # A parent exit alone is not a writer exit. Stop only the group we
            # created, retain the original parent terminal, and taint custody.
            descendant_cleanup = True
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while not observed["writers_closed_within_owned_group"] and time.monotonic() < deadline:
                time.sleep(0.05)
                observed = group_observation(self.child.pid)
            if observed.get("group_exists") is True and not observed["writers_closed_within_owned_group"]:
                forced = True
                try:
                    os.killpg(self.child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                time.sleep(0.05)
                observed = group_observation(self.child.pid)
        self.out.flush(); os.fsync(self.out.fileno())
        self.err.flush(); os.fsync(self.err.fileno())
        self.out.close(); self.err.close()
        known_stop = stop and termination_requested and code in (0, -signal.SIGTERM)
        status = "UNKNOWN" if timed_out or descendant_cleanup or not observed["writers_closed_within_owned_group"] or code is None else (
            "STOPPED" if known_stop else "SUCCESS" if code == 0 else "FAILED")
        terminal = {
            "schema": SCHEMA, "label": self.label, "pid": self.child.pid,
            "reason": reason, "status": status, "exit_code": code,
            "timeout_seconds": timeout, "deadline_exceeded": timed_out,
            "termination_requested": termination_requested, "forced_termination": forced,
            "descendant_cleanup_required": descendant_cleanup,
            "automatic_retry": False, "native_outcome_inferred_from_timeout": False,
            "group_observation": observed,
            "writers_closed_within_owned_group": observed["writers_closed_within_owned_group"],
            "stdout": file_identity(self.stdout), "stderr": file_identity(self.stderr),
        }
        new_json(self.evidence / (self.label + ".terminal.json"), terminal)
        self.terminal = terminal
        self.closed = True
        return terminal

    def json_result(self, shared, *, expected_ok=True, timeout=20):
        terminal = self.finish(timeout=timeout)
        require(terminal["status"] not in ("UNKNOWN", "NOT_RUN"), "original-cli-outcome-unknown-no-retry")
        require(self.stdout.stat().st_size <= 256000 and self.stderr.stat().st_size <= 256000,
                "original-cli-receipt-budget")
        return shared.checked_private_cli_payload(self.stdout.read_bytes(), self.stderr.read_bytes(),
                                                  terminal["exit_code"], expected_ok)


class OwnerLifecycle:
    def __init__(self, evidence):
        self.evidence = private_directory(evidence)
        self.children = []
        self.labels = set()
        self.closed = False
        self.receipt = None
        self.custody_failure = None
        self.taints = []
        self.start_failures = []

    def start(self, label, argv, env, *, cwd=None):
        raise_deferred_cancellation()
        require(not self.closed and label not in self.labels, "owner-command-replay-or-closed")
        require(len(self.children) < _OWNER_SPEC["limits"]["max_processes"], "owner-process-admission-limit")
        self.labels.add(label)
        # Do not leave an executed child between Popen and registration when
        # the outer browser deadline requests cancellation.
        enter_cancellation_boundary()
        try:
            try:
                child = OwnedChild(label, argv, env, self.evidence, cwd=cwd)
            except BaseException as error:
                status = "UNKNOWN" if getattr(error, "graph_process_started", False) else "NOT_RUN"
                self.taints.append(label + ":" + status)
                failure = {"schema": SCHEMA, "label": label, "status": status,
                           "error_class": type(error).__name__, "original_error_repr": repr(error),
                           "automatic_retry": False,
                           "command_constructed": True, "process_started": status == "UNKNOWN"}
                self.start_failures.append(failure)
                try:
                    new_json(self.evidence / (label + ".start-failure.json"), failure)
                except BaseException as custody_error:
                    self.taints.append(label + ":start-failure-custody:" + type(custody_error).__name__)
                raise
            self.children.append(child)
            return child
        finally:
            leave_cancellation_boundary()

    def wait_socket(self, child, socket, *, deadline):
        socket = Path(socket)
        while time.monotonic() < deadline:
            if child.child.poll() is not None:
                child.finish(timeout=1)
                raise GraphOwnerFailure("owner-exited-before-socket")
            if socket.is_socket():
                return
            time.sleep(0.02)
        self.taints.append("owner-socket-readiness-deadline")
        raise GraphOwnerFailure("owner-socket-readiness-unknown-no-retry")

    def close(self, *, reason="explicit-close"):
        enter_cancellation_boundary()
        try:
            return self._close_owned(reason=reason)
        finally:
            leave_cancellation_boundary()

    def _close_owned(self, *, reason):
        require(self.custody_failure is None, "owner-close-custody-failed-no-positive-cache")
        if self.closed:
            return self.receipt
        self.closed = True
        terminals = []
        for child in reversed(self.children):
            try:
                terminal = child.finish(timeout=10, stop=True, reason=reason)
                terminals.append(terminal)
                if terminal["status"] not in ("SUCCESS", "STOPPED"):
                    self.taints.append(child.label + ":" + terminal["status"])
            except BaseException as error:
                self.taints.append(child.label + ":close:" + type(error).__name__)
                terminals.append({"schema": SCHEMA, "label": child.label, "status": "UNKNOWN",
                                  "positive_original_receipt_available": False,
                                  "custody_failure": child.custody_failure,
                                  "cleanup_observation": child.custody_cleanup,
                                  "automatic_retry": False})
        if reason != "explicit-close":
            self.taints.append(reason)
        receipt = {"schema": SCHEMA, "status": "CLOSED" if not self.taints else "INCOMPLETE",
                        "terminals": terminals, "taints": list(self.taints), "automatic_retry": False,
                        "start_failures": [dict(row) for row in self.start_failures],
                        "database_removed": False, "owner_originals_removed": False,
                        "studio_peer_exits_observed_by_this_helper": False,
                        "parent_must_require_all_studio_peer_exits_before_store_capture": True}
        try:
            new_json(self.evidence / "owner-close.json", receipt)
        except BaseException as error:
            self.custody_failure = type(error).__name__
            self.receipt = None
            raise
        self.receipt = receipt
        return receipt


def captured_text(lifecycle, label, argv, env, *, timeout=10):
    child = lifecycle.start(label, argv, env)
    terminal = child.finish(timeout=timeout)
    require(terminal["status"] == "SUCCESS", label + "-not-successful")
    require(child.stdout.stat().st_size <= 256000 and child.stderr.stat().st_size <= 256000,
            label + "-receipt-budget")
    return child.stdout.read_bytes().decode("utf-8", errors="strict")


def executable_identity(path):
    identity = file_identity(Path(path).resolve(strict=True))
    require(os.access(identity["path"], os.X_OK), "required-executable-not-executable")
    return identity


def canonical_directory(path):
    path = Path(path)
    require(path.is_absolute() and path.is_dir() and path.resolve(strict=True) == path
            and not path.is_symlink(), "canonical-directory-required")
    return path


def declared_executable_identity(role):
    reference = _OWNER_SPEC["binaries"][role]
    observed = executable_identity(reference["path"])
    require(observed["path"] == reference["path"]
            and observed["sha256"] == reference["sha256"]
            and observed["bytes"] == reference["size_bytes"],
            "declared-binary-original-mismatch:" + role)
    return observed


def load_shared(repo, expected_source, lifecycle):
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
            "real-owner-requires-linux-actions")
    require(SHA.fullmatch(expected_source) is not None and os.environ.get("GITHUB_SHA") == expected_source,
            "source-environment-mismatch")
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    observed = captured_text(lifecycle, "source-head", ["git", "-C", str(repo), "rev-parse", "HEAD"], env).strip()
    require(observed == expected_source, "source-head-mismatch")
    tree = captured_text(lifecycle, "source-tree", ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], env).strip()
    require(SHA.fullmatch(tree) is not None, "source-tree-shape")
    require(not captured_text(lifecycle, "source-status", ["git", "-C", str(repo), "status",
                "--porcelain=v1", "--untracked-files=no"], env),
            "tracked-source-dirty")
    shared = importlib.import_module("native_host_shared_state")
    require(Path(shared.__file__).resolve() == repo / "tooling/native_host_shared_state.py",
            "shared-host-module-origin")
    require(callable(getattr(shared, "owner_config", None))
            and callable(getattr(shared, "checked_private_cli_payload", None)),
            "reviewed-shared-host-successor-required")
    # Reuse the shared real owner configuration and typed envelope parser. Its full
    # Host9 acceptance entrypoint also requires an unrelated seed actor; this
    # helper never seeds/copies a Store or runs that acceptance sequence.
    require(shutil.which("bwrap") is not None, "bubblewrap-required-no-fallback")
    lock = json.loads((repo / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
    pin = lock["dependencies"]["semwright"]["revision"]
    require(SHA.fullmatch(pin) is not None and shared.PIN == pin, "shared-sdk-pin-mismatch")
    sdk_head = captured_text(lifecycle, "sdk-head", ["git", "-C", str(shared.SEMWRIGHT),
                             "rev-parse", "HEAD"], env).strip()
    require(sdk_head == pin, "sdk-head-mismatch")
    require(not captured_text(lifecycle, "sdk-status", ["git", "-C", str(shared.SEMWRIGHT),
                "status", "--porcelain=v1", "--untracked-files=no"], env), "tracked-sdk-source-dirty")
    # Declaration and original-byte equality are necessary inputs, not build
    # authority. The independent producer/runtime guard below remains DENY.
    binary_roles = (
        ("semwright", "semwright"), ("semwrightd", "semwrightd"),
        ("semwright_sandbox", "semwright-sandbox"),
        ("native_provider", "motionwright-native-provider"),
        ("manim_runner", "motionwright-manim-runner"),
        ("shared_state_actor", "shared-state-actor"),
        ("ordinary_service", "studio_review_session"),
        ("native_graph_session", "native-graph-review-session"),
    )
    binaries = {label: declared_executable_identity(role) for role, label in binary_roles}
    # Reuse the existing SDK launch path only when it is the declared sandbox.
    require(Path(_OWNER_SPEC["binaries"]["semwright_sandbox"]["path"])
            == shared.SW_BINS / "semwright-sandbox", "declared-shared-sandbox-path-mismatch")
    source_names = (
        "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json",
        "tooling/native_host_shared_state.py", "tooling/native_graph_browser_owner.py",
        "tooling/native_host_installed_shared_state.py", "tooling/native_provider_lifecycle.py",
        "tooling/native_manim_graph_runtime_intake.py",
        "apps/studio/tests/support/scopedChangeService.ts",
        "apps/studio/tests/support/nativeGraphCase.ts",
        "apps/studio/tests/support/graph-owner/producer_guard.mjs",
        "apps/studio/tests/support/graph-owner/factory.mjs",
        "apps/studio/tests/support/graph-owner/sdk_ticket_prewrite.mjs",
        "apps/studio/tests/support/graph-owner/custody.mjs",
        "apps/studio/tests/support/graph-owner/contracts.mjs",
        "apps/studio/tests/support/graph-owner/schemas/spec.r2.json",
        "apps/studio/tests/support/graph-owner/schemas/final.r2.json",
        "tooling/ci_canvas_service.py",
        ".github/workflows/studio-service-verification.yml",
        "crates/service/examples/studio_review_session.rs",
        "crates/native/examples/native-graph-review-session.rs",
        "crates/native/examples/shared-state-actor.rs",
        "crates/manim-profile/src/semantic_graph_source.rs",
        "crates/manim-profile/src/semantic_graph_helper_loader.py",
        "crates/manim-profile/src/semantic_graph_text_probe.py",
        *("tooling/" + name + ".py" for name in sorted(GRAPH_HELPERS)))
    captured_text(lifecycle, "required-source-tracked", ["git", "-C", str(repo), "ls-files",
                  "--error-unmatch", "--", *source_names], env)
    sources = {name: file_identity(repo / name) for name in source_names}
    receipt = {"schema": SCHEMA, "source": expected_source, "tree": tree, "sdk_source": pin,
               "sources": sources, "binaries": binaries,
               "binary_source_causality_validated_by_this_helper": _GRAPH_J1 is not None,
               "binary_source_causality_requires_exact_workflow_build_records": True,
               "full_host9_acceptance_invoked": False, "runtime_pass_inherited": False}
    new_json(lifecycle.evidence / "source-inputs.json", receipt)
    return shared, receipt


def runtime_inputs(repo):
    runtime = canonical_directory(Path(os.environ.get("SEMWRIGHT_TEST_MANIM_RUNTIME",
                                                       repo / ".manim-runtime")).resolve(strict=True))
    fontconfig = canonical_directory(Path("/etc/fonts").resolve(strict=True))
    python = os.environ.get("SEMWRIGHT_TEST_PYTHON") or shutil.which("python3")
    ffmpeg = os.environ.get("SEMWRIGHT_TEST_FFMPEG") or shutil.which("ffmpeg")
    require(python and ffmpeg, "graph-probe-tools-unavailable")
    return {"runtime": runtime, "fontconfig": fontconfig,
            "python": executable_identity(python), "ffmpeg": executable_identity(ffmpeg)}


def check_base_runtime_inventory(path, source_inputs, runtime, context):
    """Validate the existing /2 receipt; its package tree is measured by CI.

    This small per-case check deliberately does not rehash the entire shared
    site-packages tree. The exact CI producer and its final unchanged check are
    separate required evidence. Graph's fifteen originals are checked below.
    """
    original = file_identity(path)
    require(0 < original["bytes"] <= 2 * 1024 * 1024, "base-runtime-inventory-budget")
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == original["sha256"], "base-runtime-inventory-changed")
    value = strict_json(raw)
    require(type(context) is dict and set(context) == {"source_sha", "semwright_sha", "run_id", "run_attempt"}
            and context["source_sha"] == source_inputs["source"]
            and context["semwright_sha"] == source_inputs["sdk_source"]
            and all(type(context[name]) is str and re.fullmatch(r"[1-9][0-9]*", context[name])
                    for name in ("run_id", "run_attempt")), "base-runtime-current-context-shape")
    require(type(value) is dict and value.get("schema") == "motionwright.manim.runtime-inventory/2"
            and value.get("context") == context and value.get("manim_version") == "0.21.0"
            and value.get("pyav_version") == "15.0.0"
            and value.get("native_render_performed") is False, "base-runtime-current-context-mismatch")
    receipt = file_identity(runtime["runtime"] / "runtime.json")
    require(value.get("runtime_receipt_sha256") == receipt["sha256"], "base-runtime-receipt-mismatch")
    selected_probe = os.environ.get("SEMWRIGHT_TEST_FFPROBE") or shutil.which("ffprobe")
    require(selected_probe, "base-runtime-ffprobe-unavailable")
    observed = dict(python=runtime["python"], ffmpeg=runtime["ffmpeg"],
                    ffprobe=executable_identity(selected_probe))
    for name, identity in observed.items():
        require(value.get("binaries", {}).get(name) == {key: identity[key] for key in ("path", "bytes", "sha256")},
                "base-runtime-actual-tool-mismatch:" + name)
    require(type(value.get("files")) is int and value["files"] > 0
            and type(value.get("inventory_sha256")) is str
            and DIGEST.fullmatch(value["inventory_sha256"]) is not None,
            "base-runtime-tree-inventory-shape")
    require(type(value.get("pyav_native_files")) is list and 0 < len(value["pyav_native_files"]) <= 512,
            "base-runtime-native-file-roster")
    return {"original_inventory": original, "context": context, "runtime_receipt": receipt,
            "binaries": observed, "site_packages_inventory_sha256": value["inventory_sha256"],
            "site_packages_rehashed_by_this_helper": False,
            "shared_runtime_before_and_after_checks_required_from_ci": True,
            "base_inventory_alone_is_graph_readiness": False}


def check_graph_profile_entries(runtime_root, assets_lock_path, entry_manifest_path, repo):
    """Check emitted inert bytes only; the caller must bind the actual writer.

    This consumes the compiler's original assets.lock.json and manifest.json.
    It neither manufactures pins nor interprets this source package as native
    admission. The outer exact source/run producer receipt remains mandatory.
    """
    root = canonical_directory(runtime_root)
    originals = {}
    values = {}
    for name, path in (("assets_lock", assets_lock_path), ("entry_manifest", entry_manifest_path)):
        identity = file_identity(path)
        require(0 < identity["bytes"] <= 2 * 1024 * 1024, "graph-source-manifest-budget")
        raw = Path(path).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == identity["sha256"], "graph-source-manifest-changed")
        values[name] = strict_json(raw)
        originals[name] = identity
    assets, manifest = values["assets_lock"], values["entry_manifest"]
    require(type(assets) is dict and type(assets.get("schema_version")) is int
            and assets["schema_version"] == 1 and assets.get("profile_id") == "mw-manim-semantic-graph-v1"
            and assets.get("native_dependency_admission") == "REQUIRES_AUTHENTIC_CURRENT_OWNER_BINDING",
            "graph-source-lock-contract")
    require(type(manifest) is dict and type(manifest.get("schema_version")) is int
            and manifest["schema_version"] == 1 and manifest.get("profile_id") == "mw-manim-semantic-graph-v1"
            and manifest.get("bundle_kind") == "motionwright.manim.semantic_graph.editable_source"
            and manifest.get("assets_lock_sha256") == originals["assets_lock"]["sha256"]
            and manifest.get("native_render_verified") is False
            and manifest.get("native_reopen_verified") is False
            and manifest.get("profile_admitted") is False, "graph-source-entry-manifest-contract")
    raw_entries = manifest.get("entries")
    require(type(raw_entries) is list and len(raw_entries) == 19, "graph-source-entry-roster")
    entries = {}
    for row in raw_entries:
        require(type(row) is dict and type(row.get("path")) is str and row["path"] not in entries,
                "graph-source-entry-duplicate-or-shape")
        entries[row["path"]] = row
    require(entries.get("assets.lock.json") == {"path": "assets.lock.json",
                "bytes": originals["assets_lock"]["bytes"], "sha256": originals["assets_lock"]["sha256"]},
            "graph-lock-entry-byte-identity")
    helpers = assets.get("helper_modules")
    fonts = assets.get("fonts")
    require(type(helpers) is list and len(helpers) == 6 and type(fonts) is dict
            and fonts.get("schema") == "motionwright.manim.graph-font-lock/1"
            and fonts.get("native_font_support_attested") is False, "graph-profile-dependency-shape")
    packages = fonts.get("packages")
    require(type(packages) is list and len(packages) == 2, "graph-font-package-roster")
    declared = []
    seen_helpers = set()
    for row in helpers:
        require(type(row) is dict and row.get("module") in GRAPH_HELPERS
                and row["module"] not in seen_helpers, "graph-helper-roster")
        module = row["module"]
        relative = "graph-profile-v1/helpers/" + module + ".py"
        require(row.get("relative_path") == relative, "graph-helper-relative-path")
        source = file_identity(repo / "tooling" / (module + ".py"))
        require(source["bytes"] == row.get("size_bytes") and source["sha256"] == row.get("sha256"),
                "graph-helper-differs-from-current-source")
        seen_helpers.add(module)
        declared.append((relative, row))
    require(seen_helpers == GRAPH_HELPERS, "graph-helper-roster")
    expected_families = {"@fontsource-variable/instrument-sans": "Instrument Sans",
                         "@fontsource/ibm-plex-mono": "IBM Plex Mono"}
    seen_packages = set()
    for package in packages:
        require(type(package) is dict and package.get("package") in expected_families
                and package["package"] not in seen_packages and package.get("version") == "5.3.0"
                and package.get("fallback") == "forbidden"
                and package.get("expected_native_family") == expected_families[package["package"]],
                "graph-font-package-contract")
        seen_packages.add(package["package"])
        require(type(package.get("font_files")) is list and type(package.get("license")) is dict,
                "graph-font-assets-shape")
        for row in [package["license"], *package["font_files"]]:
            require(type(row) is dict and type(row.get("relative_path")) is str, "graph-font-path-shape")
            declared.append(("graph-profile-v1/fonts/" + row["relative_path"], row))
    require(len(declared) == 15 and seen_packages == set(expected_families), "graph-profile-fifteen-originals-required")
    seen = set()
    observed = []
    for relative, row in declared:
        path = Path(relative)
        require(not path.is_absolute() and ".." not in path.parts and path.as_posix() == relative
                and "\\" not in relative and relative not in seen, "graph-profile-path-containment")
        require(type(row.get("size_bytes")) is int and 0 < row["size_bytes"] <= 1024 * 1024
                and type(row.get("sha256")) is str and DIGEST.fullmatch(row["sha256"]) is not None,
                "graph-profile-file-pin")
        actual = file_identity(root / path)
        require(actual["bytes"] == row["size_bytes"] and actual["sha256"] == row["sha256"],
                "graph-profile-original-bytes-changed")
        require(entries.get(relative) == {"path": relative, "bytes": actual["bytes"], "sha256": actual["sha256"]},
                "graph-profile-entry-manifest-mismatch")
        seen.add(relative)
        observed.append(dict(actual, relative_path=relative))
    require(sum(name.endswith("/LICENSE") for name in seen) == 2
            and sum(name.endswith(".woff2") for name in seen) == 7, "graph-font-license-roster")
    require(set(entries) == seen | {"assets.lock.json", "plan.json", "scene.py", "render-config.json"},
            "graph-source-entry-closed-roster")
    return {"original_manifests": originals, "files": observed, "helper_count": 6, "font_and_license_count": 9,
            "source_package_is_native_admission": False, "actual_writer_source_binding_checked_here": False}


def load_graph_runtime_custody(_repo, _source_inputs, _runtime, _lifecycle):
    # Draft remains closed until the actual inert writer contract binds its
    # source-built executable/build originals and emitted asset manifests to
    # this source/tree/locks/SDK/run/attempt. Directory existence or a freshly
    # manufactured receipt after provision is never an alternative admission.
    raise GraphOwnerFailure("graph-runtime-producer-binding-awaiting-reviewed-contract")


def graph_manifest(repo, source_inputs, runtime, work):
    """The public Motionwright provider delegates only its closed font tools."""
    provider = source_inputs["binaries"]["motionwright-native-provider"]
    runner = source_inputs["binaries"]["motionwright-manim-runner"]
    version = tomllib.loads((repo / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]["package"]["version"]
    roots = ("manim-runtime", "manim-work", "fontconfig")
    return {
        "manifest_version": 1, "protocol": 9, "id": "motionwright", "version": version,
        "publisher": "motionwright-project", "executable": provider["path"], "sha256": provider["sha256"],
        "application": {"desktop_id": None, "process_names": ["motionwright-native-provider"],
                        "supported_versions": []},
        "transport": "stdio_v1", "network": False,
        "mounts": [{"root": "motionwright-data", "read_only": False, "execute": False}]
                  + [{"root": name, "read_only": name != "manim-work", "execute": False} for name in roots],
        "system_config": [], "secrets": [],
        "tools": [{"root": "manim-runner-root", "name": "manim-runner", "sha256": runner["sha256"],
                   "mounts": list(roots), "dependencies": ["python3", "ffmpeg"]},
                  {"root": "python-root", "name": "python3", "sha256": runtime["python"]["sha256"],
                   "mounts": [], "dependencies": []},
                  {"root": "ffmpeg-root", "name": "ffmpeg", "sha256": runtime["ffmpeg"]["sha256"],
                   "mounts": [], "dependencies": []}],
        "resources": {"open_files": 256, "processes": min(64, _OWNER_SPEC["limits"]["max_processes"]), "cpu_seconds": 90,
                      "operation_cpu_seconds": 0, "address_space_bytes": 4_294_967_296,
                      "file_size_bytes": 4_194_304},
        "request_timeout_ms": 60_000,
        "interfaces": {"dynamic_capabilities": False, "cooperative_cancellation": True,
                       "events": False, "progress": False, "artifacts": False,
                       "health": True, "native_refs": True, "host_tools": True},
    }


def graph_owner_config(shared, database, manifest, resource, project, runtime, source_inputs, work):
    require(CORE_PROJECT.fullmatch(project) is not None, "real-core-project-required")
    result = shared.owner_config(database.parent, manifest=manifest, selections=[(resource, project)])
    grants = [("manim-runtime", runtime["runtime"], False), ("manim-work", work, True),
              ("fontconfig", runtime["fontconfig"], False),
              ("manim-runner-root", source_inputs["binaries"]["motionwright-manim-runner"]["path"], False),
              ("python-root", runtime["python"]["path"], False),
              ("ffmpeg-root", runtime["ffmpeg"]["path"], False)]
    for name, path, write in grants:
        result += ("\n[[policy.filesystem]]\nname = " + json.dumps(name)
                   + "\npath = " + json.dumps(str(path)) + "\nread = true\nwrite = "
                   + ("true" if write else "false") + "\n")
    return result


GRAPH_REQUEST_ID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(0|[1-9][0-9]*)\Z")


def graph_integer(value, minimum=0, maximum=(1 << 53) - 1):
    return type(value) is int and minimum <= value <= maximum


class ClientReservationLedger:
    """Consumed original helper/launch IDs; no retry of missing CLIENT replies."""

    def __init__(self, *, max_clients):
        require(graph_integer(max_clients, 1, 64), "client-limit-shape")
        self.max_clients = max_clients
        self.sealed = False
        self.failed = False
        self._rows = []
        self._ids = set()
        self._helper_next = {}

    def reserve(self, request_id):
        require(not self.sealed and not self.failed, "client-admissions-closed")
        match = GRAPH_REQUEST_ID.fullmatch(request_id) if type(request_id) is str else None
        require(match is not None, "client-request-id-shape")
        helper_id, decimal_index = match.groups()
        # Check bounded decimal length before converting an adversarial integer.
        require(len(decimal_index) <= 16, "client-launch-index-bound")
        launch_index = int(decimal_index)
        require(graph_integer(launch_index, 0, 63), "client-launch-index-bound")
        if (request_id in self._ids or len(self._rows) >= self.max_clients
                or launch_index != self._helper_next.get(helper_id, 0)
                or any(row["status"] != "READY" for row in self._rows)):
            self.failed = True
            raise GraphOwnerFailure("client-reservation-reused-missing-or-exhausted")
        self._ids.add(request_id)
        self._helper_next[helper_id] = launch_index + 1
        row = {"id": request_id, "helper_id": helper_id, "launch_index": launch_index,
               "client_id": "client-" + str(len(self._rows) + 1).zfill(4), "status": "UNKNOWN"}
        self._rows.append(row)
        # No owner effect may precede this append. Callers get a copy, not a
        # mutable reference that could change the admitted internal tuple.
        return dict(row)

    def ready(self, request_id):
        require(not self.sealed and not self.failed, "client-admissions-closed")
        matches = [row for row in self._rows if row["id"] == request_id]
        require(len(matches) == 1 and matches[0]["status"] == "UNKNOWN",
                         "client-ready-without-original-reservation")
        matches[0]["status"] = "READY"

    def unknown(self, request_id):
        self.failed = True
        for row in self._rows:
            if row["id"] == request_id:
                row["status"] = "UNKNOWN"

    def seal(self):
        self.sealed = True

    def snapshot(self):
        return tuple(dict(row) for row in self._rows)

    def require_actual_launch_bijection(self, actual_launches):
        self.seal()
        try:
            require(not self.failed and all(row["status"] == "READY" for row in self._rows),
                             "client-original-outcome-unknown")
            require(type(actual_launches) in (set, frozenset)
                             and actual_launches == {(row["helper_id"], row["launch_index"])
                                                     for row in self._rows},
                             "client-actual-launch-bijection")
        except GraphOwnerFailure:
            self.failed = True
            raise


class GraphBrowserOwner:
    """One Host/Store owner; every wrapper receives its own genuine session."""
    def __init__(self, repo, expected_source, database, resource, lifecycle):
        self.lifecycle = lifecycle
        self.repo = repo
        self.database = Path(database)
        require(self.database.name == "motionwright.sqlite3", "provider-database-basename-must-be-motionwright.sqlite3")
        self.database_initial = file_identity(self.database)
        require(_INITIALIZER_PHYSICAL == {"path": self.database_initial["path"], "device": str(self.database_initial["device"]), "inode": str(self.database_initial["inode"])}, "owner-store-not-initializer-physical-store")
        new_json(lifecycle.evidence / "database-initial.original.json", {**self.database_initial,
            "device": str(self.database_initial["device"]), "inode": str(self.database_initial["inode"])})
        require(not lifecycle.evidence.is_relative_to(self.database.parent)
                and not self.database.parent.is_relative_to(lifecycle.evidence),
                "owner-custody-and-data-mount-roots-must-be-disjoint")
        require(RESOURCE.fullmatch(resource) is not None, "actual-project-resource-required")
        self.resource = resource
        self.shared, self.source_inputs = load_shared(repo, expected_source, lifecycle)
        validate_bound_source(self.source_inputs)
        require(Path(self.shared.SEMWRIGHT).resolve() == Path(_OWNER_SPEC["source"]["sdk_root"]), "spec-sdk-root-mismatch")
        self.runtime = runtime_inputs(repo)
        self.runtime_custody = load_graph_runtime_custody(repo, self.source_inputs, self.runtime, lifecycle)
        self.verified_native_service_binary = _GRAPH_J1["index"]["binaries"]["native_graph_session"]["path"]
        self.initial_store_show = observe_existing_store(self)
        self.clients = []
        self.client_ledger = ClientReservationLedger(max_clients=_OWNER_SPEC["limits"]["max_clients"])
        self.closed = False
        self.paths = {name: private_directory(lifecycle.evidence / name)
                      for name in ("home", "state", "config", "clients", "work", "native-output")}
        # UNIX socket names have a strict small byte limit. The short socket
        # directory is retained, never automatically deleted; all regular
        # sessions, owner state, configuration and originals remain in evidence.
        self.socket_directory = private_directory(Path(_OWNER_SPEC["paths"]["owner_runtime"]))
        require(stat.S_IMODE(self.socket_directory.stat().st_mode) == 0o700, "owner-socket-directory-mode")
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}}
        self.env.update(HOME=str(self.paths["home"]), XDG_RUNTIME_DIR=str(self.socket_directory),
                        XDG_STATE_HOME=str(self.paths["state"]), RUST_BACKTRACE="1")
        self.cli = self.source_inputs["binaries"]["semwright"]["path"]
        self.daemon_executable = self.source_inputs["binaries"]["semwrightd"]["path"]
        new_json(lifecycle.evidence / "owner-locations.json", {
            "schema": SCHEMA, "socket_directory": str(self.socket_directory),
            "private_paths": {name: str(path) for name, path in self.paths.items()},
            "database": self.database_initial, "resource": resource, "automatic_cleanup": False})
        # Config paths is a plain configuration JSON document, not a native
        # operation envelope. Preserve the exact streams before parsing it.
        raw_paths = captured_text(lifecycle, "owner-config-paths", [self.cli, "--json", "config", "paths"], self.env)
        effective_paths = json.loads(raw_paths)
        self.owner_state = Path(effective_paths["state"]).resolve()
        require(self.owner_state.is_relative_to(self.paths["state"]), "owner-state-escaped-private-profile")
        new_json(lifecycle.evidence / "owner-paths.parsed.json", effective_paths)
        manifest_path = self.paths["config"] / "motionwright-driver.json"
        new_json(manifest_path, graph_manifest(repo, self.source_inputs, self.runtime, self.paths["work"]))
        bootstrap_config = self.paths["config"] / "owner-bootstrap.toml"
        new_bytes(bootstrap_config, self.shared.owner_config(self.database.parent).encode("utf-8"))
        bootstrap_socket = self.socket_directory / "bootstrap.sock"
        revalidate_original_producer_admission(lifecycle, before_effect=True)
        self.bootstrap = lifecycle.start("owner-bootstrap", [self.daemon_executable, "--config",
                                          str(bootstrap_config), "--socket", str(bootstrap_socket)], self.env)
        lifecycle.wait_socket(self.bootstrap, bootstrap_socket, deadline=time.monotonic() + 20)
        bootstrap_session = self.paths["clients"] / "owner-bootstrap.session"
        created = self.invoke("owner-project-create", bootstrap_socket, bootstrap_session,
                              "project.create", {"root": "motionwright-data"})
        project = created.get("data", {}).get("project")
        require(isinstance(project, str) and CORE_PROJECT.fullmatch(project) is not None,
                "owner-project-create-did-not-issue-real-core-identity")
        terminal = self.bootstrap.finish(timeout=10, stop=True, reason="bootstrap-complete")
        require(terminal["status"] in ("SUCCESS", "STOPPED")
                and terminal["writers_closed_within_owned_group"], "bootstrap-owner-closure-unproved")
        # Reuse precisely the same private Core owner state, then narrow its
        # resource selection. No prj_ identity, grant or Store is fabricated.
        config = self.paths["config"] / "owner-restricted.toml"
        new_bytes(config, graph_owner_config(self.shared, self.database, manifest_path, resource, project,
                    self.runtime, self.source_inputs, self.paths["work"]).encode("utf-8"))
        self.socket = self.socket_directory / "broker.sock"
        self.daemon = lifecycle.start("owner-restricted", [self.daemon_executable, "--config", str(config),
                                      "--socket", str(self.socket)], self.env)
        lifecycle.wait_socket(self.daemon, self.socket, deadline=time.monotonic() + 20)
        self.descriptor_path = lifecycle.evidence / "owner-descriptor.json"
        new_json(self.descriptor_path, {"schema": SCHEMA, "status": "OWNER_READY_NOT_ACCEPTANCE",
            "source": expected_source, "tree": self.source_inputs["tree"],
            "database": self.database_initial, "resource": resource, "core_project": project,
            "socket": str(self.socket), "owner_state": str(self.owner_state),
            "owner_inputs": file_identity(lifecycle.evidence / "source-inputs.json"),
            "driver_manifest": file_identity(manifest_path), "owner_config": file_identity(config),
            "runtime": {"root": str(self.runtime["runtime"]), "fontconfig": str(self.runtime["fontconfig"]),
                        "python": self.runtime["python"], "ffmpeg": self.runtime["ffmpeg"]},
            "session_reuse_across_wrapper_instances_allowed": False,
            "binding_ref_transfers_allowed": False, "new_client_operation": "new-client",
            "database_copied_or_seeded": False, "automatic_retry": False,
            "native_recipe_executed_by_bootstrap": False, "current_pass_claim": False,
            "formal_acceptance": False, "release_authorized": False})

    def invoke(self, label, socket, session, command, args):
        child = self.lifecycle.start(label, [self.cli, "--socket", str(socket), "--session-file", str(session),
                     "--json", "execute", command, "--args-json", json.dumps(args, separators=(",", ":"))], self.env)
        return child.json_result(self.shared, timeout=_OWNER_SPEC["limits"]["cli_timeout_ms"] / 1000)

    def new_client(self, request_id):
        require(not self.closed and len(self.clients) < MAX_CLIENTS, "owner-closed-or-client-budget")
        require(all(row["status"] == "CLIENT_READY_NOT_ACCEPTANCE" for row in self.clients),
                "previous-client-outcome-unknown-no-retry")
        require(self.daemon.child.poll() is None, "owner-exited-before-new-client")
        reserved = self.client_ledger.reserve(request_id)
        identifier = reserved["client_id"]
        # Consume the attempt before any effect; a failed handshake is never
        # silently replayed with either the same or another identity.
        attempt = dict(reserved, automatic_retry=False)
        self.clients.append(attempt)
        request_original = self.paths["clients"] / (identifier + ".request.original.json")
        new_json(request_original, attempt)
        request_identity = file_identity(request_original)
        session = self.paths["clients"] / (identifier + ".session")
        require(not session.exists(), "new-client-session-already-exists")
        mounted = self.invoke(identifier + "-mount", self.socket, session, "providers.mount.observe",
                              {"provider": "driver:motionwright", "root": "motionwright-data"})
        data = mounted.get("data", {})
        require(type(data) is dict and data.get("provider") == "driver:motionwright"
                and type(data.get("provider_generation")) is int and data["provider_generation"] >= 0
                and type(data.get("connection")) is str and bool(data["connection"])
                and type(data.get("binding_ref")) is str and bool(data["binding_ref"])
                and data.get("mount", {}).get("root") == "motionwright-data"
                and data["mount"].get("path") == str(self.database.parent)
                and data["mount"].get("read") is True and data["mount"].get("write") is True
                and data["mount"].get("execute") is False, "actual-client-writable-binding-required")
        session_identity = file_identity(session)
        output = private_directory(self.paths["native-output"] / identifier)
        connection = {"schema": "motionwright-semwright-connection/1", "executable": self.cli,
                      "executable_sha256": self.source_inputs["binaries"]["semwright"]["sha256"],
                      "socket": str(self.socket), "session_file": str(session),
                      "output_root": str(output), "resource": self.resource}
        path = self.paths["clients"] / (identifier + ".connection.json")
        new_json(path, connection)
        require(file_identity(request_original) == request_identity, "client-request-original-changed")
        ready = dict(attempt, status="CLIENT_READY_NOT_ACCEPTANCE", client_request_original=request_identity, session=session_identity,
                     connection=file_identity(path), actual_mount_observation=data)
        new_json(self.paths["clients"] / (identifier + ".custody.json"), ready)
        attempt.update(ready)
        self.client_ledger.ready(request_id)
        return {"schema": SCHEMA, "event": "client-ready", "status": "CLIENT_READY", "id": request_id,
                "client_id": identifier, "connection_file": str(path),
                "owner_descriptor": str(self.descriptor_path), "automatic_retry": False}

    def close(self, *, reason="explicit-close"):
        require(not self.closed, "graph-owner-close-replay")
        self.client_ledger.seal()
        self.closed = True
        if self.daemon.child.poll() is not None:
            self.lifecycle.taints.append("restricted-owner-exited-before-explicit-close")
        receipt = self.lifecycle.close(reason=reason)
        current = file_identity(self.database)
        same_file = (current["device"], current["inode"]) == (
            self.database_initial["device"], self.database_initial["inode"])
        client_failures = sorted({row["client_id"] for row in self.clients
                                  if row["status"] != "CLIENT_READY_NOT_ACCEPTANCE"}
                                 | {row["client_id"] for row in self.client_ledger.snapshot()
                                    if row["status"] != "READY"})
        if self.client_ledger.failed and not client_failures:
            client_failures = ["client-reservation-protocol-tainted"]
        result = {"schema": SCHEMA, "event": "owner-closed",
                  "status": "CLOSED" if receipt["status"] == "CLOSED" and same_file and not client_failures else "INCOMPLETE",
                  "owner_close": file_identity(self.lifecycle.evidence / "owner-close.json"),
                  "database_final_observation": current, "same_physical_database_file": same_file,
                  "client_roster": self.clients, "unknown_client_attempts": client_failures,
                  "studio_peer_exits_observed_by_this_helper": False,
                  "parent_must_require_all_studio_peer_exits_before_store_capture": True,
                  "current_pass_claim": False, "formal_acceptance": False, "release_authorized": False,
                  "database_removed": False, "owner_originals_removed": False}
        final_path = self.lifecycle.evidence / "graph-owner-final.json"
        new_json(final_path, result)
        # Private mount/binding/session observations stay only in the original
        # custody files. The parent's stdout protocol gets a bounded reference,
        # never the secret-bearing private roster or a native wrapper envelope.
        return {"schema": SCHEMA, "event": "owner-closed", "status": result["status"],
                "final_receipt": file_identity(final_path), "client_count": len(self.clients),
                "unknown_client_attempts": client_failures, "same_physical_database_file": same_file,
                "studio_peer_exits_observed_by_this_helper": False,
                "parent_must_require_all_studio_peer_exits_before_store_capture": True,
                "current_pass_claim": False, "formal_acceptance": False, "release_authorized": False}


def control_messages(stream, *, deadline):
    buffer = bytearray()
    while time.monotonic() < deadline:
        raise_deferred_cancellation()
        ready, _, _ = select.select([stream], [], [], min(1.0, max(0.0, deadline - time.monotonic())))
        if not ready:
            continue
        raw = os.read(stream.fileno(), MAX_CONTROL_BYTES + 1)
        if not raw:
            raise GraphOwnerFailure("control-eof-before-explicit-close")
        buffer.extend(raw)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            require(len(line) <= MAX_CONTROL_BYTES, "control-message-budget")
            buffer = bytearray(rest)
            value = strict_json(line.decode("utf-8", errors="strict"))
            require(type(value) is dict and (
                (set(value) == {"op", "id"} and value.get("op") == "new-client"
                 and type(value.get("id")) is str)
                or (set(value) == {"op"} and value.get("op") == "close")),
                "closed-control-operation-required")
            yield value
        require(len(buffer) <= MAX_CONTROL_BYTES, "control-message-budget")
    raise GraphOwnerFailure("owner-lifetime-deadline-unknown-no-retry")


def emit(value):
    if value.get("status") in ("OWNER_READY", "CLIENT_READY", "CLOSED"):
        raise_deferred_cancellation()
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def interrupted_owner(signum, _frame):
    _OBSERVED_SIGNALS.append(signum)
    # An already active exception is also a cleanup boundary. This covers the
    # first bytecodes of an except suite before its explicit counter is raised.
    if _CANCELLATION_CRITICAL or sys.exc_info()[0] is not None:
        _DEFERRED_SIGNALS.append(signum)
        return
    raise GraphOwnerFailure("owner-interrupted-unknown-no-retry:" + str(signum))


def finish_owner_error(lifecycle, graph, error):
    enter_cancellation_boundary()
    try:
        error_class = type(error).__name__
        original_traceback = traceback.format_exc().encode("utf-8")
        reason = str(error) if isinstance(error, GraphOwnerFailure) else None
        if lifecycle is not None:
            lifecycle.taints.append("owner-error:" + error_class)
            try:
                if graph is not None and not graph.closed:
                    graph.close(reason="owner-error")
                elif not lifecycle.closed:
                    lifecycle.close(reason="owner-error")
            except BaseException as cleanup_error:
                lifecycle.taints.append("cleanup-error:" + type(cleanup_error).__name__)
            try:
                new_bytes(lifecycle.evidence / "owner-error.traceback.original", original_traceback)
                new_json(lifecycle.evidence / "owner-error.json", {
                    "schema": SCHEMA, "status": "UNKNOWN", "error_class": error_class,
                    "reason_code": reason, "observed_signals": list(_OBSERVED_SIGNALS),
                    "taints": list(lifecycle.taints), "automatic_retry": False,
                    "current_pass_claim": False, "formal_acceptance": False})
            except BaseException:
                pass
        emit({"schema": SCHEMA, "event": "owner-error", "status": "UNKNOWN",
              "error_class": error_class, "reason_code": reason,
              "automatic_retry": False, "current_pass_claim": False})
        return 1
    finally:
        leave_cancellation_boundary(propagate=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--project-resource", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted_owner)
    signal.signal(signal.SIGINT, interrupted_owner)
    lifecycle = None
    graph = None
    try:
        lifecycle = OwnerLifecycle(Path(args.evidence_dir))
        repo = Path(__file__).resolve().parents[1]
        graph = GraphBrowserOwner(repo, args.expected_source, Path(args.database), args.project_resource, lifecycle)
        emit({"schema": SCHEMA, "event": "owner-ready", "status": "OWNER_READY",
              "owner_descriptor": str(graph.descriptor_path), "new_client_operation": "new-client",
              "session_reuse_across_wrapper_instances_allowed": False, "automatic_retry": False})
        for operation in control_messages(sys.stdin.buffer, deadline=time.monotonic() + 900):
            if operation["op"] == "new-client":
                response = graph.new_client(operation["id"])
                try:
                    emit(response)
                except BaseException:
                    graph.client_ledger.unknown(operation["id"])
                    raise
            else:
                result = graph.close()
                emit(result)
                return 0 if result["status"] == "CLOSED" else 1
    except BaseException as error:
        return finish_owner_error(lifecycle, graph, error)




# Literal R2 structures extracted without executing their publisher.
_SPEC_SCHEMA = {'type': 'object', 'properties': {'schema': {'const': 'motionwright.graph-browser-owner-spec/6'}, 'case_id': {'type': 'string', 'minLength': 1, 'pattern': '^[a-z][a-z0-9-]{0,63}$'}, 'source': {'type': 'object', 'properties': {'repository_root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{40}$'}, 'tree': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{40}$'}, 'sdk_root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sdk_sha': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{40}$'}, 'locks': {'type': 'object', 'properties': {'Cargo.lock': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'SOURCE_LOCK.json': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'apps/studio/package-lock.json': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'rust-toolchain.toml': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}}, 'required': ['Cargo.lock', 'SOURCE_LOCK.json', 'apps/studio/package-lock.json', 'rust-toolchain.toml'], 'additionalProperties': False}, 'run_id': {'type': 'string', 'minLength': 1, 'pattern': '^[1-9][0-9]*$'}, 'run_attempt': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['repository_root', 'sha', 'tree', 'sdk_root', 'sdk_sha', 'locks', 'run_id', 'run_attempt'], 'additionalProperties': False}, 'paths': {'type': 'object', 'properties': {'private_root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'owner_home': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'owner_runtime': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'owner_state': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'owner_socket': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'manim_work': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}}, 'required': ['private_root', 'owner_home', 'owner_runtime', 'owner_state', 'owner_socket', 'manim_work'], 'additionalProperties': False}, 'binaries': {'type': 'object', 'properties': {'semwright': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'semwrightd': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'native_provider': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'shared_state_actor': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'manim_runner': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'python3': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'ffmpeg': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'ordinary_service': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'native_graph_session': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'semwright_sandbox': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}}, 'required': ['semwright', 'semwrightd', 'native_provider', 'shared_state_actor', 'manim_runner', 'python3', 'ffmpeg', 'ordinary_service', 'native_graph_session', 'semwright_sandbox'], 'additionalProperties': False}, 'runtime': {'type': 'object', 'properties': {'root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'manifest': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'manifest_sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'intake_receipt': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'intake_receipt_sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'fontconfig_root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}}, 'required': ['root', 'manifest', 'manifest_sha256', 'intake_receipt', 'intake_receipt_sha256', 'fontconfig_root'], 'additionalProperties': False}, 'store': {'type': 'object', 'properties': {'database_path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute existing file named exactly motionwright.sqlite3; actual physical identity retained; no init/rename/copy/rebind.'}, 'project_id': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'}}, 'required': ['database_path', 'project_id'], 'additionalProperties': False}, 'limits': {'type': 'object', 'properties': {'deadline_unix_ms': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991, 'x-runtime-validation': 'Absolute future deadline, no more than900000ms from owner start; cannot be extended.'}, 'max_clients': {'type': 'integer', 'minimum': 1, 'maximum': 64}, 'max_processes': {'type': 'integer', 'minimum': 1, 'maximum': 128}, 'max_stream_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 1048576}, 'cli_timeout_ms': {'type': 'integer', 'minimum': 1, 'maximum': 20000}, 'close_timeout_ms': {'type': 'integer', 'minimum': 1, 'maximum': 30000}}, 'required': ['deadline_unix_ms', 'max_clients', 'max_processes', 'max_stream_bytes', 'cli_timeout_ms', 'close_timeout_ms'], 'additionalProperties': False}}, 'required': ['schema', 'case_id', 'source', 'paths', 'binaries', 'runtime', 'store', 'limits'], 'additionalProperties': False, '$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Motionwright Graph browser owner input, draft6 R1', 'description': 'Closed data contract for the new original-backed owner implementation. This schema is not evidence that draft5 supports it or that any runtime/gate has executed. Runtime path/source/binary/physical-store validation is mandatory in addition to structural validation.'}
_FINAL_SCHEMA = {'type': 'object', 'properties': {'schema': {'const': 'motionwright.graph-browser-owner-final/6'}, 'case_id': {'type': 'string', 'minLength': 1, 'pattern': '^[a-z][a-z0-9-]{0,63}$'}, 'status': {'enum': ['CLOSED', 'UNKNOWN']}, 'source': {'type': 'object', 'properties': {'repository_root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{40}$'}, 'tree': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{40}$'}, 'sdk_root': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sdk_sha': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{40}$'}, 'locks': {'type': 'object', 'properties': {'Cargo.lock': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'SOURCE_LOCK.json': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'apps/studio/package-lock.json': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'rust-toolchain.toml': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}}, 'required': ['Cargo.lock', 'SOURCE_LOCK.json', 'apps/studio/package-lock.json', 'rust-toolchain.toml'], 'additionalProperties': False}, 'run_id': {'type': 'string', 'minLength': 1, 'pattern': '^[1-9][0-9]*$'}, 'run_attempt': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991}}, 'required': ['repository_root', 'sha', 'tree', 'sdk_root', 'sdk_sha', 'locks', 'run_id', 'run_attempt'], 'additionalProperties': False}, 'spec_original': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, 'source_inputs': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'client_roster': {'type': 'array', 'items': {'type': 'object', 'properties': {'client_id': {'type': 'string', 'minLength': 1}, 'id': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/(?:0|[1-9][0-9]*)$'}, 'helper_id': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'}, 'launch_index': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}, 'status': {'enum': ['READY', 'UNKNOWN']}, 'connection_original': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'session_original': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'output_root': {'anyOf': [{'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, {'type': 'null'}]}}, 'required': ['client_id', 'id', 'helper_id', 'launch_index', 'status', 'connection_original', 'session_original', 'output_root'], 'additionalProperties': False}, 'maxItems': 64}, 'service_closure': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'service_closure_validated': {'type': 'boolean'}, 'owner_close': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'database_initial': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'database_final': {'anyOf': [{'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'x-runtime-validation': 'Absolute local path on the runtime OS; reject NUL, do not infer existence or authority from the spelling.'}, 'sha256': {'type': 'string', 'minLength': 1, 'pattern': '^[0-9a-f]{64}$'}, 'size_bytes': {'type': 'integer', 'minimum': 0, 'maximum': 9007199254740991}}, 'required': ['path', 'sha256', 'size_bytes'], 'additionalProperties': False}, {'type': 'null'}]}, 'same_physical_database': {'type': 'boolean'}, 'owner_cohort_absent': {'type': 'boolean'}, 'owner_streams_eof': {'type': 'boolean'}, 'capture_permitted': {'type': 'boolean'}, 'errors': {'type': 'array', 'items': {'enum': ['SPEC_INVALID', 'SOURCE_MISMATCH', 'RUNTIME_INVALID', 'STORE_INVALID', 'BINARY_MISMATCH', 'OWNER_START_FAILED', 'CLIENT_LIMIT', 'REQUEST_INVALID', 'CLIENT_START_FAILED', 'SERVICE_BARRIER_INVALID', 'OWNER_CLOSE_FAILED', 'CUSTODY_FAILED', 'DEADLINE_EXCEEDED', 'CANCELLED', 'INTERNAL_ERROR']}}, 'native_authoring_approved': {'const': False}, 'formal_acceptance': {'const': False}}, 'required': ['schema', 'case_id', 'status', 'source', 'spec_original', 'source_inputs', 'client_roster', 'service_closure', 'service_closure_validated', 'owner_close', 'database_initial', 'database_final', 'same_physical_database', 'owner_cohort_absent', 'owner_streams_eof', 'capture_permitted', 'errors', 'native_authoring_approved', 'formal_acceptance'], 'additionalProperties': False, '$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Graph browser owner private final receipt draft6 R1', 'description': 'Owner lifecycle/custody receipt; does not assert global shared-pool capture, supported copied-store restore, Native authoring approval, browser PASS, G2 or release acceptance. FileRefs for database observations reference original JSON observations, never database copies.'}

SAFE_INTEGER = (1 << 53) - 1

UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

UTC_MILLISECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")

class GraphContractFailure(ValueError):
    """A closed contract reason; the caller still owes actual bounded cleanup."""

def contract_require(ok, code):
    if not ok:
        raise GraphContractFailure(code)

def exact_object(value, fields, code):
    contract_require(type(value) is dict and set(value) == set(fields), code)

def integer(value, minimum=0, maximum=SAFE_INTEGER):
    return type(value) is int and minimum <= value <= maximum

def absolute_linux_path(value):
    if type(value) is not str or not value or "\0" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return path.is_absolute() and str(path) == value and ".." not in path.parts

def original_timestamp(value):
    if type(value) is not str or UTC_MILLISECONDS.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True

@dataclass(frozen=True)
class ExpectedServiceLaunches:
    """Independent expectation, never reconstructed from the submitted receipt.

    The initializer comes from its separately retained pre-owner launch/closure
    original; Native launch indices come from the owner's consumed CLIENT IDs.
    Exact executable provenance and actual physical Store identity are separate
    mandatory checks performed before this value may be constructed.
    """

    helper_id: str
    role: str
    binary_path: str
    database_path: str
    launch_indices: tuple[int, ...]

def validate_service_closure(value, *, database_path, expected_services,
                             expected_owner_count=1):
    """Validate the literal R2 Service CLOSED conjunction against external facts.

    Return the admitted tuple set, not a capability or acceptance decision.
    Reading the original bytes and checking the independently expected SHA must
    precede this call. A rejection must not skip Host/cohort cleanup.
    """
    contract_require(absolute_linux_path(database_path)
                     and PurePosixPath(database_path).name == "motionwright.sqlite3",
                     "expected-database-path")
    contract_require(integer(expected_owner_count, 1, 64), "expected-owner-count")
    contract_require(type(expected_services) in (tuple, list)
                     and 1 <= len(expected_services) <= 65, "expected-service-roster")
    expected = {}
    initializer_count = 0
    for row in expected_services:
        contract_require(type(row) is ExpectedServiceLaunches
                         and type(row.helper_id) is str
                         and UUID_TEXT.fullmatch(row.helper_id) is not None
                         and row.helper_id not in expected,
                         "expected-helper-identity")
        contract_require(row.role in ("initializer", "native")
                         and absolute_linux_path(row.binary_path)
                         and row.database_path == database_path,
                         "expected-helper-role-or-path")
        contract_require(type(row.launch_indices) is tuple
                         and 1 <= len(row.launch_indices) <= 64
                         and all(integer(index, 0, 63) for index in row.launch_indices)
                         and row.launch_indices == tuple(range(len(row.launch_indices))),
                         "expected-launch-indices")
        initializer_count += row.role == "initializer"
        if row.role == "initializer":
            contract_require(row.launch_indices == (0,), "initializer-single-original-launch")
        expected[row.helper_id] = row
    contract_require(initializer_count == 1, "exactly-one-original-initializer")
    contract_require(sum(len(row.launch_indices) for row in expected.values()) <= 65,
                     "expected-total-launch-bound")

    exact_object(value, ("schema", "recorded_at", "directory", "database_path", "status",
                         "phases", "sealed_helper_ids", "participants"), "service-top-fields")
    contract_require(value["schema"] == "motionwright.service-closure/1"
                     and value["status"] == "CLOSED"
                     and original_timestamp(value["recorded_at"]), "service-status-or-time")
    contract_require(value["database_path"] == database_path
                     and value["directory"] == str(PurePosixPath(database_path).parent),
                     "service-store-paths")
    phases = value["phases"]
    exact_object(phases, ("preparation", "oracle", "owner_admission_seal"), "service-phase-fields")
    contract_require(type(phases["preparation"]) is list
                     and all(status == "fulfilled" for status in phases["preparation"])
                     and phases["oracle"] == "fulfilled"
                     and type(phases["owner_admission_seal"]) is list
                     and phases["owner_admission_seal"] == ["fulfilled"] * expected_owner_count,
                     "service-phase-incomplete")
    helpers = value["sealed_helper_ids"]
    contract_require(type(helpers) is list and helpers == sorted(expected), "service-sealed-roster")
    participants = value["participants"]
    contract_require(type(participants) is list and len(participants) == len(expected),
                     "service-participant-count")
    seen = set()
    admitted = set()
    fields = ("helper_id", "database_path", "binary_path", "launch_count", "process_exits",
              "process_streams", "active_child_count", "starting_count", "closing_count",
              "pending_count", "taints", "close_status")
    for participant in participants:
        exact_object(participant, fields, "service-participant-fields")
        helper = participant["helper_id"]
        contract_require(type(helper) is str and helper in expected and helper not in seen,
                         "service-participant-identity")
        seen.add(helper)
        want = expected[helper]
        contract_require(participant["database_path"] == database_path
                         and participant["binary_path"] == want.binary_path,
                         "service-participant-paths")
        count = participant["launch_count"]
        contract_require(integer(count, 1, 64) and count == len(want.launch_indices),
                         "service-launch-count")
        contract_require(all(integer(participant[key], 0, 0) for key in
                             ("active_child_count", "starting_count", "closing_count", "pending_count"))
                         and type(participant["taints"]) is list and not participant["taints"]
                         and participant["close_status"] == "fulfilled", "service-helper-not-clean")
        exits, streams = participant["process_exits"], participant["process_streams"]
        contract_require(type(exits) is list and type(streams) is list
                         and len(exits) == len(streams) == count, "service-stream-exit-count")
        for exit_row in exits:
            exact_object(exit_row, ("process_id", "code", "signal", "observed_at"), "service-exit-fields")
            contract_require(integer(exit_row["process_id"], 1)
                             and integer(exit_row["code"], 0, 0)
                             and exit_row["signal"] is None
                             and original_timestamp(exit_row["observed_at"]), "service-exit-not-clean")
        exit_indices = set()
        for index, stream in enumerate(streams):
            exact_object(stream, ("launch_index", "process_id", "exit_index", "stdout_ended",
                                  "stderr_ended", "jsonl_reader_closed"), "service-stream-fields")
            exit_index = stream["exit_index"]
            contract_require(integer(stream["launch_index"], index, index)
                             and integer(exit_index, 0, count - 1)
                             and exit_index not in exit_indices,
                             "service-launch-exit-bijection")
            exit_indices.add(exit_index)
            contract_require(integer(stream["process_id"], 1)
                             and stream["process_id"] == exits[exit_index]["process_id"]
                             and all(stream[key] is True for key in
                                     ("stdout_ended", "stderr_ended", "jsonl_reader_closed")),
                             "service-stream-not-closed")
            admitted.add((helper, index))
        contract_require(exit_indices == set(range(count)), "service-exit-index-set")
    contract_require(seen == set(expected), "service-roster-difference")
    return frozenset(admitted)

# This source is embedded into the recovered owner by build_r39.py. It reuses
# that owner's GraphBrowserOwner, OwnerLifecycle and actual SDK/Host functions.
# No second global allocator exists here: only ONE Node-assigned owner slice.

class AssignedOwnerSubquota:
    def __init__(self, maximum_bytes, maximum_files):
        require(type(maximum_bytes) is int and 4096 <= maximum_bytes <= 536870912
                and type(maximum_files) is int and 16 <= maximum_files <= 4096, "owner-subquota-shape")
        self.maximum_bytes, self.maximum_files = maximum_bytes, maximum_files
        self.close_bytes, self.close_files = min(1048576, maximum_bytes // 8), min(32, maximum_files // 8)
        self.used = {"normal": [0, 0], "close": [0, 0]}
        self.failed = {"normal": False, "close": False}
        self.closing = False
        self.lock = threading.Lock()

    def charge(self, size, *, new_file=False, lane=None):
        lane = lane or ("close" if self.closing else "normal")
        require(lane in self.used and type(size) is int and size >= 0 and type(new_file) is bool,
                "owner-subquota-charge-shape")
        with self.lock:
            cap = (self.close_bytes, self.close_files) if lane == "close" else (
                self.maximum_bytes - self.close_bytes, self.maximum_files - self.close_files)
            require(not self.failed[lane], "owner-subquota-already-tainted")
            if self.used[lane][0] + size > cap[0] or self.used[lane][1] + int(new_file) > cap[1]:
                self.failed[lane] = True
                raise GraphOwnerFailure("owner-subquota-exhausted")
            self.used[lane][0] += size
            self.used[lane][1] += int(new_file)
        # Failed/partial writes never restore either charge.

    def fail(self, lane):
        with self.lock:
            self.failed[lane] = True


_OWNER_SUBQUOTA = None
_OWNER_SPEC = None
_PARENT_ROOT = None
_INITIALIZER_ORIGINAL = None
_INITIALIZER_PHYSICAL = None
_COHORT_MODULE = None
_COHORT_MANAGER = None
_COHORT_SCOPE = None
_COHORT_RECEIPT = None
_COHORT_RESULT = None
_COHORT_CLOSE_ATTEMPTED = False
_COHORT_PUBLISH = None


def require_subquota():
    require(_OWNER_SUBQUOTA is not None, "owner-subquota-required-before-writer")
    return _OWNER_SUBQUOTA


class PinnedDirectory:
    """Retain every named parent and use openat-style dir_fd leaf operations."""
    def __init__(self, path):
        self.path = Path(path)
        require(absolute_linux_path(str(path)) and str(self.path) == str(path), "directory-canonical-path")
        self.held = []
        self.closed = False
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            descriptor = os.open("/", flags)
            self.held.append((descriptor, None, os.fstat(descriptor)))
            for name in self.path.parts[1:]:
                require(name not in (".", ".."), "directory-component")
                descriptor = os.open(name, flags, dir_fd=descriptor)
                self.held.append((descriptor, name, os.fstat(descriptor)))
            self.fd = descriptor
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self):
        require(not self.closed, "directory-pin-closed")
        for index, (descriptor, name, before) in enumerate(self.held):
            current = os.fstat(descriptor)
            named = os.stat("/", follow_symlinks=False) if index == 0 else os.stat(
                name, dir_fd=self.held[index - 1][0], follow_symlinks=False)
            require(stat.S_ISDIR(current.st_mode) and stat.S_ISDIR(named.st_mode)
                    and (before.st_dev, before.st_ino) == (current.st_dev, current.st_ino)
                    == (named.st_dev, named.st_ino), "directory-pin-changed")

    def close(self):
        if self.closed:
            return
        self.closed = True
        error = None
        for descriptor, _, _ in reversed(self.held):
            try:
                os.close(descriptor)
            except OSError as failure:
                error = error or failure
        if error is not None:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def owner_original_path(path):
    path = Path(path)
    require(_OWNER_SPEC is not None and absolute_linux_path(str(path))
            and path.is_relative_to(Path(_OWNER_SPEC["paths"]["private_root"]))
            and path != Path(_OWNER_SPEC["paths"]["private_root"]), "owner-original-path-outside-private-root")
    return path


def file_stat_tuple(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def new_bytes(path, raw):
    path = owner_original_path(path)
    require(type(raw) is bytes, "owner-original-bytes-required")
    budget = require_subquota()
    lane = "close" if budget.closing else "normal"
    descriptor = None
    try:
        with PinnedDirectory(path.parent) as parent:
            budget.charge(len(raw), new_file=True, lane=lane)
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent.fd)
            parent.verify()
            view = memoryview(raw)
            while view:
                count = os.write(descriptor, view)
                require(count > 0, "owner-original-zero-write")
                view = view[count:]
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            require(stat.S_ISREG(written.st_mode) and written.st_nlink == 1 and written.st_size == len(raw), "owner-original-written-file")
            os.close(descriptor)
            descriptor = None
            os.fsync(parent.fd)
            parent.verify()
            observed, _ = read_original(path, len(raw))
            named = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            require(observed == raw and file_stat_tuple(written) == file_stat_tuple(named), "owner-original-readback-identity")
            parent.verify()
    except BaseException:
        budget.fail(lane)
        raise
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                budget.fail(lane)


def private_directory(path):
    path = Path(path)
    require(absolute_linux_path(str(path)), "private-directory-path")
    with PinnedDirectory(path.parent) as parent:
        os.mkdir(path.name, mode=0o700, dir_fd=parent.fd)
        info = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700, "private-directory-mode")
        os.fsync(parent.fd)
        parent.verify()
    return path


class OriginalPipeCapture:
    """Bounded raw draining. Only an actual zero-byte pipe read establishes EOF.

    Close cannot create EOF. Early close, deadline, parent replacement, partial
    writes or a live reader thread permanently make this stream incomplete.
    """
    def __init__(self, path, maximum, deadline):
        self.path = owner_original_path(path)
        require(type(maximum) is int and 0 < maximum <= 1048576, "owner-stream-bound")
        self.maximum, self.deadline = maximum, deadline
        self.budget = require_subquota()
        self.fd = None
        self.parent = None
        self.eof, self.closed, self.failure, self.count = False, False, None, 0
        self.thread, self.pipe = None, None
        self.write_lock = threading.Lock()
        self.stop = threading.Event()
        self.expected_hash = hashlib.sha256()
        try:
            self.parent = PinnedDirectory(self.path.parent)
            self.budget.charge(0, new_file=True, lane="normal")
            self.fd = os.open(self.path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.parent.fd)
            self.parent.verify()
        except BaseException:
            self.budget.fail("normal")
            try:
                if self.fd is not None:
                    os.close(self.fd)
            finally:
                if self.parent is not None:
                    self.parent.close()
            raise

    def fail(self, error):
        if self.failure is None:
            self.failure = type(error).__name__ + ":" + str(error)
        self.budget.fail("normal")

    def bind(self, pipe):
        try:
            require(self.thread is None and not self.closed, "raw-pipe-bind-replay")
            self.pipe = pipe
            os.set_blocking(pipe.fileno(), False)
            self.thread = threading.Thread(target=self._drain, name="owner-raw-original", daemon=True)
            self.thread.start()
        except BaseException as error:
            self.fail(error)
            raise

    def _drain(self):
        try:
            while not self.stop.is_set():
                remaining = self.deadline - time.monotonic()
                require(remaining > 0, "owner-stream-deadline")
                ready, _, _ = select.select([self.pipe], [], [], min(0.1, remaining))
                if not ready:
                    continue
                try:
                    raw = os.read(self.pipe.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not raw:
                    self.eof = True
                    return
                with self.write_lock:
                    if self.failure is not None or self.closed:
                        continue
                    try:
                        self.parent.verify()
                        require(self.count + len(raw) <= self.maximum, "owner-stream-limit")
                        self.budget.charge(len(raw), lane="normal")
                        self.count += len(raw)
                        self.expected_hash.update(raw)
                        view = memoryview(raw)
                        while view:
                            count = os.write(self.fd, view)
                            require(count > 0, "owner-stream-zero-write")
                            view = view[count:]
                    except BaseException as error:
                        self.fail(error)
            if not self.eof:
                self.fail(GraphOwnerFailure("owner-stream-stop-before-eof"))
        except BaseException as error:
            self.fail(error)
        finally:
            try:
                self.pipe.close()
            except BaseException as error:
                self.fail(error)

    def flush(self):
        if self.thread is not None:
            self.thread.join(timeout=max(0.0, min(5.0, self.deadline - time.monotonic())))
        require(self.thread is not None and not self.thread.is_alive() and self.eof and self.failure is None,
                "owner-stream-eof-or-custody-incomplete")
        self.parent.verify()

    def fileno(self):
        require(not self.closed, "owner-stream-already-closed")
        return self.fd

    def close(self):
        if self.closed:
            return
        if self.thread is not None:
            self.thread.join(timeout=max(0.0, min(5.0, self.deadline - time.monotonic())))
        if self.thread is None or self.thread.is_alive() or not self.eof:
            self.fail(GraphOwnerFailure("owner-stream-close-before-eof"))
            self.stop.set()
            if self.thread is not None:
                self.thread.join(timeout=0.2)
        with self.write_lock:
            self.closed = True
            descriptor_open = True
            try:
                os.fsync(self.fd)
                written = os.fstat(self.fd)
                require(stat.S_ISREG(written.st_mode) and written.st_nlink == 1, "owner-stream-written-file")
                self.parent.verify()
                os.close(self.fd)
                descriptor_open = False
                os.fsync(self.parent.fd)
                raw, reference = read_original(self.path, self.maximum)
                named = os.stat(self.path.name, dir_fd=self.parent.fd, follow_symlinks=False)
                require(file_stat_tuple(written) == file_stat_tuple(named), "owner-stream-readback-identity")
                if self.failure is None:
                    require(len(raw) == self.count and reference["sha256"] == self.expected_hash.hexdigest(), "owner-stream-readback-bytes")
                self.parent.verify()
            except BaseException as error:
                self.fail(error)
                raise
            finally:
                try:
                    if descriptor_open:
                        os.close(self.fd)
                finally:
                    self.parent.close()
        require(self.thread is None or not self.thread.is_alive(), "owner-raw-thread-still-live")


def validate_literal_schema(value, schema, location="$"):
    if "anyOf" in schema:
        for item in schema["anyOf"]:
            try:
                validate_literal_schema(value, item, location)
                return
            except (GraphOwnerFailure, TypeError, ValueError):
                pass
        raise GraphOwnerFailure("literal-schema-anyOf:" + location)
    if "const" in schema:
        require(type(value) is type(schema["const"]) and value == schema["const"], "literal-schema-const:" + location)
    if "enum" in schema:
        require(any(type(value) is type(item) and value == item for item in schema["enum"]), "literal-schema-enum:" + location)
    kind = schema.get("type")
    if kind == "object":
        require(type(value) is dict, "literal-schema-object:" + location)
        require(set(schema.get("required", ())) <= set(value), "literal-schema-required:" + location)
        for key, item in value.items():
            if key in schema.get("properties", {}):
                validate_literal_schema(item, schema["properties"][key], location + "." + key)
            else:
                require(schema.get("additionalProperties") is not False, "literal-schema-extra:" + location)
    elif kind == "array":
        require(type(value) is list and len(value) <= schema.get("maxItems", 9007199254740991), "literal-schema-array:" + location)
        for item in value:
            validate_literal_schema(item, schema["items"], location + "[]")
    elif kind == "string":
        require(type(value) is str and len(value) >= schema.get("minLength", 0)
                and ("pattern" not in schema or re.search(schema["pattern"], value)), "literal-schema-string:" + location)
    elif kind == "integer":
        require(type(value) is int and schema.get("minimum", -9007199254740991) <= value <= schema.get("maximum", 9007199254740991),
                "literal-schema-integer:" + location)
    elif kind == "boolean":
        require(type(value) is bool, "literal-schema-boolean:" + location)
    elif kind == "null":
        require(value is None, "literal-schema-null:" + location)


def read_original(path, maximum, *, expected_sha=None, root=None):
    path = Path(path)
    require(absolute_linux_path(str(path)) and type(maximum) is int and 0 <= maximum <= 536870912
            and (root is None or path.is_relative_to(Path(root))), "original-path-or-root")
    with PinnedDirectory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd)
        try:
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= maximum, "original-kind-or-limit")
            chunks, remaining = [], before.st_size
            while remaining:
                raw = os.read(descriptor, min(65536, remaining))
                require(bool(raw), "original-short-read")
                chunks.append(raw)
                remaining -= len(raw)
            after = os.fstat(descriptor)
            named = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
            require(fields(before) == fields(after) == fields(named) and not stat.S_ISLNK(named.st_mode), "original-changed")
            parent.verify()
            raw = b"".join(chunks)
            sha = hashlib.sha256(raw).hexdigest()
            require(expected_sha is None or sha == expected_sha, "original-sha-mismatch")
            return raw, {"path": str(path), "sha256": sha, "size_bytes": len(raw)}
        finally:
            os.close(descriptor)


def file_identity(path):
    path = Path(path)
    require(absolute_linux_path(str(path)), "identity-canonical-path")
    with PinnedDirectory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd)
        try:
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 536870912, "identity-kind-or-limit")
            digest_value, remaining = hashlib.sha256(), before.st_size
            while remaining:
                raw = os.read(descriptor, min(65536, remaining))
                require(bool(raw), "identity-short-read")
                digest_value.update(raw)
                remaining -= len(raw)
            after = os.fstat(descriptor)
            named = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
            require(fields(before) == fields(after) == fields(named) and not stat.S_ISLNK(named.st_mode), "identity-changed")
            parent.verify()
            return {"path": str(path), "device": before.st_dev, "inode": before.st_ino,
                    "bytes": before.st_size, "sha256": digest_value.hexdigest()}
        finally:
            os.close(descriptor)


def digest(path):
    return file_identity(path)["sha256"]


def original_ref(path):
    return read_original(path, 16 * 1024 * 1024)[1]


def validate_spec_original(path):
    raw, reference = read_original(path, 1024 * 1024)
    spec = strict_json(raw)
    validate_literal_schema(spec, _SPEC_SCHEMA)
    for name, value in spec["paths"].items():
        require(absolute_linux_path(value), "spec-private-path-shape:" + name)
    for value in (spec["source"]["repository_root"], spec["source"]["sdk_root"], spec["store"]["database_path"]):
        require(absolute_linux_path(value), "spec-source-path-shape")
    require(Path(spec["store"]["database_path"]).name == "motionwright.sqlite3", "spec-actual-store-name")
    now = int(time.time() * 1000)
    require(now < spec["limits"]["deadline_unix_ms"] <= now + 900000, "spec-deadline")
    root = Path(spec["paths"]["private_root"])
    require(not root.exists() and root.parent.resolve(strict=True) == root.parent, "spec-fresh-owner-root")
    require(spec["paths"]["owner_home"] == str(root / "home")
            and spec["paths"]["owner_state"] == str(root / "state")
            and spec["paths"]["manim_work"] == str(root / "work")
            and spec["paths"]["owner_runtime"] == str(root / "runtime")
            and spec["paths"]["owner_socket"] == str(root / "runtime/broker.sock"), "spec-canonical-owner-path-layout")
    require(len(spec["paths"]["owner_socket"].encode()) <= 100, "spec-owner-socket-path-bound")
    parent = Path(os.environ.get("MOTIONWRIGHT_GRAPH_PARENT_ROOT", ""))
    require(parent.is_absolute() and parent.resolve(strict=True) == parent
            and root.is_relative_to(parent) and Path(path).is_relative_to(parent), "spec-parent-custody-root")
    require(not Path(spec["store"]["database_path"]).is_relative_to(root), "spec-store-owner-root-disjoint")
    return spec, reference, parent


def read_initializer_original(parent, database):
    global _INITIALIZER_PHYSICAL
    path = os.environ.get("MOTIONWRIGHT_GRAPH_INITIALIZER_ORIGINAL", "")
    expected_sha = os.environ.get("MOTIONWRIGHT_GRAPH_INITIALIZER_SHA256", "")
    require(DIGEST.fullmatch(expected_sha) is not None, "initializer-original-digest-required")
    raw, reference = read_original(path, 1048576, expected_sha=expected_sha, root=parent)
    value = strict_json(raw)
    require(type(value) is dict and set(value) == {"context", "process_id", "exit", "raw", "physical_store_original"}, "initializer-original-fields")
    context = value["context"]
    require(type(context) is dict and set(context) == {"helper_id", "launch_index", "binary_path", "database_path"}
            and UUID_TEXT.fullmatch(context["helper_id"]) is not None
            and type(context["launch_index"]) is int and context["launch_index"] == 0
            and absolute_linux_path(context["binary_path"]) and context["database_path"] == database,
            "initializer-context")
    exit_row = value["exit"]
    require(type(value["process_id"]) is int and value["process_id"] > 0
            and type(exit_row) is dict and set(exit_row) == {"processId", "code", "signal", "observedAt"}
            and exit_row["processId"] == value["process_id"]
            and type(exit_row["code"]) is int and exit_row["code"] == 0 and exit_row["signal"] is None
            and original_timestamp(exit_row["observedAt"]), "initializer-actual-exit")
    require(type(value["raw"]) is dict and set(value["raw"]) == {"stdout", "stderr"}, "initializer-raw-fields")
    paths = set()
    for stream in value["raw"].values():
        require(type(stream) is dict and set(stream) == {"path", "sha256", "size_bytes", "eof"}
                and stream["eof"] is True and stream["path"] not in paths, "initializer-raw-shape")
        paths.add(stream["path"])
        _, observed = read_original(stream["path"], 1048576, expected_sha=stream["sha256"], root=parent)
        require(observed["size_bytes"] == stream["size_bytes"], "initializer-raw-size")
    physical_original = value["physical_store_original"]
    require(type(physical_original) is dict and set(physical_original) == {"path", "sha256", "size_bytes"}, "initializer-physical-original-fields")
    physical_raw, physical_ref = read_original(physical_original["path"], 65536, expected_sha=physical_original["sha256"], root=parent)
    require(physical_ref == physical_original, "initializer-physical-original-reference")
    physical = strict_json(physical_raw)
    require(type(physical) is dict and set(physical) == {"path", "device", "inode"}
            and physical["path"] == database and all(type(physical[name]) is str and re.fullmatch(r"[0-9]+", physical[name]) for name in ("device", "inode")), "initializer-physical-fields")
    current = file_identity(database)
    require(physical == {"path": current["path"], "device": str(current["device"]), "inode": str(current["inode"])}, "initializer-physical-store-replaced")
    _INITIALIZER_PHYSICAL = physical
    return value, reference


def verify_source279_runtime(repo, source_inputs, runtime):
    module = importlib.import_module("native_manim_graph_runtime_intake")
    require(Path(module.__file__).resolve() == repo / "tooling/native_manim_graph_runtime_intake.py", "source279-module-origin")
    originals, entries, rows = module._inputs(repo)
    path = Path(_OWNER_SPEC["runtime"]["intake_receipt"])
    raw, reference = read_original(path, 2 * 1024 * 1024, expected_sha=_OWNER_SPEC["runtime"]["intake_receipt_sha256"])
    value = strict_json(raw)
    expected = {
        "schema": "motionwright.manim.graph-runtime-source-intake/1", "status": "SOURCE_INTAKE_ONLY",
        "profile_id": "mw-manim-semantic-graph-v1", "source_root": str(repo), "runtime_root": str(runtime["runtime"]),
        "source_tree_binding": "OWNER_MUST_VERIFY_CURRENT_GIT_SOURCE",
        "compiler_source": {"relative_path": module.COMPILER, "sha256": module.sha(originals[0][1]), "size_bytes": len(originals[0][1])},
        "source_lock": {"relative_path": "SOURCE_LOCK.json", "sha256": module.sha(originals[1][1]), "size_bytes": len(originals[1][1])},
        "files": rows, "files_count": len(rows), "total_original_bytes": sum(map(len, entries.values())),
        "source_closure_sha256": module.sha(module._encode(rows)),
        "existing_destination_policy": "VERIFY_EXACT_COMPLETE_OR_REJECT_WITHOUT_WRITES",
        "compiled_runner_pin_agreement_verified": False, "native_font_open_observed": False,
        "pango_layout_observed": False, "native_render_performed": False, "profile_admitted": False,
    }
    require(value == expected and raw == module._encode(expected), "source279-intake-original-mismatch")
    entries[module.CLOSURE_NAME] = raw
    module._verify_profile(runtime["runtime"] / module.PROFILE_DIRECTORY, entries)
    module._unchanged(repo, originals)
    context = {"source_sha": source_inputs["source"], "semwright_sha": source_inputs["sdk_source"],
               "run_id": _OWNER_SPEC["source"]["run_id"], "run_attempt": str(_OWNER_SPEC["source"]["run_attempt"])}
    manifest = Path(_OWNER_SPEC["runtime"]["manifest"])
    require(file_identity(manifest)["sha256"] == _OWNER_SPEC["runtime"]["manifest_sha256"], "runtime-manifest-digest")
    check_base_runtime_inventory(manifest, source_inputs, runtime, context)
    return reference


def load_graph_runtime_custody(repo, source_inputs, runtime, lifecycle):
    intake = verify_source279_runtime(repo, source_inputs, runtime)
    new_json(lifecycle.evidence / "runtime-source-intake-verified.original.json", {
        "intake": intake, "source": _OWNER_SPEC["source"], "profile_admitted": False,
        "actual_producer_originals_validated": _GRAPH_J1 is not None})
    admitted = revalidate_original_producer_admission(lifecycle)
    require(admitted["index"]["runtime"] == _OWNER_SPEC["runtime"]
            and admitted["index"]["runtime_originals"]["intake"] == intake,
            "graph-runtime-not-the-independent-producer-original")
    return {"intake": intake, "producer_index": admitted["index_ref"],
            "independent_status_id": admitted["approval"]["status_id"],
            "actual_producer_originals_validated": True,
            "shared_runtime_before_and_after_checks_required_from_ci": True,
            "profile_admitted": False}


def runtime_inputs(repo):
    value = _OWNER_SPEC["runtime"]
    runtime = canonical_directory(value["root"])
    fontconfig = canonical_directory(value["fontconfig_root"])
    python = executable_identity(_OWNER_SPEC["binaries"]["python3"]["path"])
    ffmpeg = executable_identity(_OWNER_SPEC["binaries"]["ffmpeg"]["path"])
    for name, observed in (("python3", python), ("ffmpeg", ffmpeg)):
        require(observed["sha256"] == _OWNER_SPEC["binaries"][name]["sha256"]
                and observed["bytes"] == _OWNER_SPEC["binaries"][name]["size_bytes"], "runtime-executable-mismatch")
    return {"runtime": runtime, "fontconfig": fontconfig, "python": python, "ffmpeg": ffmpeg}


def validate_bound_source(source_inputs):
    source = _OWNER_SPEC["source"]
    require(source_inputs["source"] == source["sha"] and source_inputs["tree"] == source["tree"]
            and source_inputs["sdk_source"] == source["sdk_sha"], "spec-source-tuple-mismatch")
    for name, expected in source["locks"].items():
        require(source_inputs["sources"][name]["sha256"] == expected, "spec-lock-mismatch")
    for old, new in (("semwright", "semwright"), ("semwrightd", "semwrightd"),
                     ("motionwright-native-provider", "native_provider"), ("motionwright-manim-runner", "manim_runner"),
                     ("shared-state-actor", "shared_state_actor")):
        actual, expected = source_inputs["binaries"][old], _OWNER_SPEC["binaries"][new]
        require(actual["path"] == expected["path"] and actual["sha256"] == expected["sha256"]
                and actual["bytes"] == expected["size_bytes"], "spec-binary-mismatch")


def observe_existing_store(graph):
    """Use the declared actual Service actor only in its existing show mode."""
    expected = _OWNER_SPEC["binaries"]["shared_state_actor"]
    actual = executable_identity(expected["path"])
    require(actual["sha256"] == expected["sha256"] and actual["bytes"] == expected["size_bytes"], "store-observer-binary-mismatch")
    project_id = _OWNER_SPEC["store"]["project_id"]
    argv = [actual["path"], str(graph.database), "show", project_id]
    child = graph.lifecycle.start("existing-store-show", argv, dict(os.environ))
    terminal = child.finish(timeout=_OWNER_SPEC["limits"]["cli_timeout_ms"] / 1000)
    require(terminal["status"] == "SUCCESS" and terminal["exit_code"] == 0, "store-observer-not-successful")
    raw, reference = read_original(child.stdout, 256000, root=graph.lifecycle.evidence)
    value = strict_json(raw)
    require(type(value) is dict and value.get("id") == project_id
            and value.get("resource") == "project:" + project_id, "store-observer-project-mismatch")
    current = file_identity(graph.database)
    require((current["device"], current["inode"]) == (graph.database_initial["device"], graph.database_initial["inode"]), "store-observer-physical-store-changed")
    new_json(graph.lifecycle.evidence / "existing-store-show-verified.original.json", {
        "command": argv, "stdout_original": reference, "binary_original": actual,
        "project_id": project_id, "physical_store_unchanged": True, "init_rename_copy_rebind": False})
    return reference


def observe_source_before_close(graph):
    revalidate_original_producer_admission(graph.lifecycle)
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    for label, args, expected in (
        ("final-source-head", ["rev-parse", "HEAD"], _OWNER_SPEC["source"]["sha"]),
        ("final-source-tree", ["rev-parse", "HEAD^{tree}"], _OWNER_SPEC["source"]["tree"]),
        ("final-source-status", ["status", "--porcelain=v1", "--untracked-files=no"], "")):
        require(captured_text(graph.lifecycle, label, ["git", "-C", str(graph.repo), *args], env).strip() == expected,
                "source-changed-before-close")
    for record in graph.source_inputs["sources"].values():
        require(file_identity(record["path"]) == record, "source-original-changed-before-close")
    for record in graph.source_inputs["binaries"].values():
        require(file_identity(record["path"]) == record, "binary-changed-before-close")


def literal_service_barrier(graph, barrier):
    require(type(barrier) is dict and set(barrier) == {"closed", "receipt_path", "receipt_sha256"}, "service-barrier-fields")
    require(barrier["closed"] is True and type(barrier["receipt_path"]) is str
            and type(barrier["receipt_sha256"]) is str and DIGEST.fullmatch(barrier["receipt_sha256"]), "service-barrier-not-closed")
    raw, reference = read_original(barrier["receipt_path"], 4 * 1024 * 1024,
                                  expected_sha=barrier["receipt_sha256"], root=_PARENT_ROOT)
    initializer = _INITIALIZER_ORIGINAL[0]["context"]
    expected = [ExpectedServiceLaunches(initializer["helper_id"], "initializer", initializer["binary_path"], str(graph.database), (0,))]
    grouped = {}
    for row in graph.client_ledger.snapshot():
        require(row["status"] == "READY", "service-unmatched-client")
        grouped.setdefault(row["helper_id"], []).append(row["launch_index"])
    value = strict_json(raw)
    # The current closed R2 spec has no Native Service binary input. Do not use
    # participant.binary_path as its own expected source. The producer verifier
    # must populate this from independently checked build originals.
    native_binary = getattr(graph, "verified_native_service_binary", None)
    require(type(native_binary) is str and absolute_linux_path(native_binary), "native-service-producer-binding-unavailable")
    for helper_id, indices in grouped.items():
        expected.append(ExpectedServiceLaunches(helper_id, "native", native_binary, str(graph.database), tuple(indices)))
    admitted = validate_service_closure(value, database_path=str(graph.database), expected_services=expected)
    graph.client_ledger.require_actual_launch_bijection(frozenset(item for item in admitted if item[0] != initializer["helper_id"]))
    return reference


def close_complete_cohort():
    global _COHORT_RESULT, _COHORT_CLOSE_ATTEMPTED
    require(_COHORT_MODULE is not None and _COHORT_SCOPE is not None, "cohort-supervision-unavailable")
    if _COHORT_RESULT is None:
        require(not _COHORT_CLOSE_ATTEMPTED, "cohort-close-prior-attempt-has-no-original-no-retry")
        _COHORT_CLOSE_ATTEMPTED = True
        error = None
        try:
            _COHORT_SCOPE.__exit__(None, None, None)
        except BaseException as failure:
            error = type(failure).__name__
        raw, _ = read_original(_COHORT_RECEIPT, 4 * 1024 * 1024, root=_OWNER_SPEC["paths"]["private_root"])
        result = strict_json(raw)
        observed = result.get("complete_empty_descendant_tree", {})
        complete = (error is None and result.get("status") == "PASS" and observed.get("root_observed") is True
                    and observed.get("truncated") is False and len(observed.get("processes", ())) == 1
                    and observed["processes"][0].get("pid") == os.getpid())
        _COHORT_RESULT = (result, complete)
    return _COHORT_RESULT


def graph_close_v6(self, *, service_barrier=None, request_id=None, reason="explicit-close"):
    require(not self.closed, "graph-owner-close-replay")
    self.client_ledger.seal()
    errors, service_original, source_validated = [], None, False
    try:
        service_original = literal_service_barrier(self, service_barrier)
    except BaseException:
        errors.append("SERVICE_BARRIER_INVALID")
    try:
        observe_source_before_close(self)
        source_validated = True
    except BaseException:
        errors.append("SOURCE_MISMATCH")
    self.closed = True
    require_subquota().closing = True
    lifecycle_receipt, cohort, cohort_absent = None, None, False
    try:
        lifecycle_receipt = self.lifecycle.close(reason=reason)
    except BaseException:
        errors.append("OWNER_CLOSE_FAILED")
    finally:
        try:
            cohort, cohort_absent = close_complete_cohort()
        except BaseException:
            errors.append("OWNER_CLOSE_FAILED")
    streams_eof = all(child.out.eof and child.err.eof and child.out.failure is None and child.err.failure is None
                      and child.out.closed and child.err.closed for child in self.lifecycle.children)
    current, same_database, database_final = None, False, None
    try:
        current = file_identity(self.database)
        same_database = (current["device"], current["inode"]) == (self.database_initial["device"], self.database_initial["inode"])
        new_json(self.lifecycle.evidence / "database-final.original.json", {**current, "device": str(current["device"]), "inode": str(current["inode"])})
        database_final = original_ref(self.lifecycle.evidence / "database-final.original.json")
    except BaseException:
        errors.append("STORE_INVALID")
    if any(require_subquota().failed.values()) or not streams_eof:
        errors.append("CUSTODY_FAILED")
    if not cohort_absent:
        errors.append("OWNER_CLOSE_FAILED")
    closed = (not errors and source_validated and service_original is not None and same_database and cohort_absent
              and streams_eof and lifecycle_receipt is not None and lifecycle_receipt["status"] == "CLOSED"
              and not self.client_ledger.failed and reason == "explicit-close")
    owner_close = None
    try:
        new_json(self.lifecycle.evidence / "owner-complete-close.original.json", {
            "lifecycle": lifecycle_receipt, "cohort": cohort, "owner_streams_eof": streams_eof,
            "source_validated_before_close": source_validated, "status": "CLOSED" if closed else "UNKNOWN"})
        owner_close = original_ref(self.lifecycle.evidence / "owner-complete-close.original.json")
    except BaseException:
        closed = False
        errors.append("CUSTODY_FAILED")
    roster = []
    for attempt in self.clients:
        row = {key: attempt[key] for key in ("client_id", "id", "helper_id", "launch_index")}
        row.update(status="READY" if attempt["status"] == "CLIENT_READY_NOT_ACCEPTANCE" else "UNKNOWN",
                   connection_original=None, session_original=None, output_root=None)
        if row["status"] == "READY":
            try:
                row["connection_original"] = original_ref(attempt["connection"]["path"])
                row["session_original"] = original_ref(attempt["session"]["path"])
                connection_raw, _ = read_original(row["connection_original"]["path"], 65536)
                row["output_root"] = strict_json(connection_raw)["output_root"]
            except BaseException:
                row["status"] = "UNKNOWN"
                errors.append("CUSTODY_FAILED")
                closed = False
        roster.append(row)
    result = {"schema": "motionwright.graph-browser-owner-final/6", "case_id": _OWNER_SPEC["case_id"],
              "status": "CLOSED" if closed else "UNKNOWN", "source": _OWNER_SPEC["source"], "spec_original": self.spec_original,
              "source_inputs": original_ref(self.lifecycle.evidence / "source-inputs.json"), "client_roster": roster,
              "service_closure": service_original, "service_closure_validated": service_original is not None,
              "owner_close": owner_close, "database_initial": self.database_initial_original, "database_final": database_final,
              "same_physical_database": same_database, "owner_cohort_absent": cohort_absent, "owner_streams_eof": streams_eof,
              "capture_permitted": closed, "errors": sorted(set(errors)), "native_authoring_approved": False, "formal_acceptance": False}
    validate_literal_schema(result, _FINAL_SCHEMA)
    final_path = self.lifecycle.evidence / "owner-final.original.json"
    new_json(final_path, result)
    reference = original_ref(final_path)
    return {"event": "FINAL_RECEIPT", "id": request_id, "case_id": result["case_id"], "status": result["status"],
            "receipt_path": reference["path"], "receipt_sha256": reference["sha256"], "owner_cohort_absent": cohort_absent,
            "owner_streams_eof": streams_eof, "capture_permitted": closed}


def control_messages(stream, *, deadline):
    buffer = bytearray()
    while time.monotonic() < deadline:
        raise_deferred_cancellation()
        ready, _, _ = select.select([stream], [], [], min(0.2, max(0.0, deadline - time.monotonic())))
        if not ready:
            continue
        raw = os.read(stream.fileno(), MAX_CONTROL_BYTES + 1)
        require(bool(raw), "control-eof-before-explicit-close")
        buffer.extend(raw)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            require(len(line) <= MAX_CONTROL_BYTES, "control-message-budget")
            buffer = bytearray(rest)
            value = strict_json(line.decode("utf8", errors="strict"))
            require(type(value) is dict and type(value.get("id")) is str and 0 < len(value["id"]) <= 180, "control-message-id")
            require((value.get("op") == "new-client" and set(value) == {"op", "id"})
                    or (value.get("op") == "close" and set(value) == {"op", "id", "service_barrier"}), "control-message-fields")
            yield value
        require(len(buffer) <= MAX_CONTROL_BYTES, "control-message-budget")
    raise GraphOwnerFailure("owner-lifetime-deadline-unknown-no-retry")


def emit(value):
    if value.get("event") in ("READY", "CLIENT") or value.get("status") == "CLOSED":
        raise_deferred_cancellation()
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    require(len(raw) <= 65536, "owner-public-protocol-bound")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


_GRAPH_J1 = None
_GRAPH_J1_ATTEMPTED = False
_GRAPH_READ_TOKEN = None
_GRAPH_API_SEQUENCE = 0
_GRAPH_IMPORT_CLOSURE = tuple("tooling/" + name + ".py" for name in (
    "native_graph_browser_owner", "ci_canvas_service", "native_host_shared_state", "native_host_installed_shared_state",
    "native_manim_graph_runtime_intake", "native_av_failure_guard", "candidate_smoke",
    "linux_first_use_custody", "linux_cold_start", "linux_first_use_accessible",
    "source_checkout_observation", "storage_schema_contract", "linux_gui_probe",
    "native_provider_lifecycle", "runtime_first_use_audit", "manim_bundled_font_intake", "mlt_official_runtime_intake"))


def independently_observe_r74(index, index_ref, parent, lifecycle):
    """The owner obtains real HTTPS originals itself before importing producers."""
    import ssl
    import urllib.error
    import urllib.request
    global _GRAPH_API_SEQUENCE
    source, approval = _OWNER_SPEC["source"], parent["approval"]
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and os.environ.get("GITHUB_API_URL") == "https://api.github.com"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_JOB") == "real-service"
            and os.environ.get("GITHUB_SHA") == source["sha"] == os.environ.get("GITHUB_WORKFLOW_SHA")
            and os.environ.get("GITHUB_RUN_ID") == source["run_id"]
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1" and source["run_attempt"] == 1
            and type(_GRAPH_READ_TOKEN) is str and _GRAPH_READ_TOKEN, "owner-r74-actual-job-context")
    require(os.environ.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/.github/workflows/studio-service-verification.yml@"), "owner-r74-workflow")
    end = time.monotonic() + min(12, max(0, (_OWNER_SPEC["limits"]["deadline_unix_ms"] - time.time() * 1000) / 1000))
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise GraphOwnerFailure("owner-r74-redirect-refused")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    base = "https://api.github.com/repos/seradotcom/motionwright"
    def fetch(suffix):
        global _GRAPH_API_SEQUENCE
        require(time.monotonic() < end, "owner-r74-original-deadline")
        label = "r74-api-" + str(_GRAPH_API_SEQUENCE); _GRAPH_API_SEQUENCE += 1
        url = base + suffix
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + _GRAPH_READ_TOKEN,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"})
        started = time.monotonic_ns()
        try:
            response = opener.open(request, timeout=max(.001, end - time.monotonic()))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(256 * 1024 + 1)
            path = lifecycle.evidence / (label + ".response.original.json")
            new_bytes(path, raw)
            meta = {"url": url, "status": response.status, "date": response.headers.get("Date"),
                    "age": response.headers.get("Age", "0"), "link": response.headers.get("Link", ""),
                    "request_id": response.headers.get("X-GitHub-Request-Id"),
                    "started_mono_ns": started, "finished_mono_ns": time.monotonic_ns(), "body": original_ref(path)}
            new_json(lifecycle.evidence / (label + ".transport.original.json"), meta)
            require(response.status == 200 and meta["age"] == "0" and type(meta["date"]) is str
                    and 0 < len(raw) <= 256 * 1024 and time.monotonic() <= end, "owner-r74-http-denied")
            return strict_json(raw), meta
    run, _ = fetch("/actions/runs/" + source["run_id"])
    require(type(run.get("id")) is int and str(run["id"]) == source["run_id"]
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1 and run.get("head_sha") == source["sha"]
            and run.get("repository", {}).get("full_name") == "seradotcom/motionwright"
            and run.get("head_repository", {}).get("full_name") == "seradotcom/motionwright"
            and run.get("event") == "workflow_dispatch" and run.get("status") == "in_progress"
            and run.get("path", "").split("@", 1)[0] == ".github/workflows/studio-service-verification.yml"
            and type(run.get("workflow_id")) is int and run["workflow_id"] > 0
            and run.get("actor", {}).get("type") == "User" and type(run["actor"].get("id")) is int
            and run["actor"]["id"] > 0, "owner-r74-original-run-actor")
    context = "mw-owner/phase1-native-graph/" + source["run_id"] + "/1/" + _OWNER_SPEC["case_id"]
    rows, selected, previous = [], None, 2**53 - 1
    for page in range(1, 5):
        values, meta = fetch("/commits/" + source["sha"] + "/statuses?per_page=100&page=" + str(page))
        require(type(values) is list and len(values) <= 100, "owner-r74-status-page")
        for row in values:
            require(type(row) is dict and type(row.get("id")) is int and 0 < row["id"] < previous,
                    "owner-r74-status-order")
            previous = row["id"]
            if selected is None and type(row.get("context")) is str and row["context"].lower() == context.lower():
                selected = row
        rows.extend(values)
        if selected is not None or len(values) < 100 and 'rel="next"' not in meta["link"]:
            break
        require(page < 4, "owner-r74-incomplete-pagination")
    require(selected is not None and selected.get("state") == "success"
            and selected.get("creator", {}).get("type") == "User" and type(selected["creator"].get("id")) is int
            and selected["creator"]["id"] == run["actor"]["id"]
            and selected.get("description") == "admission-sha256:" + index_ref["sha256"]
            and selected.get("target_url") == "https://github.com/seradotcom/motionwright/actions/runs/" + source["run_id"],
            "owner-r74-newest-status-not-authorized")
    observed = {"context": context, "status_id": selected["id"], "actor_id": run["actor"]["id"],
                "source_sha": source["sha"], "run_id": source["run_id"], "run_attempt": 1,
                "index_sha256": index_ref["sha256"], "meaning": "OWNER_ADMISSION_ONLY_NEVER_PRODUCT_OR_A5_ACCEPTANCE"}
    require(all(approval.get(key) == value for key, value in observed.items())
            and approval.get("index") == index_ref, "owner-r74-parent-and-owner-approval-differ")
    new_json(lifecycle.evidence / ("r74-independent-join-" + str(_GRAPH_API_SEQUENCE) + ".original.json"), observed)
    return observed


def receive_original_producer_admission(repo, lifecycle):
    global _GRAPH_J1, _GRAPH_J1_ATTEMPTED, _GRAPH_READ_TOKEN
    require(not _GRAPH_J1_ATTEMPTED, "owner-j1-already-consumed")
    _GRAPH_J1_ATTEMPTED = True
    _GRAPH_READ_TOKEN = os.environ.pop("MOTIONWRIGHT_GRAPH_METADATA_TOKEN", None)
    raw, parent_ref = read_original(_PARENT_ROOT / "parent-source-binding.original.json", 4 * 1024 * 1024, root=_PARENT_ROOT)
    parent = strict_json(raw)
    require(all(parent.get(name) == _OWNER_SPEC[name] for name in ("source", "binaries", "runtime")), "owner-j1-parent-spec-binding")
    index_ref = parent["producer_originals"]["index"]
    raw, actual = read_original(index_ref["path"], 1024 * 1024, expected_sha=index_ref["sha256"])
    require(actual == index_ref, "owner-j1-original-index")
    index = strict_json(raw)
    require(all(index.get(name) == _OWNER_SPEC[name] for name in ("source", "binaries", "runtime")), "owner-j1-index-spec-binding")
    approval = independently_observe_r74(index, index_ref, parent, lifecycle)
    files = index["contract"]["source_files"]
    for name in _GRAPH_IMPORT_CLOSURE:
        expected = files[name]
        observed = file_identity(repo / name)
        require(observed["bytes"] == expected["bytes"] and observed["sha256"] == expected["sha256"], "owner-j1-import-source:" + name)
    # These dictionaries are the reviewed owner's literal schemas. Require exact
    # parity with the authenticated source files before trusting a child DTO.
    for name, expected in (("spec.r2.json", _SPEC_SCHEMA), ("final.r2.json", _FINAL_SCHEMA)):
        relative = "apps/studio/tests/support/graph-owner/schemas/" + name
        schema_raw, _ = read_original(repo / relative, 1024 * 1024, expected_sha=files[relative]["sha256"])
        require(strict_json(schema_raw) == expected, "owner-j1-literal-schema-parity")
    # Import only after independent authentication and the complete import-time
    # closure check. This reuses the existing producer reader, not a new process.
    module_name = "motionwright_graph_original_reader"
    spec = importlib.util.spec_from_file_location(module_name, repo / "tooling/ci_canvas_service.py")
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    verified = reader.graph_verify_producer_index(index_ref, _OWNER_SPEC["case_id"])
    require(verified["index"] == index and parent["nativeService"] == index["binaries"]["native_graph_session"]
            and parent["ordinaryService"] == index["binaries"]["ordinary_service"], "owner-j1-service-causality")
    expected_caps = index["contract"]["cases"][_OWNER_SPEC["case_id"]]["callback_caps"]
    plan = parent["sdk_ticket_prewrite"]
    require(plan["source"] == index["source"] and plan["native_service"] == index["binaries"]["native_graph_session"]
            and plan["clients"] == [{"ordinal": number + 1, "callback_cap": cap} for number, cap in enumerate(expected_caps)]
            and plan["originals"] == {"callback_roster": index["callback_roster"], "native_enforcement": index["native_enforcement"]},
            "owner-j1-ticket-roster-binding")
    require(parent["execution"] == index["contract"]["execution"]
            and parent["execution"]["budget"]["owner_bytes"] == _OWNER_SUBQUOTA.maximum_bytes
            and parent["execution"]["budget"]["owner_files"] == _OWNER_SUBQUOTA.maximum_files,
            "owner-j1-assigned-subquota")
    consumed_path = Path(parent["execution"]["producer_root"]) / "consumption" / (_OWNER_SPEC["case_id"] + ".original.json")
    consumed_raw, consumed_ref = read_original(consumed_path, 8192)
    consumed = strict_json(consumed_raw)
    require(consumed["private_root"] == str(_PARENT_ROOT) and consumed["case_id"] == _OWNER_SPEC["case_id"]
            and consumed["index"] == index_ref and consumed["status_id"] == approval["status_id"]
            and consumed["source"] == index["source"] and consumed["automatic_retry"] is False,
            "owner-j1-exact-consumed-root")
    _GRAPH_J1 = {"parent": parent, "parent_ref": parent_ref, "index_ref": index_ref, "index": index,
                 "approval": approval, "reader": reader, "references": verified["references"], "physical": verified["physical"]}
    new_json(lifecycle.evidence / "producer-originals-independent.original.json", {
        "index": index_ref, "parent": parent_ref, "consumption": consumed_ref, "approval": approval,
        "actual_rust_build_roles": list(reader.GRAPH_RUST_ROLES), "actual_runtime_roles": list(reader.GRAPH_ROLES),
        "binary_source_causality_validated": True, "native_authoring_approved": False, "formal_acceptance": False})
    return _GRAPH_J1


def revalidate_original_producer_admission(lifecycle, *, before_effect=False):
    require(_GRAPH_J1 is not None, "owner-j1-original-admission-required")
    for ref in _GRAPH_J1["references"]:
        before = Path(ref["path"]).lstat()
        stamp = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns]
        raw, actual = read_original(ref["path"], max(ref["size_bytes"], 1), expected_sha=ref["sha256"])
        require(actual == ref and stamp == _GRAPH_J1["physical"][ref["path"]], "owner-j1-original-replaced-after-admission")
    if before_effect:
        independently_observe_r74(_GRAPH_J1["index"], _GRAPH_J1["index_ref"], _GRAPH_J1["parent"], lifecycle)
    return _GRAPH_J1


def protocol_main():
    global _OWNER_SUBQUOTA, _OWNER_SPEC, _PARENT_ROOT, _INITIALIZER_ORIGINAL, _COHORT_MODULE, _COHORT_MANAGER
    global _COHORT_SCOPE, _COHORT_RECEIPT, _COHORT_PUBLISH
    parser = argparse.ArgumentParser(description="Exact-source draft6 Graph owner")
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()
    graph = lifecycle = None
    signal.signal(signal.SIGTERM, interrupted_owner)
    signal.signal(signal.SIGINT, interrupted_owner)
    phase, request_id = "SPEC_INVALID", None
    try:
        _OWNER_SPEC, spec_original, _PARENT_ROOT = validate_spec_original(args.spec)
        decimal = lambda name: os.environ.get(name, "")
        byte_text, file_text = decimal("MOTIONWRIGHT_GRAPH_OWNER_MAX_BYTES"), decimal("MOTIONWRIGHT_GRAPH_OWNER_MAX_FILES")
        require(re.fullmatch(r"[1-9][0-9]{0,9}", byte_text) and re.fullmatch(r"[1-9][0-9]{0,4}", file_text), "owner-subquota-env-required")
        _OWNER_SUBQUOTA = AssignedOwnerSubquota(int(byte_text), int(file_text))
        lifecycle = OwnerLifecycle(Path(_OWNER_SPEC["paths"]["private_root"]))
        new_json(lifecycle.evidence / "owner-subquota.original.json", {"bytes": int(byte_text), "files": int(file_text), "global_allocator": False})
        _INITIALIZER_ORIGINAL = read_initializer_original(_PARENT_ROOT, _OWNER_SPEC["store"]["database_path"])
        repo = Path(_OWNER_SPEC["source"]["repository_root"])
        require(repo.resolve(strict=True) == repo, "source-repository-canonical")
        receive_original_producer_admission(repo, lifecycle)
        sys.path.insert(0, str(repo / "tooling"))
        phase = "OWNER_START_FAILED"
        _COHORT_MODULE = importlib.import_module("native_host_installed_shared_state")
        require(Path(_COHORT_MODULE.__file__).resolve() == repo / "tooling/native_host_installed_shared_state.py", "cohort-module-origin")
        _COHORT_MANAGER = _COHORT_MODULE.supervise_own_processes()
        _COHORT_MANAGER.__enter__()
        _COHORT_RECEIPT = lifecycle.evidence / "complete-descendant-scope.original.json"
        _COHORT_PUBLISH = _COHORT_MODULE.publish
        # Keep the reviewed helper's exact scope/descendant semantics. Route
        # only its original publication through this already assigned slice.
        def publish_cohort_original(path, value):
            require(Path(path) == _COHORT_RECEIPT, "cohort-original-path")
            new_bytes(path, (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())
        _COHORT_MODULE.publish = publish_cohort_original
        _COHORT_SCOPE = _COHORT_MODULE.owned_process_scope(_COHORT_RECEIPT,
            seconds=min(20, _OWNER_SPEC["limits"]["close_timeout_ms"] / 1000), grace=0)
        _COHORT_SCOPE.__enter__()
        phase = "RUNTIME_INVALID"
        graph = GraphBrowserOwner(repo, _OWNER_SPEC["source"]["sha"], Path(_OWNER_SPEC["store"]["database_path"]),
                                  "project:" + _OWNER_SPEC["store"]["project_id"], lifecycle)
        graph.spec_original = spec_original
        graph.database_initial_original = original_ref(lifecycle.evidence / "database-initial.original.json")
        emit({"event": "READY", "schema": "motionwright.graph-browser-owner/6", "case_id": _OWNER_SPEC["case_id"],
              "source_sha": _OWNER_SPEC["source"]["sha"], "owner_pid": os.getpid(),
              "private_root": str(lifecycle.evidence), "native_authoring_approved": False})
        deadline = time.monotonic() + max(0, (_OWNER_SPEC["limits"]["deadline_unix_ms"] - int(time.time() * 1000)) / 1000)
        for request in control_messages(sys.stdin.buffer, deadline=deadline):
            request_id = request["id"]
            if request["op"] == "new-client":
                phase = "CLIENT_START_FAILED"
                ready = graph.new_client(request_id)
                reply = {"event": "CLIENT", "id": request_id, "client_id": ready["client_id"],
                         "child_env": {"MOTIONWRIGHT_GRAPH_CONNECTION": ready["connection_file"]},
                         "connection_receipt": ready["connection_file"]}
                try:
                    emit(reply)
                except BaseException:
                    graph.client_ledger.unknown(request_id)
                    raise
            else:
                phase = "OWNER_CLOSE_FAILED"
                final = graph_close_v6(graph, service_barrier=request["service_barrier"], request_id=request_id)
                emit(final)
                return 0 if final["status"] == "CLOSED" else 1
    except BaseException as error:
        enter_cancellation_boundary()
        private = None
        try:
            if _OWNER_SUBQUOTA is not None:
                _OWNER_SUBQUOTA.closing = True
            if lifecycle is not None:
                lifecycle.taints.append("protocol-error:" + phase)
                try:
                    if graph is not None and not graph.closed:
                        graph_close_v6(graph, service_barrier={"closed": False, "receipt_path": None, "receipt_sha256": None}, request_id=request_id, reason="owner-error")
                    elif not lifecycle.closed:
                        lifecycle.close(reason="owner-error")
                except BaseException:
                    pass
                finally:
                    try:
                        if _COHORT_MODULE is not None:
                            cohort, complete = close_complete_cohort()
                            new_json(lifecycle.evidence / "error-cohort.original.json", {"cohort": cohort, "complete": complete})
                    except BaseException:
                        pass
                try:
                    private = lifecycle.evidence / "owner-error.original.json"
                    new_json(private, {"code": phase, "error_class": type(error).__name__, "original_error": str(error),
                                       "signals": list(_OBSERVED_SIGNALS), "status": "UNKNOWN", "capture_permitted": False})
                    private = str(private)
                except BaseException:
                    private = None
            emit({"event": "ERROR", **({"id": request_id} if request_id is not None else {}), "code": phase, "private_receipt": private})
            return 1
        finally:
            leave_cancellation_boundary(propagate=False)
    finally:
        if _COHORT_MODULE is not None and _COHORT_PUBLISH is not None:
            _COHORT_MODULE.publish = _COHORT_PUBLISH
        if _COHORT_MANAGER is not None:
            try:
                _COHORT_MANAGER.__exit__(None, None, None)
            except BaseException:
                pass


GraphBrowserOwner.close = graph_close_v6

if __name__ == "__main__":
    raise SystemExit(protocol_main())
