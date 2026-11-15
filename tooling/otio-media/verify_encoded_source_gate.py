"""One source-only CI gate for six encoded-media admission regressions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

TOTAL_SECONDS = 1200
SOURCE_PATHS = (
    "crates/service/src/lib.rs",
    "crates/service/src/portability.rs",
    "crates/service/tests/binary_bundle_admission.rs",
    "crates/service/tests/svg_delayed_marker.rs",
    "Cargo.lock",
    "SOURCE_LOCK.json",
    ".github/workflows/v05-otio-real-media.yml",
    "tooling/otio-media/verify_encoded_source_gate.py",
)
COMMANDS = (
    (
        ("cargo", "test", "--locked", "-p", "motionwright-service", "--lib",
         "encoded_source_prefix_tests", "--", "--nocapture"),
        (
            "encoded_source_prefix_tests::binary_box_size_is_not_a_bomless_utf32_encoding",
            "encoded_source_prefix_tests::encoded_svg_remains_rejected_after_long_bomless_whitespace",
            "encoded_source_prefix_tests::malformed_or_ambiguous_encoded_prefixes_remain_closed",
        ),
    ),
    (
        ("cargo", "test", "--locked", "-p", "motionwright-service",
         "--test", "binary_bundle_admission", "--", "--nocapture"),
        ("binary_box_size_survives_fingerprinted_bundle_restore",),
    ),
    (
        ("cargo", "test", "--locked", "-p", "motionwright-service", "--lib",
         "portability::svg_bundle_admission_tests::encoded_text_alias_cannot_exempt_a_restrictive_alias",
         "--", "--exact", "--nocapture"),
        ("portability::svg_bundle_admission_tests::encoded_text_alias_cannot_exempt_a_restrictive_alias",),
    ),
    (
        ("cargo", "test", "--locked", "-p", "motionwright-service",
         "--test", "svg_delayed_marker",
         "utf16_and_utf32_disguised_active_svg_never_becomes_raster_cas",
         "--", "--exact", "--nocapture"),
        ("utf16_and_utf32_disguised_active_svg_never_becomes_raster_cas",),
    ),
)
FLAGS = {
    "automatic_retry": False, "native_execution": False, "human_acceptance": False,
    "release_authorized": False, "formal_G2": False, "formal_G3": False,
}


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("original_shared_1200_second_deadline_exhausted")
    return value


def descriptor(raw: bytes) -> dict:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def retain(path: Path, value: dict, *, exclusive: bool = False) -> bytes:
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb" if exclusive else "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return raw


def source(expected: str, deadline: float) -> dict:
    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", *arguments], timeout=min(20, remaining(deadline)),
        ).decode("utf-8").strip()

    head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    if head != expected or re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        raise ValueError("encoded_source_checkout_differs_from_github")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("encoded_source_checkout_is_dirty")
    files = {}
    for relative in SOURCE_PATHS:
        remaining(deadline)
        path = Path(relative)
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or path.is_symlink()
                or before.st_size > 16 * 1024 * 1024):
            raise ValueError("encoded_source_file_not_plain_and_bounded:" + relative)
        raw = path.read_bytes()
        after = path.lstat()
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        )
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise ValueError("encoded_source_file_changed_during_read:" + relative)
        blob = hashlib.sha1(
            b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw,
            usedforsecurity=False,
        ).hexdigest()
        if blob != git("rev-parse", f"{head}:{relative}"):
            raise ValueError("encoded_source_file_differs_from_head:" + relative)
        files[relative] = {**descriptor(raw), "git_blob": blob}
    if git("rev-parse", "HEAD") != head or git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("encoded_source_changed_during_binding")
    return {"sha": head, "tree": tree, "files": files}


def rust_readback(stdout: bytes, expected: tuple[str, ...]) -> dict:
    view = stdout.decode("utf-8").replace("\r\n", "\n")
    tests = re.findall(r"^test (\S+) \.\.\. (\S+)$", view, re.M)
    names = [name for name, status in tests if status == "ok"]
    running = re.findall(r"^running (\d+) tests?$", view, re.M)
    summary_lines = re.findall(r"^test result:.*$", view, re.M)
    summaries = re.findall(
        r"^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; "
        r"(\d+) measured; (\d+) filtered out; finished in \d+(?:\.\d+)?s$",
        view, re.M,
    )
    complete = (
        len(tests) == len(names) == len(set(names)) == len(expected)
        and set(names) == set(expected) and running == [str(len(expected))]
        and len(summary_lines) == len(summaries) == 1
        and summaries[0][:4] == (str(len(expected)), "0", "0", "0")
    )
    return {"complete": complete, "observed": names, "tests_run": len(names),
            "test_lines": tests, "summaries": summaries, "running": running}


def main() -> int:
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true" or len(sys.argv) != 1:
        raise ValueError("explicit_linux_github_source_gate_required")
    expected = os.environ["GITHUB_SHA"]
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    if (re.fullmatch(r"[0-9a-f]{40}", expected) is None
            or re.fullmatch(r"[1-9][0-9]*", run_id) is None
            or re.fullmatch(r"[1-9][0-9]*", attempt) is None):
        raise ValueError("encoded_source_github_context_invalid")
    started = time.monotonic()
    deadline = started + TOTAL_SECONDS
    root = Path(os.environ["RUNNER_TEMP"]) / "otio-encoded-source"
    root.mkdir(mode=0o700, exist_ok=False)
    commands = [
        {"ordinal": index, "argv": list(argv), "required": list(required),
         "state": "NOT_RUN", "stdout_path": f"{index:02d}.stdout.log",
         "stderr_path": f"{index:02d}.stderr.log"}
        for index, (argv, required) in enumerate(COMMANDS)
    ]
    receipt = {
        "schema": "motionwright.native.otio-encoded-source-required-rust/1",
        "source_sha": expected, "run_id": run_id, "run_attempt": attempt, "state": "INTENT",
        "source_before": None, "source_after": None, "source_paths": list(SOURCE_PATHS),
        "started_monotonic": started, "deadline_monotonic": deadline,
        "total_timeout_seconds": TOTAL_SECONDS, "commands": commands,
        "expected_tests_run": 6, "tests_run": 0, **FLAGS,
    }
    intent = retain(root / "intent.original.json", receipt, exclusive=True)
    for item in commands:
        for label in ("stdout", "stderr"):
            with (root / item[label + "_path"]).open("xb"):
                pass
            item[label] = descriptor(b"")
    retain(root / "receipt.json", receipt)
    primary = None
    try:
        receipt["source_before"] = source(expected, deadline)
        retain(root / "receipt.json", receipt)
        for item in commands:
            item["state"] = "INTENT"
            retain(root / "receipt.json", receipt)
            try:
                result = subprocess.run(item["argv"], capture_output=True, timeout=remaining(deadline))
                stdout, stderr, code = result.stdout, result.stderr, result.returncode
                item["state"] = "RETURNED"
            except subprocess.TimeoutExpired as original:
                stdout, stderr, code = original.stdout or b"", original.stderr or b"", 124
                item["state"] = "UNKNOWN_NO_RETRY"
                item["failure"] = "original_cargo_timeout_no_retry"
            for label, raw in (("stdout", stdout), ("stderr", stderr)):
                with (root / item[label + "_path"]).open("wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                item[label] = descriptor(raw)
            item["exit_code"] = code
            retain(root / "receipt.json", receipt)
            try:
                item["readback"] = rust_readback(stdout, tuple(item["required"]))
            except UnicodeDecodeError:
                item["readback"] = {"complete": False, "tests_run": 0, "error": "stdout_is_not_utf8"}
            passed = code == 0 and item["readback"]["complete"]
            if passed:
                item["state"] = "PASS"
            else:
                if item["state"] != "UNKNOWN_NO_RETRY":
                    item["state"] = "FAIL"
                primary = item.get("failure", "original_rust_exit_roster_or_summary_failed")
                retain(root / "receipt.json", receipt)
                break
            retain(root / "receipt.json", receipt)
    except BaseException as original:
        primary = primary or next((item["failure"] for item in commands if item.get("failure")), None)
        primary = primary or type(original).__name__ + ":" + str(original)
        for item in commands:
            if item["state"] == "INTENT":
                item["state"] = "UNKNOWN_NO_RETRY"
                item["failure"] = primary
    finally:
        try:
            receipt["source_after"] = source(expected, deadline)
            if receipt["source_before"] is None or receipt["source_after"] != receipt["source_before"]:
                raise ValueError("encoded_source_before_after_differs")
        except BaseException as original:
            receipt["source_failure"] = type(original).__name__ + ":" + str(original)
            primary = primary or receipt["source_failure"]
        elapsed = time.monotonic() - started
        complete = primary is None and elapsed <= TOTAL_SECONDS and all(
            item["state"] == "PASS" for item in commands
        )
        unknown = any(item["state"] == "UNKNOWN_NO_RETRY" for item in commands)
        receipt.update(
            state="PASS" if complete else "UNKNOWN_NO_RETRY" if unknown else "FAIL",
            failure=primary or (None if complete else "original_shared_deadline_not_complete"),
            elapsed_seconds=elapsed,
            tests_run=sum(item.get("readback", {}).get("tests_run", 0) for item in commands),
        )
        final_receipt = retain(root / "receipt.json", receipt)
        retain(root / "terminal.original.json", {
            "schema": "motionwright.native.otio-encoded-source-required-rust-terminal/1",
            "source_sha": expected, "run_id": run_id, "run_attempt": attempt,
            "state": receipt["state"], "failure": receipt["failure"],
            "intent": descriptor(intent), "receipt": descriptor(final_receipt),
            "streams": {item[label + "_path"]: item[label] for item in commands
                        for label in ("stdout", "stderr")},
            "tests_run": receipt["tests_run"], "expected_tests_run": 6, **FLAGS,
        }, exclusive=True)
        print(json.dumps({
            "encoded_source_gate": receipt["state"], "tests_run": receipt["tests_run"],
            "failure": receipt["failure"], "automatic_retry": False, "native_execution": False,
        }, sort_keys=True), flush=True)
    return 0 if receipt["state"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
