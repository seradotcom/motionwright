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
            "crates/storage/src/lib.rs": "55e64709680b7f6c369f8b0fb3cae6fc963e8a87d2f84b9ffa511eabaa5aac54",
            "crates/storage/src/portability.rs": "890a3095a86c485ab5c76ca7f0a815d0e328d32820e9dcb4ba8e3a791b31e13d",
            "crates/storage/Cargo.toml": "3d5ddb85a9c2998090a0f0e7be0c0cd4031bfbeb76a7e42b1b3b75be84105c82",
            "crates/storage/src/bundle_export_unix.rs": "61a31e6d255ea0331b12957b02d8281cfe15f54f2f929e03d9fd78cd13beabb7",
            "crates/storage/src/bundle_export_windows.rs": "b0f88151c36c209ca040ac3ca6b49d381f415d8d083ecbbd7d5d230a65a35f96",
            "crates/storage/src/file_import.rs": "be51d92ed3a5512b5bd118b84438c23cea6c935e34d098888cf39478aee8ebc4",
            "Cargo.lock": "af9ab7a693d34db49ebbbb897c0b89de1990e5aae774dcf4ece60b3155780931",
            "SOURCE_LOCK.json": "4a03e1e5f7b91b4608260fb8437ce51e7df346683d50667860dc5354cb1074b8",
        }
        maps = re.findall(r"^          pins=(\{[^\n]+\})$", source, flags=re.M)
        self.assertEqual(len(maps), 2, "G0 and real CrossOS must bind the same reviewed inputs")
        self.assertEqual(source.count('foundation_reviewed_source="ddf9ef9e0123632835b9a2bc6343659fa4463b3c"'), 2)
        self.assertEqual(source.count('source_lock_reviewed_source="576201b3fca3a7a6f7bc57cc4d6cbf5dd7d3d347"'), 2)
        self.assertEqual(source.count('workspace_lock_source="4679ca7b0965409fb9c230e5063dbeec5490108c"'), 2)
        self.assertIn('source_lock_reviewed_source if rel=="SOURCE_LOCK.json"', source)
        self.assertIn('workspace_lock_source if rel=="Cargo.lock"', source)
        origin = "foundation_reviewed_source if rel in ('crates/storage/src/lib.rs', 'crates/storage/src/portability.rs', 'crates/storage/Cargo.toml', 'crates/storage/src/bundle_export_unix.rs', 'crates/storage/src/bundle_export_windows.rs', 'crates/storage/src/file_import.rs')"
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


    def test_integrate_push_selects_two_macos_cases_without_replaying_11x2(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "branches: [main, 'integrate/phase1-final-composition-20261027']",
            source,
        )
        original = source.split("  real-sqlite-concurrency:\n", 1)[1].split(
            "\n  macos-history-export-selected:\n", 1
        )[0]
        selected = source.split("  macos-history-export-selected:\n", 1)[1]
        self.assertIn("os: [windows-latest, macos-latest]", original)
        self.assertIn(
            "if: github.event_name != 'push' || github.ref != "
            "'refs/heads/integrate/phase1-final-composition-20261027'",
            original,
        )
        self.assertIn(
            "if: github.event_name == 'push' && github.ref == "
            "'refs/heads/integrate/phase1-final-composition-20261027'",
            selected,
        )
        self.assertIn("runs-on: macos-latest", selected)
        self.assertIn('test "$RUNNER_OS" = macOS', selected)
        self.assertNotIn("windows-latest", selected)
        self.assertIn(
            "run_one orphan-history "
            "tests::history_refuses_orphan_original_event_created_by_external_sqlite_writer",
            selected,
        )
        self.assertIn(
            "run_one foreign-kind-export "
            "portability::tests::export_rejects_foreign_event_kind_before_publishing_backup",
            selected,
        )
        self.assertIn('"selected_case_count": 2', selected)
        self.assertIn('"status": "PASS_EXACT_TWO"', selected)
        self.assertIn('"formal_G2": "NOT_GRANTED"', selected)
        self.assertIn("if: always()", selected)
        self.assertEqual(selected.count("run_one orphan-history "), 1)
        self.assertEqual(selected.count("run_one foreign-kind-export "), 1)


if __name__ == "__main__":
    unittest.main()
