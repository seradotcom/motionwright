"""Closed lineage predicates only; these fixtures never grant Native or G2."""
from __future__ import annotations
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import native_manim_recovery_lineage as lineage
import native_manim_retained_reference as retained


class RetainedLineageTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "MANIM_NATIVE_SCENARIO": "recovery-runtime-cycle",
            retained.ENVIRONMENT: retained.CONTINUATION,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.current = {**retained.ORIGIN, "source_sha": "1" * 40, "run_id": "42379999999"}

    def test_artifact_roster_and_workflow_select_exactly_three_new_seals(self):
        self.assertEqual(lineage.selected_files(), {
            "reopen-original-reference.mwseal", "edited.mwseal", "reopen-edited.mwseal",
            "resume-outer.mwseal", "resume-outer.public.json"})
        with patch.dict(os.environ, {retained.ENVIRONMENT: "none"}):
            self.assertEqual(len(lineage.selected_files()), 6)
            self.assertIn("original-reference.mwseal", lineage.selected_files())
        with patch.dict(os.environ, {"MANIM_NATIVE_SCENARIO": "editable-source-resume"}):
            with self.assertRaisesRegex(ValueError, "requires_frozen_runtime"):
                lineage.selected_files()
        with patch.dict(os.environ, {retained.ENVIRONMENT: "retained-n1-other"}):
            with self.assertRaisesRegex(ValueError, "closed_selector"):
                lineage.selected_files()
        workflow = (ROOT / ".github/workflows/native-manim-e2e.yml").read_text()
        step = workflow.split("      - name: Retain the three attempted continuation phases", 1)[1]
        step = step.split("  native-manim-roundtrip:", 1)[0]
        self.assertIn("inputs.recovery_continuation == 'retained-n1-42369864661'", step)
        paths = step.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
        self.assertEqual({line.strip().rsplit("/", 1)[-1] for line in paths.splitlines()}, lineage.selected_files())
        self.assertIn("fromJSON(steps.resume_private_budget.outputs.minutes)", step)

    def test_cycle_refuses_four_effects_or_promoted_missing_reference_postcheck(self):
        runtime = {"unit_scope": "STRUCTURAL_ONLY_NOT_NATIVE_EVIDENCE"}
        value = {
            "schema": "motionwright.manim.recovery-runtime.retained-reference-cycle/1",
            "status": "THREE_NEW_PHASES_COMPLETE_REFERENCE_RUNTIME_POSTCHECK_NOT_RETAINED",
            "context": self.current, "origin_context": lineage.ORIGIN,
            "source_recovery": lineage.SOURCE_RECOVERY, "native_effect_invocations": 3,
            "additional_reference_renders": 1, "runtime_binding": runtime,
            "project_changes_applied": 0, "original_edit_report_recreated": False,
            "old_result_replaced": False, "equivalence_to_old_runtime": False,
            "final_F_association": "PENDING", "formal_G2_granted": False, "automatic_retry": False,
            "retained_reference": retained.contract(), "reference_phase_complete": False,
            "complete_cycle_verification": False, "reference_post_native_runtime_observation": "NOT_RETAINED",
        }
        lineage.checked_cycle_observation(value, self.current, runtime)
        for key, replacement in (
            ("native_effect_invocations", 4), ("additional_reference_renders", 2),
            ("reference_phase_complete", True), ("complete_cycle_verification", True),
            ("reference_post_native_runtime_observation", "PASS"),
            ("formal_G2_granted", True), ("project_changes_applied", 1),
            ("context", retained.ORIGIN), ("old_result_replaced", True),
            ("retained_reference", {}),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                lineage.checked_cycle_observation({**value, key: replacement}, self.current, runtime)
        with patch.dict(os.environ, {retained.ENVIRONMENT: "none"}):
            with self.assertRaises(ValueError):
                lineage.checked_cycle_observation(value, self.current, runtime)

    def test_equal_pre_native_measurements_keep_the_postcheck_absent(self):
        # Data-shaped protocol input, not a runtime observation or Native result.
        original = {"schema": "motionwright.manim.runtime-inventory/2", "context": retained.ORIGIN,
                    "native_render_performed": False, "inventory_sha256": "2" * 64,
                    "binaries": {"python": {"sha256": "3" * 64}}, "manim_version": "0.21.0"}
        current = {**copy.deepcopy(original), "context": self.current}
        observed = lineage.checked_retained_runtime(original, current, self.current)
        self.assertEqual(observed, {"observation_scope": "ORIGINAL_PRE_NATIVE_INVENTORY_ONLY",
            "post_native_runtime_observation": "NOT_RETAINED", "reference_phase_complete": False})
        for key, replacement in (("inventory_sha256", "4" * 64), ("binaries", {}),
                                 ("native_render_performed", True), ("context", retained.ORIGIN)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "pre_native_inventory_changed"):
                lineage.checked_retained_runtime(original, {**current, key: replacement}, self.current)


if __name__ == "__main__":
    unittest.main()
