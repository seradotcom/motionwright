#!/usr/bin/env python3
"""Synthetic immutable native handoff tests. Never publishes to Launchwright."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from export_handoff import Refused,package

PROJECT="018f0000-0000-7000-8000-000000000001"
GENERATION="018f0000-0000-7000-8000-000000000002"
SCENE="018f0000-0000-7000-8000-000000000003"
CLAIM="018f0000-0000-7000-8000-000000000004"
ASSET="018f0000-0000-7000-8000-000000000005"
BINDING="018f0000-0000-7000-8000-000000000006"
DIGEST="ab"*32

class HandoffContractTests(unittest.TestCase):
    def setUp(self)->None:
        self.tmp=tempfile.TemporaryDirectory(prefix="mw-launchwright-immutable-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.master=self.root/"output"/"master.mp4"
        self.master.parent.mkdir(parents=True)
        self.master.write_bytes(b"\x00\x00\x00\x14ftypisom\x00\x00\x00\x00isom0000 synthetic original")
        msha=hashlib.sha256(self.master.read_bytes()).hexdigest()
        self.project=self.root/"project"/"native.json"
        self.project.parent.mkdir(parents=True)
        self.payload={
            "schema_version":3,"id":PROJECT,"generation":GENERATION,"revision":12,
            "title":"Original synthetic test",
            "scenes":[{"id":SCENE}],
            "assets":[{"id":ASSET,"content_sha256":DIGEST}],
            "brief":{"claims":[{"id":CLAIM,"text":"An attested original test claim",
                "source":{"kind":"asset","asset_id":ASSET},"context":"synthetic",
                "source_revision":"r12"}]},
            "production_design":{"plan":{"objective":"A reviewable original product story",
                "shots":[{"scene_id":SCENE,"purpose":"Own illustration",
                 "claim_ids":[CLAIM],"asset_ids":[ASSET],"evidence_kind":"synthetic_labeled"}],
                "approval":{"content_sha256":"bc"*32,"reviewer":"Owner reviewer","note":"Source approval, not release grant"}}},
            "handoffs":[{"id":BINDING,"system":"launchwright","direction":"artifact_output",
                "external_kind":"artifact","external_id":"target-artifact-approval-ref",
                "external_revision":"r1","local_resource":"project:"+PROJECT,
                "artifact_sha256":msha}]
        }
        self.save_project()
        self.request={
            "schema":"motionwright.launchwright-handoff-request/1","mode":"synthetic_fixture",
            "project_snapshot":self.ref(self.project),
            "master_video":self.ref(self.master),
            "expected_project":{"id":PROJECT,"generation":GENERATION,"revision":12},
            "handoff_binding_id":BINDING,
            "asset_rights":[{"asset_id":ASSET,"sha256":DIGEST,"owner":"Original fixture",
                            "usage_rights":"Fixture author approved for isolated test","authorized":True}],
            "claim_reviews":[{"claim_id":CLAIM,"verified":True,
                              "source_note":"Source bound to an original synthetic illustrative artifact"}],
            "owner_review":{"reviewer":"Owner reviewer","approval_content_sha256":"bc"*32,
                            "creative_approved":True,"publication_requested":False},
        }

    def save_project(self)->None:
        self.project.write_text(json.dumps(self.payload,sort_keys=True,separators=(",",":"))+"\n")

    def ref(self,p:Path)->dict:
        return {"path":str(p.relative_to(self.root)),
                "sha256":hashlib.sha256(p.read_bytes()).hexdigest()}

    def refresh(self)->None:
        self.save_project()
        self.request["project_snapshot"]=self.ref(self.project)

    def test_zip_preserves_exact_project_and_master_without_publish_authority(self)->None:
        first=self.root/"handoff-1.zip"
        result=package(self.request,self.root,first)
        self.assertEqual(result["status"],"SYNTHETIC_HANDOFF_PROTOCOL_ONLY")
        self.assertFalse(result["publication_approved"])
        with zipfile.ZipFile(first)as archive:
            self.assertIsNone(archive.testzip())
            manifest=json.loads(archive.read("handoff.json"))
            self.assertEqual(archive.read("source/motionwright-project.json"),self.project.read_bytes())
            self.assertEqual(archive.read("media/creative-master.mp4"),self.master.read_bytes())
            self.assertEqual(manifest["native_project"]["revision"],12)
            self.assertEqual(manifest["master_video"]["sha256"],self.ref(self.master)["sha256"])
            self.assertFalse(manifest["publication_approved"])
            self.assertEqual(manifest["launchwright_import_status"],"NOT_PERFORMED")
            self.assertTrue(manifest["timeline_unchanged"])
        other=package(self.request,self.root,self.root/"handoff-2.zip")
        self.assertEqual(result["archive_sha256"],other["archive_sha256"])
        with self.assertRaisesRegex(Refused,"overwrite"):
            package(self.request,self.root,first)

    def test_mutated_master_and_stale_project_are_rejected(self)->None:
        self.master.write_bytes(b"\x00\x00\x00\x14ftypisom---tampered")
        with self.assertRaisesRegex(Refused,"content changed"):
            package(self.request,self.root,self.root/"tampered.zip")
        self.master.write_bytes(b"\x00\x00\x00\x14ftypisom\x00\x00\x00\x00isom0000 synthetic original")
        self.request["expected_project"]["revision"]=11
        with self.assertRaisesRegex(Refused,"stale"):
            package(self.request,self.root,self.root/"stale.zip")

    def test_project_claims_and_source_rights_are_mandatory(self)->None:
        self.request["asset_rights"]=[]
        with self.assertRaisesRegex(Refused,"rights declaration"):
            package(self.request,self.root,self.root/"missing-rights.zip")
        self.request["asset_rights"]=[{"asset_id":ASSET,"sha256":DIGEST,"owner":"fixture",
                                      "usage_rights":"fixture only","authorized":True}]
        self.payload["brief"]["claims"][0]["source"]=None
        self.refresh()
        with self.assertRaisesRegex(Refused,"sourced claim"):
            package(self.request,self.root,self.root/"no-source.zip")

    def test_handoff_binding_cannot_cross_destination_or_change_master(self)->None:
        self.payload["handoffs"][0]["system"]="another-company"
        self.refresh()
        with self.assertRaisesRegex(Refused,"approved exact"):
            package(self.request,self.root,self.root/"other.zip")
        self.payload["handoffs"][0]["system"]="launchwright"
        self.payload["handoffs"][0]["artifact_sha256"]="c"*64
        self.refresh()
        with self.assertRaisesRegex(Refused,"approved exact"):
            package(self.request,self.root,self.root/"wrong-master.zip")

    def test_approval_never_requests_publication_and_is_source_specific(self)->None:
        self.request["owner_review"]["publication_requested"]=True
        with self.assertRaisesRegex(Refused,"cannot request publication"):
            package(self.request,self.root,self.root/"publish.zip")
        self.request["owner_review"]["publication_requested"]=False
        self.request["owner_review"]["approval_content_sha256"]="c"*64
        with self.assertRaisesRegex(Refused,"exact approved ProductionPlan"):
            package(self.request,self.root,self.root/"forged-approval.zip")

    def test_symlinked_media_or_untrusted_file_cannot_be_packaged(self)->None:
        mirror=self.root/"source-link.mp4"
        mirror.symlink_to(self.master)
        self.request["master_video"]={"path":"source-link.mp4","sha256":self.ref(self.master)["sha256"]}
        with self.assertRaisesRegex(Refused,"symlink"):
            package(self.request,self.root,self.root/"link.zip")

    def test_unrecognized_manifest_privilege_is_rejected(self)->None:
        self.request["publish_now"]=True
        with self.assertRaisesRegex(Refused,"exactly"):
            package(self.request,self.root,self.root/"unauthorized.zip")

if __name__=="__main__":unittest.main()
