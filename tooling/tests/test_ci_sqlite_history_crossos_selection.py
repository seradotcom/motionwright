"""Source-only checks for the selected cross-platform SQLite history roster."""

import ast
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


if __name__ == "__main__":
    unittest.main()
