"""Causal infrastructure controls for pre-native source-original retention.

Only the embedded workflow custody composition is loaded. Product modules,
renderers, Git, network, and real encryption are never invoked by these tests.
The crypto boundary is injected; its independent cryptographic tests remain
owned by ci_private_evidence. These fixtures test which original bytes reach
that boundary and which outcomes may be claimed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import textwrap
from types import SimpleNamespace
import unittest


def workflow_namespace():
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/native-mlt-sequence-e2e.yml"
    lines = workflow.read_text(encoding="utf-8").splitlines()
    fragments, start = [], None
    for index, line in enumerate(lines):
        if line.strip() == "# BEGIN A6_MLT_PREFLIGHT_CUSTODY":
            if start is not None:
                raise AssertionError("nested custody fragment")
            start = index
        elif line.strip() == "# END A6_MLT_PREFLIGHT_CUSTODY":
            if start is None:
                raise AssertionError("unpaired custody fragment")
            fragments.append(textwrap.dedent("\n".join(lines[start:index + 1])) + "\n")
            start = None
    if start is not None or len(fragments) != 2 or fragments[0] != fragments[1]:
        raise AssertionError("initial/final workflow custody compositions differ")
    namespace = {"__name__": "a6_workflow_preflight_contract"}
    exec(compile(fragments[0], str(workflow), "exec"), namespace)
    return namespace


class SyntheticCustody:
    """Records the bytes presented to the existing encryption boundary."""
    MAX_JSON = 512 * 1024

    def __init__(self):
        self.encryption_calls = []
        self.captured = None

    @staticmethod
    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def json_bytes(value):
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()

    @staticmethod
    def strict(raw):
        return json.loads(raw)

    @staticmethod
    def metadata_identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def regular(self, path, limit=None):
        raw = path.read_bytes()
        if limit is not None and len(raw) > limit:
            raise ValueError("fixture input too large")
        return raw, {"sha256": self.sha(raw), "bytes": len(raw)}

    @staticmethod
    def new_bytes(path, raw):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)

    def new_json(self, path, value):
        self.new_bytes(path, self.json_bytes(value))

    @staticmethod
    def public_key(recipient):
        if recipient != {"sha256": "b" * 64, "spki_der_base64": "SYNTHETIC_BOUNDARY_ONLY"}:
            raise ValueError("fixture recipient differs")

    def gather_snapshot(self, roots, preserve_partial=False):
        if preserve_partial is not True:
            raise AssertionError("partial original retention must remain enabled")
        self.captured = {}
        for label, root in sorted(roots.items()):
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    self.captured[label + "/" + path.relative_to(root).as_posix()] = path.read_bytes()
        value = {name: base64.b64encode(raw).decode() for name, raw in self.captured.items()}
        return self.json_bytes(value), True

    def encrypt_snapshot(self, raw, recipient, current):
        self.public_key(recipient)
        self.encryption_calls.append((raw, recipient, current))
        return b"SYNTHETIC_ENCRYPTION_BOUNDARY:" + self.sha(raw).encode()


class PreflightCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = workflow_namespace()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mlt-preflight-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.checkout = self.base / "checkout"
        self.checkout.mkdir(mode=0o700)
        (self.checkout / "source.py").write_bytes(b"source-before\n")
        self.current = {"source_sha": "1" * 40, "semwright_sha": "2" * 40,
                        "run_id": "41", "run_attempt": "1"}
        self.dispatch = {"source_sha": self.current["source_sha"],
                         "recipient": {"sha256": "b" * 64, "spki_der_base64": "SYNTHETIC_BOUNDARY_ONLY"}}
        self.env = {"GITHUB_WORKSPACE": str(self.checkout), "GITHUB_JOB": "native-mlt-sequence",
                    "MW_MLT_SCOPE": "film180-g2", "MW_MLT_G2_ADMISSION": json.dumps(self.dispatch),
                    "P1_COMMON_OUTCOME": "failure", "P1_FILM_OUTCOME": "skipped",
                    "P1_PUBLICATION_OUTCOME": "skipped", "P1_PREPARE_OUTCOME": "skipped",
                    "P1_ADMISSION_OUTCOME": "skipped", "P1_PRODUCER_OUTCOME": "skipped"}
        self.custody = SyntheticCustody()
        def dispatch(raw, current, scenario):
            value = json.loads(raw)
            if value.get("source_sha") != current["source_sha"] or scenario != "film180-g2":
                raise ValueError("fixture dispatch/source differs")
            self.custody.public_key(value.get("recipient"))
            return value
        def forbidden(*args, **kwargs):
            raise AssertionError("preflight must never invoke intake/admission/producer")
        self.native = SimpleNamespace(
            context=lambda env: dict(self.current), runner_root=lambda env: self.base,
            names=lambda env: (self.base / "native-control", self.base / "native-installation", self.base / "native-session"),
            validate_dispatch=dispatch,
            source_guard=lambda repo, current, scenario: {"source_sha256": self.custody.sha((repo / "source.py").read_bytes())},
            prepare=forbidden, intake_compiler=forbidden, begin=forbidden, guard_before_admission=forbidden)
        self.source_state = lambda repo, sha: {"executed_source_sha": sha, "tree_sha": "3" * 40}

    def initialize(self):
        return self.code["p1_initialize"](self.env, self.custody, self.native, self.source_state)

    def finish(self):
        return self.code["p1_finish"](self.env, self.custody, self.native, self.source_state)

    def reports(self, film=True):
        names = ["mw-mlt-g2-contracts-41-1", "mw-mlt-workflow-contracts-41-1"] + (["mw-film180-contracts-41-1"] if film else [])
        for name in names:
            root = self.base / name
            root.mkdir(mode=0o700)
            for relative, raw in {
                "contracts.json": b'{"status":"FAIL","exit_code":1}\n',
                "contracts-00-stdout.log": b"observed stdout\x00\xff\r\n",
                "contracts-00-stderr.log": b"original traceback\r\n",
                "runner.stdout": b"original runner stdout\n", "runner.stderr": b"original runner stderr\n",
                "runner-exit.json": b'{"exit_code":1,"automatic_retry":false}\n',
            }.items():
                self.custody.new_bytes(root / relative, raw)

    def native_dispatch(self):
        control = self.base / "native-control"
        control.mkdir(mode=0o700)
        (control / "records").mkdir(mode=0o700)
        self.custody.new_json(control / "records/dispatch.json", self.dispatch)

    def test_failed_preflight_before_prepare_seals_original_streams_without_effects(self):
        self.initialize()
        self.reports(film=False)
        public, code = self.finish()
        self.assertEqual(code, 2)
        self.assertEqual(public["native_terminal"], "NOT_STARTED")
        self.assertFalse(public["native_dispatch_valid"])
        self.assertEqual(self.custody.captured["common/contracts-00-stdout.log"], b"observed stdout\x00\xff\r\n")
        self.assertEqual(self.custody.captured["common/contracts-00-stderr.log"], b"original traceback\r\n")
        self.assertEqual(len(self.custody.encryption_calls), 1)
        self.assertFalse((self.base / "native-control").exists())
        self.assertFalse(public["source_test_pass_claimed"])
        self.assertFalse(public["admission_granted"])

    def test_lost_control_after_an_observed_effect_is_unknown_even_when_paths_are_absent(self):
        self.initialize()
        self.reports()
        self.env.update(P1_ADMISSION_OUTCOME="success", P1_PRODUCER_OUTCOME="failure")
        public, code = self.finish()
        self.assertEqual((public["native_terminal"], code), ("ERROR_OR_UNKNOWN", 2))
        self.assertFalse(public["native_originals_claimed"])
        self.assertEqual(len(self.custody.encryption_calls), 1)

    def test_present_effect_namespace_prevents_not_started_even_with_skipped_step_claims(self):
        self.initialize()
        self.reports()
        (self.base / "native-installation").mkdir(mode=0o700)
        public, code = self.finish()
        self.assertEqual((public["native_terminal"], code), ("ERROR_OR_UNKNOWN", 2))

    def test_missing_or_boolean_step_outcome_is_not_proof_of_no_effect(self):
        terminal = self.code["p1_native_terminal"]
        for outcome in (None, "", False, "failure", "cancelled"):
            with self.subTest(outcome=outcome):
                self.assertEqual(terminal({"P1_ADMISSION_OUTCOME": outcome, "P1_PRODUCER_OUTCOME": "skipped"},
                                          True, False, False, False), "ERROR_OR_UNKNOWN")

    def test_unreadable_effect_marker_still_seals_source_and_reports_unknown(self):
        self.initialize()
        self.reports()
        original = self.code["p1_exists"]
        def unreadable(path):
            if path.name == "producer-started.json":
                raise PermissionError("fixture marker cannot be observed")
            return original(path)
        self.code["p1_exists"] = unreadable
        try:
            public, code = self.finish()
        finally:
            self.code["p1_exists"] = original
        self.assertEqual((public["native_terminal"], code), ("ERROR_OR_UNKNOWN", 2))
        self.assertEqual(len(self.custody.encryption_calls), 1)
        self.assertIn("common/contracts.json", self.custody.captured)

    def test_changed_source_stays_sealed_and_cannot_finish_successfully(self):
        self.initialize()
        self.reports()
        self.native_dispatch()
        (self.checkout / "source.py").write_bytes(b"source-after\n")
        public, code = self.finish()
        self.assertEqual(code, 2)
        self.assertFalse(public["selected_source_bindings_unchanged"])
        self.assertIn("common/contracts.json", self.custody.captured)

    def test_later_generated_runtime_outputs_do_not_weaken_or_repeat_the_source_epoch(self):
        self.initialize()
        self.reports()
        self.native_dispatch()
        (self.checkout / "_semwright").mkdir()
        (self.checkout / "target").mkdir()
        def strict_after_runtime(*args):
            raise AssertionError("the strict preflight checker must not be misapplied after runtime preparation")
        public, code = self.code["p1_finish"](self.env, self.custody, self.native, strict_after_runtime)
        self.assertEqual(code, 0)
        self.assertTrue(public["selected_source_bindings_unchanged"])
        self.assertFalse(public["full_checkout_input_postcheck_at_final_seal"])
        observed = json.loads(self.custody.captured["records/source-after.json"])
        self.assertIn("selected physical files", observed["scope"])
        self.assertIn("records/source-before.json", self.custody.captured)

    def test_matching_native_dispatch_only_allows_the_existing_native_seal_path(self):
        self.initialize()
        self.reports()
        self.native_dispatch()
        self.env.update(P1_COMMON_OUTCOME="success", P1_FILM_OUTCOME="success",
                        P1_ADMISSION_OUTCOME="success", P1_PRODUCER_OUTCOME="success")
        public, code = self.finish()
        self.assertEqual(code, 0)
        self.assertTrue(public["native_dispatch_valid"])
        self.assertEqual(public["native_terminal"], "ERROR_OR_UNKNOWN")
        self.assertFalse(public["physical_AV_master_acceptance"])

    def test_changed_native_dispatch_is_retained_but_not_accepted(self):
        self.initialize()
        self.reports()
        self.native_dispatch()
        path = self.base / "native-control/records/dispatch.json"
        raw = b'{"source_sha":"foreign","recipient":{}}\n'
        path.write_bytes(raw)
        public, code = self.finish()
        self.assertEqual(code, 2)
        self.assertFalse(public["native_dispatch_valid"])
        self.assertEqual(self.custody.captured["records/native-dispatch-observed.json"], raw)

    def test_second_finish_refuses_before_another_capture_or_encryption(self):
        self.initialize()
        self.reports()
        self.finish()
        with self.assertRaises(ValueError):
            self.finish()
        self.assertEqual(len(self.custody.encryption_calls), 1)

    def test_partial_encryption_failure_is_not_retried(self):
        self.initialize()
        self.reports()
        calls = []
        def fail_once(*args):
            calls.append(args)
            raise OSError("fixture encryption boundary failed")
        self.custody.encrypt_snapshot = fail_once
        with self.assertRaises(OSError):
            self.finish()
        with self.assertRaises(FileExistsError):
            self.finish()
        self.assertEqual(len(calls), 1)

    def test_0755_source_report_root_is_not_fixed_or_accepted(self):
        self.initialize()
        previous = os.umask(0o022)
        try:
            root = self.base / "mw-mlt-g2-contracts-41-1"
            root.mkdir()
        finally:
            os.umask(previous)
        public, code = self.finish()
        self.assertEqual(code, 2)
        self.assertFalse(public["captured_source_originals_complete"])
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)

    def test_0644_report_is_not_fixed_or_copied(self):
        self.initialize()
        root = self.base / "mw-mlt-g2-contracts-41-1"
        root.mkdir(mode=0o700)
        previous = os.umask(0o022)
        try:
            (root / "contracts.json").write_bytes(b'{"status":"FAIL"}\n')
        finally:
            os.umask(previous)
        public, code = self.finish()
        self.assertEqual(code, 2)
        self.assertFalse(public["captured_source_originals_complete"])
        self.assertNotIn("common/contracts.json", self.custody.captured)
        self.assertEqual(stat.S_IMODE((root / "contracts.json").stat().st_mode), 0o644)

    def test_unselected_neighbor_is_never_copied_or_opened(self):
        self.initialize()
        self.reports()
        neighbor = self.base / "mw-mlt-g2-contracts-41-1/not-source-original.txt"
        self.custody.new_bytes(neighbor, b"unselected private neighbor")
        public, code = self.finish()
        self.assertEqual(code, 2)
        self.assertFalse(public["captured_source_originals_complete"])
        self.assertNotIn("common/not-source-original.txt", self.custody.captured)
        self.assertEqual(neighbor.read_bytes(), b"unselected private neighbor")

    def test_changed_recipient_or_source_rejected_before_any_private_namespace(self):
        self.env["MW_MLT_G2_ADMISSION"] = '{"source_sha":"foreign","recipient":{}}'
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertFalse((self.base / "mw-mlt-preflight-custody-41-1").exists())
        self.assertEqual(self.custody.encryption_calls, [])

    def test_preexisting_effect_or_report_directory_refuses_initialization(self):
        (self.base / "native-session").mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertFalse((self.base / "mw-mlt-preflight-custody-41-1").exists())


if __name__ == "__main__":
    unittest.main()
