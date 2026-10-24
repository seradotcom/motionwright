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
        self.assertFalse(any("stdout" in r["relative_path"] or r["relative_path"].endswith(".rgb24") for r in manifest["members"]))
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
