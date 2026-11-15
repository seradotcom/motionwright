"""Source controls for the package extraction owner.

Only inert Python child processes run. Linux kernel controls are explicitly
skipped where fork/pidfd/subreaper are unavailable. No package is extracted.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import candidate_package_extraction as extraction


class ExtractionPolicyTests(unittest.TestCase):
    def test_fixed_budget_reserves_cleanup_inside_ninety_seconds(self):
        budget = extraction.Budget(123, 90)
        self.assertEqual(budget.operation_ns, 123 + 88_000_000_000)
        self.assertEqual(budget.join_ns, 123 + 89_000_000_000)
        self.assertEqual(budget.cleanup_ns, 123 + 90_000_000_000)
        self.assertLess(budget.operation_ns, budget.join_ns)
        self.assertLess(budget.join_ns, budget.cleanup_ns)

    def test_budget_cannot_be_extended_or_coerced(self):
        for origin, seconds in [(-1, 90), (True, 90), (0, 91), (0, 2), (0, 90.0), (0, True)]:
            with self.subTest(origin=origin, seconds=seconds):
                with self.assertRaises(extraction.ExtractionError):
                    extraction.Budget(origin, seconds)

    def test_request_has_no_shell_relative_program_or_foreign_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            for argv, label in [(["relative-program"], "extract"),
                                ([sys.executable], "attach"),
                                ([sys.executable, "\0"], "extract")]:
                with self.subTest(argv=argv, label=label):
                    with self.assertRaises(extraction.ExtractionError):
                        extraction.request(argv, Path(tmp), label)

    def test_request_metadata_limit_precedes_process_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(extraction.ExtractionError, "request_budget"):
                extraction.request([str(Path(tmp) / ("x" * 4000))] + ["x" * 4096] * 15,
                                   Path(tmp), "extract")

    def test_closed_boolean_without_originals_is_never_custody(self):
        with self.assertRaisesRegex(extraction.ExtractionError, "original_set"):
            extraction.validate_originals({"process_tree_closed": True}, {}, {})


LINUX = sys.platform.startswith("linux") and hasattr(os, "fork") and hasattr(os, "P_PIDFD")


@unittest.skipUnless(LINUX, "requires real Linux fork, pidfd and subreaper kernel controls")
class LinuxOwnedExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.originals = self.root / "originals"
        self.originals.mkdir()

    def command(self, source):
        return [sys.executable, "-I", "-B", "-c", source]

    def read_originals(self):
        return {key: (self.originals / ("extract." + suffix)).read_bytes()
                for key, suffix in extraction.SUFFIXES.items()}

    def run_inert(self, source, **kwargs):
        argv = self.command(source)
        result = extraction.run_owned(argv, self.root, self.originals, "extract",
                                      timeout=5, **kwargs)
        return argv, result

    def test_real_barrier_pidfd_wait_and_originals_reject_tampering(self):
        argv, result = self.run_inert("import sys;print('inert stdout');print('inert stderr',file=sys.stderr)")
        raw = self.read_originals()
        parsed = extraction.validate_originals(
            raw, extraction.request(argv, self.root, "extract"), extraction.sources())
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(raw["stdout"], b"inert stdout\n")
        self.assertEqual(raw["stderr"], b"inert stderr\n")
        self.assertTrue(parsed["terminal"]["process_tree_closed"])
        self.assertEqual(len(parsed["reaps"]), 1)
        changed = copy.deepcopy(raw)
        closure = extraction.core.decode_frame(changed["closure"], eof=True)
        closure["supervisor_wait"]["si_status"] = True
        changed["closure"] = extraction.encoded(closure)
        completion = extraction.core.decode_frame(changed["completion"], eof=True)
        completion["closure_sha256"] = extraction.sha(changed["closure"])
        changed["completion"] = extraction.encoded(completion)
        with self.assertRaisesRegex(extraction.ExtractionError, "wait_not_closed"):
            extraction.validate_originals(
                changed, extraction.request(argv, self.root, "extract"), extraction.sources())

    def test_closed_stdio_orphan_is_reaped_before_success(self):
        source = (
            "import os,time\n"
            "pid=os.fork()\n"
            "if pid==0:\n"
            " os.setsid();os.close(1);os.close(2);time.sleep(.15);os._exit(0)\n"
            "os._exit(0)\n")
        argv, _ = self.run_inert(source)
        parsed = extraction.validate_originals(
            self.read_originals(), extraction.request(argv, self.root, "extract"),
            extraction.sources())
        self.assertEqual(len(parsed["reaps"]), 2)
        self.assertEqual(parsed["terminal"]["closure_mechanism"],
                         "PR_SET_CHILD_SUBREAPER+waitid_ECHILD")

    def test_lingering_orphan_fails_and_retains_kernel_cleanup_original(self):
        source = (
            "import os,time\n"
            "pid=os.fork()\n"
            "if pid==0:\n"
            " os.setsid();os.close(1);os.close(2);time.sleep(30);os._exit(0)\n"
            "os._exit(0)\n")
        with self.assertRaises(extraction.ExtractionError):
            extraction.run_owned(self.command(source), self.root, self.originals,
                                 "extract", timeout=3)
        raw = self.read_originals()
        closure = extraction.core.decode_frame(raw["closure"], eof=True)
        self.assertTrue(closure["errors"])
        records = [extraction.core.decode_frame(line + b"\n", eof=True)
                   for line in raw["subtree"].splitlines()]
        self.assertTrue(records[-1]["process_tree_closed"])
        self.assertTrue(records[-1]["errors"])
        self.assertEqual(closure["budget"]["seconds"], 3)
        self.assertFalse((self.originals / "inventory.json").exists())

    def test_stream_overflow_is_retained_failure_and_never_partial_success(self):
        with self.assertRaises(extraction.ExtractionError):
            self.run_inert("import sys;sys.stdout.write('x'*100000)", stream_limit=1024)
        raw = self.read_originals()
        self.assertEqual(len(raw["stdout"]), 1024)
        closure = extraction.core.decode_frame(raw["closure"], eof=True)
        self.assertTrue(closure["errors"])
        self.assertTrue(closure["streams"]["stdout"]["truncated"])
        self.assertFalse((self.originals / "inventory.json").exists())

    def test_exclusive_originals_refuse_a_second_invocation(self):
        argv, _ = self.run_inert("print('once')")
        before = self.read_originals()
        with self.assertRaisesRegex(extraction.ExtractionError, "already_exist"):
            extraction.run_owned(argv, self.root, self.originals, "extract", timeout=5)
        self.assertEqual(before, self.read_originals())


if __name__ == "__main__":
    unittest.main()
