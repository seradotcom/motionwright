#!/usr/bin/env python3
"""First-party portable OTIO tests. Synthetic bytes; real NLE/ffprobe is CI only."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from bridge import (
    BridgeRefused, export_portable, file_probe, import_preview, rtc, trange, SCHEMA,
    _probe_output, preview_kdenlive_cut, KDENLIVE_REQUEST_SCHEMA, fingerprint, _nle_local_media,
)

ID=lambda number:f"00000000-0000-4000-8000-{number:012x}"
VIDEO_INFO={"fps":30,"frames":90,"width":320,"height":180,"codec":"h264"}

class PortableOtioTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="motionwright-otio-media-unit-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.src=self.root/"owner"
        self.src.mkdir()
        self.media=self.src/"original.mp4"
        self.media.write_bytes(b"original source media bytes, not a ready video")
        sha=hashlib.sha256(self.media.read_bytes()).hexdigest()
        self.project={
            "id":ID(1),"generation":ID(2),"revision":7,"title":"Owned video source",
            "scenes":[{"id":ID(10+i),"name":f"Source scene {i}","start":{"num":str(i),"den":"1"},
                       "duration":{"num":"1","den":"1"}} for i in range(3)],
            "assets":[{"id":ID(50),"name":"Owner original","media_type":"video/mp4",
                       "content_sha256":sha,"source_revision":"first-party-test"}],
        }
        self.otio={
            "OTIO_SCHEMA":"Timeline.1",
            "metadata":{"motionwright":{
                "project_id":self.project["id"],"generation":self.project["generation"],
                "revision":7,"export_profile":"conservative-cut-v1",
                "loss_report":["Canvas nodes and original composition remain native."]
            }},
            "name":"Owned video source",
            "tracks":{"OTIO_SCHEMA":"Stack.1","children":[{
                "OTIO_SCHEMA":"Track.1","kind":"Video",
                "children":[{
                    "OTIO_SCHEMA":"Clip.1","enabled":True,
                    "effects":[],"markers":[],
                    "media_reference":{"OTIO_SCHEMA":"MissingReference.1",
                                       "available_range":None,"metadata":{},"name":None},
                    "source_range":trange(0,24,24),
                    "metadata":{"motionwright":{"scene_id":ID(10+i)}},
                    "name":f"Source scene {i}"
                }for i in range(3)]}],
            }
        }
        self.save()
        self.rights={"owner":"Original creator","license":"Personal original demo rights",
                     "authorized":True,"redistributable":True}
        self.request={
            "schema":SCHEMA,"project":"project.json","otio":"baseline.otio",
            "bindings":[{"scene_id":ID(10+i),"asset_id":ID(50),
                         "file":"original.mp4","sha256":sha,
                         "rights":self.rights.copy(),
                         "source_start_frame":i*30} for i in range(2)],
            "owner_review":{"reviewer":"Owner reviewed assets",
                            "synthetic_source_allowed":False}
        }
        self.out=self.root/"export"
        self.expected_hash=sha

    def save(self):
        (self.src/"project.json").write_text(json.dumps(self.project,indent=2))
        (self.src/"baseline.otio").write_text(json.dumps(self.otio,indent=2))

    def export(self):
        with patch("bridge.file_probe",return_value=VIDEO_INFO):
            return export_portable(self.src,self.request,self.out)

    def read(self):
        return json.loads((self.out/"timeline.otio").read_text())

    def preview(self,name="timeline.otio",current=None):
        with patch("bridge.file_probe",return_value=VIDEO_INFO):
            return import_preview(self.out,name,current or self.out/"source-project.json")

    def test_real_digest_bound_external_reference_with_actual_source_bytes(self):
        report=self.export()
        self.assertEqual(report["verified_media_refs"],2)
        self.assertEqual(report["missing_refs"],1)
        self.assertFalse(report["owner_publication_authorized"])
        body=self.read()
        clips=body["tracks"]["children"][0]["children"]
        for index in (0,1):
            media=clips[index]["media_reference"]
            self.assertEqual(media["OTIO_SCHEMA"],"ExternalReference.1")
            self.assertEqual(media["target_url"],"media/"+self.expected_hash+".mp4")
            self.assertEqual(media["available_range"]["duration"]["value"],90)
            self.assertEqual(clips[index]["source_range"]["start_time"]["value"],index*30)
            self.assertEqual(clips[index]["source_range"]["duration"]["value"],30)
        self.assertEqual(clips[2]["media_reference"]["OTIO_SCHEMA"],"MissingReference.1")
        self.assertEqual((self.out/"media"/(self.expected_hash+".mp4")).read_bytes(),
                         self.media.read_bytes())
        with zipfile.ZipFile(self.out/"portable-cut.zip")as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read("timeline.otio"),(self.out/"timeline.otio").read_bytes())
            self.assertEqual(z.read("media/"+self.expected_hash+".mp4"),self.media.read_bytes())
            self.assertEqual(len(z.namelist()),5)
        manifest=json.loads((self.out/"bridge-manifest.json").read_text())
        self.assertEqual(len(manifest["bound_media"]),2)
        self.assertEqual(manifest["missing_scene_ids"],[ID(12)])
        self.assertFalse(manifest["nle_import_verified"])
        self.assertTrue(manifest["original_project_unchanged"])
        assert len(manifest["property_fidelity"])==12
        losses={item["property"]:item["status"]for item in manifest["property_fidelity"]}
        self.assertEqual(losses["scene_order"],"editable_otio")
        self.assertEqual(losses["native_object_geometry_hierarchy_text_masks"],"source_project_only")
        self.assertEqual(losses["publication_rights"],"not_granted")

    def test_unchanged_import_is_read_only_no_edit_claim(self):
        self.export()
        result=self.preview()
        self.assertEqual(result["native_change_proposals"],[])
        self.assertEqual(result["media_refs_verified"],2)
        self.assertEqual(result["missing_references_retained"],1)
        self.assertFalse(result["edit_imported"])
        self.assertTrue(result["owner_revision_commit_required"])
        self.assertEqual(result["semantic_native_roundtrip"],"NOT_SUPPORTED")

    def test_bounded_nle_reorder_and_end_trim_produces_existing_changes_only(self):
        self.export()
        edited=self.read()
        clips=edited["tracks"]["children"][0]["children"]
        edited["tracks"]["children"][0]["children"]=[clips[1],clips[0],clips[2]]
        clips[0]["source_range"]["duration"]=rtc(15,30)
        (self.out/"edited.otio").write_text(json.dumps(edited))
        result=self.preview("edited.otio")
        self.assertEqual(result["proposed_scene_order"],[ID(11),ID(10),ID(12)])
        self.assertEqual(result["native_change_proposals"],[
            {"type":"move_scene","scene_id":ID(11),"to_index":0},
            {"type":"set_scene_duration","scene_id":ID(10),"duration":{"num":"1","den":"2"}}
        ])

    def test_extended_scene_duration_is_never_a_native_end_trim(self):
        self.export()
        edited=self.read()
        clip=edited["tracks"]["children"][0]["children"][0]
        # Underlying media has 90 frames, but the owner authored 30 frames.
        # A 31-frame edit extends the scene; it cannot be called an end trim.
        clip["source_range"]["duration"]=rtc(31,30)
        (self.out/"extended.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"cannot extend native scene"):
            self.preview("extended.otio")

    def test_missing_binding_remains_missing_not_a_fabricated_clip(self):
        self.request["bindings"]=[]
        self.export()
        body=self.read()
        refs=[c["media_reference"]["OTIO_SCHEMA"]
              for c in body["tracks"]["children"][0]["children"]]
        self.assertEqual(refs,["MissingReference.1"]*3)
        self.assertEqual(self.preview()["media_refs_verified"],0)
        self.assertEqual(self.preview()["missing_references_retained"],3)

    def test_forged_owner_asset_digest_or_unlicensed_redistribution_fails(self):
        self.request["bindings"][0]["sha256"]="ac"*32
        with self.assertRaisesRegex(BridgeRefused,"asset digest"):self.export()
        self.request["bindings"][0]["sha256"]=self.expected_hash
        self.request["bindings"][0]["rights"]["redistributable"]=False
        with self.assertRaisesRegex(BridgeRefused,"redistribution rights"):self.export()
        self.assertFalse(self.out.exists())

    def test_changed_real_file_and_missing_declared_media_are_rejected(self):
        self.media.write_bytes(b"replacement media content not owner-approved")
        with self.assertRaisesRegex(BridgeRefused,"changed"):self.export()
        self.media.unlink()
        with self.assertRaisesRegex(BridgeRefused,"Missing or unsafe"):self.export()

    def test_stale_project_generation_or_owner_current_revision_refuses_import(self):
        self.export()
        updated=self.root/"newer-project.json"
        project=json.loads((self.out/"source-project.json").read_text())
        project["revision"]=8
        updated.write_text(json.dumps(project))
        with self.assertRaisesRegex(BridgeRefused,"edited after portable NLE export"):
            self.preview(current=updated)
        edited=self.read()
        edited["metadata"]["motionwright"]["generation"]=ID(99)
        (self.out/"other.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"exact original creative revision"):
            self.preview("other.otio")

    def test_unknown_nle_media_inpoint_and_unverified_target_are_rejected(self):
        self.export()
        edited=self.read()
        clip=edited["tracks"]["children"][0]["children"][0]
        clip["media_reference"]["target_url"]="../../private.mp4"
        (self.out/"relink.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"unverified source media"):
            self.preview("relink.otio")
        edited=self.read()
        edited["tracks"]["children"][0]["children"][0]["source_range"]["start_time"]=rtc(12,30)
        (self.out/"inpoint.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"source-offset/trim-in"):
            self.preview("inpoint.otio")

    def test_nle_cannot_install_effects_or_invent_scene_identity(self):
        self.export()
        edited=self.read()
        edited["tracks"]["children"][0]["children"][0]["effects"].append(
            {"OTIO_SCHEMA":"Effect.1","name":"unknown executable"})
        (self.out/"effect.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"NLE effects"):
            self.preview("effect.otio")
        edited=self.read()
        edited["tracks"]["children"][0]["children"][0]["metadata"]["motionwright"]["scene_id"]=ID(666)
        (self.out/"unknown.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"unique scene identity"):
            self.preview("unknown.otio")

    def test_tampered_available_range_cannot_fake_source_length(self):
        self.export()
        edited=self.read()
        edited["tracks"]["children"][0]["children"][0]["media_reference"]["available_range"]["duration"]["value"]=900.0
        (self.out/"oversized-available.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"actual verified media"):
            self.preview("oversized-available.otio")

    def test_authored_project_serde_time_strings_are_required_in_native_change_plan(self):
        self.export()
        edited=self.read()
        edited["tracks"]["children"][0]["children"][0]["source_range"]["duration"]["value"]=15.0
        (self.out/"half-time.otio").write_text(json.dumps(edited))
        impact=self.preview("half-time.otio")
        self.assertEqual(impact["native_change_proposals"],[
            {"type":"set_scene_duration","scene_id":ID(10),
             "duration":{"num":"1","den":"2"}}
        ])
        self.assertFalse(impact["edit_imported"])

    def test_nle_cannot_drop_explicit_native_property_losses(self):
        self.export()
        edited=self.read()
        edited["metadata"]["motionwright"]["property_fidelity"]=[]
        (self.out/"silent-loss.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"per-property fidelity"):
            self.preview("silent-loss.otio")

    def test_symlinked_media_is_never_admitted(self):
        media=self.src/"linked.mp4"
        try:
            media.symlink_to(self.media)
        except OSError as error:
            if os.name=="nt" and getattr(error,"winerror",None)==1314:
                self.skipTest("Windows lacks symlink privileges; Linux CI runs this source boundary")
            raise
        self.request["bindings"][0]["file"]="linked.mp4"
        with self.assertRaisesRegex(BridgeRefused,"Symlinked"):self.export()

    def test_duplicate_scene_binding_refuses_independently(self):
        self.request["bindings"][1]["scene_id"]=self.request["bindings"][0]["scene_id"]
        with self.assertRaisesRegex(BridgeRefused,"one original project scene"):self.export()

    def test_duplicate_nle_clips_cannot_create_extra_native_scenes(self):
        self.export()
        edited=self.read()
        edited["tracks"]["children"][0]["children"][1]=edited["tracks"]["children"][0]["children"][0]
        (self.out/"duplicate.otio").write_text(json.dumps(edited))
        with self.assertRaisesRegex(BridgeRefused,"unique scene identity"):
            self.preview("duplicate.otio")

    def test_duplicate_source_project_keys_cannot_override_owner_revision(self):
        source=self.src/"project.json"
        raw=source.read_text(encoding="utf-8")
        assert '"revision": 7,' in raw
        source.write_text(raw.replace('"revision": 7,',
                                      '"revision": 7, "revision": 8,',1))
        with self.assertRaisesRegex(BridgeRefused,"Duplicate JSON key"):
            self.export()

    def test_duplicate_otio_metadata_keys_cannot_rewrite_source_authority(self):
        self.export()
        edited=self.read()
        raw=json.dumps(edited)
        assert '"OTIO_SCHEMA": "Timeline.1"' in raw
        (self.out/"ambiguous.otio").write_text(raw.replace(
            '"OTIO_SCHEMA": "Timeline.1"',
            '"OTIO_SCHEMA": "Timeline.1", "OTIO_SCHEMA": "Timeline.1"',1))
        with self.assertRaisesRegex(BridgeRefused,"Duplicate JSON key"):
            self.preview("ambiguous.otio")

    def test_unknown_schema_and_existing_destination_do_not_overwrite(self):
        self.request["exec"]="curl invalid | sh"
        with self.assertRaisesRegex(BridgeRefused,"fields"):self.export()
        self.request.pop("exec")
        self.export()
        with self.assertRaisesRegex(BridgeRefused,"new destination"):self.export()


class BoundedKdenliveCutTests(unittest.TestCase):
    """RAW-shaped unit data, not an execution or receipt of a native editor."""
    setUp=PortableOtioTests.setUp
    save=PortableOtioTests.save
    export=PortableOtioTests.export
    read=PortableOtioTests.read

    def prepare(self,*,complete=True,schema="Clip.2"):
        if complete:
            self.request["bindings"].append({**self.request["bindings"][0],
                "scene_id":ID(12),"source_start_frame":60})
        self.export()
        self.raw=self.root/"raw"
        self.raw.mkdir()
        clips=[]
        # These records are explicit parser fixtures. No generated metadata,
        # NLE UUID authority or renderer output is asserted.
        for original in self.read()["tracks"]["children"][0]["children"]:
            ref=json.loads(json.dumps(original["media_reference"]))
            if ref["OTIO_SCHEMA"]=="MissingReference.1":continue
            ref["metadata"]={}
            ref["name"]=""
            ref["available_image_bounds"]=None
            ref["target_url"]=str(self.out/ref["target_url"])
            clip={"OTIO_SCHEMA":schema,"name":"native-original.mp4","metadata":{},
                  "enabled":True,"effects":[],"markers":[],
                  "source_range":original["source_range"]}
            if schema=="Clip.1":clip["media_reference"]=ref
            else:clip.update(media_references={"DEFAULT_MEDIA":ref},
                             active_media_reference_key="DEFAULT_MEDIA")
            clips.append(clip)
        self.received={"OTIO_SCHEMA":"Timeline.1","name":"","metadata":{},
            "global_start_time":None,"tracks":{"OTIO_SCHEMA":"Stack.1",
            "metadata":{},"source_range":None,"effects":[],"markers":[],"enabled":True,
            "children":[{"OTIO_SCHEMA":"Track.1","kind":"Video","name":"V1",
                "metadata":{},"source_range":trange(0,len(clips)*30,30),
                "effects":[],"markers":[],"enabled":True,"children":clips}]}}
        self.edited=json.loads(json.dumps(self.received))
        self.write_raw()

    def write_raw(self):
        for name,value in (("received.otio",self.received),("edited.otio",self.edited)):
            (self.raw/name).write_text(json.dumps(value,sort_keys=True),encoding="utf-8")

    def raw_request(self):
        digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
        return {"schema":KDENLIVE_REQUEST_SCHEMA,
            "capsule_manifest_sha256":digest(self.out/"bridge-manifest.json"),
            "received_otio":"received.otio","received_sha256":digest(self.raw/"received.otio"),
            "edited_otio":"edited.otio","edited_sha256":digest(self.raw/"edited.otio")}

    def check(self,*,roots=None,request=None,probe=None):
        with patch("bridge.file_probe",return_value=probe or VIDEO_INFO):
            return preview_kdenlive_cut(self.out,self.raw,request or self.raw_request(),
                self.out/"source-project.json",authorized_media_roots=roots or (self.out,self.raw))

    def children(self):return self.edited["tracks"]["children"][0]["children"]

    def ref(self,clip):
        return clip.get("media_reference")or clip["media_references"]["DEFAULT_MEDIA"]

    def assert_loss(self,code):
        self.write_raw()
        result=self.check()
        self.assertEqual(result["status"],"REJECTED_LOSS")
        self.assertEqual(result["rejection_code"],code)
        self.assertEqual(result["native_change_proposals"],[])
        self.assertFalse(result["edit_imported"])
        self.assertFalse(result["raw_files_rewritten"])
        return result

    def test_unique_physical_inpoints_recover_ids_without_raw_metadata_or_unique_names(self):
        self.prepare()
        before={p:p.read_bytes()for p in (*self.raw.glob("*.otio"),
            self.out/"source-project.json",self.out/"timeline.otio",self.out/"bridge-manifest.json")}
        self.assertNotIn(ID(10),(self.raw/"received.otio").read_text())
        result=self.check()
        self.assertEqual(result["proposed_scene_order"],[ID(10),ID(11),ID(12)])
        self.assertEqual(result["native_change_proposals"],[])
        self.assertEqual([m["original_inpoint"]for m in result["source_identity_mapping"]],[0,30,60])
        self.assertEqual(len({m["native_name_observed"]for m in result["source_identity_mapping"]}),1)
        self.assertEqual(result["nle_receiver_acceptance"],"NOT_VERIFIED")
        self.assertEqual(result["native_project_reopened"],"NOT_VERIFIED")
        self.assertFalse(result["native_final_frames_verified"])
        self.assertFalse(result["publication_approved"])
        self.assertTrue(any("immutable capsule"in loss for loss in result["loss_report"]))
        for path,data in before.items():self.assertEqual(path.read_bytes(),data)

    def test_reorder_and_end_trim_use_only_existing_changes_on_original_uuids(self):
        self.prepare()
        clips=self.children()
        self.edited["tracks"]["children"][0]["children"]=[clips[1],clips[0],clips[2]]
        clips[0]["source_range"]["duration"]=rtc(15,30)
        self.edited["tracks"]["children"][0]["source_range"]=trange(0,75,30)
        self.write_raw()
        result=self.check()
        self.assertEqual(result["native_change_proposals"],[
            {"type":"move_scene","scene_id":ID(11),"to_index":0},
            {"type":"set_scene_duration","scene_id":ID(10),"duration":{"num":"1","den":"2"}}])
        self.assertEqual(result["expected_project_revision"],7)
        self.assertFalse(result["source_media_relinked"])

    def test_clip1_has_a_separate_closed_shape(self):
        self.prepare(schema="Clip.1")
        self.assertEqual(self.check()["native_change_proposals"],[])
        self.children()[0]["media_references"]={"DEFAULT_MEDIA":self.ref(self.children()[0])}
        self.assert_loss("unsupported_schema")

    def test_same_bytes_relocation_is_separate_from_project_relink_and_native_receipt(self):
        self.prepare()
        source=Path(self.ref(self.children()[0])["target_url"])
        relocated=self.raw/"relocated.mp4"
        relocated.write_bytes(source.read_bytes())
        for clip in self.children():self.ref(clip)["target_url"]=relocated.as_uri()
        self.write_raw()
        result=self.check()
        self.assertTrue(result["same_source_bytes_relocated"])
        self.assertTrue(all(m["same_source_bytes_relocated"]for m in result["source_identity_mapping"]))
        self.assertFalse(result["source_media_relinked"])
        self.assertEqual(result["native_change_proposals"],[])
        with patch("bridge.file_probe",return_value=VIDEO_INFO):
            with self.assertRaises(BridgeRefused):
                import_preview(self.out,"../raw/edited.otio",self.out/"source-project.json")

    def test_original_missing_reference_omission_is_rejected_loss_and_never_filled(self):
        self.prepare(complete=False)
        result=self.assert_loss("original_missing_reference_not_receivable")
        self.assertEqual(result["original_missing_scene_ids"],[ID(12)])
        self.assertEqual(result["received_clip_count"],2)
        self.assertEqual(result["edited_clip_count"],2)
        self.assertEqual(self.read()["tracks"]["children"][0]["children"][2]
                         ["media_reference"]["OTIO_SCHEMA"],"MissingReference.1")

    def test_gap_and_explicit_missing_are_loss_not_placeholder_frames(self):
        self.prepare()
        original=json.loads(json.dumps(self.edited))
        self.children()[0]["OTIO_SCHEMA"]="Gap.1"
        self.assert_loss("native_gap_observed")
        self.edited=json.loads(json.dumps(original))
        self.children()[0]["media_references"]["DEFAULT_MEDIA"]={"OTIO_SCHEMA":"MissingReference.1"}
        self.assert_loss("native_missing_reference_observed")

    def test_omitted_duplicate_or_split_native_instances_are_refused(self):
        self.prepare()
        original=json.loads(json.dumps(self.edited))
        self.children().pop()
        self.edited["tracks"]["children"][0]["source_range"]=trange(0,60,30)
        self.assert_loss("scene_set_changed")
        self.edited=json.loads(json.dumps(original))
        self.children()[1]=json.loads(json.dumps(self.children()[0]))
        self.assert_loss("duplicate_native_instance")

    def test_same_media_same_original_inpoint_is_ambiguous_before_any_name_mapping(self):
        self.request["bindings"][1]["source_start_frame"]=0
        self.prepare()
        self.assert_loss("ambiguous_source_identity")

    def test_initial_native_observation_cannot_already_contain_the_edit(self):
        self.prepare()
        self.received["tracks"]["children"][0]["children"].reverse()
        self.assert_loss("initial_receipt_already_edited")

    def test_native_rename_is_rejected_even_when_physical_binding_is_unique(self):
        self.prepare()
        self.children()[0]["name"]="same source but renamed by editor"
        self.assert_loss("native_clip_renamed")

    def test_inpoint_slip_and_duration_extension_are_not_end_trims(self):
        self.prepare()
        original=json.loads(json.dumps(self.edited))
        self.children()[0]["source_range"]["start_time"]=rtc(1,30)
        self.assert_loss("unbound_source_or_inpoint")
        self.edited=json.loads(json.dumps(original))
        self.children()[0]["source_range"]["duration"]=rtc(31,30)
        self.edited["tracks"]["children"][0]["source_range"]=trange(0,91,30)
        self.assert_loss("native_end_trim_exceeded")

    def test_same_filename_foreign_bytes_never_acquire_original_identity(self):
        self.prepare()
        bad=self.raw/Path(self.ref(self.children()[0])["target_url"]).name
        bad.write_bytes(b"different source with the same name")
        self.ref(self.children()[0])["target_url"]=str(bad)
        self.assert_loss("unbound_source_or_inpoint")

    def test_relative_relink_stays_within_explicit_backend_roots(self):
        self.prepare()
        source=Path(self.ref(self.children()[0])["target_url"])
        (self.raw/"relocated.mp4").write_bytes(source.read_bytes())
        for clip in self.children():self.ref(clip)["target_url"]="relocated.mp4"
        self.write_raw()
        self.assertTrue(self.check()["same_source_bytes_relocated"])
        result=self.check(roots=(self.out,))
        self.assertEqual(result["rejection_code"],"unauthorized_media_root")

    def test_protocol_traversal_and_symlink_targets_do_not_gain_scope(self):
        self.prepare()
        ref=self.ref(self.children()[0])
        for target in ("https://example.invalid/a.mp4","file://server/a.mp4","../original.mp4"):
            ref["target_url"]=target
            self.assert_loss("unsafe_media_target")
        link=self.raw/"linked.mp4"
        try:link.symlink_to(self.out/"media"/(self.expected_hash+".mp4"))
        except OSError:self.skipTest("Platform did not permit the symlink fixture")
        ref["target_url"]=str(link)
        self.assert_loss("unsafe_media_target")

    def reparse_at(self, flagged):
        original=Path.lstat
        def observed(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path==flagged:
                # A junction/reparse point need not have POSIX symlink mode.
                return SimpleNamespace(st_mode=value.st_mode,
                    st_file_attributes=getattr(value,"st_file_attributes",0)|0x400)
            return value
        return patch.object(Path,"lstat",new=observed)

    def assert_reparse_root_rejected(self, flagged):
        request=self.raw_request()
        before={p:p.read_bytes()for p in (*self.raw.glob("*.otio"),
            self.out/"source-project.json",self.out/"timeline.otio",self.out/"bridge-manifest.json")}
        with self.reparse_at(flagged),patch("bridge.file_probe",return_value=VIDEO_INFO)as probe:
            self.assertFalse(flagged.is_symlink())
            with self.assertRaisesRegex(BridgeRefused,"^Invalid backend-authorized media root$"):
                preview_kdenlive_cut(self.out,self.raw,request,self.out/"source-project.json",
                    authorized_media_roots=(self.out,self.raw))
            probe.assert_not_called()
        for path,data in before.items():self.assertEqual(path.read_bytes(),data)

    def test_path_reparse_authorized_root_is_rejected_before_probe(self):
        self.prepare()
        self.assert_reparse_root_rejected(self.out)

    def test_path_reparse_ancestor_of_authorized_root_is_rejected_before_probe(self):
        self.prepare()
        self.assert_reparse_root_rejected(self.root)

    def assert_reparse_candidate_rejected(self, *, ancestor):
        self.prepare()
        parent=self.raw/"nested" if ancestor else self.raw
        if ancestor:parent.mkdir()
        source=Path(self.ref(self.children()[0])["target_url"])
        candidate=parent/"relocated.mp4"
        candidate.write_bytes(source.read_bytes())
        self.ref(self.children()[0])["target_url"]=str(candidate)
        self.children().reverse()
        self.write_raw()
        request=self.raw_request()
        before={p:p.read_bytes()for p in (*self.raw.glob("*.otio"),candidate,
            self.out/"source-project.json",self.out/"timeline.otio",self.out/"bridge-manifest.json")}
        flagged=parent if ancestor else candidate
        with self.reparse_at(flagged),patch("bridge.file_probe",return_value=VIDEO_INFO)as probe:
            self.assertFalse(flagged.is_symlink())
            result=preview_kdenlive_cut(self.out,self.raw,request,self.out/"source-project.json",
                authorized_media_roots=(self.out,self.raw))
        self.assertEqual(result["status"],"REJECTED_LOSS")
        self.assertEqual(result["rejection_code"],"unsafe_media_target")
        self.assertEqual(result["native_change_proposals"],[])
        self.assertFalse(result["edit_imported"])
        self.assertFalse(result["raw_files_rewritten"])
        self.assertNotIn(candidate,[call.args[0]for call in probe.call_args_list])
        for path,data in before.items():self.assertEqual(path.read_bytes(),data)

    def test_path_reparse_candidate_file_is_rejected_before_its_probe(self):
        self.assert_reparse_candidate_rejected(ancestor=False)

    def test_path_reparse_candidate_ancestor_is_rejected_before_its_probe(self):
        self.assert_reparse_candidate_rejected(ancestor=True)

    def test_path_resolved_target_must_remain_in_authorized_root(self):
        self.prepare()
        candidate=self.raw/"relocated.mp4"
        candidate.write_bytes(self.media.read_bytes())
        outside=self.src/"outside.mp4"
        outside.write_bytes(candidate.read_bytes())
        original=Path.resolve
        def resolve(path,*,strict=False):
            if path==candidate:
                self.assertTrue(strict)
                return outside
            return original(path,strict=strict)
        with patch.object(Path,"resolve",new=resolve):
            with self.assertRaises(BridgeRefused)as error:
                _nle_local_media(self.raw,str(candidate),(self.raw,))
        self.assertEqual(error.exception.code,"unauthorized_media_root")

    def test_path_missing_candidate_is_rejected_by_strict_admission(self):
        self.prepare()
        with self.assertRaises(BridgeRefused)as error:
            _nle_local_media(self.raw,"absent.mp4",(self.raw,))
        self.assertEqual(error.exception.code,"unsafe_media_target")

    def test_path_unavailable_lstat_is_a_bounded_refusal(self):
        self.prepare()
        original=Path.lstat
        def denied(path,*args,**kwargs):
            if path==self.media:raise PermissionError("private-owner-path-must-not-leak")
            return original(path,*args,**kwargs)
        with patch.object(Path,"lstat",new=denied):
            with self.assertRaises(BridgeRefused)as error:
                _nle_local_media(self.src,str(self.media),(self.src,))
        self.assertEqual(error.exception.code,"unsafe_media_target")
        self.assertNotIn("private-owner-path",str(error.exception))

    def test_metadata_effects_markers_audio_tracks_and_unknown_fields_are_closed(self):
        self.prepare()
        original=json.loads(json.dumps(self.edited))
        for field,value in (("metadata",{"motionwright":{"scene_id":ID(10)}}),
                            ("effects",[{"OTIO_SCHEMA":"Effect.1"}]),
                            ("markers",[{"name":"fake-id"}]),("enabled",False)):
            self.edited=json.loads(json.dumps(original))
            self.children()[0][field]=value
            self.assert_loss("unsupported_native_control")
        self.edited=json.loads(json.dumps(original))
        self.children()[0]["unknown_semantics"]={"gain":2}
        self.assert_loss("unsupported_schema")
        self.edited=json.loads(json.dumps(original))
        self.edited["tracks"]["children"][0]["kind"]="Audio"
        self.assert_loss("track_set_changed")

    def test_multireference_proxy_or_unrecognized_schema_is_not_flattened(self):
        self.prepare()
        original=json.loads(json.dumps(self.edited))
        self.children()[0]["media_references"]["PROXY"]=self.ref(self.children()[0]).copy()
        self.assert_loss("ambiguous_media_reference")
        self.edited=json.loads(json.dumps(original))
        self.children()[0]["OTIO_SCHEMA"]="Clip.99"
        self.assert_loss("unsupported_schema")

    def test_declared_available_range_and_composition_clock_are_not_trusted(self):
        self.prepare()
        original=json.loads(json.dumps(self.edited))
        self.ref(self.children()[0])["available_range"]=trange(0,91,30)
        self.assert_loss("native_media_range_changed")
        self.edited=json.loads(json.dumps(original))
        self.edited["tracks"]["children"][0]["source_range"]=trange(1,90,30)
        self.assert_loss("timeline_interval_changed")

    def test_untrusted_hash_map_verified_boolean_and_stale_project_are_rejected(self):
        self.prepare()
        for extra in ({"verified":True},{"scene_ids":[ID(10),ID(11),ID(12)]}):
            with self.assertRaises(BridgeRefused):self.check(request={**self.raw_request(),**extra})
        for field in ("capsule_manifest_sha256","received_sha256","edited_sha256"):
            with self.assertRaises(BridgeRefused):self.check(request={**self.raw_request(),field:"f"*64})
        current=self.out/"source-project.json"
        before=current.read_bytes()
        current.write_bytes(before+b"\n")
        with self.assertRaises(BridgeRefused):self.check()
        current.write_bytes(before)

    def test_raw_duplicate_keys_and_source_drift_are_rejected_without_rewriting(self):
        self.prepare()
        edited=self.raw/"edited.otio"
        original=edited.read_text()
        duplicated=original.replace('"OTIO_SCHEMA": "Timeline.1"',
            '"OTIO_SCHEMA": "Timeline.1", "OTIO_SCHEMA": "Timeline.1"',1)
        edited.write_text(duplicated)
        with self.assertRaises(BridgeRefused):self.check()
        self.assertEqual(edited.read_text(),duplicated)
        edited.write_text(original)
        timeline=self.out/"timeline.otio"
        timeline.write_bytes(timeline.read_bytes()+b"\n")
        with self.assertRaises(BridgeRefused):self.check()

    def test_raw_drift_after_bound_parse_cannot_emit_a_proposal(self):
        self.prepare()
        calls=0
        def change_after_source_preflight(path):
            nonlocal calls
            calls+=1
            if calls==4:
                edited=self.raw/"edited.otio"
                edited.write_bytes(edited.read_bytes()+b"\n")
            return VIDEO_INFO
        with patch("bridge.file_probe",side_effect=change_after_source_preflight):
            with self.assertRaisesRegex(BridgeRefused,"changed during bounded cut"):
                preview_kdenlive_cut(self.out,self.raw,self.raw_request(),
                    self.out/"source-project.json",authorized_media_roots=(self.out,self.raw))

    def test_boolean_global_clock_and_percent_encoded_control_path_are_not_accepted(self):
        self.prepare()
        self.edited["global_start_time"]={"OTIO_SCHEMA":"RationalTime.1","rate":30.0,"value":False}
        self.write_raw()
        with self.assertRaises(BridgeRefused):self.check()
        self.edited["global_start_time"]=None
        self.ref(self.children()[0])["target_url"]="file:///invalid%00.mp4"
        self.assert_loss("unsafe_media_target")


class BoundedFileDigestTests(unittest.TestCase):
    def test_source_growth_or_truncation_cannot_extend_the_admitted_read(self):
        with tempfile.TemporaryDirectory(prefix="mw-otio-bounded-hash-")as folder:
            path=Path(folder)/"source.mp4"
            path.write_bytes(b"abcd")
            for observed in (b"abc",b"abcde"):
                with patch.object(Path,"open",return_value=io.BytesIO(observed)):
                    with self.assertRaises(BridgeRefused):fingerprint(path,10)
            self.assertEqual(fingerprint(path,10),(hashlib.sha256(b"abcd").hexdigest(),4))


class VideoReadbackTests(unittest.TestCase):
    @staticmethod
    def header(**changes):
        stream={"codec_name":"h264","width":320,"height":180,
                "r_frame_rate":"30/1","avg_frame_rate":"30/1","nb_frames":"60"}
        stream.update(changes)
        return json.dumps({"streams":[stream]}).encode()

    @staticmethod
    def decoded(**changes):
        stream={"codec_name":"h264","width":320,"height":180,"nb_read_frames":"60"}
        stream.update(changes)
        return json.dumps({"streams":[stream]}).encode()

    def test_profile_limits_are_checked_before_whole_video_decode(self):
        for changes in ({"codec_name":"vp9"},{"width":4097},{"height":0},
                        {"r_frame_rate":"30000/1001"},{"avg_frame_rate":"29/1"},
                        {"nb_frames":"216001"},{"nb_frames":"N/A"},
                        {"nb_frames":float("inf")}):
            with self.subTest(changes=changes):
                with patch("bridge._probe_output",return_value=self.header(**changes))as probe:
                    with self.assertRaises(BridgeRefused):
                        file_probe(Path("private-owner-source.mp4"))
                    self.assertEqual(probe.call_count,1)

    def test_complete_count_is_observed_for_only_the_selected_video(self):
        with patch("bridge._probe_output",side_effect=[self.header(),self.decoded()])as probe:
            self.assertEqual(file_probe(Path("private-owner-source.mp4")),{
                "fps":30,"frames":60,"width":320,"height":180,"codec":"h264"})
        self.assertEqual(probe.call_count,2)
        for call in probe.call_args_list:
            args=call.args[0]
            self.assertEqual(args[args.index("-select_streams")+1],"v:0")
            self.assertEqual(args[args.index("-threads")+1],"1")
            self.assertEqual(args[args.index("-protocol_whitelist")+1],"file")
        self.assertNotIn("-count_frames",probe.call_args_list[0].args[0])
        self.assertIn("-count_frames",probe.call_args_list[1].args[0])
        self.assertIn("-err_detect",probe.call_args_list[1].args[0])
        self.assertEqual(probe.call_args_list[0].kwargs["deadline"],
                         probe.call_args_list[1].kwargs["deadline"])

    def test_zero_error_readback_still_must_match_the_declared_frame_count(self):
        for count in ("0","39","61","N/A",None,60.5,True,float("inf")):
            with self.subTest(count=count):
                with patch("bridge._probe_output",side_effect=[
                    self.header(),self.decoded(nb_read_frames=count)
                ]):
                    with self.assertRaises(BridgeRefused):
                        file_probe(Path("private-owner-source.mp4"))

    def test_decode_cannot_change_geometry_or_substitute_a_codec(self):
        for changes in ({"codec_name":"vp9"},{"width":640},{"height":360}):
            with self.subTest(changes=changes):
                with patch("bridge._probe_output",side_effect=[
                    self.header(),self.decoded(**changes)
                ]):
                    with self.assertRaisesRegex(BridgeRefused,"geometry or codec"):
                        file_probe(Path("private-owner-source.mp4"))

    def test_missing_or_ambiguous_decoded_stream_is_refused(self):
        for decoded in ({"streams":[]},{"streams":[{},{}]},
                        {"streams":"malformed"},{"streams":[{}]}):
            with self.subTest(decoded=decoded):
                with patch("bridge._probe_output",side_effect=[
                    self.header(),json.dumps(decoded).encode()
                ]):
                    with self.assertRaises(BridgeRefused):
                        file_probe(Path("private-owner-source.mp4"))

    def test_zero_exit_with_decoder_error_is_refused_without_private_diagnostics(self):
        with self.assertRaisesRegex(BridgeRefused,"decode error")as raised:
            _probe_output([sys.executable,"-I","-c",
                           "import sys; sys.stderr.write('PRIVATE_OWNER_PATH packet corrupt')"])
        self.assertNotIn("PRIVATE_OWNER_PATH",str(raised.exception))

    def test_failed_or_missing_decoder_does_not_leak_process_details(self):
        with self.assertRaises(BridgeRefused):
            _probe_output([sys.executable,"-I","-c","raise SystemExit(2)"])
        with patch("bridge.subprocess.Popen",side_effect=OSError("PRIVATE_OWNER_PATH")):
            with self.assertRaises(BridgeRefused)as raised:
                _probe_output(["ffprobe","PRIVATE_OWNER_PATH"])
        self.assertNotIn("PRIVATE_OWNER_PATH",str(raised.exception))

    def test_readback_timeout_kills_and_reaps_the_process(self):
        started=[]
        popen=subprocess.Popen
        def remember(*args,**kwargs):
            child=popen(*args,**kwargs)
            started.append(child)
            return child
        with patch("bridge.PROBE_TIMEOUT_SECONDS",0.05), \
             patch("bridge.subprocess.Popen",side_effect=remember):
            with self.assertRaisesRegex(BridgeRefused,"bounded"):
                _probe_output([sys.executable,"-I","-c","import time; time.sleep(2)"])
        self.assertEqual(len(started),1)
        self.assertIsNotNone(started[0].returncode)

    def test_expired_source_budget_does_not_start_another_decoder(self):
        with patch("bridge.subprocess.Popen")as start:
            with self.assertRaisesRegex(BridgeRefused,"bounded"):
                _probe_output(["ffprobe","private-owner-source.mp4"],deadline=0)
        start.assert_not_called()

    def test_stdout_and_stderr_are_bounded_while_the_process_is_alive(self):
        for stream in ("stdout","stderr"):
            with self.subTest(stream=stream):
                with patch("bridge.MAX_JSON",128):
                    with self.assertRaisesRegex(BridgeRefused,"bounded"):
                        _probe_output([sys.executable,"-I","-c",
                            f"import sys,time; sys.{stream}.write('x'*4096); "
                            f"sys.{stream}.flush(); time.sleep(2)"])


# ALab read-only causal controls: no network, ffprobe, decode or adoption.
import copy as _alab_copy
import struct as _alab_struct
import time as _alab_time
import zlib as _alab_zlib
import alab_readonly as _alab

def _alab_request_fixture():
    return {
        "schema": _alab.REQUEST_SCHEMA,
        "context": {"source_sha": "1"*40, "semwright_sha": "2"*40,
                    "run_id": "1234567", "run_attempt": 1},
        "source_tree": "3"*40, "job_name": "admitted-native-kdenlive-gui",
        "recipient_spki_sha256": "4"*64,
        "admission_reference": "source-test-only-external-reference",
        "archive": _alab_copy.deepcopy(_alab.ARCHIVE),
        "members": _alab_copy.deepcopy(_alab.MEMBERS),
        "limits": _alab_copy.deepcopy(_alab.LIMITS),
        "mode": "external-alab-readonly",
        "prohibited_effects": _alab_copy.deepcopy(_alab.PROHIBITED_EFFECTS),
    }

def _alab_deflate(data):
    encoder = _alab_zlib.compressobj(level=6, wbits=-15)
    return encoder.compress(data) + encoder.flush()

def _alab_member_fixture(data=b"synthetic media bytes for source controls",
                         flags=0, descriptor="signed"):
    row = _alab_copy.deepcopy(_alab.MEMBERS[0])
    name = row["name"].encode("ascii")
    compressed = _alab_deflate(data)
    crc = _alab_zlib.crc32(data) & 0xffffffff
    extra = b"\x55\x54\x01\x00\x00"
    row.update(bytes=len(data), compressed_bytes=len(compressed),
               crc32=f"{crc:08x}", sha256=hashlib.sha256(data).hexdigest(),
               local_header_offset=128, compression=8)
    header = _alab_struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, flags, 8, 0, 0,
        0 if flags & 8 else crc, 0 if flags & 8 else len(compressed),
        len(data), len(name), len(extra))
    trailer = b""
    if flags & 8:
        values = _alab_struct.pack("<III", crc, len(compressed), len(data))
        trailer = (_alab_struct.pack("<I", 0x08074b50)+values
                   if descriptor=="signed" else values)
    body = header + name + extra + compressed + trailer + b"PK\x03\x04"
    blob = b"\x00"*row["local_header_offset"] + body
    position = row["local_header_offset"]+30+len(name)+len(extra)+len(compressed)
    return row, data, compressed, blob, position

class _ALabWire:
    def __init__(self, blob=None, *, status=206, changed=None,
                 duplicate=None, short=False, extra=b""):
        self.blob, self.status = blob, status
        self.changed, self.duplicate = changed or {}, duplicate
        self.short, self.extra = short, extra
        self.calls, self.raw_headers, self.raw_bodies = [], [], []
    def __call__(self, start, length, deadline, on_header, on_body):
        self.calls.append((start, length))
        body = b"q"*length if self.blob is None else self.blob[start:start+length]
        if self.short:
            body = body[:-1]
        body += self.extra
        fields = [
            ("ETag", _alab.ARCHIVE["etag"]),
            ("Content-Range", f"bytes {start}-{start+length-1}/{_alab.ARCHIVE['bytes']}"),
            ("Content-Length", str(length)), ("Content-Encoding", "identity"),
        ]
        fields = [(key, self.changed.get(key,value)) for key,value in fields]
        if self.duplicate is not None:
            fields.append(self.duplicate)
        raw = (f"HTTP/1.1 {self.status} source-test\r\n".encode("ascii")
               + b"".join(key.encode("ascii")+b": "+value.encode("ascii")+b"\r\n"
                          for key,value in fields) + b"\r\n")
        self.raw_headers.append(raw)
        self.raw_bodies.append(body)
        on_header(raw)
        on_body(body)
        return {"status":self.status,"headers":fields}

class ExternalALabReadonlyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="alab-readonly-source-")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.counter=0
    def area(self):
        self.counter+=1
        result=self.root/f"case-{self.counter}"
        result.mkdir()
        return result
    def intake(self,wire,*,deadline=None):
        root=self.area()
        return root,_alab.Intake(root,_alab_time.monotonic()+10 if deadline is None else deadline,transport=wire)
    def assert_originals(self,root,record):
        for descriptor in record["originals"].values():
            raw=(root/descriptor["path"]).read_bytes()
            self.assertEqual(len(raw),descriptor["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),descriptor["sha256"])
    def observe_popen(self):
        real=subprocess.Popen
        children=[]
        def remember(*args,**kwargs):
            child=real(*args,**kwargs);children.append(child);return child
        def close_owned():
            for child in children:
                if child.poll() is None:child.kill()
                child.wait(timeout=3)
        self.addCleanup(close_owned)
        return children,remember

    def test_request_accepts_pinned_external_reference_without_mutation(self):
        request=_alab_request_fixture();before=_alab_copy.deepcopy(request)
        _alab.validate_request(request)
        self.assertEqual(request,before)
        self.assertTrue(all(v is False for v in request["prohibited_effects"].values()))
    def test_request_refuses_unknown_and_missing_keys(self):
        for change in ("extra","missing"):
            with self.subTest(change=change):
                r=_alab_request_fixture()
                if change=="extra":r["grant"]=True
                else:r.pop("recipient_spki_sha256")
                with self.assertRaises(_alab.Refused):_alab.validate_request(r)
    def test_request_rejects_bool_and_float_integer_aliases(self):
        for key,value in (("per_range_attempts",True),("per_range_attempts",1.0),("all_range_requests_max",27.0)):
            with self.subTest(key=key,value=value):
                r=_alab_request_fixture();r["limits"][key]=value
                with self.assertRaises(_alab.Refused):_alab.validate_request(r)
        r=_alab_request_fixture();r["members"][0]["compression"]=8.0
        with self.assertRaises(_alab.Refused):_alab.validate_request(r)
    def test_request_archive_and_member_roster_are_fixed(self):
        changes=(
            lambda r:r["archive"].update(url="https://example.invalid/a.zip"),
            lambda r:r["archive"].update(redirects_allowed=True),
            lambda r:r["members"][0].update(name="../replacement.mov"),
            lambda r:r["members"].reverse(),lambda r:r["members"].pop(),
        )
        for i,change in enumerate(changes):
            with self.subTest(change=i):
                r=_alab_request_fixture();change(r)
                with self.assertRaises(_alab.Refused):_alab.validate_request(r)
    def test_request_cannot_enable_effects_or_change_mode_or_job(self):
        changes=(
            lambda r:r["prohibited_effects"].update(decode=True),
            lambda r:r["prohibited_effects"].update(infer_source_origin=True),
            lambda r:r.update(mode="render"),lambda r:r.update(job_name="another-job"),
        )
        for i,change in enumerate(changes):
            with self.subTest(change=i):
                r=_alab_request_fixture();change(r)
                with self.assertRaises(_alab.Refused):_alab.validate_request(r)
    def test_request_context_and_external_references_have_exact_shapes(self):
        changes=(
            lambda r:r["context"].update(run_attempt=True),
            lambda r:r["context"].update(run_attempt=1.0),
            lambda r:r["context"].update(run_attempt=2),
            lambda r:r["context"].update(run_id=1234567),
            lambda r:r["context"].update(run_id="0"),
            lambda r:r["context"].update(source_sha="z"*40),
            lambda r:r.update(source_tree="3"*39),
            lambda r:r.update(recipient_spki_sha256="4"*63),
            lambda r:r.update(admission_reference=""),
        )
        for i,change in enumerate(changes):
            with self.subTest(change=i):
                r=_alab_request_fixture();change(r)
                with self.assertRaises(_alab.Refused):_alab.validate_request(r)
    def test_context_requires_exact_observed_ci_environment(self):
        r=_alab_request_fixture()
        env={"GITHUB_SHA":r["context"]["source_sha"],"GITHUB_RUN_ID":r["context"]["run_id"],
             "GITHUB_RUN_ATTEMPT":"1","GITHUB_JOB":r["job_name"]}
        _alab.validate_context(r,env)
        for key in env:
            with self.subTest(key=key):
                changed=dict(env);changed[key]="mismatch"
                with self.assertRaises(_alab.Refused):_alab.validate_context(r,changed)
        with self.assertRaises(_alab.Refused):_alab.validate_context(r,{})
    def test_json_rejects_nested_duplicate_keys(self):
        for raw in (b'{"x":1,"x":2}',b'{"context":{"run_id":"1","run_id":"2"}}'):
            with self.subTest(raw=raw):
                with self.assertRaises(_alab.Refused):_alab.strict_object(raw,"source-test")
    def test_json_rejects_nonobjects_nonfinite_and_bad_utf8(self):
        for raw in (b"[]",b"null",b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1e999}',b'{"x":"\xff"}'):
            with self.subTest(raw=raw):
                with self.assertRaises(_alab.Refused):_alab.strict_object(raw,"source-test")
    def test_raw_deflate_accepts_exact_crc_size_and_sha(self):
        row,data,compressed,_,_=_alab_member_fixture()
        self.assertEqual(_alab.inflate_exact(compressed,row),data)
    def test_raw_deflate_rejects_crc_or_sha_mismatch(self):
        row,_,compressed,_,_=_alab_member_fixture()
        for key,value in (("crc32","00000000"),("sha256","0"*64)):
            with self.subTest(key=key):
                changed=dict(row);changed[key]=value
                with self.assertRaises(_alab.Refused):_alab.inflate_exact(compressed,changed)
    def test_raw_deflate_rejects_wrong_size_and_uncompressed_budget(self):
        row,data,compressed,_,_=_alab_member_fixture()
        for size in (len(data)-1,len(data)+1,_alab.LIMITS["max_uncompressed_member_bytes"]+1):
            with self.subTest(size=size):
                with self.assertRaises(_alab.Refused):_alab.inflate_exact(compressed,dict(row,bytes=size))
    def test_raw_deflate_requires_real_eof(self):
        row,_,compressed,_,_=_alab_member_fixture();raw=compressed[:-1]
        with self.assertRaises(_alab.Refused):_alab.inflate_exact(raw,dict(row,compressed_bytes=len(raw)))
    def test_raw_deflate_refuses_trailing_bytes_or_second_stream(self):
        row,_,compressed,_,_=_alab_member_fixture()
        for suffix in (b"\x00",_alab_deflate(b"another stream")):
            with self.subTest(suffix=suffix):
                raw=compressed+suffix
                with self.assertRaises(_alab.Refused):_alab.inflate_exact(raw,dict(row,compressed_bytes=len(raw)))
    def test_range_retains_exact_original_headers_and_body(self):
        wire=_ALabWire();root,intake=self.intake(wire)
        self.assertEqual(intake.fetch(100,17),b"q"*17)
        retained=[p.read_bytes() for p in root.rglob("*") if p.is_file()]
        self.assertIn(wire.raw_headers[0],retained);self.assertIn(wire.raw_bodies[0],retained)
    def test_range_refuses_wrong_status_etag_range_encoding_or_total(self):
        variants=({"status":200},{"status":302},{"changed":{"ETag":'"changed"'}},
            {"changed":{"Content-Range":"bytes 1-17/573418844"}},
            {"changed":{"Content-Range":"bytes 0-16/573418845"}},
            {"changed":{"Content-Encoding":"gzip"}},{"changed":{"Content-Length":"18"}})
        for variant in variants:
            with self.subTest(variant=variant):
                wire=_ALabWire(**variant);_,intake=self.intake(wire)
                with self.assertRaises(_alab.Refused):intake.fetch(0,17)
                self.assertEqual(len(wire.calls),1)
    def test_range_refuses_ambiguous_duplicate_critical_headers(self):
        wire=_ALabWire(duplicate=("etag",_alab.ARCHIVE["etag"]));_,intake=self.intake(wire)
        with self.assertRaises(_alab.Refused):intake.fetch(0,17)
        self.assertEqual(len(wire.calls),1)
    def test_range_refuses_short_or_overlong_actual_body(self):
        for variant in ({"short":True},{"extra":b"x"}):
            with self.subTest(variant=variant):
                wire=_ALabWire(**variant);_,intake=self.intake(wire)
                with self.assertRaises(_alab.Refused):intake.fetch(0,17)
                self.assertEqual(len(wire.calls),1)
    def test_range_refuses_invalid_size_or_expired_deadline_before_transport(self):
        for start,length in ((-1,1),(True,1),(0.0,1),(0,0),(0,-1),(0,True),(0,1.0),(0,_alab.LIMITS["one_request_max"]+1)):
            with self.subTest(start=start,length=length):
                wire=_ALabWire();_,intake=self.intake(wire)
                with self.assertRaises(_alab.Refused):intake.fetch(start,length)
                self.assertEqual(wire.calls,[])
        wire=_ALabWire();_,intake=self.intake(wire,deadline=_alab_time.monotonic()-1)
        with self.assertRaises(_alab.Refused):intake.fetch(0,1)
        self.assertEqual(wire.calls,[])
    def test_range_count_budget_stops_before_twenty_eighth_request(self):
        wire=_ALabWire();_,intake=self.intake(wire);limit=_alab.LIMITS["all_range_requests_max"]
        for index in range(limit):intake.fetch(index,1)
        with self.assertRaises(_alab.Refused):intake.fetch(limit,1)
        self.assertEqual(len(wire.calls),limit)
    def test_range_bytes_budget_is_shared_across_requests(self):
        wire=_ALabWire();_,intake=self.intake(wire)
        chunk=_alab.LIMITS["one_request_max"];total=_alab.LIMITS["range_bytes_budget"]
        self.assertEqual(total%chunk,0)
        for index in range(total//chunk):intake.fetch(index*chunk,chunk)
        with self.assertRaises(_alab.Refused):intake.fetch(total,1)
        self.assertEqual(len(wire.calls),total//chunk)
    def test_member_accepts_fixed_header_and_both_data_descriptor_encodings(self):
        for flags,encoding in ((0,"signed"),(8,"signed"),(8,"unsigned")):
            with self.subTest(flags=flags,encoding=encoding):
                row,data,_,blob,_=_alab_member_fixture(flags=flags,descriptor=encoding)
                wire=_ALabWire(blob);root,intake=self.intake(wire);d=intake.member(row)
                self.assertEqual((root/row["retained_path"]).read_bytes(),data)
                self.assertEqual(d["bytes"],len(data));self.assertEqual(d["sha256"],row["sha256"])
                self.assertEqual(d["crc32"],row["crc32"])
    def test_member_rejects_header_identity_method_sizes_or_unflagged_zeros(self):
        row,_,_,original,_=_alab_member_fixture();offset=row["local_header_offset"]
        mutations=((0,b"BAD!"),(6,_alab_struct.pack("<H",1)),(8,_alab_struct.pack("<H",0)),
            (14,_alab_struct.pack("<I",0)),(18,_alab_struct.pack("<I",row["compressed_bytes"]+1)),
            (22,_alab_struct.pack("<I",row["bytes"]+1)),(30,b"X"))
        for relative,replacement in mutations:
            with self.subTest(relative=relative):
                changed=bytearray(original);changed[offset+relative:offset+relative+len(replacement)]=replacement
                wire=_ALabWire(bytes(changed));root,intake=self.intake(wire)
                with self.assertRaises(_alab.Refused):intake.member(row)
                self.assertFalse((root/row["retained_path"]).exists())
        flagged,_,_,original,_=_alab_member_fixture(flags=8)
        changed=bytearray(original);i=flagged["local_header_offset"]+6;changed[i:i+2]=b"\x00\x00"
        _,intake=self.intake(_ALabWire(bytes(changed)))
        with self.assertRaises(_alab.Refused):intake.member(flagged)
    def test_member_rejects_wrong_data_descriptor_crc_or_sizes(self):
        row,_,_,original,offset=_alab_member_fixture(flags=8)
        for relative,value in ((4,0),(8,row["compressed_bytes"]+1),(12,row["bytes"]+1)):
            with self.subTest(relative=relative):
                changed=bytearray(original);changed[offset+relative:offset+relative+4]=_alab_struct.pack("<I",value)
                root,intake=self.intake(_ALabWire(bytes(changed)))
                with self.assertRaises(_alab.Refused):intake.member(row)
                self.assertFalse((root/row["retained_path"]).exists())
    def test_command_retains_binary_streams_and_original_terminal(self):
        root=self.area()
        script="import os; os.write(1,b'\\x00\\xffok\\n'); os.write(2,b'\\xfeerr\\x00')"
        record=_alab.run_command([sys.executable,"-I","-B","-c",script],root,"binary",_alab_time.monotonic()+5)
        self.assertEqual(record["stdout"],b"\x00\xffok\n");self.assertEqual(record["stderr"],b"\xfeerr\x00")
        self.assertIs(type(record["terminal"]["exit_code"]),int);self.assertEqual(record["terminal"]["exit_code"],0)
        self.assert_originals(root,record)
        terminal=json.loads((root/record["originals"]["terminal"]["path"]).read_bytes())
        self.assertEqual(terminal["exit_code"],0)
    def test_command_overflow_stops_the_owned_child_and_marks_partial_capture(self):
        root=self.area();children,remember=self.observe_popen()
        script="import os,time; os.write(1,b'x'*65536); time.sleep(3)"
        with patch.object(_alab.subprocess,"Popen",side_effect=remember):
            with self.assertRaises(_alab.Refused) as raised:
                _alab.run_command([sys.executable,"-I","-B","-c",script],root,"overflow",_alab_time.monotonic()+5,limit=128)
        self.assertNotIsInstance(raised.exception,_alab.Unknown)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
        record=raised.exception.record;self.assertTrue(record["terminal"]["overflow"])
        self.assertFalse(record["terminal"]["stdout"]["complete"])
        self.assertLessEqual(record["terminal"]["stdout"]["retained_bytes"],128)
        self.assertGreater(record["terminal"]["stdout"]["observed_bytes"],128)
        self.assert_originals(root,record)
    def test_command_timeout_remains_unknown_without_leaving_owned_child(self):
        root=self.area();children,remember=self.observe_popen()
        script="import os,time; os.write(1,b'partial\\x00'); os.write(2,b'waiting\\n'); time.sleep(3)"
        with patch.object(_alab.subprocess,"Popen",side_effect=remember):
            with self.assertRaises(_alab.Unknown) as raised:
                _alab.run_command([sys.executable,"-I","-B","-c",script],root,"timeout",_alab_time.monotonic()+0.6)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
        record=raised.exception.record;self.assertTrue(record["terminal"]["timed_out"]);self.assert_originals(root,record)
    def test_primary_nonzero_survives_terminal_custody_failure(self):
        class Completed:
            def __init__(self):
                self.stdout=io.BytesIO(b"\xfforiginal stdout");self.stderr=io.BytesIO(b"original failure\n")
                self.returncode=7;self.pid=None
            def poll(self):return self.returncode
            def wait(self,timeout=None):return self.returncode
            def kill(self):raise AssertionError("A completed process must not be killed")
        root=self.area();original_write=_alab.write_new
        def custody_failure(owner,relative,raw):
            if "terminal" in str(relative):raise OSError("source-test terminal custody failure")
            return original_write(owner,relative,raw)
        with patch.object(_alab.subprocess,"Popen",return_value=Completed()),patch.object(_alab,"write_new",side_effect=custody_failure):
            with self.assertRaises(_alab.Refused) as raised:
                _alab.run_command(["inert-test-double"],root,"nonzero",_alab_time.monotonic()+5)
        self.assertNotIsInstance(raised.exception,_alab.Unknown)
        record=raised.exception.record;self.assertEqual(record["terminal"]["exit_code"],7)
        self.assertEqual(record["stderr"],b"original failure\n");self.assertTrue(record["custody_errors"])

# Orchestration controls: five small synthetic members and original process stubs.
import socket as _alab_test_socket

def _alab_small_roster():
    rows=[];archive=bytearray()
    for index,original in enumerate(_alab.MEMBERS):
        row=_alab_copy.deepcopy(original)
        data=f"SYNTHETIC SOURCE TEST {index}: {row['name']}\n".encode("utf-8")
        compressed=_alab_deflate(data);crc=_alab_zlib.crc32(data)&0xffffffff;name=row["name"].encode("ascii")
        row.update(bytes=len(data),compressed_bytes=len(compressed),crc32=f"{crc:08x}",
                   sha256=hashlib.sha256(data).hexdigest(),local_header_offset=len(archive),compression=8)
        archive.extend(_alab_struct.pack("<IHHHHHIIIHH",0x04034b50,20,0,8,0,0,crc,len(compressed),len(data),len(name),0))
        archive.extend(name);archive.extend(compressed);rows.append(row)
    return rows,bytes(archive)

def _alab_stub_command_record(root,index,argv,stdout,stderr=b"",*,exit_code=0,execution_status="TERMINAL_OBSERVED"):
    terminal={"spawned":True,"exit_code":exit_code,"execution_status":execution_status,
        "timed_out":execution_status=="UNKNOWN_NO_RETRY","overflow":False,"forced":False,
        "stdout":{"observed_bytes":len(stdout),"retained_bytes":len(stdout),"complete":execution_status=="TERMINAL_OBSERVED"},
        "stderr":{"observed_bytes":len(stderr),"retained_bytes":len(stderr),"complete":execution_status=="TERMINAL_OBSERVED"}}
    prefix=f"source-test-probe-{index}"
    originals={
        "argv":_alab.write_new(root,f"{prefix}.argv.original.json",(json.dumps(argv)+"\n").encode()),
        "stdout":_alab.write_new(root,f"{prefix}.stdout.original.bin",stdout),
        "stderr":_alab.write_new(root,f"{prefix}.stderr.original.bin",stderr),
        "terminal":_alab.write_new(root,f"{prefix}.terminal.original.json",(json.dumps(terminal,sort_keys=True)+"\n").encode()),
    }
    return {"stdout":stdout,"stderr":stderr,"terminal":terminal,"originals":originals,"custody_errors":[]}

class _ALabBooleanTerminalChild:
    def __init__(self):
        self.stdout=io.BytesIO(b"\xffretained source-test stdout")
        self.stderr=io.BytesIO(b"source-test uncertain terminal\n");self.returncode=True;self.pid=None
    def poll(self):return self.returncode
    def wait(self,timeout=None):return self.returncode
    def kill(self):raise AssertionError("A completed test double must not be killed")

class ExternalALabOrchestrationTests(unittest.TestCase):
    setUp=ExternalALabReadonlyTests.setUp
    area=ExternalALabReadonlyTests.area
    def assert_no_acceptance(self,result):
        self.assertEqual(result["source_origin"],"NOT_OBSERVED")
        for key in ["decoded_frames_verified","candidate_admitted","human_acceptance","release_authorized","automatic_retry"]:
            self.assertIs(result[key],False)
    def scenario(self,variant):
        owner=self.area();request_path=owner/"request.json";evidence_root=owner/"evidence";tool=owner/"ffprobe"
        tool.write_bytes(b"SYNTHETIC TOOL IDENTITY; NEVER EXECUTED\n");tool.chmod(0o700)
        rows,archive=_alab_small_roster();wire=_ALabWire(archive);calls=[];records=[]
        malformed=b"\xffALAB-METADATA-CORRUPT\x00"
        def command_runner(argv,root,label,deadline,**kwargs):
            index=len(calls);calls.append(list(argv))
            if index==0:stdout=b"ffprobe version 7.1.1 source-test-only\n"
            else:
                frames=52 if index==1 else 182;timecode="00:00:41:18" if index==1 else "00:00:42:00"
                document={"streams":[
                    {"index":0,"codec_type":"video","codec_name":"h264","width":1920,"height":1080,
                     "nb_frames":str(frames),"r_frame_rate":"24/1","avg_frame_rate":"24/1",
                     "time_base":"1/2400","start_pts":0,"duration_ts":frames*100,"tags":{}},
                    {"index":1,"codec_type":"data","codec_name":"unknown","codec_tag_string":"tmcd",
                     "time_base":"1/2400","start_pts":0,"duration_ts":frames*100,"tags":{"timecode":timecode}}],
                    "format":{"start_time":"0.000000","duration":str(frames/24),"tags":{"timecode":timecode}}}
                stdout=json.dumps(document).encode()
            exit_code=0;execution_status="TERMINAL_OBSERVED";stderr=b""
            if variant=="invalid-json" and index==1:stdout=malformed
            elif variant=="version-nonzero" and index==0:exit_code=7;stderr=b"source-test version failure\n"
            elif variant=="version-unknown" and index==0:
                exit_code=None;execution_status="UNKNOWN_NO_RETRY";stderr=b"source-test unknown version terminal\n"
            elif variant=="boolean-terminal" and index==0:exit_code=True
            record=_alab_stub_command_record(root,index,argv,stdout,stderr,exit_code=exit_code,execution_status=execution_status)
            records.append(record)
            if variant=="tool-drift" and index==0:tool.write_bytes(b"CHANGED SYNTHETIC TOOL IDENTITY\n")
            if variant=="request-drift" and index==0:
                with request_path.open("ab") as changed:changed.write(b" \n")
            if variant=="version-unknown" and index==0:
                error=_alab.Unknown("source-test unknown version terminal");error.record=record;raise error
            return record
        with patch.object(_alab,"MEMBERS",rows):
            request=_alab_request_fixture();request_path.write_text(json.dumps(request),encoding="utf-8")
            environment={"GITHUB_SHA":request["context"]["source_sha"],"GITHUB_RUN_ID":request["context"]["run_id"],
                         "GITHUB_RUN_ATTEMPT":"1","GITHUB_JOB":request["job_name"]}
            result=_alab.external_alab_readonly(request_path,evidence_root,tool,transport=wire,command_runner=command_runner,environ=environment)
        retained=json.loads((evidence_root/"result.json").read_bytes())
        self.assertEqual(retained["status"],result["status"]);self.assert_no_acceptance(result)
        return result,evidence_root,calls,records,malformed

    def test_invalid_metadata_preserves_originals_before_parse_and_stops(self):
        result,root,calls,records,malformed=self.scenario("invalid-json")
        self.assertEqual(result["status"],"FAILED");self.assertEqual(len(calls),2)
        record=records[1];self.assertEqual(record["terminal"]["exit_code"],0)
        self.assertEqual((root/record["originals"]["stdout"]["path"]).read_bytes(),malformed)
        self.assertEqual((root/record["originals"]["stderr"]["path"]).read_bytes(),b"")
        terminal=json.loads((root/record["originals"]["terminal"]["path"]).read_bytes())
        self.assertIs(type(terminal["exit_code"]),int);self.assertEqual(terminal["exit_code"],0)
    def test_version_unknown_or_nonzero_never_starts_a_media_probe(self):
        for variant,expected in (("version-unknown","UNKNOWN_NO_RETRY"),("version-nonzero","FAILED")):
            with self.subTest(variant=variant):
                result,root,calls,records,_=self.scenario(variant)
                self.assertEqual(result["status"],expected);self.assertEqual(len(calls),1);self.assertEqual(len(records),1)
                for d in records[0]["originals"].values():
                    raw=(root/d["path"]).read_bytes();self.assertEqual(len(raw),d["bytes"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(),d["sha256"])
    def test_tool_identity_change_stops_before_first_media_probe(self):
        result,_,calls,_,_=self.scenario("tool-drift");self.assertEqual(result["status"],"FAILED");self.assertEqual(len(calls),1)
    def test_request_identity_change_cannot_publish_metadata_success(self):
        result,_,calls,_,_=self.scenario("request-drift");self.assertEqual(result["status"],"FAILED")
        self.assertGreaterEqual(len(calls),1);self.assertLessEqual(len(calls),3)
    def test_orchestration_boolean_exit_code_is_unknown_and_not_retried(self):
        result,root,calls,records,_=self.scenario("boolean-terminal")
        self.assertEqual(result["status"],"UNKNOWN_NO_RETRY");self.assertEqual(len(calls),1)
        terminal=json.loads((root/records[0]["originals"]["terminal"]["path"]).read_bytes())
        self.assertIs(terminal["exit_code"],True)
    def test_run_command_boolean_terminal_is_unknown_with_null_exit_code(self):
        root=self.area()
        with patch.object(_alab.subprocess,"Popen",return_value=_ALabBooleanTerminalChild()):
            with self.assertRaises(_alab.Unknown) as raised:
                _alab.run_command(["source-test-double"],root,"boolean-terminal",_alab_time.monotonic()+5)
        record=raised.exception.record;self.assertEqual(record["terminal"]["execution_status"],"UNKNOWN_NO_RETRY")
        self.assertIsNone(record["terminal"]["exit_code"])
        terminal=json.loads((root/record["originals"]["terminal"]["path"]).read_bytes());self.assertIsNone(terminal["exit_code"])
    def test_unknown_primary_survives_failure_to_preserve_terminal(self):
        root=self.area();original_write=_alab.write_new
        def fail_terminal(owner,relative,raw):
            if "terminal" in str(relative):raise OSError("source-test secondary terminal custody failure")
            return original_write(owner,relative,raw)
        with patch.object(_alab.subprocess,"Popen",return_value=_ALabBooleanTerminalChild()),patch.object(_alab,"write_new",side_effect=fail_terminal):
            with self.assertRaises(_alab.Unknown) as raised:
                _alab.run_command(["source-test-double"],root,"unknown-custody",_alab_time.monotonic()+5)
        record=raised.exception.record;self.assertEqual(record["terminal"]["execution_status"],"UNKNOWN_NO_RETRY")
        self.assertIsNone(record["terminal"]["exit_code"]);self.assertTrue(record["custody_errors"])
        self.assertEqual(record["stderr"],b"source-test uncertain terminal\n")
    def test_https_read_error_closes_response_and_connection_preserving_primary(self):
        closures=[];connections=[]
        response_wire=(b"HTTP/1.1 206 Partial Content\r\n"+b"ETag: "+_alab.ARCHIVE["etag"].encode()+b"\r\n"
                       +b"Content-Range: bytes 0-15/573418844\r\nContent-Length: 16\r\nContent-Encoding: identity\r\n\r\n0123456789abcdef")
        class BrokenBody(io.BytesIO):
            def read(self,size=-1):raise OSError("source-test primary HTTP body read failure")
            def close(self):
                if not self.closed:
                    closures.append("response");super().close();raise RuntimeError("source-test secondary response close")
                super().close()
        class FakeSocket:
            def __init__(self):self.file=BrokenBody(response_wire)
            def makefile(self,mode,*args,**kwargs):return self.file
            def settimeout(self,timeout):pass
            def shutdown(self,*args):pass
            def close(self):pass
        class FakeConnection:
            def __init__(self,host,port=None,timeout=None,**kwargs):
                self.host=host;self.port=port;self.timeout=timeout;self.sock=FakeSocket()
                self.response_class=None;self.response=None;self.closed=False;connections.append(self)
            def connect(self):pass
            def request(self,method,path,body=None,headers=None,**kwargs):self.request_observed=(method,path,headers)
            def getresponse(self):
                self.response=self.response_class(self.sock,method="GET");self.response.begin();return self.response
            def close(self):
                if not self.closed:
                    self.closed=True;self.sock=None;closures.append("connection");raise RuntimeError("source-test secondary connection close")
        raw_headers=[];raw_bodies=[]
        with patch.object(_alab.http.client,"HTTPSConnection",FakeConnection),patch.object(_alab_test_socket,"getaddrinfo",side_effect=AssertionError("No DNS is permitted in this source test")):
            with self.assertRaisesRegex(OSError,"primary HTTP body read failure"):
                _alab._http_transport(0,16,_alab_time.monotonic()+2,raw_headers.append,raw_bodies.append)
        self.assertEqual(len(connections),1);self.assertTrue(connections[0].closed)
        self.assertIn("response",closures);self.assertIn("connection",closures)
        self.assertTrue(b"".join(raw_headers).startswith(b"HTTP/1.1 206 Partial Content"))
        self.assertEqual(raw_bodies,[])


if __name__=="__main__":unittest.main()
