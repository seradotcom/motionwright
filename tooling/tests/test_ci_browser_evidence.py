"""Custody protocol controls using Git fixtures and harmless Python processes.

No npm, browser, Rust, service, or product command runs in this module.
Synthetic report fields are retained data, never product test observations.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("browser_custody_under_test", ROOT / "tooling/ci_browser_evidence.py")
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)
PLAN_ID = "a" * 64
REPORT = {"config": {"retries": 0}, "suites": [], "errors": [],
          "stats": {"expected": 2, "unexpected": 1, "skipped": 1, "flaky": 1}}


class GitFixture:
    def __init__(self, root):
        self.root = root
        self.repo = root / "source"
        self.output = root / "evidence"
        self.marker = root / "calls.txt"
        self.script = root / "emit.py"
        self.repo.mkdir()
        self.environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                                GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull)
        for relative in (CI.SELF, CI.PLANNER):
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        for relative, text in {
            ".github/workflows/ci.yml": "name: Synthetic custody protocol\n",
            "Cargo.lock": "# synthetic locked input\nversion = 3\n",
            ".gitignore": "apps/studio/node_modules/\n",
            "apps/studio/package.json": '{"scripts":{"test:e2e":"playwright test"}}\n',
            "apps/studio/package-lock.json": '{"lockfileVersion":3}\n',
            "apps/studio/playwright.config.ts": "// synthetic config, never executed\n",
        }.items():
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for relative in CI.RUNTIME_INPUTS:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n" if target.suffix == ".json" else "// inert fixture bytes\n", encoding="utf-8")
        self.git("init", "--quiet")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic original source")
        self.sha = self.git("rev-parse", "HEAD").decode().strip()
        self.context = {"GITHUB_REPOSITORY": "fixture/custody", "GITHUB_EVENT_NAME": "workflow_dispatch",
                        "GITHUB_SHA": self.sha, "GITHUB_REF": "refs/heads/fixture",
                        "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1",
                        "GITHUB_WORKFLOW_SHA": self.sha,
                        "GITHUB_WORKFLOW_REF": "fixture/custody/.github/workflows/ci.yml@refs/heads/fixture",
                        "GITHUB_JOB": "browser-acceptance"}

    def git(self, *args):
        result = subprocess.run(["git", "-c", "user.name=Custody fixture", "-c", "user.email=fixture@example.invalid",
                                 "-c", "core.hooksPath=" + str(self.root / "no-hooks"),
                                 "-c", "core.autocrlf=false", "-C", str(self.repo), *args],
                                env=self.environment, capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stderr.decode(errors="replace"))
        return result.stdout

    def prepare(self, *, exit_code=0, report=REPORT, extra=""):
        raw_report = None if report is None else (report if isinstance(report, bytes) else json.dumps(report).encode())
        self.script.write_text(
            "import os, sys\nfrom pathlib import Path\n"
            + f"marker = Path({str(self.marker)!r})\n"
            + "with marker.open('a', encoding='utf-8') as stream: stream.write('call\\n')\n"
            + "sys.stdout.buffer.write(b'original\\r\\nbytes\\xff\\n')\n"
            + "sys.stderr.buffer.write(b'original error\\x00\\r\\n')\n"
            + (f"Path(os.environ['PLAYWRIGHT_JSON_OUTPUT_FILE']).write_bytes({raw_report!r})\n" if raw_report is not None else "")
            + extra + f"\nsys.exit({exit_code})\n", encoding="utf-8")
        self.command = [sys.executable, "-I", "-B", str(self.script)]

    def invoke(self):
        with patch.dict(os.environ, self.context), redirect_stdout(io.StringIO()):
            return CI.capture(self.repo, self.output, PLAN_ID, self.command,
                              {"python": sys.executable, "synthetic_script": self.script})

    def read(self, name="receipt.json"):
        return json.loads((self.output / name).read_bytes())


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix="ci-browser-original-", dir=os.environ.get("MW_CI_BROWSER_TEST_TMP")) as directory:
        yield GitFixture(Path(directory))


class BrowserOriginalCustodyTests(unittest.TestCase):
    def test_real_failed_process_preserves_raw_bytes_exit_and_omissions_without_replay(self):
        with fixture() as f:
            f.prepare(exit_code=7)
            self.assertEqual(f.invoke(), 7)
            receipt = f.read()
            self.assertEqual(receipt["state"], "ORIGINAL_EXECUTION_FAILED")
            self.assertEqual(receipt["original_exit_code"], 7)
            self.assertEqual(f.read("process-outcome.json")["original_exit_code"], 7)
            self.assertEqual(receipt["report"]["stats_as_reported"], REPORT["stats"])
            for name, raw in (("stdout.log", b"original\r\nbytes\xff\n"), ("stderr.log", b"original error\0\r\n")):
                self.assertEqual((f.output / name).read_bytes(), raw)
                self.assertEqual(receipt["streams"][name]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertFalse(receipt["test_case_results_admitted"])
            self.assertFalse(receipt["same_run_reuse_eligible"])
            with self.assertRaises(FileExistsError):
                f.invoke()
            self.assertEqual(f.marker.read_text(), "call\n")

    def test_exit_zero_with_original_omissions_retains_them_without_new_policy(self):
        with fixture() as f:
            report = {**REPORT, "stats": {"expected": 3, "unexpected": 0, "skipped": 1, "flaky": 1},
                      "suites": [{"tests": [{"expectedStatus": "failed", "results": [{"status": "failed", "retry": 0}]}]}]}
            f.prepare(report=report)
            self.assertEqual(f.invoke(), 0)
            receipt = f.read()
            self.assertEqual(receipt["state"], "ORIGINAL_COMMAND_COMPLETED")
            self.assertEqual(receipt["report"]["stats_as_reported"], report["stats"])
            self.assertEqual(json.loads((f.output / "playwright-report.json").read_bytes()), report)
            self.assertFalse(receipt["test_case_results_admitted"])

    def test_absent_or_invalid_report_cannot_hide_exit_zero_or_become_admitted(self):
        for raw, state in ((None, "ABSENT"), (b'{"stats":{},"stats":{},"config":{},"suites":[],"errors":[]}', "ORIGINAL_JSON_INVALID")):
            with self.subTest(state=state), fixture() as f:
                f.prepare(report=raw)
                self.assertEqual(f.invoke(), 2)
                receipt = f.read()
                self.assertEqual(receipt["state"], "CUSTODY_FAILED")
                self.assertEqual(receipt["original_exit_code"], 0)
                self.assertEqual(f.read("process-outcome.json")["original_exit_code"], 0)
                self.assertEqual(receipt["report"]["state"], state)
                if raw is not None:
                    self.assertEqual(receipt["report"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_replaced_observer_and_stale_checkout_are_rejected_before_process_start(self):
        for variant in ("observer", "stale_sha"):
            with self.subTest(variant=variant), fixture() as f:
                f.prepare()
                if variant == "observer":
                    with (f.repo / CI.PLANNER).open("a", encoding="utf-8") as stream:
                        stream.write("\nraise RuntimeError('altered observer must never execute')\n")
                else:
                    (f.repo / "new-input.txt").write_text("new source\n", encoding="utf-8")
                    f.git("add", ".")
                    f.git("commit", "--quiet", "-m", "New source cannot inherit old execution")
                self.assertEqual(f.invoke(), 2)
                self.assertFalse(f.marker.exists())
                self.assertEqual(f.read("process-outcome.json")["state"], "NOT_STARTED")
                self.assertIsNone(f.read()["original_exit_code"])

    def test_changed_lock_runtime_or_command_input_after_process_keeps_original_exit(self):
        for variant in ("Cargo.lock", CI.RUNTIME_INPUTS[-1], "script"):
            with self.subTest(variant=variant), fixture() as f:
                target = f.script if variant == "script" else f.repo / variant
                f.prepare(extra=f"with Path({str(target)!r}).open('ab') as stream: stream.write(b'changed input\\n')\n")
                self.assertEqual(f.invoke(), 2)
                receipt = f.read()
                self.assertEqual(receipt["state"], "CUSTODY_FAILED")
                self.assertEqual(receipt["original_exit_code"], 0)
                self.assertFalse(receipt["source_and_inputs_unchanged"])
                self.assertTrue(any(x.startswith("source_postcheck:") for x in receipt["custody_errors"]))

    def test_interrupted_observation_keeps_unknown_and_refuses_replay(self):
        with fixture() as f:
            f.prepare()
            original_popen = subprocess.Popen
            launched = []

            def interrupt_wait(command, *args, **kwargs):
                process = original_popen(command, *args, **kwargs)
                if command == f.command:
                    launched.append((process, process.wait))
                    process.wait = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("synthetic transport interrupt"))
                return process

            try:
                with patch.object(CI.subprocess, "Popen", side_effect=interrupt_wait):
                    self.assertEqual(f.invoke(), 2)
                self.assertEqual(f.read()["state"], "UNKNOWN")
                self.assertIsNone(f.read()["original_exit_code"])
                with self.assertRaises(FileExistsError):
                    f.invoke()
            finally:
                # Reap the sole harmless child without replaying the command or changing its UNKNOWN receipt.
                for process, original_wait in launched:
                    original_wait(timeout=10)
            self.assertEqual(len(launched), 1)
            self.assertEqual(f.marker.read_text(), "call\n")


class BoundedEvidenceHashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ci-browser-bounded-", dir=os.environ.get("MW_CI_BROWSER_TEST_TMP"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @contextmanager
    def measured_reads(self, target, mutation=None):
        """Wrap real file reads; mutations are finite, synchronous fixture writes."""
        real_open = Path.open
        target = target.absolute()
        reads = []
        open_count = 0
        case = self

        class MeasuredStream:
            def __init__(self, stream, open_index):
                self.stream = stream
                self.open_index = open_index
                self.read_index = 0

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, requested):
                case.assertIs(type(requested), int)
                case.assertGreaterEqual(requested, 0)
                self.read_index += 1
                event = {"open_index": self.open_index, "read_index": self.read_index, "requested": requested}
                reads.append(event)
                if mutation is not None:
                    mutation(event, real_open)
                raw = self.stream.read(requested)
                event["returned"] = len(raw)
                return raw

        def open_measured(path, mode="r", *args, **kwargs):
            nonlocal open_count
            stream = real_open(path, mode, *args, **kwargs)
            if Path(path).absolute() == target and mode == "rb":
                open_count += 1
                return MeasuredStream(stream, open_count)
            return stream

        with patch.object(Path, "open", new=open_measured):
            yield reads

    def test_stable_binary_and_empty_inputs_keep_exact_hashes_with_bounded_reads(self):
        self.assertEqual(CI.MAX_HASH_BYTES, 256 * 1024 * 1024)
        for raw in (b"", b"original\r\n\xff\x00" * 1024):
            with self.subTest(size=len(raw)):
                target = self.root / "original.bin"
                target.write_bytes(raw)
                with self.measured_reads(target) as reads:
                    record = CI.file_record(target, max_bytes=len(raw))
                self.assertEqual(record, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                self.assertEqual(sum(item["requested"] for item in reads), len(raw) + 1)
                self.assertEqual(sum(item["returned"] for item in reads), len(raw))
                self.assertEqual(target.read_bytes(), raw)

    def test_over_budget_and_invalid_budgets_are_rejected_before_open(self):
        target = self.root / "original.bin"
        raw = b"retained original" * 512
        target.write_bytes(raw)
        with patch.object(Path, "open", side_effect=AssertionError("Rejected input must not be opened")) as opened:
            with self.assertRaisesRegex(ValueError, "exceeds hashing budget"):
                CI.file_record(target, max_bytes=len(raw) - 1)
            for invalid in (True, -1, CI.MAX_HASH_BYTES + 1):
                with self.subTest(budget=invalid), self.assertRaisesRegex(ValueError, "Invalid evidence hashing budget"):
                    CI.file_record(target, max_bytes=invalid)
            opened.assert_not_called()
        self.assertEqual(target.read_bytes(), raw)

    def test_growth_is_rejected_after_at_most_initial_size_plus_one_bytes(self):
        target = self.root / "stdout.log"
        raw = b"x" * (1024 * 1024 + 37)
        appended = b"finite fixture append\r\n\xff" * 128
        target.write_bytes(raw)

        def grow(event, real_open):
            if event["read_index"] == 1:
                with real_open(target, "ab") as stream:
                    stream.write(appended)

        with self.measured_reads(target, grow) as reads:
            with self.assertRaisesRegex(ValueError, "grew during hashing"):
                CI.file_record(target)
        self.assertEqual(sum(item["requested"] for item in reads), len(raw) + 1)
        self.assertEqual(sum(item["returned"] for item in reads), len(raw) + 1)
        self.assertTrue(all(0 < item["requested"] <= 1024 * 1024 for item in reads))
        self.assertEqual(target.read_bytes(), raw + appended)

    def test_truncation_is_rejected_without_restoring_or_rewriting_original(self):
        target = self.root / "stdout.log"
        raw = b"original bytes\r\n\x00\xff" * 256
        target.write_bytes(raw)

        def truncate(event, real_open):
            if event["read_index"] == 1:
                with real_open(target, "r+b") as stream:
                    stream.truncate(17)

        with self.measured_reads(target, truncate) as reads:
            with self.assertRaisesRegex(ValueError, "Evidence changed during hashing"):
                CI.file_record(target)
        self.assertEqual(sum(item["requested"] for item in reads), len(raw) + 1)
        self.assertEqual(sum(item["returned"] for item in reads), 17)
        self.assertEqual(target.read_bytes(), raw[:17])

    def test_same_size_mutation_retains_the_existing_signature_rejection(self):
        target = self.root / "original.bin"
        raw = b"original bytes\r\n\x00\xff" * 256
        changed = b"z" + raw[1:]
        target.write_bytes(raw)
        before = target.stat()

        def mutate(event, real_open):
            if event["read_index"] == 1:
                with real_open(target, "r+b") as stream:
                    stream.write(changed)
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

        with self.measured_reads(target, mutate):
            with self.assertRaisesRegex(ValueError, "Evidence changed during hashing"):
                CI.file_record(target)
        self.assertEqual(target.read_bytes(), changed)

    def test_changed_path_descriptor_identity_is_rejected_before_reading(self):
        target, replacement = self.root / "original.bin", self.root / "replacement.bin"
        target.write_bytes(b"original")
        replacement.write_bytes(b"replaced")
        real_open = Path.open
        reads = []

        class NoReadStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, requested):
                reads.append(requested)
                raise AssertionError("A different descriptor must be rejected before reading")

        def redirected(path, mode="r", *args, **kwargs):
            if Path(path).absolute() == target.absolute() and mode == "rb":
                return NoReadStream(real_open(replacement, mode, *args, **kwargs))
            return real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", new=redirected):
            with self.assertRaisesRegex(ValueError, "Input identity changed"):
                CI.file_record(target)
        self.assertEqual(reads, [])
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(replacement.read_bytes(), b"replaced")

    def test_report_reread_is_bounded_to_the_previously_verified_size(self):
        target = self.root / "playwright-report.json"
        raw = json.dumps(REPORT).encode()
        appended = b" " * (len(raw) * 32)
        target.write_bytes(raw)
        observer = type("SyntheticReportParser", (), {"strict_json": staticmethod(json.loads)})
        stable = CI.retained_report(observer, target)
        self.assertEqual(stable["state"], "ORIGINAL_JSON_RETAINED")
        self.assertEqual(stable["stats_as_reported"], REPORT["stats"])
        self.assertFalse(stable["test_case_results_admitted"])

        def grow_report(event, real_open):
            if event["open_index"] == 2 and event["read_index"] == 1:
                with real_open(target, "ab") as stream:
                    stream.write(appended)

        with self.measured_reads(target, grow_report) as reads:
            with self.assertRaisesRegex(ValueError, "Report changed after hashing"):
                CI.retained_report(observer, target)
        reread = [item for item in reads if item["open_index"] == 2]
        self.assertEqual(len(reread), 1)
        self.assertEqual(reread[0]["requested"], len(raw) + 1)
        self.assertEqual(reread[0]["returned"], len(raw) + 1)
        self.assertEqual(target.read_bytes(), raw + appended)


if __name__ == "__main__":
    unittest.main()
