"""Select native work only for a reviewed, exact portable-CAS source change.

This gate runs fast contracts before publishing any selection output. A native
job not selected by this gate has not passed a native test. Required Rust and
SQLite integration still belongs to CI on the same actual checkout.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

SCHEMA = "motionwright.native-impact/1"
REPOSITORY = "seradotcom/motionwright"
SELECTOR_PATH = "tooling/ci_native_impact.py"
TEST_PATH = "tooling/tests/test_ci_native_impact.py"
CI_PATH = ".github/workflows/ci.yml"
GIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_FILE = 8 * 1024 * 1024
MAX_SOURCE = 64 * 1024 * 1024
MAX_FILES = 5000
MAX_JSON = 2 * 1024 * 1024
POLICIES = ("source_policy.py", "acceptance_policy.py", "creative_status_policy.py")
DECISIONS = {"NOT_SELECTED_REVIEWED_CAS_PROFILE", "REQUIRED_UNREVIEWED_CHANGE",
             "REQUIRED_FULL_EVENT"}
G1_COMMAND = ("cargo test --locked -p motionwright-domain -p motionwright-storage "
              "-p motionwright-service -p motionwright-native --all-targets")

WORKFLOWS = {
    ".github/workflows/native-render-e2e.yml": {
        "key": "render",
        "job": "real-native-render",
        "title": "Native Motion Canvas production",
        "original_sha256": "41fe8c456f75217f92f265c26b3bdf86ae3ae79fafda84b573c1029908701783",
        "patterns": [
            "test_native_cli_failure_redaction.py",
            "test_native_render_public_artifact.py",
            "test_native_render_public_failure_category.py"
        ]
    },
    ".github/workflows/native-blender-e2e.yml": {
        "key": "blender",
        "job": "native-blender-roundtrip",
        "title": "Native Blender round-trip",
        "original_sha256": "37d6110f40d157c944a10775f45266af463bece70fc45ae45312f1f101412eee",
        "patterns": [
            "test_native_scene_public_artifact.py"
        ]
    },
    ".github/workflows/native-cross-app-recovery.yml": {
        "key": "cross_app",
        "job": "real-blender-motion-restart",
        "title": "Native cross-app selective recovery",
        "original_sha256": "87acf87ab55c5d5bf3119820eb9beb3348296d58b78691d419e75650b4cb4b2b",
        "patterns": [
            "test_cross_app_public_artifact.py",
            "test_cross_app_demo_report.py"
        ]
    },
    ".github/workflows/native-manim-e2e.yml": {
        "key": "manim",
        "job": "native-manim-roundtrip",
        "title": "Native Manim Community round-trip",
        "original_sha256": "207c0a7441ee6beebfebf3c697a48e7385790ac79dc562fe6946d4f2632e37f9",
        "patterns": [
            "test_native_scene_public_artifact.py"
        ]
    },
    ".github/workflows/native-mlt-sequence-e2e.yml": {
        "key": "mlt",
        "job": "native-mlt-sequence",
        "title": "Native Motionwright MLT multisegment timeline",
        "original_sha256": "14a9a8f2138286fd3c03b9bf91660b0d39b257623eaf370be37110a87c87f167",
        "patterns": [
            "test_native_cli_failure_redaction.py",
            "test_native_mlt_public_artifact.py"
        ]
    }
}
ENGINE_PATHS = tuple([
    "tooling/ci_native_impact.py",
    "tooling/tests/test_ci_native_impact.py",
    ".github/workflows/ci.yml",
    ".github/workflows/native-render-e2e.yml",
    ".github/workflows/native-blender-e2e.yml",
    ".github/workflows/native-cross-app-recovery.yml",
    ".github/workflows/native-manim-e2e.yml",
    ".github/workflows/native-mlt-sequence-e2e.yml"
])
MOVED_CONTRACTS = {
    ".github/workflows/native-render-e2e.yml": [
        [
            "      # native-impact-moved-render-0\n\n",
            "      - name: Verify v0.1 native CLI failures never expose private job receipts\n        run: python3 -m unittest discover -s tooling/tests -p 'test_native_cli_failure_redaction.py' -v\n\n"
        ],
        [
            "      # native-impact-moved-render-1\n\n",
            "      - name: Reject raw Broker session or forged native parity in public evidence\n        run: |\n          python3 -m unittest discover -s tooling/tests -p 'test_native_render_public_artifact.py' -v\n          python3 -m unittest discover -s tooling/tests -p 'test_native_render_public_failure_category.py' -v\n\n"
        ]
    ],
    ".github/workflows/native-blender-e2e.yml": [
        [
            "      # native-impact-moved-blender-0\n\n",
            "      - name: Reject unsafe public Blender and Manim scene media\n        run: python3 -m unittest discover -s tooling/tests -p 'test_native_scene_public_artifact.py' -v\n\n"
        ]
    ],
    ".github/workflows/native-cross-app-recovery.yml": [
        [
            "          # native-impact-moved-cross-app-demo\n",
            "          python3 -m unittest discover -s tooling/tests -p 'test_cross_app_demo_report.py' -v\n"
        ],
        [
            "      # native-impact-moved-cross_app-0\n\n",
            "      - name: Assert v0.1 public cross-app artifacts cannot leak owner receipts\n        run: python3 -m unittest discover -s tooling/tests -p 'test_cross_app_public_artifact.py' -v\n\n"
        ]
    ],
    ".github/workflows/native-manim-e2e.yml": [
        [
            "      # native-impact-moved-manim-0\n\n",
            "      - name: Reject unsafe public Blender and Manim scene media\n        run: python3 -m unittest discover -s tooling/tests -p 'test_native_scene_public_artifact.py' -v\n\n"
        ]
    ],
    ".github/workflows/native-mlt-sequence-e2e.yml": [
        [
            "      # native-impact-moved-mlt-0\n\n",
            "      - name: Verify v0.1 native CLI failures never expose private job receipts\n        run: python3 -m unittest discover -s tooling/tests -p 'test_native_cli_failure_redaction.py' -v\n\n"
        ],
        [
            "      # native-impact-moved-mlt-1\n\n",
            "      - name: Reject private MLT CI uploads and false success before native execution\n        run: python3 -m unittest discover -s tooling/tests -p 'test_native_mlt_public_artifact.py' -v\n\n"
        ]
    ]
}
ORIGINAL_CI_SHA256 = '26126771ca906af88eabb43d785f132cc7657a452093069aef68fd5c4f98ead0'

# These are fingerprints of the reviewed PRODUCT inventory, excluding the
# eight gate files. The six workflow payloads are checked separately after
# reversing only the gate additions and the explicitly moved fast contracts.
# Engine bytes must already exist, identically, in legitimate base/head/merge.
# No file embeds its own digest and initial gate adoption cannot skip itself.
PROFILE = {
    "id": "portable-cas-preflight-exact-v1",
    "reviewed_before_commit": "a6a6e98349dd65298a7d1612f3b45aad03443fae",
    "reviewed_after_commit": "20b3604bf5a66c9243dc46a9eb0f87335e79f71a",
    "before_product": "846cb35b6660ee5f85d4ca41a1b5eaddf0a975459b2b9b1591c91fa83b711f61",
    "after_product": "b88cc75f750dd2aaf51e7a0e21f536764eb99b78ab40b513180c5353ed0db7d0",
    "changes": {
        "crates/storage/src/portability.rs": (
            ("100644", "blob", "04fc60b8aa0ca16a00fa9ad9aaa4d898c472ba3c", 49724),
            ("100644", "blob", "49805b7d0c8d0995033d073fda660e2b04e220ed", 53711)),
        "docs/reliability/portable-cas-publication-preflight.md": (
            None,
            ("100644", "blob", "477b8d684a248981ffb20ec921e71846ac4d1fba", 1876)),
    },
}


class Refused(ValueError):
    """A source, event, contract or output failed an explicit invariant."""


class Uncertain(Refused):
    """An original child did not have a complete terminal observation."""


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_object(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode()
                        + b"\0" + data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refused("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise Refused("non-finite JSON constant: " + value)

    if len(data) > MAX_JSON:
        raise Refused("JSON budget exceeded")
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=invalid_constant)


def token(value):
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def regular_bytes(path, limit=MAX_FILE):
    path = Path(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or path.is_symlink()
            or getattr(before, "st_file_attributes", 0) & 0x400):
        raise Refused("input is not a regular file: " + str(path))
    if before.st_size > limit:
        raise Refused("input exceeds its byte budget: " + str(path))
    with path.open("rb") as stream:
        data = stream.read(min(limit + 1, before.st_size + 1))
        opened = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(data) != before.st_size or token(before) != token(opened)
            or token(before) != token(after)):
        raise Refused("input changed while reading: " + str(path))
    return data, {"bytes": len(data), "sha256": digest(data),
                  "file_identity": list(token(before))}


def safe_path(value):
    if (not isinstance(value, str) or not value or "\\" in value or "\0" in value
            or ":" in value or any(ord(c) < 32 for c in value)
            or value.startswith("/") or value.endswith("/")):
        raise Refused("unsafe source path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.casefold() == ".git" for p in parts):
        raise Refused("unsafe source path component")
    if str(PurePosixPath(value)) != value:
        raise Refused("noncanonical source path")
    return value


def parse_inventory(data):
    if len(data) > 4 * 1024 * 1024 or not data.endswith(b"\0"):
        raise Refused("invalid or oversized Git inventory")
    rows, folded, prefixes = {}, set(), {}
    for raw in data[:-1].split(b"\0"):
        header, separator, path_bytes = raw.partition(b"\t")
        if not separator:
            raise Refused("missing Git path separator")
        fields = header.decode("ascii").split()
        if len(fields) != 4:
            raise Refused("invalid Git inventory fields")
        mode, kind, oid, size_text = fields
        path = safe_path(path_bytes.decode("utf-8"))
        if (path in rows or path.casefold() in folded
                or mode not in ("100644", "100755") or kind != "blob"
                or not GIT_SHA.fullmatch(oid) or not size_text.isdigit()):
            raise Refused("duplicate or unsupported Git source entry")
        for length in range(1, len(path.split("/")) + 1):
            prefix = "/".join(path.split("/")[:length])
            if prefixes.setdefault(prefix.casefold(), prefix) != prefix:
                raise Refused("case-colliding source directory")
        size = int(size_text)
        if size > MAX_FILE:
            raise Refused("Git file exceeds source budget")
        rows[path] = (mode, kind, oid, size)
        folded.add(path.casefold())
    if len(rows) > MAX_FILES or sum(r[3] for r in rows.values()) > MAX_SOURCE:
        raise Refused("Git source exceeds total budget")
    return rows


def rebuilt_tree(rows):
    root = {}
    for path, (mode, kind, oid, _size) in sorted(rows.items()):
        safe_path(path)
        if mode not in ("100644", "100755") or kind != "blob":
            raise Refused("unsupported tree entry")
        current = root
        parts = path.split("/")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
            if not isinstance(current, dict):
                raise Refused("file/directory collision")
        if parts[-1] in current:
            raise Refused("duplicate tree entry")
        current[parts[-1]] = (mode, oid)

    def build(directory):
        raw = bytearray()
        for name, value in sorted(directory.items(),
                                  key=lambda pair: (pair[0] + ("/" if isinstance(
                                      pair[1], dict) else "")).encode("utf-8")):
            mode, oid = ("40000", build(value)) if isinstance(value, dict) else value
            if not GIT_SHA.fullmatch(oid):
                raise Refused("invalid object identity")
            raw.extend(mode.encode() + b" " + name.encode("utf-8")
                       + b"\0" + bytes.fromhex(oid))
        return git_object("tree", bytes(raw))
    return build(root)


def product_fingerprint(rows, engine_paths=ENGINE_PATHS):
    closed = [[path, *row] for path, row in sorted(rows.items())
              if path not in engine_paths]
    return digest(json.dumps(closed, ensure_ascii=True,
                             separators=(",", ":")).encode("utf-8"))


def parse_commit(data, expected):
    if not GIT_SHA.fullmatch(expected) or git_object("commit", data) != expected:
        raise Refused("raw commit identity mismatch")
    header = data.split(b"\n\n", 1)[0].split(b"\n")
    trees = [line[5:].decode("ascii") for line in header if line.startswith(b"tree ")]
    parents = [line[7:].decode("ascii") for line in header if line.startswith(b"parent ")]
    if (len(trees) != 1 or not GIT_SHA.fullmatch(trees[0]) or len(parents) > 16
            or any(not GIT_SHA.fullmatch(p) for p in parents)
            or len(set(parents)) != len(parents)):
        raise Refused("invalid raw commit header")
    return {"sha": expected, "tree": trees[0], "parents": parents,
            "raw_sha256": digest(data)}


def remove_gate_block(text, name):
    begin = re.compile(r"(?m)^ *# native-impact-" + re.escape(name) + r"-begin\n")
    end = re.compile(r"(?m)^ *# native-impact-" + re.escape(name) + r"-end\n")
    starts, ends = list(begin.finditer(text)), list(end.finditer(text))
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].end():
        raise Refused("missing or duplicate gate boundary")
    return text[:starts[0].start()] + text[ends[0].end():]


def reviewed_workflow_bytes(path, data):
    # Only reconstruct known gate additions, never remove arbitrary job bodies.
    text = data.decode("utf-8")
    if "\r" in text:
        raise Refused("workflow is not original LF source")
    if path == CI_PATH:
        return remove_gate_block(text, "tests").encode("utf-8")
    if path not in WORKFLOWS:
        raise Refused("workflow has no reviewed native consumer")
    for name in ("preflight", "dependency", "triggers"):
        text = remove_gate_block(text, name)
    for marker, original in MOVED_CONTRACTS[path]:
        if text.count(marker) != 1:
            raise Refused("moved contract boundary differs")
        text = text.replace(marker, original, 1)
    return text.encode("utf-8")


def review_workflows(root):
    results = {}
    for path in (CI_PATH, *WORKFLOWS):
        data, actual = regular_bytes(root / path)
        expected = ORIGINAL_CI_SHA256 if path == CI_PATH else WORKFLOWS[path]["original_sha256"]
        try:
            reconstructed = reviewed_workflow_bytes(path, data)
            matches = digest(reconstructed) == expected
        except (Refused, UnicodeError):
            reconstructed, matches = b"", False
        results[path] = {"actual": actual, "reviewed_payload_sha256": expected,
                         "reconstructed_sha256": digest(reconstructed),
                         "reviewed_payload_matches": matches}
    return results


def hardened_env(source=None):
    env = dict(os.environ if source is None else source)
    for name in list(env):
        if name.upper().startswith("GIT_") or name.upper().startswith("PYTHON"):
            del env[name]
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor",
               GIT_CONFIG_VALUE_0="false", PYTHONDONTWRITEBYTECODE="1")
    return env


def new_json(path, value):
    data = (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False)
            + "\n").encode("utf-8")
    if len(data) > 16 * 1024 * 1024:
        raise Refused("receipt budget exceeded")
    path = Path(path)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    got, record = regular_bytes(path, 16 * 1024 * 1024)
    if got != data:
        raise Refused("receipt readback mismatch")
    return {"path": str(path), **record}


def stop_child(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def capture_process(argv, cwd, env, folder, timeout, limit=8 * 1024 * 1024):
    """Retain intent/outcome before parsing; timeout or truncated streams fail."""
    folder.mkdir()
    intent = {"at_utc": utc(), "argv": [str(x) for x in argv], "cwd": str(cwd),
              "state": "UNKNOWN_UNTIL_OUTCOME", "timeout_seconds": timeout,
              "stream_byte_limit": limit,
              "environment": {k: env[k] for k in sorted(env) if k.startswith(
                  ("GIT_", "MW_NATIVE_", "PYTHONDONTWRITE")) or k in ("TMP", "TEMP", "TMPDIR")}}
    new_json(folder / "intent.json", intent)
    flags = {"start_new_session": True} if os.name == "posix" else {}
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)
    failures, writers = [], []
    started = time.monotonic()

    def drain(pipe, destination):
        total = 0
        try:
            with destination.open("xb") as stream:
                while True:
                    chunk = pipe.read(65536)
                    if not chunk:
                        break
                    if total + len(chunk) > limit:
                        retained = max(0, limit - total)
                        stream.write(chunk[:retained])
                        failures.append("stream byte budget exceeded")
                        stop_child(process)
                        break
                    stream.write(chunk)
                    total += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ValueError) as error:
            failures.append(type(error).__name__)
            stop_child(process)
        finally:
            pipe.close()

    for name in ("stdout", "stderr"):
        worker = threading.Thread(target=drain, args=(getattr(process, name),
                                  folder / (name + ".bin")), daemon=True)
        worker.start()
        writers.append(worker)
    timed_out = False
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_child(process)
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
    for worker in writers:
        worker.join(timeout=3)
    alive = any(worker.is_alive() for worker in writers)
    outcome = {"at_utc": utc(), "exit_code": code, "timed_out": timed_out,
               "writer_alive": alive, "capture_errors": failures,
               "elapsed_seconds": round(time.monotonic() - started, 6)}
    if not alive:
        for name in ("stdout", "stderr"):
            path = folder / (name + ".bin")
            if path.is_file():
                _data, record = regular_bytes(path, limit)
                outcome[name] = record
    new_json(folder / "outcome.json", outcome)
    if timed_out or alive or code is None:
        raise Uncertain("child outcome incomplete; no retry: " + str(folder))
    if failures or "stdout" not in outcome or "stderr" not in outcome:
        raise Refused("child streams incomplete: " + str(folder))
    return outcome


class Commands:
    def __init__(self, root, output):
        self.root, self.output = Path(root), Path(output)
        self.env = hardened_env()
        found = shutil.which("git", path=self.env.get("PATH"))
        if found is None:
            raise Refused("Git executable unavailable")
        self.git_path = Path(found).resolve(strict=True)
        self.python = Path(sys.executable).resolve(strict=True)
        if any(p.is_relative_to(self.root) for p in (self.git_path, self.python)):
            raise Refused("tool resolves into source")
        self.env["PATH"] = str(self.git_path.parent) + os.pathsep + self.env.get("PATH", "")
        self.index, self.records = 0, []
        self.output.mkdir()
        self.tools = {name: {"path": str(p), **regular_bytes(p, 128 * 1024 * 1024)[1]}
                      for name, p in (("git", self.git_path), ("python", self.python))}

    def run(self, label, argv, *, timeout=60, allowed=(0,), limit=8 * 1024 * 1024):
        if not re.fullmatch(r"[a-z0-9-]+", label):
            raise Refused("invalid command label")
        self.index += 1
        folder = self.output / ("%03d-%s" % (self.index, label))
        outcome = capture_process([str(x) for x in argv], self.root, self.env,
                                  folder, timeout, limit)
        self.records.append({"label": label, "folder": str(folder), **outcome})
        if type(outcome["exit_code"]) is not int or outcome["exit_code"] not in allowed:
            raise Refused("command failed: " + label)
        stdout, record = regular_bytes(folder / "stdout.bin", limit)
        if record != outcome["stdout"]:
            raise Refused("command stdout changed after capture")
        return stdout, outcome

    def git(self, *args, allowed=(0,)):
        return self.run("git", [self.git_path, "--no-replace-objects",
                               "-c", "core.fsmonitor=false", *args],
                        allowed=allowed)[0]

    def commit(self, sha):
        return parse_commit(self.git("cat-file", "commit", sha), sha)

    def inventory(self, commit):
        rows = parse_inventory(self.git("ls-tree", "-r", "-z", "--full-tree",
                                       "--long", commit["sha"]))
        if rebuilt_tree(rows) != commit["tree"]:
            raise Refused("complete Git inventory does not reconstruct raw tree")
        return rows

    def recheck_tools(self):
        current = {name: {"path": rec["path"], **regular_bytes(
            Path(rec["path"]), 128 * 1024 * 1024)[1]} for name, rec in self.tools.items()}
        if current != self.tools:
            raise Refused("observed command executable changed")


def snapshot_source(commands, commit, rows):
    head = commands.git("rev-parse", "HEAD").strip().decode("ascii")
    if head != commit["sha"]:
        raise Refused("checkout source differs")
    if commands.git("status", "--porcelain", "--untracked-files=all", "--ignored=matching"):
        raise Refused("tracked, untracked or ignored source inputs are not clean")
    records = {}
    for path, (mode, kind, oid, size) in sorted(rows.items()):
        candidate = commands.root / path
        parent = candidate.parent
        while parent != commands.root:
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or parent.is_symlink()
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise Refused("source directory is a link or reparse point")
            parent = parent.parent
        data, record = regular_bytes(candidate)
        if len(data) != size or git_object(kind, data) != oid:
            raise Refused("physical source differs from raw Git blob: " + path)
        records[path] = {"mode": mode, "git_blob": oid, **record}
    return {"commit": commit, "files": records,
            "inventory_sha256": digest(json.dumps(
                [[path, *row] for path, row in sorted(rows.items())],
                separators=(",", ":"), ensure_ascii=True).encode())}


def positive_integer(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise Refused("invalid positive " + label)
    return int(value)


def event_context(env, workflow, root, event):
    if workflow not in WORKFLOWS:
        raise Refused("workflow is not allowlisted")
    if (env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_JOB") != "native-impact"
            or env.get("GITHUB_WORKFLOW") != WORKFLOWS[workflow]["title"]):
        raise Refused("actual workflow context differs")
    source, ref = env.get("GITHUB_SHA", ""), env.get("GITHUB_REF", "")
    if (not GIT_SHA.fullmatch(source)
            or env.get("GITHUB_WORKFLOW_SHA") != source
            or env.get("GITHUB_WORKFLOW_REF") != REPOSITORY + "/" + workflow + "@" + ref
            or not ref.startswith("refs/")
            or Path(env.get("GITHUB_WORKSPACE", "")).resolve(strict=True) != root):
        raise Refused("workflow source/ref binding differs")
    if not isinstance(event, dict) or event.get("repository", {}).get("full_name") != REPOSITORY:
        raise Refused("original event repository differs")
    result = {"repository": REPOSITORY, "workflow": workflow,
              "workflow_ref": env["GITHUB_WORKFLOW_REF"], "workflow_sha": source,
              "checkout_sha": source, "ref": ref, "event_name": env.get("GITHUB_EVENT_NAME", ""),
              "run_id": positive_integer(env.get("GITHUB_RUN_ID"), "run ID"),
              "run_attempt": positive_integer(env.get("GITHUB_RUN_ATTEMPT"), "run attempt"),
              "job_key": env["GITHUB_JOB"]}
    if not re.fullmatch(r"[a-z_]+", result["event_name"]):
        raise Refused("missing or invalid event name")
    if result["event_name"] == "pull_request":
        pr = event.get("pull_request")
        number = event.get("number")
        if (not isinstance(pr, dict) or type(number) is not int or number <= 0
                or ref != "refs/pull/%d/merge" % number):
            raise Refused("pull-request merge ref differs")
        base, head = pr.get("base"), pr.get("head")
        if (not isinstance(base, dict) or not isinstance(head, dict)
                or not GIT_SHA.fullmatch(str(base.get("sha", "")))
                or not GIT_SHA.fullmatch(str(head.get("sha", "")))
                or base.get("repo", {}).get("full_name") != REPOSITORY
                or not isinstance(base.get("ref"), str)):
            raise Refused("pull-request source identities unavailable")
        result.update(pr_number=number, base_sha=base["sha"], head_sha=head["sha"],
                      base_ref=base["ref"])
    return result


def select_profile(context, commits, inventories, workflow_review, *, profile=PROFILE,
                   engine_paths=ENGINE_PATHS, advanced_base=False):
    """Pure decision over already authenticated objects; no test result reuse."""
    decision = {"decision": "REQUIRED_FULL_EVENT", "native_required": True,
                "profile": profile["id"], "native_executed": False,
                "g1": "REQUIRED_SEPARATE_CI", "g1_command": G1_COMMAND,
                "native_pass": False, "formal_criteria_closed": 0,
                "candidate_sha": None, "observed_jobs_avoided": 0,
                "observed_minutes_saved": None, "reasons": []}
    if context["event_name"] != "pull_request":
        decision["reasons"] = ["manual/candidate/other event keeps original native scope"]
        return decision
    merge, base, head = (commits[key] for key in ("merge", "base", "head"))
    if (merge["sha"] != context["checkout_sha"] or base["sha"] != context["base_sha"]
            or head["sha"] != context["head_sha"]):
        raise Refused("selection objects differ from actual event source")
    if len(merge["parents"]) != 2 or merge["parents"][1] != head["sha"]:
        raise Refused("merge must have two raw parents with exact event head second")
    if merge["parents"][0] != base["sha"] and not advanced_base:
        raise Refused("merge first parent is neither event base nor its proven descendant")
    decision["decision"] = "REQUIRED_UNREVIEWED_CHANGE"
    reasons = decision["reasons"]
    before, after, actual = (inventories[key] for key in ("base", "head", "merge"))
    if context["base_ref"] != "main":
        reasons.append("base ref is outside reviewed main profile")
    if advanced_base:
        reasons.append("base advanced: full scope retained")
    if merge["tree"] != head["tree"] or actual != after:
        reasons.append("merge contains source composition outside reviewed head")
    if any(path not in before or before[path] != after.get(path)
           or before[path] != actual.get(path) for path in engine_paths):
        reasons.append("gate must already exist identically in base/head/merge")
    if set(workflow_review) != {CI_PATH, *WORKFLOWS} or not all(row.get("reviewed_payload_matches") is True
                                     for row in workflow_review.values()):
        reasons.append("executed workflow payload differs from reviewed consumer")
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    if changed != set(profile["changes"]):
        reasons.append("change set is outside the two reviewed CAS inputs")
    elif any((before.get(path), after.get(path)) != pair
             for path, pair in profile["changes"].items()):
        reasons.append("CAS bytes or modes differ")
    fingerprints = {"base": product_fingerprint(before, engine_paths),
                    "head": product_fingerprint(after, engine_paths),
                    "merge": product_fingerprint(actual, engine_paths)}
    decision["product_fingerprints"] = fingerprints
    if (fingerprints["base"] != profile["before_product"]
            or fingerprints["head"] != profile["after_product"]
            or fingerprints["merge"] != profile["after_product"]):
        reasons.append("baseline/product/dependency inventory differs from approved closure")
    if not reasons:
        decision.update(decision="NOT_SELECTED_REVIEWED_CAS_PROFILE", native_required=False,
                        reasons=["exact CAS bytes; protected product and workflow closure; "
                                 "engine already integrated and unchanged"])
    return decision


def declared_test_ids(path):
    data, record = regular_bytes(path)
    tree = ast.parse(data, filename=str(path))
    ids = []
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef):
            ids.extend(cls.name + "." + method.name for method in cls.body
                       if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and method.name.startswith("test_"))
    if not ids or len(set(ids)) != len(ids):
        raise Refused("empty or duplicate declared unittest inventory")
    return sorted(ids), record


def observed_test_ids(stderr):
    text = stderr.decode("utf-8", "strict").replace("\r\n", "\n").replace("\r", "\n")
    ids = []
    for line in text.splitlines():
        found = re.fullmatch(r"(test_\w+) \(([\w.]+)\) \.\.\. ok", line)
        if found:
            parts = found.group(2).split(".")
            if len(parts) < 2 or parts[-1] != found.group(1):
                raise Refused("unittest method identity differs")
            ids.append(".".join(parts[-2:]))
    if len(ids) != len(set(ids)):
        raise Refused("duplicate unittest method observation")
    return sorted(ids)


def validate_unittest_receipt(report, root, patterns, source, context, runner_hash, python):
    data, report_record = regular_bytes(report, MAX_JSON)
    result = strict_json(data)
    if (result.get("schema") != "motionwright.unittest-execution/1"
            or result.get("status") != "PASS" or type(result.get("exit_code")) is not int
            or result["exit_code"] != 0 or result.get("require_complete") is not True
            or result.get("executed_source_sha") != source["sha"]
            or result.get("tree_sha") != source["tree"]
            or result.get("runner_sha256") != runner_hash or "source_postcheck_error" in result):
        raise Refused("unittest receipt source/completeness differs")
    expected_context = {"GITHUB_SHA": context["checkout_sha"],
                        "GITHUB_WORKFLOW_REF": context["workflow_ref"],
                        "GITHUB_RUN_ID": str(context["run_id"]),
                        "GITHUB_RUN_ATTEMPT": str(context["run_attempt"]),
                        "GITHUB_JOB": context["job_key"]}
    if result.get("github") != expected_context:
        raise Refused("unittest run/job/attempt binding differs")
    definitions, inventories = {}, {}
    for pattern in patterns:
        path = "tooling/tests/" + pattern
        ids, record = declared_test_ids(root / path)
        definitions[path], inventories[pattern] = record["sha256"], ids
    if result.get("test_definition_sha256") != definitions:
        raise Refused("unittest source inventory differs")
    expected = [("discovery:" + name, name, [str(python), "-B", "-E", "-s", "-m",
                 "unittest", "discover", "-s", "tooling/tests", "-p", name, "-v"])
                for name in patterns]
    expected += [("dotted:tooling.tests." + name[:-3], name,
                  [str(python), "-B", "-E", "-s", "-m", "unittest",
                   "tooling.tests." + name[:-3], "-v"]) for name in sorted(patterns)]
    steps = result.get("steps")
    if not isinstance(steps, list) or len(steps) != len(expected):
        raise Refused("unittest entrypoints omitted or duplicated")
    observations = []
    for step, (label, pattern, argv) in zip(steps, expected):
        ids = inventories[pattern]
        counts = step.get("counts", {})
        if (step.get("entry_point") != label or step.get("command") != argv
                or step.get("status") != "PASS" or type(step.get("exit_code")) is not int
                or step["exit_code"] != 0 or counts.get("terminal_status") != "OK"):
            raise Refused("unittest command or terminal differs")
        for key, wanted in {"tests_run": len(ids), "passed": len(ids), "failures": 0,
                            "errors": 0, "skipped": 0, "expected_failures": 0,
                            "unexpected_successes": 0}.items():
            if type(counts.get(key)) is not int or counts[key] != wanted:
                raise Refused("unittest count/omission differs: " + key)
        streams = {}
        for name in ("stdout", "stderr"):
            original = step.get(name, {})
            filename = original.get("file")
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise Refused("unittest log locator is not a closed filename")
            raw, measured = regular_bytes(report.parent / filename, MAX_JSON)
            if measured["sha256"] != original.get("sha256"):
                raise Refused("original unittest log digest differs")
            streams[name] = {"file": filename, **measured}
            if name == "stderr" and observed_test_ids(raw) != ids:
                raise Refused("unittest verbose IDs differ from exact source inventory")
        observations.append({"entry_point": label, "ids": ids, "counts": counts, "logs": streams})
    return {"receipt": {"path": str(report), **report_record}, "entrypoints": observations,
            "scope": "source and synthetic unittest contracts; no native execution"}


def run_g0(commands, output, source, context, patterns):
    policy_results = []
    # Appending the source directory preserves isolated stdlib lookup order.
    launch = ("import pathlib,runpy,sys;sys.path.append(sys.argv[1]);"
              "runpy.run_path(str(pathlib.Path(sys.argv[1])/sys.argv[2]),run_name='__main__')")
    for policy in POLICIES:
        _raw, outcome = commands.run("policy-" + policy[:-3].replace("_", "-"),
                                    [commands.python, "-B", "-I", "-S", "-c", launch,
                                     str(commands.root / "tooling"), policy], timeout=120)
        policy_results.append({"policy": policy, "outcome": outcome})
    runner = commands.root / "tooling/ci_test_runner.py"
    _raw, runner_record = regular_bytes(runner)
    ordered = [Path(TEST_PATH).name, *patterns]
    if len(set(ordered)) != len(ordered):
        raise Refused("duplicate G0 selection")
    report = output / "unittest.json"
    argv = [commands.python, "-B", "-I", "-S", str(runner),
            "--repo", str(commands.root), "--test-directory", "tooling/tests",
            "--mode", "both", "--require-complete", "--expected-source", source["sha"],
            "--timeout", "180", "--report", str(report)]
    for pattern in ordered:
        argv.extend(["--pattern", pattern])
    commands.run("unittest-g0", argv, timeout=600)
    validated = validate_unittest_receipt(report, commands.root, ordered, source, context,
                                         runner_record["sha256"], commands.python)
    return {"policies": policy_results, "selected_tests": validated}


def publish_outputs(path, decision, receipt_sha256):
    if (decision.get("decision") not in DECISIONS
            or type(decision.get("native_required")) is not bool
            or not SHA256.fullmatch(receipt_sha256)
            or (decision["decision"] == "NOT_SELECTED_REVIEWED_CAS_PROFILE")
            != (decision["native_required"] is False)):
        raise Refused("inadmissible selection output")
    path = Path(path)
    regular_bytes(path, MAX_JSON)
    data = ("native_required=" + str(decision["native_required"]).lower() + "\n"
            "decision=" + decision["decision"] + "\n"
            "receipt_sha256=" + receipt_sha256 + "\n").encode("ascii")
    with path.open("ab") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--workflow", choices=tuple(WORKFLOWS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    output, commands, before, context = None, None, None, None
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
            raise Refused("CLI requires -B -I -S")
        root = args.repo.resolve(strict=True)
        if (not args.output.is_absolute() or not args.output.parent.is_dir()
                or args.output.exists() or args.output.is_symlink()):
            raise Refused("output requires a new absolute directory")
        output = args.output.parent.resolve(strict=True) / args.output.name
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise Refused("output and source overlap")
        if (str(args.github_output) != os.environ.get("GITHUB_OUTPUT")
                or not args.github_output.is_absolute()
                or args.github_output.resolve(strict=True).is_relative_to(root)):
            raise Refused("GitHub output locator differs")
        event_data, event_record = regular_bytes(Path(os.environ["GITHUB_EVENT_PATH"]), MAX_JSON)
        event = strict_json(event_data)
        context = event_context(os.environ, args.workflow, root, event)
        output.mkdir()
        new_json(output / "start.json", {"schema": SCHEMA, "at_utc": utc(),
                 "state": "UNKNOWN_SELECTION_ATTEMPT", "context": context,
                 "event_original": event_record, "native_executed": False})
        with (output / "event-original.json").open("xb") as stream:
            stream.write(event_data)
        commands = Commands(root, output / "commands")
        merge = commands.commit(context["checkout_sha"])
        actual = commands.inventory(merge)
        before = snapshot_source(commands, merge, actual)
        new_json(output / "source-before.json", before)
        commands.run("git-version", [commands.git_path, "--version"])
        commands.run("python-version", [commands.python, "--version"])
        commits, inventories = {"merge": merge}, {"merge": actual}
        advanced = False
        if context["event_name"] == "pull_request":
            for key in ("base", "head"):
                commits[key] = commands.commit(context[key + "_sha"])
                inventories[key] = commands.inventory(commits[key])
            if len(merge["parents"]) != 2 or merge["parents"][1] != commits["head"]["sha"]:
                raise Refused("PR raw merge parent identity differs")
            if merge["parents"][0] != commits["base"]["sha"]:
                _raw, proof = commands.run("base-ancestor",
                    [commands.git_path, "--no-replace-objects", "-c", "core.fsmonitor=false",
                     "merge-base", "--is-ancestor", commits["base"]["sha"], merge["parents"][0]],
                    allowed=(0, 1))
                if proof["exit_code"] != 0:
                    raise Refused("merge first parent is unrelated to event base")
                advanced = True
        workflows = review_workflows(root)
        decision = select_profile(context, commits, inventories, workflows, advanced_base=advanced)
        new_json(output / "selection-inputs.json", {"context": context, "commits": commits,
                 "inventories": inventories, "workflow_payloads": workflows,
                 "tentative_decision": decision})
        g0 = run_g0(commands, output, merge, context, WORKFLOWS[args.workflow]["patterns"])
        after = snapshot_source(commands, merge, actual)
        if after != before:
            raise Refused("source identity changed during G0")
        commands.recheck_tools()
        new_json(output / "source-after.json", after)
        if regular_bytes(Path(os.environ["GITHUB_EVENT_PATH"]), MAX_JSON)[1] != event_record:
            raise Refused("original event changed during G0")
        result = {"schema": SCHEMA, "at_utc": utc(), "state": "G0_COMPLETE_SELECTION_READY",
                  "context": context, "decision": decision, "g0": g0,
                  "source_before_after_equal": True, "event_original": event_record,
                  "tools_observed": commands.tools, "runtime_full_attestation": False,
                  "native_executed": False, "g1_executed_by_this_gate": False,
                  "physical_media_verified": False, "human_acceptance": False,
                  "formal_criteria_closed": 0, "candidate_sha": None,
                  "observed_jobs_avoided": 0, "observed_minutes_saved": None}
        receipt = new_json(output / "receipt.json", result)
        publish_outputs(args.github_output, decision, receipt["sha256"])
        print(json.dumps({"state": result["state"], "decision": decision,
                          "receipt": receipt}, sort_keys=True))
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError) as error:
        failure = {"schema": SCHEMA, "at_utc": utc(),
                   "state": "UNKNOWN_SELECTION_ATTEMPT" if isinstance(error, Uncertain)
                   else "FAILED_SOURCE_EVENT_OR_G0", "error": type(error).__name__,
                   "message": str(error), "context": context, "native_executed": False,
                   "native_required_output_admissible": False,
                   "formal_criteria_closed": 0, "candidate_sha": None}
        if output is not None and output.is_dir():
            try:
                new_json(output / "failure.json", failure)
            except OSError:
                pass
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
