"""Development-only conformance impact, preserving strict final acceptance."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import conformance_development_impact as dev

DIGEST = "a" * 64
BLOB_A = ("100644", "a" * 40)
BLOB_B = ("100644", "b" * 40)


def case(identity="record-1", source="product.py", oracle="oracle.py"):
    return {
        "id": identity,
        "criterion_ids": ["CR-TEST-01#/statement"],
        "source_inputs": [{"path": source, "sha256": DIGEST}],
        "definitions": [{"path": oracle, "sha256": DIGEST}],
    }


@contextmanager
def development_history(source_name):
    """A tiny synthetic catalog and real Git history, independent of product HEAD."""
    with TemporaryDirectory(prefix="motionwright-impact-") as folder:
        root = Path(folder).resolve()
        dev.git(root, "-c", "init.templateDir=", "init",
                "--object-format=sha1", "--initial-branch=fixture")

        def commit(message):
            dev.git(root, "add", "--all")
            dev.git(root, "-c", "user.name=Conformance fixture",
                    "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=",
                    "commit", "--quiet", "-m", message)
            return dev.commit_sha(root, "HEAD")

        def write(name, content):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))

        write("SOURCE_LOCK.json", "{}\n")
        write(source_name, "value = 1\n")
        write("oracle.py", "assert value == 1\n")
        original = commit("fixture source")
        source = {
            "motionwright_sha": original,
            "digest_kind": "git-text-lf",
            "source_lock_sha256": hashlib.sha256(b"{}\n").hexdigest(),
        }
        rid, fid = "CR-TEST-01", "CR-TEST-01#/statement"
        record = case(source=source_name)
        record["complete_criterion_ids"] = [fid]
        for ref in record["source_inputs"] + record["definitions"]:
            ref["sha256"] = hashlib.sha256(
                (root / ref["path"]).read_bytes()).hexdigest()
        index = {
            "schema": "motionwright.technical-evidence/1",
            "source": source, "criteria": {fid: DIGEST},
            "contracts": {rid: {"scope": "required"}}, "normative_edges": [],
            "records": [record],
        }
        index_text = json.dumps(index, sort_keys=True) + "\n"
        index_path = "docs/acceptance/technical-evidence.json"
        write(index_path, index_text)
        descriptor = {
            "path": index_path, "digest_kind": "git-text-lf",
            "sha256": hashlib.sha256(index_text.encode("utf-8")).hexdigest(),
        }
        row = {
            "id": rid,
            "technical_assessment": {
                "criteria": [{"id": fid, "source_clause_sha256": DIGEST}],
            },
        }
        for position, (name, groups) in enumerate(dev.LEDGERS):
            ledger = {
                "technical_evidence_index": descriptor,
                "current_assessment_source": source,
                **{group: [] for group in groups},
            }
            if position == 0:
                ledger[groups[0]] = [row]
            write(name, json.dumps(ledger, sort_keys=True) + "\n")
        baseline = commit("fixture historical catalog")
        write(source_name, "value = 2\n")
        target = commit("fixture mapped source change")
        # This tiny synthetic one-row history tests path impact only. The
        # actual full 396-row normative catalog is validated independently.
        with patch.multiple(dev, EXPECTED_ROWS=1, EXPECTED_FIELDS=1,
                            EXPECTED_EDGES=0, PHASE1_ALL_ROWS=1,
                            PHASE1_REQUIRED_ROWS=1, PHASE1_REQUIRED_FIELDS=1), patch.object(
                                dev, "validate_catalog_links", return_value=None):
            yield root, baseline, target


class DevelopmentSourceImpactPureTests(unittest.TestCase):
    def test_phase1_impact_does_not_turn_future_required_rows_into_phase1(self):
        current = case(identity="required-source", source="SOURCE_LOCK.json")
        future = case(identity="future-source", source="SOURCE_LOCK.json")
        future["criterion_ids"] = ["A05-E00-01#/statement"]
        original = {"SOURCE_LOCK.json": BLOB_A, "oracle.py": BLOB_A}
        modified = {**original, "SOURCE_LOCK.json": BLOB_B}
        result = dev.summarize_references(
            [current, future], original, modified, {"SOURCE_LOCK.json"},
            phase1_required_fields=frozenset({"CR-TEST-01#/statement"}))
        self.assertEqual(result["counts"]["source_path_affected_records"], 2)
        p1 = result["phase1_required_impact"]
        self.assertEqual(p1["historical_record_associations"], 1)
        self.assertEqual(p1["affected_atomic_criteria"], 1)
        self.assertEqual(p1["affected_rows"], ["CR-TEST-01"])
        self.assertEqual(p1["affected_row_count"], 1)
        self.assertTrue(p1["not_a_test_rerun_or_new_acceptance"])
        self.assertEqual(result["automatically_admitted_criteria"], 0)

    def test_phase1_required_scope_is_exact_canonical_247_872_without_reloading_evidence(self):
        result = dev.inspect_development(ROOT, "HEAD", baseline="HEAD")
        scope = result["impact"]["phase1_required_impact"]
        self.assertEqual(scope["canonical_required_rows"], 247)
        self.assertEqual(scope["canonical_required_atomic_criteria"], 872)
        self.assertEqual(scope["historical_record_associations"], 0)
        self.assertEqual(scope["affected_atomic_criteria"], 0)
        self.assertEqual(scope["affected_rows"], [])
        self.assertEqual(result["recorded_or_new_current_PASS"], 0)
        self.assertFalse(result["strict_full_F_index_and_artifact_review_performed"])

    def test_original_normative_links_are_validated_on_development_impact(self):
        # Cardinality alone would accept a rewritten 506-edge traceability
        # graph. Reuse the canonical existing validator, not a new matrix.
        with patch.object(dev, "validate_catalog_links",
                          side_effect=ValueError("A5_ORIGINAL_TRACEABILITY_REJECT")):
            with self.assertRaisesRegex(ValueError, "A5_ORIGINAL_TRACEABILITY_REJECT"):
                dev.inspect_development(ROOT, "HEAD", baseline="HEAD")

    def test_changed_506_edge_content_cannot_masquerade_as_preserved_catalog(self):
        index = json.loads((ROOT / "docs/acceptance/technical-evidence.json")
                           .read_text(encoding="utf-8"))
        rows = []
        for name, groups in dev.LEDGERS:
            doc = json.loads((ROOT / name).read_text(encoding="utf-8"))
            rows.extend(row for group in groups for row in doc[group])
        dev.validate_catalog_links(index, rows)
        # Same 396/1652/506 dimensions and valid IDs, different traceability.
        clone = json.loads(json.dumps(index))
        clone["normative_edges"][0]["source_rewrite"] = "not in original"
        self.assertEqual(len(clone["normative_edges"]), 506)
        with self.assertRaisesRegex(ValueError, "original normative link identity changed"):
            dev.validate_catalog_links(clone, rows)
        # A ledger link that no longer agrees with the original also fails.
        bad_rows = json.loads(json.dumps(rows))
        case = next(row for row in bad_rows if row["id"].startswith("ACC-"))
        valid_other = next(name for name in index["contracts"]
                           if name.startswith("CR-") and name not in case["requirement_ids"])
        case["requirement_ids"] = [valid_other]
        with self.assertRaises(ValueError):
            dev.validate_catalog_links(index, bad_rows)

    def test_unchanged_refs_remain_historical_not_current_execution(self):
        refs = [case()]
        a = {"product.py": BLOB_A, "oracle.py": BLOB_A}
        report = dev.summarize_references(refs, a, a, set())
        self.assertEqual(report["counts"]["source_path_affected_records"], 0)
        self.assertEqual(report["counts"]["unchanged_references_historical_only_records"], 1)
        self.assertEqual(report["automatically_current_evidence_records"], 0)
        self.assertEqual(report["automatically_admitted_criteria"], 0)
        self.assertEqual(report["affected_canonical_rows"], [])

    def test_global_pin_change_is_one_review_candidate_not_rerun_approval(self):
        refs = [case(source="SOURCE_LOCK.json")]
        first = {"SOURCE_LOCK.json": BLOB_A, "oracle.py": BLOB_A}
        after = dict(first, **{"SOURCE_LOCK.json": BLOB_B})
        report = dev.summarize_references(refs, first, after, {"SOURCE_LOCK.json"})
        self.assertEqual(report["counts"]["global_dependency_pin_only_record_candidates"], 1)
        self.assertEqual(report["counts"]["source_path_affected_records"], 1)
        self.assertTrue(report["global_dependency_pin_only_cannot_prove_behavioral_change"])
        self.assertTrue(report["impact_counts_are_record_associations_not_test_rerun_instructions"])
        self.assertEqual(report["affected_canonical_rows"], ["CR-TEST-01"])
        self.assertEqual(report["automatically_admitted_criteria"], 0)

    def test_nonpin_oracle_change_widens_direct_reassessment(self):
        refs = [case()]
        first = {"product.py": BLOB_A, "oracle.py": BLOB_A}
        after = {**first, "oracle.py": BLOB_B}
        report = dev.summarize_references(refs, first, after, {"oracle.py"})
        self.assertEqual(report["counts"]["direct_nonpin_code_or_oracle_record_candidates"], 1)
        self.assertEqual(report["counts"]["source_path_affected_criteria"], 1)

    def test_mixed_dependency_and_product_change_distinguished(self):
        refs = [case(source="SOURCE_LOCK.json", oracle="product.py")]
        old = {"SOURCE_LOCK.json": BLOB_A, "product.py": BLOB_A}
        new = {"SOURCE_LOCK.json": BLOB_B, "product.py": BLOB_B}
        r = dev.summarize_references(refs, old, new, set(new))
        self.assertEqual(r["counts"]["dependency_pins_plus_direct_code_or_oracle_records"], 1)

    def test_changed_unmapped_path_does_not_disappear(self):
        refs = [case()]
        src = {"product.py": BLOB_A, "oracle.py": BLOB_A}
        r = dev.summarize_references(refs, src, src, {"unmapped/new_driver.rs"})
        self.assertEqual(r["unmapped_changed_path_count"], 1)
        self.assertEqual(r["unmapped_changed_paths"], ["unmapped/new_driver.rs"])
        self.assertEqual(r["counts"]["source_path_affected_records"], 0)
        self.assertEqual(r["automatically_admitted_criteria"], 0)

    def test_missing_or_symlinked_target_is_not_reusable(self):
        refs = [case()]
        first = {"product.py": BLOB_A, "oracle.py": BLOB_A}
        for new in (
            {"product.py": BLOB_A},  # missing oracle
            {"product.py": BLOB_A, "oracle.py": ("120000", BLOB_A[1])},
        ):
            with self.subTest(new=new):
                r = dev.summarize_references(refs, first, new, set())
                self.assertEqual(r["counts"]["records_with_missing_or_linked_references"], 1)
                self.assertEqual(r["counts"]["source_path_affected_records"], 1)
                self.assertEqual(r["automatically_current_evidence_records"], 0)

    def test_nul_delimited_git_paths_preserve_newlines_and_quoting(self):
        raw = ("ordinary.py\0with space.mjs\0a\nnewline.rs\0"
               "quoted\"name.txt\0unicode-\u00e9.md\0").encode("utf-8")
        result = dev.changed_git_paths(raw)
        self.assertEqual(result,
                         {"ordinary.py", "with space.mjs", "a\nnewline.rs",
                          "quoted\"name.txt", "unicode-\u00e9.md"})
        self.assertEqual(dev.changed_git_paths(b""), set())

    def test_nul_delimited_git_paths_reject_lossy_or_ambiguous_scopes(self):
        samples = [
            b"x.py\n", b"file.py\0\0", b"x.py\0x.py\0",
            b"\xff\0", b"../private\0", b"C:/foreign\0",
            b"/absolute\0", b"bad\\path\0",
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                with self.assertRaises((ValueError, UnicodeDecodeError)):
                    dev.changed_git_paths(raw)

    def test_real_parent_source_delta_uses_unquoted_exact_git_paths(self):
        p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD^"],
                           capture_output=True, text=True, timeout=15, check=True)
        previous = p.stdout.strip()
        output = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(ROOT), "diff",
             "--no-renames", "--name-only", "-z", previous, "HEAD", "--"],
            capture_output=True, timeout=20, check=True).stdout
        decoded = dev.changed_git_paths(output)
        before = dev.original_git_tree(ROOT, previous)
        after = dev.original_git_tree(ROOT, dev.commit_sha(ROOT, "HEAD"))
        expected = {name for name in before.keys() | after.keys()
                    if before.get(name) != after.get(name)}
        self.assertEqual(decoded, expected)

    def test_original_reference_path_traversal_and_windows_drive_rejected(self):
        sample = {"product.py": BLOB_A, "oracle.py": BLOB_A}
        for unsafe in ("../secret.env", "C:/secret.env", "/absolute", "x\\evil",
                       "foo/../bar", "foo//bar"):
            with self.subTest(path=unsafe):
                with self.assertRaises(ValueError):
                    dev.summarize_references([case(source=unsafe)], sample, sample, set())

    def test_malformed_digest_is_not_an_evidence_reference(self):
        b = case()
        b["definitions"][0]["sha256"] = "not-a-source-digest"
        with self.assertRaisesRegex(ValueError, "invalid source digest"):
            dev.summarize_references([b], {}, {}, set())

    def test_duplicate_original_record_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate or invalid"):
            dev.summarize_references([case(), case()], {}, {}, set())

    def test_changed_records_are_source_candidates_not_acceptance(self):
        records = [case("a", "SOURCE_LOCK.json"), case("b", "product.py")]
        old = {"SOURCE_LOCK.json": BLOB_A, "product.py": BLOB_A, "oracle.py": BLOB_A}
        after = dict(old, **{"SOURCE_LOCK.json": BLOB_B})
        r = dev.summarize_references(records, old, after, {"SOURCE_LOCK.json"})
        self.assertEqual(r["counts"]["original_evidence_records"], 2)
        self.assertEqual(r["counts"]["source_path_affected_records"], 1)
        self.assertEqual(r["counts"]["unchanged_references_historical_only_records"], 1)
        self.assertEqual(r["automatically_current_evidence_records"], 0)

    def test_real_preserved_396_catalog_is_read_only_and_source_only(self):
        r = dev.inspect_development(ROOT, "HEAD")
        self.assertEqual(r["schema"], dev.SCHEMA)
        self.assertTrue(r["historical_index_digest_verified"])
        self.assertTrue(r["historical_ledger_lock_git_verified"])
        self.assertEqual(r["catalog_preserved"],
                         {"rows": 396, "criteria": 1652, "normative_edges": 506})
        self.assertEqual(r["impact"]["counts"]["original_evidence_records"], 1936)
        self.assertEqual(r["impact"]["counts"]["distinct_referenced_paths"], 1591)
        self.assertFalse(r["release_authorized"])
        self.assertEqual(r["G3"], "NOT_GRANTED")
        self.assertEqual(r["recorded_or_new_current_PASS"], 0)
        self.assertFalse(r["strict_full_F_index_and_artifact_review_performed"])
        self.assertTrue(r["impact"]["impact_counts_are_record_associations_not_test_rerun_instructions"])

    def test_explicit_parent_baseline_is_dev_delta_not_renewed_evidence(self):
        p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD^"],
                           capture_output=True, text=True, timeout=20, check=True)
        parent = p.stdout.strip()
        r = dev.inspect_development(ROOT, "HEAD", baseline=parent)
        self.assertEqual(r["comparison_baseline_source_sha"], parent)
        self.assertFalse(r["comparison_baseline_is_historical_assessment"])
        self.assertTrue(r["comparison_is_development_delta"])
        before = dev.original_git_tree(ROOT, parent)
        after = dev.original_git_tree(ROOT, dev.commit_sha(ROOT, "HEAD"))
        expected = {name for name in before.keys() | after.keys()
                    if before.get(name) != after.get(name)}
        self.assertEqual(r["source_changed_path_count"], len(expected))
        self.assertEqual(r["changed_source_paths"], sorted(expected)[:100])
        self.assertLessEqual(r["impact"]["unmapped_changed_path_count"],
                             len(expected))
        self.assertEqual(r["recorded_or_new_current_PASS"], 0)
        self.assertFalse(r["release_authorized"])

    def test_explicit_baseline_rejects_rev_expression_and_invalid_sha(self):
        for unsafe in ("HEAD^", "HEAD~1", "deadbeef", "z"*40):
            with self.subTest(baseline=unsafe):
                with self.assertRaises(ValueError):
                    dev.inspect_development(ROOT, "HEAD", baseline=unsafe)

    def test_explicit_baseline_target_equal_is_no_source_delta_and_not_pass(self):
        r = dev.inspect_development(ROOT, "HEAD", baseline="HEAD")
        self.assertEqual(r["source_changed_path_count"], 0)
        self.assertEqual(r["impact"]["counts"]["source_path_affected_records"], 0)
        self.assertEqual(r["impact"]["unmapped_changed_path_count"], 0)
        self.assertFalse(r["release_authorized"])
        self.assertEqual(r["G3"], "NOT_GRANTED")
        self.assertFalse(r["strict_full_F_index_and_artifact_review_performed"])


    def test_dirty_or_untracked_worktree_cannot_be_claimed_as_complete_source(self):
        from unittest.mock import patch
        original = dev.git
        target = dev.commit_sha(ROOT, "HEAD")
        for status in (b" M tooling/native_graph_browser_owner.py\0",
                       b"?? new_native_test.py\0"):
            with self.subTest(status=status):
                def local_git(root, *args):
                    if args[:2] == ("status", "--porcelain=v1"):
                        return status
                    return original(root, *args)
                with patch.object(dev, "git", side_effect=local_git):
                    a = dev.checkout_source_boundary(ROOT, target)
                self.assertFalse(a["worktree_clean_including_untracked"])
                self.assertFalse(a["can_use_as_complete_current_checkout_impact"])
                self.assertTrue(a["uncommitted_or_untracked_changes_require_owner_review"])
                self.assertFalse(a["committed_source_tree_includes_worktree_edits"])
                self.assertEqual(a["decision"],
                                 "COMMITTED_SOURCE_ONLY_NOT_COMPLETE_CURRENT_WORKTREE")

    def test_clean_worktree_commit_is_full_current_checkout_source_boundary(self):
        from unittest.mock import patch
        with patch.object(dev, "git", return_value=b""):
            a = dev.checkout_source_boundary(ROOT, dev.commit_sha(ROOT, "HEAD"))
        self.assertTrue(a["target_is_checkout_head"])
        self.assertTrue(a["worktree_clean_including_untracked"])
        self.assertTrue(a["can_use_as_complete_current_checkout_impact"])
        self.assertFalse(a["committed_source_tree_includes_worktree_edits"])

    def test_historical_target_always_not_full_current_checkout(self):
        from unittest.mock import patch
        target = "a" * 40
        with patch.object(dev, "git", return_value=b""):
            report = dev.checkout_source_boundary(ROOT, target)
        self.assertFalse(report["target_is_checkout_head"])
        self.assertFalse(report["can_use_as_complete_current_checkout_impact"])
        self.assertTrue(report["historical_target_differs_from_checked_out_HEAD"])

    def test_impact_report_exposes_current_checkout_boundary_without_paths(self):
        report = dev.inspect_development(ROOT, "HEAD")
        self.assertEqual(report["source"],
                         report["checkout_source_boundary"]["checkout_head"])
        self.assertTrue(report["analysis_cannot_clear_dirty_current_worktree_for_G1"])
        self.assertFalse(report["checkout_source_boundary"]
                         ["committed_source_tree_includes_worktree_edits"])
        self.assertFalse(report["strict_full_F_index_and_artifact_review_performed"])
        self.assertFalse(report["release_authorized"])
        self.assertNotIn("tooling/conformance_development_impact.py",
                         json.dumps(report["checkout_source_boundary"]))
    def test_unmapped_new_sources_keep_transitive_G1_owner_review_required(self):
        clean = {"can_use_as_complete_current_checkout_impact": True}
        pins = dev.GLOBAL_SOURCE_PINS
        self.assertFalse(dev.requires_transitive_owner_review(set(), pins, 0, clean))
        self.assertFalse(dev.requires_transitive_owner_review({"existing_oracle.py"}, pins, 0, clean))
        self.assertTrue(dev.requires_transitive_owner_review({"brand_new_driver.rs"}, pins, 1, clean))
        self.assertTrue(dev.requires_transitive_owner_review({"Cargo.lock"}, pins, 0, clean))
        dirty = {"can_use_as_complete_current_checkout_impact": False}
        self.assertTrue(dev.requires_transitive_owner_review(set(), pins, 0, dirty))
        for bad_count in (-1, 1.5, True):
            with self.subTest(bad_count=bad_count):
                with self.assertRaises(ValueError):
                    dev.requires_transitive_owner_review(set(), pins, bad_count, clean)

    def test_unmapped_diff_requires_transitive_G1_review(self):
        from unittest.mock import patch
        original_run = subprocess.run
        changed = b"tooling/tests/unmapped_scope_regression_fixture.rs\0"

        def controlled_diff(command, *args, **kwargs):
            if (command[:2] == ["git", "--no-replace-objects"]
                    and command[4:8] ==
                    ["diff", "--no-renames", "--name-only", "-z"]):
                return subprocess.CompletedProcess(
                    command, 0, stdout=changed, stderr=b"")
            return original_run(command, *args, **kwargs)

        with patch.object(dev.subprocess, "run", side_effect=controlled_diff):
            r = dev.inspect_development(ROOT, "HEAD", baseline="HEAD")
        self.assertEqual(r["impact"]["unmapped_changed_path_count"], 1)
        self.assertEqual(r["impact"]["unmapped_changed_paths"],
                         ["tooling/tests/unmapped_scope_regression_fixture.rs"])
        self.assertTrue(r["unmapped_changed_files_cannot_clear_G1"])
        self.assertTrue(r["transitive_impact_unknown_requires_owner_review"])
        self.assertFalse(r["release_authorized"])
        self.assertEqual(r["Graph6_Native_G2_accepted"], 0)

    def test_mapped_nested_manifest_requires_transitive_review(self):
        name = "crates/native/Cargo.toml"
        with development_history(name) as (root, baseline, target):
            r = dev.inspect_development(root, target, baseline=baseline)
        self.assertEqual(r["changed_source_paths"], [name])
        self.assertEqual(r["impact"]["unmapped_changed_path_count"], 0)
        self.assertEqual(r["impact"]["counts"]["source_path_affected_records"], 1)
        self.assertTrue(r["transitive_impact_unknown_requires_owner_review"])
        self.assertEqual(r["dependency_or_environment_paths_changed"], [name])
        self.assertEqual(r["automatic_reruns"], 0)
        self.assertFalse(r["release_authorized"])

    def test_mapped_python_custody_requirements_require_transitive_G1(self):
        # Real pinned Python dependencies can change Native custody/runtime even
        # when historical records reference the requirements file directly.
        name = "tooling/requirements/mlt-g2-custody.txt"
        with development_history(name) as (root, baseline, target):
            report = dev.inspect_development(root, target, baseline=baseline)
        self.assertEqual(report["impact"]["unmapped_changed_path_count"], 0)
        self.assertEqual(report["impact"]["counts"]["source_path_affected_records"], 1)
        self.assertEqual(report["dependency_or_environment_paths_changed"], [name])
        self.assertTrue(report["transitive_impact_unknown_requires_owner_review"])
        self.assertEqual(report["impact"]["automatically_current_evidence_records"], 0)
        self.assertFalse(report["release_authorized"])

    def test_python_requirements_detection_stays_bounded_to_dependency_files(self):
        clean = {"can_use_as_complete_current_checkout_impact": True}
        for name in ("requirements.txt", "tooling/requirements/mlt-g2-custody.txt",
                     "tooling/requirements/constraints.in"):
            with self.subTest(path=name):
                self.assertTrue(dev.requires_transitive_owner_review(
                    {name}, dev.GLOBAL_SOURCE_PINS, 0, clean))
        for name in ("docs/requirements-policy.md", "docs/requirements/README.md",
                     "tooling/requirements/readme.md"):
            with self.subTest(path=name):
                self.assertFalse(dev.requires_transitive_owner_review(
                    {name}, dev.GLOBAL_SOURCE_PINS, 0, clean))

    def test_mapped_dependency_and_environment_paths_keep_G1_review(self):
        clean = {"can_use_as_complete_current_checkout_impact": True}
        for name in (
                "crates/storage/Cargo.toml", "vendor/runtime/Cargo.lock",
                "apps/studio/package.json", "tooling/render/package-lock.json",
                "tooling/probe/rust-toolchain", "tooling/probe/rust-toolchain.toml",
                ".cargo/config", "crates/native/.cargo/config.toml",
                ".github/workflows/studio-service-verification.yml",
                ".github/actions/native/action.yml"):
            with self.subTest(path=name):
                self.assertTrue(dev.requires_transitive_owner_review(
                    {name}, dev.GLOBAL_SOURCE_PINS, 0, clean))
        self.assertFalse(dev.requires_transitive_owner_review(
            {"docs/build-notes.md"}, dev.GLOBAL_SOURCE_PINS, 0, clean))

    def test_cli_cannot_turn_dev_mode_into_strict_acceptance(self):
        p = subprocess.run([sys.executable, "-B",
                            str(ROOT / "tooling/conformance_development_impact.py"),
                            "--target", "HEAD", "--strict"],
                           cwd=ROOT, text=True, capture_output=True, timeout=25)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unrecognized arguments", p.stderr)


if __name__ == "__main__":
    unittest.main()
