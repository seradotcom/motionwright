"""Synthetic ALab source-report retention; never run an owner/media operation."""
import ast
import copy
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock

from tooling import ci_alab_source_control_custody as gate


class SourceControlCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="alab-source-custody-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / "inputs"; self.inputs.mkdir()
        self.outputs = self.root / "outputs"; self.outputs.mkdir()
        self.identity = self.context("g0")
        self.source = None

    def context(self, profile, attempt="1"):
        return gate.context({"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux", "GITHUB_SHA": "a" * 40,
            "GITHUB_JOB": gate.PROFILES[profile]["job"], "GITHUB_RUN_ID": "1234567", "GITHUB_RUN_ATTEMPT": attempt,
            "GITHUB_EVENT_NAME": "workflow_dispatch", "MOTIONWRIGHT_KDENLIVE_GUI_SCOPE": "external-alab-readonly",
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/v05-otio-real-media.yml@refs/heads/synthetic"}, profile, "a" * 40)

    def seed(self, profile="g0"):
        self.identity = self.context(profile)
        paths = {gate.WORKFLOW, gate.HELPER, gate.RUNNER, gate.CONTROL}
        paths.update(path for _, cases in gate.PROFILES[profile]["reports"] for path, _ in cases)
        hashes, rosters = {}, {}
        for relative in paths:
            raw = (gate.ROOT / relative).read_bytes()
            hashes[relative] = gate.sha(raw)
            if relative.endswith(".py"):
                rosters[relative] = sorted(Path(relative).stem + "." + cls.name + "." + method.name
                    for cls in ast.parse(raw).body if isinstance(cls, ast.ClassDef)
                    for method in cls.body if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"))
        self.source = {"executed_source_sha": self.identity["source_sha"], "tree_sha": "b" * 40,
                       "files": hashes, "rosters": rosters}
        for stem, cases in gate.PROFILES[profile]["reports"]:
            steps = []
            for index, (path, total) in enumerate(cases):
                self.assertEqual(len(rosters[path]), total)
                stderr = "".join(name.rsplit(".", 1)[-1] + " (" + name + ") ... ok\n" for name in rosters[path])
                stderr += "\n----------------------------------------------------------------------\nRan " + str(total) + " tests in 0.001s\n\nOK\n"
                output = {"stdout": b"synthetic source-control bytes only\n", "stderr": stderr.encode()}
                step = {"entry_point": "discovery:" + Path(path).name,
                    "command": ["/synthetic/python", "-B", "-E", "-s", "-m", "unittest", "discover", "-s",
                        Path(path).parent.as_posix(), "-p", Path(path).name, "-v"],
                    "exit_code": 0, "status": "PASS", "counts": gate.runner.parse_unittest_summary(stderr)}
                for channel, raw in output.items():
                    name = f"{stem}-{index:02d}-{channel}.log"
                    (self.inputs / name).write_bytes(raw)
                    step[channel] = {"file": name, "sha256": gate.sha(raw)}
                steps.append(step)
            report = {"schema": "motionwright.unittest-execution/1", "executed_source_sha": self.identity["source_sha"],
                "tree_sha": self.source["tree_sha"], "runner_sha256": hashes[gate.RUNNER],
                "test_definition_sha256": {path: hashes[path] for path, _ in cases}, "require_complete": True,
                "scope": "Selected unittest entry points; not installed/native/human acceptance",
                "github": {"GITHUB_SHA": self.identity["source_sha"], "GITHUB_RUN_ID": self.identity["run_id"],
                    "GITHUB_RUN_ATTEMPT": self.identity["run_attempt"], "GITHUB_JOB": self.identity["job"],
                    "GITHUB_WORKFLOW_REF": self.identity["workflow_ref"]}, "steps": steps, "exit_code": 0, "status": "PASS"}
            (self.inputs / (stem + ".json")).write_bytes(gate.json_bytes(report))

    def mutate_report(self, fn, stem="alab-caller-g0"):
        path = self.inputs / (stem + ".json")
        value = json.loads(path.read_bytes()); fn(value); path.write_bytes(gate.json_bytes(value))

    def publish(self, *, outcome="success", changed=False):
        after = {**self.source, "tree_sha": "c" * 40} if changed else self.source
        with mock.patch.object(gate, "observed_source", side_effect=[self.source, after]):
            return gate.publish(gate.ROOT, self.inputs, self.outputs, self.identity, outcome)

    def archive(self, ready):
        folder = Path(ready["directory"])
        self.assertEqual({p.name for p in folder.iterdir()}, set(gate.OUTPUT_NAMES))
        raw = (folder / gate.OUTPUT_NAMES[0]).read_bytes()
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            self.assertTrue(all(member.isfile() for member in archive.getmembers()))
            return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}

    def test_g0_retains_five_exact_originals_and_closed_manifest(self):
        self.seed(); ready, report = self.publish()
        self.assertTrue(ready["ready"] and ready["verified"])
        self.assertEqual(report["status"], "SOURCE_REPORTS_BOUND")
        originals = self.archive(ready)
        self.assertEqual(set(originals), set(gate.expected_names("g0")))
        self.assertEqual(len(originals), 5)
        for name, raw in originals.items():
            self.assertEqual(raw, (self.inputs / name).read_bytes())
            self.assertEqual(gate.sha(raw), report["originals"][name]["sha256"])
        self.assertFalse(report["human_acceptance"])
        self.assertEqual(report["http_media_ffprobe"], "NOT_ACCESSED")

    def test_alab_retains_caller_and_bridge_eight_fixed_files(self):
        self.seed("alab"); ready, report = self.publish()
        self.assertTrue(ready["verified"])
        self.assertEqual(len(self.archive(ready)), 8)
        self.assertEqual([r["tests_run"] for r in report["reports"]], [45 + gate.CUSTODY_TEST_COUNT, 94])
        self.assertEqual(report["native_execution"], "NOT_RUN")

    def test_failure_report_is_retained_without_promotion(self):
        self.seed(); self.mutate_report(lambda r: r.update(status="FAIL", exit_code=1))
        ready, report = self.publish(outcome="failure")
        self.assertFalse(ready["verified"])
        self.assertEqual(report["reports"][0]["reported_status"], "FAIL")
        self.assertEqual(report["status"], "NOT_ADMITTED")
        self.assertIn("alab-caller-g0.json", self.archive(ready))

    def test_unknown_survives_missing_stream_and_source_change(self):
        self.seed(); self.mutate_report(lambda r: r.update(status="UNKNOWN", exit_code=124))
        (self.inputs / "alab-caller-g0-00-stderr.log").unlink()
        ready, report = self.publish(outcome="failure", changed=True)
        self.assertEqual(ready["status"], "UNKNOWN")
        self.assertFalse(ready["verified"])
        self.assertFalse(report["automatic_retry"])
        self.assertFalse(report["source_unchanged"])

    def test_absent_reports_produce_only_bounded_not_admitted_forensics(self):
        self.seed()
        for path in self.inputs.iterdir(): path.unlink()
        ready, report = self.publish(outcome="skipped")
        self.assertFalse(ready["verified"])
        self.assertEqual(self.archive(ready), {})
        self.assertEqual(report["reports"][0]["reported_status"], "NOT_RUN")
        self.assertEqual(set(report["rejected"]), set(gate.expected_names("g0")))
        self.outputs = self.root / "failure-outputs"; self.outputs.mkdir()
        ready, report = self.publish(outcome="failure")
        self.assertFalse(ready["verified"])
        self.assertEqual(report["reports"][0]["reported_status"], "NOT_OBSERVED")
        self.outputs = self.root / "cancelled-outputs"; self.outputs.mkdir()
        ready, report = self.publish(outcome="cancelled")
        self.assertEqual(ready["status"], "UNKNOWN")
        self.assertEqual(report["reports"][0]["reported_status"], "NOT_OBSERVED")

    def test_missing_log_refuses_complete_custody_and_preserves_other_originals(self):
        self.seed(); (self.inputs / "alab-caller-g0-01-stdout.log").unlink()
        ready, report = self.publish()
        self.assertFalse(ready["verified"])
        self.assertEqual(len(self.archive(ready)), 4)
        self.assertIn("alab-caller-g0-01-stdout.log", report["rejected"])

    def test_changed_stream_hash_is_never_bound(self):
        self.seed(); (self.inputs / "alab-caller-g0-00-stdout.log").write_bytes(b"changed")
        ready, report = self.publish()
        self.assertFalse(ready["verified"])
        self.assertFalse(report["reports"][0]["source_bound"])
        self.assertEqual(self.archive(ready)["alab-caller-g0-00-stdout.log"], b"changed")

    def test_other_sha_tree_run_job_or_attempt_is_rejected(self):
        self.seed()
        baseline = (self.inputs / "alab-caller-g0.json").read_bytes()
        for section, key, replacement in ((None, "executed_source_sha", "d" * 40), (None, "tree_sha", "e" * 40),
                ("github", "GITHUB_RUN_ID", "999"), ("github", "GITHUB_JOB", "other"), ("github", "GITHUB_RUN_ATTEMPT", "2")):
            value = json.loads(baseline)
            (value if section is None else value[section])[key] = replacement
            files = {p.name: p.read_bytes() for p in self.inputs.iterdir()}
            files["alab-caller-g0.json"] = gate.json_bytes(value)
            with self.subTest(key=key), self.assertRaises(gate.Rejected):
                gate.verify_report("alab-caller-g0", gate.CALLER_CASES, files, self.source, self.identity)

    def test_runner_test_source_and_nonobject_reports_are_rejected(self):
        self.seed(); originals = {p.name: p.read_bytes() for p in self.inputs.iterdir()}
        for key in ("runner_sha256", "test_definition_sha256"):
            value = json.loads(originals["alab-caller-g0.json"]); value[key] = "0" * 64
            files = {**originals, "alab-caller-g0.json": gate.json_bytes(value)}
            with self.assertRaises(gate.Rejected): gate.verify_report("alab-caller-g0", gate.CALLER_CASES, files, self.source, self.identity)
        (self.inputs / "alab-caller-g0.json").write_bytes(b"[]")
        ready, report = self.publish()
        self.assertFalse(ready["verified"])
        self.assertIn("alab-caller-g0.json", self.archive(ready))

    def test_alab_rerun_is_rejected_and_g0_attempt_is_bound_to_its_own_receipt(self):
        with self.assertRaises(gate.Rejected): self.context("alab", attempt="2")
        self.assertEqual(self.context("g0", attempt="2")["run_attempt"], "2")
        self.seed(); self.identity["run_attempt"] = "2"
        ready, _ = self.publish()
        self.assertFalse(ready["verified"])

    def test_skip_or_nominal_omission_cannot_use_an_ok_footer(self):
        self.seed(); base = {p.name: p.read_bytes() for p in self.inputs.iterdir()}
        name = "alab-caller-g0-00-stderr.log"
        for altered in (base[name].replace(b" ... ok", b" ... skipped 'synthetic'", 1),
                        b"\n".join(base[name].split(b"\n")[1:])):
            files = {**base, name: altered}
            report = json.loads(files["alab-caller-g0.json"])
            report["steps"][0]["stderr"]["sha256"] = gate.sha(altered)
            files["alab-caller-g0.json"] = gate.json_bytes(report)
            with self.assertRaises(gate.Rejected): gate.verify_report("alab-caller-g0", gate.CALLER_CASES, files, self.source, self.identity)

    def test_unreviewed_commands_extra_steps_and_boolean_exits_are_rejected(self):
        self.seed(); original = {p.name: p.read_bytes() for p in self.inputs.iterdir()}
        for change in ("command", "steps", "bool"):
            report = json.loads(original["alab-caller-g0.json"])
            if change == "command": report["steps"][0]["command"][1] = "-c"
            elif change == "steps": report["steps"].append(copy.deepcopy(report["steps"][0]))
            else: report["steps"][0]["exit_code"] = False
            files = {**original, "alab-caller-g0.json": gate.json_bytes(report)}
            with self.assertRaises(gate.Rejected): gate.verify_report("alab-caller-g0", gate.CALLER_CASES, files, self.source, self.identity)

    def test_links_special_files_and_parent_aliases_are_rejected_without_following(self):
        target = self.root / "outside"; target.write_bytes(b"must remain outside")
        (self.inputs / "symlink").symlink_to(target)
        os.link(target, self.inputs / "hardlink")
        os.mkfifo(self.inputs / "fifo")
        (self.inputs / "directory").mkdir()
        held = gate.FlatRoot(self.inputs)
        try:
            for name in ("symlink", "hardlink", "fifo", "directory", "../outside"):
                with self.subTest(name=name), self.assertRaises(gate.Rejected): held.read(name, 1024)
        finally: held.close()
        alias = self.root / "alias"; alias.symlink_to(self.inputs, target_is_directory=True)
        with self.assertRaises(gate.Rejected): gate.FlatRoot(alias)
        self.assertEqual(target.read_bytes(), b"must remain outside")

    def test_size_limits_reject_oversized_stream_before_open(self):
        self.seed(); path = self.inputs / "alab-caller-g0-00-stdout.log"
        with path.open("wb") as stream: stream.truncate(gate.STREAM_LIMIT + 1)
        held = gate.FlatRoot(self.inputs)
        try:
            with mock.patch.object(os, "open", wraps=os.open) as opened:
                with self.assertRaises(gate.Rejected): held.read(path.name, gate.STREAM_LIMIT)
                opened.assert_not_called()
        finally: held.close()
        ready, report = self.publish()
        self.assertFalse(ready["verified"])
        self.assertNotIn(path.name, self.archive(ready))

    def test_nonallowlisted_media_http_paths_are_never_opened_or_archived(self):
        self.seed()
        for name in ("archive.mov", "http.original.bin", "ffprobe.json", "admission.json"):
            os.mkfifo(self.inputs / name)
        (self.inputs / "alab-readonly-original").mkdir()
        with mock.patch.object(gate.FlatRoot, "read", autospec=True, side_effect=gate.FlatRoot.read) as observed:
            # Fixed source originals + closed output readback; no directory traversal.
            ready, _ = self.publish()
        read_names = {call.args[1] for call in observed.call_args_list}
        self.assertEqual(read_names, set(gate.expected_names("g0")) | set(gate.OUTPUT_NAMES))
        self.assertTrue(ready["verified"])
        self.assertEqual(set(self.archive(ready)), set(gate.expected_names("g0")))

    def test_source_change_during_capture_prevents_credit(self):
        self.seed(); ready, report = self.publish(changed=True)
        self.assertFalse(ready["verified"])
        self.assertFalse(report["source_unchanged"])
        self.assertEqual(report["status"], "NOT_ADMITTED")

    def test_fresh_output_is_not_reused_and_github_output_is_closed(self):
        self.seed(); ready, _ = self.publish()
        with self.assertRaises(FileExistsError): self.publish()
        channel = self.root / "github-output"; channel.write_bytes(b"")
        gate.github_output(ready, channel)
        fields = dict(line.split("=", 1) for line in channel.read_text().splitlines())
        self.assertEqual(set(fields), {"ready", "verified", "directory", "manifest_sha256"})
        self.assertEqual(fields["directory"], ready["directory"])
        alias = self.root / "channel-alias"; alias.symlink_to(channel)
        with self.assertRaises(gate.Rejected): gate.github_output(ready, alias)

    def test_workflow_separates_source_artifact_before_effect_and_keeps_media_sealed(self):
        text = (gate.ROOT / gate.WORKFLOW).read_text()
        self.assertEqual(text.count("--pattern test_ci_alab_source_control_custody.py"), 2)
        self.assertIn("steps.alab_controls_custody.outputs.verified == 'true'", text)
        self.assertLess(text.index("Retain the closed ALab alab source-control artifact"), text.index("Run one admitted read-only ALab metadata observation"))
        for profile in ("g0", "alab"):
            block = text.split("      - name: Retain the closed ALab " + profile + " source-control artifact\n", 1)[1].split("      - ", 1)[0]
            self.assertIn("/source-control-originals.tar", block)
            self.assertIn("/source-control-custody.json", block)
            self.assertNotIn("alab-readonly-original", block)
            self.assertNotIn("*.log", block)
        sealed = text.split("      - name: Retain only sealed originals", 1)[1].split("      - name:", 1)[0]
        self.assertIn("/original-evidence.mwseal", sealed)
        self.assertIn("/projection.json", sealed)
        self.assertNotIn("source-control", sealed)
        caller = (gate.ROOT / "tooling/ci_otio_alab_readonly.py").read_text()
        self.assertIn('"tooling/ci_alab_source_control_custody.py"', caller)
        self.assertIn('"tooling/ci_test_runner.py"', caller)


if __name__ == "__main__":
    unittest.main()
