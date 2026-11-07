#!/usr/bin/env python3
"""One reused cgroup for bounded Linux resume command trees.

The outer controller and this observer remain outside the limited child cgroup
so an allocation failure cannot erase its original process/resource observation.
The existing 2520-second outer deadline is never restarted. Actions executed by
the runner itself are not represented as cgroup-limited command trees.
"""
from __future__ import annotations
import argparse
import json
import os
import pwd
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import native_manim_editable_outer as outer
from native_provider_lifecycle import memory_events

LIMIT = 1536 * 1024 * 1024
RESERVE = 512 * 1024 * 1024
RECOVERY_CGROUP_MAX = 1472 * 1024 * 1024
RECOVERY_CGROUP_HIGH = 1408 * 1024 * 1024
TASKS = 256
_CANCEL = None


def cancelled(signum, _frame):
    global _CANCEL
    if _CANCEL is None:
        _CANCEL = signum


def require(value, code):
    if not value:
        raise ValueError(code)


def available_memory():
    raw = Path("/proc/meminfo").read_bytes()
    require(len(raw) <= 65536, "resume_meminfo_budget")
    matches = re.findall(rb"^MemAvailable:\s+([0-9]+) kB$", raw, re.M)
    require(len(matches) == 1, "resume_physical_memory_observation_required")
    return int(matches[0]) * 1024


def root_for(current):
    return Path("/sys/fs/cgroup") / ("motionwright-manim-resume-" + current["run_id"])


def policy_fields():
    # memory.max can be exceeded temporarily by the kernel. This new path
    # leaves operating room below the unchanged physical peak budget; it
    # never accepts an observation above that budget or resets memory.peak.
    if os.environ.get("MANIM_NATIVE_SCENARIO") == "recovery-runtime-cycle":
        return {"physical_peak_budget_bytes": LIMIT, "cgroup_max_bytes": RECOVERY_CGROUP_MAX,
                "cgroup_high_bytes": RECOVERY_CGROUP_HIGH}
    return {}


def observe(root):
    value = memory_events(root)
    require(all(type(value.get(key)) is int for key in (
        "current_bytes", "peak_bytes", "limit_bytes", "oom", "oom_kill")),
        "resume_complete_cgroup_memory_observation_required")
    policy = policy_fields()
    require(value["limit_bytes"] == policy.get("cgroup_max_bytes", LIMIT)
            and (root / "memory.swap.max").read_text().strip() == "0"
            and (root / "pids.max").read_text().strip() == str(TASKS),
            "resume_cgroup_limits_changed")
    if policy:
        high = (root / "memory.high").read_text().strip()
        require(high == str(policy["cgroup_high_bytes"]), "recovery_cgroup_high_changed")
        value["high_bytes"] = int(high)
    return value


def members(root):
    raw = (root / "cgroup.procs").read_bytes()
    require(len(raw) <= 8192, "resume_cgroup_process_roster_budget")
    values = raw.decode("ascii").splitlines()
    require(len(values) <= TASKS and all(item.isdecimal() and int(item) > 0 for item in values),
            "resume_cgroup_process_roster")
    return [int(item) for item in values]


def initialize(uid, gid):
    deadline = outer.load_deadline()
    current = deadline.context
    require(os.geteuid() == 0 and type(uid) is int and uid > 0 and type(gid) is int and gid > 0
            and os.environ.get("GITHUB_JOB") == "native-manim-resume"
            and os.environ.get("CARGO_BUILD_JOBS") == "1", "resume_root_resource_setup_only")
    require(available_memory() >= LIMIT + RESERVE, "resume_initial_physical_reserve")
    root = root_for(current)
    require(not root.exists() and not root.is_symlink(), "resume_cgroup_already_exists_no_reset")
    parent = root.parent
    controllers = (parent / "cgroup.controllers").read_text().split()
    require({"memory", "pids"} <= set(controllers), "resume_cgroup_v2_controllers_required")
    enabled = set((parent / "cgroup.subtree_control").read_text().split())
    missing = {"memory", "pids"} - enabled
    if missing:
        (parent / "cgroup.subtree_control").write_text(" ".join("+" + name for name in sorted(missing)))
    root.mkdir(mode=0o755)
    policy = policy_fields()
    (root / "memory.max").write_text(str(policy.get("cgroup_max_bytes", LIMIT)))
    if policy:
        (root / "memory.high").write_text(str(policy["cgroup_high_bytes"]))
    (root / "memory.swap.max").write_text("0")
    (root / "memory.oom.group").write_text("1")
    (root / "pids.max").write_text(str(TASKS))
    # Entry uses the narrow root helper below; writing only the destination's
    # cgroup.procs would not satisfy v2 common-ancestor delegation containment.
    # Only termination of this owned group is delegated. Cap files stay root-owned.
    for name in ("cgroup.kill",):
        os.chown(root / name, uid, gid)
        os.chmod(root / name, 0o600)
    require(members(root) == [], "resume_new_resource_group_not_empty")
    result = {"schema": "motionwright.manim.resume.resource-origin/1", "context": current,
              "scope": "controlled-command-process-trees", "cgroup": str(root), "uid": uid, "gid": gid,
              "memory_limit_bytes": LIMIT, "physical_reserve_bytes": RESERVE, "task_limit": TASKS,
              "initial_available_bytes": available_memory(), "observed": observe(root),
              "actions_node_limits_claimed": False, "outer_origin": deadline.binding(), "automatic_retry": False,
              **policy}
    destination = deadline.root / "resource-origin.json"
    outer.new_json(destination, result)
    os.chown(destination, uid, gid)
    print(json.dumps({"status": "ORIGINAL_CGROUP_LIMITS_CONFIGURED", "memory_limit_bytes": LIMIT,
                      "physical_reserve_bytes": RESERVE, "actions_node_limits_claimed": False, **policy}))


def admitted_group(deadline):
    value = json.loads(outer.original(deadline.root / "resource-origin.json"))
    root = root_for(deadline.context)
    require(value.get("schema") == "motionwright.manim.resume.resource-origin/1"
            and value.get("context") == deadline.context and value.get("outer_origin") == deadline.binding()
            and value.get("cgroup") == str(root) and value.get("uid") == os.geteuid()
            and value.get("memory_limit_bytes") == LIMIT and value.get("physical_reserve_bytes") == RESERVE
            and root.resolve(strict=True) == root and not root.is_symlink(), "resume_original_cgroup_required")
    require(all(value.get(key) == expected for key, expected in policy_fields().items()),
            "recovery_original_resource_policy_changed")
    observe(root)
    return root


def expanded_argv(label, command):
    repo = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    return ["python3", "-B", str(repo / "tooling/native_manim_resume_resources.py"),
            "execute", "--label", label, "--", *command]


def run_bounded(deadline, label, command, cwd):
    admitted_group(deadline)
    return outer.run_command(deadline, label, expanded_argv(label, command), cwd)


def classify_completion(code, reason, error, before, after, quiescent, minimum):
    if (reason is None and error is None and type(code) is int and code == 0
            and after is not None and after["peak_bytes"] > LIMIT):
        reason = "OBSERVED_PEAK_EXCEEDS_PHYSICAL_BUDGET"
    success = (type(code) is int and code == 0 and reason is None and error is None
               and after is not None and after["peak_bytes"] <= LIMIT
               and after["oom"] == before["oom"] and after["oom_kill"] == before["oom_kill"]
               and quiescent and minimum >= RESERVE)
    return ("EXITED_WITHIN_LIMITS" if success else "RESOURCE_OR_COMMAND_FAILED_NO_RETRY"), reason


def terminate_owned(child, root, grace):
    observed = child.poll() if child is not None else None
    if child is not None and observed is None:
        # The retained live handle owns this original PGID. Once it exits, all
        # remaining cleanup uses the exclusive cgroup, never the old PGID.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            observed = child.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    remaining = members(root)
    if remaining:
        (root / "cgroup.kill").write_text("1")
    if child is not None and observed is None:
        observed = child.wait(timeout=5)
    stop = time.monotonic() + 5
    while members(root) and time.monotonic() < stop:
        time.sleep(0.05)
    require(not members(root), "resume_owned_cgroup_not_quiescent")
    return observed, remaining


def enter(label, guard_pid, original_path, original_home, command):
    """Move only this root helper, then irreversibly drop to the original UID."""
    deadline = outer.load_deadline()
    value = json.loads(outer.original(deadline.root / "resource-origin.json"))
    root = root_for(deadline.context)
    uid, gid = value.get("uid"), value.get("gid")
    require(os.geteuid() == 0 and type(uid) is int and uid > 0 and type(gid) is int and gid > 0
            and os.environ.get("SUDO_UID") == str(uid) and os.environ.get("SUDO_GID") == str(gid)
            and value.get("context") == deadline.context and value.get("outer_origin") == deadline.binding()
            and value.get("cgroup") == str(root) and root.resolve(strict=True) == root
            and os.environ.get("GITHUB_JOB") == "native-manim-resume",
            "resume_privileged_self_entry_original_identity")
    require(command and Path(command[0]).is_absolute() and "\0" not in original_path
            and 0 < len(original_path) <= 32768 and Path(original_home).is_absolute(),
            "resume_privileged_entry_closed_command")
    folder = deadline.root / "resources" / label
    stop = time.monotonic() + min(5, deadline.remaining(outer.STEPS[label]))
    while not (folder / "start.json").exists() and time.monotonic() < stop:
        time.sleep(0.01)
    start = json.loads(outer.original(folder / "start.json"))
    require(start.get("schema") == "motionwright.manim.resume.resource-child-start/1"
            and start.get("context") == deadline.context and start.get("origin") == deadline.binding()
            and start.get("guard_pid") == guard_pid and start.get("label") == label
            and type(start.get("pid")) is int and start["pid"] > 0,
            "resume_privileged_entry_observed_controller")
    observe(root)
    (root / "cgroup.procs").write_text(str(os.getpid()))
    require(os.getpid() in members(root), "resume_original_cgroup_self_entry_not_observed")
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    require(os.getuid() == os.geteuid() == uid and os.getgid() == os.getegid() == gid
            and os.getpid() in members(root), "resume_nonroot_exec_identity_changed")
    outer.new_json(folder / "entered.json", {
        "schema": "motionwright.manim.resume.original-cgroup-entry/1", "context": deadline.context,
        "origin": deadline.binding(), "label": label, "guard_pid": guard_pid,
        "controller_pid": start["pid"], "pid": os.getpid(), "uid": uid, "gid": gid,
        "cgroup_self_membership_observed": True, "privileges_dropped_before_exec": True,
        "command_execution_started": False, "automatic_retry": False})
    environment = {key: item for key, item in os.environ.items() if not key.startswith("SUDO_")}
    username = pwd.getpwuid(uid).pw_name
    environment.update(PATH=original_path, HOME=original_home, USER=username, LOGNAME=username)
    os.execvpe(command[0], command, environment)


def execute(label, command):
    deadline = outer.load_deadline()
    stage = outer.STEPS[label]
    if stage == "work":
        deadline.admit_work()
    root = admitted_group(deadline)
    require(command and all(type(value) is str and value and "\0" not in value for value in command),
            "resume_controlled_command_required")
    require(not members(root), "resume_previous_command_tree_not_quiescent")
    require(available_memory() >= LIMIT + RESERVE, "resume_command_physical_preflight")
    folder = deadline.root / "resources" / label
    folder.parent.mkdir(mode=0o700, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=False)
    before = observe(root)
    state = {"schema": "motionwright.manim.resume.resource-command/1", "context": deadline.context,
             "origin": deadline.binding(), "label": label, "command": command,
             "memory_limit_bytes": LIMIT, "physical_reserve_bytes": RESERVE,
             "resource_before": before, "automatic_retry": False, **policy_fields()}
    outer.new_json(folder / "intent.json", state)
    child, code, reason, error, remaining = None, None, None, None, []
    minimum = available_memory()
    try:
        require(_CANCEL is None, "resume_cancelled_before_original_spawn")
        executable = shutil.which(command[0])
        require(executable is not None and Path(executable).is_absolute(), "resume_command_executable_missing")
        # No caller PID is accepted by root. The helper moves its own PID only,
        # records real membership, drops all root IDs, then execs the original.
        privileged = ["sudo", "-n", "-E", "/usr/bin/python3", "-B", str(Path(__file__).resolve()),
                      "enter", "--label", label, "--guard-pid", str(os.getpid()),
                      "--original-path", os.environ["PATH"], "--original-home", os.environ["HOME"],
                      "--", executable, *command[1:]]
        child = subprocess.Popen(privileged, stdin=subprocess.DEVNULL, start_new_session=True)
        outer.new_json(folder / "start.json", {
            "schema": "motionwright.manim.resume.resource-child-start/1", "context": deadline.context,
            "origin": deadline.binding(), "label": label, "guard_pid": os.getpid(),
            "pid": child.pid, "automatic_retry": False})
        while (code := child.poll()) is None:
            minimum = min(minimum, available_memory())
            if _CANCEL is not None:
                reason = "ORIGINAL_CANCELLATION"
            elif minimum < RESERVE:
                reason = "PHYSICAL_RESERVE_LOST"
            else:
                try:
                    deadline.remaining(stage)
                except outer.DeadlineExceeded:
                    reason = "ORIGINAL_OUTER_DEADLINE"
            if reason:
                code, remaining = terminate_owned(child, root, 20 if label == "native-wrapper" else 5)
                break
            time.sleep(0.1)
        if members(root):
            reason = reason or "DESCENDANTS_REMAINED_AFTER_ORIGINAL_EXIT"
            code, remaining = terminate_owned(child, root, 5)
    except BaseException as caught:
        error = {"type": type(caught).__name__, "message": str(caught)[:4096]}
        try:
            code, remaining = terminate_owned(child, root, 20 if label == "native-wrapper" else 5)
        except BaseException as cleanup:
            error["cleanup_error"] = {"type": type(cleanup).__name__, "message": str(cleanup)[:4096]}
    after = None
    entry = None
    try:
        after = observe(root)
        entry = json.loads(outer.original(folder / "entered.json"))
        require(entry.get("schema") == "motionwright.manim.resume.original-cgroup-entry/1"
                and entry.get("context") == deadline.context and entry.get("origin") == deadline.binding()
                and entry.get("guard_pid") == os.getpid() and entry.get("controller_pid") == child.pid
                and entry.get("uid") == os.geteuid() and entry.get("gid") == os.getegid()
                and entry.get("cgroup_self_membership_observed") is True
                and entry.get("privileges_dropped_before_exec") is True,
                "resume_actual_nonroot_cgroup_entry_required")
    except BaseException as observation:
        error = error or {"type": type(observation).__name__, "message": str(observation)[:4096]}
    quiescent = not members(root)
    status, reason = classify_completion(code, reason, error, before, after, quiescent, minimum)
    state.update(status=status,
                 guard_pid=os.getpid(), child_pid=child.pid if child else None, original_child_exit=code,
                 original_child_exit_known=type(code) is int, resource_after=after,
                 original_cgroup_entry=entry,
                 minimum_physical_available_bytes=minimum, reason=reason, first_error=error,
                 remaining_processes_before_owned_cleanup=remaining, cgroup_quiescent=quiescent,
                 native_result_interpreted=False)
    outer.new_json(folder / "terminal.json", state)
    if status != "EXITED_WITHIN_LIMITS":
        print(json.dumps({"status": status, "reason": reason, "original_child_exit": code,
            "physical_peak_budget_bytes": LIMIT, "observed_peak_bytes": after["peak_bytes"] if after else None,
            "native_result_interpreted": False, "automatic_retry": False}))
    return 0 if status == "EXITED_WITHIN_LIMITS" else 1


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("initialize")
    setup.add_argument("--uid", type=int, required=True)
    setup.add_argument("--gid", type=int, required=True)
    step = sub.add_parser("workflow-step")
    step.add_argument("--label", choices=tuple(outer.STEPS), required=True)
    step.add_argument("--script", type=Path, required=True)
    execution = sub.add_parser("execute")
    execution.add_argument("--label", choices=tuple(outer.STEPS), required=True)
    execution.add_argument("argv", nargs=argparse.REMAINDER)
    entry = sub.add_parser("enter")
    entry.add_argument("--label", choices=tuple(outer.STEPS), required=True)
    entry.add_argument("--guard-pid", type=int, required=True)
    entry.add_argument("--original-path", required=True)
    entry.add_argument("--original-home", required=True)
    entry.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "initialize":
        initialize(args.uid, args.gid)
        return 0
    if args.command == "workflow-step":
        signal.signal(signal.SIGTERM, outer.workflow_cancelled)
        signal.signal(signal.SIGINT, outer.workflow_cancelled)
        outer.workflow_step(outer.load_deadline(), args.label, args.script.resolve(strict=True),
                            command_runner=run_bounded)
        return 0
    argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
    if args.command == "enter":
        enter(args.label, args.guard_pid, args.original_path, args.original_home, argv)
        raise AssertionError("resume privileged entry must exec after dropping UID")
    return execute(args.label, argv)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    raise SystemExit(main())
