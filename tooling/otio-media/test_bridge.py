#!/usr/bin/env python3
"""First-party portable OTIO tests. Synthetic bytes; real NLE/ffprobe is CI only."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from bridge import (
    BridgeRefused, export_portable, file_probe, import_preview, rtc, trange, SCHEMA,
    _probe_output,
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


if __name__=="__main__":unittest.main()
