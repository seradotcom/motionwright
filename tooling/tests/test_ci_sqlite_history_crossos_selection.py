"""Source-only checks for the selected cross-platform SQLite history roster."""

import ast
import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/storage-sqlite-boundary-crossos.yml"
STORAGE = ROOT / "crates/storage/src/lib.rs"
SNAPSHOT = (
    "history_snapshot_never_mixes_unvalidated_external_revision",
    "history_snapshot_forward_never_mixes_unvalidated_external_revision",
)


class HistoryCrossOsSelectionTests(unittest.TestCase):
    def test_both_source_guards_bind_snapshot_cases(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        catalogs = [
            "{" + section.split("\n          for rel,cases in wanted.items():", 1)[0]
            for section in source.split("\n          wanted={")[1:]
        ]
        self.assertEqual(len(catalogs), 2)
        definitions = STORAGE.read_text(encoding="utf-8")
        for catalog in catalogs:
            wanted = ast.literal_eval(catalog)
            self.assertEqual(len(wanted), 4)
            self.assertEqual(sum(map(len, wanted.values())), 11)
            self.assertEqual(wanted["crates/storage/src/lib.rs"], SNAPSHOT)
            for name in SNAPSHOT:
                self.assertEqual(definitions.count("fn " + name + "("), 1)
        self.assertEqual(source.count('"expected_total_invocations_per_OS":11'), 2)
        self.assertEqual(source.count('"bound_SQLite_cases":11'), 2)

    def test_reviewed_composed_storage_source_pins_are_exact_in_both_jobs(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        expected = {
            "crates/storage/src/lib.rs": "23f85144b11965b38f7cdee9d76ad8d87475041ca4d4b7ebeb588edb939282b9",
            "crates/storage/src/portability.rs": "886c0b016b91faf1badeff2ac9ab07a8786d904da29bf3860e00a2b7e5cc1d52",
        }
        maps = re.findall(r"^          pins=(\{[^\n]+\})$", source, flags=re.M)
        self.assertEqual(len(maps), 2, "G0 and real CrossOS must bind the same reviewed inputs")
        self.assertEqual(source.count('foundation_reviewed_source="6e5ca13310aa3e8fe819816d39072075df40bd7c"'), 2)
        origin = 'foundation_reviewed_source if rel in ("crates/storage/src/lib.rs","crates/storage/src/portability.rs")'
        self.assertEqual(source.count(origin), 2, "Both Storage inputs must name the composed source")
        for raw in maps:
            pins = ast.literal_eval(raw)
            for rel, sha in expected.items():
                self.assertEqual(pins[rel], sha)
                self.assertEqual(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), sha)

    def test_real_os_matrix_selects_two_history_cases(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        start = source.index("          suites=[")
        end = source.index("          env=dict(", start)
        suites = source[start:end]
        self.assertEqual(suites.count('("history-snapshot",'), 1)
        self.assertIn(
            '("history-snapshot",["cargo","test","--locked","-p",'
            '"motionwright-storage","--lib","history_snapshot_","--","--test-threads=1"]',
            suites,
        )
        for name in SNAPSHOT:
            self.assertIn('"tests::' + name + '"', suites)
        self.assertEqual(suites.count('("late-writer",'), 1)
        self.assertEqual(suites.count('("event-cursor",'), 1)

    def test_case_accounting_is_scoped_and_not_physical_g2(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('len(report["suites"])==6', source)
        self.assertIn('report["tests_executed"]==11', source)
        self.assertIn('"selected_SQLite_cases":report["tests_executed"]', source)
        self.assertNotIn("REAL_SQLITE_G2", source)
        self.assertNotIn('"G2_selected_tests":report["tests_executed"]', source)
        self.assertIn('"filesystem_SQLite_crash_atomicity":"NOT_ESTABLISHED"', source)


    def test_incremental_sqlite_source_gate_does_not_restamp_formal_acceptance(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        g0 = workflow.split("  g0-source-policy:", 1)[1].split(
            "\n  real-sqlite-concurrency:", 1)[0]
        source_step = g0.split(
            "- name: Enforce public source and conformance policies", 1)[1].split(
            "\n      - name: Verify selected SQLite snapshot", 1)[0]
        for policy in ("source_policy", "public_surface_policy"):
            self.assertEqual(source_step.count("tooling/" + policy + ".py"), 1)
        # Formal assessed ledgers are verified separately from incremental G0.
        self.assertNotIn("tooling/acceptance_policy.py", source_step)
        self.assertNotIn("tooling/creative_status_policy.py", source_step)
        formal = (ROOT / ".github/workflows/package-candidate.yml").read_text(
            encoding="utf-8")
        self.assertIn("tooling/acceptance_policy.py", formal)
        self.assertIn("tooling/creative_status_policy.py", formal)


if __name__ == "__main__":
    unittest.main()
