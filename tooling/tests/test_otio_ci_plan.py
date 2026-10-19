"""Synthetic selection/identity fixtures; no Cargo, media or product test is run."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "otio_ci_plan.py"
spec = importlib.util.spec_from_file_location("otio_ci_plan_under_test", SOURCE)
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


def inventory(paths=(), state="EXACT_CHANGED_PATHS"):
    return {"state": state, "paths": list(paths), "reason": "original-fixture-observation"}


class SelectionTests(unittest.TestCase):
    def selection(self, paths, expected):
        got = plan.select_jobs("pull_request", "all", inventory(paths))
        self.assertEqual((got["rust_media"], got["windows_paths"]), expected)
        return got

    def test_manual_scopes_are_explicit(self):
        for scope, expected in (("all", (True, True)), ("rust_media", (True, False)),
                                ("windows_paths", (False, True))):
            with self.subTest(scope=scope):
                got = plan.select_jobs("workflow_dispatch", scope, inventory())
                self.assertEqual((got["rust_media"], got["windows_paths"]), expected)

    def test_automatic_event_cannot_use_a_partial_manual_scope(self):
        for scope in ("rust_media", "windows_paths", "invalid"):
            with self.subTest(scope=scope), self.assertRaises(plan.PlanError):
                plan.select_jobs("push", scope, inventory())

    def test_rust_exporter_inputs_do_not_request_windows(self):
        for path in ("crates/domain/src/delivery.rs", "crates/domain/Cargo.toml",
                     "crates/domain/examples/otio_real_media_fixture.rs", "Cargo.toml",
                     "Cargo.lock", "SOURCE_LOCK.json", ".cargo/config.toml", "rust-toolchain.toml"):
            with self.subTest(path=path):
                self.selection([path], (True, False))

    def test_shared_bridge_and_execution_controls_request_both(self):
        for path in sorted(plan.BOTH):
            with self.subTest(path=path):
                self.selection([path], (True, True))

    def test_linux_media_and_publication_inputs_request_linux(self):
        for path in sorted(plan.LINUX):
            with self.subTest(path=path):
                self.selection([path], (True, False))

    def test_only_reviewed_test_and_markdown_changes_need_g0(self):
        for path in (plan.TESTS, plan.DOCUMENT, "docs/verification/notes.md"):
            with self.subTest(path=path):
                self.selection([path], (False, False))
        self.selection(["docs/fixture.bin"], (True, True))

    def test_new_otio_file_and_unreviewed_dependency_broaden(self):
        for path in ("tooling/otio-media/new_helper.py", "apps/studio/src/new.ts",
                     "vendor/new-native-input.dat"):
            with self.subTest(path=path):
                got = self.selection([path], (True, True))
                self.assertEqual(got["reasons"][0]["reason"], "unreviewed-dependency-conservative-full")

    def test_empty_exact_diff_and_unknown_inventory_are_different(self):
        self.selection([], (False, False))
        for state in ("CONSERVATIVE_FULL", "UNKNOWN", "TRUNCATED"):
            with self.subTest(state=state):
                got = plan.select_jobs("push", "all", inventory(state=state))
                self.assertTrue(got["rust_media"] and got["windows_paths"])

    def test_rename_copy_and_delete_include_original_paths(self):
        paths, changes = plan.parse_diff(
            b"R050\0tooling/otio-media/bridge.py\0docs/moved.md\0"
            b"C100\0crates/domain/src/a.rs\0docs/copied.md\0D\0Cargo.lock\0")
        self.assertEqual(len(changes), 3)
        self.assertIn("tooling/otio-media/bridge.py", paths)
        self.assertIn("docs/moved.md", paths)
        self.assertIn("Cargo.lock", paths)
        self.selection(paths, (True, True))

    def test_unsafe_path_never_becomes_an_omission(self):
        for path in ("/tmp/a", "../a", "a/../b", "a//b", "a/./b",
                     "C:/source", "a\\b", "a\nb", ".git/config"):
            with self.subTest(path=path), self.assertRaises(plan.PlanError):
                self.selection([path], (False, False))

    def test_truncated_or_malformed_diff_is_rejected(self):
        for raw in (b"M\0a", b"R100\0a\0", b"R101\0a\0b\0", b"Q\0a\0"):
            with self.subTest(raw=raw), self.assertRaises(plan.PlanError):
                plan.parse_diff(raw)

    def test_duplicate_event_fields_and_nonfinite_json_are_rejected(self):
        for raw in (b'{"before":1,"before":2}', b'{"n":NaN}', b'[]'):
            with self.subTest(raw=raw), self.assertRaises(plan.PlanError):
                plan.strict_json(raw)


class GitCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="otio-plan-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.invocations = 0
        fixtures = {
            plan.PLANNER: SOURCE.read_bytes(),
            plan.WORKFLOW: b"name: synthetic selector fixture\non: workflow_dispatch\njobs: {}\n",
            ".gitignore": b"verification/\n",
            "crates/domain/src/lib.rs": b"pub fn fixture() {}\n",
            "tooling/otio-media/bridge.py": b"# Synthetic input, never imported.\n",
            plan.DOCUMENT: b"# Synthetic documentation\n",
        }
        for rel, data in fixtures.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git("init", "-q", "--initial-branch=main")
        self.git("add", "--all")
        self.git("commit", "-q", "-m", "synthetic base")
        self.base = self.git("rev-parse", "HEAD").strip().decode()

    def git(self, *args, data=None):
        cmd = ["git", "--no-replace-objects", "-c", "core.autocrlf=false",
               "-c", "core.fsmonitor=false", "-c", "user.name=OTIO selection fixture",
               "-c", "user.email=fixture@invalid.example", "-C", str(self.repo), *args]
        proc = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=plan.git_env(), timeout=20, check=False)
        if proc.returncode:
            raise AssertionError(proc.stderr.decode("utf-8", "replace"))
        return proc.stdout

    def change(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        self.git("add", "--all")
        self.git("commit", "-q", "-m", "synthetic input change")
        return self.git("rev-parse", "HEAD").strip().decode()

    def run_cli(self, event_name="push", event=None, scope="all", env_updates=None,
                source_arg=None, repeat_directory=None):
        self.invocations += 1
        head = self.git("rev-parse", "HEAD").strip().decode()
        ref = "refs/pull/7/merge" if event_name == "pull_request" else "refs/heads/main"
        body = {"repository": {"full_name": plan.REPOSITORY}}
        if event_name == "push":
            body.update(before=self.base, after=head, ref=ref)
        elif event_name == "workflow_dispatch":
            body["inputs"] = {"scope": scope}
        else:
            body["pull_request"] = {"base": {"sha": self.base}, "head": {"sha": head}}
        if event:
            body.update(event)
        directory = repeat_directory or self.root / ("call-" + str(self.invocations))
        directory.mkdir(exist_ok=True)
        event_file, outputs, result = directory/"event.json", directory/"github-output", directory/"plan.json"
        event_file.write_text(json.dumps(body), encoding="utf-8")
        if not outputs.exists():
            outputs.write_bytes(b"")
        env = plan.git_env()
        env.update(GITHUB_REPOSITORY=plan.REPOSITORY, GITHUB_EVENT_NAME=event_name,
                   GITHUB_SHA=head, GITHUB_REF=ref, GITHUB_RUN_ID="1001",
                   GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="otio-impact",
                   GITHUB_WORKFLOW_SHA=head,
                   GITHUB_WORKFLOW_REF=plan.REPOSITORY+"/"+plan.WORKFLOW+"@"+ref,
                   GITHUB_EVENT_PATH=str(event_file), GITHUB_OUTPUT=str(outputs))
        env.update(env_updates or {})
        argv = [sys.executable, "-I", "-B", "-S", str(self.repo/plan.PLANNER),
                "--repo", str(self.repo), "--event-file", str(event_file),
                "--event-name", event_name, "--source-sha", source_arg or head,
                "--scope", scope, "--output", str(result), "--github-output", str(outputs)]
        proc = subprocess.run(argv, env=env, cwd=self.root, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30, check=False)
        doc = json.loads(result.read_bytes()) if result.exists() else None
        return proc, doc, directory

    def admitted(self, returned, rust, windows):
        proc, result, _ = returned
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(result["status"], "JOB_REQUESTS_PLANNED")
        self.assertEqual(result["jobs"]["rust_media"]["selected"], rust)
        self.assertEqual(result["jobs"]["windows_paths"]["selected"], windows)
        self.assertTrue(result["g0_required"])
        self.assertFalse(result["test_results_admitted"])
        self.assertFalse(result["release_candidate_verified"])
        self.assertEqual({j["result"] for j in result["jobs"].values()}, {"NOT_EXECUTED_BY_PLANNER"})
        expected = {k: v for k, v in result.items() if k != "plan_id"}
        self.assertEqual(result["plan_id"], plan.sha256(plan.canonical(expected)))
        return result

    def rejected(self, returned):
        proc, result, _ = returned
        self.assertEqual(proc.returncode, 2, proc.stderr.decode("utf-8", "replace"))
        self.assertIsNone(result)
        self.assertIn(b"NOT_ADMITTED", proc.stderr)

    def test_actual_push_domain_diff_is_rust_only(self):
        head = self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        result = self.admitted(self.run_cli(), True, False)
        self.assertEqual(result["source"]["sha"], head)
        self.assertEqual(result["change_inventory"]["paths"], ["crates/domain/src/lib.rs"])

    def test_actual_renamed_bridge_still_requests_windows(self):
        self.git("mv", "tooling/otio-media/bridge.py", "tooling/otio-media/moved.py")
        self.git("commit", "-q", "-m", "synthetic rename")
        result = self.admitted(self.run_cli(), True, True)
        self.assertIn("tooling/otio-media/bridge.py", result["change_inventory"]["paths"])
        self.assertIn("tooling/otio-media/moved.py", result["change_inventory"]["paths"])

    def test_manual_scope_and_attempt_do_not_transfer_any_result(self):
        one = self.admitted(self.run_cli("workflow_dispatch", scope="windows_paths"), False, True)
        two = self.admitted(self.run_cli("workflow_dispatch", scope="windows_paths",
                                      env_updates={"GITHUB_RUN_ATTEMPT": "2"}), False, True)
        self.assertEqual(one["source"], two["source"])
        self.assertNotEqual(one["plan_id"], two["plan_id"])

    def test_unknown_or_zero_base_selects_both(self):
        for base in (plan.ZERO, "1"*40):
            with self.subTest(base=base):
                result = self.admitted(self.run_cli(event={"before": base}), True, True)
                self.assertEqual(result["change_inventory"]["state"], "CONSERVATIVE_FULL")

    def test_shallow_history_cannot_authorize_rust_only(self):
        self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        (self.repo/".git"/"shallow").write_text(self.base+"\n", encoding="ascii")
        result = self.admitted(self.run_cli(), True, True)
        self.assertEqual(result["change_inventory"]["reason"], "incomplete-git-history")

    def test_source_sha_and_job_attempt_cannot_be_relabelled(self):
        head = self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        self.rejected(self.run_cli(source_arg=self.base, env_updates={"GITHUB_SHA": self.base}))
        for change in ({"GITHUB_RUN_ATTEMPT": "0"}, {"GITHUB_JOB": "other"},
                       {"GITHUB_WORKFLOW_REF": "other/repo/"+plan.WORKFLOW+"@refs/heads/main"}):
            with self.subTest(change=change):
                self.rejected(self.run_cli(env_updates=change))
        self.assertEqual(self.git("rev-parse", "HEAD").strip().decode(), head)

    def test_original_event_repository_and_scope_are_not_overridable(self):
        self.rejected(self.run_cli(event={"repository": {"full_name": "other/repo"}}))
        self.rejected(self.run_cli("workflow_dispatch", scope="rust_media",
                                  event={"inputs": {"scope": "windows_paths"}}))
        self.rejected(self.run_cli(event={"after": self.base, "ref": "refs/heads/other"}))

    def test_modified_executing_planner_cannot_emit_a_plan(self):
        with (self.repo/plan.PLANNER).open("ab") as stream:
            stream.write(b"\n# Changed physical source, deliberately uncommitted.\n")
        self.rejected(self.run_cli())

    def test_original_replacement_ref_cannot_rewrite_source_or_diff(self):
        head = self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        self.git("update-ref", "refs/replace/"+head, self.base)
        result = self.admitted(self.run_cli(), True, False)
        self.assertEqual(result["source"]["tree"], tree)
        self.assertEqual(result["change_inventory"]["paths"], ["crates/domain/src/lib.rs"])

    def test_exact_pr_head_diff_uses_original_merge_base(self):
        head = self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        result = self.admitted(self.run_cli("pull_request"), True, False)
        self.assertEqual(result["change_inventory"]["merge_base_sha"], self.base)
        self.assertEqual(result["change_inventory"]["pr_head_sha"], head)

    def test_advanced_pr_base_change_is_included_in_actual_checkout_impact(self):
        head = self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        self.git("checkout", "-q", "-b", "advanced", self.base)
        advanced = self.change("tooling/otio-media/bridge.py", b"# Changed synthetic bridge input.\n")
        self.git("merge", "-q", "--no-ff", "-m", "synthetic merge", head)
        event = {"pull_request": {"base": {"sha": self.base}, "head": {"sha": head}}}
        result = self.admitted(self.run_cli("pull_request", event=event), True, True)
        self.assertEqual(result["change_inventory"]["event_base_sha"], self.base)
        self.assertEqual(result["change_inventory"]["actual_merge_base_parent"], advanced)
        self.assertEqual(set(result["change_inventory"]["paths"]),
                         {"crates/domain/src/lib.rs", "tooling/otio-media/bridge.py"})

    def test_reversed_stale_octopus_and_unrelated_pr_parents_are_rejected(self):
        head = self.change("crates/domain/src/lib.rs", b"pub fn changed() {}\n")
        tree = self.git("rev-parse", "HEAD^{tree}").strip().decode()
        other = self.git("commit-tree", tree, data=b"synthetic unrelated root\n").strip().decode()
        alternatives = ((head, self.base), (other, self.base),
                        (self.base, other, head), (other, head))
        event = {"pull_request": {"base": {"sha": self.base}, "head": {"sha": head}}}
        for parents in alternatives:
            args = ["commit-tree", tree]
            for parent in parents:
                args += ["-p", parent]
            commit = self.git(*args, data=b"synthetic PR merge shape\n").strip().decode()
            self.git("checkout", "-q", "--detach", commit)
            with self.subTest(parents=parents):
                self.rejected(self.run_cli("pull_request", event=event))

    def test_existing_output_is_retained_and_never_overwritten(self):
        first = self.run_cli("workflow_dispatch")
        self.admitted(first, True, True)
        directory = first[2]
        original_plan = (directory/"plan.json").read_bytes()
        original_outputs = (directory/"github-output").read_bytes()
        repeated, _, _ = self.run_cli("workflow_dispatch", repeat_directory=directory)
        self.assertEqual(repeated.returncode, 2)
        self.assertEqual((directory/"plan.json").read_bytes(), original_plan)
        self.assertEqual((directory/"github-output").read_bytes(), original_outputs)


if __name__ == "__main__":
    unittest.main()
