"""G0 source/custody contracts only; synthetic media is not native evidence."""
import ast
import copy
import hashlib
import io
from pathlib import Path
import re
import struct
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import native_evidence_projection as core
import native_mlt_evidence_selection as mlt
import native_film180_evidence_selection as film
from native_selection_test_fixtures import World,CURRENT,WAV180,PCM180,source_text,rewrite_selected,selected_value_change


class FilmSelectionTests(unittest.TestCase):
    def world(self):return World("film180-g2")

    def test_film_source_contract_is_required_separately_from_common_contracts(self):
        for missing_root in (False, True):
            with self.subTest(missing_root=missing_root):
                world = World("film180-g2")
                del world.files[("film-contracts", "contracts.json")]
                if missing_root: del world.roots["film-contracts"]
                with world.patched():
                    raw, manifest = world.capture()
                    with self.assertRaises(core.SelectionError): world.verify(raw)
                self.assertFalse(manifest["required_layers_complete"])
                common = next(r for r in manifest["members"] if r["role"] == "mlt-source-contracts")
                film = next(r for r in manifest["members"] if r["role"] == "film-source-contracts")
                self.assertEqual(common["state"], "INCLUDED")
                self.assertEqual(film["state"], "REQUIRED_MISSING")

    def test_film_failed_source_contract_original_does_not_become_acceptance(self):
        for status, code in (("FAIL", 1), ("UNKNOWN", 124)):
            with self.subTest(status=status):
                world = World("film180-g2")
                world.edit("film-contracts", "contracts.json", lambda v:v.update(status=status, exit_code=code))
                original = world.files[("film-contracts", "contracts.json")]
                with world.patched():
                    raw, manifest = world.capture()
                    with self.assertRaisesRegex(core.SelectionError, "native-source-contract-not-passed"):
                        world.verify(raw)
                self.assertTrue(manifest["required_layers_complete"])
                self.assertIn(original, raw)
                self.assertFalse(manifest["physical_acceptance"])
                self.assertFalse(manifest["human_acceptance"])
                self.assertFalse(manifest["release_authorized"])

    def test_film_source_contract_neighbors_are_outside_selected_scope(self):
        world = World("film180-g2"); secret = b"UNLISTED-FILM-SOURCE-GATE-NEIGHBOR"
        paths = [("film-contracts", "unlisted.json"), ("film-contracts", "contracts-00-stderr.log"),
                 ("film-contracts", "nested/contracts.json")]
        for key in paths: world.files[key] = secret
        with world.patched(): raw, manifest = world.capture(); world.verify(raw)
        self.assertNotIn(secret, raw)
        self.assertTrue(set(paths).isdisjoint(world.reads))
        self.assertTrue(set(paths).isdisjoint(world.stats))
        row = next(r for r in manifest["members"] if r["role"] == "film-source-contracts")
        self.assertTrue(row["original_bytes"])
        self.assertEqual(row["kind"], "json-original")

    def test_actual_decoded_WAV_bytes_are_validated_in_each_phase(self):
        for phase in ("original","edited"):
            with self.subTest(phase=phase):
                world=World("film180-g2");original=b"not a real native decoded WAV"
                digest=world.put("owner","output/"+phase+"-decoded.wav",original)
                def master(v):
                    for artifact in (v["decoded_audio"],v["master"]["decoded_audio"]):
                        artifact.update(sha256=digest,bytes=len(original))
                world.edit("observation",phase+"-H264-AAC-native-master.json",master)
                # Keep this synthetic child receipt bound to the malformed WAV
                # acknowledgement so the independent decoder guard is exercised.
                world.edit("observation",phase+"-shared-session/master.terminal.json",
                    lambda v:v.update(evidence_sha256=core.sha(
                        world.files[("observation",phase+"-H264-AAC-native-master.json")])))
                with world.patched():
                    raw,manifest=world.capture();self.assertTrue(manifest["required_layers_complete"])
                    with self.assertRaisesRegex(core.SelectionError,"decoded-wav"):world.verify(raw)

    def test_synthetic_original_and_edited_graph_has_all360_png_and_no_acceptance(self):
        world=self.world()
        with world.patched():raw,manifest=world.capture();result=world.verify(raw)
        self.assertEqual(sum(r["role"]=="native-png" for r in manifest["members"]),360)
        self.assertTrue(result["saved_source_edit_preservation_verified"])
        self.assertTrue(result["source_content_oracle_recomputed"])
        self.assertFalse(result["decode_or_psnr_recomputed"])
        self.assertTrue(all(result[k] is False for k in ("physical_acceptance","human_acceptance","release_authorized","native_reopen_performed","packet_PTS_and_AAC_priming_verified")))
        from native_film180_command_custody import entries
        originals={entry["relative_path"] for entry in entries()}
        self.assertTrue(all(r["relative_path"] in originals and r["kind"]=="diagnostic-original"
            for r in manifest["members"] if "stdout" in r["relative_path"]))
        self.assertFalse(any(r["relative_path"].endswith(".rgb24") for r in manifest["members"]))
        self.assertEqual(1920*1080*3*180,1119744000)
        self.assertEqual(core.MAX_TOTAL,512*1024*1024)

    def test_both_last_original_pngs_are_mandatory_not_a_sampled_selection(self):
        for phase,directory in (("original","render-"+"d"*32),("edited","render-"+"e"*32)):
            world=self.world();del world.files[("owner","output/"+directory+"/frames/000179.png")]
            with world.patched():
                raw,manifest=world.capture();self.assertFalse(manifest["required_layers_complete"])
                with self.assertRaises(core.SelectionError):world.verify(raw)

    def test_all_media_and_custody_groups_are_required_in_each_phase(self):
        for phase in film.PHASES:
            for suffix in ("-original-master-decoded.s16le","-original-master-packets.json","-original-pcm-capture.json", "-all180-decoded-original-RGB24.json","-film180-allframe-RGB-PSNR.stats","-reopened-before-render.json"):
                with self.subTest(phase=phase,suffix=suffix):
                    world=self.world();del world.files[("observation",phase+suffix)]
                    with world.patched():raw,manifest=world.capture()
                    self.assertFalse(manifest["required_layers_complete"])

    def test_saved_source_original_bytes_preserve_every_field_with_new_phase_identity(self):
        world=self.world()
        with world.patched():
            raw,manifest=world.capture()
            intake=mlt.Intake(raw,CURRENT,"film180-g2",film.role_contract(),film.intake_policies(raw,CURRENT))
        for phase in film.PHASES:
            row,value,payload=intake.one("saved-project",phase)
            self.assertEqual(row["kind"],"json-original");self.assertTrue(row["original_bytes"])
            self.assertEqual(payload,world.files[("observation",phase+"-project.json")])
            self.assertEqual(value["production_design"],film.PROJECT_TEMPLATE["production_design"])
            self.assertEqual(len(value["deliverables"]),3)

    def test_personal_text_unknown_fields_and_unlisted_extensions_cannot_enter_saved_source(self):
        changes=(lambda v:v.update(private_extra="sensitive text"),lambda v:v["scenes"][0]["nodes"][0].update(text="personal unrelated story"),lambda v:v["extensions"].append({"private":"value"}))
        for change in changes:
            world=self.world();world.edit("observation","original-project.json",change)
            with world.patched():raw,manifest=world.capture()
            self.assertFalse(manifest["required_layers_complete"])
            self.assertNotIn(b"sensitive text",raw);self.assertNotIn(b"personal unrelated story",raw)

    def test_authored_source_identity_alias_is_rejected_before_policy_creation(self):
        project=copy.deepcopy(film.PROJECT_TEMPLATE)
        project["scenes"][1]["id"]=project["scenes"][0]["id"]
        with self.assertRaisesRegex(core.SelectionError,"identity-alias"):film.validate_project(project,"original")

    def test_edited_source_cannot_change_unrelated_node_geometry(self):
        world=self.world();world.edit("observation","edited-project.json",lambda v:v["scenes"][0]["nodes"][0].update(x=181))
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])

    def test_original_edited_receipt_swap_is_not_one_complete_film(self):
        world=self.world();a=("observation","original-H264-AAC-native-master.json");b=("observation","edited-H264-AAC-native-master.json")
        world.files[a],world.files[b]=world.files[b],world.files[a]
        with world.patched():
            try:raw,manifest=world.capture()
            except core.SelectionError:return
            with self.assertRaises(core.SelectionError):world.verify(raw)

    def test_altered_native_png_and_manifest_metadata_cannot_replace_actualbytes(self):
        world=self.world();key=("owner","output/render-"+"e"*32+"/frames/000179.png");world.files[key]+=b"altered"
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertTrue(any(r["relative_path"]==key[1] and r["state"]!="INCLUDED" for r in manifest["members"]))

    def test_all180_decode_manifest_cannot_drop_tail_or_lie_about_unique_hashes(self):
        for change in (lambda v:v["all_original_frame_sha256"].pop(),lambda v:v.update(unique_decoded_RGB_frames=1)):
            world=self.world();world.edit("observation","edited-all180-decoded-original-RGB24.json",change)
            with world.patched():
                raw,manifest=world.capture()
                if manifest["required_layers_complete"]:
                    with self.assertRaises(core.SelectionError):world.verify(raw)
                else:self.assertFalse(manifest["required_layers_complete"])

    def test_decoded_receipt_cannot_borrow_other_project_revision(self):
        world=self.world();world.edit("observation","edited-independent-AV-content.json",lambda v:v["identity"].update(revision=1))
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"decoded-source-identity"):world.verify(raw)

    def test_failed_edited_terminal_keeps_original_success_without_full_success(self):
        world=self.world()
        def change(v):v["native_command_terminals"]["edited.master"]="ERROR_OR_UNKNOWN";v["native_terminal"]="ERROR_OR_UNKNOWN"
        world.edit("observation","final-custody.json",change)
        with world.patched():
            raw,manifest=world.capture();self.assertTrue(manifest["required_layers_complete"])
            with self.assertRaisesRegex(core.SelectionError,"native-command-terminals"):world.verify(raw)

    def test_shared_original_edit_cas_is_retained_once_with_both_source_bindings(self):
        world=self.world()
        with world.patched():raw,manifest=world.capture();result=world.verify(raw)
        self.assertEqual(sum(r["role"]=="cas-source" for r in manifest["members"]),1)
        self.assertEqual(len(result["phases"]),2)
        self.assertNotEqual(result["phases"]["original"]["master_sha256"],result["phases"]["edited"]["master_sha256"])

    def test_same_actual_consumer_identity_cannot_stand_in_for_two_phases(self):
        world=self.world();path="edited-original-live-MLT-loaded-module.json"
        world.edit("observation",path,lambda v:v["original_live_pid_observer_receipt"].update(pid=111,process_start_ticks=2001))
        with world.patched():
            raw,manifest=world.capture();self.assertFalse(manifest["required_layers_complete"])

    def test_master_and_pcm_capture_phase_binding_is_independent_of_reported_success(self):
        world=self.world();world.edit("observation","edited-original-pcm-capture.json",lambda v:v.update(phase="original"))
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"direct-pcm-capture"):world.verify(raw)

    def test_packet_observation_cannot_bind_other_published_master(self):
        world=self.world();world.edit("observation","edited-original-packet-capture.json",lambda v:v.update(master_sha256="0"*64))
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"packet-source-link"):world.verify(raw)

    def test_psnr_stats_are_original_required_bytes_without_quality_floor(self):
        world=self.world();world.put("observation","original-film180-allframe-RGB-PSNR.stats",b"substituted metric stream")
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"psnr-links"):world.verify(raw)

    def test_manifest_rejects_rawlog_payload_even_if_labeled_required(self):
        world=self.world()
        with world.patched():
            raw,_=world.capture()
            def change(manifest,entries):
                row=next(r for r in manifest["members"] if r["role"]=="decoded-pcm")
                row["relative_path"]="original.original-pcm-decode.stdout"
            forged=rewrite_selected(raw,change)
            with self.assertRaises(core.SelectionError):world.verify(forged)

    def test_png_header_geometry_and_color_type_are_checked_independently(self):
        header=b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"+struct.pack(">IIBBBBB",1920,1080,8,6,0,0,0)+b"0000"
        self.assertEqual(film.png_header(header),6)
        for blob in (b"not a PNG",header[:16]+struct.pack(">I",1)+header[20:],header[:25]+b"\x09"+header[26:]):
            with self.assertRaises(core.SelectionError):film.png_header(blob)



class FilmSharedStageCustodyTests(unittest.TestCase):
    """Retained synthetic records cannot borrow success from process exit zero."""
    def rejected(self, world):
        with world.patched():
            raw, manifest = world.capture()
            with self.assertRaises(core.SelectionError): world.verify(raw)
        self.assertFalse(manifest["physical_acceptance"])
        self.assertFalse(manifest["human_acceptance"])
        self.assertFalse(manifest["release_authorized"])
        return raw, manifest

    def test_missing_master_terminal_keeps_render_but_blocks_complete_custody(self):
        world=World("film180-g2")
        del world.files[("observation","original-shared-session/master.terminal.json")]
        raw,manifest=self.rejected(world)
        rows={r["label"]:r for r in manifest["film_command_originals"]["native_stages"]}
        self.assertEqual(rows["original.render"]["reported_result"],"SUCCESS_REPORTED")
        self.assertEqual(rows["original.master"]["reported_result"],"UNKNOWN")
        self.assertIn(world.files[("observation","original-shared-session/master.started.json")],raw)

    def test_different_producer_pids_cannot_claim_one_shared_owner(self):
        world=World("film180-g2")
        for suffix in ("started","terminal"):
            world.edit("observation","edited-shared-session/master."+suffix+".json",lambda v:v.update(producer_pid=9876))
        _,manifest=self.rejected(world)
        rows=[r for r in manifest["film_command_originals"]["native_stages"] if r["phase"]=="edited"]
        self.assertTrue(all(not r["session_link_verified"] for r in rows))

    def test_foreign_canonical_film_is_rejected_even_when_all_child_records_agree(self):
        world=World("film180-g2")
        def stage(v):
            v["canonical_Film_sha256"]="f"*64
            if v["result"] is not None:v["result"]["canonical_Film_sha256"]="f"*64
        for name in ("render.started","render.terminal","master.started","master.terminal"):
            world.edit("observation","original-shared-session/"+name+".json",stage)
        def session(v):
            v["canonical_Film_sha256"]="f"*64
            for key in ("render","master"):v[key]["canonical_Film_sha256"]="f"*64
        world.edit("observation","original.session.stdout",session)
        stdout=world.files[("observation","original.session.stdout")]
        world.edit("observation","original.session.terminal.json",lambda v:v.update(stdout_sha256=core.sha(stdout),stdout_bytes=len(stdout)))
        self.rejected(world)

    def test_stage_terminal_must_link_actual_selected_native_receipt_bytes(self):
        world=World("film180-g2")
        world.edit("observation","edited-shared-session/master.terminal.json",lambda v:v.update(evidence_sha256="0"*64))
        raw,manifest=self.rejected(world)
        self.assertIn(world.files[("observation","edited-shared-session/master.terminal.json")],raw)
        row=next(r for r in manifest["film_command_originals"]["native_stages"] if r["label"]=="edited.master")
        self.assertEqual(row["capture_status"],"INCOMPLETE_OR_UNKNOWN")

    def test_exit_zero_cannot_attest_a_recreated_coordinator_or_human_acceptance(self):
        for key in ("coordinator_recreated_between_stages","human_approval"):
            with self.subTest(key=key):
                world=World("film180-g2")
                def change(v):
                    if key=="human_approval":v["render"][key]=True
                    else:v[key]=True
                world.edit("observation","original.session.stdout",change)
                stdout=world.files[("observation","original.session.stdout")]
                world.edit("observation","original.session.terminal.json",lambda v:v.update(stdout_sha256=core.sha(stdout),stdout_bytes=len(stdout)))
                self.rejected(world)

    def test_original_unknown_terminal_is_retained_without_fabricating_success(self):
        world=World("film180-g2")
        world.edit("observation","edited-shared-session/master.terminal.json",lambda v:v.update(state="ERROR_OR_UNKNOWN",result=None,evidence_sha256=None))
        raw,manifest=self.rejected(world)
        row=next(r for r in manifest["film_command_originals"]["native_stages"] if r["label"]=="edited.master")
        self.assertEqual(row["capture_status"],"ORIGINAL_STAGE_RETAINED")
        self.assertEqual(row["reported_result"],"ERROR_OR_UNKNOWN")
        self.assertIn(world.files[("observation","edited-shared-session/master.terminal.json")],raw)

class DedicatedPcmCaptureTests(unittest.TestCase):
    def fixture(self,raw_failure=False,write_failure=False):
        tree=ast.parse(source_text("tooling/native_film180_original_g2.py"))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="FilmAttempt")
        function=copy.deepcopy(next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="capture_original_pcm"))
        error_class=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="Film180G2Rejected"))
        original_require=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="require"))
        code=compile(ast.fix_missing_locations(ast.Module(body=[error_class,original_require,function],type_ignores=[])),"original-producer-method","exec")
        self.raw=[];self.files={}
        def write(path,data):
            if write_failure:raise OSError("synthetic custody write failure")
            if str(path) in self.files:raise FileExistsError()
            self.files[str(path)]=data
        def capture(label):
            def keep(value):
                self.raw.append((label,value))
                if raw_failure:raise OSError("synthetic raw custody failure")
            return keep
        namespace=dict(require=core.require,HEX64=re.compile(r"[0-9a-f]{64}\Z"),hashlib=hashlib,new_bytes=write,write_private_json=write)
        exec(code,namespace)
        attempt=types.SimpleNamespace(evidence_dir=Path("/pure-synthetic-memory"),capture=capture)
        return namespace["capture_original_pcm"](attempt,"original","a"*64)

    def payload(self,pcm=PCM180,exit_code=0,deadline=False):
        return dict(stdout=pcm,stderr=b"PRIVATE-DIAGNOSTIC-SENTINEL",exit_code=exit_code,deadline_exceeded=deadline,automatic_retry=False)

    def test_direct_callback_preserves_all_original_pcm_before_content_inspection(self):
        callback=self.fixture();pcm=PCM180+b"\0"*(2048*4);payload=self.payload(pcm)
        callback(payload)
        self.assertIs(self.raw[0][1],payload)
        self.assertEqual(self.files[str(Path("/pure-synthetic-memory") / "original-original-master-decoded.s16le")],pcm)
        receipt=self.files[str(Path("/pure-synthetic-memory") / "original-original-pcm-capture.json")]
        self.assertEqual(receipt["decoded_stereo_frames"],290048)
        self.assertEqual(receipt["decoded_pcm_sha256"],hashlib.sha256(pcm).hexdigest())
        self.assertFalse(receipt["packet_PTS_and_AAC_priming_verified"])
        self.assertNotIn("stderr",receipt)

    def test_unknown_timeout_or_nonzero_decode_never_creates_dedicated_success(self):
        for exit_code,deadline in ((None,True),(1,False),(False,False)):
            callback=self.fixture();callback(self.payload(b"partial",exit_code,deadline))
            self.assertEqual(len(self.raw),1);self.assertEqual(self.files,{})

    def test_partial_stereo_or_oversize_payload_is_not_trimmed_into_a_valid_capture(self):
        for pcm in (b"abc",b"x"*(2*1024*1024+4)):
            callback=self.fixture()
            with self.assertRaises(ValueError):callback(self.payload(pcm))
            self.assertEqual(self.files,{})
            self.assertEqual(self.raw[0][1]["stdout"],pcm)

    def test_callback_latch_blocks_replay_after_success_raw_failure_or_write_failure(self):
        for raw_failure,write_failure in ((False,False),(True,False),(False,True)):
            callback=self.fixture(raw_failure,write_failure)
            try:callback(self.payload())
            except OSError:pass
            before=len(self.raw)
            with self.assertRaisesRegex(ValueError,"replay_refused"):callback(self.payload())
            self.assertEqual(len(self.raw),before)

    def test_actual_stage_passes_one_direct_pcm_callback_without_stdout_reread(self):
        tree=ast.parse(source_text("tooling/native_film180_original_g2.py"))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="stage_and_decode")
        calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="verified_source_readback"]
        self.assertEqual(len(calls),1)
        argument=next(k.value for k in calls[0].keywords if k.arg=="pcm_capture")
        self.assertEqual(ast.unparse(argument),"attempt.capture_original_pcm(stage, video_sha)")



class DedicatedPacketCaptureTests(unittest.TestCase):
    """Actual producer function, process and file outputs replaced by memory only."""
    def fixture(self,raw=None,exit_code=0,failure=None,changed_master=False,write_failure=False):
        import subprocess
        tree=ast.parse(source_text("tooling/native_film180_original_g2.py"))
        wanted=("Film180G2Rejected","require","retain_original_audio_packets")
        nodes=[copy.deepcopy(n) for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in wanted]
        self.files={};self.calls=[];self.digests=[];self.secondary=[]
        self.stderr=b"PRIVATE-DIAGNOSTIC-SENTINEL"
        self.write_error=OSError("synthetic write failure")
        if raw is None:raw=core.canonical(World("film180-g2").packets())
        def write(path,data):
            if write_failure:raise self.write_error
            if str(path) in self.files:raise FileExistsError()
            self.files[str(path)]=copy.deepcopy(data)
        def digest(path):
            self.digests.append(str(path));return "b"*64 if changed_master and len(self.digests)>1 else "a"*64
        def run(argv,**kwargs):
            self.calls.append((argv,kwargs))
            kwargs["stdout"].write(raw)
            if failure is not None:raise failure
            return types.SimpleNamespace(returncode=exit_code,stderr=self.stderr)
        namespace=dict(HEX64=re.compile(r"[0-9a-f]{64}\Z"),hashlib=hashlib,new_bytes=write,
                       write_private_json=write,digest=digest,
                       subprocess=types.SimpleNamespace(run=run,PIPE=-1,TimeoutExpired=subprocess.TimeoutExpired))
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),"original-packet-producer","exec"),namespace)
        attempt=types.SimpleNamespace(evidence_dir=Path("/pure-synthetic-memory"),command_labels=set(),
                                      secondary=self.secondary.append)
        def call():
            with mock.patch("tempfile.TemporaryFile",side_effect=io.BytesIO):
                namespace["retain_original_audio_packets"](attempt,"original",Path("/owner/output/master.mp4"),"a"*64)
        return call,raw

    def assert_probe_originals(self,raw,exit_code,deadline=False,error_class=None):
        root=Path("/pure-synthetic-memory")
        prefix="original.original-packet-probe"
        stdout_path=str(root / (prefix+".stdout"))
        stderr_path=str(root / (prefix+".stderr"))
        terminal_path=str(root / (prefix+".terminal.json"))
        terminal=self.files[terminal_path]
        bounded=len(raw)<=1024*1024
        expected={stderr_path,terminal_path}
        if bounded:
            expected.add(stdout_path)
            self.assertEqual(self.files[stdout_path],raw)
        else:
            self.assertNotIn(stdout_path,self.files)
        self.assertEqual(self.files[stderr_path],self.stderr)
        self.assertEqual(terminal["schema"],"motionwright.native.film180.original-packet-probe/1")
        self.assertEqual(terminal["phase"],"original")
        self.assertEqual(terminal["master_sha256"],"a"*64)
        self.assertEqual(terminal["argv"],self.calls[0][0])
        self.assertIs(type(terminal["exit_code"]),type(exit_code))
        self.assertEqual(terminal["exit_code"],exit_code)
        self.assertIs(terminal["deadline_exceeded"],deadline)
        self.assertEqual(terminal["error_class"],error_class)
        self.assertEqual(terminal["stdout_observed_bytes"],len(raw))
        self.assertEqual(terminal["stderr_observed_bytes"],len(self.stderr))
        self.assertEqual(terminal["stdout_sha256"],core.sha(raw) if bounded else None)
        self.assertEqual(terminal["stderr_sha256"],core.sha(self.stderr))
        self.assertIs(terminal["stdout_original_included"],bounded)
        self.assertIs(terminal["stderr_original_included"],True)
        self.assertIs(terminal["stdout_within_retention_bound"],bounded)
        self.assertIs(terminal["stderr_within_retention_bound"],True)
        self.assertIs(terminal["terminal_captured_before_json_parse"],True)
        for field in ("automatic_retry","native_effect_invoked","native_terminal_inferred_from_exit"):
            self.assertIs(terminal[field],False)
        self.assertEqual(terminal["custody_failures"],[])
        self.assertEqual(self.secondary,[])
        return expected

    def test_one_readonly_probe_preserves_original_packet_document_and_master_binding(self):
        call,raw=self.fixture();call()
        argv,options=self.calls[0]
        self.assertEqual(argv,["ffprobe","-v","error","-select_streams","a:0","-show_streams","-show_packets","-of","json",str(Path("/owner/output/master.mp4"))])
        self.assertEqual(options["timeout"],60)
        self.assertEqual(self.files[str(Path("/pure-synthetic-memory") / "original-original-master-packets.json")],raw)
        receipt=self.files[str(Path("/pure-synthetic-memory") / "original-original-packet-capture.json")]
        self.assertEqual(receipt["master_sha256"],"a"*64)
        self.assertEqual(receipt["original_packet_document_sha256"],core.sha(raw))
        self.assertIs(receipt["packet_PTS_and_AAC_priming_verified"],False)
        self.assertNotIn("stderr",receipt)
        expected=self.assert_probe_originals(raw,0)
        expected.update(str(Path("/pure-synthetic-memory") / name) for name in (
            "original-original-master-packets.json","original-original-packet-capture.json"))
        self.assertEqual(set(self.files),expected)
        before_replay=copy.deepcopy(self.files)
        with self.assertRaisesRegex(ValueError,"replay_refused"):call()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.files,before_replay)

    def test_unknown_nonzero_bool_oversize_or_malformed_packets_never_yield_original_success(self):
        cases = [
            ("boolean-terminal", dict(exit_code=False), False),
            ("nonzero-terminal", dict(exit_code=1), False),
            ("oversize-original", dict(raw=b"x"*(1024*1024+1)), False),
            ("malformed-json", dict(raw=b"not JSON"), True),
            ("missing-packet-fields", dict(raw=b"{}"), True),
        ]
        original_path = str(Path("/pure-synthetic-memory") / "original-original-master-packets.json")
        receipt_path = str(Path("/pure-synthetic-memory") / "original-original-packet-capture.json")
        for label, kwargs, retains_original in cases:
            with self.subTest(case=label):
                call, raw = self.fixture(**kwargs)
                with self.assertRaises(ValueError):
                    call()
                # Probe originals and terminal survive rejected observations;
                # only exit0/bounded/unchanged input reaches packet parsing.
                expected = self.assert_probe_originals(raw, kwargs.get("exit_code", 0))
                if retains_original:
                    expected.add(original_path)
                self.assertEqual(set(self.files), expected)
                self.assertNotIn(receipt_path, self.files)
                if retains_original:
                    self.assertEqual(self.files[original_path], kwargs["raw"])
                    with self.assertRaises(core.SelectionError):
                        core.project(core.strict(self.files[original_path]), mlt.PACKETS)
                before_replay = copy.deepcopy(self.files)
                with self.assertRaisesRegex(ValueError, "replay_refused"):
                    call()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.files, before_replay)

    def test_timeout_master_mutation_and_write_failure_are_latched_without_probe_replay(self):
        import subprocess
        timeout=subprocess.TimeoutExpired(["ffprobe"],60,stderr=b"PRIVATE-DIAGNOSTIC-SENTINEL")
        cases=[("timeout",dict(raw=b"original partial packets",failure=timeout)),
               ("changed-master",dict(changed_master=True)),("write-failure",dict(write_failure=True))]
        for label,kwargs in cases:
            with self.subTest(case=label):
                call,raw=self.fixture(**kwargs)
                if "failure" in kwargs:
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:call()
                    self.assertIs(caught.exception,timeout)
                    expected=self.assert_probe_originals(raw,None,True,"TimeoutExpired")
                elif kwargs.get("write_failure"):
                    with self.assertRaises(OSError) as caught:call()
                    self.assertIs(caught.exception,self.write_error)
                    self.assertEqual(self.secondary,["original.original-packet-probe.capture"])
                    expected=set()
                else:
                    with self.assertRaisesRegex(ValueError,"master_changed_during_packet_probe"):call()
                    expected=self.assert_probe_originals(raw,0)
                self.assertEqual(set(self.files),expected)
                self.assertNotIn(str(Path("/pure-synthetic-memory") / "original-original-packet-capture.json"),self.files)
                self.assertNotIn(str(Path("/pure-synthetic-memory") / "original-original-master-packets.json"),self.files)
                before_replay=copy.deepcopy(self.files)
                with self.assertRaisesRegex(ValueError,"replay_refused"):call()
                self.assertEqual(len(self.calls),1)
                self.assertEqual(self.files,before_replay)

    def test_actual_stage_probes_the_published_master_once_before_same_decode_capture(self):
        tree=ast.parse(source_text("tooling/native_film180_original_g2.py"))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="stage_and_decode")
        calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        packet=[n for n in calls if n.func.id=="retain_original_audio_packets"]
        readback=[n for n in calls if n.func.id=="verified_source_readback"]
        self.assertEqual(len(packet),1);self.assertEqual(len(readback),1)
        self.assertEqual([ast.unparse(n) for n in packet[0].args],["attempt","stage","original_mp4","video_sha"])
        self.assertLess(packet[0].lineno,readback[0].lineno)


if __name__=="__main__":unittest.main()

class FilmCommandOriginalCustodyTests(unittest.TestCase):
    """Real private filesystem/ciphertext tests; no native renderer is invoked."""
    @classmethod
    def setUpClass(cls):
        import tempfile
        import subprocess
        import shutil
        import native_film180_command_custody as commands
        cls.commands = commands
        cls.home = tempfile.TemporaryDirectory(prefix="film-command-source-")
        cls.source = Path(cls.home.name) / "source"
        cls.source.mkdir(mode=0o700)
        (cls.source / "tooling").mkdir()
        import native_film180_evidence_selection as film
        import native_mlt_evidence_selection as selection
        cls.source_paths = ("tooling/native_evidence_projection.py",
                            "tooling/native_film180_command_custody.py",
                            "tooling/native_film180_evidence_selection.py",
                            "tooling/native_mlt_evidence_selection.py")
        for name, module in zip(cls.source_paths, (core, commands, film, selection)):
            shutil.copyfile(module.__file__, cls.source / name)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(cls.source), *args], stderr=subprocess.DEVNULL).decode().strip()
        git("init", "--quiet")
        git("config", "user.email", "source-test@example.invalid")
        git("config", "user.name", "Film custody source test")
        git("add", "tooling")
        git("commit", "--quiet", "-m", "Synthetic source-only custody fixture")
        from native_mlt_evidence_selection import SEM
        cls.current = dict(source_sha=git("rev-parse", "HEAD"), semwright_sha=SEM,
                           run_id="34567", run_attempt="1", scenario="film180-g2")
        cls.bindings = [dict(path=name, sha256=core.sha((cls.source/name).read_bytes()))
                        for name in cls.source_paths]
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import base64
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = dict(spki_der_base64=base64.b64encode(der).decode(), sha256=core.sha(der))

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory(prefix="film-command-originals-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / "control"; self.control.mkdir(mode=0o700)
        self.observation = self.root / "observation"; self.observation.mkdir(mode=0o700)
        self.anchor = b"SYNTHETIC_SOURCE_ONLY_CONTROL"
        (self.control / "anchor.bin").write_bytes(self.anchor)
        self.contract = self.commands.contract(self.current)
        self.roles = self.commands.role_contract()
        self.roles[("single", "attempt-custody", "anchor")] = (1, 1)

    def put(self, path, raw):
        target = self.observation / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
        return target

    def command(self, label="original.session", stdout=b"PRIVATE-PARTIAL-STDOUT-\xff\x00", stderr=b"", *, code=None, deadline=True):
        self.put(label+".stdout", stdout)
        self.put(label+".stderr", stderr)
        self.put(label+".intent.json", b'{"argv":["PRIVATE-OWNER-PATH","render"],"automatic_retry":false}')
        terminal = dict(exit_code=code, deadline_exceeded=deadline, automatic_retry=False,
            stdout_sha256=core.sha(stdout), stdout_bytes=len(stdout), stderr_sha256=core.sha(stderr),
            stderr_bytes=len(stderr), native_terminal_inferred_from_exit=False)
        self.put(label+".terminal.json", core.canonical(terminal))
        return stdout, stderr

    def spec(self, *, root_present=True):
        roots = dict(source=str(self.source), control=str(self.control))
        if root_present: roots["observation"] = str(self.observation)
        rows = [dict(root="control", relative_path="anchor.bin", phase="single", layer="attempt-custody",
            role="anchor", kind="original", required=True, expected_sha256=core.sha(self.anchor),
            bytes=len(self.anchor), policy=None)]
        # Exercise the actual Film collector and Selection.add with real private
        # filesystem handles. The generic capture below verifies committed copies
        # of all four executing source modules; no native owner is constructed.
        import native_film180_evidence_selection as film
        collector = film.FilmSelection.__new__(film.FilmSelection)
        collector.roots = roots; collector.context = self.current
        collector.diagnostic_contract = self.contract; collector.contract = self.roles
        collector.policies = {}; collector.members = rows; collector.absence = []; collector.cache = {}
        handles = core.RootHandles(roots)
        try:
            handles.admit_diagnostic_contract(self.contract)
            collector.collect_command_originals(handles)
        finally:
            handles.close()
        guards = collector.absence
        return dict(schema=core.SPEC_SCHEMA_V3, context=self.current, roots=roots, members=rows,
            absence_guards=guards, exclusions=[], source_bindings=self.bindings,
            origin=dict(kind="generated-first-party-fixture", fixture_id="film180-g2",
                producer_paths=[self.source_paths[1]], private_data=True,
                upload_authorization="NOT_GRANTED_BY_COLLECTOR"))

    def capture(self, spec=None):
        return core.capture_selected_originals(spec or self.spec(), policies={},
            role_contract=self.roles, diagnostic_contract=self.contract)

    def inspect(self, raw):
        return core.inspect_selected_tar(raw, expected_context=self.current,
            role_contract=self.roles, policies={}, expected_diagnostic_contract=self.contract,
            require_complete=False)

    def row(self, manifest, path):
        return next(row for row in manifest["members"] if row["relative_path"] == path)

    def test_source_owned_roster_has_16_commands_68_real_names_and_finite_limits(self):
        self.assertEqual(len(self.commands.COMMANDS), 16)
        self.assertEqual(len(self.contract["entries"]), 68)
        self.assertEqual(self.commands.MAX_TOTAL, 92471296)
        names = {row["relative_path"] for row in self.contract["entries"]}
        self.assertNotIn("edited.stage-audio.intent.json", names)
        for phase in ("original","edited"):
            self.assertNotIn(phase+".original-pcm-decode.intent.json", names)
            self.assertNotIn(phase+".original-packet-probe.intent.json", names)
        self.assertTrue(all(not name.startswith("fixed.") for name in names))
        self.assertIn("original-attempt-error.json", names)

    def test_partial_binary_and_zero_stream_survive_original_tar_and_authenticated_encryption(self):
        import ci_private_evidence as crypto
        stdout, stderr = self.command()
        error = b'{"error":"PRIVATE-ORIGINAL-ERROR","traceback":"PRIVATE-TRACE","automatic_retry":false}'
        self.put("original-attempt-error.json", error)
        raw, manifest = self.capture(); self.inspect(raw)
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for path, expected in (("original.session.stdout",stdout),("original.session.stderr",stderr),
                                   ("original-attempt-error.json",error)):
                row = self.row(manifest,path)
                self.assertEqual(archive.extractfile(row["member"]).read(), expected)
                self.assertEqual(row["retained_bytes"], len(expected))
                self.assertEqual(row["retained_sha256"], core.sha(expected))
        selected = next(row for row in manifest["film_command_originals"]["commands"] if row["label"]=="original.session")
        self.assertEqual(selected["capture_status"], "ORIGINALS_RETAINED")
        self.assertEqual(selected["reported_result"], "UNKNOWN")
        self.assertFalse(manifest["film_command_originals"]["stream_eof_claimed"])
        context = {key:value for key,value in self.current.items() if key!="scenario"}
        sealed = crypto.encrypt_snapshot(raw,self.recipient,context)
        self.assertNotIn(stdout,sealed)
        self.assertNotIn(error,sealed)
        recovered, bound = crypto.decrypt_snapshot(sealed,self.key)
        self.assertEqual(recovered,raw); self.assertEqual(bound,context)

    def test_absence_is_explicit_and_does_not_invent_a_terminal_or_no_effects(self):
        raw, manifest = self.capture(); self.inspect(raw)
        self.assertEqual(len(manifest["absence_guards"]),68)
        self.assertTrue(all(row["capture_status"]=="ABSENT_OBSERVED"
            for row in manifest["film_command_originals"]["commands"]))
        self.assertFalse(manifest["film_command_originals"]["absent_means_no_effects"])
        self.assertFalse(manifest["raw_diagnostics_included"])
        with self.assertRaises(core.SelectionError):
            self.commands.require_successful_capture(manifest["film_command_originals"])

    def test_missing_stderr_is_not_an_invented_empty_original(self):
        self.command(stdout=b"",stderr=b"partial")
        (self.observation/"original.session.stderr").unlink()
        raw, manifest=self.capture(); self.inspect(raw)
        self.assertEqual(self.row(manifest,"original.session.stdout")["retained_bytes"],0)
        self.assertFalse(any(row["relative_path"]=="original.session.stderr" for row in manifest["members"]))
        row=next(row for row in manifest["film_command_originals"]["commands"] if row["label"]=="original.session")
        self.assertEqual(row["capture_status"],"INCOMPLETE_OR_UNKNOWN")
        self.assertEqual(row["parts"]["stderr"]["state"],"ABSENT_OBSERVED")

    def test_unlisted_neighbors_and_other_scope_cannot_enter_the_nofollow_reader(self):
        self.put("neighbor.stdout",b"NEIGHBOR-MUST-NOT-BE-READ")
        self.put("nested/original.session.stdout",b"NESTED-MUST-NOT-BE-READ")
        handles=core.RootHandles(dict(observation=str(self.observation)))
        try:
            with self.assertRaises(core.SelectionError):
                handles.read("observation",("original.session.stdout",),1024)
            handles.admit_diagnostic_contract(self.contract)
            for path in ("neighbor.stdout","nested/original.session.stdout","fixed.project-inspect.stdout"):
                with self.assertRaises(core.SelectionError):
                    handles.read("observation",core.member_path(path),1024)
        finally: handles.close()
        wrong=copy.deepcopy(self.contract);wrong["context"]["scenario"]="fixedtxt-v4"
        with self.assertRaises(core.SelectionError):
            core._checked_diagnostic_contract(wrong,wrong["context"])

    def test_symlink_and_hardlink_are_failed_originals_without_following_them(self):
        import os
        target=self.put("private-target.bin",b"ORIGINAL-SECRET-TARGET")
        os.symlink(target,self.observation/"original.session.stdout")
        os.link(target,self.observation/"original.session.stderr")
        raw,manifest=self.capture();self.inspect(raw)
        self.assertNotIn(b"ORIGINAL-SECRET-TARGET",raw)
        for path in ("original.session.stdout","original.session.stderr"):
            self.assertEqual(self.row(manifest,path)["state"],"CAPTURE_FAILED")
        self.assertFalse(manifest["diagnostic_roster_complete"])

    def test_oversize_is_failed_not_truncated_and_keeps_other_originals(self):
        self.command()
        path=self.observation/"original.session.stdout"
        with path.open("wb") as stream: stream.truncate(self.commands.MAX_STDOUT+1)
        raw,manifest=self.capture();self.inspect(raw)
        self.assertEqual(self.row(manifest,"original.session.stdout")["state"],"CAPTURE_FAILED")
        self.assertEqual(self.row(manifest,"original.session.stderr")["state"],"INCLUDED")
        self.assertFalse(manifest["diagnostic_roster_complete"])

    def test_changed_selected_bytes_are_retained_as_uncertain_not_replaced(self):
        self.command()
        spec=self.spec()
        self.put("original.session.stdout",b"CHANGED-AFTER-PREFLIGHT")
        raw,manifest=self.capture(spec)
        self.assertEqual(self.row(manifest,"original.session.stdout")["state"],"CHANGED")
        self.assertNotIn(b"CHANGED-AFTER-PREFLIGHT",raw)
        self.assertFalse(manifest["required_layers_complete"])

    def test_contract_source_run_attempt_or_limit_changes_fail_before_any_open(self):
        for change in (lambda c:c["context"].update(source_sha="b"*40),
                       lambda c:c["context"].update(run_id="34568"),
                       lambda c:c["context"].update(run_attempt="2"),
                       lambda c:c["entries"][0].update(maximum=self.commands.MAX_INTENT+1),
                       lambda c:c["entries"][0].update(relative_path="unlisted.intent.json")):
            spec=self.spec(); changed=copy.deepcopy(self.contract);change(changed)
            with mock.patch.object(core,"RootHandles",side_effect=AssertionError("no path may be opened")):
                with self.assertRaises(core.SelectionError):
                    core.capture_selected_originals(spec,policies={},role_contract=self.roles,
                        diagnostic_contract=changed)

    def test_missing_observation_root_is_unobserved_not_an_absence_proof(self):
        raw,manifest=self.capture(self.spec(root_present=False));self.inspect(raw)
        self.assertEqual(len(manifest["absence_guards"]),0)
        diagnostics=[row for row in manifest["members"] if row["kind"]=="diagnostic-original"]
        self.assertEqual(len(diagnostics),68)
        self.assertTrue(all(row["state"]=="CAPTURE_FAILED" for row in diagnostics))
        self.assertFalse(manifest["diagnostic_roster_complete"])

    def test_forged_terminal_summary_cannot_promote_unknown_to_exit_zero(self):
        self.command()
        raw,manifest=self.capture()
        import tarfile
        entries={}
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
            for info in archive.getmembers():entries[info.name]=archive.extractfile(info).read()
        for row in manifest["film_command_originals"]["commands"]:
            if row["label"]=="original.session":row["reported_result"]="EXIT_ZERO_REPORTED"
        entries["selected-evidence-manifest.json"]=core.canonical(manifest)
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode="w",format=tarfile.USTAR_FORMAT) as archive:
            for name,data in entries.items():
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600
                archive.addfile(info,io.BytesIO(data))
        with self.assertRaisesRegex(core.SelectionError,"film-command-summary-originals"):
            self.inspect(stream.getvalue())

    def test_private_public_projection_contains_no_original_diagnostics_or_success_credit(self):
        import native_mlt_g2_custody as custody
        import native_mlt_evidence_selection as selection
        import ci_private_evidence as crypto
        self.command(stdout=b"PRIVATE-RAW-OUTPUT-SENTINEL")
        self.put("original-attempt-error.json",b'{"error":"PRIVATE-ERROR-SENTINEL"}')
        raw,manifest=self.capture()
        current={key:value for key,value in self.current.items() if key!="scenario"}
        encrypted=crypto.encrypt_snapshot(raw,self.recipient,current)
        public=custody.selected_projection(current,None,1,"failure",encrypted,self.recipient,
            manifest,producer_started=True,source_unchanged=True,intake_verified=False,
            scenario="film180-g2",diagnostic_intake_verified=True)
        selection.validate_public_evidence(public)
        self.assertEqual(public["schema"],"motionwright.native.film180-g2-public/3")
        self.assertTrue(public["command_originals_intake_verified"])
        self.assertTrue(public["raw_diagnostics_included"])
        self.assertFalse(public["selected_intake_verified"])
        self.assertFalse(public["source_content_and_live_module_verified"])
        self.assertNotIn(b"PRIVATE-",core.canonical(public))
        self.assertEqual(public["native_terminal"],"ERROR_OR_UNKNOWN")
        for name in ("physical_AV_master_acceptance","human_acceptance","release_authorized"):
            self.assertFalse(public[name])

    def test_actual_changed_collector_source_rejects_capture_before_selected_payloads(self):
        self.command()
        spec=self.spec()
        source=self.source/self.source_paths[0]
        original=source.read_bytes()
        try:
            source.write_bytes(original+b"\n# changed source fixture\n")
            with self.assertRaises(core.SelectionError):
                self.capture(spec)
        finally:
            source.write_bytes(original)


def fixedtxt_fixture(*, fixture="flow-control-base", arm="RED", decoded=False):
    """Synthetic source-only originals; no caller, renderer or kernel evidence."""
    import ci_private_evidence as crypto
    current = {**CURRENT, "semwright_sha": "c" * 40 if arm == "GREEN" else CURRENT["semwright_sha"]}
    directory = "output/synthetic-fixed-frame-source"
    mandatory = film.fixedtxt_required_members(fixture, directory)
    originals = {root + "/" + path: b'{"source_control_fixture":true}\n'
                 for root, path in mandatory}
    readback = "observation/" + fixture + "/fixed-text-original-readback/"
    png = b"SYNTHETIC-PNG-ORIGINAL-NOT-A-NATIVE-RENDER"
    rgba = b"\x00\x11\x22\xff" * (1920 * 1080) if decoded else b"SHORT-SYNTHETIC-RGBA"
    frames = list(film.fixedtxt_frames(fixture))
    manifest = {"frames": [{"index": i, "file": f"frames/{i:06d}.png", "bytes": len(png),
                             "sha256": crypto.sha(png)} for i in range(180)]}
    originals[readback + "artifact-manifest.json"] = crypto.json_bytes(manifest)
    for i in range(180):
        originals["owner/" + directory + f"/frames/{i:06d}.png"] = png
    font = b"SYNTHETIC-FONT-ORIGINAL-NOT-A-FONT"
    font_path = readback + "font-0000.original.bin"
    originals[font_path] = font
    originals[readback + "font-source-inventory.json"] = crypto.json_bytes({
        "originals": [{"role": "bundled", "path": "fonts/source.woff2", "retained": "font-0000.original.bin",
                       "bytes": len(font), "sha256": crypto.sha(font)}],
        "glyph_fidelity_accepted": False, "browser_readiness_is_glyph_fidelity": False})
    originals["inputs/source-input.original.json"] = b'{"explicit_synthetic_input":true}\n'
    rows = []
    for frame in frames:
        stem = f"frame-{frame:06d}"
        originals[readback + stem + ".original.png"] = png
        originals[readback + stem + ".decoder.stdout"] = rgba
        originals[readback + stem + ".decoder.stderr"] = b""
        capture = dict(state="TERMINAL_OBSERVED", automatic_retry=False,
            terminal=dict(whole_process_completed=True, exit_code=0, timed_out=False,
                          output_budget_exceeded=False, pipes_closed=True, stdin_complete=True, pipe_errors=[]),
            original_streams={name:dict(present=True, bytes=len(data), sha256=crypto.sha(data),
                                       buffer_collected_from_process=True)
                              for name, data in (("stdout", rgba), ("stderr", b""))},
            invocation=dict(callable="decoded_rgba_png_capture", width=1920, height=1080,
                            decoder="/synthetic/ffmpeg", decoder_sha256="d" * 64,
                            input_png_sha256=crypto.sha(png)))
        originals[readback + stem + ".decoder.capture.json"] = crypto.json_bytes(capture)
        rows.append(dict(frame=frame, png_sha256=crypto.sha(png), png_bytes=len(png),
            rgba_sha256=crypto.sha(rgba), rgba_bytes=len(rgba), original_rgba_file=stem + ".decoder.stdout",
            compared_with_original_SDK_pixel_sample=False))
    selected = dict(schema="motionwright.native.fixed-text-selected-originals/4", arm=arm, fixture=fixture,
        manifest_sha256=crypto.sha(originals[readback + "artifact-manifest.json"]),
        native_observation_sha256=crypto.sha(originals[readback + "native-observations.ndjson"]),
        original_Host_stdin_sha256=crypto.sha(originals["observation/" + fixture + "/native-result.host-stdin/capture-0001.stdin"]),
        original_font_readiness_sha256=crypto.sha(originals[readback + "font-readiness-receipt.json"]),
        selected_frames=rows, actual_browser_readiness_observed=True, native_effect_repeated=False,
        oracle_v3_reused=False, causal_pixel_verdict="NOT_EVALUATED", glyph_fidelity_accepted=False,
        formal_acceptance=False, human_acceptance=False)
    originals[readback + "selected-originals.json"] = crypto.json_bytes(selected)
    members = []
    for name, data in sorted(originals.items()):
        root, path = name.split("/", 1)
        role = mandatory.get((root, path), "font-original" if name == font_path else "input-original")
        maximum = film.FIXEDTXT_RGBA_BYTES if role == "selected-rgba" else (
            film.FIXEDTXT_MAX_JSON if role == "selection-plan" else max(4096, len(data)))
        pin = crypto.sha(data) if role in ("cycle-inputs", "input-original") else None
        members.append(dict(root=root, relative_path=path, role=role, maximum=maximum, expected_sha256=pin))
    plan = dict(schema=film.FIXEDTXT_PLAN_SCHEMA, scope=film.FIXEDTXT_SCOPE, context=current,
        arm=arm, fixture=fixture, case_roster=film.fixedtxt_case_roster(),
        source_bindings=[dict(path=path, sha256="b" * 64) for path in film.FIXEDTXT_SOURCE_PATHS],
        arm_sources={"RED":dict(source_sha=CURRENT["semwright_sha"], tree_sha="e" * 40, native_source_sha256="e" * 64),
                     "GREEN":dict(source_sha="c" * 40, tree_sha="f" * 40, native_source_sha256="f" * 64)},
        cycle_input_sha256=crypto.sha(originals["observation/fixed-text-inputs.original.json"]),
        decoder_sha256="d" * 64, native_directory=directory, owner_count=1, expected_fixture_count=1,
        default_fixture_count=12, render_frames=180, selected_frames=frames,
        limits=dict(capsule_bytes=128 * 1024 * 1024, files=512, attempt_seconds=600, custody_seconds=120),
        members=members)
    originals["control/" + film.FIXEDTXT_PLAN_PATH] = crypto.json_bytes(plan)
    return current, plan, originals


def fixedtxt_snapshot_fixture(plan, originals):
    """Existing TAR/schema with explicitly simulated regular-file observations."""
    import ci_private_evidence as crypto
    import stat
    import tarfile
    roots = {name:dict(status="CAPTURED", files=[]) for name in film.FIXEDTXT_ROOTS}
    for index, (name, payload) in enumerate(sorted(originals.items()), 1):
        root, path = name.split("/", 1)
        mark = dict(device=1, inode=index, mode=stat.S_IFREG | 0o600, size=len(payload),
                    uid=1000, gid=1000, mtime_ns=1, ctime_ns=1)
        roots[root]["files"].append(dict(archive_path=name, observation=dict(
            path="/synthetic/" + name, size=len(payload), sha256=crypto.sha(payload),
            before=mark, after=mark, namespace_after=mark)))
    manifest = dict(schema="motionwright.ci.private-snapshot/1", scope="selected-original-files-at-read-time",
        roots=roots, selected_originals_complete=True, complete_owner_state_claimed=False)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, payload in sorted(originals.items()) + [("snapshot-manifest.json", crypto.json_bytes(manifest))]:
            info = tarfile.TarInfo(name); info.size = len(payload); info.mode = 0o600; info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


class FixedTxtClosedPlanTests(unittest.TestCase):
    def test_nominal_24_arms_keep_48_decodes_per_arm_without_dispatch_authority(self):
        roster = film.fixedtxt_case_roster()
        self.assertEqual(len(roster), 24)
        self.assertEqual(len({(row["arm"], row["fixture"]) for row in roster}), 24)
        for arm in film.FIXEDTXT_ARMS:
            self.assertEqual(sum(len(film.fixedtxt_frames(row["fixture"])) for row in roster if row["arm"] == arm), 48)
        self.assertEqual(12 * 180, 2160)
        self.assertEqual(96 * film.FIXEDTXT_RGBA_BYTES, 796262400)
        self.assertGreater(96 * film.FIXEDTXT_RGBA_BYTES, core.MAX_TOTAL)
        self.assertEqual(core.MAX_TOTAL, 512 * 1024 * 1024)
        self.assertEqual(core.MAX_FILES, 4096)

    def test_each_closed_fixture_has_one_explicit_arm_owner_and_actual_frame_roster(self):
        for arm in film.FIXEDTXT_ARMS:
            for fixture in film.FIXEDTXT_KEYS:
                with self.subTest(arm=arm, fixture=fixture):
                    current, plan, _ = fixedtxt_fixture(fixture=fixture, arm=arm)
                    self.assertEqual(film.fixedtxt_validate_plan(plan, current=current), plan)
                    self.assertEqual(plan["expected_fixture_count"], 1)
                    self.assertEqual(plan["default_fixture_count"], 12)
                    self.assertEqual(sum(row["role"] == "native-png" for row in plan["members"]), 180)

    def test_full_cycle_pair_unknown_fixture_and_boolean_counters_are_rejected(self):
        current, plan, _ = fixedtxt_fixture()
        for key, value in (("expected_fixture_count", 12), ("expected_fixture_count", True),
                           ("owner_count", 2), ("owner_count", True), ("arm", ["RED", "GREEN"]),
                           ("fixture", "all"), ("scope", "film180-g2"), ("render_frames", 2160)):
            with self.subTest(key=key, value=value), self.assertRaises(core.SelectionError):
                film.fixedtxt_validate_plan({**plan, key:value}, current=current)
        for roster in (plan["case_roster"][:-1], plan["case_roster"] + plan["case_roster"][:1],
                       list(reversed(plan["case_roster"]))):
            with self.assertRaisesRegex(core.SelectionError, "exact-24"):
                film.fixedtxt_validate_plan({**plan, "case_roster":roster}, current=current)

    def test_caps_include_full_originals_metadata_cipher_and_never_admit_full_pair(self):
        current, plan, _ = fixedtxt_fixture()
        for limits in ({**plan["limits"], "capsule_bytes":512 * 1024 * 1024 + 1},
                       {**plan["limits"], "files":4097}, {**plan["limits"], "files":True},
                       {**plan["limits"], "custody_seconds":600}, {**plan["limits"], "attempt_seconds":0}):
            with self.assertRaises(core.SelectionError):
                film.fixedtxt_validate_plan({**plan, "limits":limits}, current=current)
        changed = copy.deepcopy(plan)
        for row in changed["members"]:
            if row["role"] == "native-png": row["maximum"] = 32 * 1024 * 1024
        with self.assertRaisesRegex(core.SelectionError, "whole-capsule-budget"):
            film.fixedtxt_validate_plan(changed, current=current)
        changed = copy.deepcopy(plan)
        next(row for row in changed["members"] if row["role"] == "selected-rgba")["maximum"] -= 1
        with self.assertRaisesRegex(core.SelectionError, "full-rgba-bound"):
            film.fixedtxt_validate_plan(changed, current=current)

    def test_required_tail_selected_originals_and_paths_cannot_be_omitted_or_aliased(self):
        current, plan, _ = fixedtxt_fixture()
        for role in ("native-png", "host-stdin", "owner-closed-original", "selected-rgba", "decoder-terminal"):
            changed = copy.deepcopy(plan)
            victim = next(row for row in reversed(changed["members"]) if row["role"] == role)
            changed["members"].remove(victim)
            with self.subTest(role=role), self.assertRaises(core.SelectionError):
                film.fixedtxt_validate_plan(changed, current=current)
        for path in ("../outside", "/absolute", "a\\b", "a//b", "a/../b", "a\nb"):
            changed = copy.deepcopy(plan); changed["members"][0]["relative_path"] = path
            with self.assertRaises(core.SelectionError): film.fixedtxt_validate_plan(changed, current=current)
        changed = copy.deepcopy(plan); changed["members"].append(changed["members"][0])
        with self.assertRaisesRegex(core.SelectionError, "duplicate-original"):
            film.fixedtxt_validate_plan(changed, current=current)

    def test_arm_sources_input_and_member_roles_are_bound_independently(self):
        current, plan, _ = fixedtxt_fixture()
        # V4 binds one SDK revision and separate RED/GREEN native.ts originals.
        same_sdk = copy.deepcopy(plan)
        same_sdk["arm_sources"]["GREEN"].update(
            source_sha=plan["arm_sources"]["RED"]["source_sha"],
            tree_sha=plan["arm_sources"]["RED"]["tree_sha"])
        self.assertIs(film.fixedtxt_validate_plan(same_sdk, current=current), same_sdk)
        contradictory = copy.deepcopy(same_sdk)
        contradictory["arm_sources"]["GREEN"]["tree_sha"] = "0" * 40
        with self.assertRaisesRegex(core.SelectionError, "distinct-exact-arm-sources"):
            film.fixedtxt_validate_plan(contradictory, current=current)
        for change in (
            lambda value:value["arm_sources"].update(GREEN=copy.deepcopy(value["arm_sources"]["RED"])),
            lambda value:value["arm_sources"]["RED"].update(source_sha="0" * 40),
            lambda value:value["source_bindings"].pop(),
            lambda value:value.update(cycle_input_sha256="0" * 64),
            lambda value:next(row for row in value["members"] if row["role"] == "selected-rgba").update(role="input-original"),
        ):
            changed = copy.deepcopy(plan); change(changed)
            with self.assertRaises(core.SelectionError): film.fixedtxt_validate_plan(changed, current=current)

    def test_selected_rgba_is_reused_as_original_without_decode_or_native_success(self):
        current, plan, originals = fixedtxt_fixture(decoded=True)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("no decoder or native process")):
            result = film.fixedtxt_validate_selected_frames(originals, plan=plan)
        self.assertEqual(result["selected_decode_count"], 3)
        self.assertTrue(result["original_rgba_reused"])
        self.assertFalse(result["decoder_invoked_by_intake"])
        self.assertFalse(result["pair_comparison_performed"])
        self.assertFalse(result["native_success_verified"])

    def test_consistently_rehashed_truncated_rgba_is_not_a_complete_original(self):
        import ci_private_evidence as crypto
        _current, plan, originals = fixedtxt_fixture(decoded=True)
        prefix = "observation/" + plan["fixture"] + "/fixed-text-original-readback/"
        frame = plan["selected_frames"][0]; stem = f"frame-{frame:06d}"
        changed = dict(originals); shortened = originals[prefix + stem + ".decoder.stdout"][:-4]
        changed[prefix + stem + ".decoder.stdout"] = shortened
        receipt = crypto.strict(changed[prefix + "selected-originals.json"])
        receipt["selected_frames"][0].update(rgba_sha256=crypto.sha(shortened), rgba_bytes=len(shortened))
        changed[prefix + "selected-originals.json"] = crypto.json_bytes(receipt)
        capture = crypto.strict(changed[prefix + stem + ".decoder.capture.json"])
        capture["original_streams"]["stdout"].update(sha256=crypto.sha(shortened), bytes=len(shortened))
        changed[prefix + stem + ".decoder.capture.json"] = crypto.json_bytes(capture)
        with self.assertRaisesRegex(core.SelectionError, "original-rgba-truncated"):
            film.fixedtxt_validate_selected_frames(changed, plan=plan)

    def test_unknown_decoder_incomplete_eof_or_changed_invocation_never_becomes_native_pass(self):
        import ci_private_evidence as crypto
        _current, plan, originals = fixedtxt_fixture(decoded=True)
        path = "observation/" + plan["fixture"] + "/fixed-text-original-readback/frame-000045.decoder.capture.json"
        for change in (lambda value:value.update(state="UNKNOWN"),
                       lambda value:value["terminal"].update(exit_code=True),
                       lambda value:value["terminal"].update(pipes_closed=False),
                       lambda value:value["terminal"].update(timed_out=True),
                       lambda value:value["original_streams"]["stdout"].update(buffer_collected_from_process=False),
                       lambda value:value["invocation"].update(decoder_sha256="0" * 64)):
            changed = dict(originals); capture = crypto.strict(originals[path]); change(capture)
            changed[path] = crypto.json_bytes(capture)
            with self.assertRaises(core.SelectionError): film.fixedtxt_validate_selected_frames(changed, plan=plan)

    def test_all_180_png_and_font_original_rosters_survive_selected_rgba_checks(self):
        import ci_private_evidence as crypto
        _current, plan, originals = fixedtxt_fixture(decoded=True)
        prefix = "observation/" + plan["fixture"] + "/fixed-text-original-readback/"
        changed = dict(originals)
        changed["owner/" + plan["native_directory"] + "/frames/000179.png"] += b"changed"
        with self.assertRaisesRegex(core.SelectionError, "all180-original-binding"):
            film.fixedtxt_validate_selected_frames(changed, plan=plan)
        changed = dict(originals); changed[prefix + "font-0000.original.bin"] += b"changed"
        with self.assertRaisesRegex(core.SelectionError, "font-original-binding"):
            film.fixedtxt_validate_selected_frames(changed, plan=plan)
