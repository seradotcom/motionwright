#!/usr/bin/env python3
"""Adversarial source-currency proof; public ledgers only, no private catalogue."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from conformance_history_guard import (
    INDEX, LEDGERS, HistoryViolation, expand_assessment_history,
    inspect, parse_unique, source_limit,
)
from conformance_policy import source_digest
import conformance_source_rollforward as rollforward_module
from conformance_source_rollforward import (
    assert_source, digest, git, json_bytes, rollforward, verify_frozen_inputs,
)

OLD = parse_unique((ROOT / INDEX).read_bytes(), INDEX)["source"]["motionwright_sha"]
TARGET = "c" * 40 if OLD != "c" * 40 else "d" * 40
STAMP = "2026-10-25T00:00:00+00:00"


def all_rows(docs):
    for name in LEDGERS:
        for key in ("requirements", "cases", "expansion_cases"):
            yield from docs[name].get(key, [])


class SourceCurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originals = {
            name: (ROOT / name).read_bytes() for name in (*LEDGERS, INDEX)
        }
        cls.previous = {
            name: parse_unique(raw, name) for name, raw in cls.originals.items()
        }
        assert cls.previous[INDEX]["source"]["motionwright_sha"] == OLD
        cls.posts, cls.report = rollforward(
            cls.previous, TARGET, source_digest(ROOT / "SOURCE_LOCK.json"),
            original_inputs=cls.originals,
            integrated=False, at_utc=STAMP, predecessor=OLD,
        )
        cls.after = {name: parse_unique(content, name)
                     for name, content in cls.posts.items()}

    def test_every_canonical_field_is_present_and_not_approved(self):
        expected_counts = {}
        for row in all_rows(self.previous):
            for field in row["technical_assessment"]["criteria"]:
                status = "not_run" if field["status"] == "not_run" else "stale"
                expected_counts[status] = expected_counts.get(status, 0) + 1
        self.assertEqual(self.report["criterion_counts"], expected_counts)
        for name in LEDGERS:
            self.assertEqual(self.after[name]["current_assessment_source_semantics"],
                             "Exact branch snapshot assessed here; main integration is not claimed. "
                             "Later integration requires its own currency review; "
                             "no result is transferred to another source.")
        rows = list(all_rows(self.after))
        self.assertEqual(len(rows), 396)
        self.assertEqual(sum(len(r["technical_assessment"]["criteria"])
                             for r in rows), 1652)
        for row in rows:
            self.assertEqual(row["current_contract_verdict"], "open")
            self.assertEqual(row["current_visible_verdict_source"], TARGET)
            self.assertNotEqual(row.get("status"), "PASS")
            self.assertNotEqual(row.get("complete_requirement_acceptance"), "COMPLETED")
            self.assertFalse(any(c["status"] == "pass" for c in row["technical_assessment"]["criteria"]))

    def test_all_original_evidence_and_reviews_byte_reversible(self):
        original = self.previous[INDEX]
        current = self.after[INDEX]
        for key in ("records", "sufficiency_reviews", "criteria", "contracts",
                    "normative_edges", "human_reviews", "criterion_aliases"):
            self.assertEqual(json_bytes(original[key]), json_bytes(current[key]), key)
        self.assertGreater(len(original["records"]), 0)
        self.assertGreater(len(original["sufficiency_reviews"]), 0)
        self.assertEqual(len(original["records"]), len(current["records"]))
        self.assertEqual(len(original["sufficiency_reviews"]), len(current["sufficiency_reviews"]))
        self.assertEqual(len(original["normative_edges"]), 506)

    def test_real_history_snapshot_and_old_visible_fields_recovered(self):
        previous = {r["id"]: r for r in all_rows(self.previous)}
        current = {r["id"]: r for r in all_rows(self.after)}
        self.assertEqual(previous.keys(), current.keys())
        for rid, old in previous.items():
            after = current[rid]
            expanded = expand_assessment_history(after["assessment_history"])
            earlier = expand_assessment_history(old["assessment_history"])
            self.assertEqual(expanded[:-1], earlier)
            tail = expanded[-1]
            self.assertEqual(tail["source"], old["technical_assessment"]["source"])
            self.assertEqual(tail["prior_technical_assessment"], old["technical_assessment"])
            self.assertEqual(tail["prior_criteria"], old["technical_assessment"]["criteria"])
            for key, value in tail["prior_visible_fields"].items():
                self.assertEqual(value, old[key])

    def test_rollforward_keeps_original_H03_archive_byte_exact_and_caps_tail(self):
        prior = {row["id"]: row for row in all_rows(self.previous)}
        current = {row["id"]: row for row in all_rows(self.after)}
        for key, old in prior.items():
            old_history = old["assessment_history"]
            new_history = current[key]["assessment_history"]
            self.assertEqual(len(new_history), 2, key)
            self.assertEqual(new_history[0], old_history[0], key)
            self.assertEqual(new_history[1]["schema"],
                             "motionwright.assessment-history-zlib-json/1", key)
            self.assertLessEqual(new_history[1]["plain_bytes"], 1_048_576)
            self.assertEqual(expand_assessment_history(new_history)[:-1],
                             expand_assessment_history(old_history), key)

    def test_zero_relabelled_evidence_records_and_completeness(self):
        original = {r["id"]: r for r in all_rows(self.previous)}
        newer = {r["id"]: r for r in all_rows(self.after)}
        for rid, current in newer.items():
            prev = original[rid]["technical_assessment"]
            technical = current["technical_assessment"]
            self.assertEqual(technical["integration"]["status"], "branch")
            self.assertEqual(technical["evidence_currency"]["current_record_ids"], [])
            self.assertTrue(set(prev["evidence_currency"]["current_record_ids"]).issubset(
                technical["evidence_currency"]["historical_record_ids"]))
            self.assertEqual(technical["technical_verification"]["evidence_ids"], [])
            self.assertEqual(technical["integration"]["source_sha"], TARGET)
            for before, after in zip(prev["criteria"], technical["criteria"]):
                self.assertEqual(before["id"], after["id"])
                self.assertEqual(before["source_clause_sha256"], after["source_clause_sha256"])
                self.assertEqual(after["evidence_ids"], [])
                self.assertTrue(after["missing_assertions"])
                self.assertTrue(set(before["evidence_ids"]).issubset(
                    after["evidence_currency"]["historical_record_ids"]))

    def test_bounds_and_separate_policy_hashes(self):
        for name, blob in self.posts.items():
            self.assertLessEqual(len(blob), source_limit(name))
            self.assertEqual(self.report["postimage_digests"][name],
                             {"bytes": len(blob), "sha256": digest(blob)})
        index = self.posts[INDEX]
        for name in LEDGERS:
            self.assertEqual(self.after[name]["technical_evidence_index"]["sha256"],
                             digest(index))

    def test_history_gate_accepts_a_lossless_source_transition(self):
        report = inspect(self.previous, self.after)
        self.assertEqual(report["ledger_rows_verified"], 396)
        self.assertEqual(report["changed_assessments_with_history"], 396)

    def test_tamper_with_source_clause_is_detected(self):
        broken = copy.deepcopy(self.after)
        first = next(all_rows(broken))
        first["technical_assessment"]["criteria"][0]["source_clause_sha256"] = "0" * 64
        with self.assertRaises(HistoryViolation):
            inspect(self.previous, broken)

    def test_deleting_an_original_archive_entry_is_rejected(self):
        broken = copy.deepcopy(self.after)
        first = next(all_rows(broken))
        history = expand_assessment_history(first["assessment_history"])
        self.assertGreater(len(history), 1)
        first["assessment_history"] = [history[-1]]
        with self.assertRaises(HistoryViolation):
            inspect(self.previous, broken)

    def test_missing_normative_trace_is_rejected(self):
        broken = copy.deepcopy(self.after)
        broken[INDEX]["normative_edges"].pop()
        with self.assertRaises(HistoryViolation):
            inspect(self.previous, broken)

    def test_refusing_identical_source_cannot_create_a_new_execution(self):
        with self.assertRaises(ValueError):
            rollforward(self.previous, OLD, source_digest(ROOT / "SOURCE_LOCK.json"),
                        original_inputs=self.originals,
                        integrated=False, at_utc=STAMP, predecessor=OLD)

    def test_exact_git_snapshot_and_integration_provenance_required(self):
        self.assertEqual(len(git(ROOT, "rev-parse", "HEAD")), 40)
        with self.assertRaises(ValueError):
            assert_source(ROOT, TARGET, integrated=False)
        # A clean matching remote may legitimately be integrated; do not assert
        # a failure whose truth depends on live branch movement.

    def test_live_remote_mismatch_is_rejected_deterministically(self):
        with patch("conformance_source_rollforward._assert_git_identity"), patch(
                "conformance_source_rollforward.git", side_effect=[
            TARGET, "", "f" * 40 + " refs/heads/main"
        ]):
            with self.assertRaises(ValueError):
                assert_source(ROOT, TARGET, integrated=True)

    def test_matching_live_remote_can_be_admitted_deterministically(self):
        with patch("conformance_source_rollforward._assert_git_identity"), patch(
                "conformance_source_rollforward.git", side_effect=[
            TARGET, "", TARGET + " refs/heads/main"
        ]):
            assert_source(ROOT, TARGET, integrated=True)
        posts, _ = rollforward(self.previous, TARGET, source_digest(ROOT / "SOURCE_LOCK.json"),
                               original_inputs=self.originals, integrated=True,
                               at_utc=STAMP, predecessor=OLD)
        after = {name: parse_unique(raw, name) for name, raw in posts.items()}
        for name in LEDGERS:
            self.assertEqual(after[name]["current_assessment_source_semantics"],
                             "Exact integrated snapshot assessed here. "
                             "Later integration requires its own currency review; "
                             "no result is transferred to another source.")
        for row in all_rows(after):
            self.assertEqual(row["technical_assessment"]["integration"]["status"], "main")

    def test_fail_is_historical_only(self):
        old = copy.deepcopy(self.previous)
        old["docs/acceptance/acceptance-status.json"]["cases"][0]["status"] = "FAIL"
        posts, _ = rollforward(old, TARGET, source_digest(ROOT / "SOURCE_LOCK.json"),
                               original_inputs={name: json_bytes(value)
                                                for name, value in old.items()},
                               integrated=False, at_utc=STAMP, predecessor=OLD)
        case = parse_unique(posts["docs/acceptance/acceptance-status.json"],
                            "docs/acceptance/acceptance-status.json")["cases"][0]
        self.assertEqual(case["status"], "NOT_RUN")

    def test_exact_lock_and_ledger_snapshot_guard(self):
        originals = {name: b"original" for name in (*LEDGERS, INDEX)}
        observed = {"SOURCE_LOCK.json": b"lock", **originals}
        with patch("conformance_source_rollforward.assert_source"), patch(
                "conformance_source_rollforward.git", return_value="tree"), patch(
                "conformance_source_rollforward.capture_source_inputs",
                side_effect=lambda *_: dict(observed)):
            verify_frozen_inputs(Path("fixture"), TARGET, False, "tree", b"lock", originals)
            observed["SOURCE_LOCK.json"] = b"changed"
            with self.assertRaisesRegex(ValueError, "source lock changed"):
                verify_frozen_inputs(Path("fixture"), TARGET, False, "tree", b"lock", originals)
            observed["SOURCE_LOCK.json"] = b"lock"
            observed[INDEX] = b"changed"
            with self.assertRaisesRegex(ValueError, "ledger source changed"):
                verify_frozen_inputs(Path("fixture"), TARGET, False, "tree", b"lock", originals)

    def test_tree_guard_rejects_different_tree(self):
        with patch("conformance_source_rollforward.assert_source"), patch(
                "conformance_source_rollforward.git", return_value="wrong"):
            with self.assertRaises(ValueError):
                verify_frozen_inputs(Path("fixture"), TARGET, False, "tree", b"x", {})

    def test_no_source_input_files_were_written_by_rollforward(self):
        for name, original_bytes in self.originals.items():
            self.assertEqual((ROOT / name).read_bytes(), original_bytes)


def fixture_git(root, *args):
    """Mutate only a fresh test repository, independent of inherited Git settings."""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_DATE": "2001-01-01T00:00:00+00:00",
                "GIT_COMMITTER_DATE": "2001-01-01T00:00:00+00:00"})
    result = subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(root), *args],
        env=env, capture_output=True, timeout=30, check=True)
    return result.stdout.decode("utf-8").strip()


class SourceAuthenticationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="a5-rf-fixture-")
        self.addCleanup(self.workspace.cleanup)
        self.folder = Path(self.workspace.name)
        self.root, self.originals, self.head, self.tree = self.make_repo("first", "one")

    def make_repo(self, name, label):
        root = self.folder / name
        root.mkdir()
        fixture_git(root, "init", "--quiet")
        fixture_git(root, "config", "core.autocrlf", "false")
        raws = {path: json.dumps({"fixture": label, "path": path}, indent=1).encode() + b"\n"
                for path in ("SOURCE_LOCK.json", *LEDGERS, INDEX)}
        for path, raw in raws.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        fixture_git(root, "add", "--all")
        fixture_git(root, "commit", "--quiet", "-m", "synthetic source fixture")
        return (root, raws, fixture_git(root, "rev-parse", "HEAD"),
                fixture_git(root, "rev-parse", "HEAD^{tree}"))

    def test_exact_clean_repo_and_five_input_roster_are_admitted(self):
        assert_source(self.root, self.head, integrated=False)
        captured = rollforward_module.capture_source_inputs(self.root, self.head)
        self.assertEqual(captured, self.originals)
        verify_frozen_inputs(self.root, self.head, False, self.tree,
                             captured["SOURCE_LOCK.json"],
                             {p: captured[p] for p in (*LEDGERS, INDEX)})

    def test_git_environment_cannot_substitute_another_repository(self):
        other, _, head, _ = self.make_repo("second", "different")
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git"),
                                     "GIT_WORK_TREE": str(other)}):
            with self.assertRaises(ValueError):
                assert_source(self.root, head, integrated=False)

    def test_git_environment_channels_do_not_redirect_the_valid_source(self):
        wrong = str(self.folder / "nonexistent")
        overrides = {
            "GIT_DIR": wrong, "GIT_WORK_TREE": wrong, "GIT_COMMON_DIR": wrong,
            "GIT_INDEX_FILE": wrong, "GIT_OBJECT_DIRECTORY": wrong,
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": wrong, "GIT_SHALLOW_FILE": wrong,
            "GIT_CONFIG_SYSTEM": wrong, "GIT_CONFIG_GLOBAL": wrong,
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": wrong, "GIT_CONFIG_PARAMETERS": "invalid fixture",
        }
        with patch.dict(os.environ, overrides):
            assert_source(self.root, self.head, integrated=False)
            self.assertEqual(rollforward_module.capture_source_inputs(self.root, self.head),
                             self.originals)

    def test_subdirectory_cannot_stand_in_for_worktree_root(self):
        with self.assertRaises(ValueError):
            assert_source(self.root / "docs", self.head, integrated=False)

    def test_skip_worktree_cannot_hide_a_changed_ledger(self):
        fixture_git(self.root, "update-index", "--skip-worktree", INDEX)
        changed = self.originals[INDEX].replace(b'"one"', b'"two"')
        (self.root / INDEX).write_bytes(changed)
        self.assertEqual(fixture_git(self.root, "status", "--porcelain"), "")
        raw = {p: (self.root / p).read_bytes() for p in (*LEDGERS, INDEX)}
        with self.assertRaises(ValueError):
            verify_frozen_inputs(self.root, self.head, False, self.tree,
                                 self.originals["SOURCE_LOCK.json"], raw)

    def test_assume_unchanged_cannot_hide_a_changed_lock(self):
        fixture_git(self.root, "update-index", "--assume-unchanged", "SOURCE_LOCK.json")
        changed = self.originals["SOURCE_LOCK.json"].replace(b'"one"', b'"two"')
        (self.root / "SOURCE_LOCK.json").write_bytes(changed)
        self.assertEqual(fixture_git(self.root, "status", "--porcelain"), "")
        with self.assertRaises(ValueError):
            verify_frozen_inputs(self.root, self.head, False, self.tree, changed,
                                 {p: self.originals[p] for p in (*LEDGERS, INDEX)})

    def test_linked_worktree_remains_a_valid_root(self):
        linked = self.folder / "linked"
        fixture_git(self.root, "worktree", "add", "--quiet", "--detach", str(linked), self.head)
        assert_source(linked, self.head, integrated=False)
        self.assertEqual(rollforward_module.capture_source_inputs(linked, self.head),
                         self.originals)

    def test_git_symlink_mode_is_rejected_before_checkout_read(self):
        blob = fixture_git(self.root, "rev-parse", self.head + ":" + INDEX)
        fixture_git(self.root, "update-index", "--cacheinfo", "120000," + blob + "," + INDEX)
        fixture_git(self.root, "commit", "--quiet", "-m", "synthetic link mode")
        head = fixture_git(self.root, "rev-parse", "HEAD")
        with patch("conformance_source_rollforward._read_checkout_input") as reader:
            with self.assertRaises(ValueError):
                rollforward_module.capture_source_inputs(self.root, head)
            reader.assert_not_called()

    def test_raw_objects_require_type_size_and_authentic_bytes(self):
        with self.assertRaises(ValueError):
            rollforward_module._git_object(self.root, self.head, "blob", 1024)
        blob = fixture_git(self.root, "rev-parse", self.head + ":" + INDEX)
        with self.assertRaises(ValueError):
            rollforward_module._git_object(self.root, blob, "blob", 1)
        original_transport = rollforward_module._unredirected_git
        def corrupt(root, *args):
            raw = original_transport(root, *args)
            if args == ("cat-file", "blob", blob):
                return b"!" + raw[1:]
            return raw
        with patch("conformance_source_rollforward._unredirected_git", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "bytes do not match"):
                rollforward_module._git_object(self.root, blob, "blob", 1024)

    def test_commit_and_tree_bytes_are_authenticated(self):
        original_transport = rollforward_module._unredirected_git
        for kind, identity in (("commit", self.head), ("tree", self.tree)):
            with self.subTest(kind=kind):
                def corrupt(root, *args):
                    raw = original_transport(root, *args)
                    return b"!" + raw[1:] if args == ("cat-file", kind, identity) else raw
                with patch("conformance_source_rollforward._unredirected_git", side_effect=corrupt):
                    with self.assertRaisesRegex(ValueError, "bytes do not match"):
                        rollforward_module.capture_source_inputs(self.root, self.head)

    def test_only_git_text_crlf_normalization_is_allowed_and_raw_is_retained(self):
        for path, raw in self.originals.items():
            (self.root / path).write_bytes(raw.replace(b"\n", b"\r\n"))
        captured = rollforward_module.capture_source_inputs(self.root, self.head)
        self.assertEqual(captured[INDEX], self.originals[INDEX].replace(b"\n", b"\r\n"))
        (self.root / INDEX).write_bytes(captured[INDEX].replace(b'"fixture": ', b'"fixture":  '))
        with self.assertRaisesRegex(ValueError, "differs from the exact Git blob"):
            rollforward_module.capture_source_inputs(self.root, self.head)


class OriginalBytesFixtureTests(unittest.TestCase):
    def test_raw_roster_and_typed_value_binding_are_required(self):
        previous = {p: {"flag": True, "count": 1} for p in (*LEDGERS, INDEX)}
        raws = {p: json_bytes(value) for p, value in previous.items()}
        self.assertEqual(rollforward_module._bound_originals(previous, raws), raws)
        for missing in (*LEDGERS, INDEX):
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    rollforward_module._bound_originals(previous,
                                                       {p: b for p, b in raws.items() if p != missing})
        with self.assertRaises(ValueError):
            rollforward_module._bound_originals(previous, dict(raws, unexpected=b"{}"))
        for value in (1, 1.0):
            with self.subTest(value=value):
                changed = copy.deepcopy(previous)
                changed[INDEX]["flag"] = value
                with self.assertRaises(ValueError):
                    rollforward_module._bound_originals(changed, raws)
        with self.assertRaises(ValueError):
            rollforward_module._bound_originals(previous, dict(raws, **{INDEX: b'{"flag":true,"flag":true}'}))

    def test_source_hashes_use_original_json_bytes_not_reserialization(self):
        originals = {p: (ROOT / p).read_bytes() for p in (*LEDGERS, INDEX)}
        previous = {p: parse_unique(raw, p) for p, raw in originals.items()}
        # Different order, indentation and CRLF are an explicit synthetic raw
        # input, not a new on-disk assessment or an assertion of Git currency.
        raw_index = (json.dumps(previous[INDEX], sort_keys=False, ensure_ascii=False,
                                indent=1) + "\n").replace("\n", "\r\n").encode()
        originals[INDEX] = raw_index
        self.assertNotEqual(raw_index, json_bytes(previous[INDEX]))
        posts, report = rollforward(previous, TARGET, source_digest(ROOT / "SOURCE_LOCK.json"),
                                   original_inputs=originals, integrated=False,
                                   at_utc=STAMP, predecessor=OLD)
        self.assertEqual(report["previous_source_index_sha256"], digest(raw_index))
        emitted = parse_unique(posts[INDEX], INDEX)
        self.assertEqual(emitted["assessment_history"][-1]["original_index_sha256"],
                         digest(raw_index))
        for path, raw in originals.items():
            if path != INDEX:
                self.assertEqual((ROOT / path).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
