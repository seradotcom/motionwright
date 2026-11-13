"""Observe one original Manim process, cancel it, and prove namespace closure.

This owner never treats Running as a spawn witness or Cancelled as a kernel
terminal. It neither signals Native children itself nor retries a render.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import select
import stat
import subprocess
import sys
import time

STREAM_LIMIT = 4 * 1024 * 1024
ARGV_LIMIT = 256 * 1024
EVIDENCE_NAME = "motionwright-manim-cancel.original.json"


def need(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def new_bytes(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def new_json(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    need(len(raw) <= STREAM_LIMIT, "cancel_json_budget")
    new_bytes(path, raw)
    return raw


def bounded(path, maximum):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    need(len(raw) <= maximum, "cancel_read_budget")
    return raw


def process_stat(pid):
    raw = bounded(Path("/proc") / str(pid) / "stat", 8192)
    close = raw.rfind(b")")
    need(close > 0 and int(raw[:raw.index(b" ")]) == pid, "cancel_proc_stat_identity")
    fields = raw[close + 2:].split()
    need(len(fields) >= 20, "cancel_proc_stat_shape")
    return {"pid": pid, "parent": int(fields[1]), "state": fields[0].decode("ascii"),
            "start_ticks": int(fields[19]), "raw": raw}


def namespace(pid):
    value = os.stat(Path("/proc") / str(pid) / "ns/pid")
    return (value.st_dev, value.st_ino)


def census():
    rows = {}
    with os.scandir("/proc") as entries:
        candidates = [entry.name for entry in entries if entry.name.isdigit()]
    need(len(candidates) <= 8192, "cancel_proc_census_budget")
    for name in candidates:
        pid = int(name)
        try:
            info = process_stat(pid)
            info["uid"] = os.stat(Path("/proc") / name).st_uid
            rows[pid] = info
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return rows


def descended(rows, pid, ancestor):
    seen = set()
    while pid in rows and pid not in seen and len(seen) <= 128:
        if pid == ancestor:
            return True
        seen.add(pid)
        pid = rows[pid]["parent"]
    return False


def pidfd_observation(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    events = poll.poll(0)
    if not events:
        return {"state": "LIVE", "mask": 0}
    need(len(events) == 1 and events[0][0] == fd, "cancel_pidfd_poll_identity")
    mask = events[0][1]
    need(not mask & (select.POLLERR | select.POLLNVAL), "cancel_pidfd_poll_invalid")
    need(mask & (select.POLLIN | select.POLLHUP), "cancel_pidfd_poll_unexpected")
    return {"state": "REAPED" if mask & select.POLLHUP else "EXITED", "mask": mask}


def alive(fd):
    return pidfd_observation(fd)["state"] == "LIVE"


class ExecutableNotSelected(Exception):
    """An argv forwarding monitor is not the admitted render executable."""


def pinned_process(info, expected_executable_sha, *, argv_check, selecting=False):
    pid = info["pid"]
    fd = os.pidfd_open(pid, 0)
    try:
        before = process_stat(pid)
        need(before["start_ticks"] == info["start_ticks"] and alive(fd)
             and before["state"] != "Z", "cancel_original_process_not_live")
        cmdline = bounded(Path("/proc") / str(pid) / "cmdline", ARGV_LIMIT)
        need(cmdline.endswith(b"\0"), "cancel_original_argv_terminated")
        argv = cmdline[:-1].split(b"\0")
        need(argv_check(argv), "cancel_original_argv_binding")
        hasher, total = hashlib.sha256(), 0
        # This is the kernel's executable descriptor, not a project pathname.
        with (Path("/proc") / str(pid) / "exe").open("rb") as stream:
            identity = os.fstat(stream.fileno())
            need(stat.S_ISREG(identity.st_mode), "cancel_kernel_executable_regular")
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                need(total <= 256 * 1024 * 1024, "cancel_kernel_executable_budget")
                hasher.update(chunk)
            after_file = os.fstat(stream.fileno())
            need((identity.st_dev, identity.st_ino, identity.st_size, identity.st_mtime_ns)
                 == (after_file.st_dev, after_file.st_ino, after_file.st_size, after_file.st_mtime_ns),
                 "cancel_kernel_executable_changed")
        if hasher.hexdigest() != expected_executable_sha:
            if selecting:
                raise ExecutableNotSelected()
            raise ValueError("cancel_kernel_executable_digest")
        ns = namespace(pid)
        after = process_stat(pid)
        need(after["start_ticks"] == before["start_ticks"] and alive(fd)
             and after["state"] != "Z", "cancel_process_ended_during_witness")
        return {"pid": pid, "start_ticks": before["start_ticks"], "parent": before["parent"],
                "namespace": ns, "executable_sha256": hasher.hexdigest(),
                "executable_bytes": total, "stat_raw": before["raw"], "cmdline_raw": cmdline,
                "pidfd": fd}
    except BaseException:
        os.close(fd)
        raise


def wait_original_processes(start, broker, runner_sha, python_sha, writer_raw, *, deadline, guard):
    runner, python = None, None
    source = start["source_file"].encode()
    try:
        while time.monotonic() < deadline:
            guard()
            need(broker.poll() is None, "cancel_original_broker_ended_before_witness")
            rows = census()
            owned = [row for row in rows.values()
                     if row["uid"] == os.geteuid() and row["state"] != "Z"
                     and descended(rows, row["pid"], broker.pid)]
            if runner is None:
                for row in owned:
                    try:
                        cmd = bounded(Path("/proc") / str(row["pid"]) / "cmdline", ARGV_LIMIT)
                        args = cmd.rstrip(b"\0").split(b"\0")
                        if b"--source" not in args:
                            continue
                        index = args.index(b"--source")
                        if index + 1 >= len(args) or args[index + 1] != source:
                            continue
                        # bwrap forwards the same --source argv. Select the
                        # admitted kernel executable before pinning a witness.
                        runner = pinned_process(row, runner_sha, selecting=True,
                            argv_check=lambda values: b"--source" in values
                            and values.index(b"--source") + 1 < len(values)
                            and values[values.index(b"--source") + 1] == source)
                        break
                    except (FileNotFoundError, ProcessLookupError, ExecutableNotSelected):
                        continue
            if runner is not None:
                need(alive(runner["pidfd"]), "cancel_original_runner_ended_before_control")
                for row in owned:
                    if row["pid"] == runner["pid"] or not descended(rows, row["pid"], runner["pid"]):
                        continue
                    try:
                        cmd = bounded(Path("/proc") / str(row["pid"]) / "cmdline", ARGV_LIMIT)
                        args = cmd.rstrip(b"\0").split(b"\0")
                        # The original fixed writer runs with exactly the saved source.
                        if len(args) != 10 or args[1] != b"-c" or args[2] != writer_raw:
                            continue
                        if PurePosixPath(os.fsdecode(args[3])).name != os.fsdecode(source):
                            continue
                        python = pinned_process(row, python_sha,
                            argv_check=lambda values: values == args)
                        need(python["namespace"] == runner["namespace"]
                             and python["namespace"] != namespace(os.getpid()),
                             "cancel_original_namespace_not_isolated")
                        need(alive(runner["pidfd"]) and alive(python["pidfd"]),
                             "cancel_original_process_closed_before_control")
                        return runner, python
                    except (FileNotFoundError, ProcessLookupError):
                        continue
            time.sleep(0.01)
        raise ValueError("cancel_active_native_process_not_observed")
    except BaseException:
        for original in (runner, python):
            if original is not None:
                os.close(original["pidfd"])
        raise


def parse_kernel_status(raw, pid):
    need(type(raw) is bytes and 0 < len(raw) <= 65536, "cancel_kernel_status_budget")
    wanted = {b"Pid", b"Tgid", b"PPid", b"Uid", b"NSpid", b"NStgid"}
    fields = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(b":")
        if separator and key in wanted:
            need(key not in fields, "cancel_kernel_status_duplicate")
            parts = value.split()
            need(parts and all(part.isdigit() for part in parts), "cancel_kernel_status_numeric")
            fields[key] = [int(part) for part in parts]
    need(set(fields) == wanted and fields[b"Pid"] == fields[b"Tgid"] == [pid]
         and len(fields[b"PPid"]) == 1 and len(fields[b"Uid"]) == 4,
         "cancel_kernel_status_identity")
    ids = fields[b"NSpid"]
    need(1 <= len(ids) <= 32 and ids[0] == pid and all(value > 0 for value in ids)
         and fields[b"NStgid"] == ids, "cancel_kernel_namespace_process_identity")
    return {"pid": pid, "parent": fields[b"PPid"][0], "uids": fields[b"Uid"], "nspid": ids}


def pin_namespace_init(runner, broker_pid):
    # Only the original runner or its direct parent can be selected. No scan of
    # unrelated namespaces, no permission fallback, and no namespace entry.
    ns = runner["namespace"]
    need(ns != namespace(os.getpid()) and ns != namespace(broker_pid),
         "cancel_namespace_init_not_isolated")
    runner_status_raw = bounded(Path("/proc") / str(runner["pid"]) / "status", 65536)
    runner_status = parse_kernel_status(runner_status_raw, runner["pid"])
    need(runner_status["parent"] == runner["parent"] and runner_status["uids"] == [os.geteuid()] * 4,
         "cancel_runner_parent_changed")
    pid = runner["pid"] if runner_status["nspid"][-1] == 1 else runner["parent"]
    need(pid > 1 and pid != broker_pid, "cancel_namespace_init_not_original")
    before = process_stat(pid)
    fd = os.pidfd_open(pid, 0)
    try:
        status_raw = bounded(Path("/proc") / str(pid) / "status", 65536)
        status = parse_kernel_status(status_raw, pid)
        need(status["nspid"][-1] == 1 and len(status["nspid"]) == len(runner_status["nspid"])
             and status["uids"] == [os.geteuid()] * 4
             and status["parent"] == before["parent"] and namespace(pid) == ns,
             "cancel_original_namespace_init_not_bound")
        after = process_stat(pid)
        runner_after = process_stat(runner["pid"])
        status_after = parse_kernel_status(bounded(Path("/proc") / str(pid) / "status", 65536), pid)
        runner_status_after = parse_kernel_status(
            bounded(Path("/proc") / str(runner["pid"]) / "status", 65536), runner["pid"])
        need(status_after == status and runner_status_after == runner_status
             and after["start_ticks"] == before["start_ticks"] and after["parent"] == before["parent"]
             and after["state"] != "Z" and alive(fd)
             and runner_after["start_ticks"] == runner["start_ticks"]
             and runner_after["parent"] == runner["parent"] and runner_after["state"] != "Z"
             and alive(runner["pidfd"]), "cancel_namespace_init_changed_during_pin")
        return {"pid": pid, "parent": before["parent"], "start_ticks": before["start_ticks"],
                "namespace": ns, "nspid": status["nspid"], "observed_uid": os.geteuid(),
                "same_process_as_runner": pid == runner["pid"],
                "stat_raw": before["raw"], "status_raw": status_raw,
                "runner_status_raw": runner_status_raw, "pidfd": fd,
                "stat_sha256": sha(before["raw"]), "status_sha256": sha(status_raw),
                "runner_status_sha256": sha(runner_status_raw)}
    except BaseException:
        os.close(fd)
        raise


def wait_namespace_closed(originals, namespace_init, broker, broker_identity, *, deadline, guard):
    # Linux guarantees that all processes in a PID namespace are gone before
    # its init is reaped. pidfd HUP means REAPED; IN alone only means EXITED.
    # https://man7.org/linux/man-pages/man2/pidfd_open.2.html
    # https://man7.org/linux/man-pages/man7/pid_namespaces.7.html
    # kernel/pid_namespace.c: zap_pid_ns_processes() waits for every other PID.
    ns = originals[0]["namespace"]
    need(namespace_init["namespace"] == ns and namespace_init["nspid"][-1] == 1,
         "cancel_closure_requires_original_namespace_init")
    first_shutdown = None
    shutdown_observations = 0
    polls = 0
    while time.monotonic() < deadline:
        guard()
        need(broker.poll() is None, "cancel_broker_cleanup_cannot_prove_job_closure")
        current_broker = process_stat(broker.pid)
        need(current_broker["start_ticks"] == broker_identity["start_ticks"],
             "cancel_original_broker_identity_changed")
        rendered = [{"pid": item["pid"], "start_ticks": item["start_ticks"],
                     **pidfd_observation(item["pidfd"])} for item in originals]
        init = {"pid": namespace_init["pid"], "start_ticks": namespace_init["start_ticks"],
                **pidfd_observation(namespace_init["pidfd"])}
        polls += 1
        if init["state"] == "REAPED" and all(item["state"] == "REAPED" for item in rendered):
            sample = {"observed_monotonic_ns": time.monotonic_ns(),
                      "render_pidfds": rendered, "namespace_init_pidfd": init,
                      "broker_pid": broker.pid, "broker_start_ticks": broker_identity["start_ticks"],
                      "broker_alive": True}
            shutdown_observations += 1
            if first_shutdown is not None and (
                    sample["observed_monotonic_ns"] - first_shutdown["observed_monotonic_ns"] >= 50000000):
                return {"schema": "motionwright.manim.namespace-closure/2",
                        "proof_kind": "original_namespace_init_pidfd_reaped",
                        "namespace_device": ns[0], "namespace_inode": ns[1],
                        "original_pidfds_exited": True, "original_pidfds_reaped": True,
                        "namespace_init_reaped": True, "global_process_census_used": False,
                        "shutdown_observations": shutdown_observations, "polls": polls,
                        "samples": [first_shutdown, sample],
                        "broker_alive_before_cleanup": True, "broker_pid": broker.pid,
                        "broker_start_ticks": broker_identity["start_ticks"],
                        "observed_uid": os.geteuid(), "signals_sent_by_observer": 0}
            if first_shutdown is None:
                first_shutdown = sample
        else:
            first_shutdown = None
            shutdown_observations = 0
        time.sleep(0.025)
    raise ValueError("cancel_original_namespace_closure_not_observed")


def run_original(*, source_binary, database, connection, evidence, env, custody,
                 broker, launch, runner, python, source_root, request_id,
                 child_class, bounded_timeout):
    need(sys.platform == "linux" and os.geteuid() != 0 and hasattr(os, "pidfd_open"),
         "cancel_original_requires_linux_pidfds")
    need(broker.poll() is None, "cancel_original_broker_absent")
    broker_identity = process_stat(broker.pid)
    report_path = evidence / EVIDENCE_NAME
    output, error = evidence / "native-cancel.stdout.original", evidence / "native-cancel.stderr.original"
    child = child_class("native-cancel", evidence, custody, record_native=True)
    process, originals, namespace_init, primary, completed = None, [], None, None, False
    streams = {"stdout": output, "stderr": error}
    result = None
    try:
        with output.open("xb") as stdout, error.open("xb") as stderr:
            try:
                process = child.launch([str(source_binary), "cancel-active", str(database),
                    str(connection), str(report_path), request_id], env=env,
                    stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, start_new_session=True)
                start_path = evidence / "cancel-start.original.json"
                start = None
                deadline = time.monotonic() + bounded_timeout(40)
                while time.monotonic() < deadline:
                    need(process.poll() is None, "cancel_protocol_ended_before_original_start")
                    if start_path.exists():
                        raw = bounded(start_path, STREAM_LIMIT)
                        if raw.endswith(b"\n"):
                            try:
                                start = json.loads(raw)
                            except json.JSONDecodeError:
                                pass
                            else:
                                break
                    need(output.stat().st_size <= STREAM_LIMIT and error.stat().st_size <= STREAM_LIMIT,
                         "cancel_original_stream_budget")
                    time.sleep(0.01)
                need(start is not None and start["schema"] == "motionwright.manim.active-cancel-start/1"
                     and start["request_id"] == request_id, "cancel_original_start_not_retained")
                writer_raw = bounded(source_root / "crates/driver-manim-community/src/native_color_writer.py",
                                     ARGV_LIMIT // 2)
                runner_hash = launch["runner_sha256"]
                python_hash = launch["python_sha256"]
                need(sha(bounded(runner, 256 * 1024 * 1024)) == runner_hash
                     and sha(bounded(python, 256 * 1024 * 1024)) == python_hash,
                     "cancel_original_sealed_executables_changed")
                def guard():
                    bounded_timeout(1)
                    need(output.stat().st_size <= STREAM_LIMIT and error.stat().st_size <= STREAM_LIMIT,
                         "cancel_original_stream_budget")
                originals = list(wait_original_processes(start, broker, runner_hash, python_hash,
                    writer_raw, deadline=time.monotonic() + bounded_timeout(20), guard=guard))
                namespace_init = pin_namespace_init(originals[0], broker.pid)
                new_bytes(evidence / "cancel-namespace-init.stat.original", namespace_init["stat_raw"])
                new_bytes(evidence / "cancel-namespace-init.status.original", namespace_init["status_raw"])
                new_bytes(evidence / "cancel-runner.status.original", namespace_init["runner_status_raw"])
                public_init = {key: value for key, value in namespace_init.items()
                               if key != "pidfd" and not key.endswith("_raw")}
                public_originals = []
                for label, original in zip(("runner", "python"), originals):
                    new_bytes(evidence / ("cancel-" + label + ".stat.original"), original["stat_raw"])
                    new_bytes(evidence / ("cancel-" + label + ".cmdline.original"), original["cmdline_raw"])
                    public_originals.append({key: value for key, value in original.items()
                                             if key not in ("pidfd", "stat_raw", "cmdline_raw")})
                witness = {"schema": "motionwright.manim.active-process-witness/2",
                    "job_ref": start["job_ref"], "source_file": start["source_file"],
                    "plan_sha256": start["plan_sha256"], "start_original_sha256": sha(raw),
                    "broker_pid": broker.pid, "broker_start_ticks": broker_identity["start_ticks"],
                    "manifest_sha256": launch["manifest_sha256"], "session_sha256": launch["session_sha256"],
                    "fixed_writer_sha256": sha(writer_raw), "originals": public_originals,
                    "namespace_init": public_init,
                    "observed_monotonic_ns": time.monotonic_ns(), "signals_sent": 0}
                witness_raw = new_json(evidence / "cancel-process-witness.original.json", witness)
                need(all(alive(original["pidfd"]) for original in originals)
                     and alive(namespace_init["pidfd"]) and broker.poll() is None, "cancel_original_process_ended_before_control")
                control = {"schema": "motionwright.manim.active-cancel-control/1", "action": "cancel",
                    "job_ref": start["job_ref"], "source_file": start["source_file"],
                    "plan_sha256": start["plan_sha256"], "witness_sha256": sha(witness_raw)}
                control_raw = new_json(evidence / "cancel-control.original.json", control)
                need(len(control_raw) < 4096, "cancel_control_pipe_bound")
                process.stdin.write(control_raw)
                process.stdin.flush()
                process.stdin.close()
                closure = wait_namespace_closed(originals, namespace_init, broker, broker_identity,
                    deadline=time.monotonic() + bounded_timeout(65), guard=guard)
                new_json(evidence / "cancel-process-closure.original.json", closure)
                code = child.observe_wait(bounded_timeout(20))
                completed = True
                need(code == 0, "cancel_protocol_original_failed")
                stdout.flush(); stderr.flush()
                os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
                need(output.stat().st_size <= STREAM_LIMIT and error.stat().st_size <= STREAM_LIMIT,
                     "cancel_original_stream_budget")
                protocol = json.loads(bounded(report_path, STREAM_LIMIT))
                need(protocol["schema"] == "motionwright.manim.active-cancel-original/2"
                     and protocol["cancellation_exercised"] is True and protocol["project_unchanged"] is True
                     and protocol["physical_process_closure_claimed"] is False
                     and protocol["physical_witness_sha256"] == sha(witness_raw)
                     and protocol["control_original_sha256"] == sha(control_raw)
                     and protocol["request_id"] == request_id, "cancel_original_protocol_binding")
                result = {"native_manim_cancel": "CLOSED_WITH_UNPUBLISHABLE_RESULT",
                    "job_ref": start["job_ref"], "project_id": start["project_id"],
                    "generation": start["generation"], "revision": start["revision"],
                    "scene_id": start["scene_id"], "request_id": request_id,
                    "protocol_original_sha256": sha(bounded(report_path, STREAM_LIMIT)),
                    "process_witness_sha256": sha(witness_raw),
                    "process_closure_sha256": sha(bounded(evidence / "cancel-process-closure.original.json", STREAM_LIMIT)),
                    "native_render_starts": 1, "cancel_requests": 1, "automatic_retry": False,
                    "namespace_closure_proof": "original_namespace_init_pidfd_reaped",
                    "broker_cleanup_used_as_job_closure": False, "human_acceptance": False,
                    "formal_acceptance": False}
                new_json(evidence / "cancel-verification.original.json", result)
            except BaseException as caught:
                primary = caught
            finally:
                if process is not None and process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except BaseException as caught:
                        custody.secondary("cancel-control-close", caught)
                if not completed:
                    child.stop(primary)
                for stream, label in ((stdout, "stdout"), (stderr, "stderr")):
                    try:
                        stream.flush()
                        os.fsync(stream.fileno())
                    except BaseException as caught:
                        if primary is None:
                            primary = caught
                        else:
                            custody.secondary("cancel-" + label + "-custody", caught)
    except BaseException as caught:
        if primary is None:
            primary = caught
        else:
            custody.secondary("cancel-owner-finalization", caught)
    finally:
        if not completed:
            try:
                child.stop(primary)
            except BaseException as caught:
                if primary is None:
                    primary = caught
                else:
                    custody.secondary("cancel-child-finalization", caught)
        for original in originals + ([] if namespace_init is None else [namespace_init]):
            try:
                os.close(original["pidfd"])
            except BaseException as caught:
                if primary is None:
                    primary = caught
                else:
                    custody.secondary("cancel-pidfd-close", caught)
        try:
            child.retain(streams, primary)
        except BaseException as caught:
            if primary is None:
                primary = caught
            else:
                custody.secondary("cancel-original-child-custody", caught)
    if primary is not None:
        raise primary
    return result
