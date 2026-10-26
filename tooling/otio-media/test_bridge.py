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
import bridge as _retained_bridge
from bridge import (
    BridgeRefused, export_portable, file_probe, import_preview, rtc, trange, SCHEMA,
    _probe_output, preview_kdenlive_cut, KDENLIVE_REQUEST_SCHEMA, fingerprint, _nle_local_media,
    inspect_external_otio,
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
        def change_after_source_preflight(path,*,original_bytes=None):
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
            # Instrument the actual shared held-FD read, not Path.open,
            # which is deliberately no longer the source-data reader.
            import mlt_official_runtime_intake as intake
            for observed in (b"abc",b"abcde"):
                with patch.object(intake.os,"read",side_effect=[observed,b""]):
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
                        file_probe(Path("private-owner-source.mp4"),original_bytes=b"unit protocol source")
                    self.assertEqual(probe.call_count,1)

    def test_complete_count_is_observed_for_only_the_selected_video(self):
        with patch("bridge._probe_output",side_effect=[self.header(),self.decoded()])as probe:
            self.assertEqual(file_probe(Path("private-owner-source.mp4"),original_bytes=b"unit protocol source"),{
                "fps":30,"frames":60,"width":320,"height":180,"codec":"h264"})
        self.assertEqual(probe.call_count,2)
        for call in probe.call_args_list:
            args=call.args[0]
            self.assertEqual(args[args.index("-select_streams")+1],"v:0")
            self.assertEqual(args[args.index("-threads")+1],"1")
            self.assertEqual(args[args.index("-protocol_whitelist")+1],"pipe")
            self.assertEqual(args[-1],"pipe:0")
            self.assertEqual(call.kwargs["input_bytes"],b"unit protocol source")
        self.assertIn("-count_packets",probe.call_args_list[0].args[0])
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
                        file_probe(Path("private-owner-source.mp4"),original_bytes=b"unit protocol source")

    def test_decode_cannot_change_geometry_or_substitute_a_codec(self):
        for changes in ({"codec_name":"vp9"},{"width":640},{"height":360}):
            with self.subTest(changes=changes):
                with patch("bridge._probe_output",side_effect=[
                    self.header(),self.decoded(**changes)
                ]):
                    with self.assertRaisesRegex(BridgeRefused,"geometry or codec"):
                        file_probe(Path("private-owner-source.mp4"),original_bytes=b"unit protocol source")

    def test_missing_or_ambiguous_decoded_stream_is_refused(self):
        for decoded in ({"streams":[]},{"streams":[{},{}]},
                        {"streams":"malformed"},{"streams":[{}]}):
            with self.subTest(decoded=decoded):
                with patch("bridge._probe_output",side_effect=[
                    self.header(),json.dumps(decoded).encode()
                ]):
                    with self.assertRaises(BridgeRefused):
                        file_probe(Path("private-owner-source.mp4"),original_bytes=b"unit protocol source")

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
                capture={}
                with patch("bridge.MAX_JSON",128):
                    with self.assertRaisesRegex(BridgeRefused,"bounded"):
                        _probe_output([sys.executable,"-I","-c",
                            f"import sys,time; sys.{stream}.write('x'*131072); "
                            f"sys.{stream}.flush(); time.sleep(2)"],capture=capture)
                self.assertTrue(capture["terminal"]["output_budget_exceeded"])
                self.assertLessEqual(len(capture[stream]),129 if stream=="stdout" else 65537)


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


class ExternalOtioSourceInspectionTests(unittest.TestCase):
    """Synthetic declarations only: no external media or NLE acceptance."""

    @staticmethod
    def source_document():
        clips=[]
        for start,duration in ((1002,52),(1008,182)):
            clips.append({
                "OTIO_SCHEMA":"Clip.2","name":"Repeated external label",
                "enabled":True,"effects":[],"markers":[],
                "metadata":{"foreign":{"id":"not-a-native-scene-id"}},
                "source_range":trange(start,duration,24),
                "active_media_reference_key":"DEFAULT_MEDIA",
                "media_references":{"DEFAULT_MEDIA":{
                    "OTIO_SCHEMA":"ExternalReference.1",
                    "target_url":f"media/source-{start}.mov",
                    "available_range":None,"available_image_bounds":None,
                    "metadata":{},
                }},
            })
        return {
            "OTIO_SCHEMA":"Timeline.1","name":"Synthetic external timeline",
            "metadata":{"foreign":{"id":"not-a-project-grant"}},
            "global_start_time":rtc(86400,24),
            "tracks":{"OTIO_SCHEMA":"Stack.1","children":[{
                "OTIO_SCHEMA":"Track.1","kind":"Video","children":clips,
            },{
                "OTIO_SCHEMA":"Track.1","kind":"Audio","children":[],
                "metadata":{"opaque_mixer":{"gain":0.7}},
            }]},
        }

    def inspect(self,document=None,selection=None):
        document=self.source_document()if document is None else document
        raw=json.dumps(document,separators=(",",":")).encode("utf-8")
        pointers=selection if selection is not None else [
            "/tracks/children/0/children/0","/tracks/children/0/children/1"]
        return inspect_external_otio(raw,hashlib.sha256(raw).hexdigest(),pointers)

    @staticmethod
    def codes(report):
        return {row["code"]for row in report["blockers"]}

    def test_original_ranges_pointers_and_global_clock_do_not_become_file_offsets(self):
        report=self.inspect(selection=[
            "/tracks/children/0/children/1","/tracks/children/0/children/0"])
        self.assertEqual(report["status"],"SOURCE_INSPECTED_NOT_ADOPTED")
        self.assertEqual(report["source"]["declared_global_start_time"],rtc(86400,24))
        self.assertEqual([row["pointer"]for row in report["observations"]],[
            "/tracks/children/0/children/0","/tracks/children/0/children/1"])
        self.assertEqual([row["declared_source_range"]for row in report["observations"]],
                         [trange(1002,52,24),trange(1008,182,24)])
        self.assertTrue(all(row["range_supported_by_existing_cut_parser"]
                            for row in report["observations"]))
        self.assertEqual(report["observations"][0]["name"],
                         report["observations"][1]["name"])
        self.assertFalse(report["verified_media"])
        self.assertEqual(report["media_origin"],"unknown")
        self.assertIn("media_origin_unknown",self.codes(report))
        self.assertNotIn("native_change_proposals",report)
        self.assertNotIn("scene_id",json.dumps(report))
        self.assertNotIn("file_offset",json.dumps(report))
        self.assertNotIn("handles",json.dumps(report))

    def test_source_hash_is_checked_before_parsing_and_bytes_are_not_mutable_input(self):
        raw=json.dumps(self.source_document()).encode()
        pointers=["/tracks/children/0/children/0"]
        with patch("bridge.strict_json")as parse:
            with self.assertRaisesRegex(BridgeRefused,"digest changed"):
                inspect_external_otio(raw,"0"*64,pointers)
        parse.assert_not_called()
        for source in (bytearray(raw),memoryview(raw),raw.decode(),b""):
            with self.subTest(kind=type(source).__name__):
                with self.assertRaisesRegex(BridgeRefused,"immutable source bytes"):
                    inspect_external_otio(source,hashlib.sha256(raw).hexdigest(),pointers)
        with patch("bridge.MAX_JSON",len(raw)-1):
            with self.assertRaisesRegex(BridgeRefused,"bounded immutable"):
                inspect_external_otio(raw,hashlib.sha256(raw).hexdigest(),pointers)
        for invalid in (b"\xff{}",raw.decode().encode("utf-16")):
            with self.subTest(encoding=invalid[:4]),patch("bridge.strict_json")as parse:
                with self.assertRaisesRegex(BridgeRefused,"source is not valid UTF-8"):
                    inspect_external_otio(invalid,hashlib.sha256(invalid).hexdigest(),pointers)
            parse.assert_not_called()

    def test_inspection_never_opens_sources_follows_urls_or_starts_a_media_process(self):
        document=self.source_document()
        clips=document["tracks"]["children"][0]["children"]
        targets=["https://example.invalid/private.mov","../../outside.mov",
                 "file:///private.mov",r"C:\private\owner.mov","media/%2e%2e/secret.mov"]
        for target in targets:
            clips[0]["media_references"]["DEFAULT_MEDIA"]["target_url"]=target
            raw=json.dumps(document).encode()
            original=bytes(raw)
            with self.subTest(target=target), \
                 patch("bridge.file_probe",side_effect=AssertionError("media probe"))as probe, \
                 patch("bridge.subprocess.Popen",side_effect=AssertionError("process"))as spawn, \
                 patch.object(Path,"open",side_effect=AssertionError("file open"))as opened:
                report=inspect_external_otio(raw,hashlib.sha256(raw).hexdigest(),
                                            ["/tracks/children/0/children/0"])
            probe.assert_not_called()
            spawn.assert_not_called()
            opened.assert_not_called()
            self.assertEqual(raw,original)
            self.assertEqual(report["source"]["sha256"],hashlib.sha256(original).hexdigest())
            self.assertEqual(report["observations"][0]["active_reference"]["target_url"],target)
            self.assertIn("media_requires_authorized_observation",self.codes(report))
            self.assertFalse(report["verified_media"])

    def test_metadata_unknown_controls_and_unselected_tracks_stay_addressed_and_opaque(self):
        document=self.source_document()
        document["opaque/key~"]={"command":"not executed"}
        clip=document["tracks"]["children"][0]["children"][0]
        clip["metadata"]={"motionwright":{"scene_id":ID(900),"generation":ID(901)}}
        clip["effects"]=[{"OTIO_SCHEMA":"Effect.1","name":"Foreign effect"}]
        document["tracks"]["children"][0]["source_range"]=trange(4,234,24)
        report=self.inspect(document,["/tracks/children/0/children/0"])
        opaque={row["pointer"]:row["reason"]for row in report["opaque_regions"]}
        self.assertIn("/opaque~1key~0",opaque)
        self.assertIn("/tracks/children/0/children/0/metadata",opaque)
        self.assertIn("/tracks/children/0/children/0/effects",opaque)
        self.assertIn("/tracks/children/0/source_range",opaque)
        self.assertEqual(opaque["/tracks/children/1"],"unselected_track")
        self.assertEqual(opaque["/tracks/children/0/children"],"unselected_occurrences")
        self.assertIn("composition_range_requires_adoption_review",self.codes(report))
        self.assertNotIn(ID(900),json.dumps(report))
        self.assertNotIn(ID(901),json.dumps(report))

    def test_active_clip2_reference_is_exact_and_never_falls_back_to_the_first(self):
        document=self.source_document()
        clip=document["tracks"]["children"][0]["children"][0]
        clip["media_references"]["OTHER"]={
            "OTIO_SCHEMA":"ExternalReference.1","target_url":"media/selected.mov",
            "available_range":None}
        clip["active_media_reference_key"]="OTHER"
        report=self.inspect(document,["/tracks/children/0/children/0"])
        observed=report["observations"][0]["active_reference"]
        self.assertEqual(observed["target_url"],"media/selected.mov")
        self.assertTrue(observed["pointer"].endswith("/media_references/OTHER"))
        self.assertTrue(any(row["reason"]=="inactive_media_reference"
                            for row in report["opaque_regions"]))
        clip["active_media_reference_key"]="ABSENT"
        report=self.inspect(document,["/tracks/children/0/children/0"])
        self.assertIsNone(report["observations"][0]["active_reference"])
        self.assertIn("active_reference_unavailable",self.codes(report))

    def test_clip1_missing_reference_remains_missing_without_replacement_media(self):
        document=self.source_document()
        clip=document["tracks"]["children"][0]["children"][0]
        clip["OTIO_SCHEMA"]="Clip.1"
        clip.pop("media_references")
        clip.pop("active_media_reference_key")
        clip["media_reference"]={"OTIO_SCHEMA":"MissingReference.1","available_range":None}
        report=self.inspect(document,["/tracks/children/0/children/0"])
        ref=report["observations"][0]["active_reference"]
        self.assertEqual(ref["schema"],"MissingReference.1")
        self.assertIsNone(ref["target_url"])
        self.assertIn("missing_reference_observed",self.codes(report))
        self.assertFalse(report["verified_media"])

    def test_declared_available_range_is_not_a_measured_origin_or_handle_count(self):
        document=self.source_document()
        ref=document["tracks"]["children"][0]["children"][0]["media_references"]["DEFAULT_MEDIA"]
        ref["available_range"]=trange(1000,2000,24)
        report=self.inspect(document,["/tracks/children/0/children/0"])
        self.assertEqual(report["observations"][0]["active_reference"]["declared_available_range"],
                         trange(1000,2000,24))
        self.assertEqual(report["media_origin"],"unknown")
        self.assertFalse(report["verified_media"])
        self.assertNotIn("handles",json.dumps(report))

    def test_fractional_or_unadmitted_declared_ranges_are_preserved_without_rounding(self):
        for rate,value in ((23.976,52),(24,52.5),(0,52),(-24,52),(24,10**400)):
            with self.subTest(rate=rate,value=value):
                document=self.source_document()
                interval=document["tracks"]["children"][0]["children"][0]["source_range"]
                interval["start_time"]["rate"]=rate
                interval["duration"].update(rate=rate,value=value)
                report=self.inspect(document,["/tracks/children/0/children/0"])
                observation=report["observations"][0]
                self.assertEqual(observation["declared_source_range"],interval)
                self.assertFalse(observation["range_supported_by_existing_cut_parser"])
                self.assertIn("range_outside_existing_cut_profile",self.codes(report))

    def test_unreadable_range_and_controls_do_not_silently_become_supported_defaults(self):
        for replacement in (None,{},{"OTIO_SCHEMA":"UnknownRange.1"},trange(0,1,24)):
            document=self.source_document()
            clip=document["tracks"]["children"][0]["children"][0]
            clip["source_range"]=replacement
            if replacement and replacement.get("OTIO_SCHEMA")=="TimeRange.1":
                replacement["duration"]["value"]=True
            clip["effects"]={}
            clip["enabled"]=False
            report=self.inspect(document,["/tracks/children/0/children/0"])
            observation=report["observations"][0]
            self.assertIsNone(observation["declared_source_range"])
            self.assertFalse(observation["range_supported_by_existing_cut_parser"])
            self.assertIn("occurrence_or_composition_not_enabled",self.codes(report))
            self.assertTrue(any(row["pointer"].endswith("/effects")
                                for row in report["opaque_regions"]))

    def test_gaps_transitions_and_nested_compositions_are_not_fabricated_clips(self):
        for schema in ("Gap.1","Transition.1","Stack.1"):
            with self.subTest(schema=schema):
                document=self.source_document()
                document["tracks"]["children"][0]["children"][0]["OTIO_SCHEMA"]=schema
                report=self.inspect(document,["/tracks/children/0/children/0"])
                self.assertEqual(report["observations"][0]["schema"],schema)
                self.assertIsNone(report["observations"][0]["active_reference"])
                self.assertIn("occurrence_outside_clip_profile",self.codes(report))

    def test_canonical_occurrence_selection_cannot_alias_names_or_escape_the_tree(self):
        selections=[[],["/tracks/children/0/children/0"]*2,
                    ["/tracks/children/00/children/0"],["/tracks/children/0/children/-1"],
                    ["/tracks/children/0/children/0/name"],["/tracks/children/9/children/0"],
                    ["/tracks/children/0/children/999"],[ID(10)],[True]]
        for selection in selections:
            with self.subTest(selection=selection):
                with self.assertRaises(BridgeRefused):
                    self.inspect(selection=selection)
        too_many=[f"/tracks/children/0/children/{index}"for index in range(33)]
        with patch("bridge.strict_json")as parse:
            with self.assertRaisesRegex(BridgeRefused,"selection must contain bounded"):
                self.inspect(selection=too_many)
        parse.assert_not_called()

    def test_duplicate_json_and_nonfinite_values_even_in_opaque_metadata_are_refused(self):
        raw=json.dumps(self.source_document()).encode()
        duplicate=raw.replace(b'"OTIO_SCHEMA": "Timeline.1"',
                              b'"OTIO_SCHEMA": "Timeline.1", "OTIO_SCHEMA": "Timeline.1"',1)
        self.assertNotEqual(raw,duplicate)
        with self.assertRaisesRegex(BridgeRefused,"Duplicate JSON key"):
            inspect_external_otio(duplicate,hashlib.sha256(duplicate).hexdigest(),
                                  ["/tracks/children/0/children/0"])
        for value in (float("nan"),float("inf"),-float("inf")):
            with self.subTest(value=value):
                document=self.source_document()
                document["metadata"]["uninterpreted_number"]=value
                with self.assertRaisesRegex(BridgeRefused,"non-finite JSON number"):
                    self.inspect(document)

    def test_depth_node_and_diagnostic_budgets_fail_before_unbounded_projection(self):
        document=self.source_document()
        nested={}
        document["metadata"]=nested
        for _ in range(70):
            nested["next"]={}
            nested=nested["next"]
        with self.assertRaisesRegex(BridgeRefused,"structural budget"):
            self.inspect(document)
        with patch("bridge._EXTERNAL_MAX_NODES",10):
            with self.assertRaisesRegex(BridgeRefused,"structural budget"):
                self.inspect()
        with patch("bridge._EXTERNAL_MAX_ROWS",1):
            with self.assertRaisesRegex(BridgeRefused,"inventory exceeds"):
                self.inspect()

    def test_audio_track_and_unknown_reference_schemas_stay_blocked(self):
        document=self.source_document()
        track=document["tracks"]["children"][0]
        track["kind"]="Audio"
        track["children"][0]["media_references"]["DEFAULT_MEDIA"]["OTIO_SCHEMA"]="GeneratorReference.1"
        report=self.inspect(document,["/tracks/children/0/children/0"])
        self.assertIn("selected_track_outside_video_cut_profile",self.codes(report))
        self.assertIn("media_reference_outside_profile",self.codes(report))
        self.assertIsNone(report["observations"][0]["active_reference"]["target_url"])
        self.assertFalse(report["verified_media"])


class RetainedAlabObservationTests(unittest.TestCase):
    """Synthetic contract originals only; no native decode, ALab adoption or NLE receipt.

    Internal-validator pins below deliberately identify synthetic unit bytes.
    The production entry remains fixed to the independently admitted originals.
    """
    @staticmethod
    def clone(value):
        return json.loads(json.dumps(value))

    @staticmethod
    def rational_pair(num,den=1):
        value=_retained_bridge.Fraction(num,den)
        return {"num":value.numerator,"den":value.denominator}

    def fixture(self):
        source=ExternalOtioSourceInspectionTests.source_document()
        raw_media=(b"unit retained source MOV one",b"unit retained source MOV two")
        pins=[];containers=[];observations=[]
        for index,original in enumerate(_retained_bridge._ALAB_RETAINED_MEDIA):
            pointer,target,_,_,origin,count=original
            blob=raw_media[index]
            pins.append((pointer,target,len(blob),hashlib.sha256(blob).hexdigest(),origin,count))
            clip=source["tracks"]["children"][0]["children"][index]
            clip["media_references"]["DEFAULT_MEDIA"]["target_url"]=target
            clip["metadata"]["AAF"]={"StartTime":0}
            timecode=("00:00:41:18","00:00:42:00")[index]
            container={
                "format":"mov,mp4,m4a,3gp,3g2,mj2",
                "duration_microseconds":round(count*1000000/24),"start_time_microseconds":0,
                "metadata":{"major_brand":"qt  ","compatible_brands":"qt  ","timecode":timecode},
                "streams":[{"index":0,"type":"video","time_base":{"num":1,"den":2400},
                    "start_time":0,"duration":count*100,"container_declared_frames":count,
                    "metadata":{"handler_name":"Video Handler"},
                    "codec":{"name":"h264","profile":"Main","width":1920,"height":1080,
                             "sample_rate":{"unavailable":"AttributeError"},
                             "channels":{"unavailable":"AttributeError"}}},
                    {"index":1,"type":"data","time_base":{"num":1,"den":2400},
                     "start_time":0,"duration":count*100,"container_declared_frames":1,
                     "metadata":{"handler_name":"Timecode Handler","timecode":timecode}}]}
            containers.append(container)
            observations.append({
                "source_path":"synthetic-unit-only/"+Path(target).name,
                "source_sha256":pins[-1][3],"source_bytes":len(blob),
                "decoded_frame_count":count,
                "decoded_pts":[{"index":frame,"pts":frame*100,
                    "time_base":{"num":1,"den":2400},
                    "presentation_time":self.rational_pair(frame,24)}for frame in range(count)],
                "pixel_samples":[{"index":frame,"presentation_time":self.rational_pair(frame,24),
                    "width":1920,"height":1080,"rgb24_bytes":1920*1080*3,
                    "rgb24_sha256":hashlib.sha256(f"unit sample {index}:{frame}".encode()).hexdigest(),
                    "source_pixel_format":"yuv420p","nonconstant_rgb":True}
                    for frame in (0,count//2,count-1)],
                "source_origin":"NOT_OBSERVED",
                "container_metadata_original":{"path":f"synthetic-container-{index}.json",
                                                "bytes":0,"sha256":"0"*64},
                "physical_observation":{"width":1920,"height":1080,"codec":"h264",
                    "average_rate":{"num":24,"den":1},"container_declared_frames":count,
                    "declared_count_matches_decoded":True,"first_presentation_time":{"num":0,"den":1},
                    "last_presentation_time":self.rational_pair(count-1,24),
                    "cadence_matches_average_rate":True,"timecode_fields":"Synthetic original-shaped unit field",
                    "source_origin":"NOT_OBSERVED"}})
        readback={"schema":"motionwright.a4.alab-native-readback/1",
            "status":"PASS_TWO_RETAINED_ORIGINALS_NATIVE_DECODE","media":observations,
            "new_render":False,"nle_opened":False,"canonical_scene_ids_created":False,
            "assets_adopted":False,"unknown_replayed":False,"human_acceptance":False,
            "formal_acceptance":False,"source_origin":"NOT_OBSERVED",
            "runtime":{"av_version":"synthetic-contract-fixture-not-runtime-evidence"},
            "inputs_stable":True,"elapsed_seconds":0.0,"ended_at_utc":"unit-only"}
        return {"source":source,"readback":readback,"containers":containers,
                "media_pins":tuple(pins),"raw_media":raw_media}

    def encode(self,data,*,bind_containers=True):
        value=self.clone({k:v for k,v in data.items()if k not in ("media_pins","raw_media")})
        dump=lambda v:json.dumps(v,separators=(",",":"),sort_keys=True).encode()
        containers=tuple(dump(v)for v in value["containers"])
        if bind_containers:
            for row,blob in zip(value["readback"]["media"],containers):
                row["container_metadata_original"]["bytes"]=len(blob)
                row["container_metadata_original"]["sha256"]=hashlib.sha256(blob).hexdigest()
        originals=(dump(value["source"]),dump(value["readback"]),*containers)
        pins=tuple((role,len(blob),hashlib.sha256(blob).hexdigest())for role,blob in zip(
            ("otio","readback","container0","container1"),originals))
        return originals,pins,data["media_pins"]

    def admit(self,data=None):
        return _retained_bridge._admit_alab_retained_originals(*self.encode(data or self.fixture()))

    def portable(self,data=None):
        data=data or self.fixture()
        self.source_notices={role:("Unit original notice "+role).encode()
                             for role,_,_,_ in _retained_bridge._ALAB_SOURCE_NOTICES}
        notice_pins=tuple((role,name,len(self.source_notices[role]),
                           hashlib.sha256(self.source_notices[role]).hexdigest())
                          for role,name,_,_ in _retained_bridge._ALAB_SOURCE_NOTICES)
        notice_patch=patch.object(_retained_bridge,"_ALAB_SOURCE_NOTICES",notice_pins)
        notice_patch.start();self.addCleanup(notice_patch.stop)
        temp=tempfile.TemporaryDirectory(prefix="mw-alab-retained-unit-")
        self.addCleanup(temp.cleanup)
        root=Path(temp.name);owner=root/"owner";owner.mkdir()
        scenes=[];assets=[];clips=[];bindings=[]
        start=_retained_bridge.Fraction(0)
        for index,pin in enumerate(data["media_pins"]):
            pointer,target,size,digest,origin,count=pin
            duration=_retained_bridge.Fraction(count,24)
            scene={"id":ID(10+index),"name":f"Synthetic selected occurrence {index}",
                "start":{"num":str(start.numerator),"den":str(start.denominator)},
                "duration":{"num":str(duration.numerator),"den":str(duration.denominator)}}
            start+=duration;scenes.append(scene)
            assets.append({"id":ID(50+index),"name":f"Synthetic MOV {index}",
                "media_type":"video/quicktime","content_sha256":digest,
                "source_revision":"unit-contract-only"})
            file=Path(target).name;(owner/file).write_bytes(data["raw_media"][index])
            bindings.append({"scene_id":scene["id"],"asset_id":assets[-1]["id"],
                "file":file,"sha256":digest,"source_start_frame":0,
                "rights":{"owner":"unit fixture only","license":"synthetic contract fixture",
                          "authorized":True,"redistributable":True}})
            clips.append({"OTIO_SCHEMA":"Clip.1","enabled":True,"effects":[],"markers":[],
                "media_reference":{"OTIO_SCHEMA":"MissingReference.1","available_range":None,
                                   "metadata":{},"name":None},
                "source_range":trange(0,count,24),"name":scene["name"],
                "metadata":{"motionwright":{"scene_id":scene["id"]}}})
        project={"id":ID(1),"generation":ID(2),"revision":7,"title":"Synthetic canonical cut",
                 "scenes":scenes,"assets":assets}
        timeline={"OTIO_SCHEMA":"Timeline.1","name":"Synthetic canonical export",
            "metadata":{"motionwright":{"project_id":ID(1),"generation":ID(2),"revision":7,
                "export_profile":"conservative-cut-v1","loss_report":["Unit source remains native."]}},
            "tracks":{"OTIO_SCHEMA":"Stack.1","children":[{"OTIO_SCHEMA":"Track.1",
                "kind":"Video","children":clips}]}}
        request={"schema":SCHEMA,"project":"project.json","otio":"baseline.otio",
                 "bindings":bindings,"owner_review":{"reviewer":"unit-only",
                                                       "synthetic_source_allowed":True}}
        (owner/"project.json").write_text(json.dumps(project),encoding="utf-8")
        (owner/"baseline.otio").write_text(json.dumps(timeline),encoding="utf-8")
        return root,owner,root/"capsule",project,request,self.admit(data),data

    def test_retained_join_is_new_derivation_and_never_promotes_historical_readback(self):
        data=self.fixture();raw,pins,media=self.encode(data);before=tuple(raw)
        with patch("bridge.file_probe",side_effect=AssertionError("no new decoder")), \
             patch("bridge.subprocess.Popen",side_effect=AssertionError("no new native child")):
            admitted=_retained_bridge._admit_alab_retained_originals(raw,pins,media)
        result=admitted.descriptor()
        self.assertEqual(raw,before)
        self.assertEqual(result["status"],"RETAINED_MEDIA_ORIGIN_DERIVED_NOT_ADOPTED")
        self.assertEqual([row["source_origin_frame"]for row in result["selected"]],[1002,1008])
        self.assertEqual([row["source_start_frame"]for row in result["selected"]],[0,0])
        self.assertEqual([row["actual_media_frames"]for row in result["selected"]],[52,182])
        self.assertEqual(result["source_inspection"]["status"],"SOURCE_INSPECTED_NOT_ADOPTED")
        self.assertEqual(result["source_inspection"]["media_origin"],"unknown")
        self.assertTrue(result["source_inspection"]["opaque_regions"])
        self.assertEqual(result["historical_source_origin"],"NOT_OBSERVED")
        for key in ("historical_readback_rewritten","new_decode","new_render","assets_adopted",
                    "canonical_scene_ids_created","nle_opened","formal_acceptance"):
            self.assertIs(result[key],False)
        self.assertEqual(result["later_edit_proposal"],{
            "occurrence":"/tracks/children/0/children/1","from_frames":182,"to_frames":170,"applied":False})
        self.assertEqual([row["rgb_samples_retained"]for row in result["selected"]],[3,3])
        self.assertFalse(any(row["all_decoded_frame_pixel_hashes"]for row in result["selected"]))

    def test_fixed_benchmark_entry_refuses_self_admitted_synthetic_or_member00_sources(self):
        raw,_,_=self.encode(self.fixture())
        with patch("bridge.inspect_external_otio",side_effect=AssertionError("parse before fixed pin"))as parse:
            with self.assertRaises(BridgeRefused):
                _retained_bridge.admit_alab_retained_originals(*raw)
            parse.assert_not_called()
        self.assertEqual(_retained_bridge._ALAB_RETAINED_ORIGINALS[0],(
            "otio",218949,"6c90cbc64000e5de1d6450c95b1f5845db2862c053fa2591d02d370505f3bd28"))
        self.assertNotEqual(_retained_bridge._ALAB_RETAINED_ORIGINALS[0][2],
                           "1ef8cbab507adaeb2d83476925c187812351009558cc181383730b4de80be185")

    def test_all_four_byte_identities_are_verified_before_any_parser(self):
        raw,pins,media=self.encode(self.fixture())
        for index in range(4):
            with self.subTest(original=index):
                changed=list(raw);changed[index]=raw[index]+b" "
                with patch("bridge.inspect_external_otio",side_effect=AssertionError("early source parse"))as source_parse, \
                     patch("bridge.strict_json",side_effect=AssertionError("early JSON parse"))as json_parse:
                    with self.assertRaises(BridgeRefused):
                        _retained_bridge._admit_alab_retained_originals(tuple(changed),pins,media)
                    source_parse.assert_not_called();json_parse.assert_not_called()

    def test_container_roles_and_native_source_identity_cannot_be_swapped(self):
        data=self.fixture();raw,pins,media=self.encode(data)
        bound=self.clone(data["readback"])
        bound["media"]=[json.loads(raw[1])["media"][1],json.loads(raw[1])["media"][0]]
        for scenario in ("containers","media","source_sha","source_size"):
            with self.subTest(scenario=scenario):
                item=self.fixture()
                if scenario=="containers":
                    item["containers"].reverse()
                elif scenario=="media":item["readback"]=bound
                elif scenario=="source_sha":item["readback"]["media"][0]["source_sha256"]="0"*64
                else:item["readback"]["media"][0]["source_bytes"]+=1
                with self.assertRaises(BridgeRefused):self.admit(item)

    def test_complete_retained_clock_rejects_missing_duplicate_reordered_or_forged_frames(self):
        mutations=(
            ("missing",lambda r:r["decoded_pts"].pop()),
            ("duplicate",lambda r:r["decoded_pts"].__setitem__(1,self.clone(r["decoded_pts"][0]))),
            ("reordered",lambda r:r["decoded_pts"].reverse()),
            ("index_bool",lambda r:r["decoded_pts"][0].__setitem__("index",False)),
            ("pts_shift",lambda r:r["decoded_pts"][1].__setitem__("pts",101)),
            ("timebase",lambda r:r["decoded_pts"][1].__setitem__("time_base",{"num":1,"den":1200})),
            ("presentation",lambda r:r["decoded_pts"][1].__setitem__("presentation_time",{"num":1,"den":25})),
            ("zero_den",lambda r:r["decoded_pts"][1].__setitem__("presentation_time",{"num":1,"den":0})),
            ("unreduced",lambda r:r["decoded_pts"][1].__setitem__("presentation_time",{"num":2,"den":48})),
            ("string_num",lambda r:r["decoded_pts"][1].__setitem__("presentation_time",{"num":"1","den":24})),
            ("count",lambda r:r.__setitem__("decoded_frame_count",51)),
            ("declared_mismatch",lambda r:r["physical_observation"].__setitem__("declared_count_matches_decoded",False)),
            ("rate",lambda r:r["physical_observation"].__setitem__("average_rate",{"num":30,"den":1})),
            ("last_pts",lambda r:r["physical_observation"].__setitem__("last_presentation_time",{"num":52,"den":24})),
        )
        for name,mutate in mutations:
            with self.subTest(scenario=name):
                data=self.fixture();mutate(data["readback"]["media"][0])
                with self.assertRaises(BridgeRefused):self.admit(data)

    def test_container_profile_requires_exact_quicktime_h264_clock_and_geometry(self):
        mutations=(
            ("brand",lambda c:c["metadata"].__setitem__("major_brand","isom")),
            ("compatible",lambda c:c["metadata"].__setitem__("compatible_brands","isom")),
            ("format",lambda c:c.__setitem__("format","matroska,webm")),
            ("codec",lambda c:c["streams"][0]["codec"].__setitem__("name","hevc")),
            ("profile",lambda c:c["streams"][0]["codec"].__setitem__("profile","High")),
            ("width",lambda c:c["streams"][0]["codec"].__setitem__("width",1280)),
            ("stream_start",lambda c:c["streams"][0].__setitem__("start_time",100)),
            ("container_start",lambda c:c.__setitem__("start_time_microseconds",1)),
            ("stream_duration",lambda c:c["streams"][0].__setitem__("duration",5201)),
            ("declared_frames",lambda c:c["streams"][0].__setitem__("container_declared_frames",51)),
            ("data_handler",lambda c:c["streams"][1]["metadata"].__setitem__("handler_name","Other")),
            ("extra_stream",lambda c:c["streams"].append(self.clone(c["streams"][1]))),
        )
        for name,mutate in mutations:
            with self.subTest(scenario=name):
                data=self.fixture();mutate(data["containers"][0])
                with self.assertRaises(BridgeRefused):self.admit(data)

    def test_origin_uses_matching_ndf24_timecodes_not_global_clock_or_aaf_start(self):
        for value in ("00:00:41;18","00:00:41:24","24:00:00:00","00:60:00:00",
                      "00:00:41:17","0:00:41:18",None):
            with self.subTest(timecode=value):
                data=self.fixture()
                data["containers"][0]["metadata"]["timecode"]=value
                data["containers"][0]["streams"][1]["metadata"]["timecode"]=value
                with self.assertRaises(BridgeRefused):self.admit(data)
        data=self.fixture()
        data["containers"][0]["streams"][1]["metadata"]["timecode"]="00:00:42:00"
        with self.assertRaises(BridgeRefused):self.admit(data)
        data=self.fixture()
        data["source"]["global_start_time"]=rtc(123456,24)
        data["source"]["tracks"]["children"][0]["children"][0]["metadata"]["AAF"]["StartTime"]=999
        result=self.admit(data).descriptor()
        self.assertEqual([row["source_origin_frame"]for row in result["selected"]],[1002,1008])
        self.assertEqual([row["source_start_frame"]for row in result["selected"]],[0,0])
        self.assertEqual(result["source_inspection"]["source"]["declared_global_start_time"],rtc(123456,24))

    def test_selected_clip2_reference_and_interval_never_fall_back_to_foreign_metadata(self):
        for scenario in ("clip1","active","target","start","duration","available"):
            with self.subTest(scenario=scenario):
                data=self.fixture();clip=data["source"]["tracks"]["children"][0]["children"][0]
                if scenario=="clip1":clip["OTIO_SCHEMA"]="Clip.1"
                elif scenario=="active":clip["active_media_reference_key"]="not_present"
                elif scenario=="target":clip["media_references"]["DEFAULT_MEDIA"]["target_url"]="other.mov"
                elif scenario=="start":clip["source_range"]=trange(1001,52,24)
                elif scenario=="duration":clip["source_range"]=trange(1002,51,24)
                else:clip["media_references"]["DEFAULT_MEDIA"]["available_range"]=trange(0,52,24)
                with self.assertRaises(BridgeRefused):self.admit(data)

    def test_rgb_samples_remain_three_corroborating_instants_without_frame_hash_promotion(self):
        for scenario in ("missing","wrong_index","wrong_size","bad_hash","constant"):
            with self.subTest(scenario=scenario):
                data=self.fixture();samples=data["readback"]["media"][0]["pixel_samples"]
                if scenario=="missing":samples.pop()
                elif scenario=="wrong_index":samples[1]["index"]=25
                elif scenario=="wrong_size":samples[1]["rgb24_bytes"]-=1
                elif scenario=="bad_hash":samples[1]["rgb24_sha256"]="not-a-hash"
                else:samples[1]["nonconstant_rgb"]=False
                with self.assertRaises(BridgeRefused):self.admit(data)

    def test_historical_origin_execution_or_acceptance_flags_cannot_be_promoted(self):
        for field in ("source_origin","new_render","nle_opened","canonical_scene_ids_created",
                      "assets_adopted","unknown_replayed","human_acceptance","formal_acceptance",
                      "inputs_stable"):
            with self.subTest(field=field):
                data=self.fixture()
                data["readback"][field]="OBSERVED" if field=="source_origin" else field!="inputs_stable"
                with self.assertRaises(BridgeRefused):self.admit(data)

    def test_retained_value_is_immutable_and_a_json_descriptor_is_not_admission(self):
        observation=self.admit();expected=observation.descriptor()
        changed=observation.descriptor();changed["selected"][0]["source_sha256"]="0"*64
        self.assertEqual(observation.descriptor(),expected)
        with self.assertRaises(AttributeError):observation._media=()
        with self.assertRaises(BridgeRefused):
            _retained_bridge._admitted_video_profile({"media_type":"video/quicktime"},changed)
        with self.assertRaises(BridgeRefused):_retained_bridge._retained_output_profile({"verified":True})

    def test_retained_mov_export_and_import_preview_reuse_observations_without_decoder(self):
        root,owner,out,project,request,observation,data=self.portable()
        with patch("bridge.file_probe",side_effect=AssertionError("retained source decoded again"))as probe, \
             patch("bridge.subprocess.Popen",side_effect=AssertionError("retained source spawned a child"))as child:
            exported=export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
            preview=import_preview(out,"timeline.otio",out/"source-project.json",retained_alab=observation)
            probe.assert_not_called();child.assert_not_called()
        manifest=json.loads((out/"bridge-manifest.json").read_bytes())
        self.assertEqual(manifest["output_profile"],"alab-two-retained-mov-cut-v1")
        self.assertEqual(manifest["source_media_count"],2)
        self.assertEqual(manifest["missing_scene_ids"],[])
        self.assertEqual({row["path"]for row in manifest["bound_media"]},
                         {f"media/{pin[3]}.mov"for pin in data["media_pins"]})
        self.assertEqual(manifest["retained_source_binding"],_retained_bridge._retained_source_binding(observation))
        self.assertTrue(any("opaque/unavailable"in loss for loss in manifest["loss_report"]))
        self.assertEqual(preview["native_change_proposals"],[])
        self.assertEqual(len(preview["property_fidelity"]),12)
        self.assertIs(preview["edit_imported"],False)
        self.assertIs(exported["owner_publication_authorized"],False)
        for pin,blob in zip(data["media_pins"],data["raw_media"]):self.assertEqual((out/f"media/{pin[3]}.mov").read_bytes(),blob)

    def test_missing_independent_admission_never_becomes_generic_mov_support(self):
        root,owner,out,project,request,observation,data=self.portable()
        with patch("bridge.file_probe",side_effect=AssertionError("unadmitted MOV decode"))as probe:
            with self.assertRaises(BridgeRefused):export_portable(owner,request,out)
            self.assertFalse(out.exists());probe.assert_not_called()
            export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
            with self.assertRaises(BridgeRefused):import_preview(out,"timeline.otio",out/"source-project.json")
            with self.assertRaises(BridgeRefused):
                import_preview(out,"timeline.otio",out/"source-project.json",retained_alab=observation.descriptor())
            probe.assert_not_called()

    def test_changed_or_relabelled_source_refuses_before_export_destination_or_decoder(self):
        for scenario in ("changed_bytes","extension","mime"):
            with self.subTest(scenario=scenario):
                root,owner,out,project,request,observation,data=self.portable()
                if scenario=="changed_bytes":
                    (owner/request["bindings"][0]["file"]).write_bytes(b"unadmitted replacement")
                elif scenario=="extension":
                    old=owner/request["bindings"][0]["file"]
                    new=old.with_suffix(".mp4");old.rename(new);request["bindings"][0]["file"]=new.name
                else:
                    project["assets"][0]["media_type"]="video/mp4"
                    (owner/"project.json").write_text(json.dumps(project))
                with patch("bridge.file_probe",side_effect=AssertionError("unadmitted source decode"))as probe:
                    with self.assertRaises(BridgeRefused):export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
                    probe.assert_not_called()
                self.assertFalse(out.exists())

    def test_default_mp4_export_and_preview_keep_their_existing_probe_route(self):
        fixture=PortableOtioTests(methodName="runTest");fixture.setUp();self.addCleanup(fixture.doCleanups)
        out=fixture.root/"retained-default-mp4"
        with patch("bridge.file_probe",return_value=VIDEO_INFO)as probe:
            export_portable(fixture.src,fixture.request,out)
            import_preview(out,"timeline.otio",out/"source-project.json")
            self.assertEqual(probe.call_count,4)
        manifest=json.loads((out/"bridge-manifest.json").read_bytes())
        self.assertEqual(manifest["output_profile"],"portable-media-cut-v2")
        self.assertNotIn("retained_source_binding",manifest)
        self.assertTrue(all(row["path"].endswith(".mp4")for row in manifest["bound_media"]))

    def test_retained_nle_shaped_return_derives_only_second_182_to170_cut_without_decoder(self):
        root,owner,out,project,request,observation,data=self.portable()
        with patch("bridge.file_probe",side_effect=AssertionError("retained media decoded again"))as probe:
            export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
            native_clips=[]
            original=json.loads((out/"timeline.otio").read_bytes())
            for clip in original["tracks"]["children"][0]["children"]:
                ref=self.clone(clip["media_reference"]);ref["metadata"]={}
                ref["available_image_bounds"]=None;ref["target_url"]=str(out/ref["target_url"])
                native_clips.append({"OTIO_SCHEMA":"Clip.2","name":"Unit native-shaped original",
                    "enabled":True,"effects":[],"markers":[],"metadata":{},
                    "source_range":self.clone(clip["source_range"]),
                    "media_references":{"DEFAULT_MEDIA":ref},"active_media_reference_key":"DEFAULT_MEDIA"})
            received={"OTIO_SCHEMA":"Timeline.1","metadata":{},"tracks":{"OTIO_SCHEMA":"Stack.1",
                "children":[{"OTIO_SCHEMA":"Track.1","kind":"Video","children":native_clips}]}}
            edited=self.clone(received);edited["tracks"]["children"][0]["children"][1]["source_range"]=trange(0,170,24)
            raw=root/"raw";raw.mkdir()
            for name,value in (("received.otio",received),("edited.otio",edited)):
                (raw/name).write_text(json.dumps(value),encoding="utf-8")
            digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
            query={"schema":KDENLIVE_REQUEST_SCHEMA,"capsule_manifest_sha256":digest(out/"bridge-manifest.json"),
                   "received_otio":"received.otio","received_sha256":digest(raw/"received.otio"),
                   "edited_otio":"edited.otio","edited_sha256":digest(raw/"edited.otio")}
            result=preview_kdenlive_cut(out,raw,query,out/"source-project.json",
                                       authorized_media_roots=(out,),retained_alab=observation)
            probe.assert_not_called()
        self.assertEqual(result["status"],"BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED")
        self.assertEqual(result["native_change_proposals"],[{"type":"set_scene_duration",
            "scene_id":project["scenes"][1]["id"],"duration":{"num":"85","den":"12"}}])
        self.assertEqual([row["original_inpoint"]for row in result["source_identity_mapping"]],[0,0])
        self.assertEqual(result["nle_receiver_acceptance"],"NOT_VERIFIED")
        self.assertEqual(result["native_project_reopened"],"NOT_VERIFIED")
        self.assertIs(result["edit_imported"],False)
        self.assertIs(result["native_final_frames_verified"],False)

    def test_capsule_cannot_replace_or_drop_the_independent_retained_source_binding(self):
        for scenario in ("missing","origin_sha","descriptor_sha","self_profile"):
            with self.subTest(scenario=scenario):
                root,owner,out,project,request,observation,data=self.portable()
                export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
                manifest=json.loads((out/"bridge-manifest.json").read_bytes())
                if scenario=="missing":del manifest["retained_source_binding"]
                elif scenario=="origin_sha":manifest["retained_source_binding"]["originals"][0]["sha256"]="0"*64
                elif scenario=="descriptor_sha":manifest["retained_source_binding"]["derived_descriptor_sha256"]="0"*64
                else:manifest["output_profile"]="portable-media-cut-v2"
                (out/"bridge-manifest.json").write_text(json.dumps(manifest))
                with patch("bridge.file_probe",side_effect=AssertionError("changed capsule decoder")):
                    with self.assertRaises(BridgeRefused):
                        import_preview(out,"timeline.otio",out/"source-project.json",retained_alab=observation)

    def test_closed_selected_scene_bindings_do_not_allow_extra_missing_reordered_or_shifted_media(self):
        for scenario in ("omitted","extra","swapped","shifted","duration"):
            with self.subTest(scenario=scenario):
                root,owner,out,project,request,observation,data=self.portable()
                if scenario=="omitted":request["bindings"].pop()
                elif scenario=="extra":request["bindings"].append(self.clone(request["bindings"][0]))
                elif scenario=="swapped":
                    request["bindings"][0]["scene_id"],request["bindings"][1]["scene_id"]=\
                        request["bindings"][1]["scene_id"],request["bindings"][0]["scene_id"]
                elif scenario=="shifted":request["bindings"][0]["source_start_frame"]=1
                else:
                    project["scenes"][1]["duration"]={"num":"85","den":"12"}
                    (owner/"project.json").write_text(json.dumps(project))
                with patch("bridge.file_probe",side_effect=AssertionError("invalid binding decoder"))as probe:
                    with self.assertRaises(BridgeRefused):export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
                    probe.assert_not_called()
                self.assertFalse(out.exists())

    def test_original_alab_notices_are_required_before_export_destination_or_decoder(self):
        for scenario in ("missing_license","missing_readme","missing_selection_notice",
                         "altered_license","altered_readme","altered_selection_notice",
                         "extra_role","not_bytes","no_notices","no_retained_observation"):
            with self.subTest(scenario=scenario):
                root,owner,out,project,request,observation,data=self.portable()
                notices=dict(self.source_notices)
                if scenario.startswith("missing_"):del notices[scenario.removeprefix("missing_")]
                elif scenario.startswith("altered_"):notices[scenario.removeprefix("altered_")]+=b"changed"
                elif scenario=="extra_role":notices["foreign"]=b"extra original"
                elif scenario=="not_bytes":notices["license"]=notices["license"].decode()
                elif scenario=="no_notices":notices=None
                else:observation=None
                with patch("bridge.file_probe",side_effect=AssertionError("invalid license must not decode"))as probe, \
                     patch("bridge.subprocess.Popen",side_effect=AssertionError("invalid license must not spawn"))as child:
                    with self.assertRaises(BridgeRefused):
                        export_portable(owner,request,out,retained_alab=observation,source_notices=notices)
                    probe.assert_not_called();child.assert_not_called()
                self.assertFalse(out.exists())

    def test_alab_portable_zip_retains_all_original_notices_and_extracts_to_the_same_import(self):
        root,owner,out,project,request,observation,data=self.portable()
        exported=export_portable(owner,request,out,retained_alab=observation,
                                 source_notices=self.source_notices)
        archive=out/"portable-cut.zip"
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),exported["portable_zip_sha256"])
        expected={"README.md","bridge-manifest.json","source-project.json","timeline.otio",
                  *[row[1]for row in _retained_bridge._ALAB_SOURCE_NOTICES],
                  *[f"media/{row[3]}.mov"for row in data["media_pins"]]}
        extracted=root/"extracted-standalone-zip";extracted.mkdir()
        with zipfile.ZipFile(archive)as zipped:
            self.assertEqual(set(zipped.namelist()),expected)
            self.assertEqual(len(zipped.infolist()),len(expected))
            for name in sorted(expected):
                raw=zipped.read(name)
                self.assertEqual(raw,(out/name).read_bytes())
                target=extracted/name;target.parent.mkdir(exist_ok=True)
                target.write_bytes(raw)
            for role,name,_,_ in _retained_bridge._ALAB_SOURCE_NOTICES:
                self.assertEqual(zipped.read(name),self.source_notices[role])
        self.assertFalse((extracted/"portable-cut.zip").exists())
        with patch("bridge.file_probe",side_effect=AssertionError("no decoder during retained ZIP import"))as probe:
            original=import_preview(out,"timeline.otio",out/"source-project.json",retained_alab=observation)
            restored=import_preview(extracted,"timeline.otio",extracted/"source-project.json",retained_alab=observation)
            probe.assert_not_called()
        self.assertEqual(restored,original)
        self.assertFalse(restored["edit_imported"])
        self.assertEqual(restored["nle_receiver_acceptance"],"NOT_VERIFIED")

    def test_import_rejects_missing_changed_or_forged_notice_bindings_even_with_original_MOVs(self):
        cases=[("file_missing",role)for role in ("license","readme","selection_notice")]
        cases += [("file_changed",role)for role in ("license","readme","selection_notice")]
        cases += [(where,defect)for where in ("manifest","timeline")
                  for defect in ("missing","sha","bytes","extra")]
        for scenario,detail in cases:
            with self.subTest(scenario=scenario,detail=detail):
                root,owner,out,project,request,observation,data=self.portable()
                export_portable(owner,request,out,retained_alab=observation,source_notices=self.source_notices)
                if scenario.startswith("file_"):
                    name=next(row[1]for row in _retained_bridge._ALAB_SOURCE_NOTICES if row[0]==detail)
                    if scenario=="file_missing":(out/name).unlink()
                    else:(out/name).write_bytes(b"changed original notice")
                else:
                    path=out/("bridge-manifest.json"if scenario=="manifest"else"timeline.otio")
                    document=json.loads(path.read_bytes())
                    value=document if scenario=="manifest"else document["metadata"]["motionwright"]
                    if detail=="missing":del value["original_source_notices"]
                    elif detail=="sha":value["original_source_notices"][0]["sha256"]="0"*64
                    elif detail=="bytes":value["original_source_notices"][0]["bytes"]+=1
                    else:value["original_source_notices"].append(dict(value["original_source_notices"][0]))
                    path.write_text(json.dumps(document))
                with patch("bridge.file_probe",side_effect=AssertionError("invalid notice decoder"))as probe:
                    with self.assertRaises(BridgeRefused):
                        import_preview(out,"timeline.otio",out/"source-project.json",retained_alab=observation)
                    probe.assert_not_called()


class ImmutableOwnerCaptureTests(unittest.TestCase):
    """Real bounded file reads; decoder/native results remain simulated."""

    def fixture(self, alab=False):
        if alab:
            helper=RetainedAlabObservationTests()
            self.addCleanup(helper.doCleanups)
            root,owner,out,project,request,retained,data=helper.portable()
            return helper,owner,out,request,retained,helper.source_notices
        helper=PortableOtioTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        return helper,helper.src,helper.out,helper.request,None,None

    def test_owner_JSON_and_Project_digest_consume_the_same_immutable_capture(self):
        import ci_private_evidence as custody
        helper,root,_,_,_,_=self.fixture()
        path=root/"project.json"
        original=path.read_bytes()
        changed=original.replace(b"Owned video source",b"Other video source")
        self.assertNotEqual(original,changed)
        real=custody.regular
        observed=[]
        def replace_after_capture(candidate,limit):
            result=real(candidate,limit)
            if candidate==path:
                observed.append(result)
                path.write_bytes(changed)
            return result
        with patch.object(custody,"regular",side_effect=replace_after_capture), \
             patch.object(Path,"read_bytes",side_effect=AssertionError("consumer reopened pathname")), \
             patch("bridge.subprocess.Popen",side_effect=AssertionError("no native process")):
            project,digest=_retained_bridge.parse_project(root,"project.json")
        self.assertEqual(project,helper.project)
        self.assertEqual(digest,hashlib.sha256(original).hexdigest())
        self.assertEqual(len(observed),1)
        self.assertEqual(path.read_bytes(),changed)
        path.write_bytes(original)
        with patch.object(custody,"regular",side_effect=replace_after_capture), \
             patch.object(Path,"read_bytes",side_effect=AssertionError("JSON reopened pathname")):
            self.assertEqual(_retained_bridge.read_json(root,"project.json"),helper.project)
        self.assertEqual(len(observed),2)

    def test_portable_directory_and_ZIP_copy_captured_bytes_for_MP4_and_ALab(self):
        for alab in (False,True):
            with self.subTest(alab=alab):
                helper,root,out,request,retained,notices=self.fixture(alab)
                project_raw=(root/"project.json").read_bytes()
                source_media={row["file"]:(root/row["file"]).read_bytes()for row in request["bindings"]}
                real_read=Path.read_bytes
                def no_source_reopen(path):
                    if path.is_relative_to(root):raise AssertionError("copy/parse reopened owner source")
                    return real_read(path)
                with patch.object(Path,"read_bytes",no_source_reopen), \
                     patch("bridge.shutil.copyfile",side_effect=AssertionError("copyfile reopened source")), \
                     patch("bridge.file_probe",return_value=VIDEO_INFO)as probe, \
                     patch("bridge.subprocess.Popen",side_effect=AssertionError("no native process")):
                    result=export_portable(root,request,out,retained_alab=retained,source_notices=notices)
                self.assertEqual((out/"source-project.json").read_bytes(),project_raw)
                with zipfile.ZipFile(out/"portable-cut.zip")as archive:
                    self.assertEqual(archive.read("source-project.json"),project_raw)
                    for source,raw in source_media.items():
                        name="media/"+hashlib.sha256(raw).hexdigest()+Path(source).suffix
                        self.assertEqual(archive.read(name),raw)
                        self.assertEqual((out/name).read_bytes(),raw)
                    if alab:
                        for role,name,_,_ in _retained_bridge._ALAB_SOURCE_NOTICES:
                            self.assertEqual(archive.read(name),notices[role])
                self.assertEqual(probe.call_count,0 if alab else len(request["bindings"]))
                self.assertEqual(result["portable_zip_sha256"],hashlib.sha256((out/"portable-cut.zip").read_bytes()).hexdigest())

    def test_input_replacement_after_capture_cannot_publish_a_rebased_portable_cut(self):
        for kind in ("project","otio","media"):
            with self.subTest(kind=kind):
                helper,root,out,request,_,_=self.fixture()
                target=root/{"project":"project.json","otio":"baseline.otio","media":"original.mp4"}[kind]
                before=target.read_bytes()
                real=_retained_bridge._admitted_video_probe
                changed=[]
                def replace_before_existing_probe(path,retained,**kwargs):
                    if not changed:
                        target.write_bytes(before+b" ")
                        changed.append(True)
                    return real(path,retained,**kwargs)
                with patch.object(_retained_bridge,"_admitted_video_probe",side_effect=replace_before_existing_probe), \
                     patch("bridge.file_probe",return_value=VIDEO_INFO), \
                     patch("bridge.subprocess.Popen",side_effect=AssertionError("no native process")):
                    with self.assertRaisesRegex(BridgeRefused,"Original source changed|Owner media changed"):
                        export_portable(root,request,out)
                self.assertEqual(changed,[True])
                self.assertFalse(out.exists())
                self.assertEqual(target.read_bytes(),before+b" ")

        with self.subTest(alias="identical_media_bindings_remain_admitted"):
            helper,root,out,request,_,_=self.fixture()
            real=_retained_bridge.capture_owner_original
            observed=[]
            def remember_media(*args,**kwargs):
                result=real(*args,**kwargs)
                if args[1]=="original.mp4":
                    observed.append((result[0],result[2]["sha256"],len(result[1])))
                return result
            with patch.object(_retained_bridge,"capture_owner_original",side_effect=remember_media), \
                 patch("bridge.file_probe",return_value=VIDEO_INFO), \
                 patch("bridge.subprocess.Popen",side_effect=AssertionError("no native process")):
                result=export_portable(root,request,out)
            self.assertEqual(len(observed),2)
            self.assertEqual(observed[0],observed[1])
            self.assertEqual(result["verified_media_refs"],2)
            manifest=json.loads((out/"bridge-manifest.json").read_bytes())
            self.assertTrue(manifest["original_project_unchanged"])
            self.assertEqual(manifest["source_media_count"],1)

        with self.subTest(alias="project_A_then_OTIO_B_same_path"):
            helper,root,out,request,_,_=self.fixture()
            project_path=root/request["project"]
            project_original=project_path.read_bytes()
            otio_original=(root/request["otio"]).read_bytes()
            request["otio"]=request["project"]
            self.assertNotEqual(project_original,otio_original)
            real=_retained_bridge.capture_original
            captured=[]
            def replace_after_first_capture(path,budget):
                result=real(path,budget)
                if path==project_path:
                    captured.append(result[0])
                    if len(captured)==1:
                        project_path.write_bytes(otio_original)
                return result
            with patch.object(_retained_bridge,"capture_original",side_effect=replace_after_first_capture), \
                 patch("bridge.file_probe",side_effect=AssertionError("alias mismatch before native observation"))as probe, \
                 patch("bridge.subprocess.Popen",side_effect=AssertionError("no native process")):
                with self.assertRaisesRegex(BridgeRefused,"Repeated original path changed between captures"):
                    export_portable(root,request,out)
            self.assertEqual(captured,[project_original,otio_original])
            probe.assert_not_called()
            self.assertFalse(out.exists())
            self.assertEqual(project_path.read_bytes(),otio_original)

        with self.subTest(alias="media_A_then_media_B_same_path"):
            helper,root,out,request,_,_=self.fixture()
            media=root/"original.mp4"
            original=media.read_bytes()
            replacement=original+b" changed for the second binding"
            replacement_sha=hashlib.sha256(replacement).hexdigest()
            second=dict(helper.project["assets"][0])
            second.update(id=ID(51),content_sha256=replacement_sha)
            helper.project["assets"].append(second)
            helper.save()
            request["bindings"][1].update(asset_id=ID(51),sha256=replacement_sha)
            real=_retained_bridge.capture_owner_original
            captured=[]
            def replace_before_second_media(*args,**kwargs):
                if args[1]=="original.mp4" and len(captured)==1:
                    media.write_bytes(replacement)
                result=real(*args,**kwargs)
                if args[1]=="original.mp4":
                    captured.append(result[1])
                return result
            with patch.object(_retained_bridge,"capture_owner_original",side_effect=replace_before_second_media), \
                 patch("bridge.file_probe",return_value=VIDEO_INFO)as probe, \
                 patch("bridge.subprocess.Popen",side_effect=AssertionError("no native process")):
                with self.assertRaisesRegex(BridgeRefused,"Repeated original path changed between captures"):
                    export_portable(root,request,out)
            self.assertEqual(captured,[original,replacement])
            self.assertEqual(probe.call_count,1)
            self.assertFalse(out.exists())
            self.assertEqual(media.read_bytes(),replacement)

    def test_ALab_notices_validate_captured_bytes_without_a_second_open(self):
        import ci_private_evidence as custody
        helper,root,out,request,retained,notices=self.fixture(True)
        export_portable(root,request,out,retained_alab=retained,source_notices=notices)
        manifest=json.loads((out/"bridge-manifest.json").read_bytes())
        role,name,_,_=_retained_bridge._ALAB_SOURCE_NOTICES[0]
        target=out/name
        original=target.read_bytes()
        real=custody.regular
        calls=[]
        def replace_after_capture(path,limit):
            result=real(path,limit)
            if path==target:
                target.write_bytes(b"x"*len(original))
                calls.append(path)
            return result
        real_open=Path.open
        def no_read_open(path,mode="r",*args,**kwargs):
            if path==target and "r"in mode:raise AssertionError("notice reopened after capture")
            return real_open(path,mode,*args,**kwargs)
        with patch.object(custody,"regular",side_effect=replace_after_capture), \
             patch.object(Path,"open",no_read_open):
            _retained_bridge._require_alab_source_notices(out,manifest,retained)
        self.assertEqual(calls,[target])
        self.assertEqual(target.read_bytes(),b"x"*len(original))
        with self.assertRaises(BridgeRefused):
            _retained_bridge._require_alab_source_notices(out,manifest,retained)
        self.assertEqual(notices[role],original)

    def test_shared_capture_rejects_namespace_replacement_link_growth_and_shortening(self):
        import mlt_official_runtime_intake as intake
        import stat
        for kind in ("replacement","link","growth","shortening"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory()as folder:
                path=Path(folder).resolve()/"source.bin"
                path.write_bytes(b"original")
                real_open,real_read,real_lstat=intake.os.open,intake.os.read,Path.lstat
                state={"opened":False,"read":False}
                def opened(candidate,flags,*args,**kwargs):
                    result=real_open(candidate,flags,*args,**kwargs)
                    if Path(candidate)==path:state["opened"]=True
                    return result
                def namespace(candidate,*args,**kwargs):
                    value=real_lstat(candidate,*args,**kwargs)
                    if candidate==path and state["opened"]and kind in ("replacement","link"):
                        attrs={key:getattr(value,key)for key in ("st_dev","st_ino","st_size","st_mode","st_uid","st_gid","st_mtime_ns","st_ctime_ns")}
                        if kind=="replacement":attrs["st_ino"]+=1
                        else:attrs["st_mode"]=stat.S_IFLNK|0o777
                        return SimpleNamespace(**attrs)
                    return value
                def read(fd,limit):
                    value=real_read(fd,limit)
                    if state["opened"]and not state["read"]:
                        state["read"]=True
                        if kind=="growth":
                            with path.open("ab")as output:output.write(b"grew")
                        elif kind=="shortening":path.write_bytes(b"x")
                    return value
                with patch.object(intake.os,"open",side_effect=opened), \
                     patch.object(intake.os,"read",side_effect=read), \
                     patch.object(Path,"lstat",namespace):
                    with self.assertRaises(BridgeRefused)as caught:
                        _retained_bridge.capture_original(path,64)
                self.assertIsInstance(caught.exception.__cause__,intake.IntakeError)
                self.assertTrue(state["opened"])
                self.assertTrue(state["read"])


class CapturedMp4ProbeTests(unittest.TestCase):
    """Synthetic protocol controls; no MP4/NLE acceptance is inferred."""
    def portable(self,kind=PortableOtioTests):
        helper=kind(methodName="runTest")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        return helper

    def test_mp4_export_probes_exact_captured_stdin_across_A_B_A_path_replacement(self):
        helper=self.portable()
        first=helper.media.read_bytes()
        replacement=first+b" different pathname bytes during ffprobe"
        calls=[]
        def observe(args,*,deadline,input_bytes,capture=None):
            self.assertEqual(input_bytes,first)
            self.assertEqual(args[-1],"pipe:0")
            self.assertNotIn(str(helper.media),args)
            self.assertEqual(args[args.index("-protocol_whitelist")+1],"pipe")
            calls.append((args,input_bytes,deadline))
            if "-count_frames" not in args:
                self.assertIn("-count_packets",args)
                helper.media.write_bytes(replacement)
                return VideoReadbackTests.header(nb_frames="90")
            self.assertEqual(helper.media.read_bytes(),replacement)
            helper.media.write_bytes(first)
            return VideoReadbackTests.decoded(nb_read_frames="90")
        with patch("bridge._probe_output",side_effect=observe), \
             patch("bridge.subprocess.Popen",side_effect=AssertionError("protocol fixture only")):
            exported=export_portable(helper.src,helper.request,helper.out)
        self.assertEqual(len(calls),2*len(helper.request["bindings"]))
        for metadata,decoded in zip(calls[::2],calls[1::2]):
            self.assertIs(metadata[1],decoded[1])
            self.assertEqual(metadata[2],decoded[2])
        digest=hashlib.sha256(first).hexdigest()
        manifest=json.loads((helper.out/"bridge-manifest.json").read_bytes())
        self.assertIs(manifest["external_program_executed"],True)
        self.assertIs(manifest["nle_import_verified"],False)
        self.assertIs(manifest["publishing_authorized"],False)
        self.assertTrue(all(row["source_sha256"]==digest and row["bytes"]==len(first)
                            for row in manifest["bound_media"]))
        self.assertEqual((helper.out/f"media/{digest}.mp4").read_bytes(),first)
        with zipfile.ZipFile(helper.out/"portable-cut.zip")as zipped:
            self.assertEqual(zipped.read(f"media/{digest}.mp4"),first)
        self.assertEqual(exported["verified_media_refs"],len(helper.request["bindings"]))
        with self.subTest(profile="empty_export_has_no_probe"):
            empty=self.portable()
            empty.request["bindings"]=[]
            with patch("bridge._probe_output",side_effect=AssertionError("no bound media to observe"))as probe:
                export_portable(empty.src,empty.request,empty.out)
            probe.assert_not_called()
            empty_manifest=json.loads((empty.out/"bridge-manifest.json").read_bytes())
            self.assertIs(empty_manifest["external_program_executed"],False)
            self.assertEqual(empty_manifest["bound_media"],[])

    def test_import_and_native_preview_probe_the_same_verified_source_bytes(self):
        for native in (False,True):
            with self.subTest(native=native):
                helper=self.portable(BoundedKdenliveCutTests if native else PortableOtioTests)
                if native:helper.prepare()
                else:helper.export()
                seen=[]
                def check(path,*,original_bytes=None):
                    self.assertIs(type(original_bytes),bytes)
                    self.assertEqual(original_bytes,path.read_bytes())
                    self.assertEqual(hashlib.sha256(original_bytes).hexdigest(),path.stem)
                    seen.append((path,original_bytes))
                    return dict(VIDEO_INFO)
                with patch("bridge.file_probe",side_effect=check), \
                     patch("bridge.subprocess.Popen",side_effect=AssertionError("protocol fixture only")):
                    if native:
                        result=preview_kdenlive_cut(helper.out,helper.raw,helper.raw_request(),
                            helper.out/"source-project.json",authorized_media_roots=(helper.out,helper.raw))
                    else:
                        result=import_preview(helper.out,"timeline.otio",helper.out/"source-project.json")
                self.assertTrue(seen)
                self.assertEqual(result["native_change_proposals"],[])
                self.assertTrue(all(row[1]==seen[0][1]for row in seen))
        with self.subTest(retained_alab="same_capture_without_new_decoder"):
            import ci_private_evidence as custody
            helper=RetainedAlabObservationTests()
            self.addCleanup(helper.doCleanups)
            _,owner,out,_,request,retained,_=helper.portable()
            export_portable(owner,request,out,retained_alab=retained,source_notices=helper.source_notices)
            self.assertIs(json.loads((out/"bridge-manifest.json").read_bytes())["external_program_executed"],False)
            real=custody.regular
            captured=[]
            def capture(path,budget):
                result=real(path,budget)
                if path.parent==out/"media":captured.append(path)
                return result
            with patch.object(custody,"regular",side_effect=capture), \
                 patch.object(type(retained),"probe",side_effect=AssertionError("retained bytes reopened")), \
                 patch("bridge.subprocess.Popen",side_effect=AssertionError("retained media decoded again")):
                result=import_preview(out,"timeline.otio",out/"source-project.json",retained_alab=retained)
            self.assertEqual(result["native_change_proposals"],[])
            self.assertEqual(len(captured),2)
            self.assertEqual(len(set(captured)),2)

    def test_native_RAW_alias_preserves_the_first_capture_and_accepts_only_identical_bytes(self):
        for changed in (False,True):
            with self.subTest(alias="different_bytes"if changed else"same_bytes"):
                helper=self.portable(BoundedKdenliveCutTests)
                helper.prepare()
                path=helper.raw/"received.otio"
                first=path.read_bytes()
                second=json.loads(first)
                second["name"]="Changed content at the same native RAW pathname"
                second=json.dumps(second,sort_keys=True).encode()
                self.assertNotEqual(first,second)
                request=helper.raw_request()
                request["edited_otio"]=request["received_otio"]
                request["edited_sha256"]=hashlib.sha256(second if changed else first).hexdigest()
                real=_retained_bridge.capture_owner_original
                captures=[]
                def capture(*args,**kwargs):
                    result=real(*args,**kwargs)
                    if result[0]==path:
                        captures.append(result[1])
                        if changed and len(captures)==1:path.write_bytes(second)
                    return result
                with patch.object(_retained_bridge,"capture_owner_original",side_effect=capture), \
                     patch("bridge.file_probe",return_value=VIDEO_INFO), \
                     patch("bridge.subprocess.Popen",side_effect=AssertionError("RAW fixture only")):
                    if changed:
                        with self.assertRaisesRegex(BridgeRefused,"Repeated original path changed"):
                            preview_kdenlive_cut(helper.out,helper.raw,request,
                                helper.out/"source-project.json",authorized_media_roots=(helper.out,helper.raw))
                    else:
                        result=preview_kdenlive_cut(helper.out,helper.raw,request,
                            helper.out/"source-project.json",authorized_media_roots=(helper.out,helper.raw))
                        self.assertEqual(result["native_change_proposals"],[])
                        self.assertEqual(result["received_otio_sha256"],result["edited_otio_sha256"])
                self.assertEqual(captures,[first,second]if changed else[first,first])
                self.assertEqual(path.read_bytes(),second if changed else first)

    def test_mp4_input_budget_and_types_reject_before_any_probe(self):
        for raw in (b"",bytearray(b"x"),"x",b"123456789"):
            with self.subTest(type=type(raw).__name__,length=len(raw)), \
                 patch("bridge.MAX_MEDIA",8),patch("bridge._probe_output")as probe:
                with self.assertRaises(BridgeRefused):
                    file_probe(Path("not-reopened.mp4"),original_bytes=raw)
                probe.assert_not_called()
        original=OSError("source capture failed")
        with patch("bridge.capture_original",side_effect=original)as captured, \
             patch("bridge._probe_output")as probe:
            with self.assertRaises(OSError)as raised:file_probe(Path("original.mp4"))
        self.assertIs(raised.exception,original)
        captured.assert_called_once()
        probe.assert_not_called()

    def test_shared_executor_default_32MiB_and_explicit_256MiB_are_bounded_before_spawn(self):
        import manim_native_readback as executor
        # One real bounded byte value proves the old default has not widened.
        raw=b"x"*(32*1024*1024+1)
        with patch.object(executor.subprocess,"Popen")as start:
            with self.assertRaises(executor.ReadbackError):
                executor.run_bounded(["not-started"],32,input_bytes=raw)
            start.assert_not_called()
        sentinel=OSError("only a startup boundary witness")
        original={}
        with patch.object(executor.subprocess,"Popen",side_effect=sentinel)as start:
            with self.assertRaises(OSError)as raised:
                executor.run_bounded(["not-started"],32,input_bytes=raw,capture=original,
                                     max_stdin_bytes=256*1024*1024)
        self.assertIs(raised.exception,sentinel)
        start.assert_called_once()
        self.assertEqual(original["state"],"NOT_STARTED")
        self.assertEqual(original["stdin_bytes"],len(raw))
        del raw
        for limit in (False,None,0,-1,256*1024*1024+1):
            with self.subTest(limit=limit),patch.object(executor.subprocess,"Popen")as start:
                with self.assertRaises(executor.ReadbackError):
                    executor.run_bounded(["not-started"],32,input_bytes=b"x",max_stdin_bytes=limit)
                start.assert_not_called()

    def test_shared_executor_sends_one_exact_original_stdin_and_retains_its_terminal(self):
        raw=bytes(range(256))*512
        digest=hashlib.sha256(raw).hexdigest()
        capture={}
        started=[]
        real=subprocess.Popen
        def once(*args,**kwargs):
            child=real(*args,**kwargs);started.append(child);return child
        argv=[sys.executable,"-I","-c",
              "import hashlib,sys; data=sys.stdin.buffer.read(); "
              "sys.stdout.write(hashlib.sha256(data).hexdigest())"]
        with patch("bridge.subprocess.Popen",side_effect=once):
            observed=_probe_output(argv,input_bytes=raw,capture=capture)
        self.assertEqual(len(started),1)
        self.assertEqual(observed,digest.encode("ascii"))
        self.assertEqual(capture["argv"],argv)
        self.assertEqual((capture["stdin_bytes"],capture["stdin_sha256"]),(len(raw),digest))
        self.assertEqual(capture["stdout"],observed)
        self.assertEqual(capture["stderr"],b"")
        self.assertEqual(capture["terminal"]["exit_code"],0)
        self.assertTrue(capture["terminal"]["stdin_complete"])
        self.assertTrue(capture["terminal"]["pipes_closed"])
        self.assertIsNotNone(started[0].returncode)

    def test_shared_executor_feed_error_retains_known_exit_and_does_not_redispatch(self):
        import manim_native_readback as executor
        class FailingInput(io.BytesIO):
            def write(self,data):raise OSError("original feed failure")
        class Child:
            def __init__(self):
                self.stdin=FailingInput();self.stdout=io.BytesIO(b"partial JSON")
                self.stderr=io.BytesIO(b"original stderr");self.returncode=7
            def wait(self,timeout=None):return self.returncode
            def poll(self):return self.returncode
            def kill(self):raise AssertionError("known terminal must not be killed")
        child=Child();capture={}
        with patch.object(executor.subprocess,"Popen",return_value=child)as start:
            with self.assertRaises(BridgeRefused)as raised:
                _probe_output(["ffprobe","pipe:0"],input_bytes=b"original",capture=capture)
        start.assert_called_once()
        self.assertIsInstance(raised.exception.__cause__,executor.ReadbackError)
        self.assertEqual(capture["terminal"]["exit_code"],7)
        self.assertFalse(capture["terminal"]["stdin_complete"])
        self.assertIn("write:OSError",capture["terminal"]["pipe_errors"])
        self.assertEqual(capture["stdout"],b"partial JSON")
        self.assertEqual(capture["stderr"],b"original stderr")
        self.assertTrue(all(stream.closed for stream in (child.stdin,child.stdout,child.stderr)))

    def test_shared_executor_incomplete_pipes_keep_known_exit_separate_from_closure(self):
        import manim_native_readback as executor
        class PendingThread:
            def __init__(self,*args,**kwargs):self.joins=[]
            def start(self):pass
            def join(self,timeout=None):
                self.joins.append(timeout)
                if timeout is None:raise AssertionError("unbounded join")
            def is_alive(self):return True
        class Child:
            returncode=0
            def __init__(self):
                self.stdout=io.BytesIO(b"");self.stderr=io.BytesIO(b"")
            def wait(self,timeout=None):return 0
            def poll(self):return 0
            def kill(self):raise AssertionError("known exit remains known")
        threads=[]
        def thread(*args,**kwargs):
            result=PendingThread(*args,**kwargs);threads.append(result);return result
        child=Child();capture={}
        try:
            with patch.object(executor.subprocess,"Popen",return_value=child)as start, \
                 patch.object(executor.threading,"Thread",side_effect=thread):
                with self.assertRaises(BridgeRefused):
                    _probe_output(["ffprobe","pipe:0"],capture=capture)
            start.assert_called_once()
            self.assertEqual(capture["terminal"]["exit_code"],0)
            self.assertFalse(capture["terminal"]["pipes_closed"])
            self.assertFalse(capture["terminal"]["whole_process_completed"])
            self.assertTrue(all(t.joins and all(x is not None and x>=0 for x in t.joins)for t in threads))
        finally:
            # These are owned BytesIO fixtures, never cross-thread native pipes.
            child.stdout.close();child.stderr.close()

    def test_probe_primary_and_closed_diagnostics_survive_secondary_capture_failure(self):
        import manim_native_readback as executor
        class RejectedCapture(dict):
            def update(self,*args,**kwargs):raise SystemExit("secondary custody failure")
        originals=[OSError("PRIVATE_OWNER_PATH"),executor.ReadbackError("original readback failure"),
                   executor.BoundedProcessExitError("original exit7"),KeyboardInterrupt(),SystemExit(9)]
        for original in originals:
            with self.subTest(type=type(original).__name__):
                def failed(*args,**kwargs):
                    kwargs["capture"].update({"state":"TERMINAL_OBSERVED","stdout":b"prefix",
                        "stderr":b"private original stderr","terminal":{"exit_code":7,"pipes_closed":False}})
                    raise original
                with patch.object(executor,"run_bounded",side_effect=failed)as run:
                    with self.assertRaises(BaseException)as raised:
                        _probe_output(["ffprobe","pipe:0"],capture=RejectedCapture())
                run.assert_called_once()
                if isinstance(original,(OSError,executor.ReadbackError)):
                    self.assertIsInstance(raised.exception,BridgeRefused)
                    self.assertIs(raised.exception.__cause__,original)
                    self.assertNotIn("PRIVATE_OWNER_PATH",str(raised.exception))
                else:self.assertIs(raised.exception,original)
                self.assertEqual(original.otio_probe_original["terminal"]["exit_code"],7)
                self.assertEqual(original.otio_probe_original["stderr"],b"private original stderr")
        custody_error=OSError("success cannot hide failed custody")
        class FailedSuccessCapture(dict):
            def update(self,*args,**kwargs):raise custody_error
        def success(*args,**kwargs):
            kwargs["capture"].update({"stdout":b"{}","stderr":b"","terminal":{"exit_code":0}})
            return b"{}",{}
        with patch.object(executor,"run_bounded",side_effect=success)as run:
            with self.assertRaises(OSError)as raised:
                _probe_output(["ffprobe","pipe:0"],capture=FailedSuccessCapture())
        self.assertIs(raised.exception,custody_error)
        run.assert_called_once()

    def test_metadata_stderr_rejection_remains_primary_over_failed_capture(self):
        import manim_native_readback as executor
        class RejectedCapture(dict):
            def update(self,*args,**kwargs):raise KeyboardInterrupt()
        def warning(*args,**kwargs):
            kwargs["capture"].update({"stdout":b"{}","stderr":b"PRIVATE_OWNER_PATH decode warning",
                                      "terminal":{"exit_code":0,"pipes_closed":True}})
            return b"{}",{}
        with patch.object(executor,"run_bounded",side_effect=warning)as run:
            with self.assertRaisesRegex(BridgeRefused,"decode error")as raised:
                _probe_output(["ffprobe","pipe:0"],capture=RejectedCapture())
        run.assert_called_once()
        self.assertNotIn("PRIVATE_OWNER_PATH",str(raised.exception))
        self.assertEqual(raised.exception.otio_probe_original["terminal"]["exit_code"],0)


if __name__=="__main__":unittest.main()
