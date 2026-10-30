"""Bounded Linux owner supervision for the reviewed FixedTxt G2 composition.

No admission is issued here. An existing owner must prepare two empty cgroup-v2
leaves for the actual run. Missing/foreign containment fails before spawn. The
new explicit producer path still requires the external reviewed P/R/Q input;
the legacy catalogue remains unchanged.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time

_LAUNCH_CRITICAL = 0
_CANCEL_SIGNAL = None
_CANCEL_DELIVERED = False


def _deliver_cancellation():
    global _CANCEL_DELIVERED
    if _CANCEL_SIGNAL is not None and not _CANCEL_DELIVERED:
        _CANCEL_DELIVERED = True
        raise InterruptedError("original FixedTxt workflow cancellation " + str(_CANCEL_SIGNAL))


def _cancel(signum, _frame):
    """Like the existing Manim outer controller: retain the first launch handle."""
    global _CANCEL_SIGNAL
    if _CANCEL_SIGNAL is not None:
        return
    _CANCEL_SIGNAL = signum
    if not _LAUNCH_CRITICAL:
        _deliver_cancellation()


class SupervisionRejected(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise SupervisionRejected(code)


class OwnerCgroup:
    """An owner-created, initially empty kernel containment leaf, retained by fd."""
    def __init__(self, run_id, phase):
        need(sys.platform == "linux" and type(run_id) is str and run_id.isdecimal()
             and run_id[0] != "0" and phase in ("preparation", "work", "custody"), "fixedtxt-cgroup-context")
        mount = Path("/sys/fs/cgroup")
        # The mount table comes from the kernel, not from the external plan.
        mounted = any(line.split()[4] == str(mount) and " - cgroup2 " in line
                      for line in Path("/proc/self/mountinfo").read_text().splitlines())
        need(mounted, "fixedtxt-cgroup-v2-required")
        self.path = mount / ("motionwright-fixedtxt-" + run_id + "-1") / phase
        need(self.path.resolve(strict=True) == self.path
             and not any(path.is_symlink() for path in (self.path, *self.path.parents)),
             "fixedtxt-owner-cgroup-location")
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.identity = os.fstat(self.fd)
        self.procs = self.kill = None
        try:
            need(stat.S_ISDIR(self.identity.st_mode), "fixedtxt-owner-cgroup-directory")
            need(not any(item.is_dir(follow_symlinks=False) for item in os.scandir(self.path)),
                 "fixedtxt-owner-cgroup-no-subgroups")
            self.procs = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
            self.kill = os.open("cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
            need(not self.populated(), "fixedtxt-owner-cgroup-not-empty")
        except BaseException:
            self.close()
            raise

    def _stable(self):
        now = self.path.lstat()
        need((now.st_dev, now.st_ino) == (self.identity.st_dev, self.identity.st_ino),
             "fixedtxt-owner-cgroup-replaced")

    def populated(self):
        self._stable()
        fd = os.open("cgroup.events", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        need(len(raw) <= 4096, "fixedtxt-cgroup-events-bound")
        rows = dict(line.split() for line in raw.decode("ascii", "strict").splitlines())
        need(rows.get("populated") in ("0", "1"), "fixedtxt-cgroup-populated-state")
        return rows["populated"] == "1"

    def child_enter(self):
        # Runs only in the new child, before exec, through the retained fd.
        raw = str(os.getpid()).encode()
        if os.write(self.procs, raw) != len(raw):
            raise OSError("incomplete cgroup membership write")

    def stop(self):
        self._stable()
        # cgroup.kill also covers descendants that changed sessions/groups.
        need(os.write(self.kill, b"1") == 1, "fixedtxt-cgroup-kill-write")

    def close(self):
        primary = None
        for name in ("procs", "kill", "fd"):
            value = getattr(self, name, None)
            if value is not None:
                setattr(self, name, None)
                try:
                    os.close(value)
                except OSError as error:
                    if primary is None:
                        primary = error
        if primary is not None:
            raise primary


class AttemptClock:
    def __init__(self, limits, *, started=None, upload_seconds=0):
        need(type(limits) is dict and all(type(limits.get(key)) is int for key in
             ("attempt_seconds", "custody_seconds"))
             and 0 < limits["custody_seconds"] < limits["attempt_seconds"] <= 2**31 - 1,
             "fixedtxt-supervisor-finite-limits")
        now = time.monotonic()
        need(started is None or type(started) in (int, float)
             and math.isfinite(started) and 0 <= started <= now,
             "fixedtxt-supervisor-original-start")
        need(type(upload_seconds) is int and 0 <= upload_seconds < limits["custody_seconds"],
             "fixedtxt-supervisor-upload-inside-custody-reserve")
        self.started = now if started is None else started
        self.total = self.started + limits["attempt_seconds"]
        self.custody_end = self.total - upload_seconds
        self.work = self.total - limits["custody_seconds"]
        self.custody_seconds = limits["custody_seconds"]
        self.custody = None
        self.used = set()

    def reserve(self, phase):
        need(phase in ("work", "custody") and phase not in self.used,
             "fixedtxt-supervisor-no-retry")
        need(phase != "work" or not self.used, "fixedtxt-supervisor-work-after-custody")
        self.used.add(phase)  # even a failed spawn consumes this original stage
        if phase == "custody":
            self.custody = min(self.custody_end, time.monotonic() + self.custody_seconds)
            deadline = self.custody
        else:
            deadline = self.work
        need(time.monotonic() < deadline, "fixedtxt-supervisor-deadline-before-spawn")
        return deadline


def _run_original(argv, *, environment, directory, phase, clock, containment, stream_limit):
    """One original child; only the closed owner composition calls this primitive.

    Tests may exercise mechanical children with an explicit containment double;
    such tests are never evidence that a Linux native owner was contained.
    """
    from ci_private_evidence import new_json
    global _LAUNCH_CRITICAL
    need(threading.active_count() == 1, "fixedtxt-supervisor-single-thread-before-fork")
    need(type(stream_limit) is int and 0 < stream_limit <= 8 * 1024 * 1024,
         "fixedtxt-supervisor-stream-budget")
    directory = Path(directory)
    need(directory.is_absolute() and directory.resolve(strict=True) == directory
         and not any(path.is_symlink() for path in (directory, *directory.parents)),
         "fixedtxt-supervisor-original-root")
    deadline = clock.reserve(phase)
    # Cleanup and original terminal observation stay inside this stage's budget.
    execution_deadline = deadline - min(0.25, (deadline - time.monotonic()) / 5)
    record = {"schema": "motionwright.native.fixedtxt-supervised-stage/1", "phase": phase,
        "argv": list(argv), "automatic_retry": False, "process_started": False,
        "exit_code": None, "deadline_exceeded": False, "overflow": False,
        "native_success_inferred": False, "original_complete": False, "containment_empty": None,
        "attempt_started_monotonic": clock.started, "attempt_deadline_monotonic": clock.total,
        "stage_deadline_monotonic": deadline, "execution_deadline_monotonic": execution_deadline}
    # Exclusive intent precedes process creation; a second owner cannot reuse it.
    new_json(directory / (phase + ".intent.json"), record)
    stdout_path, stderr_path = (directory / (phase + suffix) for suffix in (".stdout", ".stderr"))
    logs = {}
    readers = selectors.DefaultSelector()
    child = None
    primary = None
    states = {name: {"observed_bytes": 0, "retained_bytes": 0,
                    "hash": hashlib.sha256(), "eof": False} for name in ("stdout", "stderr")}
    try:
        for name, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            logs[name] = os.fdopen(fd, "wb")
        need(not containment.populated(), "fixedtxt-supervisor-foreign-child-before-spawn")
        if phase == "work":
            need(_CANCEL_SIGNAL is None, "fixedtxt-supervisor-cancelled-before-work")
        _LAUNCH_CRITICAL += 1
        try:
            child = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                preexec_fn=containment.child_enter, close_fds=True)
            record.update(process_started=True, pid=child.pid)
            new_json(directory / (phase + ".spawned.json"), {"pid": child.pid, "automatic_retry": False})
        finally:
            _LAUNCH_CRITICAL -= 1
        _deliver_cancellation()
        for name in ("stdout", "stderr"):
            stream = getattr(child, name)
            os.set_blocking(stream.fileno(), False)
            readers.register(stream, selectors.EVENT_READ, name)
        stopped = False
        while readers.get_map() or child.poll() is None or containment.populated():
            now = time.monotonic()
            if now >= execution_deadline:
                record["deadline_exceeded"] = True
            if not stopped and (record["deadline_exceeded"] or record["overflow"]):
                containment.stop()
                stopped = True
            # Never wait past the whole attempt. Partial originals stay partial.
            if now >= deadline:
                break
            if child.poll() is not None and not readers.get_map() and containment.populated():
                # A nominal wrapper exit does not authorize surviving owners.
                record["descendants_survived_wrapper"] = True
                if not stopped:
                    containment.stop()
                    stopped = True
            for key, _ in readers.select(min(0.02, max(0.0, deadline - now))):
                data = os.read(key.fileobj.fileno(), 65536)
                state = states[key.data]
                if not data:
                    state["eof"] = True
                    readers.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                state["observed_bytes"] += len(data)
                state["hash"].update(data)
                available = max(0, stream_limit + 1 - state["retained_bytes"])
                kept = data[:available]
                logs[key.data].write(kept)
                state["retained_bytes"] += len(kept)
                record["overflow"] |= state["observed_bytes"] > stream_limit
        record["exit_code"] = child.poll()
        record["containment_empty"] = not containment.populated()
    except BaseException as error:
        primary = error
        # Even an interrupted/failed Popen can have created a child. Killing the
        # already retained owner leaf does not depend on receiving its PID.
        try:
            containment.stop()
        except BaseException:
            record["containment_stop_failed"] = True
        record["original_error_class"] = type(error).__name__
    finally:
        if child is not None:
            if child.poll() is None:
                try:
                    child.wait(timeout=max(0, min(0.1, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            record["exit_code"] = child.poll()
        for key in list(readers.get_map().values()):
            readers.unregister(key.fileobj)
            key.fileobj.close()
        readers.close()
        if child is not None:
            for name in ("stdout", "stderr"):
                stream = getattr(child, name)
                if stream is not None and not stream.closed:
                    stream.close()
        for stream in logs.values():
            try:
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException as error:
                record["original_sync_failed"] = True
                if primary is None:
                    primary = error
            finally:
                stream.close()
        record["streams"] = {name: {**{key: item for key, item in state.items() if key != "hash"},
                                  "observed_sha256": state["hash"].hexdigest()}
                             for name, state in states.items()}
        record["original_complete"] = (primary is None and type(record["exit_code"]) is int
            and record["containment_empty"] is True and not record.get("original_sync_failed")
            and all(state["eof"] and state["observed_bytes"] <= stream_limit for state in states.values()))
        record["native_outcome"] = ("UNKNOWN_NO_RETRY" if record["deadline_exceeded"] or record["overflow"]
            or primary is not None or record.get("descendants_survived_wrapper") else "NOT_INFERRED")
        record["terminal_observed_monotonic"] = time.monotonic()
        try:
            new_json(directory / (phase + ".terminal.json"), record)
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Original supervisor terminal publication also failed; no retry.")
    if primary is not None:
        raise primary
    return record


def supervise_owner(held, *, environment, source_root, custody_directory):
    """Closed existing wrapper then selected custody, one owner context only.

    The P/2 route is explicit and independently committed. The wrapper's legacy
    path retains its original guard. This candidate still needs A5 review.
    """
    import native_fixedtxt_g2_admission as fixed
    import native_mlt_g2_observation as g2
    current = {"source_sha": environment["GITHUB_SHA"], "semwright_sha": g2.SEMWRIGHT_PIN,
               "run_id": environment["GITHUB_RUN_ID"], "run_attempt": "1"}
    fixed.admit_inputs(held.dispatch_raw, held.plan_raw, held.runtime_raw, held.cycle_raw,
                      current=current, runner_temp=environment["RUNNER_TEMP"])
    need(environment.get("MW_MLT_G2_ADMISSION", "").encode() == held.dispatch_raw,
         "fixedtxt-supervisor-original-dispatch-changed")
    plan = fixed.strict(held.plan_raw, 512 * 1024)
    layout = fixed.owner_layout(current=current, runner_temp=environment["RUNNER_TEMP"])
    root = Path(source_root)
    need(str(root) == environment.get("GITHUB_WORKSPACE") and root.resolve(strict=True) == root,
         "fixedtxt-supervisor-exact-source-root")
    clock_members = {row["relative_path"] for row in plan["members"] if row["root"] == "control"}
    if (environment.get("MW_FIXEDTXT_WORKFLOW_ROUTE") == "original-r28-delivery/1"
            or "clock.original.json" in clock_members or "request.original.json" in clock_members):
        from native_fixedtxt_workflow import admitted_original_clock
        original, request = admitted_original_clock(plan, current=current,
                                                     runner_temp=environment["RUNNER_TEMP"])
        clock = AttemptClock(plan["limits"], started=original["started_monotonic"],
                             upload_seconds=request["upload_seconds"])
    else:
        clock = AttemptClock(plan["limits"])
    groups = []
    try:
        # Both groups must exist, be authentic and empty BEFORE native spawn.
        for phase in ("work", "custody"):
            groups.append(OwnerCgroup(current["run_id"], phase))
        work = [sys.executable, "-B", "-E", "-s", str(root / "tooling/native_film180_original_g2.py"),
                "--g2-inputs", layout["runtime"], "--fixed-text-v4-inputs", layout["cycle"],
                "--fixed-text-v4-fixture", plan["fixture"], "--fixedtxt-external-g2"]
        custody = [sys.executable, "-B", "-E", "-s", str(root / "tooling/native_fixedtxt_g2_admission.py"),
                   "--capture-selected"]
        primary = None
        first = second = None
        try:
            first = _run_original(work, environment=environment, directory=custody_directory,
                phase="work", clock=clock, containment=groups[0], stream_limit=1024 * 1024)
        except BaseException as error:
            primary = error
        # No second renderer even after failure/timeout. Custody has its own
        # finite reserved window and sees only already-existing originals.
        try:
            second = _run_original(custody, environment=environment, directory=custody_directory,
                phase="custody", clock=clock, containment=groups[1], stream_limit=1024 * 1024)
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Original FixedTxt custody stage also failed; nothing retried.")
        if primary is not None:
            raise primary
        return {"work": first, "custody": second, "native_operation_retried": False,
                "native_success_verified": False, "human_acceptance": False}
    finally:
        closing = None
        active_error = sys.exception()
        for group in groups:
            try:
                group.close()
            except BaseException as error:
                if closing is None:
                    closing = error
        if closing is not None:
            if active_error is None:
                raise closing
            active_error.add_note("Original owner cgroup handle close also failed; nothing retried.")


def require_work_containment(current, *, source_root):
    """Kernel membership and the actual parent command; no environment grant."""
    from ci_private_evidence import validate_context
    validate_context(current)
    need(current["run_attempt"] == "1" and sys.platform == "linux",
         "fixedtxt-owner-work-context")
    expected = "0::/motionwright-fixedtxt-" + current["run_id"] + "-1/work"
    need(Path("/proc/self/cgroup").read_text().splitlines() == [expected],
         "fixedtxt-owner-work-containment-required")
    raw = Path("/proc", str(os.getppid()), "cmdline").read_bytes()
    need(0 < len(raw) <= 65536, "fixedtxt-owner-supervisor-parent-command")
    argv = raw.rstrip(b"\0").split(b"\0")
    script = str(Path(source_root) / "tooling/native_fixedtxt_g2_supervisor.py").encode()
    need(argv[-2:] == [script, b"--run-one"], "fixedtxt-owner-supervisor-parent-required")


def main(argv=None):
    import native_fixedtxt_g2_admission as fixed
    need((sys.argv[1:] if argv is None else argv) == ["--run-one"],
         "fixedtxt-supervisor-closed-command")
    env = os.environ
    root = str(Path(__file__).resolve().parents[1])
    held = fixed.acquire_inputs(env.get("MW_MLT_G2_ADMISSION", "").encode(),
                                environment=env, source_root=root)
    current = fixed.current_context(env)
    target = Path(fixed.owner_layout(current=current, runner_temp=env["RUNNER_TEMP"])["control_root"]) / "supervision"
    target.mkdir(mode=0o700, exist_ok=False)
    result = supervise_owner(held, environment=env, source_root=root, custody_directory=target)
    need(all(result[phase]["exit_code"] == 0 and result[phase]["original_complete"]
             and not result[phase]["deadline_exceeded"] and not result[phase]["overflow"]
             for phase in ("work", "custody")), "fixedtxt-original-owner-incomplete")
    return result


if __name__ == "__main__":
    previous = {kind: signal.signal(kind, _cancel) for kind in (signal.SIGTERM, signal.SIGINT)}
    try:
        main()
    except Exception:
        print("FixedTxt owner incomplete; originals retained, no retry or native acceptance.", file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)
