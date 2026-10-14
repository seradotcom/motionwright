"""Pure source-traceability checks; never certify a private requirement."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("reconcile_requirements", ROOT / "tooling/reconcile_requirements.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()

    def fake_git(self, replacement):
        original = module.git
        def patched(*args):
            if args[0] == "show" and args[1].endswith(":docs/creative-production/requirements-status.json"):
                payload = json.loads(original(*args))
                replacement(payload)
                return json.dumps(payload)
            return original(*args)
        return patch.object(module, "git", side_effect=patched)

    def test_exact_git_tree_has_all_identifiers_without_acceptance_promotion(self):
        report = module.reconcile(self.sha)
        self.assertEqual(len(report["requirement_rows"]), 272)
        self.assertEqual(len(report["acceptance_rows"]), 60)
        self.assertEqual(len({r["id"] for r in report["requirement_rows"]}), 272)
        self.assertEqual(sum(report["counts"]["v01"].values()), 208)
        self.assertEqual(sum(report["counts"]["v05"].values()), 64)
        self.assertIsNone(report["source_or_product_completion_percentage"])
        self.assertFalse(report["release_authorized"])
        self.assertTrue(all(not r["independent_acceptance_by_this_inventory"]
                            and r["test_execution_by_this_inventory"] == "NOT_RUN"
                            for r in report["requirement_rows"]))

    def test_full_git_history_is_mandatory_to_measure_drift(self):
        with patch.object(module, "git", return_value="true"):
            with self.assertRaisesRegex(ValueError, "full Git history"):
                module.reconcile(self.sha)

    def test_shallow_or_missing_sha_is_never_mistaken_for_latest(self):
        for value in ["main", "0" * 39, "", "a" * 41, "../.."]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "full immutable Git SHA"):
                    module.reconcile(value)

    def test_missing_implementation_reference_in_commit_is_rejected(self):
        def remove_source(data):
            row = next(r for r in data["requirements"] if r.get("source_paths"))
            row["source_paths"] = ["private/nonexistent-execution-grant.rs"]
        with self.fake_git(remove_source):
            with self.assertRaisesRegex(ValueError, "missing source pointer"):
                module.reconcile(self.sha)

    def test_duplicate_requirement_id_cannot_be_reconciled(self):
        def duplicate(data):
            data["requirements"][0]["id"] = data["requirements"][1]["id"]
        with self.fake_git(duplicate):
            with self.assertRaisesRegex(ValueError, "changed or duplicated"):
                module.reconcile(self.sha)

    def test_git_symlinks_cannot_be_used_to_claim_source_evidence(self):
        original = module.git
        # Patch one actual source reference instead of an unrelated tree entry.
        report = module.reconcile(self.sha)
        source = next(iter(report["evidence_files"]))
        def patched_target(*args):
            output = original(*args)
            if args[0] == "ls-tree":
                entries = output.split("\0")
                for index, entry in enumerate(entries):
                    if entry.endswith("\t" + source):
                        entries[index] = entry.replace("100644 blob ", "120000 blob ").replace(
                            "100755 blob ", "120000 blob ")
                        break
                return "\0".join(entries)
            return output
        with patch.object(module, "git", side_effect=patched_target):
            with self.assertRaisesRegex(ValueError, "tracked regular file"):
                module.reconcile(self.sha)

    def test_duplicate_and_nonfinite_json_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate authority field"):
            json.loads('{"id":1,"id":2}', object_pairs_hook=module.strict_json_pairs)
        with self.assertRaisesRegex(ValueError, "nonfinite value"):
            json.loads('{"score":NaN}', parse_constant=module.reject_nonfinite_json)


if __name__ == "__main__":
    unittest.main()
