"""Source-only adversarial tests of dynamic technical completeness classification.

All positive fixtures below are explicitly synthetic *in-memory* statements;
they are NOT real signed tests, native/installed proof or human acceptance.
The actual production CLI independently validates physical artifact bytes,
source identity and per-field sufficiency before calling the pure classifier.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import conformance_technical_readiness as gate  # noqa: E402


class DynamicTechnicalReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_index = json.loads(
            (ROOT / "docs/acceptance/technical-evidence.json").read_bytes())
        cls.original_rows = []
        for name, keys in gate.LEDGERS.items():
            doc = json.loads((ROOT / name).read_bytes())
            for key in keys:
                cls.original_rows.extend(doc.get(key, []))
        assert len(cls.original_rows) == 396
        assert len(cls.original_index["criteria"]) == 1652

    def setUp(self):
        self.index = self.original_index
        self.rows = copy.deepcopy(self.original_rows)
        self.checkout = {
            "sha": self.index["source"]["motionwright_sha"],
            "tree": "f" * 40,
            "ledger_package_sha": "c" * 40,
            "remote_main_sha": "c" * 40,
            "source_lock_sha256": self.index["source"]["source_lock_sha256"],
        }

    def report(self):
        return gate.derive_eligibility(self.index, self.rows, self.checkout)

    def fake_all_required_complete_in_memory(self):
        # This intentionally does NOT attach evidence; inspect() would reject
        # a falsified real ledger because conformance_policy independently
        # checks actual source-bound artifacts and sufficiency reviews.
        for row in self.rows:
            tier = self.index["contracts"][row["id"]]["scope"]
            if tier != "required":
                continue
            assessed = row["technical_assessment"]
            assessed["implementation"]["status"] = "implemented"
            assessed["integration"]["status"] = "main"
            assessed["technical_verification"]["status"] = "pass"
            assessed["contract_acceptance"]["pending_criteria"] = []
            for field in assessed["criteria"]:
                field["status"] = "pass"

    def test_actual_historical_source_remains_blocked(self):
        # Explicit synthetic historical gap: future completion of the real
        # repository must not make this negative fixture require a regression.
        row = next(row for row in self.rows
                   if self.index["contracts"][row["id"]]["scope"] == "required")
        row["technical_assessment"]["criteria"][0]["status"] = "stale"
        report = self.report()
        self.assertEqual(
            report["status"],
            "BLOCKED_INCOMPLETE_OR_STALE_REQUIRED_TECHNICAL_EVIDENCE")
        self.assertEqual(report["canonical_counts"]["rows"], 396)
        self.assertEqual(report["scope"]["required"]["rows"], 367)
        self.assertEqual(report["scope"]["required"]["atomic_fields"], 1532)
        self.assertEqual(report["scope"]["conditional"]["rows"], 11)
        self.assertEqual(report["scope"]["optional"]["rows"], 18)
        self.assertFalse(report["technical_source_claims_complete"])
        self.assertFalse(report["release_authorized"])

    def test_true_arithmetic_positive_path_exists_without_release_permission(self):
        self.fake_all_required_complete_in_memory()
        report = self.report()
        self.assertTrue(report["technical_source_claims_complete"])
        self.assertEqual(
            report["status"],
            "TECHNICAL_SOURCE_CLAIMS_COMPLETE_AWAITING_INDEPENDENT_ATTESTATION")
        self.assertEqual(report["scope"]["required"]["full_technical_rows"], 367)
        self.assertEqual(report["scope"]["required"]["atomic_statuses"]["pass"], 1532)
        self.assertEqual(report["human_required_criterion_fields"], 35)
        self.assertFalse(report["product_acceptance_READY"])
        self.assertFalse(report["automatic_publish"])
        self.assertFalse(report["release_authorized"])
        self.assertEqual(report["technical_RC_READY"],
                         "NOT_ESTABLISHED_EXTERNALLY")

    def test_optional_and_conditional_still_counted_but_not_silently_required(self):
        self.fake_all_required_complete_in_memory()
        optional = next(row for row in self.rows
                        if self.index["contracts"][row["id"]]["scope"] == "optional")
        optional["technical_assessment"]["criteria"][0]["status"] = "not_run"
        outcome = self.report()
        self.assertTrue(outcome["technical_source_claims_complete"])
        self.assertEqual(outcome["scope"]["conditional"]["atomic_fields"], 42)
        self.assertEqual(outcome["scope"]["optional"]["atomic_fields"], 78)
        self.assertLess(
            outcome["scope"]["optional"]["full_technical_rows"], 18)

    def test_main_integration_missing_on_one_required_record_blocks(self):
        self.fake_all_required_complete_in_memory()
        row = next(row for row in self.rows
                   if self.index["contracts"][row["id"]]["scope"] == "required")
        row["technical_assessment"]["integration"]["status"] = "branch"
        result = self.report()
        self.assertFalse(result["technical_source_claims_complete"])
        self.assertEqual(result["scope"]["required"]["full_technical_rows"], 366)
        self.assertEqual(result["required_incomplete_reasons"]["not_main_integrated"], 1)

    def test_missing_one_original_atomic_observation_blocks(self):
        self.fake_all_required_complete_in_memory()
        row = next(row for row in self.rows
                   if self.index["contracts"][row["id"]]["scope"] == "required")
        row["technical_assessment"]["criteria"][0]["status"] = "stale"
        out = self.report()
        self.assertFalse(out["technical_source_claims_complete"])
        self.assertEqual(out["scope"]["required"]["atomic_statuses"]["pass"], 1531)
        self.assertEqual(out["required_incomplete_reasons"]["incomplete_atomic_criteria"], 1)

    def test_all_machine_evidence_complete_but_mismatched_source_is_blocked(self):
        self.fake_all_required_complete_in_memory()
        self.checkout["sha"] = "a" * 40
        outcome = self.report()
        self.assertFalse(outcome["technical_source_claims_complete"])
        self.assertFalse(outcome["source_identity_current"])
        self.assertFalse(outcome["release_authorized"])

    def test_stale_local_remote_main_cannot_be_spoofed_by_ledger(self):
        self.fake_all_required_complete_in_memory()
        self.checkout["remote_main_sha"] = "b" * 40
        report = self.report()
        self.assertFalse(report["technical_source_claims_complete"])
        self.assertFalse(report["source_identity_current"])

    def test_new_lock_with_old_evidence_is_blocked(self):
        self.fake_all_required_complete_in_memory()
        self.checkout["source_lock_sha256"] = "0" * 64
        self.assertFalse(self.report()["technical_source_claims_complete"])

    def test_duplicate_original_contract_row_is_rejected(self):
        self.rows[1] = copy.deepcopy(self.rows[0])
        with self.assertRaises(gate.TechnicalReadinessError):
            self.report()

    def test_missing_original_row_is_rejected(self):
        self.rows.pop()
        with self.assertRaises(gate.TechnicalReadinessError):
            self.report()

    def test_changed_canonical_atomic_clause_identity_is_rejected(self):
        self.rows[0]["technical_assessment"]["criteria"][0][
            "source_clause_sha256"] = "a" * 64
        with self.assertRaisesRegex(
                gate.TechnicalReadinessError, "atomic criterion"):
            self.report()

    def test_invented_applicability_scope_is_rejected(self):
        self.index = copy.deepcopy(self.index)
        self.index["contracts"][self.rows[0]["id"]]["scope"] = "waived"
        with self.assertRaises(gate.TechnicalReadinessError):
            self.report()

    def test_required_original_cannot_be_marked_not_applicable(self):
        self.fake_all_required_complete_in_memory()
        row = next(x for x in self.rows
                   if self.index["contracts"][x["id"]]["scope"] == "required")
        row["technical_assessment"]["contract_acceptance"]["status"] = "not_applicable"
        with self.assertRaisesRegex(
                gate.TechnicalReadinessError, "cannot be waived"):
            self.report()

    def test_human_contract_claim_never_grants_authorization(self):
        self.fake_all_required_complete_in_memory()
        for row in self.rows:
            if self.index["contracts"][row["id"]]["scope"] == "required":
                row["technical_assessment"]["contract_acceptance"]["status"] = "accepted"
        result = self.report()
        self.assertTrue(result["technical_source_claims_complete"])
        self.assertEqual(
            result["scope"]["required"][
                "recorded_contract_acceptances_not_authenticated"], 367)
        self.assertFalse(result["product_acceptance_READY"])
        self.assertFalse(result["release_authorized"])

    def test_empty_execution_reference_does_not_infer_human_approval(self):
        result = self.report()
        self.assertEqual(result["human_review_identity_authentication"],
                         "NOT_ASSESSED_BY_THIS_REPORT")
        self.assertTrue(result["human_observations_not_inferred_from_evidence_index"])
        self.assertEqual(result["external_release_authorization"], "NOT_ISSUED")



class LedgerPackageChainSyntheticTests(unittest.TestCase):
    """Pure negative Git-boundary controls; no product evidence implied."""

    def setUp(self):
        self.product = "a" * 40
        self.package = "b" * 40
        self.product_tree = "c" * 40
        self.package_tree = "d" * 40
        self.lock = b"source lock including ending newline\n"
        self.modified = set(gate.LEDGERS) | {gate.INDEX}
        self.parent = self.product
        self.dirty = ""
        self.extra_changed_path = None
        self.bad_ledger_mode = None
        self.git_lock = self.lock
        self.remote = self.package
        self.effective_remote = "https://github.com/seradotcom/motionwright.git"
        self.effective_remote_after = None
        self.remote_reads = 0
        self.queries = []
        self.ledger_blobs = {name: b'{"synthetic_ledger":true}\n'
                             for name in self.modified}
        self.index_declaration = {"records": [], "human_reviews": []}
        self.ledger_blobs[gate.INDEX] = json.dumps(self.index_declaration).encode("utf-8")
        self.worktree_bytes = dict(self.ledger_blobs)
        self.worktree_bytes["SOURCE_LOCK.json"] = self.lock
        self.additions = {}
        self.preexisting_artifacts = set()
        self.git_sizes = {}
        self.git_entries = {}

    def fake_git(self, root, *args, raw=False):
        self.queries.append(args)
        if args == ("rev-parse", "--show-toplevel"):
            return str(ROOT.resolve())
        if args == ("rev-parse", "HEAD"):
            return self.package
        if args == ("status", "--porcelain"):
            return self.dirty
        if args == ("rev-list", "--parents", "-n", "1", self.package):
            return self.package + " " + self.parent
        if args == ("rev-parse", self.product + "^{tree}"):
            return self.product_tree
        if args == ("rev-parse", self.package + "^{tree}"):
            return self.package_tree
        if args == ("diff", "--name-status", "--no-renames",
                    self.product, self.package):
            changed = set(self.modified)
            if self.extra_changed_path:
                changed.add(self.extra_changed_path)
            entries = ["M\t" + name for name in sorted(changed)]
            entries += [status + "\t" + name
                        for name, status in sorted(self.additions.items())]
            return "\n".join(entries)
        if args == ("ls-tree", "--full-tree", "-r", "--name-only", "-z",
                    self.product, "--", ":(literal)docs/acceptance/evidence/"):
            wire = b"".join(name.encode("utf-8") + b"\0"
                            for name in sorted(self.preexisting_artifacts))
            return wire if raw else wire.decode("utf-8").strip()
        if len(args) == 3 and args[:2] == ("cat-file", "-s"):
            name = args[2].split(":", 1)[1]
            return str(self.git_sizes.get(name, len(self.ledger_blobs[name])))
        if len(args) == 5 and args[:4] == (
                "ls-tree", "--full-tree", self.package, "--"):
            path = args[4].removeprefix(":(literal)")
            if path in self.git_entries:
                return self.git_entries[path]
            mode = "120000" if self.bad_ledger_mode == path else "100644"
            return mode + " blob " + ("e" * 40) + "\t" + path
        if len(args) == 2 and args[0] == "show" and args[1].startswith(self.package + ":"):
            value = self.ledger_blobs[args[1].split(":", 1)[1]]
            return value if raw else value.decode().strip()
        if args == ("remote", "get-url", "--all", "origin"):
            self.remote_reads += 1
            if self.remote_reads > 1 and self.effective_remote_after is not None:
                return self.effective_remote_after
            return self.effective_remote
        if args == ("show", self.product + ":SOURCE_LOCK.json"):
            return self.git_lock if raw else self.git_lock.decode().strip()
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return self.remote + "\trefs/heads/main"
        raise AssertionError("unrecognized Git read-only query: " + repr(args))

    def sync_index_declarations(self):
        raw = json.dumps(self.index_declaration).encode("utf-8")
        self.ledger_blobs[gate.INDEX] = raw
        self.worktree_bytes[gate.INDEX] = raw

    def add_artifact(self, name=None, content=b"original execution bytes\n",
                     role="artifact"):
        name = name or "docs/acceptance/evidence/new-run.json"
        self.additions[name] = "A"
        self.ledger_blobs[name] = content
        self.worktree_bytes[name] = content
        descriptor = {"path": name, "sha256": gate._sha(content)}
        if role == "artifact":
            self.index_declaration["records"].append({
                "artifacts": [descriptor], "complete_criterion_ids": []})
        elif role == "human_record":
            self.index_declaration["human_reviews"].append({"record": descriptor})
        elif role == "source_input":
            self.index_declaration["records"].append({
                "artifacts": [], "source_inputs": [descriptor]})
        self.sync_index_declarations()
        return name

    def validate(self):
        with patch.object(gate, "_git", side_effect=self.fake_git), patch.object(
                gate, "_read_tracked_bytes",
                side_effect=lambda root, name, **kwargs: self.worktree_bytes[name]):
            return gate._frozen_checkout(ROOT, self.product)

    def test_realistic_single_parent_four_ledger_package_is_eligible(self):
        result = self.validate()
        self.assertEqual(result["sha"], self.product)
        self.assertEqual(result["product_tree"], self.product_tree)
        self.assertEqual(result["ledger_package_sha"], self.package)
        self.assertEqual(result["ledger_package_tree"], self.package_tree)
        self.assertEqual(result["remote_main_sha"], self.package)
        self.assertEqual(result["packaged_paths"], sorted(self.modified))
        self.assertEqual(result["remote_identity"]["repository"],
                         "seradotcom/motionwright")
        self.assertEqual(set(result["packaged_git_text_sha256"]), self.modified)

    def test_parent_product_source_mismatch_is_denied(self):
        self.parent = "f" * 40
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "directly follow"):
            self.validate()

    def test_unrelated_source_change_in_package_is_denied(self):
        self.extra_changed_path = "crates/native/src/lib.rs"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "exactly four"):
            self.validate()

    def test_symlink_ledger_in_package_is_denied(self):
        self.bad_ledger_mode = sorted(self.modified)[0]
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "ordinary file"):
            self.validate()

    def test_original_product_lock_mismatch_is_denied(self):
        self.git_lock = b"different original source lock\n"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "source lock"):
            self.validate()

    def test_dirty_packaged_checkout_is_denied(self):
        self.dirty = " M docs/acceptance/requirements-status.json"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "not clean"):
            self.validate()

    def test_missing_live_remote_main_reference_is_denied(self):
        self.remote = ""
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "live remote"):
            self.validate()

    def test_tool_never_attests_a_human_or_approves_release(self):
        result = self.validate()
        self.assertNotIn("human_approved", result)
        self.assertNotIn("release_authorized", result)


    def test_hidden_worktree_bytes_are_denied_even_when_git_status_is_clean(self):
        self.worktree_bytes[sorted(self.modified)[0]] += b" "
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "consumed ledger bytes"):
            self.validate()
        self.assertNotIn(("ls-remote", "origin", "refs/heads/main"), self.queries)

    def test_only_git_text_lf_checkout_normalization_is_permitted(self):
        self.worktree_bytes = {
            name: value.replace(b"\n", b"\r\n")
            for name, value in self.worktree_bytes.items()
        }
        result = self.validate()
        self.assertEqual(
            result["packaged_git_text_sha256"],
            {name: gate._sha(value) for name, value in self.ledger_blobs.items()})

    def test_foreign_effective_remote_is_rejected_before_network_query(self):
        self.effective_remote = "https://github.com/other/motionwright.git"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "canonical effective"):
            self.validate()
        self.assertNotIn(("ls-remote", "origin", "refs/heads/main"), self.queries)

    def test_multiple_effective_remote_urls_are_rejected(self):
        self.effective_remote += "\nhttps://github.com/other/motionwright.git"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "canonical effective"):
            self.validate()
        self.assertNotIn(("ls-remote", "origin", "refs/heads/main"), self.queries)

    def test_effective_remote_change_during_observation_is_rejected(self):
        self.effective_remote_after = "git@github.com:seradotcom/motionwright.git"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "effective remote changed"):
            self.validate()


    def test_new_declared_artifact_in_child_package_is_eligible(self):
        name = self.add_artifact()
        result = self.validate()
        self.assertEqual(result["packaged_artifact_sha256"],
                         {name: gate._sha(self.ledger_blobs[name])})
        self.assertEqual(set(result["packaged_paths"]), self.modified | {name})
        self.assertNotIn("release_authorized", result)

    def test_new_human_record_bytes_do_not_grant_human_approval(self):
        name = self.add_artifact(role="human_record")
        result = self.validate()
        self.assertIn(name, result["packaged_artifact_sha256"])
        self.assertNotIn("human_approved", result)

    def test_new_unreferenced_evidence_file_is_denied(self):
        self.add_artifact(role="unreferenced")
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "not a retained artifact"):
            self.validate()

    def test_source_input_reference_does_not_authorize_new_artifact(self):
        self.add_artifact(role="source_input")
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "not a retained artifact"):
            self.validate()

    def test_historical_artifact_modification_deletion_and_rename_are_denied(self):
        name = self.add_artifact()
        for status in ("M", "D", "R100"):
            with self.subTest(status=status):
                self.additions[name] = status
                with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                            "only add declared"):
                    self.validate()

    def test_declared_artifact_outside_public_evidence_directory_is_denied(self):
        self.add_artifact(name="tooling/claimed-output.py")
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "only add declared"):
            self.validate()

    def test_new_artifact_traversal_and_path_aliases_are_denied(self):
        name = self.add_artifact()
        for bad in (gate.EVIDENCE_PREFIX + "../output.json",
                    gate.EVIDENCE_PREFIX + "./output.json",
                    gate.EVIDENCE_PREFIX + "output.json."):
            with self.subTest(path=bad):
                self.additions = {bad: "A"}
                with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                            "only add declared"):
                    self.validate()
        self.additions = {name: "A", gate.EVIDENCE_PREFIX + "NEW-RUN.JSON": "A"}
        with self.assertRaises(gate.TechnicalReadinessError):
            self.validate()

    def test_linked_artifact_git_entry_is_denied(self):
        self.bad_ledger_mode = self.add_artifact()
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "tracked ordinary file"):
            self.validate()

    def test_new_artifact_cannot_replace_a_historical_git_path(self):
        name = self.add_artifact()
        self.preexisting_artifacts.add(name)
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "historical path"):
            self.validate()

    def test_new_artifact_cannot_alias_historical_path_by_case(self):
        self.add_artifact(name=gate.EVIDENCE_PREFIX + "Original.json")
        self.preexisting_artifacts.add(gate.EVIDENCE_PREFIX + "original.JSON")
        with self.assertRaisesRegex(gate.TechnicalReadinessError, "alias a historical"):
            self.validate()
        inventory_queries = [query for query in self.queries
                             if "--name-only" in query]
        self.assertEqual(len(inventory_queries), 1)

    def test_ambiguous_git_artifact_entry_is_denied(self):
        name = self.add_artifact(name=gate.EVIDENCE_PREFIX + "run[1].json")
        self.git_entries[name] = ("100644 blob " + "e" * 40 + "\tother\n"
                                  + "120000 blob " + "f" * 40 + "\t" + name)
        with self.assertRaisesRegex(gate.TechnicalReadinessError, "tracked ordinary"):
            self.validate()

    def test_new_artifact_git_bytes_must_match_declared_raw_digest(self):
        name = self.add_artifact()
        self.ledger_blobs[name] += b"changed Git bytes"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "Git bytes differ"):
            self.validate()

    def test_hidden_artifact_worktree_bytes_are_denied(self):
        name = self.add_artifact()
        self.worktree_bytes[name] += b"hidden local mutation"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "consumed new evidence bytes"):
            self.validate()

    def test_new_artifact_raw_hash_never_uses_text_normalization(self):
        name = self.add_artifact(content=b"one\ntwo\n")
        self.worktree_bytes[name] = b"one\r\ntwo\r\n"
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "consumed new evidence bytes"):
            self.validate()

    def test_conflicting_retained_digests_for_the_same_path_are_denied(self):
        name = self.add_artifact()
        self.index_declaration["human_reviews"].append({
            "record": {"path": name, "sha256": "e" * 64}})
        self.sync_index_declarations()
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "conflicting raw digests"):
            self.validate()

    def test_reader_per_file_limit_precedes_artifact_blob_read(self):
        name = self.add_artifact()
        self.git_sizes[name] = gate.MAX_ADDED_ARTIFACT_BYTES + 1
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "reader per-file"):
            self.validate()
        self.assertNotIn(("show", self.package + ":" + name), self.queries)

    def test_reader_total_limit_precedes_all_artifact_blob_reads(self):
        names = [self.add_artifact(name=gate.EVIDENCE_PREFIX + f"run-{n}.json")
                 for n in range(5)]
        self.git_sizes.update({name: gate.MAX_ADDED_ARTIFACT_BYTES for name in names})
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "reader total"):
            self.validate()
        for name in names:
            self.assertNotIn(("show", self.package + ":" + name), self.queries)


class FrozenLedgerConsumptionSyntheticTests(unittest.TestCase):
    """In-memory wire fixtures test rejection before the physical index loader."""

    def fixtures(self, descriptor):
        raw = json.dumps({"technical_evidence_index": descriptor}).encode("utf-8")
        documents = {name: raw for name in gate.LEDGERS}
        digests = {name: gate._sha(raw) for name in gate.LEDGERS}
        digests[gate.INDEX] = "f" * 64
        return documents, digests

    def rejected_before_loader(self, documents, digests, reason):
        with patch.object(gate, "_read_tracked_bytes",
                          side_effect=lambda root, name: documents[name]), patch.object(
                gate, "load_evidence_index") as loader:
            with self.assertRaisesRegex(gate.TechnicalReadinessError, reason):
                gate._rows_and_index(ROOT, digests)
            loader.assert_not_called()

    def test_matching_descriptors_cannot_select_an_alternate_valid_index(self):
        documents, digests = self.fixtures({
            "path": "docs/acceptance/alternate-evidence.json",
            "sha256": "f" * 64, "digest_kind": "git-text-lf"})
        self.rejected_before_loader(documents, digests, "literal canonical path")

    def test_ledger_changed_between_freeze_and_parse_is_rejected(self):
        documents, digests = self.fixtures({
            "path": gate.INDEX, "sha256": "f" * 64, "digest_kind": "git-text-lf"})
        documents[next(iter(gate.LEDGERS))] += b" "
        self.rejected_before_loader(documents, digests, "consumed ledger bytes")

    def test_canonical_index_descriptor_must_match_the_packaged_blob(self):
        documents, digests = self.fixtures({
            "path": gate.INDEX, "sha256": "e" * 64, "digest_kind": "git-text-lf"})
        self.rejected_before_loader(documents, digests, "frozen Git blob")

    def test_post_read_change_is_rejected_even_with_unchanged_git_metadata(self):
        before = {name: b"initial bytes\n"
                  for name in (*gate.LEDGERS, gate.INDEX, "SOURCE_LOCK.json")}
        current = dict(before)
        frozen = {"packaged_git_text_sha256": {}, "packaged_artifact_sha256": {}}

        def consume(root, digests):
            current[next(iter(gate.LEDGERS))] = b"changed after consumption\n"
            return {}, []

        with patch.object(gate, "_frozen_checkout", return_value=frozen), patch.object(
                gate, "_read_tracked_bytes",
                side_effect=lambda root, name, **kwargs: current[name]), patch.object(
                gate, "_rows_and_index", side_effect=consume), patch.object(
                gate, "derive_eligibility", return_value={}):
            with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                        "changed during assessment"):
                gate.inspect(ROOT, "a" * 40)


    def test_added_artifact_must_be_in_the_fully_validated_index(self):
        with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                    "not consumed"):
            gate._validated_package_additions(
                {"records": [], "human_reviews": []},
                {gate.EVIDENCE_PREFIX + "unconsumed.json": "f" * 64})

    def test_added_artifact_post_read_change_is_rejected(self):
        name = gate.EVIDENCE_PREFIX + "run.json"
        before = {path: b"initial bytes\n"
                  for path in (*gate.LEDGERS, gate.INDEX, "SOURCE_LOCK.json", name)}
        current = dict(before)
        digest = gate._sha(before[name])
        frozen = {"packaged_git_text_sha256": {},
                  "packaged_artifact_sha256": {name: digest}}
        index = {"records": [{"artifacts": [{"path": name, "sha256": digest}]}],
                 "human_reviews": []}

        def consume(root, digests):
            current[name] = b"changed after canonical loader read\n"
            return index, []

        with patch.object(gate, "_frozen_checkout", return_value=frozen), patch.object(
                gate, "_read_tracked_bytes",
                side_effect=lambda root, path, **kwargs: current[path]), patch.object(
                gate, "_rows_and_index", side_effect=consume), patch.object(
                gate, "derive_eligibility", return_value={}):
            with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                        "changed during assessment"):
                gate.inspect(ROOT, "a" * 40)


class PhysicalReadBoundTests(unittest.TestCase):
    """Real small files exercise byte limits; no large allocation is needed."""

    def test_exact_raw_limit_and_empty_file_are_captured_without_prefix_rules(self):
        with tempfile.TemporaryDirectory(prefix="readiness-bound-") as directory:
            root = Path(directory)
            target = root / "sample.bin"
            for content in (b"", b"raw\r\n\x00bytes"):
                with self.subTest(content=content):
                    target.write_bytes(content)
                    with patch.object(Path, "read_bytes", side_effect=AssertionError(
                            "unbounded read_bytes must not be used")):
                        self.assertEqual(gate._read_tracked_bytes(
                            root, "sample.bin", max_bytes=len(content)), content)

    def test_physical_oversize_is_rejected_before_open(self):
        with tempfile.TemporaryDirectory(prefix="readiness-bound-") as directory:
            root = Path(directory)
            (root / "sample.bin").write_bytes(b"oversized")
            with patch.object(Path, "open", side_effect=AssertionError(
                    "oversized capture must not open a stream")):
                with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                            "reader byte limit"):
                    gate._read_tracked_bytes(root, "sample.bin", max_bytes=3)

    def test_growth_after_handle_observation_uses_only_bounded_read_and_rejects_prefix(self):
        with tempfile.TemporaryDirectory(prefix="readiness-bound-") as directory:
            root = Path(directory)
            target = root / "sample.bin"
            original = b"small"
            target.write_bytes(original)
            original_open = Path.open
            reads = []

            class GrowingStream:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def fileno(self):
                    return self.stream.fileno()

                def read(self, size=-1):
                    reads.append(size)
                    if size != len(original) + 1:
                        raise AssertionError("physical read is not bounded")
                    with original_open(target, "ab") as writer:
                        writer.write(b"grew-after-fstat")
                    return self.stream.read(size)

            def open_growing(path, *args, **kwargs):
                return GrowingStream(original_open(path, *args, **kwargs))

            with patch.object(Path, "open", open_growing), patch.object(
                    Path, "read_bytes", side_effect=AssertionError("unbounded read")):
                with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                            "reader byte limit"):
                    gate._read_tracked_bytes(root, "sample.bin", max_bytes=len(original))
            self.assertEqual(reads, [len(original) + 1])

    def test_frozen_package_uses_git_blob_size_for_actual_physical_read(self):
        fixture = LedgerPackageChainSyntheticTests()
        fixture.setUp()
        name = fixture.add_artifact(content=b"small")
        actual_reader = gate._read_tracked_bytes
        with tempfile.TemporaryDirectory(prefix="readiness-bound-") as directory:
            root = Path(directory)
            target = root / name
            target.parent.mkdir(parents=True)
            target.write_bytes(b"small-plus-unadmitted-bytes")
            observed_limits = []

            def read(root_arg, path, **kwargs):
                if path == name:
                    observed_limits.append(kwargs.get("max_bytes"))
                    return actual_reader(root, path, **kwargs)
                return fixture.worktree_bytes[path]

            with patch.object(gate, "_git", side_effect=fixture.fake_git), patch.object(
                    gate, "_read_tracked_bytes", side_effect=read):
                with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                            "reader byte limit"):
                    gate._frozen_checkout(ROOT, fixture.product)
            self.assertEqual(observed_limits, [5])

    def test_assessment_and_final_stability_reads_keep_exact_artifact_limit(self):
        name = gate.EVIDENCE_PREFIX + "physical-bound.bin"
        content = b"small"
        digest = gate._sha(content)
        frozen = {"packaged_git_text_sha256": {},
                  "packaged_artifact_sha256": {name: digest},
                  "packaged_artifact_sizes": {name: len(content)}}
        index = {"records": [{"artifacts": [{"path": name, "sha256": digest}]}],
                 "human_reviews": []}
        actual_reader = gate._read_tracked_bytes
        with tempfile.TemporaryDirectory(prefix="readiness-bound-") as directory:
            root = Path(directory)
            target = root / name
            target.parent.mkdir(parents=True)
            target.write_bytes(content)
            limits = []

            def read(root_arg, path, **kwargs):
                if path == name:
                    limits.append(kwargs.get("max_bytes"))
                    return actual_reader(root, path, **kwargs)
                return b"unchanged synthetic ledger bytes"

            def consume(*args):
                target.write_bytes(content + b"grew-during-assessment")
                return index, []

            with patch.object(gate, "_frozen_checkout", return_value=frozen), patch.object(
                    gate, "_read_tracked_bytes", side_effect=read), patch.object(
                    gate, "_rows_and_index", side_effect=consume), patch.object(
                    gate, "derive_eligibility", return_value={}):
                with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                            "reader byte limit"):
                    gate.inspect(root, "a" * 40)
            self.assertEqual(limits, [len(content), len(content)])


class EffectiveRemoteGitResolutionTests(unittest.TestCase):
    """Actual local Git configuration, without any network operation."""

    def test_canonical_configured_url_rewritten_to_foreign_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="readiness-remote-") as directory:
            root = Path(directory)
            gate._git(root, "init", "--quiet")
            gate._git(root, "remote", "add", "origin",
                      "https://github.com/seradotcom/motionwright.git")
            gate._git(root, "config", "--local",
                      "url.https://example.invalid/.insteadOf", "https://github.com/")
            with self.assertRaisesRegex(gate.TechnicalReadinessError,
                                        "canonical effective"):
                gate._canonical_remote(root)

    def test_canonical_effective_ssh_identity_is_retained(self):
        with tempfile.TemporaryDirectory(prefix="readiness-remote-") as directory:
            root = Path(directory)
            gate._git(root, "init", "--quiet")
            gate._git(root, "remote", "add", "origin",
                      "git@github.com:seradotcom/motionwright.git")
            identity = gate._canonical_remote(root)
            self.assertEqual(identity["repository"], "seradotcom/motionwright")
            self.assertEqual(identity["effective_url"],
                             "git@github.com:seradotcom/motionwright.git")

if __name__ == "__main__":
    unittest.main(verbosity=2)
