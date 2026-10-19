"""Synthetic negative/positive tests for A5 history guard; no original private data."""
from __future__ import annotations
import copy
import sys
from pathlib import Path
import unittest

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
            "display_title":"A concise title",
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
        self.new[guard.LEDGERS[0]]["requirements"][0]["display_title"]="Renamed visibly"
        result=guard.inspect(self.old,self.new)
        self.assertEqual(result["ledger_rows_verified"],1)
        self.assertEqual(result["result"],"history_preserved")
        self.assertFalse(result["release_authorized"])

    def test_removed_history_is_not_reset(self):
        self.new[guard.LEDGERS[0]]["requirements"][0]["assessment_history"]=[]
        self.assertViolation("assessment_history_rewritten_or_truncated")

    def test_reordered_history_is_rejected(self):
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
        self.assertViolation("changed_assessment_has_no_prior_snapshot")

    def test_forged_new_source_label_does_not_satisfy_prior_snapshot(self):
        row=self.new[guard.LEDGERS[0]]["requirements"][0]
        row["technical_assessment"]["technical_verification"]["status"]="partial"
        row["assessment_history"].append({
            "source":{**self.source,"motionwright_sha":"e"*40},
            "prior_technical_status":"not_run","prior_criteria":[self.field]})
        self.assertViolation("changed_assessment_has_no_prior_snapshot")

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
        row["display_title"]="Change name"
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


if __name__=="__main__":
    unittest.main()
