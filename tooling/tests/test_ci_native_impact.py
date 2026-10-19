"""Synthetic contracts for exact native-impact selection and its receipts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "ci_native_impact.py"
SPEC = importlib.util.spec_from_file_location("native_impact_contract_module", SOURCE)
ci = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ci
SPEC.loader.exec_module(ci)


def temporary():
    root = os.environ.get("MW_NATIVE_IMPACT_TEST_TMP") or os.environ.get("RUNNER_TEMP")
    return tempfile.TemporaryDirectory(prefix="native-impact-contract-", dir=root)


def row(data):
    return ("100644", "blob", ci.git_object("blob", data), len(data))


class SourceDataContracts(unittest.TestCase):
    def test_json_rejects_duplicates_and_nonfinite_numbers(self):
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(data=data), self.assertRaises(ci.Refused):
                ci.strict_json(data)
        self.assertEqual(ci.strict_json(b'{"a":1}'), {"a": 1})

    def test_inventory_rejects_unsafe_aliases_modes_and_duplicates(self):
        entry = b"100644 blob " + b"1" * 40 + b" 1\t"
        for names in ([b"../x"], [b"a\\x"], [b"/x"], [b".git/x"],
                      [b"x", b"x"], [b"X", b"x"], [b"a/x", b"A/y"]):
            with self.subTest(names=names), self.assertRaises(ci.Refused):
                ci.parse_inventory(b"".join(entry + name + b"\0" for name in names))
        with self.assertRaises(ci.Refused):
            ci.parse_inventory(b"120000 blob " + b"1" * 40 + b" 1\tx\0")
        with self.assertRaises(ci.Refused):
            ci.parse_inventory(entry + b"x")

    def test_raw_commit_identity_and_tree_shape_are_not_metadata_trust(self):
        data = b"tree " + b"1" * 40 + b"\nauthor Fixture <fixture@example.invalid> 1 +0000\n\nx\n"
        sha = ci.git_object("commit", data)
        self.assertEqual(ci.parse_commit(data, sha)["tree"], "1" * 40)
        with self.assertRaises(ci.Refused):
            ci.parse_commit(data + b"x", sha)
        with self.assertRaises(ci.Refused):
            ci.rebuilt_tree({"a": row(b"x"), "a/b": row(b"y")})

    def test_positive_run_integer_rejects_bool_zero_and_noncanonical_text(self):
        self.assertEqual(ci.positive_integer("2", "attempt"), 2)
        for value in (True, 1, "0", "01", "-1", "1.0", "", None):
            with self.subTest(value=value), self.assertRaises(ci.Refused):
                ci.positive_integer(value, "attempt")

    def test_regular_file_growth_between_stat_and_open_is_rejected(self):
        with temporary() as tmp:
            path = Path(tmp) / "input"
            path.write_bytes(b"abcd")
            original = Path.open
            changed = False

            def changing_open(candidate, mode="r", *args, **kwargs):
                nonlocal changed
                if candidate == path and mode == "rb" and not changed:
                    changed = True
                    with original(path, "ab") as stream:
                        stream.write(b"x" * 64)
                return original(candidate, mode, *args, **kwargs)

            with mock.patch.object(Path, "open", changing_open):
                with self.assertRaises(ci.Refused):
                    ci.regular_bytes(path, 8)
            self.assertTrue(changed)

    def test_workflow_reconstruction_preserves_native_commands(self):
        for path, rules in ci.MOVED_CONTRACTS.items():
            with self.subTest(path=path):
                original = "jobs:\n  producer:\n    run: exact-original-native-command\n"
                original += "".join(before for _marker, before in rules)
                changed = original
                for marker, before in rules:
                    changed = changed.replace(before, marker, 1)
                for name in ("preflight", "dependency", "triggers"):
                    changed += ("  # native-impact-" + name + "-begin\n"
                                "  added-gate-step\n"
                                "  # native-impact-" + name + "-end\n")
                self.assertEqual(ci.reviewed_workflow_bytes(path, changed.encode()), original.encode())
                mutated = changed.replace("exact-original-native-command", "different-command")
                self.assertNotEqual(ci.reviewed_workflow_bytes(path, mutated.encode()), original.encode())
                with self.assertRaises(ci.Refused):
                    ci.reviewed_workflow_bytes(path, changed.replace(
                        "# native-impact-dependency-end", "# lost-boundary").encode())
        original_ci = b"name: CI\njobs:\n  rust-tests:\n    run: " + ci.G1_COMMAND.encode() + b"\n"
        added = (original_ci + b"  # native-impact-tests-begin\n  G0\n"
                 b"  # native-impact-tests-end\n")
        self.assertEqual(ci.reviewed_workflow_bytes(ci.CI_PATH, added), original_ci)

    def test_event_binds_real_workflow_job_ref_and_attempt(self):
        with temporary() as tmp:
            root = Path(tmp).resolve()
            wf = next(iter(ci.WORKFLOWS))
            env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": ci.REPOSITORY,
                   "GITHUB_JOB": "native-impact", "GITHUB_WORKFLOW": ci.WORKFLOWS[wf]["title"],
                   "GITHUB_SHA": "1" * 40, "GITHUB_WORKFLOW_SHA": "1" * 40,
                   "GITHUB_REF": "refs/pull/7/merge",
                   "GITHUB_WORKFLOW_REF": ci.REPOSITORY + "/" + wf + "@refs/pull/7/merge",
                   "GITHUB_WORKSPACE": str(root), "GITHUB_EVENT_NAME": "pull_request",
                   "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
            event = {"repository": {"full_name": ci.REPOSITORY}, "number": 7,
                     "pull_request": {"base": {"sha": "2" * 40, "ref": "main",
                                               "repo": {"full_name": ci.REPOSITORY}},
                                      "head": {"sha": "3" * 40}}}
            self.assertEqual(ci.event_context(env, wf, root, event)["run_attempt"], 2)
            for key, value in (("GITHUB_JOB", "another-job"), ("GITHUB_WORKFLOW_SHA", "2" * 40),
                               ("GITHUB_WORKFLOW_REF", ci.REPOSITORY + "/" + wf + "@refs/heads/main"),
                               ("GITHUB_RUN_ATTEMPT", "0")):
                changed = dict(env, **{key: value})
                with self.subTest(key=key), self.assertRaises(ci.Refused):
                    ci.event_context(changed, wf, root, event)
            altered = copy.deepcopy(event)
            altered["number"] = True
            with self.assertRaises(ci.Refused):
                ci.event_context(env, wf, root, altered)


class ReviewedProfileContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = temporary()
        cls.root = Path(cls.tmp.name).resolve() / "repo"
        cls.root.mkdir()
        cls.hooks = Path(cls.tmp.name) / "hooks"
        cls.hooks.mkdir()
        cls.git_path = Path(shutil.which("git")).resolve(strict=True)
        cls.env = ci.hardened_env()
        cls.env.update(GIT_AUTHOR_NAME="Verification Fixture",
                       GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="Verification Fixture",
                       GIT_COMMITTER_EMAIL="fixture@example.invalid",
                       GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00",
                       GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")
        cls.git("init", "--initial-branch=main", "--template=" + str(cls.hooks))
        cls.engine = ci.ENGINE_PATHS
        files = {p: ("integrated-engine:" + p + "\n").encode() for p in cls.engine}
        files.update({"storage/cas.rs": b"before-exact-cas\n", "inputs/runtime.lock": b"same-runtime\n"})
        for path, data in files.items():
            target = cls.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        cls.git("add", "--", *sorted(files))
        cls.git("commit", "-m", "integrated gate fixture")
        base = cls.git("rev-parse", "HEAD").strip().decode()
        (cls.root / "storage/cas.rs").write_bytes(b"after-exact-cas\n")
        (cls.root / "docs").mkdir()
        (cls.root / "docs/cas.md").write_bytes(b"exact explanatory document\n")
        cls.git("add", "--", "storage/cas.rs", "docs/cas.md")
        cls.git("commit", "-m", "exact product change")
        head = cls.git("rev-parse", "HEAD").strip().decode()
        tree = cls.git("rev-parse", "HEAD^{tree}").strip().decode()
        merge = cls.git("commit-tree", tree, "-p", base, "-p", head,
                        input_data=b"actual synthetic merge object\n").strip().decode()
        cls.git("update-ref", "refs/heads/main", merge)
        cls.commits = {name: ci.parse_commit(cls.git("cat-file", "commit", sha), sha)
                       for name, sha in (("base", base), ("head", head), ("merge", merge))}
        cls.inventories = {name: ci.parse_inventory(cls.git(
            "ls-tree", "-r", "-z", "--full-tree", "--long", commit["sha"]))
                           for name, commit in cls.commits.items()}
        for name, inv in cls.inventories.items():
            if ci.rebuilt_tree(inv) != cls.commits[name]["tree"]:
                raise AssertionError("synthetic objects did not match real Git trees")
        cls.context = {"event_name": "pull_request", "base_ref": "main",
                       "base_sha": base, "head_sha": head, "checkout_sha": merge}
        cls.profile = {"id": "unit-fixture-only",
                       "before_product": ci.product_fingerprint(cls.inventories["base"], cls.engine),
                       "after_product": ci.product_fingerprint(cls.inventories["head"], cls.engine),
                       "changes": {p: (cls.inventories["base"].get(p),
                                       cls.inventories["head"].get(p))
                                   for p in ("storage/cas.rs", "docs/cas.md")}}
        cls.review = {p: {"reviewed_payload_matches": True} for p in (ci.CI_PATH, *ci.WORKFLOWS)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def git(cls, *args, input_data=None):
        result = subprocess.run([str(cls.git_path), "--no-replace-objects",
                "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
                "-c", "core.hooksPath=" + str(cls.hooks), "-c", "commit.gpgsign=false",
                *args], cwd=cls.root, env=cls.env, input=input_data,
                capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stderr.decode("utf-8", "replace"))
        return result.stdout

    def choose(self, *, context=None, commits=None, inventories=None, review=None, advanced=False):
        return ci.select_profile(context if context is not None else self.context,
                                 commits if commits is not None else self.commits,
                                 inventories if inventories is not None else self.inventories,
                                 review if review is not None else self.review,
                                 profile=self.profile, engine_paths=self.engine, advanced_base=advanced)

    def full(self, result):
        self.assertTrue(result["native_required"])
        self.assertFalse(result["native_pass"])
        self.assertFalse(result["native_executed"])
        self.assertEqual(result["g1"], "REQUIRED_SEPARATE_CI")
        self.assertIsNone(result["candidate_sha"])
        self.assertIsNone(result["observed_minutes_saved"])

    def test_integrated_gate_exact_pair_is_reachable_without_self_hash_cycle(self):
        result = self.choose()
        self.assertFalse(result["native_required"])
        self.assertEqual(result["decision"], "NOT_SELECTED_REVIEWED_CAS_PROFILE")
        self.assertEqual(result["observed_jobs_avoided"], 0)
        self.assertFalse(result["native_pass"])
        self.assertEqual(self.commits["merge"]["parents"],
                         [self.commits["base"]["sha"], self.commits["head"]["sha"]])
        self.assertEqual(self.commits["merge"]["tree"], self.commits["head"]["tree"])

    def test_initial_gate_adoption_and_each_engine_change_select_full(self):
        changed = copy.deepcopy(self.inventories)
        for path in self.engine:
            changed["base"].pop(path)
        self.full(self.choose(inventories=changed))
        for path in self.engine:
            changed = copy.deepcopy(self.inventories)
            changed["head"][path] = row(b"new engine")
            changed["merge"][path] = changed["head"][path]
            with self.subTest(path=path):
                self.full(self.choose(inventories=changed))

    def test_unknown_product_dependency_or_new_file_selects_full(self):
        for path in ("inputs/runtime.lock", "tooling/new_runtime.py", "docs/other.md"):
            changed = copy.deepcopy(self.inventories)
            changed["head"][path] = row(b"changed dependency")
            changed["merge"][path] = changed["head"][path]
            with self.subTest(path=path):
                self.full(self.choose(inventories=changed))

    def test_baseline_drift_invalidates_review_even_when_diff_is_exact_pair(self):
        changed = copy.deepcopy(self.inventories)
        for key in changed:
            changed[key]["inputs/runtime.lock"] = row(b"new identical runtime in all three")
        self.full(self.choose(inventories=changed))

    def test_wrong_cas_bytes_mode_or_document_never_matches_by_path_alone(self):
        for path in self.profile["changes"]:
            for value in (row(b"other bytes"), ("100755", *self.inventories["head"][path][1:])):
                changed = copy.deepcopy(self.inventories)
                changed["head"][path] = value
                changed["merge"][path] = value
                with self.subTest(path=path, value=value):
                    self.full(self.choose(inventories=changed))

    def test_manual_candidate_and_other_events_keep_original_full_scope(self):
        for event in ("workflow_dispatch", "push", "release", "merge_group"):
            with self.subTest(event=event):
                result = self.choose(context=dict(self.context, event_name=event))
                self.full(result)
                self.assertEqual(result["decision"], "REQUIRED_FULL_EVENT")
        self.full(self.choose(context=dict(self.context, base_ref="other")))

    def test_wrong_merge_parents_or_source_identity_are_rejected(self):
        for parents in ([self.commits["head"]["sha"], self.commits["base"]["sha"]],
                        [self.commits["base"]["sha"]], ["f" * 40, self.commits["head"]["sha"]]):
            commits = copy.deepcopy(self.commits)
            commits["merge"]["parents"] = parents
            with self.subTest(parents=parents), self.assertRaises(ci.Refused):
                self.choose(commits=commits)
        with self.assertRaises(ci.Refused):
            self.choose(context=dict(self.context, checkout_sha="e" * 40))

    def test_proven_advanced_base_or_extra_merge_composition_keeps_full(self):
        commits = copy.deepcopy(self.commits)
        commits["merge"]["parents"][0] = "f" * 40
        self.full(self.choose(commits=commits, advanced=True))
        commits = copy.deepcopy(self.commits)
        commits["merge"]["tree"] = "d" * 40
        changed = copy.deepcopy(self.inventories)
        changed["merge"]["inputs/runtime.lock"] = row(b"composed different bytes")
        self.full(self.choose(commits=commits, inventories=changed))

    def test_missing_or_changed_workflow_payload_cannot_select_skip(self):
        changed = copy.deepcopy(self.review)
        changed.pop(ci.CI_PATH)
        self.full(self.choose(review=changed))
        for path in self.review:
            changed = copy.deepcopy(self.review)
            changed[path]["reviewed_payload_matches"] = False
            with self.subTest(path=path):
                self.full(self.choose(review=changed))
        self.assertNotIn(".github/workflows/native-av-master-e2e.yml", ci.WORKFLOWS)
        self.assertEqual(len(ci.WORKFLOWS), 5)
        self.assertEqual(len(ci.ENGINE_PATHS), 8)

    def test_real_git_source_snapshot_rejects_dirty_and_ignored_inputs(self):
        commands = types.SimpleNamespace(root=self.root, git=self.git)
        original = ci.snapshot_source(commands, self.commits["merge"], self.inventories["merge"])
        self.assertEqual(original["commit"]["sha"], self.commits["merge"]["sha"])
        stray = self.root / "untracked.txt"
        try:
            stray.write_bytes(b"unbound")
            with self.assertRaises(ci.Refused):
                ci.snapshot_source(commands, self.commits["merge"], self.inventories["merge"])
        finally:
            stray.unlink(missing_ok=True)
        exclude = self.root / ".git/info/exclude"
        exclude.parent.mkdir(exist_ok=True)
        prior = exclude.read_bytes() if exclude.exists() else b""
        try:
            with exclude.open("ab") as stream:
                stream.write(b"\nuntracked.txt\n")
            stray.write_bytes(b"ignored but still unbound")
            with self.assertRaises(ci.Refused):
                ci.snapshot_source(commands, self.commits["merge"], self.inventories["merge"])
        finally:
            stray.unlink(missing_ok=True)
            exclude.write_bytes(prior)

    def test_git_replace_cannot_relabel_the_original_merge(self):
        source = self.commits["merge"]["sha"]
        try:
            self.git("replace", source, self.commits["head"]["sha"])
            original = ci.parse_commit(self.git("cat-file", "commit", source), source)
            self.assertEqual(original, self.commits["merge"])
        finally:
            self.git("replace", "--delete", source)


class ReceiptAndCaptureContracts(unittest.TestCase):
    def make_receipt(self, root):
        source = {"sha": "1" * 40, "tree": "2" * 40}
        context = {"checkout_sha": source["sha"], "workflow_ref": "source/workflow@refs/pull/1/merge",
                   "run_id": 42, "run_attempt": 2, "job_key": "native-impact"}
        tests = root / "source/tooling/tests"
        tests.mkdir(parents=True)
        definition = tests / "test_original.py"
        definition.write_text("class Original:\n    def test_one(self):\n        pass\n", encoding="utf-8")
        logs = root / "logs"
        logs.mkdir()
        stderr = b"test_one (test_original.Original.test_one) ... ok\n\nRan 1 test in 0.001s\n\nOK\n"
        steps = []
        for index, (label, command) in enumerate((
            ("discovery:test_original.py", [sys.executable, "-B", "-E", "-s", "-m",
               "unittest", "discover", "-s", "tooling/tests", "-p", "test_original.py", "-v"]),
            ("dotted:tooling.tests.test_original", [sys.executable, "-B", "-E", "-s", "-m",
               "unittest", "tooling.tests.test_original", "-v"]))):
            streams = {}
            for name, raw in (("stdout", b""), ("stderr", stderr)):
                path = logs / ("%02d-%s.log" % (index, name))
                path.write_bytes(raw)
                streams[name] = {"file": path.name, "sha256": ci.digest(raw)}
            steps.append({"entry_point": label, "command": command, "status": "PASS",
                          "exit_code": 0, **streams,
                          "counts": {"terminal_status": "OK", "tests_run": 1, "passed": 1,
                           "failures": 0, "errors": 0, "skipped": 0, "expected_failures": 0,
                           "unexpected_successes": 0}})
        value = {"schema": "motionwright.unittest-execution/1", "status": "PASS",
                 "exit_code": 0, "require_complete": True, "executed_source_sha": source["sha"],
                 "tree_sha": source["tree"], "runner_sha256": "3" * 64,
                 "test_definition_sha256": {"tooling/tests/test_original.py": ci.digest(definition.read_bytes())},
                 "github": {"GITHUB_SHA": source["sha"], "GITHUB_WORKFLOW_REF": context["workflow_ref"],
                            "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2",
                            "GITHUB_JOB": "native-impact"}, "steps": steps}
        return logs / "receipt.json", value, source, context

    def validate(self, root, change=None):
        report, value, source, context = self.make_receipt(root)
        if change is not None:
            change(value, report)
        report.write_text(json.dumps(value), encoding="utf-8")
        return ci.validate_unittest_receipt(report, root / "source", ["test_original.py"],
                                             source, context, "3" * 64, Path(sys.executable))

    def test_complete_two_entrypoints_retain_original_ids_and_counts(self):
        with temporary() as tmp:
            got = self.validate(Path(tmp))
            self.assertEqual(len(got["entrypoints"]), 2)
            self.assertTrue(all(x["ids"] == ["Original.test_one"] for x in got["entrypoints"]))

    def test_counts_skips_boolean_exit_and_missing_mode_are_rejected(self):
        variants = [lambda v, _p: v.update(exit_code=False),
                    lambda v, _p: v["steps"][0].update(exit_code=False),
                    lambda v, _p: v["steps"][0]["counts"].update(tests_run=False),
                    lambda v, _p: v["steps"][0]["counts"].update(skipped=1),
                    lambda v, _p: v["steps"].pop(),
                    lambda v, _p: v["steps"][0]["counts"].update(expected_failures=1)]
        for index, change in enumerate(variants):
            with self.subTest(index=index), temporary() as tmp, self.assertRaises(ci.Refused):
                self.validate(Path(tmp), change)

    def test_source_attempt_and_log_identity_mismatch_are_rejected(self):
        variants = [lambda v, _p: v.update(executed_source_sha="e" * 40),
                    lambda v, _p: v["github"].update(GITHUB_RUN_ATTEMPT="1"),
                    lambda v, _p: v["steps"][0]["stderr"].update(sha256="f" * 64)]
        for index, change in enumerate(variants):
            with self.subTest(index=index), temporary() as tmp, self.assertRaises(ci.Refused):
                self.validate(Path(tmp), change)

    def test_forged_verbose_method_does_not_pass_a_matching_footer(self):
        def forge(value, report):
            item = value["steps"][0]["stderr"]
            path = report.parent / item["file"]
            raw = path.read_bytes().replace(b"test_one", b"test_other")
            path.write_bytes(raw)
            item["sha256"] = ci.digest(raw)
        with temporary() as tmp, self.assertRaises(ci.Refused):
            self.validate(Path(tmp), forge)

    def test_outputs_do_not_publish_an_omission_as_native_pass(self):
        with temporary() as tmp:
            output = Path(tmp) / "github-output"
            output.write_bytes(b"")
            with self.assertRaises(ci.Refused):
                ci.publish_outputs(output, {"decision": "PASS", "native_required": False}, "1" * 64)
            self.assertEqual(output.read_bytes(), b"")
            ci.publish_outputs(output, {"decision": "NOT_SELECTED_REVIEWED_CAS_PROFILE",
                                         "native_required": False}, "1" * 64)
            text = output.read_text()
            self.assertIn("native_required=false\n", text)
            self.assertNotIn("PASS", text)

    def test_capture_positive_preserves_intent_original_stream_and_exit(self):
        with temporary() as tmp:
            folder = Path(tmp) / "child"
            outcome = ci.capture_process([sys.executable, "-B", "-I", "-S", "-c",
                                           "print('observed')"], Path(tmp), ci.hardened_env(),
                                          folder, timeout=10, limit=128)
            self.assertEqual(outcome["exit_code"], 0)
            self.assertEqual((folder / "stdout.bin").read_bytes().strip(), b"observed")
            intent = json.loads((folder / "intent.json").read_text())
            self.assertEqual(intent["state"], "UNKNOWN_UNTIL_OUTCOME")
            self.assertLessEqual(intent["at_utc"], json.loads((folder / "outcome.json").read_text())["at_utc"])

    def capture_rejection(self, code, timeout, limit, error):
        with temporary() as tmp:
            folder = Path(tmp) / "child"
            with self.assertRaises(error):
                ci.capture_process([sys.executable, "-B", "-I", "-S", "-c", code],
                                   Path(tmp), ci.hardened_env(), folder, timeout, limit)
            outcome = json.loads((folder / "outcome.json").read_text())
            self.assertTrue(outcome["timed_out"] or outcome["capture_errors"])
            self.assertLessEqual((folder / "stdout.bin").stat().st_size, limit)
            self.assertFalse((folder / "receipt.json").exists())
            return outcome

    def test_capture_overflow_remains_failure_without_replay(self):
        outcome = self.capture_rejection("print('x'*4096)", 10, 8, ci.Refused)
        self.assertTrue(outcome["capture_errors"])

    def test_capture_timeout_remains_unknown_without_replay(self):
        outcome = self.capture_rejection("import time;time.sleep(10)", 0.03, 128, ci.Uncertain)
        self.assertTrue(outcome["timed_out"])


if __name__ == "__main__":
    unittest.main()
