"""Synthetic trust-boundary tests; none execute a private acceptance case."""
from hashlib import sha256
import copy
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("acceptance_policy", ROOT / "tooling/acceptance_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class AcceptancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "source.txt").write_text("Synthetic implementation pointer", encoding="utf-8")
        meta = {"schema_version": 1, "source_spec_version": "0.1.0", "source_text_published": False}
        self.req = {**meta, "requirements": [
            {"id": value, "area": value.split("-")[1], "implementation_assessment": "EVIDENCE_PRESENT",
             "verification_status": "CI_PARTIAL", "evidence": ["source.txt"]}
            for value in sorted(policy.REQUIREMENT_IDS)]}
        self.acc = {**meta, "cases": [
            {"id": value, "status": "NOT_RUN", "independent": False, "evidence": []}
            for value in sorted(policy.ACCEPTANCE_IDS)]}

    def check(self):
        return policy.validate(self.req, self.acc, self.root)

    def receipt(self):
        relative = "docs/acceptance/evidence/ACC-001/summary.json"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = b'{"synthetic":true,"human_acceptance":"NOT_EXECUTED"}\n'
        path.write_bytes(raw)
        return {"path": relative, "sha256": sha256(raw).hexdigest(), "size_bytes": len(raw)}

    def approve_fixture(self):
        self.acc["cases"][0].update(status="PASS", independent=True, evidence=[self.receipt()])
        return self.acc["cases"][0]

    def test_real_ledgers_keep_their_existing_nonacceptance(self):
        acceptance = policy.load(ROOT / policy.ACC_PATH)
        result = policy.validate(policy.load(ROOT / policy.REQ_PATH), acceptance)
        recorded_pass = sum(case["status"] == "PASS" for case in acceptance["cases"])
        self.assertEqual(result, {"requirements": 208, "acceptance_cases": 60, "product_pass": recorded_pass})

    def test_synthetic_valid_descriptor_checks_metadata_not_review_truth(self):
        self.approve_fixture()
        self.assertEqual(self.check()["product_pass"], 1)

    def test_truthy_strings_and_integers_never_establish_independence(self):
        case = self.approve_fixture()
        for value in ["false", "true", 1, 0, None, [], {}]:
            with self.subTest(value=value):
                case["independent"] = value
                with self.assertRaisesRegex(policy.PolicyError, "boolean"):
                    self.check()

    def test_nonexistent_or_opaque_acceptance_evidence_is_rejected(self):
        case = self.approve_fixture()
        for value in [[{}], ["source.txt"], [{"path": "missing.json"}], []]:
            with self.subTest(value=value):
                case["evidence"] = value
                with self.assertRaises(policy.PolicyError): self.check()
        case["evidence"] = [self.receipt()]
        (self.root / case["evidence"][0]["path"]).unlink()
        with self.assertRaisesRegex(policy.PolicyError, "unavailable"): self.check()

    def test_implementation_paths_reject_directories_and_both_os_escape_syntaxes(self):
        for value in [".", "docs", "../source.txt", "/tmp/source.txt", str(ROOT / "README.md"),
                      "C:/source.txt", "C:source.txt", "a\\b", "a//b", "a/./b", "a/../b", "", 7, None]:
            with self.subTest(value=value):
                self.req["requirements"][0]["evidence"] = [value]
                with self.assertRaises(policy.PolicyError): self.check()

    def test_existing_directory_is_not_evidence(self):
        (self.root / "directory").mkdir()
        self.req["requirements"][0]["evidence"] = ["directory"]
        with self.assertRaisesRegex(policy.PolicyError, "regular_file"): self.check()

    def test_symlink_and_windows_reparse_components_are_refused(self):
        folder = self.root / "nested"
        folder.mkdir()
        (folder / "source.txt").write_text("fixture", encoding="utf-8")
        real = Path.lstat
        for mode, attributes in [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)]:
            with self.subTest(mode=mode, attributes=attributes):
                def mocked(path, *args, **kwargs):
                    if path == folder:
                        return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                    return real(path, *args, **kwargs)
                with patch.object(Path, "lstat", mocked):
                    with self.assertRaisesRegex(policy.PolicyError, "link_refused"):
                        policy.source_file(self.root, "nested/source.txt")

    def test_claimed_requirement_cannot_omit_evidence(self):
        self.req["requirements"][0]["evidence"] = []
        with self.assertRaisesRegex(policy.PolicyError, "needs_evidence"): self.check()

    def test_not_run_cannot_carry_review_or_receipts(self):
        case = self.acc["cases"][0]
        case["independent"] = True
        with self.assertRaisesRegex(policy.PolicyError, "not_run"): self.check()
        case["independent"] = False
        case["evidence"] = [self.receipt()]
        with self.assertRaisesRegex(policy.PolicyError, "not_run"): self.check()

    def test_missing_duplicate_and_malformed_id_rows_are_rejected(self):
        original = copy.deepcopy(self.req)
        for value in [None, {}, [], [None] * 208, [self.req["requirements"][0]] * 208]:
            self.req = {**original, "requirements": value}
            with self.assertRaises(policy.PolicyError): self.check()

    def test_tampered_hash_size_duplicate_and_foreign_case_are_rejected(self):
        case = self.approve_fixture()
        valid = copy.deepcopy(case["evidence"])
        for key, value in [("sha256", "0" * 64), ("size_bytes", True), ("size_bytes", 1)]:
            case["evidence"] = copy.deepcopy(valid)
            case["evidence"][0][key] = value
            with self.assertRaises(policy.PolicyError): self.check()
        case["evidence"] = valid + valid
        with self.assertRaisesRegex(policy.PolicyError, "duplicate"): self.check()
        case["evidence"] = valid
        other = self.acc["cases"][1]
        other.update(status="PASS", independent=True, evidence=valid)
        with self.assertRaisesRegex(policy.PolicyError, "case_scope"): self.check()

    def test_duplicate_nonfinite_oversized_and_nonobject_json_rejected(self):
        path = self.root / "bad.json"
        for raw in ['{"x":1,"x":2}', '{"x":NaN}', '[]', '{"x":Infinity}', 'x' * (policy.MAX_JSON_BYTES + 1)]:
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(policy.PolicyError): policy.load(path)

    def test_malformed_status_is_a_policy_error_not_a_type_error(self):
        self.acc["cases"][0]["status"] = ["PASS"]
        with self.assertRaises(policy.PolicyError): self.check()


if __name__ == "__main__":
    unittest.main()
