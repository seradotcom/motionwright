"""Bounded launcher for an acyclic, workflow-bound startup capture admission.

Workflow review and the original CI identity are the admission boundary. Kernel
identities bind processes, not human approval or a same-UID security boundary.
No installed operation or replay is authorized by this module alone.
"""
from __future__ import annotations

import argparse
import ast
import base64
import ctypes
from dataclasses import dataclass, field
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time
import types
import uuid

NS = 1_000_000_000
SECONDS = {"operation": 24, "join": 26, "cleanup": 32, "custody": 40}
SCHEMA = "motionwright.startup-launch-context/1"
MANIFEST_SCHEMA = "motionwright.startup-capture-admission/1"
LAUNCHER_PATH = "tooling/startup_capture_launcher.py"
MANIFEST_PATH = "tooling/fixtures/startup-capture/admission.json"
WORKFLOW_PATH = ".github/workflows/startup-capture-source.yml"
SOURCE_ROLES = ("launcher", "outer", "bootstrap", "core", "collector", "observer")
BOUND_PLAN_ROLES = {"bootstrap": "bootstrap", "observer": "observer", "observer_subtree_core": "core"}
RUNTIME_FIELDS = frozenset({"start_mono_ns", "channels", "capture_directory", "external_parent", "launch_context"})
CONTEXT_LIMIT = 262144
PREPARATION_LIMIT = 1048576
OUTER_STDOUT_LIMIT = 2 * 1024 * 1024
OUTER_STDERR_LIMIT = 128 * 1024
PHASE_LIMIT = 1024 * 1024
CUSTODY_RECORD_LIMIT = 65536
RESULT_LIMIT = 4096
CORE_SOURCE_SHA256 = "509d7fc1756064764ab1970806ad63ef70d4b161ea1343bedd711e1a4d3f3468"
ENV_KEYS = frozenset({"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "GDK_BACKEND",
    "AT_SPI_BUS_ADDRESS", "NO_AT_BRIDGE", "GTK_USE_PORTAL", "QT_QPA_PLATFORM"})
PYTHON_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1"}
INJECTION_KEYS = frozenset({"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH", "GCONV_PATH",
    "LOCPATH", "GLIBC_TUNABLES", "NODE_OPTIONS", "PERL5OPT", "RUBYOPT"})
ANCHOR_KEYS = ("STARTUP_CAPTURE_MANIFEST_PATH", "STARTUP_CAPTURE_MANIFEST_SHA256",
               "STARTUP_CAPTURE_LAUNCHER_PATH", "STARTUP_CAPTURE_LAUNCHER_SHA256")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")


class LauncherError(RuntimeError):
    def __init__(self, message, *, prefix=b"", custody=None):
        super().__init__(message)
        self.prefix, self.custody = bytes(prefix), custody


def require(condition, message):
    if not condition:
        raise LauncherError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()


def strict_json(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit, "bounded JSON required")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    def invalid(_):
        raise LauncherError("nonfinite JSON value")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    require(type(value) is dict, "JSON object required")
    return value


def original_deadlines(started_ns, *, now_ns=None):
    require(type(started_ns) is int and started_ns > 0, "original integer clock required")
    now = time.monotonic_ns() if now_ns is None else now_ns
    require(type(now) is int and started_ns <= now < started_ns + 24 * NS,
            "original clock is future or admission deadline expired")
    return {name + "_ns": started_ns + seconds * NS for name, seconds in SECONDS.items()}


def allowlisted_environment(environment):
    require(type(environment) is dict and all(type(k) is str and type(v) is str for k, v in environment.items()),
            "typed environment required")
    for key, value in environment.items():
        forbidden = key in INJECTION_KEYS or key.startswith(("LD_", "MALLOC_"))
        forbidden |= key.startswith("PYTHON") and (key not in PYTHON_ENV or value != PYTHON_ENV[key])
        require(not forbidden or value == "", "unsafe pre-exec environment: " + key)
    result = {key: value for key, value in environment.items() if key in ENV_KEYS}
    require(all("\0" not in value and len(value.encode()) <= 8192 for value in result.values()),
            "environment value exceeds bound")
    result.setdefault("PATH", "/usr/bin:/bin")
    result.setdefault("LANG", "C.UTF-8")
    result.update(PYTHON_ENV)
    return result


def _relative(path):
    require(type(path) is str and re.fullmatch(r"[A-Za-z0-9_.\-/]+", path) and
            not path.startswith("/") and all(part not in ("", ".", "..") for part in path.split("/")),
            "safe repository-relative path required")
    return path


def _read_file(path, limit):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, "canonical regular file required")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "bounded regular file required")
        chunks, total = [], 0
        while total <= limit:
            raw = os.read(fd, min(65536, limit + 1 - total))
            if not raw:
                break
            chunks.append(raw); total += len(raw)
        after = os.fstat(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        require(total <= limit and identity(before) == identity(after), "file changed during readback")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _proc_bytes(pid, leaf, limit):
    require(type(pid) is int and pid > 1 and leaf in {"stat", "cmdline", "status"}, "owned process query required")
    with open(f"/proc/{pid}/{leaf}", "rb", buffering=0) as source:
        raw = source.read(limit + 1)
    require(len(raw) <= limit, "procfs byte bound")
    return raw


def _kernel_stamp(pid):
    raw = _proc_bytes(pid, "stat", 4096)
    end = raw.rfind(b")")
    fields = raw[end + 2:].split()
    require(end > 0 and raw[:raw.find(b" ")].isdigit() and int(raw.split(b" ", 1)[0]) == pid and len(fields) >= 20,
            "invalid procfs identity")
    return {"pid": pid, "start_ticks": int(fields[19]), "parent_pid": int(fields[1]),
            "process_group_id": int(fields[2]), "session_id": int(fields[3]),
            "uid": os.stat(f"/proc/{pid}").st_uid}


def _same_stamp(left, right):
    return type(left) is dict and type(right) is dict and set(left) == set(right) and all(
        type(left[key]) is int and type(right[key]) is int and left[key] == right[key] for key in left)


def _process_binding(pid, source):
    before = _kernel_stamp(pid)
    executable = Path(os.readlink(f"/proc/{pid}/exe"))
    exe_raw = _read_file(executable, 536870912)
    argv = _proc_bytes(pid, "cmdline", 65536)
    require(_same_stamp(before, _kernel_stamp(pid)), "process changed during binding")
    return {**before, "source": source, "executable": {"path": str(executable), "sha256": digest(exe_raw)},
            "cmdline_sha256": digest(argv)}


def _workflow_identity(environment):
    required = ("GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF",
                "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")
    require(all(type(environment.get(k)) is str for k in required), "original CI identity required")
    require(environment["GITHUB_ACTIONS"] == "true" and SHA1.fullmatch(environment["GITHUB_SHA"]) and
            SHA1.fullmatch(environment["GITHUB_WORKFLOW_SHA"]), "exact CI source identities required")
    repository = environment["GITHUB_REPOSITORY"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "repository identity required")
    prefix = repository + "/" + WORKFLOW_PATH + "@"
    require(environment["GITHUB_WORKFLOW_REF"].startswith(prefix) and
            len(environment["GITHUB_WORKFLOW_REF"]) > len(prefix), "actual selected workflow required")
    require(environment["GITHUB_RUN_ID"].isdigit() and int(environment["GITHUB_RUN_ID"]) > 0 and
            environment["GITHUB_RUN_ATTEMPT"] == "1" and re.fullmatch(r"[a-z0-9_-]+", environment["GITHUB_JOB"]),
            "original run/job required; no replay")
    return {"repository": repository, "source_sha": environment["GITHUB_SHA"],
            "workflow_sha": environment["GITHUB_WORKFLOW_SHA"], "workflow_ref": environment["GITHUB_WORKFLOW_REF"],
            "path": WORKFLOW_PATH, "run_id": environment["GITHUB_RUN_ID"],
            "run_attempt": environment["GITHUB_RUN_ATTEMPT"], "job": environment["GITHUB_JOB"]}


def workflow_literals(raw, job):
    require(type(raw) is bytes and len(raw) <= 262144 and re.fullmatch(r"[a-z0-9_-]+", job), "bounded workflow/job required")
    text = raw.decode("utf-8")
    require(text.count("\njobs:\n") == 1, "one literal jobs mapping required")
    require(not re.search(r"(?m)^[\"']jobs[\"']\s*:", text), "quoted duplicate jobs mapping rejected")
    jobs = text.split("\njobs:\n", 1)[1]
    require(all(re.fullmatch(r"  [a-z0-9_-]+:", line) for line in jobs.splitlines()
                if line.startswith("  ") and len(line) > 2 and line[2] not in " #"),
            "quoted, merged or nonliteral workflow jobs are not admission anchors")
    entries = list(re.finditer(r"(?m)^  ([a-z0-9_-]+):\n", jobs))
    selected = [i for i, item in enumerate(entries) if item.group(1) == job]
    require(len(selected) == 1, "one actual workflow job required")
    index = selected[0]
    body = jobs[entries[index].end():entries[index + 1].start() if index + 1 < len(entries) else len(jobs)]
    require(body.count("    steps:\n") == 1, "literal steps required")
    head = body.split("    steps:\n", 1)[0]
    require(all(re.fullmatch(r"    [A-Za-z_][A-Za-z_0-9-]*:.*", line) for line in head.splitlines()
                if line.startswith("    ") and len(line) > 4 and line[4] not in " #"),
            "quoted or merged job attributes are not admission anchors")
    require(head.count("    env:\n") == 1, "one immediate job environment required")
    env_text = head.split("    env:\n", 1)[1]
    env_text = re.split(r"(?m)^    [A-Za-z_][A-Za-z_0-9-]*:", env_text, maxsplit=1)[0]
    seen = set()
    for line in env_text.splitlines():
        if not line.strip() or line.startswith("      #"):
            continue
        entry = re.fullmatch(r'      ([A-Z][A-Z_0-9]*): (?:"([^"\\]*)"|\'([^\']*)\'|([A-Za-z0-9_./:-]+))', line)
        require(entry is not None and entry.group(1) not in seen, "closed literal job environment required")
        seen.add(entry.group(1))
    result = {}
    for key in ANCHOR_KEYS:
        values = re.findall(r'^      ' + re.escape(key) + r': "([A-Za-z0-9_./-]+)"$', env_text, re.M)
        require(len(values) == 1 and len(re.findall(r'^      ' + re.escape(key) + r':', env_text, re.M)) == 1,
                "missing, duplicate or nonliteral workflow anchor: " + key)
        result[key] = values[0]
    require(result[ANCHOR_KEYS[0]] == MANIFEST_PATH and result[ANCHOR_KEYS[2]] == LAUNCHER_PATH and
            SHA256.fullmatch(result[ANCHOR_KEYS[1]]) and SHA256.fullmatch(result[ANCHOR_KEYS[3]]),
            "foreign workflow admission path or digest")
    return result


def validate_manifest(value):
    require(type(value) is dict and set(value) == {"schema", "scope", "budgets_seconds", "sources", "plan", "required_environment"}
            and value["schema"] == MANIFEST_SCHEMA and value["scope"] in {"source-fixture", "independent-readonly"},
            "closed admission manifest required")
    require(type(value["budgets_seconds"]) is dict and set(value["budgets_seconds"]) == set(SECONDS) and all(
        type(value["budgets_seconds"][key]) is int and value["budgets_seconds"][key] == seconds for key, seconds in SECONDS.items()),
        "fixed original budgets required")
    require(type(value["sources"]) is dict and set(value["sources"]) == set(SOURCE_ROLES), "closed source role set required")
    for item in [*value["sources"].values(), value["plan"]]:
        require(type(item) is dict and set(item) == {"path", "sha256"} and type(item["sha256"]) is str and
                SHA256.fullmatch(item["sha256"]), "exact source descriptor required")
        _relative(item["path"])
    require(value["sources"]["launcher"]["path"] == LAUNCHER_PATH, "stable launcher path required")
    require(value["sources"]["core"]["sha256"] == CORE_SOURCE_SHA256, "reviewed stable core required")
    names = value["required_environment"]
    require(type(names) is list and all(type(k) is str and k in ENV_KEYS for k in names) and len(set(names)) == len(names),
            "closed required environment keys")
    return value


def _sources(repo, manifest):
    return {role: {"path": str(repo / item["path"]), "sha256": item["sha256"]}
            for role, item in manifest["sources"].items()}


def prepared_plan(template, sources, started_ns):
    require(type(template) is dict and not (set(template) & RUNTIME_FIELDS), "plan template cannot choose runtime fields")
    require(type(template.get("sources")) is dict and type(template.get("operation_id")) is str,
            "bound plan sources and operation required")
    require(str(uuid.UUID(template["operation_id"])) == template["operation_id"], "canonical operation identity required")
    result = dict(template)
    result["sources"] = dict(template["sources"])
    for plan_role, role in BOUND_PLAN_ROLES.items():
        expected = sources[role]
        original = template["sources"].get(plan_role)
        require(type(original) is dict and set(original) == {"path", "sha256"} and original["sha256"] == expected["sha256"],
                "plan cannot select source digest")
        require(original["path"] == expected["path"] or Path(expected["path"]).as_posix().endswith("/" + _relative(original["path"])),
                "plan cannot select source path")
        result["sources"][plan_role] = dict(expected)
    result["start_mono_ns"] = started_ns
    return result


def _git_bytes(repo, arguments, deadline_ns, *, limit=1048576):
    remaining = (deadline_ns - time.monotonic_ns()) / NS
    require(remaining > 0, "original preparation deadline")
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}
    command = ["/usr/bin/git", "--no-replace-objects", "-C", str(repo), *arguments]
    result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=remaining, check=False)
    require(time.monotonic_ns() < deadline_ns and result.returncode == 0 and len(result.stdout) <= limit
            and len(result.stderr) <= 65536, "exact Git read failed or exceeded original deadline")
    return result.stdout


def _git_file(repo, source, path, deadline_ns, limit):
    require(type(source) is str and SHA1.fullmatch(source), "exact Git commit required")
    reference = source + ":" + _relative(path)
    size = _git_bytes(repo, ["cat-file", "-s", reference], deadline_ns, limit=64).strip()
    require(size.isdigit() and int(size) <= limit, "Git blob exceeds source byte bound")
    return _git_bytes(repo, ["show", reference], deadline_ns, limit=limit)


def _admission_bytes(repo, workflow, started_ns):
    deadlines = original_deadlines(started_ns)
    require(repo.is_absolute() and repo.resolve(strict=True) == repo, "canonical repository required")
    head = _git_bytes(repo, ["rev-parse", "HEAD"], deadlines["operation_ns"], limit=128).decode().strip()
    require(head == workflow["source_sha"], "source checkout differs from original CI identity")
    raw_workflow = _git_file(repo, workflow["workflow_sha"], workflow["path"], deadlines["operation_ns"], 262144)
    anchor = workflow_literals(raw_workflow, workflow["job"])
    raw_manifest = _git_file(repo, workflow["workflow_sha"], MANIFEST_PATH, deadlines["operation_ns"], 65536)
    require(digest(raw_manifest) == anchor["STARTUP_CAPTURE_MANIFEST_SHA256"], "workflow-bound manifest digest mismatch")
    manifest = validate_manifest(strict_json(raw_manifest, 65536))
    require(manifest["sources"]["launcher"]["sha256"] == anchor["STARTUP_CAPTURE_LAUNCHER_SHA256"],
            "manifest cannot replace workflow-bound launcher")
    sources, source_bytes = _sources(repo, manifest), {}
    for role in SOURCE_ROLES:
        item = manifest["sources"][role]
        original = _git_file(repo, workflow["source_sha"], item["path"], deadlines["operation_ns"], 262144)
        current = _read_file(repo / item["path"], 262144)
        require(current == original and digest(original) == item["sha256"], "exact bound source changed: " + role)
        source_bytes[role] = original
    plan_item = manifest["plan"]
    raw_plan = _git_file(repo, workflow["source_sha"], plan_item["path"], deadlines["operation_ns"], 65536)
    require(digest(raw_plan) == plan_item["sha256"] and _read_file(repo / plan_item["path"], 65536) == raw_plan,
            "manifest-bound plan changed")
    template = strict_json(raw_plan, 65536)
    require(canonical(template) == raw_plan, "canonical immutable plan template required")
    plan = prepared_plan(template, sources, started_ns)
    original_deadlines(started_ns)
    return {"workflow": {**workflow, "sha256": digest(raw_workflow),
                         "original_base64": base64.b64encode(raw_workflow).decode()},
            "manifest": {"path": str(repo / MANIFEST_PATH), "sha256": digest(raw_manifest), "value": manifest,
                         "original_base64": base64.b64encode(raw_manifest).decode()},
            "plan_template": {"path": str(repo / plan_item["path"]), "sha256": digest(raw_plan), "value": template},
            "plan": plan, "sources": sources, "source_bytes": source_bytes}


class OwnedFD:
    def __init__(self, fd, label):
        require(type(fd) is int and fd >= 0, "newly owned descriptor required")
        self.fd, self.original_fd, self.label = fd, fd, label
        self.close_error = None

    def close(self):
        if self.fd is None:
            return self.close_error is None
        fd, self.fd = self.fd, None
        try:
            os.close(fd)
        except OSError as error:
            self.close_error = {"type": type(error).__name__, "errno": error.errno}
        return self.close_error is None

    def metadata(self):
        return {"fd": self.original_fd, "label": self.label, "retained": self.fd is not None, "close_error": self.close_error}


def _pipe():
    read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    r, w = OwnedFD(read, "read"), OwnedFD(write, "write")
    if read < 3 or write < 3:
        r.close(); w.close()
        raise LauncherError("new pipe cannot replace standard descriptors", custody=[r.metadata(), w.metadata()])
    return r, w


def _pipe_identity(fd, access):
    import fcntl
    info = os.fstat(fd)
    require(stat.S_ISFIFO(info.st_mode) and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == access,
            "actual directional pipe required")
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


@dataclass(frozen=True)
class LaunchContext:
    data: dict
    raw_sha256: str
    transport_identity: list
    raw: bytes = field(repr=False)


def read_launch_context(fd, *, started_ns):
    deadline = original_deadlines(started_ns)["operation_ns"]
    require(type(fd) is int and fd >= 3, "inherited context descriptor required")
    owned, raw = OwnedFD(fd, "launch-context"), bytearray()
    result, error = None, None
    try:
        identity = _pipe_identity(fd, os.O_RDONLY)
        os.set_blocking(fd, False)
        while time.monotonic_ns() < deadline:
            ready, _, _ = select.select([fd], [], [], min(.02, max(0, (deadline - time.monotonic_ns()) / NS)))
            require(time.monotonic_ns() < deadline, "context original deadline")
            if not ready:
                continue
            chunk = os.read(fd, min(65536, CONTEXT_LIMIT + 1 - len(raw)))
            if not chunk:
                value = strict_json(bytes(raw), CONTEXT_LIMIT)
                require(canonical(value) == bytes(raw), "single canonical context plus EOF required")
                require(type(value.get("started_ns")) is int and value["started_ns"] == started_ns,
                        "context cannot replace CLI original clock")
                transport = value.get("transport")
                require(type(transport) is dict and set(transport) == {"fd", "identity"}
                        and type(transport["fd"]) is int and transport["fd"] == fd
                        and type(transport["identity"]) is list and all(type(x) is int for x in transport["identity"])
                        and transport["identity"] == identity, "context pipe binding mismatch")
                result = LaunchContext(value, digest(bytes(raw)), identity, bytes(raw))
                break
            raw.extend(chunk)
            require(len(raw) <= CONTEXT_LIMIT, "context exceeds bounded frame")
        require(result is not None, "context EOF absent at original deadline")
    except BaseException as caught:
        error = caught
    finally:
        closed = owned.close()
    if error is not None or not closed:
        raise LauncherError("context unresolved: " + (str(error) if error else "descriptor close uncertain"),
                            prefix=bytes(raw), custody=owned.metadata()) from error
    return result


def _verify_kernel_binding(expected, pid, *, source_sha=None):
    fields = {"pid", "start_ticks", "parent_pid", "process_group_id", "session_id", "uid"}
    require(type(expected) is dict and set(expected) == fields | {"source", "executable", "cmdline_sha256"},
            "closed live process binding required")
    before = _kernel_stamp(pid)
    require(all(type(expected[key]) is int and expected[key] == before[key] for key in fields),
            "live process identity changed")
    require(before["uid"] == os.getuid(), "foreign process owner")
    require(type(expected["source"]) is dict and set(expected["source"]) == {"path", "sha256"}, "source binding required")
    if source_sha is not None:
        require(expected["source"]["sha256"] == source_sha, "caller cannot replace pinned launcher")
    source_path = Path(expected["source"]["path"])
    require(digest(_read_file(source_path, 262144)) == expected["source"]["sha256"], "process source bytes changed")
    executable = Path(os.readlink(f"/proc/{pid}/exe"))
    require(expected["executable"] == {"path": str(executable), "sha256": digest(_read_file(executable, 536870912))},
            "actual process executable mismatch")
    argv = _proc_bytes(pid, "cmdline", 65536)
    parts = argv.rstrip(b"\0").split(b"\0")
    require(len(parts) >= 4 and parts[1:4] == [b"-I", b"-B", os.fsencode(source_path)]
            and digest(argv) == expected["cmdline_sha256"], "actual process argv mismatch")
    require(_same_stamp(before, _kernel_stamp(pid)), "process changed during live admission")


def verify_child_launch_context(context, actual_outer_sha, plan, *, expected_launcher_sha256):
    require(type(context) is LaunchContext and type(actual_outer_sha) is str and SHA256.fullmatch(actual_outer_sha)
            and type(expected_launcher_sha256) is str and SHA256.fullmatch(expected_launcher_sha256),
            "bound launcher context and fixed launcher identity required")
    data = context.data
    require(type(data) is dict and set(data) == {"schema", "repo", "started_ns", "deadlines", "launcher", "outer",
            "workflow", "manifest", "plan_template", "plan_sha256", "environment", "transport"}
            and data["schema"] == SCHEMA and canonical(data) == context.raw and digest(context.raw) == context.raw_sha256,
            "context bytes or schema changed")
    deadlines = original_deadlines(data["started_ns"])
    require(type(data["deadlines"]) is dict and set(data["deadlines"]) == set(deadlines) and all(
        type(data["deadlines"][key]) is int and data["deadlines"][key] == value for key, value in deadlines.items()),
        "context cannot renew original budgets")
    manifest = validate_manifest(data["manifest"]["value"])
    sources = _sources(Path(data["repo"]), manifest)
    require(manifest["sources"]["outer"]["sha256"] == actual_outer_sha and
            manifest["sources"]["launcher"]["sha256"] == expected_launcher_sha256,
            "context selected a foreign outer or launcher")
    expected_plan = prepared_plan(data["plan_template"]["value"], sources, data["started_ns"])
    require(type(plan) is dict and canonical(plan) == canonical(expected_plan) and
            digest(canonical(plan)) == data["plan_sha256"], "foreign plan cannot override admitted template")
    environment = allowlisted_environment(data["environment"])
    require(environment == data["environment"] and all(environment.get(key) for key in manifest["required_environment"]),
            "manifest-required environment missing or changed")
    # Everything above is pure: a foreign plan cannot trigger a bootstrap read.
    require(data["launcher"]["pid"] == os.getppid() and data["outer"]["pid"] == os.getpid(),
            "direct launch lacks the actual retained launcher parent")
    _verify_kernel_binding(data["launcher"], os.getppid(), source_sha=expected_launcher_sha256)
    _verify_kernel_binding(data["outer"], os.getpid(), source_sha=actual_outer_sha)
    actual_env = allowlisted_environment(dict(os.environ))
    require(actual_env == environment, "effective pre-exec environment differs from admitted environment")
    verified = _admission_bytes(Path(data["repo"]), data["workflow"], data["started_ns"])
    require(canonical(verified["manifest"]) == canonical(data["manifest"])
            and canonical(verified["workflow"]) == canonical(data["workflow"])
            and canonical(verified["plan_template"]) == canonical(data["plan_template"])
            and canonical(verified["plan"]) == canonical(plan), "actual workflow/manifest/template binding mismatch")
    original_deadlines(data["started_ns"])
    return {"manifest": manifest, "sources": sources, "environment": environment,
            "started_ns": data["started_ns"], **deadlines,
            "provenance": {"workflow": data["workflow"], "manifest_sha256": data["manifest"]["sha256"],
                           "launcher": data["launcher"], "context_sha256": context.raw_sha256,
                           "human_review_authenticated": False, "same_uid_security_boundary": False}}


def _require_linux():
    require(sys.platform.startswith("linux") and hasattr(os, "fork") and hasattr(os, "pidfd_open")
            and hasattr(signal, "pidfd_send_signal"), "Linux pidfd custody required")
    pid = os.getpid()
    numeric = _kernel_stamp(pid)
    with open("/proc/self/stat", "rb", buffering=0) as stream:
        own = stream.read(4097)
    with open("/proc/self/status", "rb", buffering=0) as stream:
        status_raw = stream.read(65537)
    require(len(own) <= 4096 and own.split(b" ", 1)[0] == str(pid).encode()
            and numeric["parent_pid"] == os.getppid() and numeric["process_group_id"] == os.getpgrp()
            and numeric["session_id"] == os.getsid(0), "incoherent procfs namespace")
    nspid = [line.split(b":", 1)[1].split() for line in status_raw.splitlines() if line.startswith(b"NSpid:")]
    require(nspid == [[str(pid).encode()]], "unsupported procfs PID namespace ancestry")


def _close_in_child(keep):
    for name in os.listdir("/proc/self/fd"):
        if name.isdigit() and int(name) not in keep:
            try:
                os.close(int(name))
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise


def _write_pipe(fd, raw, deadline_ns):
    offset = 0
    while offset < len(raw) and time.monotonic_ns() < deadline_ns:
        try:
            count = os.write(fd, raw[offset:offset + 4096])
            require(count > 0, "zero pipe write")
            offset += count
        except BlockingIOError:
            select.select([], [fd], [], min(.02, max(0, (deadline_ns - time.monotonic_ns()) / NS)))
    require(offset == len(raw), "pipe write exceeded original deadline")


class Child:
    """One direct child and its original retained handles; never a tree oracle."""
    def __init__(self, pid, result, gate_fd, pidfd, identity):
        self.pid, self.result, self.gate_fd = pid, result, gate_fd
        self.pidfd, self.identity = pidfd, identity
        self.authenticated = False
        self.wait, self.wait_lost, self.exited_ns = None, False, None
        self.signal_attempts, self.errors = [], []
        self.prefix, self.eof, self.truncated = bytearray(), False, False
        self.released = False
        self.extra_descriptors = []

    def error(self, message):
        if message not in self.errors:
            self.errors.append(message)

    def poll(self, now_ns):
        if self.pidfd is not None and self.pidfd.fd is not None and self.authenticated:
            poll = select.poll(); poll.register(self.pidfd.fd, select.POLLIN)
            events = poll.poll(0)
            if any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
                self.error("retained pidfd unavailable")
            elif any(mask & (select.POLLIN | select.POLLHUP) for _, mask in events) and self.exited_ns is None:
                self.exited_ns = now_ns
        if self.wait is None and not self.wait_lost:
            try:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                if pid:
                    require(pid == self.pid, "foreign wait result")
                    self.wait = {"pid": pid, "wait_status": status, "exit_code": os.waitstatus_to_exitcode(status),
                                 "observed_monotonic_ns": now_ns}
            except (OSError, LauncherError) as error:
                self.wait_lost = True; self.error("original wait unavailable: " + type(error).__name__)

    def kill_once(self, now_ns):
        if self.signal_attempts or self.wait is not None or self.exited_ns is not None or not self.authenticated or self.pidfd is None or self.pidfd.fd is None:
            return
        record = {"signal": int(signal.SIGKILL), "mono_ns": now_ns, "outcome": "UNKNOWN", "transport": "RETAINED_PIDFD"}
        self.signal_attempts.append(record)
        try:
            signal.pidfd_send_signal(self.pidfd.fd, signal.SIGKILL, None, 0)
            record["outcome"] = "SENT"
        except ProcessLookupError:
            record["outcome"] = "ALREADY_EXITED_WAIT_REQUIRED"
        except OSError as error:
            record["error"] = type(error).__name__

    def release(self, deadline_ns):
        require(self.authenticated and not self.released and time.monotonic_ns() < deadline_ns, "owned original release required")
        self.released = True
        try:
            require(os.write(self.gate_fd.fd, b"G") == 1, "release byte failed")
        finally:
            if not self.gate_fd.close():
                self.error("release descriptor close uncertain")

    def metadata(self):
        return {"pid": self.pid, "identity": self.identity, "identity_authenticated": self.authenticated,
                "original_wait": self.wait, "wait_lost": self.wait_lost, "pidfd_exit_monotonic_ns": self.exited_ns,
                "signals": self.signal_attempts, "errors": list(self.errors), "released": self.released,
                "result_eof": self.eof, "result_truncated": self.truncated,
                "result_bytes": len(self.prefix), "result_sha256": digest(bytes(self.prefix)),
                "descriptors": [item.metadata() for item in (self.result, self.gate_fd, self.pidfd, *self.extra_descriptors) if item is not None],
                "descendant_closure": "NOT_OBSERVED",
                "process_tree_closed": False}

    def close(self):
        seen = set()
        for item in (self.result, self.gate_fd, self.pidfd, *self.extra_descriptors):
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            if item is not None and not item.close():
                self.error("descriptor close uncertain: " + item.label)


def _spawn_worker(function, deadline_ns, *, keep_fds=()):
    require(time.monotonic_ns() < deadline_ns, "no worker launch after original deadline")
    _require_linux()
    read, write = _pipe()
    gate_read = gate_write = None
    try:
        gate_read, gate_write = _pipe()
        pid = os.fork()
    except BaseException:
        for item in (read, write, gate_read, gate_write):
            if item is not None:
                item.close()
        raise
    if pid == 0:
        try:
            _close_in_child({write.fd, gate_read.fd, *keep_fds})
            while time.monotonic_ns() < deadline_ns:
                ready, _, _ = select.select([gate_read.fd], [], [], .02)
                if ready:
                    require(os.read(gate_read.fd, 1) == b"G", "worker release absent")
                    break
            else:
                raise LauncherError("worker original release deadline")
            gate_read.close()
            value = function(write.fd)
            raw = canonical({"ok": True, "value": value})
            require(len(raw) <= PREPARATION_LIMIT, "worker result byte bound")
            _write_pipe(write.fd, raw, deadline_ns)
            os._exit(0)
        except BaseException as error:
            try:
                _write_pipe(write.fd, canonical({"ok": False, "error": type(error).__name__ + ": " + str(error)[:256]}), deadline_ns)
            finally:
                os._exit(125)
    handle = Child(pid, read, gate_write, None, None)
    handle.extra_descriptors = [write, gate_read]
    try:
        closes = [write.close(), gate_read.close()]
        if not all(closes):
            raise LauncherError("worker parent descriptor close uncertain")
        before = _kernel_stamp(pid)
        handle.pidfd = OwnedFD(os.pidfd_open(pid), "worker-pidfd")
        require(_same_stamp(before, _kernel_stamp(pid)) and before["parent_pid"] == os.getpid()
                and before["uid"] == os.getuid(), "worker pidfd identity changed")
        handle.identity, handle.authenticated = before, True
        handle.release(deadline_ns)
    except BaseException as error:
        handle.error("worker start unresolved: " + type(error).__name__)
        handle.gate_fd.close()
        raise LauncherError("retained worker start failure", custody=handle) from error
    return handle


def _drain_child(handle, limit):
    if handle.eof or handle.result.fd is None:
        return
    try:
        raw = os.read(handle.result.fd, 65536)
        if raw:
            remaining = max(0, limit - len(handle.prefix))
            handle.prefix.extend(raw[:remaining])
            handle.truncated |= len(raw) > remaining
        else:
            handle.eof = True
    except BlockingIOError:
        pass
    except OSError as error:
        handle.error("result pipe read uncertain: " + type(error).__name__)


def observe_worker(handle, deadline_ns, *, limit=PREPARATION_LIMIT, clock=time.monotonic_ns, cleanup_reserve_ns=NS):
    while clock() < deadline_ns:
        now = clock()
        _drain_child(handle, limit)
        handle.poll(now)
        if handle.wait is not None and handle.eof and handle.exited_ns is not None:
            break
        if now >= deadline_ns - cleanup_reserve_ns or handle.errors or handle.truncated:
            handle.kill_once(now)
        select.select([], [], [], min(.01, max(0, (deadline_ns - clock()) / NS)))
    complete = (clock() <= deadline_ns and handle.wait is not None and handle.wait["exit_code"] == 0
                and handle.eof and not handle.truncated and handle.exited_ns is not None and not handle.errors
                and not handle.signal_attempts)
    return complete


def _write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, "short preparation write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _prepare(repo, workflow, environment, started_ns, launcher_pid):
    admitted = _admission_bytes(repo, workflow, started_ns)
    manifest, plan = admitted["manifest"]["value"], admitted["plan"]
    require(all(environment.get(key) for key in manifest["required_environment"]), "required graphical/profile environment missing")
    require(launcher_pid == os.getppid(), "preparation parent changed")
    launcher = _process_binding(launcher_pid, admitted["sources"]["launcher"])
    parent = Path(plan["parent"])
    info = parent.stat()
    require(parent.is_absolute() and parent.resolve(strict=True) == parent and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "bound private existing capture parent required")
    deadlines = original_deadlines(started_ns)
    directory = parent / ("startup-launcher-" + plan["operation_id"])
    directory.mkdir(mode=0o700, exist_ok=False)
    intent = {"schema": "motionwright.startup-launch-preparation/1", "started_ns": started_ns,
              "deadlines": deadlines, "manifest_sha256": admitted["manifest"]["sha256"],
              "workflow": admitted["workflow"], "operation_id": plan["operation_id"],
              "status": "PREPARATION_ONLY", "formal_acceptance": False, "automatic_retry": False}
    _write_new(directory / "preparation.intent.json", canonical(intent))
    _write_new(directory / "workflow.git.original", base64.b64decode(admitted["workflow"]["original_base64"], validate=True))
    _write_new(directory / "manifest.original", base64.b64decode(admitted["manifest"]["original_base64"], validate=True))
    _write_new(directory / "plan-template.original", canonical(admitted["plan_template"]["value"]))
    plan_raw = canonical(plan)
    _write_new(directory / "outer-plan.json", plan_raw)
    original_deadlines(started_ns)
    context = {"schema": SCHEMA, "repo": str(repo), "started_ns": started_ns, "deadlines": deadlines,
               "launcher": launcher, "workflow": admitted["workflow"], "manifest": admitted["manifest"],
               "plan_template": admitted["plan_template"], "plan_sha256": digest(plan_raw),
               "environment": environment}
    return {"context": context, "plan_path": str(directory / "outer-plan.json"),
            "directory": str(directory), "core_base64": base64.b64encode(admitted["source_bytes"]["core"]).decode()}


def _load_core(raw, source):
    require(type(raw) is bytes and len(raw) <= 262144 and digest(raw) == source["sha256"] == CORE_SOURCE_SHA256,
            "exact prepared core bytes required")
    name = "_startup_launcher_core_" + source["sha256"]
    module = types.ModuleType(name); module.__file__ = source["path"]
    sys.modules[name] = module
    try:
        exec(compile(raw, source["path"], "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    require(module.OPERATION_NS == 24 * NS and module.CLEANUP_NS == 32 * NS,
            "core budget contract differs")
    return module


def _birth_equal(left, right):
    return all(type(left.get(key)) is int and type(right.get(key)) is int and left[key] == right[key]
               for key in ("pid", "start_ticks", "parent_pid", "uid"))


def _spawn_outer(prepared, deadline_ns):
    require(time.monotonic_ns() < deadline_ns, "no outer launch after original D")
    _require_linux()
    pairs, endpoints = [], []
    try:
        for _ in range(4):
            pair = _pipe(); pairs.append(pair); endpoints.extend(pair)
        (out_r, out_w), (err_r, err_w), (context_r, context_w), (gate_r, gate_w) = pairs
        context_fd = context_r.fd
        context_identity = _pipe_identity(context_fd, os.O_RDONLY)
        source = _sources(Path(prepared["context"]["repo"]), prepared["context"]["manifest"]["value"])["outer"]
        executable = prepared["context"]["launcher"]["executable"]["path"]
        argv = [executable, "-I", "-B", source["path"], "--plan", prepared["plan_path"],
                "--launch-context-fd", str(context_fd), "--started-ns", str(prepared["context"]["started_ns"])]
        pid = os.fork()
    except BaseException:
        for item in endpoints:
            item.close()
        raise
    if pid == 0:
        try:
            _close_in_child({out_w.fd, err_w.fd, context_r.fd, gate_r.fd})
            os.dup2(out_w.fd, 1); os.dup2(err_w.fd, 2)
            null = os.open(os.devnull, os.O_RDONLY)
            if null != 0:
                os.dup2(null, 0); os.close(null)
            while time.monotonic_ns() < deadline_ns:
                ready, _, _ = select.select([gate_r.fd], [], [], .02)
                if ready:
                    require(os.read(gate_r.fd, 1) == b"G", "outer release absent")
                    break
            else:
                raise LauncherError("outer release original deadline")
            gate_r.close()
            os.setsid()
            os.set_inheritable(context_r.fd, True)
            _close_in_child({0, 1, 2, context_r.fd})
            require(time.monotonic_ns() < deadline_ns, "outer exec original deadline")
            os.execve(executable, argv, prepared["context"]["environment"])
        finally:
            os._exit(125)
    child = Child(pid, out_r, gate_w, None, None)
    child.extra_descriptors = [item for item in endpoints if item not in (out_r, gate_w)]
    result = {"child": child, "stderr": err_r, "context_writer": context_w,
              "context_fd": context_fd, "context_identity": context_identity, "argv": argv,
              "source": source, "endpoints": endpoints, "binding": None, "errors": []}
    try:
        closes = [out_w.close(), err_w.close(), context_r.close(), gate_r.close()]
        require(all(closes), "outer parent endpoint close uncertain")
        before = _kernel_stamp(pid)
        child.pidfd = OwnedFD(os.pidfd_open(pid), "outer-pidfd")
        require(_same_stamp(before, _kernel_stamp(pid)) and before["parent_pid"] == os.getpid()
                and before["uid"] == os.getuid(), "outer retained handle identity changed")
        child.identity, child.authenticated = before, True
        child.release(deadline_ns)
    except BaseException as error:
        child.error("outer start unresolved: " + type(error).__name__)
        child.gate_fd.close()
    return result


def _post_exec_binding(outer, prepared):
    child, argv = outer["child"], outer["argv"]
    current = _kernel_stamp(child.pid)
    require(child.authenticated and _birth_equal(child.identity, current), "outer birth identity changed")
    raw = _proc_bytes(child.pid, "cmdline", 65536)
    expected_argv = b"\0".join(os.fsencode(part) for part in argv) + b"\0"
    if raw != expected_argv:
        return None
    executable = prepared["context"]["launcher"]["executable"]
    require(os.readlink(f"/proc/{child.pid}/exe") == executable["path"]
            and current["session_id"] == child.pid and current["process_group_id"] == child.pid
            and _same_stamp(current, _kernel_stamp(child.pid)), "actual outer executable/session mismatch")
    return {**current, "source": outer["source"], "executable": executable, "cmdline_sha256": digest(raw)}


def observe_outer(outer, prepared, core, deadline_ns, *, clock=time.monotonic_ns):
    child = outer["child"]
    pump = core.CapturePump({child.result.fd: OUTER_STDOUT_LIMIT, outer["stderr"].fd: OUTER_STDERR_LIMIT},
                            types.SimpleNamespace(join_ns=deadline_ns))
    outer["pump"] = pump
    sent, context_raw = 0, None
    while clock() < deadline_ns:
        now = clock()
        child.poll(now)
        if outer["binding"] is None and child.wait is None and not child.errors:
            try:
                outer["binding"] = _post_exec_binding(outer, prepared)
                if outer["binding"] is not None:
                    context = {**prepared["context"], "outer": outer["binding"],
                               "transport": {"fd": outer["context_fd"], "identity": outer["context_identity"]}}
                    context_raw = canonical(context)
                    require(len(context_raw) <= CONTEXT_LIMIT, "launch context byte bound")
            except (OSError, LauncherError) as error:
                child.error("outer identity unresolved: " + type(error).__name__)
        writer = outer["context_writer"]
        if context_raw is not None and writer.fd is not None and not child.errors:
            try:
                require(now < prepared["context"]["deadlines"]["operation_ns"], "context cannot be delivered after D")
                count = os.write(writer.fd, context_raw[sent:sent + 4096])
                require(count > 0, "context write returned zero")
                sent += count
                if sent == len(context_raw) and not writer.close():
                    child.error("context EOF close uncertain")
            except BlockingIOError:
                pass
            except (OSError, LauncherError) as error:
                child.error("context delivery unresolved: " + type(error).__name__)
                writer.close()
        try:
            pump.step()
        except (OSError, core.CaptureError) as error:
            child.error("outer output capture unresolved: " + type(error).__name__)
        if any(stream.truncated for stream in pump.streams.values()):
            child.error("outer output byte bound exceeded")
        if child.wait is not None and not pump.pending and child.exited_ns is not None:
            break
        if now >= deadline_ns - NS or child.errors or (now >= prepared["context"]["deadlines"]["operation_ns"] and context_raw is None):
            child.kill_once(now)
        if not pump.pending:
            select.select([], [], [], min(.01, max(0, (deadline_ns - clock()) / NS)))
    stdout, stderr = pump.streams[child.result.fd], pump.streams[outer["stderr"].fd]
    child.prefix, child.eof, child.truncated = bytearray(stdout.data), stdout.eof, stdout.truncated
    complete = (clock() <= deadline_ns and child.wait is not None and child.wait["exit_code"] == 0
                and child.exited_ns is not None and stdout.eof and stderr.eof and not stdout.truncated and not stderr.truncated
                and not child.errors and not child.signal_attempts and outer["binding"] is not None
                and context_raw is not None and sent == len(context_raw) and outer["context_writer"].fd is None
                and outer["context_writer"].close_error is None)
    return {"complete": complete, "stdout": bytes(stdout.data), "stderr": bytes(stderr.data),
            "stdout_metadata": stdout.metadata("outer.stdout.original"), "stderr_metadata": stderr.metadata("outer.stderr.original"),
            "context_bytes_sent": sent, "context_sha256": digest(context_raw) if context_raw is not None else None,
            "binding": outer["binding"], "child": child.metadata()}


def external_payload(capture, prepared):
    phase, value, reason = b"", None, None
    try:
        require(capture["stdout_metadata"]["eof"] and not capture["stdout_metadata"]["truncated"], "outer stdout is only a prefix")
        value = strict_json(capture["stdout"], OUTER_STDOUT_LIMIT)
        encoded = value["phase_original_base64"]
        require(type(encoded) is str and len(encoded) <= 4 * ((PHASE_LIMIT + 2) // 3), "phase encoding byte bound")
        phase = base64.b64decode(encoded, validate=True)
        require(len(phase) <= PHASE_LIMIT, "phase byte bound")
        metadata = value["collector"]["phase"]
        require(type(metadata) is dict and type(metadata.get("bytes")) is int and metadata["bytes"] == len(phase)
                and metadata.get("sha256") == digest(phase) and type(metadata.get("eof")) is bool
                and type(metadata.get("truncated")) is bool and value.get("phase_durable") is False,
                "outer phase prefix identity mismatch")
        phase_complete = metadata["eof"] and not metadata["truncated"] and not metadata.get("errors")
        require(value.get("start_monotonic_ns") == prepared["context"]["started_ns"]
                and type(value.get("start_monotonic_ns")) is int, "outer changed original clock")
        expected = prepared["context"]["deadlines"]
        for key, original in (("operation_deadline_ns", "operation_ns"), ("join_deadline_ns", "join_ns"),
                              ("cleanup_deadline_ns", "cleanup_ns")):
            require(type(value.get(key)) is int and value[key] == expected[original], "outer changed inherited deadline")
    except (LauncherError, KeyError, ValueError, TypeError) as error:
        reason, phase_complete = str(error), False
    complete = capture["complete"] and value is not None and value.get("status") == "CAPTURE_CUSTODY_COMPLETE" and phase_complete and reason is None
    record = {"schema": "motionwright.startup-launcher-external-custody/1", "status": "OUTER_OBSERVED_PERSISTENCE_PENDING",
              "scope": prepared["context"]["manifest"]["value"]["scope"],
              "started_ns": prepared["context"]["started_ns"], "deadlines": prepared["context"]["deadlines"],
              "workflow": prepared["context"]["workflow"], "manifest_sha256": prepared["context"]["manifest"]["sha256"],
              "outer": {key: item for key, item in capture.items() if key not in ("stdout", "stderr")},
              "phase": {"bytes": len(phase), "sha256": digest(phase), "complete_original": bool(phase_complete),
                        "retained_prefix": not bool(phase_complete), "outer_claimed_durable": False},
              "outer_semantic_complete": bool(complete), "reason": reason,
              "this_record_claims_writer_completion": False, "formal_acceptance": False, "automatic_retry": False}
    raw_record = canonical(record)
    require(len(raw_record) <= CUSTODY_RECORD_LIMIT, "external custody record byte bound")
    files = {"outer.stdout.original": capture["stdout"], "outer.stderr.original": capture["stderr"],
             "phase.original": phase, "launcher.json": raw_record}
    limits = {"outer.stdout.original": OUTER_STDOUT_LIMIT, "outer.stderr.original": OUTER_STDERR_LIMIT,
              "phase.original": PHASE_LIMIT, "launcher.json": CUSTODY_RECORD_LIMIT}
    require(all(type(raw) is bytes and len(raw) <= limits[name] for name, raw in files.items()), "external writer byte bound")
    return files, record, complete


def persist_external(core, directory, files, deadline_ns, *, clock=time.monotonic_ns):
    limits = {"outer.stdout.original": OUTER_STDOUT_LIMIT, "outer.stderr.original": OUTER_STDERR_LIMIT,
              "phase.original": PHASE_LIMIT, "launcher.json": CUSTODY_RECORD_LIMIT}
    require(type(files) is dict and set(files) == set(limits) and all(type(raw) is bytes and len(raw) <= limits[name]
            for name, raw in files.items()), "closed bounded external persistence set required before fork")
    expected = {name: {"bytes": len(raw), "sha256": digest(raw)} for name, raw in files.items()}
    def persist(fd):
        Path(directory).mkdir(mode=0o700, exist_ok=False)
        # The reviewed generic writer performs actual fsync and byte readback.
        # This separate namespace does not use or redefine its three-stream API.
        core._persist(Path(directory), files, fd)
    handle = _spawn_worker(persist, deadline_ns)
    observed = observe_worker(handle, deadline_ns, limit=RESULT_LIMIT, clock=clock)
    value = None
    if observed:
        try:
            value = strict_json(bytes(handle.prefix), RESULT_LIMIT)
            require(set(value) == {"complete", "files"} and value["complete"] is True
                    and type(value["files"]) is dict and set(value["files"]) == set(expected), "external writer receipt shape")
            for name, item in value["files"].items():
                require(type(item) is dict and set(item) == {"bytes", "sha256"} and type(item["bytes"]) is int
                        and type(item["sha256"]) is str and item == expected[name], "external writer readback mismatch")
        except (LauncherError, ValueError, TypeError) as error:
            handle.error("external writer receipt rejected: " + type(error).__name__)
            observed = False
    handle.close()
    return {"status": "PERSISTED_AND_READ_BACK" if observed and not handle.errors else "UNKNOWN",
            "directory": str(directory), "files": expected, "writer": handle.metadata(),
            "writer_receipt": value, "writer_original_base64": base64.b64encode(bytes(handle.prefix)).decode(),
            "namespace": "EXTERNAL_OUTER_AND_PHASE_CUSTODY_NOT_A2_ORIGINALS"}


def run_launcher(repo, environment=None, *, started_ns=None, clock=time.monotonic_ns):
    # First operation: no manifest/Git/directory/persistence/spawn precedes t0.
    started_ns = clock() if started_ns is None else started_ns
    deadlines = original_deadlines(started_ns, now_ns=clock())
    result = {"schema": "motionwright.startup-launcher-result/1", "status": "UNKNOWN", "started_ns": started_ns,
              "deadlines": deadlines, "phase": "ADMISSION", "formal_acceptance": False, "automatic_retry": False,
              "installed_result": "NOT_EVALUATED_BY_LAUNCHER", "human_review_authenticated": False, "same_uid_security_boundary": False,
              "process_tree_closed": False, "original_prefixes": {}, "errors": []}
    prep_handle, prepared, outer = None, None, None
    try:
        inherited = dict(os.environ) if environment is None else dict(environment)
        clean = allowlisted_environment(inherited)
        workflow = _workflow_identity(inherited)
        require(type(repo) is str or isinstance(repo, Path), "repository path required")
        repo = Path(repo).absolute()
        require(repo == Path(__file__).absolute().parents[1], "actual launcher must belong to selected repository")
        _require_linux()
        result["phase"] = "PREPARATION"
        launcher_pid = os.getpid()
        prep_handle = _spawn_worker(lambda _fd: _prepare(repo, workflow, clean, started_ns, launcher_pid), deadlines["operation_ns"])
        complete = observe_worker(prep_handle, deadlines["operation_ns"], clock=clock)
        raw = bytes(prep_handle.prefix)
        result["preparation"] = prep_handle.metadata()
        result["original_prefixes"]["preparation"] = base64.b64encode(raw).decode()
        prep_handle.close()
        require(complete and not prep_handle.errors, "preparation UNKNOWN; no outer successor")
        packet = strict_json(raw, PREPARATION_LIMIT)
        require(set(packet) == {"ok", "value"} and packet["ok"] is True, "preparation did not admit source")
        prepared = packet["value"]
        require(type(prepared) is dict and set(prepared) == {"context", "plan_path", "directory", "core_base64"},
                "closed preparation result required")
        require(prepared["context"]["started_ns"] == started_ns and type(prepared["context"]["started_ns"]) is int
                and prepared["context"]["deadlines"] == deadlines, "preparation cannot renew original clock")
        result["scope"] = prepared["context"]["manifest"]["value"]["scope"]
        core_source = _sources(repo, prepared["context"]["manifest"]["value"])["core"]
        core = _load_core(base64.b64decode(prepared["core_base64"], validate=True), core_source)
        core_budget = core.Budget(started_ns)
        require(core_budget.operation_ns == deadlines["operation_ns"] and core_budget.join_ns == deadlines["join_ns"]
                and core_budget.cleanup_ns == deadlines["cleanup_ns"], "inherited core Budget mismatch")
        original_deadlines(started_ns)
        result["phase"] = "OUTER"
        outer = _spawn_outer(prepared, deadlines["operation_ns"])
        capture = observe_outer(outer, prepared, core, deadlines["custody_ns"] - 4 * NS, clock=clock)
        outer["child"].close()
        capture["child"] = outer["child"].metadata()
        capture["complete"] &= not bool(outer["child"].errors)
        result["original_prefixes"].update(outer_stdout=base64.b64encode(capture["stdout"]).decode(),
                                          outer_stderr=base64.b64encode(capture["stderr"]).decode())
        files, record, semantic_complete = external_payload(capture, prepared)
        result["outer"] = record
        result["phase"] = "EXTERNAL_PERSISTENCE"
        require(clock() < deadlines["custody_ns"] - NS, "external custody has no original time remaining")
        persistence = persist_external(core, Path(prepared["directory"]) / "external", files,
                                       deadlines["custody_ns"] - NS, clock=clock)
        result["persistence"] = persistence
        if semantic_complete and persistence["status"] == "PERSISTED_AND_READ_BACK" and clock() < deadlines["custody_ns"]:
            result["status"] = "EXTERNAL_CAPTURE_PERSISTED"
        else:
            result["errors"].append("outer semantic/EOF/terminal/persistence closure incomplete")
        result["phase"] = "OUTPUT_PENDING"
    except BaseException as error:
        result["errors"].append(type(error).__name__ + ": " + str(error)[:256])
        retained = getattr(error, "custody", None)
        if isinstance(retained, Child):
            if clock() < deadlines["custody_ns"] - NS:
                retained.kill_once(clock())
                observe_worker(retained, deadlines["custody_ns"] - NS, clock=clock)
            result["retained_start_error"] = retained.metadata()
            result["original_prefixes"]["retained_start_error"] = base64.b64encode(bytes(retained.prefix)).decode()
            retained.close()
    finally:
        if prep_handle is not None:
            prep_handle.close()
            result["preparation"] = prep_handle.metadata()
        if outer is not None:
            child = outer["child"]
            pump = outer.get("pump")
            if child.wait is None and clock() < deadlines["custody_ns"] - NS:
                child.kill_once(clock())
                while clock() < deadlines["custody_ns"] - NS and child.wait is None:
                    child.poll(clock()); select.select([], [], [], .01)
            child.close()
            if pump is not None:
                for fd, stream in pump.streams.items():
                    label = "outer_stdout" if fd == child.result.original_fd else "outer_stderr"
                    result["original_prefixes"][label] = base64.b64encode(bytes(stream.data)).decode()
            if child.errors:
                result["status"] = "UNKNOWN"
            result["retained_outer"] = child.metadata()
        result["finished_monotonic_ns"] = clock()
        if result["finished_monotonic_ns"] >= deadlines["custody_ns"]:
            result["status"] = "UNKNOWN"
            result["errors"].append("fixed external custody deadline expired")
    return result


def emit_result(result, output_fd=1, *, clock=time.monotonic_ns):
    deadline = result["deadlines"]["custody_ns"]
    if clock() >= deadline:
        return 124
    # Evidence files already contain the full captured prefixes when persistence
    # succeeded. Otherwise the bounded final channel carries retained prefixes.
    value = dict(result)
    if value.get("persistence", {}).get("status") == "PERSISTED_AND_READ_BACK":
        value["original_prefixes"] = {"retained_in": value["persistence"]["directory"]}
    raw = canonical(value)
    if len(raw) > 4 * 1024 * 1024:
        return 124
    handle = None
    try:
        before = os.fstat(output_fd)
        require(stat.S_ISFIFO(before.st_mode) or stat.S_ISREG(before.st_mode) or stat.S_ISSOCK(before.st_mode),
                "bounded capture output sink required")
        def output(_fd):
            after = os.fstat(output_fd)
            require((before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)) ==
                    (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)), "output sink identity changed")
            os.set_blocking(output_fd, False)
            _write_pipe(output_fd, raw, deadline)
            return {"bytes": len(raw), "sha256": digest(raw)}
        handle = _spawn_worker(output, deadline, keep_fds=(output_fd,))
        complete = observe_worker(handle, deadline, limit=RESULT_LIMIT, clock=clock, cleanup_reserve_ns=100_000_000)
        if complete:
            receipt = strict_json(bytes(handle.prefix), RESULT_LIMIT)
            complete = receipt == {"ok": True, "value": {"bytes": len(raw), "sha256": digest(raw)}}
        handle.close()
        return 0 if complete and not handle.errors and result["status"] == "EXTERNAL_CAPTURE_PERSISTED" else 124
    except BaseException as error:
        retained = getattr(error, "custody", None)
        if isinstance(retained, Child):
            handle = retained
        if handle is not None and clock() < deadline:
            handle.kill_once(clock())
            observe_worker(handle, deadline, limit=RESULT_LIMIT, clock=clock, cleanup_reserve_ns=100_000_000)
        return 124
    finally:
        if handle is not None:
            handle.close()


class SilentParser(argparse.ArgumentParser):
    def error(self, message):
        raise LauncherError("invalid launcher arguments: " + str(message)[:160])

    def exit(self, status=0, message=None):
        raise LauncherError("launcher parser cannot print or exit outside custody")


def main():
    started_ns = time.monotonic_ns()
    parser = SilentParser(description=__doc__, add_help=False)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    result = run_launcher(args.repo, started_ns=started_ns)
    return emit_result(result)


if __name__ == "__main__":
    try:
        _code = main()
    except BaseException:
        _code = 124
    # No implicit Python stream flush may extend the original external budget.
    os._exit(_code)
