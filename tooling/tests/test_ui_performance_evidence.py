"""Synthetic UI evidence guards; no npm, browser, service or native producer.

Git fixtures preserve the original selection sources. Harmless Python children
stand in for process plumbing only; their invented samples never prove latency.
"""
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import base64


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ui_performance_under_test", ROOT / "tooling/ui_performance_evidence.py")
UI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UI)


def context(sha="a" * 40):
    return {"GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": sha, "GITHUB_WORKFLOW_SHA": sha, "GITHUB_REF": "refs/heads/synthetic-ui",
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + UI.WORKFLOW + "@refs/heads/synthetic-ui",
            "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "ui-selection"}


@contextmanager
def environment(values):
    original = {name: value for name, value in os.environ.items()
                if not name.startswith(("GITHUB_", "NODE_", "PW", "PLAYWRIGHT_", "MOTIONWRIGHT_", "MW_UI_"))}
    with patch.dict(os.environ, {**original, **values, "MW_UI_PERFORMANCE_SCOPE": "ui"}, clear=True):
        yield


def reports(repo, output, ctx, *, samples=None):
    measurement = {"schema": "motionwright.ui-selection.v1", "source_sha": ctx["GITHUB_SHA"],
                   "dataset": {"scenes": 12, "samples": 60, "surface": UI.SURFACE}, "budget_ms": 100,
                   "samples_ms": list(range(1, 61)) if samples is None else samples}
    measurement["p95_ms"] = sorted(measurement["samples_ms"])[56]
    measurement["max_ms"] = max(measurement["samples_ms"])
    body = json.dumps(measurement, indent=2, allow_nan=False).encode()
    node = str(Path(sys.executable).resolve())
    command = UI.command_for(Path(node), output)
    result = {"status": "passed", "retry": 0, "errors": [], "annotations": [],
              "attachments": [{"name": "ui-selection-evidence", "contentType": "application/json",
                               "body": base64.b64encode(body).decode()}]}
    report = {"errors": [], "stats": {"expected": 1, "unexpected": 0, "skipped": 0, "flaky": 0},
              "config": {"version": "1.63.0", "rootDir": str(repo / "apps/studio/tests"),
                         "configFile": str(repo / UI.CONFIG), "workers": 1, "shard": None,
                         "reporter": [["dot"], ["json"]],
                         "argv": [node, str(repo / "apps/studio/node_modules/.bin/playwright"), *command[2:]],
                         "metadata": {"ci": {"commitHash": ctx["GITHUB_SHA"]}},
                         "projects": [{"name": "chromium", "id": "chromium", "testDir": str(repo / "apps/studio/tests"),
                                       "outputDir": str(output / "test-results"), "repeatEach": 1, "retries": 0}]},
              "suites": [{"specs": [{**UI.CASE, "ok": True, "tests": [{"expectedStatus": "passed", "status": "expected",
                           "projectId": "chromium", "projectName": "chromium", "annotations": [], "results": [result]}]}]}]}
    return report, measurement, body + b"\n", command


class ReportContractTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path("/synthetic-ui-source").absolute()
        self.output = Path("/synthetic-ui-evidence").absolute()
        self.context = context()

    def validate(self, report, measurement, raw, command):
        return UI.validate_reports(report, measurement, raw, self.context, self.repo, self.output, command)

    def test_original_shape_recomputes_sixty_samples_and_rank_57(self):
        values = reports(self.repo, self.output, self.context)
        summary = self.validate(*values)
        self.assertEqual((summary["p95_ms"], summary["max_ms"]), (57, 60))
        self.assertEqual((summary["samples"], summary["scenes"], summary["result_count"]), (60, 12, 1))
        # A maximum above 100 is retained; the ORIGINAL contract gates p95 only.
        values = reports(self.repo, self.output, self.context, samples=[10] * 59 + [250])
        self.assertEqual(self.validate(*values)["max_ms"], 250)

    def test_skips_flaky_retries_xfail_and_additional_cases_are_rejected(self):
        mutations = (
            lambda r: r["stats"].update(skipped=1),
            lambda r: r["stats"].update(flaky=1),
            lambda r: r["stats"].update(expected=True),
            lambda r: r["suites"][0]["specs"].append(copy.deepcopy(r["suites"][0]["specs"][0])),
            lambda r: r["suites"][0]["specs"].clear(),
            lambda r: r["suites"][0]["specs"][0]["tests"][0].update(expectedStatus="failed"),
            lambda r: r["suites"][0]["specs"][0]["tests"][0].update(annotations=[{"type": "skip"}]),
            lambda r: r["suites"][0]["specs"][0]["tests"][0]["results"][0].update(retry=1),
            lambda r: r["suites"][0]["specs"][0]["tests"][0]["results"][0].update(retry=False),
            lambda r: r["suites"][0]["specs"][0]["tests"][0]["results"].append({"status": "passed"}),
            lambda r: r["suites"][0]["specs"][0]["tests"][0]["results"][0].update(status="interrupted"),
            lambda r: r["config"]["projects"][0].update(retries=1),
            lambda r: r["config"]["projects"][0].update(repeatEach=2),
        )
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                values = reports(self.repo, self.output, self.context)
                change(values[0])
                with self.assertRaises(ValueError):
                    self.validate(*values)

    def test_wrong_file_case_source_command_and_project_are_rejected(self):
        mutations = (
            lambda r: r["suites"][0]["specs"][0].update(file="other/performance.spec.ts"),
            lambda r: r["suites"][0]["specs"][0].update(line=26),
            lambda r: r["suites"][0]["specs"][0].update(id="another-case"),
            lambda r: r["config"]["metadata"]["ci"].update(commitHash="b" * 40),
            lambda r: r["config"]["argv"].__setitem__(3, "tests/other.spec.ts"),
            lambda r: r["config"].update(rootDir="/another-source/tests"),
            lambda r: r["config"]["projects"][0].update(name="webkit"),
            lambda r: r["config"]["projects"][0].update(outputDir="/other-evidence"),
        )
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                values = reports(self.repo, self.output, self.context)
                change(values[0])
                with self.assertRaises(ValueError):
                    self.validate(*values)

    def test_missing_or_rewritten_attachment_is_not_reconstructed(self):
        mutations = (
            lambda a: a.clear(),
            lambda a: a.append(copy.deepcopy(a[0])),
            lambda a: a[0].update(body=base64.b64encode(b"{}").decode()),
            lambda a: a[0].update(body="not-base64!"),
            lambda a: a[0].update(path="/outside/attachment.json"),
            lambda a: a[0].update(name="other"),
        )
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                values = reports(self.repo, self.output, self.context)
                change(values[0]["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"])
                with self.assertRaises(ValueError):
                    self.validate(*values)

    def test_coherent_but_wrong_measurement_is_rejected_without_tolerance_changes(self):
        mutations = (
            lambda m: m.update(source_sha="b" * 40),
            lambda m: m["dataset"].update(scenes=11),
            lambda m: m["dataset"].update(samples=59),
            lambda m: m.update(budget_ms=101),
            lambda m: m.update(p95_ms=56),
            lambda m: m.update(max_ms=59),
            lambda m: m["samples_ms"].pop(),
            lambda m: m["samples_ms"].__setitem__(0, True),
            lambda m: m["samples_ms"].__setitem__(0, -1),
            lambda m: m.update(samples_ms=[100] * 60, p95_ms=100, max_ms=100),
        )
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                report, measurement, _, command = reports(self.repo, self.output, self.context)
                change(measurement)
                body = json.dumps(measurement, indent=2).encode()
                report["suites"][0]["specs"][0]["tests"][0]["results"][0]["attachments"][0]["body"] = base64.b64encode(body).decode()
                with self.assertRaises(ValueError):
                    self.validate(report, measurement, body + b"\n", command)

    def test_nonfinite_and_duplicate_json_fields_are_not_silent_overrides(self):
        for raw in (b'{"x":1,"x":2}', b'{"samples_ms":[NaN]}', b'{"max_ms":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                UI.strict_json(raw)


class ContextAndInputTests(unittest.TestCase):
    def test_actual_manual_ui_context_is_required_without_observer_overrides(self):
        original = context()
        with environment(original):
            self.assertEqual(UI.actual_context(), original)
        for key, value in (("GITHUB_JOB", "browser-acceptance"), ("GITHUB_EVENT_NAME", "pull_request"),
                           ("GITHUB_WORKFLOW_SHA", "b" * 40), ("GITHUB_WORKFLOW_REF", "other/workflow"),
                           ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ID", "0"),
                           ("MOTIONWRIGHT_SERVICE_TEST_BIN", "service.exe"), ("NODE_OPTIONS", "--require=other.js")):
            with self.subTest(key=key), environment({**original, key: value}), self.assertRaises(ValueError):
                UI.actual_context()
        with environment(original), patch.dict(os.environ, {"MW_UI_PERFORMANCE_SCOPE": "all"}), self.assertRaises(ValueError):
            UI.actual_context()

    def test_provider_digest_is_checked_before_any_module_execution(self):
        raw = (ROOT / UI.PROVIDER).read_bytes()
        if hashlib.sha256(raw).hexdigest() != UI.PROVIDER_SHA256:
            raw = raw.replace(b"\r\n", b"\n")  # Test fixture view only, never runtime source normalization.
        self.assertEqual(hashlib.sha256(raw).hexdigest(), UI.PROVIDER_SHA256)
        self.assertTrue(callable(UI.load_provider(raw).file_record))
        with self.assertRaises(ValueError):
            UI.load_provider(raw + b"\nraise RuntimeError('unreviewed')\n")

    def test_cli_isolation_precedes_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / "observer.py"
            script.write_bytes((ROOT / UI.SELF).read_bytes())
            marker = base / "imported.txt"
            (base / "hashlib.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n", encoding="utf-8")
            for flags, expected in (([], 1), (["-I", "-B", "-S"], 0)):
                result = subprocess.run([sys.executable, *flags, str(script), "--help"], cwd=base,
                                        capture_output=True, timeout=15)
                self.assertEqual(result.returncode, expected, result.stderr.decode(errors="replace"))
                self.assertFalse(marker.exists())

    def test_bootstrap_reads_are_bounded_and_preserve_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.bin"
            raw = b"original\r\n\xff"
            path.write_bytes(raw)
            self.assertEqual(UI.read(path, len(raw)), raw)
            with patch.object(Path, "open", side_effect=AssertionError("oversized input must not be opened")):
                with self.assertRaises(ValueError):
                    UI.read(path, len(raw) - 1)
            self.assertEqual(path.read_bytes(), raw)


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)
        self.repo, self.output = self.root / "source", self.root / "observation"
        self.repo.mkdir()
        for name in UI.SOURCE_INPUTS:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            original = ROOT / name
            raw = original.read_bytes() if original.exists() else b"# synthetic source input\n"
            if name in UI.REVIEWED_INPUTS and hashlib.sha256(raw).hexdigest() != UI.REVIEWED_INPUTS[name]:
                raw = raw.replace(b"\r\n", b"\n")
                if hashlib.sha256(raw).hexdigest() != UI.REVIEWED_INPUTS[name]:
                    raise AssertionError("Fixture original source differs: " + name)
            path.write_bytes(raw)
        (self.repo / ".gitignore").write_text("apps/studio/node_modules/\ntooling/ignored-*/\n", encoding="utf-8")
        self.git("init", "--quiet")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic UI source, no product executed")
        self.sha = self.git("rev-parse", "HEAD").decode().strip()
        self.context = context(self.sha)
        self.marker = self.root / "calls.txt"

    def git(self, *args):
        env = {name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_GRAFT_FILE=os.devnull,
                   GIT_NO_REPLACE_OBJECTS="1")
        result = subprocess.run(["git", "--no-replace-objects", "-c", "core.autocrlf=false",
                                 "-c", "user.name=Synthetic UI fixture", "-c", "user.email=fixture@example.invalid",
                                 "-c", "core.hooksPath=" + str(self.root / "no-hooks"), "-C", str(self.repo), *args],
                                env=env, capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stderr.decode(errors="replace"))
        return result.stdout

    def prepare(self, exit_code=0):
        report, measurement, raw, _ = reports(self.repo, self.output, self.context)
        self.script = self.root / "synthetic_child.py"
        self.script.write_text(
            "import os,sys\nfrom pathlib import Path\n"
            + f"with Path({str(self.marker)!r}).open('a') as f: f.write('one\\n')\n"
            + "sys.stdout.buffer.write(b'original\\r\\nbytes\\xff')\n"
            + "sys.stderr.buffer.write(b'original stderr\\x00\\r\\n')\n"
            + "Path(os.environ['PLAYWRIGHT_JSON_OUTPUT_FILE']).write_bytes(" + repr(json.dumps(report).encode()) + ")\n"
            + "Path(os.environ['MOTIONWRIGHT_UI_PERF_REPORT']).write_bytes(" + repr(raw) + ")\n"
            + "sys.exit(" + str(exit_code) + ")\n", encoding="utf-8")

    def runtime(self, provider, repo, original_lock, paths):
        return {"synthetic_runtime_only": True, "executables": {
            name: {"path": str(path), **provider.file_record(path)} for name, path in paths.items()}}

    @contextmanager
    def plumbing(self):
        original_popen = subprocess.Popen

        def child(command, **kwargs):
            # The desired browser argv remains observable; no browser is launched.
            if isinstance(command, list) and len(command) > 1 and command[1] == "playwright":
                return original_popen([sys.executable, "-I", "-B", str(self.script)], **kwargs)
            return original_popen(command, **kwargs)

        with environment(self.context), patch.object(UI, "runtime", self.runtime), \
                patch.object(UI.shutil, "which", return_value=sys.executable), patch.object(UI.subprocess, "Popen", child):
            yield

    def receipt(self, name="receipt.json"):
        return json.loads((self.output / name).read_bytes())


class GitAndProcessTests(unittest.TestCase):
    def test_real_git_rejects_changed_and_ignored_sources_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            with environment(f.context):
                observed, _ = UI.source(f.repo, f.context)
                self.assertEqual(observed["source_sha"], f.sha)
                folder = f.repo / "tooling/ignored-package"
                folder.mkdir()
                (folder / "__init__.py").write_text("raise RuntimeError('must not import')\n", encoding="utf-8")
                with patch.object(UI.subprocess, "Popen", side_effect=AssertionError("No child")):
                    # This top-level identity rejection precedes Git as well as
                    # the browser process. The ignored-source guard is read below.
                    with patch.dict(os.environ, {"GITHUB_JOB": "browser-acceptance"}):
                        self.assertEqual(UI.capture(f.repo, f.output), 2)
                self.assertEqual(f.receipt()["state"], "NOT_STARTED")
                with self.assertRaises(ValueError):
                    UI.source(f.repo, f.context)
                (folder / "__init__.py").unlink()
                folder.rmdir()
                (f.repo / UI.SPEC).write_bytes((f.repo / UI.SPEC).read_bytes() + b"// changed\n")
                with self.assertRaises(ValueError):
                    UI.source(f.repo, f.context)

    def test_raw_git_replace_cannot_relabel_modified_selection_as_original(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            first = f.sha
            (f.repo / UI.SPEC).write_bytes((f.repo / UI.SPEC).read_bytes() + b"// different profile\n")
            f.git("add", ".")
            f.git("commit", "--quiet", "-m", "Synthetic unreviewed profile")
            second = f.git("rev-parse", "HEAD").decode().strip()
            f.git("replace", second, first)
            with environment(context(second)), self.assertRaises(ValueError):
                UI.source(f.repo, context(second))
            self.assertIn(b"different profile", f.git("show", second + ":" + UI.SPEC))

    def test_failed_process_preserves_exit_raw_streams_and_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            f.prepare(exit_code=7)
            with f.plumbing():
                self.assertEqual(UI.capture(f.repo, f.output), 7)
                self.assertEqual(UI.verify(f.repo, f.output, "failure", "failure"), 2)
                with self.assertRaises(FileExistsError):
                    UI.capture(f.repo, f.output)
            self.assertEqual(f.marker.read_text(), "one\n")
            self.assertEqual(f.receipt("process-outcome.json")["original_exit_code"], 7)
            self.assertEqual((f.output / "stdout.log").read_bytes(), b"original\r\nbytes\xff")
            self.assertEqual((f.output / "stderr.log").read_bytes(), b"original stderr\0\r\n")
            self.assertFalse(f.receipt("admission.json")["test_case_results_admitted"])

    def test_synthetic_plumbing_positive_and_no_release_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            f.prepare()
            with f.plumbing():
                self.assertEqual(UI.capture(f.repo, f.output), 0)
                self.assertEqual(UI.verify(f.repo, f.output, "success", "success"), 0)
            admission = f.receipt("admission.json")
            self.assertEqual(admission["state"], "SCOPED_UI_PERFORMANCE_OBSERVED")
            self.assertTrue(all(value is False for value in admission["claims"].values()))
            self.assertEqual(f.marker.read_text(), "one\n")

    def test_cancelled_or_stale_attempt_cannot_admit_exit_zero(self):
        for change, step, job in (({}, "success", "cancelled"), ({"GITHUB_RUN_ATTEMPT": "2"}, "success", "success"),
                                  ({"GITHUB_RUN_ID": "102"}, "success", "success")):
            with self.subTest(change=change, job=job), tempfile.TemporaryDirectory() as directory:
                f = Fixture(directory)
                f.prepare()
                with f.plumbing():
                    self.assertEqual(UI.capture(f.repo, f.output), 0)
                    with patch.dict(os.environ, change):
                        self.assertEqual(UI.verify(f.repo, f.output, step, job), 2)
                self.assertFalse(f.receipt("admission.json")["test_case_results_admitted"])

    def test_original_outcome_is_durable_before_post_source_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            f.prepare(exit_code=7)
            original_source, calls = UI.source, []

            def observe(repo, ctx):
                calls.append(1)
                if len(calls) == 2:
                    self.assertEqual(f.receipt("process-outcome.json")["original_exit_code"], 7)
                    raise ValueError("synthetic source mutation after exit")
                return original_source(repo, ctx)

            with f.plumbing(), patch.object(UI, "source", observe):
                self.assertEqual(UI.capture(f.repo, f.output), 7)
            self.assertIn("source mutation", f.receipt()["errors"][0])
            self.assertEqual(f.marker.read_text(), "one\n")

    def test_unknown_does_not_poll_wait_again_kill_or_launch_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(directory)
            f.prepare()
            original_popen, waits, launches = subprocess.Popen, [], []

            class Unknown:
                pid = 12345

                def wait(self, timeout):
                    waits.append(timeout)
                    raise subprocess.TimeoutExpired("synthetic boundary", timeout)

            def launch(command, **kwargs):
                if len(command) > 1 and command[1] == "playwright":
                    launches.append(command)
                    return Unknown()
                return original_popen(command, **kwargs)

            with environment(f.context), patch.object(UI, "runtime", f.runtime), \
                    patch.object(UI.shutil, "which", return_value=sys.executable), patch.object(UI.subprocess, "Popen", launch):
                self.assertEqual(UI.capture(f.repo, f.output), 124)
                self.assertEqual(UI.verify(f.repo, f.output, "failure", "failure"), 2)
            self.assertEqual(waits, [UI.COMMAND_TIMEOUT_SECONDS])
            self.assertEqual(len(launches), 1)
            self.assertFalse(f.marker.exists())
            self.assertEqual(f.receipt("process-outcome.json")["state"], "UNKNOWN")
            self.assertIsNone(f.receipt("process-outcome.json")["original_exit_code"])


if __name__ == "__main__":
    unittest.main()
