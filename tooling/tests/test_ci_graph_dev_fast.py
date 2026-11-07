"""Inert regression tests for Graph's fast development source gate.

Neither this module nor the gate executes Graph's owner, R74 reviewer or
any actual Native/Store effect. Test roots are isolated scratch directories.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tooling import ci_graph_dev_fast as gate


PRODUCER = b"""def graph_capture_cargo_executable(source, target_root, role):
    directory = target_root / "mw-r74-captured"
    captured = directory / ("semwright-sandbox" if role == "semwright_sandbox" else role)
    return captured
"""

OWNER = ('''def verify_graph_sdk_capture_roster(spec, producer_index):
    """Inert equality of the exact R74 source-captured SDK executable roster.

    The producer already authenticated Cargo paths, retained bytes and roles.
    Never substitute mutable target/debug aliases for its exclusive snapshots.
    """
    require(type(spec) is dict and type(producer_index) is dict
            and spec.get("source") == producer_index.get("source"),
            "graph-sdk-capture-source-mismatch")
    source, declared, original = spec.get("source"), spec.get("binaries"), producer_index.get("binaries")
    require(type(source) is dict and type(declared) is dict and type(original) is dict
            and type(source.get("sdk_root")) is str,
            "graph-sdk-capture-roster-shape")
    capture = Path(source["sdk_root"]) / "target" / "mw-r74-captured"
    for role in ("semwright", "semwrightd", "semwright_sandbox"):
        expected, retained = declared.get(role), original.get(role)
        require(type(expected) is dict and type(retained) is dict
                and set(expected) == {"path", "sha256", "size_bytes"}
                and expected == retained and type(expected["path"]) is str
                and DIGEST.fullmatch(expected["sha256"]) is not None
                and type(expected["size_bytes"]) is int and expected["size_bytes"] > 0
                and Path(expected["path"]) == capture / (
                    "semwright-sandbox" if role == "semwright_sandbox" else role),
                "graph-sdk-capture-role-mismatch:" + role)
    return True

def load_shared():
    verify_graph_sdk_capture_roster(_OWNER_SPEC, _GRAPH_J1["index"])
''').encode("utf-8")


def create_git_fixture(root: Path):
    def git(*args):
        p = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, check=True)
        return p.stdout.strip().decode("utf-8")
    git("init", "-q")
    git("config", "user.name", "Source Fixture")
    git("config", "user.email", "source-fixture@example.invalid")
    (root / "SOURCE_LOCK.json").write_text("{}\n", encoding="utf-8")
    git("add", "SOURCE_LOCK.json")
    git("commit", "-qm", "fixture")
    return git


class CaptureRosterContractTests(unittest.TestCase):
    def test_real_inert_contract_and_all_nine_negative_controls(self):
        result = gate.probe_capture_roster_source(OWNER, PRODUCER)
        self.assertEqual((result["status"], result["positive"], result["negative"]),
                         ("PASS_INERT_ONLY", 1, 9))
        self.assertIn("mutable_debug_alias", result["negative_names"])
        self.assertIn("obsolete_underscore_capture", result["negative_names"])
        self.assertIn("wrong_captured_root", result["negative_names"])
        self.assertEqual(result["R74_technical_admissions"], 0)
        self.assertEqual(result["actual_native_processes"], 0)

    def test_unreviewed_predicate_rejected_before_any_execution(self):
        # A changed AST can contain unbounded effects; never execute it even
        # if its calls look like an allowed subset of the original predicate.
        malicious = OWNER.replace(
            b"    return True\n\ndef load_shared():",
            b"    while True: pass\n    return True\n\ndef load_shared():")
        self.assertNotEqual(malicious, OWNER)
        with self.assertRaisesRegex(
                gate.GraphFastError, "GRAPH_DEV_UNREVIEWED_CAPTURE_PREDICATE"):
            gate.probe_capture_roster_source(malicious, PRODUCER)
        other = OWNER.replace(b"    return True", b"    return True # harmless change")
        # Comment-only changes do not alter semantics/AST and remain allowed.
        self.assertEqual(
            gate.probe_capture_roster_source(other, PRODUCER)["status"],
            "PASS_INERT_ONLY")

    def test_fail_closed_if_owner_join_or_capture_root_is_missing(self):
        variants = (
            (OWNER.replace(b"    verify_graph_sdk_capture_roster(_OWNER_SPEC, _GRAPH_J1[\"index\"])",
                           b"    pass"), PRODUCER),
            (OWNER.replace(b"def verify_graph_sdk_capture_roster", b"def old_mutable_alias"),
             PRODUCER),
            (OWNER, PRODUCER.replace(b"mw-r74-captured", b"debug")),
            (OWNER.replace(b"def load_shared():", b"def different_entrypoint():"),
             PRODUCER),
        )
        for owner, producer in variants:
            with self.subTest(owner_len=len(owner), producer_len=len(producer)):
                with self.assertRaises(gate.GraphFastError):
                    gate.probe_capture_roster_source(owner, producer)


class ImpactRoutingTests(unittest.TestCase):
    def test_workspace_consumer_closure_does_not_compile_unrelated_packages(self):
        self.assertEqual(gate.impacted_packages(["tooling/native_graph_browser_owner.py"]), [])
        self.assertEqual(gate.impacted_packages(["crates/native/src/lib.rs"]),
                         ["motionwright-native"])
        self.assertEqual(gate.impacted_packages(["crates/service/src/lib.rs"]),
                         ["motionwright-service", "motionwright-native"])
        self.assertEqual(gate.impacted_packages(["crates/storage/src/lib.rs"]),
                         ["motionwright-storage", "motionwright-service", "motionwright-native"])
        self.assertEqual(gate.impacted_packages(["crates/domain/src/lib.rs"]),
                         ["motionwright-domain", "motionwright-service", "motionwright-native"])
        self.assertEqual(gate.impacted_packages(["Cargo.lock"]),
                         ["motionwright-domain", "motionwright-storage",
                          "motionwright-service", "motionwright-native"])
        self.assertTrue(gate.impacted_ui(["apps/studio/src/semanticGraph.ts"]))
        self.assertFalse(gate.impacted_ui(["tooling/ci_graph_dev_fast.py"]))
        self.assertEqual(gate.impacted_packages(["Cargo.toml-backup"]), [])
        self.assertEqual(gate.impacted_packages(["SOURCE_LOCK.json~"]), [])

    def test_base_and_dirty_worktree_are_reported_without_retagging_head(self):
        with tempfile.TemporaryDirectory(prefix="mw-fast-graph-") as tmp:
            root = Path(tmp)
            git = create_git_fixture(root)
            first = git("rev-parse", "HEAD")
            (root / "Cargo.lock").write_text("synthetic\n", encoding="utf-8")
            git("add", "Cargo.lock")
            git("commit", "-qm", "synthetic Rust lock")
            head = git("rev-parse", "HEAD")
            snap = gate.current_source(root, first, expected=head)
            self.assertEqual(snap["source_kind"], "EXACT_HEAD")
            self.assertIn("Cargo.lock", gate.changed_files(root, first))
            (root / "tooling").mkdir()
            (root / "tooling/untracked_shadow.py").write_text("def shadow(): pass\n", encoding="utf-8")
            wip = gate.current_source(root, first)
            self.assertTrue(wip["tracked_clean"])
            self.assertFalse(wip["worktree_clean"])
            self.assertIn("tooling/untracked_shadow.py", wip["untracked_source_paths"])
            self.assertEqual(wip["source_kind"], "WORKTREE_WIP_NOT_HEAD_ATTESTATION")
            (root / "tooling/untracked_shadow.py").unlink()
            (root / "tooling").rmdir()
            self.assertTrue(gate.current_source(root, first)["worktree_clean"])
            (root / "SOURCE_LOCK.json").write_text("changed locally\n", encoding="utf-8")
            self.assertEqual(gate.current_source(root, first)["source_kind"],
                             "WORKTREE_WIP_NOT_HEAD_ATTESTATION")
            self.assertIn("SOURCE_LOCK.json", gate.changed_files(root, first))
            with self.assertRaises(gate.GraphFastError):
                gate.current_source(root, first, expected="f" * 40)

    def test_exact_binary_git_paths_and_deleted_source_are_not_erased(self):
        simulated = mock.Mock(returncode=0, stdout=b"tooling/trailing-space.py  \0")
        with mock.patch.object(gate.subprocess, "run", return_value=simulated):
            raw = gate.git(Path("."), "ls-files", "--others", "-z")
        self.assertEqual(raw, b"tooling/trailing-space.py  \0")
        self.assertEqual(gate._paths(raw), {"tooling/trailing-space.py  "})
        with tempfile.TemporaryDirectory(prefix="mw-fast-delete-") as tmp:
            root = Path(tmp)
            git = create_git_fixture(root)
            (root / "crates/native/src").mkdir(parents=True)
            (root / "crates/native/src/retired.rs").write_text("pub fn old() {}\n", encoding="utf-8")
            git("add", "crates/native/src/retired.rs")
            git("commit", "-qm", "fixture native source")
            before = git("rev-parse", "HEAD")
            git("rm", "crates/native/src/retired.rs")
            git("commit", "-qm", "fixture deletion")
            changed = gate.changed_files(root, before)
            self.assertIn("crates/native/src/retired.rs", changed)
            self.assertEqual(gate.impacted_packages(changed), ["motionwright-native"])

    def test_unsafe_path_and_unreachable_comparison_do_not_succeed(self):
        with self.assertRaises(gate.GraphFastError):
            gate._paths(b"../foreign\0")
        with self.assertRaises(gate.GraphFastError):
            gate._paths(b"/root/absolute\0")
        with self.assertRaises(gate.GraphFastError):
            gate._paths(b"tooling/./fake.py\0")
        with tempfile.TemporaryDirectory(prefix="mw-fast-source-") as tmp:
            root = Path(tmp)
            create_git_fixture(root)
            with self.assertRaises(gate.GraphFastError):
                gate.current_source(root, "1234567890" * 4)

    def test_impacted_rust_requires_existing_hot_target_without_creating_one(self):
        with tempfile.TemporaryDirectory(prefix="mw-fast-target-") as tmp:
            root = Path(tmp)
            target = root / "unallocated-cargo-target"
            missing = gate._check_cargo(root, ["motionwright-native"], None, 10)
            invalid = gate._check_cargo(root, ["motionwright-native"], target, 10)
            self.assertEqual(missing["status"], "HOLD_REQUIRE_EXISTING_CARGO_TARGET")
            self.assertEqual(invalid["status"], "HOLD_UNSAFE_OR_MISSING_CARGO_TARGET")
            self.assertFalse(target.exists())

    def test_private_process_output_is_hashed_not_copied_into_source_receipt(self):
        with tempfile.TemporaryDirectory(prefix="mw-fast-sanitize-") as tmp:
            root = Path(tmp)
            p = gate._run(root, "synthetic_failure", [sys.executable, "-B", "-c",
                "import sys; print('PRIVATE_CANARY_TOKEN'); sys.exit(7)"], 10)
            self.assertEqual(p["status"], "FAIL")
            self.assertEqual(p["exit_code"], 7)
            self.assertTrue(p["stdout_sha256"])
            self.assertNotIn("PRIVATE_CANARY", json.dumps(p))

    def test_fast_mode_never_promotes_native_even_on_success(self):
        with tempfile.TemporaryDirectory(prefix="mw-fast-report-") as tmp:
            root = Path(tmp)
            (root / "tooling").mkdir()
            for name in ("native_graph_browser_owner.py", "ci_canvas_service.py"):
                (root / "tooling" / name).write_bytes(b"fixture")
            src = {"git_head": "a" * 40, "git_tree": "b" * 40,
                   "base_commit": "c" * 40, "tracked_clean": True,
                   "source_kind": "EXACT_HEAD"}
            passing = {"check": "selected", "status": "PASS", "exit_code": 0}
            with mock.patch.object(gate, "current_source", return_value=src), \
                 mock.patch.object(gate, "changed_files",
                                   return_value=["tooling/native_graph_browser_owner.py"]), \
                 mock.patch.object(gate, "probe_capture_roster_source",
                                   return_value={"status": "PASS_INERT_ONLY"}), \
                 mock.patch.object(gate, "_check_node", return_value=[passing]), \
                 mock.patch.object(gate, "selected_python_cases", return_value=(["synthetic"], [])), \
                 mock.patch.object(gate, "_run", return_value=passing), \
                 mock.patch.object(gate, "_check_cargo", side_effect=AssertionError("Rust rerun")):
                report = gate.execute(root, "HEAD^", "fast")
            self.assertEqual(report["status"], "PASS_SOURCE_ONLY")
            self.assertEqual(report["Graph6_cases_accepted"], 0)
            self.assertEqual(report["R74_review"], "NOT_EXECUTED")
            self.assertEqual(report["Native_Host_Store_G2"], "NOT_EXECUTED")
            self.assertFalse(report["formal_acceptance"])
            self.assertEqual(report["G3"], "STOP")

    def test_first_case_red_stops_before_all_other_checks(self):
        with tempfile.TemporaryDirectory(prefix="mw-fast-red-") as tmp:
            root = Path(tmp)
            (root / "tooling").mkdir()
            for name in ("native_graph_browser_owner.py", "ci_canvas_service.py"):
                (root / "tooling" / name).write_bytes(b"fixture")
            src = {"git_head": "a" * 40, "git_tree": "b" * 40,
                   "base_commit": "c" * 40, "tracked_clean": True,
                   "source_kind": "EXACT_HEAD"}
            with mock.patch.object(gate, "current_source", return_value=src), \
                 mock.patch.object(gate, "changed_files", return_value=[]), \
                 mock.patch.object(gate, "probe_capture_roster_source",
                                   side_effect=gate.GraphFastError("GRAPH_DEV_CAPTURE_MISMATCH")), \
                 mock.patch.object(gate, "_check_node", side_effect=AssertionError("not reached")):
                report = gate.execute(root, "HEAD^", "fast")
            self.assertEqual(report["status"], "FAIL_FIRST_CASE_SOURCE_INTAKE")
            self.assertEqual(report["first_failure"]["code"], "GRAPH_DEV_CAPTURE_MISMATCH")
            self.assertEqual(report["checks"], [])
            self.assertEqual(report["Graph6_cases_accepted"], 0)


class ManualWorkflowBoundaryTests(unittest.TestCase):
    def test_graph_original_store_and_r74_archive_critical_paths_trigger_source_g0(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/graph-development-fast.yml").read_text(encoding="utf-8")
        trigger = workflow.split("    paths:", 1)[1].split("  workflow_dispatch:", 1)[0]
        required = (
            "tooling/tests/test_ci_graph_r74_singlecase.py",
            "tooling/tests/test_ci_graph_readonly_archive.py",
            "tooling/native_graph_build_reuse.py",
            "tooling/native_graph_build_transfer.py",
            "tooling/tests/test_graph_build_reuse.py",
            "tooling/tests/test_graph_build_transfer.py",
            "tooling/tests/test_graph_continuation.py",
            ".github/workflows/studio-service-verification.yml",
            "crates/storage/src/read_only.rs",
            "crates/native/src/production/graph_authoring.rs",
            "crates/native/examples/native-graph-review-session.rs",
            "crates/service/examples/studio_review_session.rs",
            "apps/studio/tests/support/nativeGraphCase.ts",
            "apps/studio/tests/semantic-graph-service.spec.ts",
        )
        for item in required:
            with self.subTest(path=item):
                self.assertIn("      - '" + item + "'", trigger)
        gn1 = workflow.split("      - name: Verify GN1 startup", 1)[1].split(
            "      - name: Verify original J1", 1)[0]
        self.assertIn("tooling.tests.test_ci_graph_readonly_archive -v", gn1)
        self.assertIn("tee knowledge/runtime/graph-fast-gn1-unittest.original.log", gn1)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("contents: read", workflow)
        for blocked in ("graph produce", "graph wait", "graph run"):
            self.assertNotIn(blocked, workflow)

    def test_graph_control_replay_originals_are_selected_in_source_fast_lane(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/graph-development-fast.yml").read_text(
            encoding="utf-8")
        trigger = workflow.split("    paths:", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertIn("      - 'tooling/tests/test_graph_owner_control_replay.py'", trigger)
        gn1 = workflow.split("      - name: Verify GN1 startup", 1)[1].split(
            "      - name: Verify original J1", 1)[0]
        self.assertIn("if [ -f tooling/tests/test_graph_owner_control_replay.py ]; then", gn1)
        self.assertIn("tooling.tests.test_graph_owner_control_replay -v", gn1)
        self.assertIn("tooling.tests.test_ci_graph_dev_fast", gn1)
        self.assertIn("tee -a knowledge/runtime/graph-fast-gn1-unittest.original.log", gn1)
        self.assertIn("set -euo pipefail", gn1)

    def test_source_g0_exercises_graph_owner_quiescence_and_r74_selection(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/graph-development-fast.yml").read_text(encoding="utf-8")
        trigger = workflow.split("    paths:", 1)[1].split("  workflow_dispatch:", 1)[0]
        self.assertIn(
            "      - 'tooling/tests/test_graph_owner_native_quiescence.py'", trigger
        )
        gn1 = workflow.split("      - name: Verify GN1 startup", 1)[1].split(
            "      - name: Verify original J1", 1
        )[0]
        # These are source-only negative oracles, not a physical Graph6 trial.
        # Both source modules can change while the Native G2 gate remains blocked.
        for module in (
            "tooling.tests.test_ci_graph_r74_singlecase",
            "tooling.tests.test_graph_owner_native_quiescence",
        ):
            with self.subTest(module=module):
                self.assertIn(module, gn1)
        self.assertIn("2>&1 | tee knowledge/runtime/graph-fast-gn1-unittest.original.log", gn1)

    def test_source_only_push_and_manual_job_cannot_be_confused_with_r74_acceptance(self):
        root = Path(__file__).resolve().parents[2]
        p = root / ".github/workflows/graph-development-fast.yml"
        text = p.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("push:", text)
        self.assertIn("branches: ['integrate/phase1-final-composition-20261027']", text)
        self.assertIn("github.event.before || inputs.base_sha", text)
        self.assertIn("github.event_name == 'push' && github.sha", text)
        self.assertIn("test \"$GITHUB_REF\" = refs/heads/integrate/phase1-final-composition-20261027", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("--expected-source \"$GITHUB_SHA\"", text)
        self.assertIn("--mode fast", text)
        self.assertNotIn("graph produce", text)
        self.assertNotIn("graph wait", text)
        self.assertNotIn("graph run", text)
        self.assertNotIn("R74_technical_admissions: true", text)


if __name__ == "__main__":
    unittest.main()
