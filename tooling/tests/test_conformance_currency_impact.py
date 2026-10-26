"""Contract tests: detect historical source evidence without silently re-awarding PASS."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import conformance_currency_impact as impact


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CurrencyImpactTests(unittest.TestCase):
    def setUp(self):
        self.holder = tempfile.TemporaryDirectory()
        self.addCleanup(self.holder.cleanup)
        self.root = Path(self.holder.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "user.name", "Synthetic source fixture")
        (self.root / "SOURCE_LOCK.json").write_text('{"source":"locked"}\n', encoding="utf-8")
        (self.root / "product.py").write_text("source_value = 1\n", encoding="utf-8")
        (self.root / "oracle.py").write_text("assert source_value == 1\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "-q", "-m", "synthetic one")
        self.first = self.run_git("rev-parse", "HEAD")
        self.snapshot = impact.GitSnapshot(self.root, self.first)
        self.lock = self.snapshot.digest("SOURCE_LOCK.json")
        self.ref = self.snapshot.digest("product.py")
        self.oracle = self.snapshot.digest("oracle.py")
        self.record = {
            "id": "replay-1",
            "source": {"motionwright_sha": self.first, "source_lock_sha256": self.lock,
                       "digest_kind": "git-text-lf"},
            "source_inputs": [{"path": "product.py", "sha256": self.ref}],
            "definitions": [{"path": "oracle.py", "sha256": self.oracle}],
            "criterion_ids": ["CR-DEMO-01#/statement"],
            "complete_criterion_ids": ["CR-DEMO-01#/statement"],
            "execution": {"kind": "integration", "profile": "fixture", "status": "pass"},
        }
        self.field = {"id": "CR-DEMO-01#/statement", "status": "pass", "evidence_ids": ["replay-1"]}
        self.assessment = {"source": copy.deepcopy(self.record["source"])}
        self.observed = {"id": "replay-1", "current_source_relation": "current_execution_candidate"}
        self.review = {"id": "review-1", "criterion_id": self.field["id"], "decision": "sufficient",
                       "source": self.record["source"],
                       "evidence_record_sha256": {"replay-1": "a" * 64}}

    def run_git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args],
                                capture_output=True, text=True, timeout=40)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def current(self, *, field=None, assessment=None, evidence=None, reviews=None, lock=None):
        return impact.classify_criterion(
            field or self.field, assessment or self.assessment,
            self.first, self.lock if lock is None else lock,
            evidence if evidence is not None else {"replay-1": self.observed},
            {"replay-1": self.record},
            reviews if reviews is not None else [self.review], set())


    def _committed_non_text_fixture(self):
        name = "crates/service/tests/fixtures/traversal-project.zip"
        data = (ROOT / name).read_bytes()
        path = self.root / name
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        self.run_git("add", name)
        self.run_git("commit", "-q", "-m", "retained non-text fixture")
        source = self.run_git("rev-parse", "HEAD")
        snapshot = impact.GitSnapshot(self.root, source)
        record = copy.deepcopy(self.record)
        record["source"]["motionwright_sha"] = source
        return name, data, record, snapshot

    def test_non_text_binding_never_reuses_matching_raw_hash_as_current_execution(self):
        name, data, base, snapshot = self._committed_non_text_fixture()
        for relation in ("source_inputs", "definitions"):
            with self.subTest(relation=relation):
                record = copy.deepcopy(base)
                record[relation].append({"path": name, "sha256": digest(data)})
                retained = copy.deepcopy(record)
                result = impact.record_currency(record, snapshot.source, self.lock, snapshot)
                self.assertEqual(result["current_source_relation"], "current_source_invalidated")
                self.assertFalse(result["exact_source_inputs"])
                self.assertEqual(result["changed_references"], [{
                    "path": name, "kind": relation[:-1], "expected_sha256": digest(data),
                    "target_sha256": None, "status": "non_text",
                }])
                self.assertEqual(json.loads(json.dumps(result)), result)
                self.assertEqual(record, retained)
                assessment = {"source": copy.deepcopy(record["source"])}
                review = copy.deepcopy(self.review)
                review["source"] = copy.deepcopy(record["source"])
                self.assertEqual(impact.classify_criterion(
                    self.field, assessment, snapshot.source, self.lock,
                    {record["id"]: result}, {record["id"]: record}, [review], set()),
                    ("pass_requires_reassessment", []))

    def test_unchanged_non_text_fixture_preserves_changed_text_dependencies(self):
        name, data, base, _ = self._committed_non_text_fixture()
        (self.root / "product.py").write_text("source_value = 2\n", encoding="utf-8")
        (self.root / "oracle.py").write_text("assert source_value == 2\n", encoding="utf-8")
        self.run_git("add", "product.py", "oracle.py")
        self.run_git("commit", "-q", "-m", "changed text dependencies")
        target = self.run_git("rev-parse", "HEAD")
        snapshot = impact.GitSnapshot(self.root, target)
        self.assertEqual(impact.git(self.root, "show", target + ":" + name), data)
        for relation in ("source_inputs", "definitions"):
            with self.subTest(relation=relation):
                record = copy.deepcopy(base)
                record[relation].append({"path": name, "sha256": digest(data)})
                result = impact.record_currency(record, target, self.lock, snapshot)
                self.assertEqual(result["current_source_relation"], "historical_changed_or_unknown")
                self.assertFalse(result["exact_source_inputs"])
                self.assertEqual({row["path"]: row["status"] for row in result["changed_references"]},
                                 {"product.py": "changed", "oracle.py": "changed", name: "non_text"})

    def test_non_text_reference_does_not_abort_other_records(self):
        name, data, base, snapshot = self._committed_non_text_fixture()
        records = []
        for index, relation in enumerate(("source_inputs", None, "definitions")):
            record = copy.deepcopy(base)
            record["id"] = "mixed-record-" + str(index)
            if relation is not None:
                record[relation].append({"path": name, "sha256": digest(data)})
            records.append(record)
        results = [impact.record_currency(record, snapshot.source, self.lock, snapshot)
                   for record in records]
        self.assertEqual([row["current_source_relation"] for row in results], [
            "current_source_invalidated", "current_execution_candidate", "current_source_invalidated"])
        self.assertEqual(results[1]["changed_references"], [])
        self.assertTrue(results[1]["exact_source_inputs"])

    def test_non_text_digest_stays_strict_and_unchanged_history_stays_unknown(self):
        name, data, record, snapshot = self._committed_non_text_fixture()
        for _ in range(2):
            with self.assertRaises(UnicodeDecodeError):
                snapshot.digest(name)
        record["source_inputs"].append({"path": name, "sha256": digest(data)})
        (self.root / "unrelated.py").write_text("pass\n", encoding="utf-8")
        self.run_git("add", "unrelated.py")
        self.run_git("commit", "-q", "-m", "unrelated source revision")
        target = self.run_git("rev-parse", "HEAD")
        result = impact.record_currency(record, target, self.lock,
                                        impact.GitSnapshot(self.root, target))
        self.assertEqual(result["current_source_relation"], "historical_changed_or_unknown")
        self.assertEqual([row["status"] for row in result["changed_references"]], ["non_text"])

    def test_exact_git_source_and_sha_digest_match(self):
        self.assertEqual(self.snapshot.digest("product.py"), self.ref)
        self.assertEqual(impact.commit_sha(self.root, "HEAD"), self.first)
        self.assertEqual(impact.commit_sha(self.root, self.first), self.first)

    def test_git_snapshot_uses_committed_file_not_working_copy(self):
        (self.root / "product.py").write_text("source_value = 999\n", encoding="utf-8")
        second = impact.GitSnapshot(self.root, self.first)
        self.assertEqual(second.digest("product.py"), self.ref)

    def test_git_snapshot_rejects_missing_or_linked_source(self):
        self.assertIsNone(self.snapshot.digest("not-found.py"))
        self.snapshot.modes["product.py"] = "120000"  # A Git symlink cannot be a trusted product source.
        self.snapshot.cache.clear()
        self.assertIsNone(self.snapshot.digest("product.py"))


    def test_source_identity_ignores_local_commit_replacements(self):
        (self.root / "product.py").write_text("source_value = 2\n", encoding="utf-8")
        self.run_git("add", "product.py")
        self.run_git("commit", "-q", "-m", "replacement candidate")
        second = self.run_git("rev-parse", "HEAD")
        self.run_git("replace", self.first, second)
        snapshot = impact.GitSnapshot(self.root, self.first)
        self.assertEqual(snapshot.source, self.first)
        self.assertEqual(snapshot.digest("product.py"), self.ref)
        self.assertEqual(impact.record_currency(self.record, self.first, self.lock,
                                               snapshot)["current_source_relation"],
                         "current_execution_candidate")

    def test_source_identity_ignores_replacement_added_after_snapshot(self):
        original = self.run_git("rev-parse", self.first + ":product.py")
        replacement = self.run_git("hash-object", "-w", "oracle.py")
        snapshot = impact.GitSnapshot(self.root, self.first)
        self.run_git("replace", original, replacement)
        self.assertEqual(snapshot.digest("product.py"), self.ref)

    def test_source_identity_ignores_inherited_repository_overrides(self):
        other = self.root / "other-repository"
        other.mkdir()
        subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
        with mock.patch.dict(os.environ, {
            "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
            "GIT_INDEX_FILE": str(other / "index"),
            "GIT_OBJECT_DIRECTORY": str(other / ".git" / "objects"),
        }):
            self.assertEqual(impact.commit_sha(self.root, "HEAD"), self.first)
            self.assertEqual(impact.GitSnapshot(self.root, self.first)
                             .digest("product.py"), self.ref)

    def test_source_identity_ignores_inherited_config_and_replace_namespace(self):
        (self.root / "product.py").write_text("source_value = 2\n", encoding="utf-8")
        self.run_git("add", "product.py")
        self.run_git("commit", "-q", "-m", "alternate replacement candidate")
        second = self.run_git("rev-parse", "HEAD")
        self.run_git("update-ref", "refs/alternate/" + self.first, second)
        with mock.patch.dict(os.environ, {
            "GIT_REPLACE_REF_BASE": "refs/alternate/",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.bare",
            "GIT_CONFIG_VALUE_0": "true",
        }):
            self.assertEqual(impact.GitSnapshot(self.root, self.first)
                             .digest("product.py"), self.ref)

    def test_source_identity_rejects_nested_nonroot_repository(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaises(ValueError):
            impact.GitSnapshot(nested, self.first)

    def test_source_identity_snapshot_requires_exact_commit(self):
        for spec in (self.first[:7], "HEAD~0", "main", self.first.upper()):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                impact.GitSnapshot(self.root, spec)


    def test_malformed_source_path_is_rejected(self):
        for pointer in ("../private", "/etc/passwd", "C:/private/file", "foo\\bar",
                        "a//b", "a/../b", "", "./product.py", "a/./b"):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                impact.source_path(pointer)
        self.assertEqual(impact.source_path("crates/storage/src/lib.rs"), "crates/storage/src/lib.rs")

    def test_commit_spec_disallows_rev_expression_short_sha_and_uppercase(self):
        for text in ("HEAD~1", self.first[:7], "main", self.first.upper(), "z"*40):
            with self.subTest(source=text), self.assertRaises(ValueError):
                impact.commit_sha(self.root, text)

    def test_exact_current_source_positive_is_only_a_candidate(self):
        result = impact.record_currency(self.record, self.first, self.lock, self.snapshot)
        self.assertEqual(result["current_source_relation"], "current_execution_candidate")
        self.assertTrue(result["exact_source_inputs"])
        self.assertEqual(result["changed_references"], [])

    def test_same_bytes_at_new_commit_remains_historical(self):
        (self.root / "unrelated.py").write_text("pass\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "-q", "-m", "another file")
        second = self.run_git("rev-parse", "HEAD")
        result = impact.record_currency(self.record, second, self.lock,
                                        impact.GitSnapshot(self.root, second))
        self.assertEqual(result["current_source_relation"], "historical_same_inputs")

    def test_mutated_source_or_definition_requires_reassessment(self):
        (self.root / "product.py").write_text("source_value = 2\n", encoding="utf-8")
        (self.root / "oracle.py").write_text("assert source_value == 2\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "-q", "-m", "change both product and test")
        second = self.run_git("rev-parse", "HEAD")
        result = impact.record_currency(self.record, second, self.lock,
                                        impact.GitSnapshot(self.root, second))
        self.assertEqual(result["current_source_relation"], "historical_changed_or_unknown")
        self.assertEqual({r["kind"] for r in result["changed_references"]},
                         {"source_input", "definition"})

    def test_locked_source_changed_even_when_input_blobs_same(self):
        (self.root / "SOURCE_LOCK.json").write_text('{"source":"new"}\n', encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "-q", "-m", "new lock")
        second = self.run_git("rev-parse", "HEAD")
        nextsnap = impact.GitSnapshot(self.root, second)
        nextlock = nextsnap.digest("SOURCE_LOCK.json")
        self.assertNotEqual(nextlock, self.lock)
        result = impact.record_currency(self.record, second, nextlock, nextsnap)
        self.assertEqual(result["current_source_relation"], "historical_changed_or_unknown")
        record = copy.deepcopy(self.record)
        record["source"]["motionwright_sha"] = second
        self.assertEqual(impact.record_currency(record, second, nextlock, nextsnap)
                         ["current_source_relation"], "current_source_invalidated")

    def test_incomplete_or_failed_current_execution_does_not_suffice(self):
        for status in ("partial", "fail", "not_run", "inconclusive"):
            with self.subTest(status=status):
                record = copy.deepcopy(self.record)
                record["execution"]["status"] = status
                self.assertEqual(impact.record_currency(record, self.first, self.lock, self.snapshot)
                                 ["current_source_relation"], "current_nonpassing_execution")

    def test_exact_case_requires_criterion_coverage_and_sufficiency_review(self):
        self.assertEqual(self.current(), ("sufficient_current_source", ["review-1"]))
        incomplete = copy.deepcopy(self.record)
        incomplete["complete_criterion_ids"] = []
        self.assertEqual(
            impact.classify_criterion(self.field, self.assessment, self.first, self.lock,
                                      {"replay-1": self.observed}, {"replay-1": incomplete},
                                      [self.review], set())[0], "pass_requires_reassessment")

    def test_no_review_or_historical_review_cannot_close_criterion(self):
        self.assertEqual(self.current(reviews=[])[0], "pass_requires_reassessment")
        old = copy.deepcopy(self.review)
        old["source"]["motionwright_sha"] = "a" * 40
        self.assertEqual(self.current(reviews=[old])[0], "pass_requires_reassessment")

    def test_review_marked_stale_must_not_close_criterion(self):
        self.assertEqual(
            impact.classify_criterion(self.field, self.assessment, self.first, self.lock,
                                      {"replay-1": self.observed}, {"replay-1": self.record},
                                      [self.review], {"review-1"})[0], "pass_requires_reassessment")

    def test_old_record_relabelled_as_a_current_pass_is_rejected(self):
        old = copy.deepcopy(self.observed)
        old["current_source_relation"] = "historical_same_inputs"
        self.assertEqual(self.current(evidence={"replay-1": old})[0], "pass_requires_reassessment")

    def test_same_sha_but_wrong_lock_does_not_pass(self):
        self.assertEqual(self.current(lock="a" * 64)[0], "pass_requires_reassessment")

    def test_partial_and_missing_are_not_promoted(self):
        partial = copy.deepcopy(self.field)
        partial["status"] = "partial"
        self.assertEqual(self.current(field=partial)[0], "current_partial_or_not_admitted")
        absent = copy.deepcopy(partial)
        absent["evidence_ids"] = []
        self.assertEqual(self.current(field=absent)[0], "not_evidenced")

    def test_public_interface_does_not_disclose_assignment_records(self):
        self.assertFalse(hasattr(impact, "original_owner_map"))
        report = impact.inspect(ROOT, "HEAD")
        self.assertNotIn("owner_criterion_counts", report)
        for row in report["row_statuses"]:
            self.assertNotIn("owner", row)
        for followup in report["source_reassessment_followups"]:
            self.assertNotIn("owner", followup)
        self.assertEqual(report["schema"], "motionwright.source-currency-impact/1")

    def test_nonpublic_assignment_flag_is_rejected(self):
        process = subprocess.run([sys.executable, "-B",
                                  str(ROOT / "tooling/conformance_currency_impact.py"),
                                  "--target", "HEAD", "--portfolio", "hidden.json"],
                                 cwd=ROOT, capture_output=True, text=True, timeout=40)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("unrecognized arguments", process.stderr)

    def test_full_repository_inventory_is_not_release_acceptance(self):
        # This one integration test deliberately reads every canonical public row.
        report = impact.inspect(ROOT, "HEAD")
        self.assertEqual(report["counts"]["requirements"], 272)
        self.assertEqual(report["counts"]["acceptance_cases"], 124)
        self.assertEqual(report["counts"]["original_criteria"], 1652)
        self.assertEqual(report["counts"]["normative_links"], 506)
        self.assertEqual(report["automatically_accepted_cases"], [])
        self.assertFalse(report["release_authorized"])
        self.assertEqual(len(report["row_statuses"]), 396)
        self.assertEqual(sum(report["recorded_implementation_counts"].values()), 396)
        self.assertEqual(sum(report["recorded_integration_counts"].values()), 396)
        self.assertEqual(sum(report["recorded_contract_acceptance_counts"].values()), 396)
        self.assertEqual(len(report["source_reassessment_followups"]),
                         len(report["affected_canonical_rows"]))
        counts = [entry["record_count"] for entry in report["changed_source_path_hotspots"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(report["recorded_pass_criteria_requiring_reassessment"],
                         sorted(report["recorded_pass_criteria_requiring_reassessment"]))


class SourceBatchTests(unittest.TestCase):
    """Closed protocol negatives and real Git byte equivalence for batched reads."""

    @staticmethod
    def oid(data):
        return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

    def frames(self, blobs):
        return b"".join((self.oid(data) + " blob " + str(len(data)) + "\n").encode("ascii")
                        + data + b"\n" for data in blobs)

    def snapshot(self, blobs):
        snap = object.__new__(impact.GitSnapshot)
        snap.root, snap.source = Path("synthetic-batch-repository"), "a" * 40
        snap.cache, snap.non_text = {}, {}
        snap.modes = {name: "100644" for name in blobs}
        snap.objects = {name: (self.oid(data), len(data)) for name, data in blobs.items()}
        return snap

    def test_batch_protocol_accepts_exact_empty_text_and_binary_objects(self):
        blobs = [b"", b"source\r\n", b"\xff\x00\xfe"]
        expected = [(self.oid(data), len(data)) for data in blobs]
        self.assertEqual(impact.source_batch_blobs(self.frames(blobs), expected),
                         {self.oid(data): data for data in blobs})

    def test_batch_protocol_rejects_wrong_identity_kind_size_and_content(self):
        data = b"abc\n"
        wire = self.frames([data])
        oid = self.oid(data)
        for mutated in (wire.replace(oid.encode(), b"0" * 40),
                        wire.replace(b" blob ", b" tree "),
                        wire.replace(b" blob 4\n", b" blob 3\n"),
                        wire.replace(b"abc\n", b"abd\n"), wire[:-1] + b"x"):
            with self.subTest(wire=mutated), self.assertRaises(ValueError):
                impact.source_batch_blobs(mutated, [(oid, len(data))])

    def test_batch_protocol_rejects_truncation_extra_reordered_and_duplicate_frames(self):
        blobs = [b"first", b"second"]
        wire = self.frames(blobs)
        expected = [(self.oid(data), len(data)) for data in blobs]
        for mutated in (b"", wire[:8], wire[:-1], wire[:-3], wire + b"\n",
                        wire + self.frames([b"extra"]), self.frames(list(reversed(blobs))),
                        self.frames([blobs[0], blobs[0]])):
            with self.subTest(length=len(mutated)), self.assertRaises(ValueError):
                impact.source_batch_blobs(mutated, expected)
        with self.assertRaises(ValueError):
            impact.source_batch_blobs(self.frames([blobs[0], blobs[0]]), [expected[0], expected[0]])

    def test_batch_prefetch_rejects_unsafe_paths_before_any_git_lookup(self):
        snap = self.snapshot({"good.py": b"pass\n"})
        for name in ("../private", "/absolute", "bad\\name", "a:b", "a//b", "./good.py", ""):
            with self.subTest(name=name), mock.patch.object(impact, "git") as lookup:
                with self.assertRaises(ValueError):
                    snap.prefetch(["good.py", name])
                lookup.assert_not_called()
                self.assertEqual(snap.cache, {})

    def test_batch_bounds_fail_before_process_and_never_drop_a_reference(self):
        for constant, maximum in (("MAX_SOURCE_OBJECTS", 1), ("MAX_SOURCE_BLOB_BYTES", 2),
                                  ("MAX_SOURCE_TOTAL_BYTES", 5)):
            snap = self.snapshot({"a": b"one", "b": b"two"})
            with self.subTest(constant=constant), mock.patch.object(impact, constant, maximum), \
                    mock.patch.object(impact, "git") as lookup:
                with self.assertRaises(ValueError):
                    snap.prefetch(["a", "b"])
                lookup.assert_not_called()
                self.assertEqual(snap.cache, {})

    def test_batch_prefetch_deduplicates_objects_chunks_and_uses_only_object_ids(self):
        blobs = {"a.py": b"abc", "odd\nname.py": b"abc", "z.py": b"def"}
        snap = self.snapshot(blobs)
        by_oid = {self.oid(data): data for data in blobs.values()}
        seen = []
        def batch(root, *args, input_data=None):
            self.assertEqual(root, snap.root)
            self.assertEqual(args, ("cat-file", "--batch"))
            requested = input_data.decode("ascii").splitlines()
            self.assertTrue(all(impact.SHA.fullmatch(oid) for oid in requested))
            seen.extend(requested)
            return self.frames([by_oid[oid] for oid in requested])
        with mock.patch.object(impact, "SOURCE_BATCH_BYTES", 3), \
                mock.patch.object(impact, "git", side_effect=batch) as lookup:
            snap.prefetch(list(blobs) + ["a.py"])
            self.assertEqual(lookup.call_count, 2)
            self.assertEqual(len(seen), len(set(seen)))
            self.assertEqual(set(seen), set(by_oid))
            for name, data in blobs.items():
                self.assertEqual(snap.digest(name), digest(data))
            snap.prefetch(list(blobs))
            self.assertEqual(lookup.call_count, 2)

    def test_batch_prefetch_preserves_missing_and_symlink_rejection(self):
        snap = self.snapshot({"link": b"target"})
        snap.modes["link"] = "120000"
        with mock.patch.object(impact, "git") as lookup:
            snap.prefetch(["link", "missing"])
            self.assertIsNone(snap.digest("link"))
            self.assertIsNone(snap.digest("missing"))
            lookup.assert_not_called()

    def test_batch_non_text_remains_invalid_without_aborting_other_records(self):
        blobs = {"good.py": b"pass\r\n", "binary.zip": b"\xff\x00\xfe"}
        snap = self.snapshot(blobs)
        with mock.patch.object(impact, "git", return_value=self.frames([blobs[n] for n in sorted(blobs)])):
            snap.prefetch(list(blobs))
        self.assertEqual(snap.digest("good.py"), digest(b"pass\n"))
        for _ in range(2):
            with self.assertRaises(UnicodeDecodeError):
                snap.digest("binary.zip")
        record = {"id": "binary-must-not-pass", "source": {"motionwright_sha": snap.source,
                  "source_lock_sha256": "c" * 64}, "source_inputs": [
                  {"path": "binary.zip", "sha256": digest(blobs["binary.zip"])}],
                  "definitions": [{"path": "good.py", "sha256": digest(b"pass\n")}],
                  "criterion_ids": [], "complete_criterion_ids": [],
                  "execution": {"kind": "unit", "profile": "synthetic", "status": "pass"}}
        observed = impact.record_currency(record, snap.source, "c" * 64, snap)
        self.assertEqual(observed["current_source_relation"], "current_source_invalidated")
        self.assertEqual([item["status"] for item in observed["changed_references"]], ["non_text"])

    def test_batch_late_corruption_keeps_preexisting_cache_and_admits_no_prefix(self):
        snap = self.snapshot({"a": b"abc", "b": b"def"})
        snap.cache["existing"] = "c" * 64
        with mock.patch.object(impact, "SOURCE_BATCH_BYTES", 3), \
                mock.patch.object(impact, "git", side_effect=[self.frames([b"abc"]), b"broken"]):
            with self.assertRaises(ValueError):
                snap.prefetch(["a", "b"])
        self.assertEqual(snap.cache, {"existing": "c" * 64})
        self.assertEqual(snap.non_text, {})

    def test_batch_git_wrapper_keeps_environment_and_replacement_guards(self):
        response = subprocess.CompletedProcess([], 0, b"closed-result", b"")
        with mock.patch.dict(os.environ, {"GIT_DIR": "foreign", "GIT_CONFIG_COUNT": "1",
                                        "GIT_REPLACE_REF_BASE": "refs/other/"}), \
                mock.patch.object(impact.subprocess, "run", return_value=response) as call:
            self.assertEqual(impact.git(Path("source"), "cat-file", "--batch", input_data=b"a" * 40 + b"\n"),
                             b"closed-result")
        args, kwargs = call.call_args
        self.assertEqual(args[0], ["git", "--no-replace-objects", "-C", "source", "cat-file", "--batch"])
        for forbidden in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_REPLACE_REF_BASE"):
            self.assertNotIn(forbidden, kwargs["env"])
        self.assertEqual(kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(kwargs["timeout"], 80)
        self.assertEqual(kwargs["input"], b"a" * 40 + b"\n")

    def repository(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        def command(*args):
            completed = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=40)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout.decode("utf-8").strip()
        command("init", "-q")
        command("config", "user.email", "batch-fixture@example.invalid")
        command("config", "user.name", "Synthetic batch source fixture")
        command("config", "core.autocrlf", "false")
        return root, command

    def test_batch_real_git_matches_single_reads_ignores_worktree_and_preserves_non_text(self):
        root, command = self.repository()
        blobs = {"a.py": b"source = 1\r\n", "empty": b"", "oracle.py": b"assert source == 1\n",
                 "binary": b"\xff\x00\xfe"}
        for name, data in blobs.items():
            (root / name).write_bytes(data)
        command("add", ".")
        command("commit", "-q", "-m", "exact batch fixture")
        source = command("rev-parse", "HEAD")
        old = impact.GitSnapshot(root, source)
        expected = {name: old.digest(name) for name in blobs if name != "binary"}
        for name in blobs:
            (root / name).write_bytes(b"uncommitted replacement")
        current = impact.GitSnapshot(root, source)
        current.prefetch(list(blobs))
        self.assertEqual({name: current.digest(name) for name in expected}, expected)
        self.assertEqual(current.digest("a.py"), digest(b"source = 1\n"))
        with self.assertRaises(UnicodeDecodeError):
            current.digest("binary")

    def test_batch_real_git_ignores_object_replacement_and_inherited_overrides(self):
        root, command = self.repository()
        (root / "source.py").write_bytes(b"original\n")
        command("add", ".")
        command("commit", "-q", "-m", "original object")
        source = command("rev-parse", "HEAD")
        original = command("rev-parse", source + ":source.py")
        (root / "replacement.py").write_bytes(b"substituted\n")
        replacement = command("hash-object", "-w", "replacement.py")
        command("replace", original, replacement)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(root / "foreign"), "GIT_CONFIG_COUNT": "1",
                                        "GIT_CONFIG_KEY_0": "core.bare", "GIT_CONFIG_VALUE_0": "true"}):
            snap = impact.GitSnapshot(root, source)
            snap.prefetch(["source.py"])
            self.assertEqual(snap.digest("source.py"), digest(b"original\n"))

    def test_batch_same_inputs_on_new_commit_remain_historical(self):
        snap = self.snapshot({"source.py": b"original\n"})
        with mock.patch.object(impact, "git", return_value=self.frames([b"original\n"])):
            snap.prefetch(["source.py"])
        record = {"id": "old", "source": {"motionwright_sha": "b" * 40,
                  "source_lock_sha256": "c" * 64}, "source_inputs": [
                  {"path": "source.py", "sha256": digest(b"original\n")}], "definitions": [],
                  "criterion_ids": [], "complete_criterion_ids": [],
                  "execution": {"kind": "unit", "profile": "synthetic", "status": "pass"}}
        result = impact.record_currency(record, snap.source, "c" * 64, snap)
        self.assertEqual(result["current_source_relation"], "historical_same_inputs")



if __name__ == "__main__":
    unittest.main()
