#!/usr/bin/env python3
"""Narrow, shared cgroup entry for the FixedTxt and Hero owner controllers.

Only this helper's own PID crosses the runner's original cgroup boundary. It
then drops all root IDs before running a closed command. The controller, the
coordinator and the existing preparation/work/custody leaves share one parent
limit. No ancestor outside that new parent is delegated to the runner.

This module grants resources, never Native authority. A terminal observation
does not turn a Native UNKNOWN into success. Actions implemented by the GitHub
runner itself are outside these controlled process trees.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

MIB = 1024 * 1024
LIMIT = 1536 * MIB
MAXIMUM = 1472 * MIB
HIGH = 1408 * MIB
RESERVE = 512 * MIB
TASKS = 512
LEAVES = ("controller", "coordinator", "preparation", "work", "custody")
WORK_LEAVES = LEAVES[1:]
OWNER_RESOURCE_POLICY = {
    "schema": "motionwright.native.owner-resource-policy/1",
    "memory_physical_limit_bytes": LIMIT, "memory_max_bytes": MAXIMUM,
    "memory_high_bytes": HIGH, "physical_reserve_bytes": RESERVE,
    "swap_max_bytes": 0, "pids_max": TASKS, "leaves": list(LEAVES),
}
MAX_JSON = 65536
MAX_ENTRIES = 128
REPOSITORY = "seradotcom/motionwright"
SHA = re.compile(r"[0-9a-f]{64}\Z")
GIT = re.compile(r"[0-9a-f]{40}\Z")
RUN = re.compile(r"[1-9][0-9]*\Z")
POLICIES = {
    "fixedtxt": {"jobs": ("fixedtxt-original-owner", "fixedtxt-session-owner"),
                 "selector": ("MW_MLT_SCOPE", "fixedtxt-v4-one-fixture"),
                 "prefix": "motionwright-fixedtxt-", "python": "mw-fixedtxt-custody-python-",
                 "modules": ("tooling/native_fixedtxt_workflow.py", "tooling/native_fixedtxt_session.py",
                             "tooling/native_fixedtxt_g2_supervisor.py")},
    "hero": {"jobs": ("hero-profiles",), "selector": ("MOTIONWRIGHT_NATIVE_SCOPE", "hero-profiles-three"),
             "prefix": "motionwright-hero-", "python": "mw-hero-custody-python-",
             "modules": ("tooling/native_profiles_workflow.py", "tooling/native_profiles_custody.py",
                         "tooling/native_fixedtxt_session.py", "tooling/native_fixedtxt_g2_supervisor.py")},
}
_CANCEL = None


class ResourceRejected(ValueError):
    pass


def need(value, reason):
    if not value:
        raise ResourceRejected(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def descriptor(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def strict(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, "owner-resource-json-bound")
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "owner-resource-json-duplicate")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ResourceRejected("owner-resource-json-constant")))


def signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def regular(path, maximum=MAX_JSON, *, owner=None):
    path = Path(path)
    need(path.is_absolute() and path.parent.resolve(strict=True) == path.parent,
         "owner-resource-file-parent")
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= maximum
         and (owner is None or before.st_uid == owner) and stat.S_IMODE(before.st_mode) & 0o022 == 0,
         "owner-resource-regular-original")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        need(signature(before) == signature(os.fstat(fd)), "owner-resource-original-open-race")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        need(len(raw) == before.st_size and signature(before) == signature(os.fstat(fd))
             == signature(path.lstat()), "owner-resource-original-read-race")
        return raw
    finally:
        os.close(fd)


def new(path, value, *, mode=0o600):
    path = Path(path)
    raw = value if type(value) is bytes else canonical(value)
    need(len(raw) <= MAX_JSON and path.parent.resolve(strict=True) == path.parent,
         "owner-resource-original-write-bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def private_directory(path, uid):
    path = Path(path)
    info = path.lstat()
    need(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode)
         and stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == uid,
         "owner-resource-private-directory")
    return path


def context(scope, env=None):
    env = os.environ if env is None else env
    need(scope in POLICIES and sys.platform == "linux", "owner-resource-linux-scope")
    policy = POLICIES[scope]
    need(all(env.get(key) == value for key, value in (("GITHUB_ACTIONS", "true"), ("CI", "true"),
        ("RUNNER_OS", "Linux"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
        ("GITHUB_REPOSITORY", REPOSITORY), ("GITHUB_RUN_ATTEMPT", "1"), policy["selector"]))
        and env.get("GITHUB_JOB") in policy["jobs"] and env.get("CARGO_BUILD_JOBS") == "1"
        and GIT.fullmatch(env.get("GITHUB_SHA", "")) and RUN.fullmatch(env.get("GITHUB_RUN_ID", "")),
        "owner-resource-closed-workflow")
    repo = Path(env["GITHUB_WORKSPACE"])
    need(repo.is_absolute() and repo.resolve(strict=True) == repo, "owner-resource-source-root")
    lock = strict(regular(repo / "SOURCE_LOCK.json"))
    sdk = lock["dependencies"]["semwright"]["revision"]
    need(type(sdk) is str and GIT.fullmatch(sdk), "owner-resource-sdk-source")
    return {"source_sha": env["GITHUB_SHA"], "semwright_sha": sdk,
            "run_id": env["GITHUB_RUN_ID"], "run_attempt": "1"}


def group_path(scope, current):
    need(scope in POLICIES and RUN.fullmatch(current["run_id"]) and current["run_attempt"] == "1",
         "owner-resource-group-context")
    return Path("/sys/fs/cgroup") / (POLICIES[scope]["prefix"] + current["run_id"] + "-1")


def base_temp(value):
    path = Path(value)
    need(path.is_absolute() and path.resolve(strict=True) == path
         and re.fullmatch(r"/home/runner/work/_temp(?:/[A-Za-z0-9_-]+)?", str(path)),
         "owner-resource-original-runner-temp")
    return path


def source_closure(scope, repo):
    paths = ("tooling/native_owner_resources.py", "SOURCE_LOCK.json",
             "tooling/source_policy.py", "tooling/requirements/mlt-g2-custody.txt", *POLICIES[scope]["modules"])
    return {name: descriptor(regular(repo / name, 4 * MIB)) for name in paths}


def original_source(scope, repo, current):
    environment = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_GRAFT_FILE": os.devnull, "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    base = ["/usr/bin/git", "--no-replace-objects", "-C", str(repo), "-c", "core.fsmonitor=false",
            "-c", "safe.directory=" + str(repo)]
    def git(*args):
        return subprocess.check_output([*base, *args], env=environment, stderr=subprocess.DEVNULL, timeout=15)
    need(git("rev-parse", "HEAD").decode().strip() == current["source_sha"],
         "owner-resource-physical-source-sha")
    closure = source_closure(scope, repo)
    for name, expected in closure.items():
        need(descriptor(git("show", current["source_sha"] + ":" + name)) == expected,
             "owner-resource-source-blob-changed")
    return closure


def clock_originals(scope, current, temp, *, uid, allow_expired=False):
    folder = (temp / ("hp" + current["run_id"] + "-1") if scope == "hero" else
              temp / ("mw-fixedtxt-g2-control-" + current["run_id"] + "-1"))
    private_directory(folder, uid)
    raw = regular(folder / "clock.original.json", owner=uid)
    request_raw = regular(folder / "request.original.json", owner=uid)
    clock, request = strict(raw), strict(request_raw)
    request_schema = "motionwright.native.hero-profiles-request/1" if scope == "hero" else "motionwright.native.fixedtxt-workflow-request/2"
    need(request.get("schema") == request_schema
         and canonical(request.get("owner_resources")) == canonical(OWNER_RESOURCE_POLICY),
         "owner-resource-explicit-versioned-policy-required")
    schema = "motionwright.native.hero-profiles-clock/1" if scope == "hero" else "motionwright.native.fixedtxt-original-clock/1"
    need(clock.get("schema") == schema and clock.get("context") == current
         and clock.get("boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
         and request.get("source_sha") == current["source_sha"]
         and request.get("semwright_sha") == current["semwright_sha"], "owner-resource-original-clock-context")
    if scope == "hero":
        need(clock.get("request") == descriptor(request_raw), "owner-resource-original-request-pin")
    else:
        need(clock.get("request_sha256") == descriptor(request_raw)["sha256"], "owner-resource-original-request-pin")
    start, end = clock.get("started_monotonic"), clock.get("deadline_monotonic")
    seconds = request.get("limits", {}).get("attempt_seconds")
    need(all(type(n) in (int, float) and math.isfinite(n) for n in (start, end))
         and type(seconds) is int and 0 < seconds <= 3900 and 0 <= start <= time.monotonic()
         and (allow_expired or time.monotonic() < end) and end - start == seconds, "owner-resource-original-clock-expired")
    return {"folder": str(folder), "clock": descriptor(raw), "request": descriptor(request_raw),
            "started_monotonic": start, "deadline_monotonic": end, "boot_id": clock["boot_id"]}, request


def available_memory():
    raw = Path("/proc/meminfo").read_bytes()
    rows = re.findall(rb"^MemAvailable:\s+([0-9]+) kB$", raw, re.M)
    need(len(raw) <= MAX_JSON and len(rows) == 1, "owner-resource-physical-memory-observation")
    return int(rows[0]) * 1024


def kernel_text(path, maximum=MAX_JSON):
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode), "owner-resource-kernel-file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), "owner-resource-kernel-open-race")
        raw = os.read(fd, maximum + 1)
        need(len(raw) <= maximum and (path.lstat().st_dev, path.lstat().st_ino) == (before.st_dev, before.st_ino),
             "owner-resource-kernel-read-race")
        return raw.decode("ascii", "strict").strip()
    finally:
        os.close(fd)


def members(path):
    rows = kernel_text(path / "cgroup.procs", 16384).splitlines()
    need(len(rows) <= TASKS and all(RUN.fullmatch(row) for row in rows), "owner-resource-member-roster")
    return [int(row) for row in rows]


def leaf_observation(path):
    names = ("memory.max", "memory.high", "memory.swap.max", "memory.oom.group", "memory.current",
             "memory.peak", "memory.events", "pids.max", "pids.current", "cgroup.events")
    raw = {name: kernel_text(path / name) for name in names}
    need(raw["memory.max"] == str(MAXIMUM) and raw["memory.high"] == str(HIGH)
         and raw["memory.swap.max"] == "0" and raw["memory.oom.group"] == "1"
         and raw["pids.max"] == str(TASKS), "owner-resource-observed-kernel-limits")
    events = {key: int(value) for key, value in (line.split() for line in raw["memory.events"].splitlines())}
    populated = dict(line.split() for line in raw["cgroup.events"].splitlines()).get("populated")
    need(populated in ("0", "1") and {"oom", "oom_kill"} <= events.keys(), "owner-resource-kernel-events")
    value = path.lstat()
    return {"device": value.st_dev, "inode": value.st_ino, "originals": raw,
            "peak_bytes": int(raw["memory.peak"]), "current_bytes": int(raw["memory.current"]),
            "pids": int(raw["pids.current"]), "events": events, "populated": populated == "1",
            "members": members(path)}


def observe_tree(root, origin):
    need(root.resolve(strict=True) == root and root.lstat().st_uid == 0,
         "owner-resource-owned-root")
    actual = sorted(entry.name for entry in os.scandir(root) if entry.is_dir(follow_symlinks=False))
    need(actual == sorted(LEAVES), "owner-resource-no-unowned-subgroups")
    rows = {name: leaf_observation(root / name) for name in LEAVES}
    parent = leaf_observation(root)
    need(parent["members"] == [], "owner-resource-parent-must-have-no-internal-processes")
    for name, row in {"parent": parent, **rows}.items():
        expected = origin["identities"][name]
        need([row["device"], row["inode"]] == expected, "owner-resource-group-identity-changed")
        path = root if name == "parent" else root / name
        need(path.lstat().st_uid == 0 and stat.S_IMODE(path.stat().st_mode) & 0o022 == 0,
             "owner-resource-group-delegation-directory")
        for cap in ("memory.max", "memory.high", "memory.swap.max", "memory.oom.group", "pids.max",
                    "cgroup.subtree_control"):
            info = (path / cap).lstat()
            need(info.st_uid == 0 and stat.S_IMODE(info.st_mode) & 0o022 == 0,
                 "owner-resource-no-delegated-cap-files")
        for delegated in (("cgroup.procs",) if name == "parent" else ("cgroup.procs", "cgroup.kill")):
            info = (path / delegated).lstat()
            need(info.st_uid == origin["uid"] and info.st_gid == origin["gid"]
                 and stat.S_IMODE(info.st_mode) == 0o600, "owner-resource-only-owned-delegation")
    return {"parent": parent, "leaves": rows, "physical_available_bytes": available_memory()}


def initialize(scope):
    current = context(scope)
    need(os.geteuid() == 0 and RUN.fullmatch(os.environ.get("SUDO_UID", ""))
         and RUN.fullmatch(os.environ.get("SUDO_GID", "")), "owner-resource-root-setup-only")
    uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    temp, repo = base_temp(os.environ["RUNNER_TEMP"]), Path(os.environ["GITHUB_WORKSPACE"])
    binding, _ = clock_originals(scope, current, temp, uid=uid)
    closure = original_source(scope, repo, current)
    need(available_memory() >= LIMIT + RESERVE, "owner-resource-initial-physical-reserve")
    root = group_path(scope, current)
    directory = temp / ("mw-owner-resources-" + scope + "-" + current["run_id"] + "-1")
    need(not root.exists() and not root.is_symlink() and not directory.exists() and not directory.is_symlink(),
         "owner-resource-existing-origin-no-reset")
    mount = root.parent
    mounted = any(line.split()[4] == str(mount) and " - cgroup2 " in line
                  for line in Path("/proc/self/mountinfo").read_text().splitlines())
    need(mounted and {"memory", "pids"} <= set(kernel_text(mount / "cgroup.controllers").split()),
         "owner-resource-cgroup2-controllers-required")
    missing = {"memory", "pids"} - set(kernel_text(mount / "cgroup.subtree_control").split())
    need(not missing, "owner-resource-global-controllers-not-enabled-no-global-mutation")
    # Root-owned origin cannot be replaced by the nonroot controller. The
    # sibling journal contains only this run's exclusive original observations.
    directory.mkdir(mode=0o750, exist_ok=False)
    os.chown(directory, 0, gid)
    os.chmod(directory, 0o750)
    journal = directory / "journal"
    journal.mkdir(mode=0o700)
    os.chown(journal, uid, gid)
    root.mkdir(mode=0o755)
    for path in (root, *(root / name for name in LEAVES)):
        if path != root:
            path.mkdir(mode=0o755)
        os.chmod(path, 0o755)
        for name, value in (("memory.max", MAXIMUM), ("memory.high", HIGH), ("memory.swap.max", 0),
                            ("memory.oom.group", 1), ("pids.max", TASKS)):
            (path / name).write_text(str(value))
        if path == root:
            (path / "cgroup.subtree_control").write_text("+memory +pids")
        # Only the new common ancestor and its leaves are delegated. No cap,
        # directory or global ancestor is made writable by the runner.
        for name in (("cgroup.procs",) if path == root else ("cgroup.procs", "cgroup.kill")):
            os.chown(path / name, uid, gid)
            os.chmod(path / name, 0o600)
        need(members(path) == [], "owner-resource-new-group-not-empty")
    identities = {name: [(root if name == "parent" else root / name).stat().st_dev,
                         (root if name == "parent" else root / name).stat().st_ino]
                  for name in ("parent", *LEAVES)}
    value = {"schema": "motionwright.native.owner-resource-origin/1", "scope": scope, "context": current,
        "job": os.environ["GITHUB_JOB"], "uid": uid, "gid": gid, "base_temp": str(temp),
        "source_root": str(repo), "source_closure": closure, "clock": binding,
        "cgroup": str(root), "identities": identities, "memory_physical_limit_bytes": LIMIT,
        "memory_max_bytes": MAXIMUM, "memory_high_bytes": HIGH, "physical_reserve_bytes": RESERVE,
        "pids_max": TASKS, "leaves": list(LEAVES), "automatic_retry": False,
        "owner_resources": OWNER_RESOURCE_POLICY,
        "Native_authority_granted": False, "actions_node_limits_claimed": False}
    value["initial"] = observe_tree(root, value)
    raw = canonical(value)
    need(len(raw) <= MAX_JSON, "owner-resource-origin-size")
    new(directory / "origin.original.json", raw, mode=0o440)
    os.chown(directory / "origin.original.json", 0, gid)
    new(journal / "origin.original.json", raw)
    os.chown(journal / "origin.original.json", uid, gid)
    # Publishing a runner command is an unprivileged action. No environment
    # variable can select a root-write target outside this new resource tree.
    drop_privileges(uid, gid)
    append_runner_environment(temp, uid, "MW_NATIVE_RESOURCE_ORIGIN=" + str(directory / "origin.original.json"))
    print("OWNER_RESOURCE_ORIGIN_CONFIGURED Native_authority_granted=false")


def admitted(scope, *, privileged=False, allow_expired=False):
    current = context(scope)
    path = Path(os.environ["MW_NATIVE_RESOURCE_ORIGIN"])
    raw = regular(path, owner=0)
    value = strict(raw)
    need(value.get("schema") == "motionwright.native.owner-resource-origin/1"
         and value.get("scope") == scope and value.get("context") == current
         and value.get("job") == os.environ["GITHUB_JOB"] and value.get("leaves") == list(LEAVES),
         "owner-resource-origin-context")
    need(canonical(value.get("owner_resources")) == canonical(OWNER_RESOURCE_POLICY),
         "owner-resource-versioned-origin-policy")
    temp = base_temp(value["base_temp"])
    expected = temp / ("mw-owner-resources-" + scope + "-" + current["run_id"] + "-1") / "origin.original.json"
    need(path == expected and path.parent.lstat().st_uid == 0 and stat.S_IMODE(path.parent.stat().st_mode) == 0o750
         and all(type(value.get(key)) is int and value[key] > 0 for key in ("uid", "gid")),
         "owner-resource-root-original-location")
    need(all(value.get(key) == expected for key, expected in (("memory_physical_limit_bytes", LIMIT),
        ("memory_max_bytes", MAXIMUM), ("memory_high_bytes", HIGH), ("physical_reserve_bytes", RESERVE),
        ("pids_max", TASKS))), "owner-resource-origin-policy")
    uid, gid = value["uid"], value["gid"]
    if privileged:
        need(os.geteuid() == 0 and os.environ.get("SUDO_UID") == str(uid)
             and os.environ.get("SUDO_GID") == str(gid), "owner-resource-privileged-original-owner")
    else:
        need(os.getuid() == os.geteuid() == uid and os.getgid() == os.getegid() == gid,
             "owner-resource-nonroot-original-owner")
    repo = Path(os.environ["GITHUB_WORKSPACE"])
    need(str(repo) == value["source_root"] and source_closure(scope, repo) == value["source_closure"],
         "owner-resource-source-closure-changed")
    binding, _ = clock_originals(scope, current, temp, uid=uid, allow_expired=allow_expired)
    need(binding == value["clock"], "owner-resource-initial-clock-changed")
    root = group_path(scope, current)
    need(str(root) == value["cgroup"], "owner-resource-root-binding")
    journal = private_directory(path.parent / "journal", uid)
    need(regular(journal / "origin.original.json", owner=uid) == raw,
         "owner-resource-publication-original-changed")
    return value, root, journal, descriptor(raw)


def selected_command(scope, action, case, origin, env):
    """No arbitrary executable, argv, PID or module can be supplied to root."""
    repo, temp = Path(origin["source_root"]), Path(origin["base_temp"])
    run = origin["context"]["run_id"]
    python = temp / (POLICIES[scope]["python"] + run + "-1") / "bin/python"
    if action == "bootstrap":
        need(case is None, "owner-resource-bootstrap-has-no-case")
        return ["/usr/bin/python3.12", "-B", "-E", "-s", str(repo / "tooling/native_owner_resources.py"),
                "bootstrap", "--scope", scope]
    if action == "source-policy":
        need(case is None, "owner-resource-source-policy-has-no-case")
        return ["/usr/bin/python3.12", "-B", "-E", "-s", str(repo / "tooling/source_policy.py")]
    if action == "release-shared":
        need(case is None and (scope == "hero" or origin["job"] == "fixedtxt-session-owner"),
             "owner-resource-closed-release-action")
        # A custody interpreter has imported extension modules from the very
        # bind mount being released. Execute a fresh system interpreter with
        # site loading disabled, so no mapped venv library can pin that mount.
        return ["/usr/bin/python3.12", "-B", "-E", "-s", "-S",
                str(repo / "tooling/native_owner_resources.py"), "release", "--scope", scope]
    if scope == "hero":
        need(case is None and action in ("prepare", "receive-and-run", "record-uploads", "release-shared", "seal-session"),
             "owner-resource-closed-hero-action")
        need(python.parent.parent.resolve(strict=True) == python.parent.parent
             and python.resolve(strict=True) == Path("/usr/bin/python3.12").resolve(strict=True),
             "owner-resource-original-custody-python")
        return [str(python), "-B", "-E", "-s", str(repo / "tooling/native_profiles_workflow.py"), action]
    commands = {"prepare": ("native_fixedtxt_workflow.py", "--prepare-originals"),
                "receive-and-run": ("native_fixedtxt_workflow.py", "--receive-and-run"),
                "seal-workflow": ("native_fixedtxt_workflow.py", "--seal-workflow-originals"),
                "open-session": ("native_fixedtxt_session.py", "--open-session"),
                "begin-case": ("native_fixedtxt_session.py", "--begin-case"),
                "close-case": ("native_fixedtxt_session.py", "--close-case"),
                "release-shared": ("native_fixedtxt_session.py", "--release-shared"),
                "seal-session": ("native_fixedtxt_session.py", "--seal-session")}
    need(action in commands and (case is None or type(case) is int and 0 <= case < 12),
         "owner-resource-closed-fixedtxt-action")
    session = origin["job"] == "fixedtxt-session-owner"
    per_case = action in ("begin-case", "prepare", "receive-and-run", "seal-workflow", "close-case")
    need((case is not None) == (session and per_case)
         and (session or action in ("prepare", "receive-and-run", "seal-workflow")),
         "owner-resource-fixedtxt-original-case-selection")
    need(python.parent.parent.resolve(strict=True) == python.parent.parent
         and python.resolve(strict=True) == Path("/usr/bin/python3.12").resolve(strict=True),
         "owner-resource-original-custody-python")
    module, option = commands[action]
    return [str(python), "-B", "-E", "-s", str(repo / "tooling" / module), option,
            *([str(case)] if action in ("begin-case", "close-case") else [])]


def action_label(action, case):
    need(type(action) is str and re.fullmatch(r"[a-z]+(?:-[a-z]+){0,3}", action)
         and (case is None or type(case) is int and 0 <= case < 12), "owner-resource-action-label")
    return action if case is None else "case-%02d-%s" % (case, action)


def action_deadline(scope, action, case, origin, env):
    current = origin["context"]
    temp = base_temp(origin["base_temp"])
    initial, initial_request = clock_originals(scope, current, temp, uid=origin["uid"])
    effective = initial["deadline_monotonic"]
    binding = initial
    request = initial_request
    if scope == "fixedtxt" and origin["job"] == "fixedtxt-session-owner" and action != "open-session":
        session_root = temp / ("mw-fixedtxt-session-" + current["run_id"] + "-1")
        if action not in ("source-policy", "bootstrap"):
            need(env.get("MW_FIXEDTXT_SESSION_ROOT") == str(session_root), "owner-resource-session-root")
            session = strict(regular(session_root / "session-clock.original.json", owner=origin["uid"]))
            need(session.get("schema") == "motionwright.native.fixedtxt-session-clock/1"
                 and session.get("context") == current and session.get("boot_id") == initial["boot_id"]
                 and session.get("started_monotonic") == initial["started_monotonic"]
                 and session.get("deadline_monotonic") == initial["deadline_monotonic"],
                 "owner-resource-session-original-clock")
        if action in ("prepare", "receive-and-run", "seal-workflow", "close-case"):
            case_temp = Path(env["RUNNER_TEMP"])
            need(case_temp.parent == temp and re.fullmatch(r"fx[0-9a-f]{20}", case_temp.name)
                 and case_temp.resolve(strict=True) == case_temp, "owner-resource-session-case-namespace")
            binding, request = clock_originals(scope, current, case_temp, uid=origin["uid"])
            plan = strict(regular(session_root / "session-plan.original.json", owner=origin["uid"]))
            need(type(case) is int and 0 <= case < 12 and type(plan.get("cases")) is list
                 and len(plan["cases"]) == 12 and plan["context"] == current
                 and plan["cases"][case]["index"] == case
                 and plan["cases"][case]["namespace"] == str(case_temp)
                 and plan["cases"][case]["request"] == binding["request"],
                 "owner-resource-case-request-namespace-binding")
            effective = min(effective, binding["deadline_monotonic"])
        else:
            need(Path(env["RUNNER_TEMP"]) == temp, "owner-resource-session-global-namespace")
    else:
        need(Path(env["RUNNER_TEMP"]) == temp, "owner-resource-current-original-namespace")
    if action in ("source-policy", "bootstrap", "prepare"):
        seconds = request["limits"]["preparation_seconds"] if scope == "hero" else request["preparation_seconds"]
        need(type(seconds) is int and 0 < seconds < 3900, "owner-resource-preparation-budget")
        effective = min(effective, binding["started_monotonic"] + seconds)
        if scope == "fixedtxt":
            effective = min(effective, min(initial["deadline_monotonic"], binding["deadline_monotonic"])
                            - request["native_seconds"] - request["limits"]["custody_seconds"])
    need(time.monotonic() + 2 < effective, "owner-resource-original-action-deadline")
    return effective, {"initial": initial, "selected": binding, "effective_deadline_monotonic": effective}


def drop_privileges(uid, gid):
    need(os.geteuid() == 0 and type(uid) is int and uid > 0 and type(gid) is int and gid > 0,
         "owner-resource-root-identity-drop")
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    need(os.getresuid() == (uid, uid, uid) and os.getresgid() == (gid, gid, gid) and os.getgroups() == [],
         "owner-resource-irreversible-identity-drop")


def clean_sudo_environment(origin, original_path, original_home):
    import pwd
    need(type(original_path) is str and 0 < len(original_path) <= 32768 and "\0" not in original_path
         and "\n" not in original_path and Path(original_home).is_absolute()
         and original_home == pwd.getpwuid(origin["uid"]).pw_dir, "owner-resource-original-user-environment")
    result = {key: value for key, value in os.environ.items() if not key.startswith("SUDO_")}
    result.update(PATH=original_path, HOME=original_home, USER=pwd.getpwuid(origin["uid"]).pw_name,
                  LOGNAME=pwd.getpwuid(origin["uid"]).pw_name, PYTHONDONTWRITEBYTECODE="1")
    return result


PREFIX_FILES = ("intent.original.json", "controller-entry.original.json",
                "spawned.original.json", "coordinator-entry.original.json")
CLOSED_FILES = (*PREFIX_FILES, "terminal.original.json", "returned.original.json")


def validate_kernel_snapshot(value, origin):
    """Join the retained kernel text, parsed counters and original cgroup IDs."""
    need(type(value) is dict and set(value.get("leaves", {})) == set(LEAVES)
         and type(value.get("physical_available_bytes")) is int
         and value["physical_available_bytes"] >= RESERVE
         and set(origin.get("identities", {})) == {"parent", *LEAVES},
         "owner-resource-readback-kernel-roster")
    for name, row in {"parent": value.get("parent", {}), **value["leaves"]}.items():
        identity = origin["identities"][name]
        raw = row.get("originals", {})
        need(type(identity) is list and len(identity) == 2
             and all(type(n) is int and n >= 0 for n in identity)
             and all(type(row.get(key)) is int for key in ("device", "inode"))
             and [row["device"], row["inode"]] == identity
             and all(raw.get(key) == expected for key, expected in (("memory.max", str(MAXIMUM)),
                 ("memory.high", str(HIGH)), ("memory.swap.max", "0"), ("memory.oom.group", "1"),
                 ("pids.max", str(TASKS)))), "owner-resource-readback-kernel-caps-and-identity")
        need(all(type(row.get(key)) is int and 0 <= row[key] <= LIMIT for key in ("peak_bytes", "current_bytes"))
             and type(row.get("pids")) is int and 0 <= row["pids"] <= TASKS
             and raw.get("memory.peak") == str(row["peak_bytes"])
             and raw.get("memory.current") == str(row["current_bytes"])
             and raw.get("pids.current") == str(row["pids"])
             and type(row.get("populated")) is bool and type(row.get("members")) is list
             and len(row["members"]) <= TASKS and all(type(n) is int and n > 0 for n in row["members"]),
             "owner-resource-readback-kernel-counter-binding")
        events = dict(line.split() for line in raw.get("memory.events", "").splitlines())
        population = dict(line.split() for line in raw.get("cgroup.events", "").splitlines())
        need({"oom", "oom_kill"} <= events.keys()
             and all(re.fullmatch(r"[0-9]+", n) for n in events.values())
             and canonical(row.get("events")) == canonical({key: int(n) for key, n in events.items()})
             and population.get("populated") == str(int(row["populated"])),
             "owner-resource-readback-kernel-event-binding")


def validate_resource_originals(originals, *, scope, current, action, case=None, completed=False, helper_sha256):
    """Portable validation of selected original bytes, including Windows readers.

    A live preparation capsule carries only PREFIX_FILES. It cannot claim its
    guard's future terminal or return observation. The session custody retains
    those later originals with completed=True. No Native result is inferred.
    """
    need(scope in POLICIES and type(completed) is bool and type(originals) is dict
         and type(helper_sha256) is str and SHA.fullmatch(helper_sha256), "owner-resource-readback-parameters")
    label = action_label(action, case)
    names = CLOSED_FILES if completed else PREFIX_FILES
    expected_names = {"origin.original.json", *("commands/" + label + "/" + name for name in names)}
    need(set(originals) == expected_names and all(type(raw) is bytes for raw in originals.values()),
         "owner-resource-readback-exact-roster")
    origin_raw = originals["origin.original.json"]
    origin = strict(origin_raw)
    original_digest = descriptor(origin_raw)
    need(origin.get("schema") == "motionwright.native.owner-resource-origin/1"
         and origin.get("scope") == scope and origin.get("context") == current
         and origin.get("job") in POLICIES[scope]["jobs"]
         and canonical(origin.get("owner_resources")) == canonical(OWNER_RESOURCE_POLICY)
         and origin.get("leaves") == list(LEAVES)
         and all(type(origin.get(key)) is int and origin[key] > 0 for key in ("uid", "gid"))
         and origin.get("source_closure", {}).get("tooling/native_owner_resources.py", {}).get("sha256") == helper_sha256
         and origin.get("automatic_retry") is False and origin.get("Native_authority_granted") is False,
         "owner-resource-readback-original-policy-source-context")
    need(type(current) is dict and set(current) == {"source_sha", "semwright_sha", "run_id", "run_attempt"}
         and all(type(current[k]) is str and GIT.fullmatch(current[k]) for k in ("source_sha", "semwright_sha"))
         and type(current["run_id"]) is str and RUN.fullmatch(current["run_id"])
         and current["run_attempt"] == "1", "owner-resource-readback-context-shape")
    group = "/sys/fs/cgroup/" + POLICIES[scope]["prefix"] + current["run_id"] + "-1"
    need(origin.get("cgroup") == group and PurePosixPath(origin["source_root"]).is_absolute()
         and all(origin.get(key) == expected for key, expected in (("memory_physical_limit_bytes", LIMIT),
             ("memory_max_bytes", MAXIMUM), ("memory_high_bytes", HIGH), ("physical_reserve_bytes", RESERVE),
             ("pids_max", TASKS))), "owner-resource-readback-closed-resource-roots")
    rows = {name: strict(originals["commands/" + label + "/" + name]) for name in names}
    intent, controller, spawn, entry = (rows[name] for name in PREFIX_FILES)
    need(intent.get("schema") == "motionwright.native.owner-resource-command/1"
         and intent.get("context") == current and intent.get("origin") == original_digest
         and intent.get("scope") == scope and intent.get("action") == action and intent.get("case_index") == case
         and intent.get("Native_outcome") == "NOT_INFERRED" and intent.get("automatic_retry") is False,
         "owner-resource-readback-original-intent")
    need(all(row.get("context") == current and row.get("origin") == original_digest for row in (controller, spawn, entry))
         and type(spawn.get("pid")) is int and spawn["pid"] > 0 and entry.get("pid") == spawn["pid"]
         and type(intent.get("controller_pid")) is int and intent["controller_pid"] > 0
         and controller.get("pid") == entry.get("parent_pid") == intent["controller_pid"]
         and entry.get("argv") == intent.get("argv") and spawn.get("automatic_retry") is False
         and entry.get("Native_authority_granted") is False and entry.get("automatic_retry") is False,
         "owner-resource-readback-original-process-join")
    for row, leaf in ((controller, "controller"), (entry, "coordinator")):
        need(row.get("uid") == [origin["uid"]] * 3 and row.get("gid") == [origin["gid"]] * 3
             and row.get("membership") == ["0::" + group.removeprefix("/sys/fs/cgroup") + "/" + leaf],
             "owner-resource-readback-real-membership-and-dropped-ids")
    need(controller.get("groups") == [] and controller.get("privileges_dropped_before_exec") is True,
         "owner-resource-readback-no-saved-root-ids")
    before = intent.get("before", {})
    validate_kernel_snapshot(before, origin)
    need(before.get("leaves", {}).get("controller", {}).get("members") == [controller["pid"]]
         and all(before.get("leaves", {}).get(name, {}).get("populated") is False for name in WORK_LEAVES)
         and before.get("parent", {}).get("members") == [], "owner-resource-readback-original-exclusive-tree")
    result = {"schema": "motionwright.native.owner-resource-readback/1", "context": current,
        "scope": scope, "action": action, "case_index": case, "origin": original_digest,
        "owner_resources": OWNER_RESOURCE_POLICY, "guard_completion_observed": completed,
        "Native_outcome": "NOT_INFERRED", "automatic_retry": False,
        "originals": {name: descriptor(raw) for name, raw in sorted(originals.items())}}
    if not completed:
        return result
    terminal, returned = rows["terminal.original.json"], rows["returned.original.json"]
    for row, schema in ((terminal, "motionwright.native.owner-resource-terminal/1"),
                        (returned, "motionwright.native.owner-resource-returned/1")):
        need(row.get("schema") == schema and row.get("context") == current and row.get("origin") == original_digest
             and row.get("action") == action and row.get("case_index") == case
             and row.get("Native_outcome") == "NOT_INFERRED" and row.get("automatic_retry") is False,
             "owner-resource-readback-closed-original-context")
    need(terminal.get("original_complete") is True and type(terminal.get("exit_code")) is int
         and terminal["exit_code"] == 0 and terminal.get("reason") is None and terminal.get("first_error_type") is None
         and terminal.get("secondary_error_types") == [] and terminal.get("killed_owned_leaves") == []
         and terminal.get("surviving_owned_leaves") == [] and terminal.get("controller_pid") == controller["pid"]
         and terminal.get("child_pid") == spawn["pid"]
         and type(terminal.get("minimum_physical_available_bytes")) is int
         and terminal["minimum_physical_available_bytes"] >= RESERVE
         and returned.get("all_owned_groups_empty") is True,
         "owner-resource-readback-closed-original-terminal")
    after, final = terminal.get("after", {}), returned.get("observed", {})
    validate_kernel_snapshot(after, origin)
    validate_kernel_snapshot(final, origin)
    need(all(final.get("leaves", {}).get(name, {}).get("populated") is False for name in LEAVES)
         and all(final["leaves"][name].get("members") == [] for name in LEAVES)
         and after.get("leaves", {}).get("controller", {}).get("members") == [controller["pid"]]
         and all(after.get("leaves", {}).get(name, {}).get("populated") is False for name in WORK_LEAVES),
         "owner-resource-readback-final-real-quiescence")
    peaks = [row.get("parent", {}).get("peak_bytes") for row in (before, after, final)]
    need(all(type(n) is int and 0 <= n <= LIMIT for n in peaks) and peaks == sorted(peaks)
         and all(before["parent"]["events"].get(key) == after["parent"]["events"].get(key)
                 == final["parent"]["events"].get(key) for key in ("oom", "oom_kill")),
         "owner-resource-readback-aggregate-peak-or-oom")
    streams = terminal.get("streams", {})
    need(set(streams) == {"stdout", "stderr"} and all(row.get("eof") is True
        and type(row.get("observed_bytes")) is int and type(row.get("retained_bytes")) is int
        and 0 <= row["observed_bytes"] == row["retained_bytes"] <= intent["stream_bytes_each"]
        and type(row.get("sha256")) is str and SHA.fullmatch(row["sha256"]) for row in streams.values()),
        "owner-resource-readback-closed-streams")
    return result


def resource_originals(scope, action, case=None, *, completed=False):
    """Select a genuine prefix or closed observation without reading live logs."""
    origin, _, journal, _ = admitted(scope)
    label = action_label(action, case)
    folder = private_directory(journal / label, origin["uid"])
    originals = {"origin.original.json": regular(Path(os.environ["MW_NATIVE_RESOURCE_ORIGIN"]), owner=0)}
    for name in CLOSED_FILES if completed else PREFIX_FILES:
        originals["commands/" + label + "/" + name] = regular(folder / name, owner=origin["uid"])
    validate_resource_originals(originals, scope=scope, current=origin["context"], action=action, case=case,
        completed=completed, helper_sha256=origin["source_closure"]["tooling/native_owner_resources.py"]["sha256"])
    return originals


def journal_selection(active_action, case=None):
    """Final custody includes prior terminals and logs, only the active prefix."""
    active = action_label(active_action, case)
    def select(parts):
        if not parts:
            return True
        if parts[0] == "origin.original.json":
            return len(parts) == 1
        if parts[0] == active:
            return len(parts) == 1 or len(parts) == 2 and parts[1] in PREFIX_FILES
        return len(parts) <= 2
    return select


def append_runner_environment(temp, uid, assignment):
    path = Path(os.environ["GITHUB_ENV"])
    need(os.getresuid() == (uid, uid, uid) and uid > 0
         and path.parent == temp / "_runner_file_commands"
         and re.fullmatch(r"set_env_[A-Za-z0-9-]+", path.name)
         and "\n" not in assignment and "\r" not in assignment,
         "owner-resource-unprivileged-runner-command-only")
    regular(path, 1024 * 1024, owner=uid)
    before = path.lstat()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "ab") as stream:
        need(signature(os.fstat(stream.fileno())) == signature(before), "owner-resource-runner-command-open-race")
        stream.write((assignment + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


class OwnedLeaf:
    """Retain only an existing leaf of the original root; optionally observe live."""
    def __init__(self, scope, current, phase, *, initially_empty=True):
        need(phase in LEAVES and type(initially_empty) is bool, "owner-resource-leaf-selection")
        self.path = group_path(scope, current) / phase
        need(self.path.resolve(strict=True) == self.path, "owner-resource-leaf-real-path")
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.identity = os.fstat(self.fd)
        self.procs = self.kill = None
        try:
            need(stat.S_ISDIR(self.identity.st_mode)
                 and not any(entry.is_dir(follow_symlinks=False) for entry in os.scandir(self.path)),
                 "owner-resource-leaf-no-subgroups")
            self.procs = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
            self.kill = os.open("cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
            need(not initially_empty or not self.populated(), "owner-resource-leaf-not-empty")
        except BaseException:
            self.close()
            raise

    def _stable(self):
        now = self.path.lstat()
        need((now.st_dev, now.st_ino) == (self.identity.st_dev, self.identity.st_ino),
             "owner-resource-leaf-replaced")

    def populated(self):
        self._stable()
        rows = dict(line.split() for line in kernel_text(self.path / "cgroup.events", 4096).splitlines())
        need(rows.get("populated") in ("0", "1"), "owner-resource-leaf-populated-state")
        return rows["populated"] == "1"

    def child_enter(self):
        # Prepared in the single-threaded parent. No imports, locks or paths
        # are consulted after fork. Only the new child's own PID is written.
        raw = str(os.getpid()).encode()
        if os.write(self.procs, raw) != len(raw):
            raise OSError("incomplete original cgroup membership write")

    def stop(self):
        self._stable()
        need(os.write(self.kill, b"1") == 1, "owner-resource-cgroup-kill-write")

    def close(self):
        first = None
        for name in ("procs", "kill", "fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                setattr(self, name, None)
                try:
                    os.close(fd)
                except OSError as error:
                    first = first or error
        if first is not None:
            raise first


def controlled_environment(origin, environment):
    result = dict(environment)
    # Installed module/runtime helpers still perform their existing exact
    # selectors. This addition carries no token or Native admission.
    need(type(environment.get("MW_NATIVE_RESOURCE_ORIGIN")) is str
         and Path(environment["MW_NATIVE_RESOURCE_ORIGIN"]).is_absolute(), "owner-resource-validated-origin-environment")
    result["MW_NATIVE_RESOURCE_ORIGIN"] = environment["MW_NATIVE_RESOURCE_ORIGIN"]
    result["MW_NATIVE_RESOURCE_SCOPE"] = origin["scope"]
    return result


def terminate_leaves(origin, root, *, child=None):
    errors, killed = [], []
    if child is not None and child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=0.2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        except BaseException as error:
            errors.append(type(error).__name__)
    for name in WORK_LEAVES:
        try:
            leaf = OwnedLeaf(origin["scope"], origin["context"], name, initially_empty=False)
            try:
                if leaf.populated():
                    leaf.stop()
                    killed.append(name)
            finally:
                leaf.close()
        except BaseException as error:
            errors.append(type(error).__name__)
    end = time.monotonic() + 1.5
    while any(members(root / name) for name in WORK_LEAVES) and time.monotonic() < end:
        time.sleep(0.02)
    return killed, errors


def command_guard(origin, root, journal, original_digest, action, case, environment, deadline, binding):
    """A small controller inside the aggregate limit; one closed child only."""
    label = action_label(action, case)
    folder = journal / label
    need(len(list(journal.iterdir())) < MAX_ENTRIES, "owner-resource-journal-entry-bound")
    folder.mkdir(mode=0o700, exist_ok=False)
    argv = selected_command(origin["scope"], action, case, origin, environment)
    before = observe_tree(root, origin)
    need(before["leaves"]["controller"]["members"] == [os.getpid()]
         and all(not before["leaves"][name]["populated"] for name in WORK_LEAVES),
         "owner-resource-controller-exclusive-before-spawn")
    stream_limit = 4 * MIB if action == "bootstrap" else 65536
    initial = {"schema": "motionwright.native.owner-resource-command/1", "context": origin["context"],
        "scope": origin["scope"], "origin": original_digest, "action": action, "case_index": case,
        "argv": argv, "controller_pid": os.getpid(), "deadline": binding, "before": before,
        "stream_bytes_each": stream_limit, "automatic_retry": False, "Native_outcome": "NOT_INFERRED"}
    if action == "release-shared":
        need(Path.cwd() == Path(origin["base_temp"]), "owner-resource-release-controller-cwd")
        initial["controller_cwd"] = str(Path.cwd())
    new(folder / "intent.original.json", initial)
    new(folder / "controller-entry.original.json", {"context": origin["context"], "origin": original_digest,
        "pid": os.getpid(), "uid": os.getresuid(), "gid": os.getresgid(), "groups": os.getgroups(),
        "membership": Path("/proc/self/cgroup").read_text().splitlines(), "privileges_dropped_before_exec": True})
    child, first, code, after = None, None, None, None
    secondary, killed = [], []
    selector = selectors.DefaultSelector()
    streams, states, handles = {}, {}, []
    minimum = before["physical_available_bytes"]
    reason, surviving = None, []
    execution_end = deadline - 2
    try:
        for name in ("stdout", "stderr"):
            fd = os.open(folder / (name + ".original"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            streams[name] = os.fdopen(fd, "wb")
            states[name] = {"observed_bytes": 0, "retained_bytes": 0, "hash": hashlib.sha256(), "eof": False}
        coordinator = OwnedLeaf(origin["scope"], origin["context"], "coordinator")
        handles.append(coordinator)
        need(_CANCEL is None and time.monotonic() < execution_end, "owner-resource-cancelled-before-child")
        command = ["/usr/bin/python3.12", "-B", "-E", "-s", str(Path(__file__).resolve()), "child",
                   "--scope", origin["scope"], "--action", action,
                   *([] if case is None else ["--case-index", str(case)])]
        child = subprocess.Popen(command, env=controlled_environment(origin, environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, preexec_fn=coordinator.child_enter)
        new(folder / "spawned.original.json", {"pid": child.pid, "context": origin["context"], "origin": original_digest,
                                               "automatic_retry": False})
        for name in ("stdout", "stderr"):
            stream = getattr(child, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        stop_sent = False
        next_resource = time.monotonic()
        while selector.get_map() or child.poll() is None or any(members(root / name) for name in WORK_LEAVES):
            now = time.monotonic()
            if _CANCEL is not None:
                reason = reason or "CANCELLED"
            if now >= execution_end:
                reason = reason or "ORIGINAL_DEADLINE"
            if now >= next_resource:
                observed = observe_tree(root, origin)
                minimum = min(minimum, observed["physical_available_bytes"])
                if minimum < RESERVE:
                    reason = reason or "PHYSICAL_RESERVE"
                if observed["parent"]["peak_bytes"] > LIMIT or any(
                    observed["parent"]["events"][key] != before["parent"]["events"][key]
                    for key in ("oom", "oom_kill")):
                    reason = reason or "PHYSICAL_PEAK_OR_OOM"
                next_resource = now + 0.1
            if child.poll() is not None and any(members(root / name) for name in WORK_LEAVES):
                reason = reason or "DESCENDANTS_SURVIVED_COORDINATOR"
            if reason is not None and not stop_sent:
                stop_sent = True
                names, errors = terminate_leaves(origin, root, child=child)
                killed.extend(names)
                secondary.extend(errors)
            for key, _ in selector.select(timeout=0.05):
                name, stream = key.data, key.fileobj
                data = os.read(stream.fileno(), 65536)
                if not data:
                    states[name]["eof"] = True
                    selector.unregister(stream)
                    stream.close()
                    continue
                state = states[name]
                state["observed_bytes"] += len(data)
                remaining = max(0, stream_limit - state["retained_bytes"])
                retained = data[:remaining]
                if retained:
                    streams[name].write(retained)
                    state["hash"].update(retained)
                    state["retained_bytes"] += len(retained)
                if state["observed_bytes"] > stream_limit:
                    reason = reason or "STREAM_LIMIT"
            if time.monotonic() >= deadline:
                break
        code = child.poll()
    except BaseException as error:
        first = error
    finally:
        if child is not None:
            try:
                remaining = [name for name in WORK_LEAVES if members(root / name)]
                if child.poll() is None or remaining:
                    names, errors = terminate_leaves(origin, root, child=child)
                    killed.extend(names)
                    secondary.extend(errors)
                code = child.wait(timeout=0.5)
                surviving = [name for name in WORK_LEAVES if members(root / name)]
            except BaseException as error:
                first = first or error
                secondary.append(type(error).__name__)
        for key in list(selector.get_map().values()):
            try:
                selector.unregister(key.fileobj)
                key.fileobj.close()
            except BaseException as error:
                first = first or error
                secondary.append(type(error).__name__)
        selector.close()
        if child is not None:
            for stream in (child.stdout, child.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        for name, stream in streams.items():
            try:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
            except BaseException as error:
                first = first or error
                secondary.append(type(error).__name__)
        for handle in handles:
            try:
                handle.close()
            except BaseException as error:
                first = first or error
                secondary.append(type(error).__name__)
        try:
            after = observe_tree(root, origin)
        except BaseException as error:
            first = first or error
            secondary.append(type(error).__name__)
        terminal = {"schema": "motionwright.native.owner-resource-terminal/1", "context": origin["context"],
            "scope": origin["scope"], "origin": original_digest, "action": action, "case_index": case,
            "controller_pid": os.getpid(), "child_pid": None if child is None else child.pid,
            "exit_code": code, "reason": reason, "first_error_type": None if first is None else type(first).__name__,
            "secondary_error_types": secondary, "killed_owned_leaves": sorted(set(killed)),
            "surviving_owned_leaves": surviving, "after": after, "minimum_physical_available_bytes": minimum,
            "streams": {name: {**{key: value for key, value in row.items() if key != "hash"},
                               "sha256": row["hash"].hexdigest()} for name, row in states.items()},
            "original_complete": first is None and reason is None and code == 0 and not surviving and after is not None
                and after["parent"]["peak_bytes"] <= LIMIT and minimum >= RESERVE
                and all(row["eof"] for row in states.values())
                and all(after["parent"]["events"][key] == before["parent"]["events"][key] for key in ("oom", "oom_kill")),
            "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
        try:
            new(folder / "terminal.original.json", terminal)
        except BaseException as error:
            first = first or error
    if first is not None:
        raise first
    need(terminal["original_complete"], "owner-resource-original-command-incomplete")
    print("OWNER_RESOURCE_COMMAND_COMPLETE action=" + label + " Native_outcome=NOT_INFERRED")


def enter(scope, action, case, original_path, original_home):
    origin, root, journal, original_digest = admitted(scope, privileged=True)
    environment = clean_sudo_environment(origin, original_path, original_home)
    selected_command(scope, action, case, origin, environment)
    deadline, binding = action_deadline(scope, action, case, origin, environment)
    before = observe_tree(root, origin)
    need(all(not row["populated"] for row in before["leaves"].values())
         and before["physical_available_bytes"] >= LIMIT + RESERVE,
         "owner-resource-new-controller-requires-empty-owned-tree")
    if action not in ("seal-workflow", "seal-session", "record-uploads", "release-shared"):
        need(before["parent"]["peak_bytes"] <= LIMIT
             and all(before["parent"]["events"][key] == origin["initial"]["parent"]["events"][key]
                     for key in ("oom", "oom_kill")), "owner-resource-prior-resource-failure-no-Native-continuation")
    # No PID argument exists. Only the privileged helper itself crosses from
    # the runner's cgroup into this exclusive controller leaf.
    (root / "controller/cgroup.procs").write_text(str(os.getpid()))
    need(members(root / "controller") == [os.getpid()], "owner-resource-privileged-self-entry-not-observed")
    drop_privileges(origin["uid"], origin["gid"])
    need(Path("/proc/self/cgroup").read_text().splitlines() == ["0::" + str(root / "controller").removeprefix("/sys/fs/cgroup")],
         "owner-resource-original-controller-membership")
    prepared_environment = controlled_environment(origin, environment)
    os.environ.clear()
    os.environ.update(prepared_environment)
    if action == "release-shared":
        os.chdir(base_temp(origin["base_temp"]))
    command_guard(origin, root, journal, original_digest, action, case, os.environ, deadline, binding)


def child(scope, action, case):
    origin, root, journal, original_digest = admitted(scope)
    label = action_label(action, case)
    folder = private_directory(journal / label, origin["uid"])
    need(Path("/proc/self/cgroup").read_text().splitlines()
         == ["0::" + str(root / "coordinator").removeprefix("/sys/fs/cgroup")]
         and os.getpid() in members(root / "coordinator"), "owner-resource-original-coordinator-membership")
    intent = strict(regular(folder / "intent.original.json", owner=origin["uid"]))
    need(intent["context"] == origin["context"] and intent["origin"] == original_digest
         and intent["action"] == action and intent["case_index"] == case,
         "owner-resource-original-coordinator-intent")
    argv = selected_command(scope, action, case, origin, os.environ)
    need(intent["argv"] == argv, "owner-resource-original-coordinator-command")
    new(folder / "coordinator-entry.original.json", {"context": origin["context"], "origin": original_digest,
        "pid": os.getpid(), "parent_pid": os.getppid(), "uid": os.getresuid(), "gid": os.getresgid(),
        "membership": Path("/proc/self/cgroup").read_text().splitlines(), "argv": argv,
        "Native_authority_granted": False, "automatic_retry": False})
    os.execve(argv[0], argv, dict(os.environ))


def release_process_observation(origin):
    """Observe only this coordinator; the closed shell/controller start outside."""
    temp, repo = Path(origin["base_temp"]), Path(origin["source_root"])
    venv = temp / (POLICIES[origin["scope"]]["python"] + origin["context"]["run_id"] + "-1")
    roots = (str(repo), str(venv))
    def inside(value):
        return any(value == root or value.startswith(root + "/") for root in roots)
    need(sys.flags.no_site == 1 and sys.prefix == sys.base_prefix
         and Path(sys.executable).resolve(strict=True) == Path("/usr/bin/python3.12").resolve(strict=True),
         "owner-resource-release-system-stdlib-interpreter")
    need(Path.cwd() == temp and not inside(str(Path.cwd())), "owner-resource-release-cwd-outside-mounts")
    maps = Path("/proc/self/maps").read_bytes()
    need(len(maps) <= MIB, "owner-resource-release-map-bound")
    for line in maps.decode("utf-8", "strict").splitlines():
        fields = line.split(None, 5)
        need(len(fields) >= 5 and (len(fields) == 5 or not inside(fields[5])),
             "owner-resource-release-mapped-owned-file")
    # Materialize and close scandir before observing descriptors, so the
    # observation does not create an FD which vanishes while being inspected.
    with os.scandir("/proc/self/fd") as entries:
        fds = sorted(int(entry.name) for entry in entries if entry.name.isdecimal())
    observed = []
    for fd in fds:
        try:
            target = os.readlink("/proc/self/fd/" + str(fd))
        except FileNotFoundError:
            continue
        need(not inside(target), "owner-resource-release-open-owned-file")
        observed.append({"fd": fd, "target": target})
    need(not any(name == "cryptography" or name.startswith("cryptography.") for name in sys.modules),
         "owner-resource-release-custody-module-imported")
    return {"pid": os.getpid(), "cwd": str(Path.cwd()), "executable": sys.executable,
        "site_disabled": True, "maps": descriptor(maps), "open_fds": observed,
        "owned_mount_mappings_present": False, "Native_authority_granted": False}


def release_deadline(scope, original_deadline, upload_seconds, now):
    need(scope in POLICIES and all(type(value) in (int, float) and math.isfinite(value)
         for value in (original_deadline, now)), "owner-resource-release-clock-value")
    if scope == "fixedtxt":
        need(type(upload_seconds) is int and 0 < upload_seconds <= 3900,
             "owner-resource-release-upload-reserve")
        reserve = min(30, upload_seconds)
    else:
        need(upload_seconds is None, "owner-resource-release-hero-clock")
        reserve = 120
    until = min(original_deadline - reserve, now + 30)
    need(now < until, "owner-resource-release-original-clock-expired")
    return until


def handoff_shared_release(scope):
    """Compatibility entry: exec discards any already mapped custody modules."""
    origin, _, _, _ = admitted(scope)
    argv = selected_command(scope, "release-shared", None, origin, os.environ)
    os.chdir(base_temp(origin["base_temp"]))
    os.execve(argv[0], argv, dict(os.environ))


def release_shared_stdlib(scope):
    # Both imports have only stdlib at module scope. The supervisor receives
    # this module's exclusive writer, so its custody/crypto import is not used.
    import native_fixedtxt_session as shared
    import native_fixedtxt_g2_supervisor as owned
    origin, cgroup, journal, origin_digest = admitted(scope)
    current, temp = origin["context"], Path(origin["base_temp"])
    need(members(cgroup / "coordinator") == [os.getpid()]
         and members(cgroup / "controller") == [os.getppid()],
         "owner-resource-release-original-coordinators")
    command = strict(regular(journal / "release-shared/intent.original.json", owner=origin["uid"]))
    need(command.get("context") == current and command.get("origin") == origin_digest
         and command.get("action") == "release-shared" and command.get("case_index") is None
         and command.get("argv") == selected_command(scope, "release-shared", None, origin, os.environ)
         and command.get("controller_cwd") == str(temp), "owner-resource-release-original-command")
    root = temp / (("hp" + current["run_id"] + "-1") if scope == "hero" else
                   ("mw-fixedtxt-session-" + current["run_id"] + "-1"))
    private_directory(root, origin["uid"])
    if scope == "fixedtxt":
        need(os.environ.get("MW_FIXEDTXT_SESSION_ROOT") == str(root), "owner-resource-release-session-root")
    process = release_process_observation(origin)
    receipt_path = root / "shared-readonly.original.json"
    if not receipt_path.exists():
        new(root / "release-unresolved.original.json", {"context": current,
            "reason": "shared-readonly-original-absent", "released": False, "automatic_retry": False})
        return
    receipt = strict(regular(receipt_path, owner=origin["uid"]))
    expected = [str(Path(origin["source_root"])),
                str(temp / (POLICIES[scope]["python"] + current["run_id"] + "-1"))]
    need(receipt.get("context") == current and type(receipt.get("targets")) is list
         and all(type(row) is dict for row in receipt["targets"])
         and [row.get("path") for row in receipt["targets"]] == expected,
         "owner-resource-release-two-original-targets")
    targets = shared.verify_mounts(receipt)
    def quiescent():
        result = {phase: leaf_observation(cgroup / phase) for phase in ("preparation", "work", "custody")}
        need(all(not row["populated"] and not row["members"] for row in result.values()),
             "owner-resource-release-live-owned-cohort")
        return result
    before = quiescent()
    binding, _ = clock_originals(scope, current, temp, uid=origin["uid"])
    upload_seconds = None
    if scope == "fixedtxt":
        session = strict(regular(root / "session-clock.original.json", owner=origin["uid"]))
        need(session.get("schema") == "motionwright.native.fixedtxt-session-clock/1"
             and session.get("context") == current and session.get("boot_id") == binding["boot_id"]
             and session.get("started_monotonic") == binding["started_monotonic"]
             and session.get("deadline_monotonic") == binding["deadline_monotonic"],
             "owner-resource-release-original-session-clock")
        template = strict(regular(root / "session-template.original.json", owner=origin["uid"]))
        upload_seconds = template.get("upload_seconds")
    until = release_deadline(scope, binding["deadline_monotonic"], upload_seconds, time.monotonic())
    until = min(until, command["deadline"]["effective_deadline_monotonic"] - 2)
    class ClosureClock:
        started = binding["started_monotonic"]
        total = binding["deadline_monotonic"]
        used = set()
        def reserve(self, phase):
            need(phase in ("release-0", "release-1") and phase not in self.used
                 and _CANCEL is None and time.monotonic() < until,
                 "owner-resource-release-expired-or-replayed-stage")
            self.used.add(phase)
            return until
    directory = root / "release"
    directory.mkdir(mode=0o700, exist_ok=False)
    new(directory / "stdlib-entry.original.json", {"context": current, "origin": origin_digest,
        "process": process, "readonly_receipt": descriptor(regular(receipt_path, owner=origin["uid"])),
        "deadline_monotonic": until, "original_deadline_monotonic": binding["deadline_monotonic"],
        "automatic_retry": False})
    timeline = ClosureClock()
    containment = OwnedLeaf(scope, current, "preparation")
    first = None
    try:
        for index, target in enumerate(reversed(targets)):
            release_process_observation(origin)
            need(shared.mount_rows(Path("/proc/self/mountinfo").read_bytes(), target["path"])
                 == target["mounts"], "owner-resource-release-mount-identity-changed")
            result = owned._run_original(["sudo", "-n", "umount", "--", target["path"]],
                environment={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}, directory=directory,
                phase="release-" + str(index), clock=timeline, containment=containment,
                stream_limit=65536, original_writer=new)
            need(result.get("exit_code") == 0 and result.get("original_complete") is True
                 and result.get("containment_empty") is True and result.get("native_outcome") == "NOT_INFERRED"
                 and result.get("overflow") is False and result.get("deadline_exceeded") is False
                 and "descendants_survived_wrapper" not in result,
                 "owner-resource-release-original-unmount-incomplete-no-retry")
            need(not shared.mount_rows(Path("/proc/self/mountinfo").read_bytes(), target["path"]),
                 "owner-resource-release-owned-bind-still-mounted")
    except BaseException as error:
        first = error
    finally:
        try:
            containment.close()
        except BaseException as error:
            if first is None:
                first = error
            else:
                first.add_note("Release leaf descriptor closure also failed: " + type(error).__name__)
    if first is not None:
        raise first
    after = quiescent()
    new(root / "released.original.json", {"context": current, "targets": targets,
        "owned_mounts_released": True, "quiescent_before": before, "quiescent_after": after,
        "observed_monotonic": time.monotonic(), "native_acceptance": False,
        "prior_owner_cleanup_inferred": False, "automatic_retry": False})


def bootstrap(scope):
    origin, root, journal, original_digest = admitted(scope)
    need(Path("/proc/self/cgroup").read_text().splitlines()
         == ["0::" + str(root / "coordinator").removeprefix("/sys/fs/cgroup")], "owner-resource-bootstrap-coordinator")
    folder = private_directory(journal / "bootstrap", origin["uid"])
    intent = strict(regular(folder / "intent.original.json", owner=origin["uid"]))
    deadline = intent["deadline"]["effective_deadline_monotonic"] - 3
    temp, repo = Path(origin["base_temp"]), Path(origin["source_root"])
    venv = temp / (POLICIES[scope]["python"] + origin["context"]["run_id"] + "-1")
    need(not venv.exists() and not venv.is_symlink(), "owner-resource-bootstrap-no-existing-venv")
    python = str(venv / "bin/python")
    commands = (
        ["sudo", "-n", "apt-get", "update"],
        ["sudo", "-n", "apt-get", "install", "--no-install-recommends", "-y", "python3.12-venv"],
        ["/usr/bin/python3.12", "-m", "venv", str(venv)],
        [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
         "--only-binary=:all:", "--require-hashes", "-r", str(repo / "tooling/requirements/mlt-g2-custody.txt")],
        [python, "-m", "pip", "check"],
    )
    environment = {key: value for key, value in os.environ.items()
                   if key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull)
    leaf = OwnedLeaf(scope, origin["context"], "preparation")
    try:
        for index, argv in enumerate(commands):
            need(time.monotonic() < deadline and not leaf.populated(), "owner-resource-bootstrap-clock-or-cohort")
            new(folder / ("bootstrap-%02d.intent.json" % index), {"context": origin["context"], "argv": argv,
                "origin": original_digest, "deadline_monotonic": deadline, "automatic_retry": False})
            process = None
            first, code, observed = None, None, False
            try:
                process = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL,
                    start_new_session=True, preexec_fn=leaf.child_enter)
                new(folder / ("bootstrap-%02d.spawned.json" % index), {"pid": process.pid, "automatic_retry": False})
                code = process.wait(timeout=max(0.01, min(600, deadline - time.monotonic())))
                observed = not leaf.populated()
                need(code == 0 and observed, "owner-resource-bootstrap-original-command-failed")
            except BaseException as error:
                first = error
            finally:
                if leaf.populated():
                    leaf.stop()
                if process is not None and process.poll() is None:
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                new(folder / ("bootstrap-%02d.terminal.json" % index), {
                    "context": origin["context"], "exit_code": code, "quiescent_observed": observed,
                    "first_error_type": None if first is None else type(first).__name__,
                    "automatic_retry": False, "Native_outcome": "NOT_INFERRED"})
            if first is not None:
                raise first
    finally:
        leaf.close()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("python=" + python + "\n")


def observe_finished(scope, action, case):
    origin, root, journal, original_digest = admitted(scope, allow_expired=True)
    value = observe_tree(root, origin)
    label = action_label(action, case)
    directory = private_directory(journal / label, origin["uid"])
    # Called once by the workflow after the original controller has exited.
    # Live processes remain a genuine negative; this read never terminates or
    # retries them, and it cannot grant the next Native case.
    complete = all(not row["populated"] for row in value["leaves"].values())
    new(directory / "returned.original.json", {"schema": "motionwright.native.owner-resource-returned/1",
        "context": origin["context"], "origin": original_digest, "action": action, "case_index": case,
        "observed": value, "all_owned_groups_empty": complete, "Native_outcome": "NOT_INFERRED",
        "automatic_retry": False})
    need(complete and value["parent"]["peak_bytes"] <= LIMIT, "owner-resource-returned-tree-incomplete")
    print("OWNER_RESOURCE_TREE_EMPTY action=" + label + " Native_outcome=NOT_INFERRED")


def cancel(signum, _frame):
    global _CANCEL
    _CANCEL = _CANCEL or signum


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("initialize", "enter", "child", "bootstrap", "observe", "release"))
    parser.add_argument("--scope", choices=tuple(POLICIES), required=True)
    parser.add_argument("--action")
    parser.add_argument("--case-index", type=int)
    parser.add_argument("--original-path")
    parser.add_argument("--original-home")
    args = parser.parse_args()
    if args.mode in ("initialize", "bootstrap", "release"):
        need(args.action is None and args.case_index is None and args.original_path is None and args.original_home is None,
             "owner-resource-closed-simple-command")
        {"initialize": initialize, "bootstrap": bootstrap, "release": release_shared_stdlib}[args.mode](args.scope)
    else:
        need(args.action is not None, "owner-resource-action-required")
        if args.mode == "enter":
            enter(args.scope, args.action, args.case_index, args.original_path, args.original_home)
        else:
            need(args.original_path is None and args.original_home is None, "owner-resource-closed-nonprivileged-command")
            (child if args.mode == "child" else observe_finished)(args.scope, args.action, args.case_index)


if __name__ == "__main__":
    previous = {number: signal.signal(number, cancel) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        main()
    except Exception as error:
        code = error.args[0] if type(error) is ResourceRejected and len(error.args) == 1 else "unclassified"
        if type(code) is not str or not re.fullmatch(r"owner-resource-[a-z0-9-]{1,100}", code):
            code = "unclassified"
        print("OWNER_RESOURCE_INCOMPLETE_NO_RETRY error_type=" + type(error).__name__ + " code=" + code, file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
