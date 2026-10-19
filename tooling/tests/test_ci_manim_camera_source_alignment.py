"""Adversarial static checks for the source-bound Manim camera G1 gate."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_manim_camera_source_alignment as gate


class CameraSourceAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[2]
        cls.sha = gate.git(cls.repo, "rev-parse", "HEAD").decode().strip()
        runner = gate.committed_blob(cls.repo, cls.sha, gate.RUNNER)
        pins = gate.source_pin_map(runner)
        paths = set(pins) | {gate.RUNNER, gate.WORKFLOW, gate.PIXEL_TEST}
        cls.original = {
            p: gate.committed_blob(cls.repo, cls.sha, p) for p in paths
        }
        cls.pins = pins

    def evaluate(self, substitutions=None):
        data = dict(self.original)
        data.update(substitutions or {})
        return gate.assess(lambda name: data[name])

    @staticmethod
    def replace_exact(original: bytes, old: bytes, new: bytes) -> bytes:
        assert original.count(old) == 1, (old, original.count(old))
        return original.replace(old, new)

    def test_source_bound_original_manifest_and_14_methods_align(self):
        observed = self.evaluate()
        self.assertEqual(observed["status"], "ALIGNED_SOURCE_ONLY")
        self.assertEqual(observed["expected_test_count_per_entrypoint"], 14)
        self.assertEqual(len(observed["static_case_names"]), 14)
        self.assertEqual(observed["G1_test_definition_sha256"],
                         hashlib.sha256(self.original[gate.PIXEL_TEST]).hexdigest())
        self.assertFalse(observed["native_execution"])
        self.assertFalse(observed["physical_media_readback"])
        self.assertFalse(observed["human_acceptance"])

    def test_stale_workflow_definition_digest_is_rejected(self):
        workflow = self.replace_exact(
            self.original[gate.WORKFLOW],
            self.pins[gate.PIXEL_TEST].encode(),
            b"0" * 64,
        )
        with self.assertRaisesRegex(gate.AlignmentError, "obsolete test definition"):
            self.evaluate({gate.WORKFLOW: workflow})

    def test_stale_workflow_test_count_is_rejected(self):
        workflow = self.replace_exact(
            self.original[gate.WORKFLOW],
            b'row["counts"]["tests_run"] == 14',
            b'row["counts"]["tests_run"] == 11',
        )
        workflow = self.replace_exact(
            workflow,
            b'row["counts"]["passed"] == 14',
            b'row["counts"]["passed"] == 11',
        )
        with self.assertRaisesRegex(gate.AlignmentError, "test count differs"):
            self.evaluate({gate.WORKFLOW: workflow})

    def test_workflow_passed_count_must_equal_exact_cases(self):
        workflow = self.replace_exact(
            self.original[gate.WORKFLOW],
            b'row["counts"]["passed"] == 14',
            b'row["counts"]["passed"] == 11',
        )
        with self.assertRaisesRegex(gate.AlignmentError, "passed count differs"):
            self.evaluate({gate.WORKFLOW: workflow})

    def test_omission_guard_must_not_be_removed(self):
        # The safety-control step also has this tuple. Change the final
        # occurrence, which belongs to the source-bound pixel G1 step.
        token = b'("errors", "failures", "skipped", "expected_failures", "unexpected_successes")'
        original = self.original[gate.WORKFLOW]
        self.assertEqual(original.count(token), 2)
        before, separator, after = original.rpartition(token)
        self.assertEqual(separator, token)
        workflow = (before
                    + b'("errors", "failures", "expected_failures", "unexpected_successes")'
                    + after)
        with self.assertRaisesRegex(gate.AlignmentError, "guard missing"):
            self.evaluate({gate.WORKFLOW: workflow})

    def test_both_entrypoints_cannot_be_reduced_to_one(self):
        workflow = self.replace_exact(
            self.original[gate.WORKFLOW],
            b'--mode both --require-complete --expected-source "$MW_SOURCE_SHA"',
            b'--mode discovery --require-complete --expected-source "$MW_SOURCE_SHA"',
        )
        with self.assertRaisesRegex(gate.AlignmentError, "guard missing"):
            self.evaluate({gate.WORKFLOW: workflow})

    def test_stdout_stderr_identity_guard_must_remain(self):
        workflow = self.replace_exact(
            self.original[gate.WORKFLOW],
            b'hashlib.sha256((p.parent / row[key]["file"]).read_bytes()).hexdigest() == row[key]["sha256"]',
            b'len((p.parent / row[key]["file"]).read_bytes()) >= 0',
        )
        with self.assertRaisesRegex(gate.AlignmentError, "guard missing"):
            self.evaluate({gate.WORKFLOW: workflow})

    def test_runner_source_pin_mutation_is_rejected(self):
        runner = self.replace_exact(
            self.original[gate.RUNNER],
            self.pins[gate.PIXEL_TEST].encode(),
            b"f" * 64,
        )
        with self.assertRaisesRegex(gate.AlignmentError, "stale source pin"):
            self.evaluate({gate.RUNNER: runner})

    def test_test_source_changed_without_repinning_is_rejected(self):
        changed = self.original[gate.PIXEL_TEST] + b"\n# Untested source change\n"
        with self.assertRaisesRegex(gate.AlignmentError, "stale source pin"):
            self.evaluate({gate.PIXEL_TEST: changed})

    def test_producer_source_changed_without_repinning_is_rejected(self):
        key = "crates/native/src/production.rs"
        self.assertIn(key, self.original)
        changed = self.original[key] + b"\n// unreviewed modification\n"
        with self.assertRaisesRegex(gate.AlignmentError, "stale source pin"):
            self.evaluate({key: changed})

    def test_duplicated_runner_pin_assignment_is_rejected(self):
        runner = self.original[gate.RUNNER] + b"\nSOURCE_INPUTS = {}\n"
        with self.assertRaisesRegex(gate.AlignmentError, "one literal SOURCE_INPUTS"):
            self.evaluate({gate.RUNNER: runner})

    def test_malformed_source_key_and_digest_are_rejected(self):
        self.assertFalse(gate.valid_relative("../fixture"))
        self.assertFalse(gate.valid_relative(r"C:\root\blob"))
        self.assertFalse(gate.valid_relative("/root/blob"))
        self.assertFalse(gate.valid_relative("-dangerous"))
        self.assertFalse(gate.valid_relative("a//b"))
        self.assertTrue(gate.valid_relative("crates/native/src/production.rs"))
        runner = self.replace_exact(
            self.original[gate.RUNNER],
            self.pins[gate.PIXEL_TEST].encode(),
            b"BAD_HASH",
        )
        with self.assertRaisesRegex(gate.AlignmentError, "malformed camera source pin"):
            self.evaluate({gate.RUNNER: runner})

    def test_failed_or_missing_source_does_not_return_admitted(self):
        missing = dict(self.original)
        del missing[gate.PIXEL_TEST]
        with self.assertRaisesRegex(gate.AlignmentError, "required source input is absent"):
            gate.assess(lambda name: missing[name])

    def test_test_source_static_method_count_excludes_helpers(self):
        names = gate.static_case_names(self.original[gate.PIXEL_TEST])
        self.assertEqual(len(names), 14)
        self.assertIn(
            "test_horizontal_only_motion_is_not_a_valid_vertical_camera_move",
            names,
        )
        self.assertIn("test_symlink_plan_is_not_trusted", names)
        test_source = self.original[gate.PIXEL_TEST] + b"\ndef load_tests(*args): return ()\n"
        with self.assertRaisesRegex(gate.AlignmentError, "dynamic unittest loading"):
            gate.static_case_names(test_source)


if __name__ == "__main__":
    unittest.main()
