#!/usr/bin/env python3
"""Acceptance intake tests run without private spec text or heavyweight runtimes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import copy
import os
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "acceptance_session.py"
SPEC = importlib.util.spec_from_file_location("acceptance_session", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

SOURCE_SHA = "a" * 40
UTC = "2026-10-08T20:00:00+00:00"


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = module.create_session(source_sha=SOURCE_SHA, executed_by="operator-fixture")

    def write_evidence(self, relative="ACC-001/screenshot.png", data=b"fake-evidence-fixture"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return module.fingerprint(self.root, relative)

    def test_unreviewed_session_cannot_auto_pass(self):
        self.assertEqual(len(self.manifest["cases"]), 60)
        self.assertEqual([case["id"] for case in self.manifest["cases"]], module.EXPECTED_IDS)
        self.assertEqual(self.manifest["source"]["semwright_sha"], module.locked_semwright_revision())
        self.assertFalse(self.manifest["source"]["private_spec_text_embedded"])
        totals = module.validate_session(self.manifest, evidence_root=self.root)
        self.assertEqual(totals, {"NOT_RUN": 60, "PASS": 0, "FAIL": 0, "BLOCKED": 0})

    def approve(self):
        case = self.manifest["cases"][0]
        case.update({
            "status": "PASS",
            "executed_by": "Operator A",
            "executed_at_utc": UTC,
            "reviewed_by": "Reviewer B",
            "reviewed_at_utc": UTC,
            "independent": True,
            "notes": "Verified the installed desktop's frame-exact scenario against the private case criteria",
            "evidence": [self.write_evidence()],
        })
        return case

    def test_complete_independent_pass_with_exact_sha_is_accepted(self):
        self.approve()
        totals = module.validate_session(self.manifest, evidence_root=self.root)
        self.assertEqual(totals["PASS"], 1)
        self.assertEqual(totals["NOT_RUN"], 59)

    def test_self_approved_pass_is_not_independent(self):
        case = self.approve()
        case["reviewed_by"] = "operator a"
        with self.assertRaisesRegex(module.IntakeError, "independently identified reviewer"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_unreviewed_pass_is_rejected_even_with_a_real_file(self):
        case = self.approve()
        case["independent"] = False
        with self.assertRaisesRegex(module.IntakeError, "independently identified reviewer"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_missing_pass_evidence_is_never_treated_as_complete(self):
        case = self.approve()
        case["evidence"] = []
        with self.assertRaisesRegex(module.IntakeError, "at least one actual evidence"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_tampered_evidence_fails_closed(self):
        self.approve()
        (self.root / "ACC-001/screenshot.png").write_bytes(b"tampered")
        with self.assertRaisesRegex(module.IntakeError, "evidence has changed"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_missing_private_spec_or_source_pin_cannot_be_forged(self):
        self.manifest["source"]["semwright_sha"] = "f" * 40
        with self.assertRaisesRegex(module.IntakeError, "Semwright source SHA"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_reordered_or_duplicated_cases_fail_closed(self):
        case = copy.deepcopy(self.manifest["cases"][0])
        self.manifest["cases"][1] = case
        with self.assertRaisesRegex(module.IntakeError, "each of the exact 60"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_traversal_and_symlink_are_rejected_before_hashing(self):
        (self.root / "sub").mkdir()
        outside = self.root.parent / (self.root.name + "-outside-evidence.txt")
        outside.write_bytes(b"private evidence")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(module.IntakeError, "relative|parent"):
            module.fingerprint(self.root, "../" + outside.name)
        with self.assertRaisesRegex(module.IntakeError, "relative|parent"):
            module.fingerprint(self.root, "/etc/hosts")
        link = self.root / "sub" / "linked.txt"
        link.symlink_to(outside)
        with self.assertRaisesRegex(module.IntakeError, "symlink"):
            module.fingerprint(self.root, "sub/linked.txt")

    def test_fail_and_blocked_require_real_evidence_or_notes(self):
        fail_case = self.manifest["cases"][0]
        fail_case.update({
            "status": "FAIL",
            "executed_by": "Operator A",
            "executed_at_utc": UTC,
            "notes": "Observed an actual render mismatch",
            "evidence": [self.write_evidence()],
        })
        blocked = self.manifest["cases"][1]
        blocked.update({
            "status": "BLOCKED",
            "executed_by": "Operator A",
            "executed_at_utc": UTC,
            "notes": "Device not available in the isolated acceptance environment",
        })
        totals = module.validate_session(self.manifest, evidence_root=self.root)
        self.assertEqual(totals["FAIL"], 1)
        self.assertEqual(totals["BLOCKED"], 1)
        self.assertEqual(totals["PASS"], 0)
        blocked["notes"] = ""
        with self.assertRaisesRegex(module.IntakeError, "blocking reason"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_evidence_paths_are_validated_with_boundaries(self):
        with self.assertRaisesRegex(module.IntakeError, "relative|parent"):
            module.fingerprint(self.root, "sub/./evidence.png")
        with self.assertRaisesRegex(module.IntakeError, "relative|parent"):
            module.fingerprint(self.root, "sub/../evidence.png")
        with self.assertRaisesRegex(module.IntakeError, "backslashes"):
            module.fingerprint(self.root, r"sub\evidence.png")

    def test_case_status_must_use_utc_timestamps(self):
        case = self.approve()
        case["executed_at_utc"] = "2026-10-08"
        with self.assertRaisesRegex(module.IntakeError, "execution time"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_review_must_follow_execution_and_contain_rationale(self):
        case = self.approve()
        case["reviewed_at_utc"] = "2026-10-08T19:00:00+00:00"
        with self.assertRaisesRegex(module.IntakeError, "cannot precede"):
            module.validate_session(self.manifest, evidence_root=self.root)
        case["reviewed_at_utc"] = UTC
        case["notes"] = ""
        with self.assertRaisesRegex(module.IntakeError, "review rationale"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_local_timezone_falsely_labeled_utc_is_rejected(self):
        case = self.approve()
        case["executed_at_utc"] = "2026-10-08T14:00:00-06:00"
        with self.assertRaisesRegex(module.IntakeError, "execution time"):
            module.validate_session(self.manifest, evidence_root=self.root)

    def test_source_lock_requires_full_commit(self):
        with self.assertRaisesRegex(module.IntakeError, "source_sha"):
            module.create_session(source_sha="main")


if __name__ == "__main__":
    unittest.main()
