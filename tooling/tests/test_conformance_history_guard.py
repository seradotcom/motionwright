"""Synthetic negative/positive tests for A5 history guard; no original private data."""
from __future__ import annotations
import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import conformance_history_guard as guard


class HistoryGuardTests(unittest.TestCase):
    def setUp(self):
        self.source={"motionwright_sha":"a"*40,"source_lock_sha256":"b"*64,
                     "digest_kind":"git-text-lf"}
        self.field={"id":"EXAMPLE-01#/statement","source_clause_sha256":"c"*64,
                    "status":"not_run","evidence_ids":[],
                    "missing_assertions":["source-run-pending"]}
        self.row={"id":"EXAMPLE-01","source_record_sha256":"d"*64,
            "title":"A concise title",
            "assessment_history":[{"source":self.source,"prior_technical_status":"not_run",
                                   "prior_criteria":[copy.deepcopy(self.field)]}],
            "technical_assessment":{
              "source":self.source,
              "criteria":[self.field],
              "technical_verification":{"status":"not_run","evidence_ids":[]},
              "evidence_currency":{"historical_record_ids":["record-older"],
                                   "current_record_ids":["record-current"]},
              "contract_acceptance":{"status":"open"}}}
        self.index={"criteria":{"EXAMPLE-01#/statement":"c"*64},
                    "contracts":{"EXAMPLE-01":{"source_record_sha256":"d"*64}},
                    "normative_edges":[{"requirement_id":"EXAMPLE-01","case_id":"CASE-01"}],
                    "criterion_aliases":{},"human_reviews":[],
                    "records":[{"id":"record-older"}],
                    "sufficiency_reviews":[{"id":"review-older"}],
                    "assessment_history":[{"source":self.source}]}
        self.old={
           guard.LEDGERS[0]:{"requirements":[self.row]},
           guard.LEDGERS[1]:{"cases":[],"expansion_cases":[]},
           guard.LEDGERS[2]:{"requirements":[]},
           guard.INDEX:self.index}
        self.new=copy.deepcopy(self.old)

    def assertViolation(self, code):
        with self.assertRaisesRegex(guard.HistoryViolation,code):
            guard.inspect(self.old,self.new)

    def test_visible_title_change_preserves_structured_history(self):
        self.new[guard.LEDGERS[0]]["requirements"][0]["title"]="Renamed visibly"
        result=guard.inspect(self.old,self.new)
        self.assertEqual(result["ledger_rows_verified"],1)
        self.assertEqual(result["result"],"history_preserved")
        self.assertFalse(result["release_authorized"])

    def test_removed_history_is_not_reset(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["title"]="Renamed requirement, same ID"
        row["assessment_history"]=[]
        self.assertViolation("assessment_history_rewritten_or_truncated")

    def test_reordered_history_is_rejected(self):
        self.new[guard.LEDGERS[0]]["requirements"][0]["title"]="Changed title"
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[{
            "source":self.source,"prior_technical_status":"not_run","prior_criteria":[]},
            {"source":self.source,"prior_technical_status":"not_run","prior_criteria":[self.field]}]
        self.assertViolation("assessment_history_rewritten_or_truncated")

    def test_new_snapshot_can_only_append(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["assessment_history"].append({"source":self.source,"prior_technical_status":"not_run",
                                         "prior_criteria":[self.field]})
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        result=guard.inspect(self.old,self.new)
        self.assertEqual(result["changed_assessments_with_history"],1)

    def test_technical_status_change_without_saved_prior_snapshot_fails(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="pass"
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_forged_new_source_label_does_not_satisfy_prior_snapshot(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":{**self.source,"motionwright_sha":"e"*40},
            "prior_technical_status":"not_run","prior_criteria":[self.field]})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_row_id_renaming_cannot_drop_old_history(self):
        self.new[guard.LEDGERS[0]]["requirements"][0]["id"]="EXAMPLE-RENAMED"
        self.assertViolation("ledger_id_removed_renamed_or_added")

    def test_unknown_new_row_cannot_replace_the_old_id(self):
        self.new[guard.LEDGERS[0]]["requirements"].append({
             **copy.deepcopy(self.row),"id":"EXAMPLE-02"})
        self.assertViolation("ledger_id_removed_renamed_or_added")

    def test_duplicate_ids_are_rejected(self):
        self.new[guard.LEDGERS[0]]["requirements"].append(copy.deepcopy(self.row))
        self.assertViolation("duplicate_ledger_id")

    def test_source_record_fingerprint_cannot_change_with_title(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["title"]="Change name"
        row["source_record_sha256"]="0"*64
        self.assertViolation("canonical_row_identity_changed")

    def test_original_clause_digest_cannot_change(self):
        self.new[guard.LEDGERS[0]]["requirements"][0]["technical_assessment"]["criteria"][0]["source_clause_sha256"]="0"*64
        self.assertViolation("canonical_criterion_sha_changed")

    def test_original_clause_id_cannot_change(self):
        self.new[guard.LEDGERS[0]]["requirements"][0]["technical_assessment"]["criteria"][0]["id"]="EXAMPLE-01#/other"
        self.assertViolation("canonical_criterion_identity_changed")

    def test_previous_historical_receipt_must_be_preserved(self):
        r=self.new[guard.LEDGERS[0]]["requirements"][0]
        r["technical_assessment"]["evidence_currency"]["historical_record_ids"]=[]
        self.assertViolation("prior_historical_evidence_lost")

    def test_old_current_record_can_move_to_history(self):
        r=self.new[guard.LEDGERS[0]]["requirements"][0]
        r["technical_assessment"]["evidence_currency"]["historical_record_ids"].append("record-current")
        r["technical_assessment"]["evidence_currency"]["current_record_ids"]=[]
        r["assessment_history"].append({"source":self.source,"prior_technical_status":"not_run",
                                        "prior_criteria":[self.field]})
        self.assertEqual(guard.inspect(self.old,self.new)["changed_assessments_with_history"],1)

    def test_current_receipt_cannot_disappear_without_history(self):
        r=self.new[guard.LEDGERS[0]]["requirements"][0]
        r["technical_assessment"]["evidence_currency"]["current_record_ids"]=[]
        self.assertViolation("current_evidence_lost_without_history")

    def test_index_records_are_append_only(self):
        self.new[guard.INDEX]["records"]=[]
        self.assertViolation("index_records_rewritten_or_truncated")

    def test_index_reviews_are_append_only(self):
        self.new[guard.INDEX]["sufficiency_reviews"][0]["id"]="changed"
        self.assertViolation("index_sufficiency_reviews_rewritten_or_truncated")

    def test_original_normative_source_is_immutable(self):
        self.new[guard.INDEX]["normative_edges"]=[]
        self.assertViolation("canonical_source_catalogue_changed")

    def test_duplicate_json_names_fail_closed(self):
        with self.assertRaisesRegex(guard.HistoryViolation,"duplicate_json_field"):
            guard.parse_unique(b'{"id":"A","id":"B"}',"synthetic")

    def test_head_alias_is_not_a_source_sha(self):
        with self.assertRaisesRegex(guard.HistoryViolation,"exact_sha_required"):
            guard.git_exact(Path("."),"HEAD",guard.LEDGERS[0])

    def test_missing_row_or_evidence_index_is_not_success(self):
        self.new.pop(guard.INDEX)
        self.assertViolation("missing_source_index")

    def test_H01_empty_previous_criterion_list_does_not_prove_history(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":self.source, "prior_technical_status":"not_run",
            "prior_criteria":[]})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_H01_invented_old_criterion_does_not_prove_history(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        false_criterion=copy.deepcopy(self.field)
        false_criterion["status"]="pass"
        row["assessment_history"].append({
            "source":self.source, "prior_technical_status":"not_run",
            "prior_criteria":[false_criterion]})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_H01_only_current_evidence_ids_is_not_prior_snapshot(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":self.source,"prior_technical_status":"not_run",
            "prior_current_record_ids":["record-current"]})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_H01_false_previous_evidence_list_invalidates_real_criteria(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":self.source, "prior_technical_status":"not_run",
            "prior_criteria":[copy.deepcopy(self.field)],
            "prior_current_record_ids":["invented"]})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_H01_real_compact_status_snapshot_matches_every_criterion(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":self.source,"prior_technical_status":"not_run",
            "prior_criterion_statuses":{"EXAMPLE-01#/statement":"not_run"}})
        result=guard.inspect(self.old,self.new)
        self.assertEqual(result["changed_assessments_with_history"],1)

    def test_H01_empty_compact_status_map_is_not_accepted(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":self.source,"prior_technical_status":"not_run",
            "prior_criterion_statuses":{}})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_H01_invented_contract_previous_state_is_not_accepted(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":self.source,"prior_technical_status":"not_run",
            "prior_criteria":[copy.deepcopy(self.field)],
            "prior_contract_status":"accepted"})
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    @staticmethod
    def _synthetic_repo(root, text):
        root.mkdir(parents=True)
        def run(*args):
            p=subprocess.run(["git","-C",str(root),*args],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            if p.returncode:
                raise AssertionError(p.stderr.decode("utf-8","replace"))
            return p.stdout.decode("utf-8").strip()
        run("init","-q")
        (root/"docs/acceptance").mkdir(parents=True)
        (root/guard.INDEX).write_text(text,encoding="utf-8")
        run("add","--",guard.INDEX)
        run("-c","user.name=Test","-c","user.email=test@example.invalid",
            "commit","-q","-m","Synthetic exact source")
        return run("rev-parse","HEAD"),run

    def test_H02_inherited_git_dir_and_work_tree_cannot_redirect_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            real_sha,real=self._synthetic_repo(base/"real", "genuine-source")
            other_sha,other=self._synthetic_repo(base/"other","substituted-source")
            self.assertNotEqual(real_sha,other_sha)
            with mock.patch.dict(os.environ,{
                    "GIT_DIR":str(base/"other"/".git"),
                    "GIT_WORK_TREE":str(base/"other"),
                    "GIT_CONFIG_COUNT":"1",
                    "GIT_CONFIG_KEY_0":"core.quotePath",
                    "GIT_CONFIG_VALUE_0":"false"}):
                self.assertEqual(guard.git_exact(base/"real",real_sha,guard.INDEX),
                                 b"genuine-source")

    def test_H02_git_replace_ref_does_not_substitute_original_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"repo"
            before,run=self._synthetic_repo(root,"original-object")
            (root/guard.INDEX).write_text("replacement-object",encoding="utf-8")
            run("add","--",guard.INDEX)
            run("-c","user.name=Test","-c","user.email=test@example.invalid",
                "commit","-q","-m","Replacement object")
            after=run("rev-parse","HEAD")
            self.assertNotEqual(before,after)
            run("replace",before,after)
            # This is the original bypass: unguarded Git show now returns
            # contents from the replaced commit, despite the original SHA.
            self.assertEqual(run("show",before+":"+guard.INDEX),
                             "replacement-object")
            self.assertEqual(guard.git_exact(root,before,guard.INDEX),
                             b"original-object")

    def test_H02_noncanonical_source_path_cannot_be_requested(self):
        with self.assertRaisesRegex(guard.HistoryViolation,"unrecognized_source_path"):
            guard.git_exact(Path("."),"a"*40,"../../private/secret.json")


    def test_H03_roundtrip_logical_history_equals_all_previous_entries(self):
        original = copy.deepcopy(self.row["assessment_history"])
        archive = guard.encode_assessment_history(original)
        self.assertEqual(archive["entry_count"],len(original))
        self.assertEqual(guard.expand_assessment_history([archive]),original)
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["assessment_history"]=[archive]
        self.assertEqual(guard.inspect(self.old,self.new)["ledger_rows_verified"],1)

    def test_H03_real_source_transition_requires_exact_prior_snapshot(self):
        original=copy.deepcopy(self.row["assessment_history"])
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["assessment_history"]=[
            guard.encode_assessment_history(original),
            {"source":copy.deepcopy(self.source),"prior_technical_status":"not_run",
             "prior_criteria":[copy.deepcopy(self.field)],
             "prior_contract_status":"open",
             "prior_current_record_ids":["record-current"]}]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        self.assertEqual(guard.inspect(self.old,self.new)["changed_assessments_with_history"],1)

    def test_H03_forged_archived_prefix_rejected(self):
        old=copy.deepcopy(self.row["assessment_history"])
        old[0]["prior_criteria"]=[]
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[
            guard.encode_assessment_history(old)]
        self.assertViolation("assessment_history_rewritten_or_truncated")

    def test_H03_archive_sha_mismatch_rejected(self):
        archive=guard.encode_assessment_history(self.row["assessment_history"])
        archive["plain_sha256"]="0"*64
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[archive]
        self.assertViolation("history_archive_sha256_mismatch")

    def test_H03_invalid_base64_rejected(self):
        archive=guard.encode_assessment_history(self.row["assessment_history"])
        archive["payload_base64"]="!not-base64?"
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[archive]
        self.assertViolation("history_archive_decode_failed")

    def test_H03_plaintext_decompression_limit_enforced(self):
        import base64
        import hashlib
        import zlib
        archive=guard.encode_assessment_history(self.row["assessment_history"])
        plain=b"X"*(guard.MAX_HISTORY_PLAIN_BYTES+1)
        archive["payload_base64"]=base64.b64encode(zlib.compress(plain,9)).decode()
        archive["plain_sha256"]=hashlib.sha256(plain).hexdigest()
        archive["plain_bytes"]=len(plain)
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[archive]
        self.assertViolation("history_archive_plain_size_exceeded")

    def test_H03_archive_after_first_entry_is_rejected(self):
        archive=guard.encode_assessment_history(self.row["assessment_history"])
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["assessment_history"].append(archive)
        self.assertViolation("history_archive_must_be_prefix")

    def test_H03_change_with_only_old_archive_cannot_auto_pass(self):
        archive=guard.encode_assessment_history(self.row["assessment_history"])
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["assessment_history"]=[archive]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        self.assertViolation("changed_assessment_has_no_authentic_prior_snapshot")

    def test_H03_unknown_archive_version_rejected(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["assessment_history"]=[{"schema":"motionwright.assessment-history-zlib-json/2"}]
        self.assertViolation("unknown_history_archive_schema")

    def test_H03_empty_history_does_not_claim_a_real_snapshot(self):
        with self.assertRaisesRegex(guard.HistoryViolation,"empty_history_archive"):
            guard.encode_assessment_history([])

    def test_H03_rearchiving_existing_archive_remains_lossless(self):
        original=copy.deepcopy(self.row["assessment_history"])
        first=guard.encode_assessment_history(original)
        second=guard.encode_assessment_history([first])
        self.assertEqual(guard.expand_assessment_history([second]),original)
        self.assertEqual(first["plain_sha256"],second["plain_sha256"])

    def test_H03_duplicate_archive_json_keys_rejected(self):
        import base64
        import hashlib
        import zlib
        archive=guard.encode_assessment_history(self.row["assessment_history"])
        invalid=b'[{"source":"x","source":"y"}]'
        archive["payload_base64"]=base64.b64encode(zlib.compress(invalid,9)).decode()
        archive["plain_sha256"]=hashlib.sha256(invalid).hexdigest()
        archive["plain_bytes"]=len(invalid)
        archive["entry_count"]=1
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[archive]
        self.assertViolation("duplicate_history_archive_json_field")

if __name__=="__main__":
    unittest.main()
