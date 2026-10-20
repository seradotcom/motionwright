#!/usr/bin/env python3
"""Source-bound, platform-selected MLT runtime admission unit contracts.

This never installs a provider, invokes melt or renders media. All thirteen
reviewed fail-closed tests, including a simulated Windows refusal, execute on
Linux with authenticated original+repaired C sources and zero omissions.
An optional Windows execution requires the same real source bytes.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

TEST_PATH = "tooling/tests/test_native_mlt_runtime_admission.py"
GATE_PATH = "tooling/native_mlt_runtime_admission.py"
SCRIPT_PATH = "tooling/ci_mlt_runtime_selected.py"
WORKFLOW_PATH = ".github/workflows/mlt-audio-diagnostic.yml"
ORIGINAL_SHA = "c1c8850ff049a8775bb3d24c64be9b9365659773ce019902585c056f5be2f910"
REPAIRED_SHA = "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0"
PORTABLE_CASE = "test_simulated_windows_machine_can_never_admit_module_on_either_runner"
EXPECTED = frozenset((
    "test_known_original_source_sha_is_fixed_not_discovered_dynamically",
    PORTABLE_CASE,
    "test_existing_unsafe_runtime_overrides_are_explicitly_forbidden",
    "test_authentic_source_and_mock_compiler_receipt_are_structurally_bound",
    "test_wrong_original_or_repaired_source_refuses_candidate",
    "test_modified_compiler_binary_rejected_even_with_valid_sources",
    "test_wrong_compiler_provenance_rejected_even_if_module_sha_matches",
    "test_duplicate_compiler_receipt_json_fields_rejected",
    "test_modified_receipt_digest_rejected_without_native_execution",
    "test_corrupt_elf_candidate_and_path_escape_rejected",
    "test_installed_module_path_is_constant_and_not_user_selected",
    "test_nonexistent_source_cannot_be_read_or_written",
    "test_repaired_module_install_requires_linux_uid0_and_ephemeral_allowance",
))
MAX_SOURCE = 1024 * 1024


class ContractError(ValueError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ContractError(message)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def selected_names(test_source: bytes, platform: str) -> tuple[str, ...]:
    require(platform in ("linux", "windows"), "unsupported platform selection")
    try:
        tree = ast.parse(test_source.decode("utf-8"))
    except (SyntaxError, UnicodeError) as error:
        raise ContractError("unparseable test definition") from error
    require(not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "load_tests" for n in ast.walk(tree)),
            "dynamic unittest loader forbidden")
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "NativeModuleAdmissionTests"]
    require(len(classes) == 1, "test class is missing or duplicated")
    cls = classes[0]
    require(any(isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name)
                and b.value.id == "unittest" and b.attr == "TestCase"
                for b in cls.bases), "test class lost unittest.TestCase")
    names = [n.name for n in cls.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    all_names = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test_")]
    require(len(names) == len(set(names)) == len(all_names) == 13
            and set(names) == EXPECTED,
            "native MLT runtime test inventory diverges from 13 reviewed names")
    special = next(n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == PORTABLE_CASE)
    literal = ast.get_source_segment(test_source.decode("utf-8"), special) or ""
    require('mock.patch.object(gate.sys, "platform", "win32")' in literal
            and "self.assertRaisesRegex" in literal
            and '"disposable_GHA"' in literal
            and "self.skipTest(" not in literal,
            "portable simulated Windows rejection must run without skips")
    return tuple(sorted(EXPECTED))


def _git(repo: Path, *args: str) -> bytes:
    env = dict(os.environ)
    for k in list(env):
        if k.upper().startswith("GIT_"):
            env.pop(k)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    cmd = ["git", "--no-replace-objects", "-C", str(repo), *args]
    cp = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
    require(cp.returncode == 0 and len(cp.stdout) < 3 * MAX_SOURCE,
            "Git source query failed or oversized")
    return cp.stdout


def tracked_blob(repo: Path, head: str, path: str) -> bytes:
    require(re.fullmatch(r"[0-9a-f]{40}", head) is not None,
            "expected a full source SHA")
    require(path in (TEST_PATH, GATE_PATH, SCRIPT_PATH, WORKFLOW_PATH),
            "source path not in reviewed allowlist")
    record = _git(repo, "ls-tree", "-z", head, "--", path)
    rows = [x for x in record.split(b"\0") if x]
    require(len(rows) == 1, "tracked blob path unavailable")
    meta, sep, actual_path = rows[0].partition(b"\t")
    require(sep == b"\t" and actual_path.decode("utf-8") == path,
            "Git tracked path differs")
    mode, kind, oid = meta.split()
    require(mode in (b"100644", b"100755") and kind == b"blob",
            "source path not a regular Git file")
    raw = _git(repo, "cat-file", "blob", oid.decode("ascii"))
    require(len(raw) <= MAX_SOURCE, "source exceeds byte budget")
    require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            == oid.decode("ascii"), "Git object identity differs")
    p = repo / path
    require(stat.S_ISREG(p.lstat().st_mode) and p.read_bytes() == raw,
            "worktree bytes differ from committed Git blob")
    return raw


def checked_c_source(path: Path, expected: str, size: int) -> str:
    require(path.is_absolute() and not path.is_symlink() and path.is_file(),
            "C source is not a trusted absolute regular file")
    first = path.stat()
    require(first.st_size == size, "MLT source size mismatch")
    with path.open("rb") as stream:
        payload = stream.read(size + 1)
    last = path.stat()
    require(first.st_size == last.st_size and first.st_mtime_ns == last.st_mtime_ns,
            "MLT input mutated while inspected")
    require(len(payload) == size and sha256(payload) == expected,
            "MLT source bytes differ from original A4 source")
    return sha256(payload)


def verify_verbose(raw: bytes, expected: tuple[str, ...]) -> None:
    text = raw.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    observed = re.findall(
        r"(?m)^(test_\w+) \(tooling\.tests\.test_native_mlt_runtime_admission\."
        r"NativeModuleAdmissionTests\.\1\) \.\.\. ok$", text)
    require(len(observed) == len(expected) and set(observed) == set(expected),
            "original verbose output is not the exact selected method inventory")
    require("skipped=" not in text and "expected failures" not in text,
            "skipped/expected failed tests are never accepted")
    require(re.search(r"(?m)^Ran " + str(len(expected)) + r" tests? in ", text)
            is not None and re.search(r"(?m)^OK$", text) is not None,
            "unittest completed count or terminal OK missing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--platform", choices=("linux", "windows"), required=True)
    parser.add_argument("--original", type=Path)
    parser.add_argument("--repaired", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        here = "linux" if sys.platform == "linux" else "windows" if sys.platform == "win32" else ""
        require(here == args.platform, "requested platform differs from executor")
        require(os.environ.get("MOTIONWRIGHT_APPROVED_MLT_G2") != "1",
                "a G1-only check may not inherit native installation permission")
        repo = args.repo.resolve(strict=True)
        require(repo.is_dir() and not repo.is_symlink(), "invalid Git source worktree")
        require(_git(repo, "rev-parse", "HEAD").decode().strip() == args.expected_source,
                "HEAD differs from expected SHA")
        require(not _git(repo, "status", "--porcelain=v1", "--untracked-files=all",
                         "--ignored=matching").strip(),
                "checkout changed or contains untracked/ignored files")
        paths = {p: sha256(tracked_blob(repo, args.expected_source, p))
                 for p in (TEST_PATH, GATE_PATH, SCRIPT_PATH, WORKFLOW_PATH)}
        methods = selected_names(tracked_blob(repo, args.expected_source, TEST_PATH),
                                 args.platform)
        require(args.original is not None and args.repaired is not None,
                "both original and repaired authentic C inputs required")
        source_hashes = {
            "original": checked_c_source(args.original, ORIGINAL_SHA, 96690),
            "repaired": checked_c_source(args.repaired, REPAIRED_SHA, 98847),
        }
        out = args.report
        require(out.is_absolute() and out.parent.is_dir() and not out.exists()
                and not out.is_symlink() and not out.resolve().is_relative_to(repo),
                "report must be new outside checked source")
        args_command = [sys.executable, "-B", "-E", "-s", "-m", "unittest", "-v"] + [
            "tooling.tests.test_native_mlt_runtime_admission.NativeModuleAdmissionTests."
            + method for method in methods]
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("MOTIONWRIGHT_APPROVED_MLT_G2", None)
        env["MOTIONWRIGHT_MLT_V722_ORIGINAL"] = str(args.original)
        env["MOTIONWRIGHT_MLT_V722_REPAIRED"] = str(args.repaired)
        proc = subprocess.run(args_command, cwd=repo, env=env,
                              capture_output=True, timeout=120)
        stdout_path = out.with_name(out.stem + "-stdout.log")
        stderr_path = out.with_name(out.stem + "-stderr.log")
        with stdout_path.open("xb") as stream:
            stream.write(proc.stdout)
        with stderr_path.open("xb") as stream:
            stream.write(proc.stderr)
        try:
            require(proc.returncode == 0, "selected source tests returned failure")
            verify_verbose(proc.stderr, methods)
            status = "PASS_SELECTED_PLATFORM_G1"
        except (ContractError, UnicodeError) as error:
            status = "FAIL_SELECTED_PLATFORM_G1: " + str(error)
        report = {
            "schema": "motionwright.mlt-runtime-selected-platform-g1/1",
            "status": status,
            "source_sha": args.expected_source,
            "tree_sha": _git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
            "executor_platform": here,
            "A4_source_sha256": source_hashes,
            "tracked_source_sha256": paths,
            "expected_total_reviewed_methods": 13,
            "selected_methods": list(methods),
            "selected_count": len(methods),
            "not_executed_on_this_platform": sorted(EXPECTED - set(methods)),
            "not_executed_are_not_passes": True,
            "exit_code": proc.returncode,
            "stdout": {"file": stdout_path.name, "sha256": sha256(proc.stdout)},
            "stderr": {"file": stderr_path.name, "sha256": sha256(proc.stderr)},
            "original_native_module_installed": False,
            "live_PID_attested": False,
            "AAC_master_decoded": False,
            "human_acceptance": False,
            "at_utc": datetime.now(timezone.utc).isoformat(),
        }
        with out.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"status": status, "source_sha": args.expected_source,
                          "platform": here, "selected": len(methods)}))
        return 0 if status == "PASS_SELECTED_PLATFORM_G1" else 1
    except (ContractError, OSError, ValueError, UnicodeError,
            subprocess.TimeoutExpired) as error:
        print("MLT G1 source selection rejected: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
